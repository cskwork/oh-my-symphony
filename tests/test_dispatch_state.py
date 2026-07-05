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
