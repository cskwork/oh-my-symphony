"""Unit tests for DispatchState — the single owner of live slot state.

These pin the invariants that were historically regressed when the rules
lived inline in Orchestrator (see dispatch_state.py docstring):
slot budget counts retry-pending; task identity before eviction; one
pending retry timer per issue.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from symphony.issue import Issue
from symphony.orchestrator.dispatch_state import DispatchState
from symphony.orchestrator.entries import RetryEntry, RunningEntry


def _issue(issue_id: str) -> Issue:
    return Issue(
        id=issue_id,
        identifier=issue_id,
        title="t",
        description="",
        priority=None,
        state="In Progress",
    )


def _entry(issue_id: str, task: asyncio.Task[None] | None = None) -> RunningEntry:
    return RunningEntry(
        issue=_issue(issue_id),
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=task,
        workspace_path=Path("/tmp/ws"),
    )


class _FakeTimerHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def _retry_entry(issue_id: str) -> RetryEntry:
    return RetryEntry(
        issue_id=issue_id,
        identifier=issue_id,
        attempt=1,
        due_at_ms=0.0,
        timer_handle=_FakeTimerHandle(),  # type: ignore[arg-type]
    )


def test_available_slots_counts_running_and_retry() -> None:
    state = DispatchState()
    state.begin_run("A", _entry("A"))
    state.schedule_retry("B", _retry_entry("B"))

    assert state.available_slots(1) == 0
    assert state.available_slots(2) == 0
    assert state.available_slots(3) == 1
    # Never negative even when over-committed.
    assert state.available_slots(0) == 0
    assert state.in_flight_ids() == {"A", "B"}


def test_begin_run_and_abort_run_round_trip() -> None:
    state = DispatchState()
    entry = _entry("A")
    state.begin_run("A", entry)
    assert state.running["A"] is entry
    assert "A" in state.claimed

    aborted = state.abort_run("A")
    assert aborted is entry
    assert "A" not in state.running
    assert "A" not in state.claimed
    assert state.abort_run("A") is None


def test_dispatch_state_does_not_retain_completed_ids() -> None:
    """AF-15 — completed ids have no consumer and must not accumulate."""
    state = DispatchState()

    assert not hasattr(state, "completed")


@pytest.mark.asyncio
async def test_entry_owned_by_requires_task_identity() -> None:
    state = DispatchState()

    async def _noop() -> None:
        return None

    stale_task = asyncio.create_task(_noop())
    fresh_task = asyncio.create_task(_noop())
    await asyncio.gather(stale_task, fresh_task)

    # Fresh entry installed under the same key (retry fired inside the
    # worker-exit yield). The stale task must NOT be treated as the owner.
    state.begin_run("A", _entry("A", fresh_task))
    assert state.entry_owned_by("A", stale_task) is None
    assert state.entry_owned_by("A", fresh_task) is state.running["A"]
    assert state.entry_owned_by("missing", fresh_task) is None


def test_schedule_retry_cancels_previous_timer() -> None:
    state = DispatchState()
    first = _retry_entry("A")
    second = _retry_entry("A")
    state.schedule_retry("A", first)
    state.schedule_retry("A", second)

    assert first.timer_handle.cancelled is True  # type: ignore[union-attr]
    assert state.retry["A"] is second

    popped = state.cancel_pending_retry("A")
    assert popped is second
    assert second.timer_handle.cancelled is True  # type: ignore[union-attr]
    assert state.cancel_pending_retry("A") is None


def test_prune_claims_not_in_keeps_in_flight() -> None:
    state = DispatchState()
    state.claimed.update({"A", "B", "C"})

    pruned = state.prune_claims_not_in({"A"})

    assert pruned == {"B", "C"}
    assert state.claimed == {"A"}


# ---------------------------------------------------------------------------
# supervised background tasks (initiative B) — strong refs + loud failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_supervised_pins_task_and_logs_failure() -> None:
    from pathlib import Path as _P

    from symphony.orchestrator import Orchestrator
    from symphony.workflow.state import WorkflowState

    orch = Orchestrator(WorkflowState(_P("/tmp/no.md")))

    async def _boom() -> None:
        raise RuntimeError("supervised failure")

    task = orch._spawn_supervised(_boom(), name="test-boom")
    assert task in orch._background_tasks
    with pytest.raises(RuntimeError):
        await task
    # done-callbacks run on the next loop slice
    await asyncio.sleep(0)
    assert task not in orch._background_tasks


@pytest.mark.asyncio
async def test_drain_background_tasks_cancels_stragglers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path as _P

    import symphony.orchestrator.core as core_module
    from symphony.orchestrator import Orchestrator
    from symphony.workflow.state import WorkflowState

    orch = Orchestrator(WorkflowState(_P("/tmp/no.md")))

    async def _hang() -> None:
        await asyncio.Future()

    task = orch._spawn_supervised(_hang(), name="test-hang")
    monkeypatch.setattr(core_module, "STOP_BACKGROUND_TASKS_TIMEOUT_S", 0.05)

    await orch._drain_background_tasks()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stop_bounds_cancellation_resistant_worker_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AF-08 — a worker that ignores cancellation cannot hold stop open."""
    from pathlib import Path as _P

    import symphony.orchestrator.core as core_module
    from symphony.orchestrator import Orchestrator
    from symphony.orchestrator.run_registry import RunRegistry
    from symphony.workflow.state import WorkflowState

    orch = Orchestrator(WorkflowState(_P("/tmp/no.md")))
    registry_path = tmp_path / "state.db"
    registry = RunRegistry(registry_path)
    issue = _issue("A")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    orch._run_registry = registry
    release = asyncio.Event()
    pause_event = asyncio.Event()
    pause_seen_before_cancel: list[bool] = []
    orch._pause_events["A"] = pause_event

    async def _resist_cancel() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pause_seen_before_cancel.append(pause_event.is_set())
                continue

    worker = asyncio.create_task(_resist_cancel())
    await asyncio.sleep(0)
    entry = _entry("A", worker)
    entry.run_id = run_id
    orch._dispatch_state.begin_run("A", entry)
    monkeypatch.setattr(core_module, "STALL_FORCE_EJECT_GRACE_S", 0.05)

    stop_task = asyncio.create_task(orch.stop())
    try:
        done, _pending = await asyncio.wait({stop_task}, timeout=0.2)
        assert stop_task in done, "stop exceeded the worker-drain bound"
    finally:
        release.set()
        await asyncio.wait_for(stop_task, timeout=1.0)

    assert orch._running == {}
    assert pause_seen_before_cancel == [True]
    reopened = RunRegistry(registry_path)
    try:
        assert reopened.get_run(run_id).status == "shutdown_abandoned"
        assert reopened.has_active_lease(issue.id) is False
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_stop_clears_issue_debug_state() -> None:
    """AF-15 — in-process stop/restart must not retain ticket diagnostics."""
    from pathlib import Path as _P

    from symphony.orchestrator import Orchestrator, _IssueDebug
    from symphony.workflow.state import WorkflowState

    orch = Orchestrator(WorkflowState(_P("/tmp/no.md")))
    orch._issue_debug["A"] = _IssueDebug(last_error="old run")

    await orch.stop()

    assert orch._issue_debug == {}
