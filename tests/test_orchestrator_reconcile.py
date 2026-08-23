"""R8 — reconcile per-issue isolation, drift cleanup, escalation durability."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from symphony.issue import Issue
from symphony.orchestrator import Orchestrator, RunningEntry
from symphony.orchestrator.constants import ESCALATION_MAX_ATTEMPTS
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    PrimeAgentConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
    WorkflowState,
)


def _make_config(
    *,
    active_states: tuple[str, ...] = ("Todo", "In Progress"),
    terminal_states: tuple[str, ...] = ("Done", "Cancelled"),
) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=Path("/tmp/ws"),
        tracker=TrackerConfig(
            kind="linear",
            endpoint="https://api.linear.app/graphql",
            api_key="tok",
            project_slug="proj",
            active_states=active_states,
            terminal_states=terminal_states,
            board_root=None,
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=5,
            max_turns=20,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
            auto_triage_actionable_todo=True,
        ),
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
        claude=ClaudeConfig(
            command="claude -p --output-format stream-json --verbose",
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
        gemini=GeminiConfig(
            command='gemini -p ""',
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
        pi=PiConfig(
            command='pi --mode json -p ""',
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
prime_agent=PrimeAgentConfig(
    command='prime-agent -p --mode json',
    turn_timeout_ms=3_600_000,
    read_timeout_ms=5_000,
    stall_timeout_ms=300_000,
    resume_across_turns=True,
),
        server=ServerConfig(port=None),
        prompt_template="hi",
    )


def _orch() -> Orchestrator:
    return Orchestrator(WorkflowState(Path("/tmp/no.md")))


def _issue(identifier: str, state: str) -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="",
        priority=2,
        state=state,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


async def _parked() -> None:
    await asyncio.sleep(3600)


def _entry(issue: Issue, task: asyncio.Task[None], workspace: Path) -> RunningEntry:
    return RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=task,
        workspace_path=workspace,
        agent_kind="codex",
    )


class _RecordingWorkspaceManager:
    def __init__(self, *, fail_first_remove: bool = False) -> None:
        self.removed: list[Path] = []
        self._fail_first = fail_first_remove

    async def remove(self, path: Path) -> None:
        self.removed.append(path)
        if self._fail_first and len(self.removed) == 1:
            raise RuntimeError("disk error")


async def _drain(task: asyncio.Task[None]) -> None:
    try:
        await asyncio.wait_for(task, timeout=1)
    except asyncio.CancelledError:
        pass
    except TimeoutError as exc:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise AssertionError("expected parked worker task to be cancelled") from exc


async def test_reconcile_drift_state_cleans_workspace(monkeypatch) -> None:
    orch = _orch()
    cfg = _make_config()
    issue = _issue("MT-1", "In Progress")
    task = asyncio.create_task(_parked())
    entry = _entry(issue, task, Path("/tmp/ws/MT-1"))
    orch._running[issue.id] = entry
    manager = _RecordingWorkspaceManager()
    orch._workspace_manager = manager  # type: ignore[assignment]

    drifted = _issue("MT-1", "Limbo")
    monkeypatch.setattr(
        orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: [drifted]
    )

    await orch._reconcile_running(cfg)
    await _drain(task)

    assert task.cancelled()
    assert manager.removed == [Path("/tmp/ws/MT-1")]


async def test_reconcile_isolates_per_issue_failures(monkeypatch) -> None:
    orch = _orch()
    cfg = _make_config()
    first = _issue("MT-1", "In Progress")
    second = _issue("MT-2", "In Progress")
    task_one = asyncio.create_task(_parked())
    task_two = asyncio.create_task(_parked())
    first_entry = _entry(first, task_one, Path("/tmp/ws/MT-1"))
    second_entry = _entry(second, task_two, Path("/tmp/ws/MT-2"))
    # Terminal cleanup now has a grace period; this test covers the expired path.
    first_entry.terminal_seen_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    second_entry.terminal_seen_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    orch._running[first.id] = first_entry
    orch._running[second.id] = second_entry
    manager = _RecordingWorkspaceManager(fail_first_remove=True)
    orch._workspace_manager = manager  # type: ignore[assignment]

    refreshed = [_issue("MT-1", "Cancelled"), _issue("MT-2", "Cancelled")]
    monkeypatch.setattr(
        orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: refreshed
    )

    await orch._reconcile_running(cfg)
    await _drain(task_one)
    await _drain(task_two)

    # The first issue's workspace removal blew up; the second issue must
    # still have been processed.
    assert task_one.cancelled()
    assert task_two.cancelled()
    assert manager.removed == [Path("/tmp/ws/MT-1"), Path("/tmp/ws/MT-2")]


async def test_escalation_survives_tracker_failure(monkeypatch) -> None:
    orch = _orch()
    cfg = _make_config()
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    orch._loop = asyncio.get_running_loop()

    calls = {"update": 0}

    def _flaky_update(_cfg, _issue, _state) -> None:
        calls["update"] += 1
        if calls["update"] == 1:
            raise RuntimeError("tracker down")

    monkeypatch.setattr(orch, "_tracker_call_update_state", _flaky_update)
    monkeypatch.setattr(orch, "_tracker_call_append_note", lambda *_a: None)

    orch._claimed.add("id-MT-1")
    await orch._escalate_max_retries(
        issue_id="id-MT-1", identifier="MT-1", attempt=4, error="boom"
    )
    # Tracker failed: the ticket must stay out of dispatch (claimed +
    # pending escalation) instead of silently re-entering the board.
    assert "id-MT-1" in orch._claimed
    assert "id-MT-1" in orch._pending_escalations
    assert "id-MT-1" in orch._in_flight_ids()

    await orch._escalate_max_retries(
        issue_id="id-MT-1", identifier="MT-1", attempt=4, error="boom"
    )
    assert calls["update"] == 2
    assert "id-MT-1" not in orch._claimed
    assert "id-MT-1" not in orch._pending_escalations


async def test_escalation_gives_up_after_bounded_attempts(monkeypatch) -> None:
    orch = _orch()
    cfg = _make_config()
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    orch._loop = asyncio.get_running_loop()

    def _always_fail(_cfg, _issue, _state) -> None:
        raise RuntimeError("tracker down")

    monkeypatch.setattr(orch, "_tracker_call_update_state", _always_fail)
    monkeypatch.setattr(orch, "_tracker_call_append_note", lambda *_a: None)

    orch._claimed.add("id-MT-1")
    orch._pending_escalations["id-MT-1"] = ESCALATION_MAX_ATTEMPTS
    await orch._escalate_max_retries(
        issue_id="id-MT-1", identifier="MT-1", attempt=4, error="boom"
    )
    # Bounded: past the cap the old discard behavior is the last resort.
    assert "id-MT-1" not in orch._claimed
    assert "id-MT-1" not in orch._pending_escalations


# ---------------------------------------------------------------------------
# F-02 — stall budget must come from the RESOLVED backend, not agent.kind
# ---------------------------------------------------------------------------


def _stalled_entry(issue: Issue, *, agent_kind: str, age_s: float) -> RunningEntry:
    started = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    entry = RunningEntry(
        issue=issue,
        started_at=started,
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp/ws/MT-1"),
        agent_kind=agent_kind,
    )
    return entry


def test_stall_budget_uses_the_pinned_backends_timeout() -> None:
    """A claude-pinned ticket must get claude's 900 s, not codex's 300 s."""
    orch = _orch()
    cfg = _make_config()
    cfg = replace(cfg, claude=replace(cfg.claude, stall_timeout_ms=900_000))
    issue = replace(_issue("MT-1", "In Progress"), agent_kind="claude")
    entry = _stalled_entry(issue, agent_kind="claude", age_s=400)

    assert orch._stall_timeout_ms_for_entry(cfg, entry) == 900_000


def test_stall_budget_uses_the_stage_routed_backends_timeout() -> None:
    orch = _orch()
    cfg = _make_config()
    cfg = replace(
        cfg,
        claude=replace(cfg.claude, stall_timeout_ms=900_000),
        agent=replace(cfg.agent, stage_kinds={"in progress": "claude"}),
    )
    issue = _issue("MT-1", "In Progress")
    entry = _stalled_entry(issue, agent_kind="", age_s=400)

    assert orch._stall_timeout_ms_for_entry(cfg, entry) == 900_000


def test_stall_budget_falls_back_to_the_default_backend() -> None:
    orch = _orch()
    cfg = _make_config()
    entry = _stalled_entry(_issue("MT-1", "In Progress"), agent_kind="codex", age_s=1)

    assert orch._stall_timeout_ms_for_entry(cfg, entry) == 300_000


def test_stall_timeout_ms_by_state_overrides_the_backend_budget() -> None:
    orch = _orch()
    cfg = _make_config()
    cfg = replace(
        cfg,
        agent=replace(cfg.agent, stall_timeout_ms_by_state={"in progress": 900_000}),
    )
    entry = _stalled_entry(_issue("MT-1", "In Progress"), agent_kind="codex", age_s=1)

    assert orch._stall_timeout_ms_for_entry(cfg, entry) == 900_000
    other = _stalled_entry(_issue("MT-2", "Todo"), agent_kind="codex", age_s=1)
    assert orch._stall_timeout_ms_for_entry(cfg, other) == 300_000


async def test_reconcile_does_not_cancel_a_pinned_worker_inside_its_own_budget(
    monkeypatch,
) -> None:
    """The regression itself: two backends, two stall budgets, one reconcile."""
    orch = _orch()
    cfg = _make_config()
    cfg = replace(cfg, claude=replace(cfg.claude, stall_timeout_ms=900_000))

    healthy = replace(_issue("MT-1", "In Progress"), agent_kind="claude")
    doomed = _issue("MT-2", "In Progress")
    healthy_task = asyncio.create_task(_parked())
    doomed_task = asyncio.create_task(_parked())
    healthy_entry = _stalled_entry(healthy, agent_kind="claude", age_s=400)
    healthy_entry.worker_task = healthy_task
    doomed_entry = _stalled_entry(doomed, agent_kind="codex", age_s=400)
    doomed_entry.worker_task = doomed_task
    orch._running[healthy.id] = healthy_entry
    orch._running[doomed.id] = doomed_entry
    orch._workspace_manager = _RecordingWorkspaceManager()  # type: ignore[assignment]
    monkeypatch.setattr(
        orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: [healthy, doomed]
    )

    await orch._reconcile_running(cfg)

    assert healthy_entry.cancelled_at is None, (
        "claude worker was stall-cancelled on codex's 300 s budget"
    )
    assert doomed_entry.cancelled_at is not None
    healthy_task.cancel()
    doomed_task.cancel()
    await asyncio.gather(healthy_task, doomed_task, return_exceptions=True)


# ---------------------------------------------------------------------------
# F-32 — transition targets resolve through the board's own terminal lanes
# ---------------------------------------------------------------------------


def test_human_review_target_prefers_a_human_lane_then_blocked():
    from symphony.orchestrator.helpers import _human_review_target_state

    cfg = _make_config(terminal_states=("Done", "Needs Human", "Blocked"))
    assert _human_review_target_state(cfg) == "Needs Human"

    cfg = _make_config(terminal_states=("Done", "Blocked"))
    assert _human_review_target_state(cfg) == "Blocked"

    # Fully customized board: fall back to the first terminal lane rather
    # than writing a state the tracker does not know.
    cfg = _make_config(terminal_states=("Shipped", "Parked"))
    assert _human_review_target_state(cfg) == "Shipped"

    cfg = _make_config(terminal_states=())
    assert _human_review_target_state(cfg) == ""


def test_rewind_budget_target_prefers_blocked_then_human():
    from symphony.orchestrator.helpers import _rewind_budget_target_state

    cfg = _make_config(terminal_states=("Done", "Needs Human", "Blocked"))
    assert _rewind_budget_target_state(cfg) == "Blocked"

    cfg = _make_config(terminal_states=("Done", "Needs Human"))
    assert _rewind_budget_target_state(cfg) == "Needs Human"

    cfg = _make_config(terminal_states=("Shipped",))
    assert _rewind_budget_target_state(cfg) == "Shipped"


async def test_skip_document_refuses_a_board_with_no_terminal_lane(monkeypatch):
    orch = _orch()
    cfg = _make_config(active_states=("Document",), terminal_states=())
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    issue = _issue("MT-DOC", state="Document")
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_fetch_issue_full_by_id",
        staticmethod(lambda _cfg, _identifier: issue),
    )

    ok, message = await orch.skip_document("MT-DOC")

    assert ok is False
    assert "no terminal lane" in message


# ---------------------------------------------------------------------------
# M7 — shared tracker client close-race protection
# ---------------------------------------------------------------------------


class _FakeTrackerClient:
    """Duck-typed tracker client whose pool can be closed mid-flight."""

    def __init__(self) -> None:
        self.closed = False
        self.fetches = 0

    def fetch_candidate_issues(self) -> list[Issue]:
        if self.closed:
            raise RuntimeError(
                "Cannot send a request, as the client has been closed."
            )
        self.fetches += 1
        return []

    def record_agent_kind(self, identifier: str, agent_kind: str) -> None:
        if self.closed:
            raise RuntimeError(
                "Cannot send a request, as the client has been closed."
            )

    def record_last_agent_kind(self, identifier: str, agent_kind: str) -> None:
        if self.closed:
            raise RuntimeError(
                "Cannot send a request, as the client has been closed."
            )

    def close(self) -> None:
        self.closed = True


def test_shared_tracker_client_reuses_and_rebuilds_on_cfg_change(monkeypatch):
    """Hot reload (new ServiceConfig identity) still rebuilds the client."""
    import symphony.orchestrator.core as core_module

    orch = _orch()
    cfg_a = _make_config()
    cfg_b = _make_config()
    built: list[_FakeTrackerClient] = []

    def fake_build(_cfg):
        client = _FakeTrackerClient()
        built.append(client)
        return client

    monkeypatch.setattr(core_module, "build_tracker_client", fake_build)

    first = orch._shared_tracker_client(cfg_a)
    assert orch._shared_tracker_client(cfg_a) is first
    assert orch._tracker_call_candidates(cfg_a) == []
    assert first.fetches == 1

    second = orch._shared_tracker_client(cfg_b)
    assert second is not first
    assert built == [first, second]


def test_tracker_bridge_raises_clean_error_when_pool_closes_mid_call(monkeypatch):
    """A stop() racing a bridge call surfaces a retryable error, logged once."""
    import symphony.orchestrator.core as core_module
    from symphony.orchestrator.core import _TrackerClientUnavailable

    orch = _orch()
    cfg = _make_config()
    client = _FakeTrackerClient()
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    warnings: list[str] = []
    monkeypatch.setattr(
        core_module.log, "warning", lambda event, **_f: warnings.append(event)
    )

    assert orch._tracker_call_candidates(cfg) == []

    # stop() closed the pool while the bridge held the client reference.
    client.closed = True
    with pytest.raises(_TrackerClientUnavailable):
        orch._tracker_call_candidates(cfg)
    with pytest.raises(_TrackerClientUnavailable):
        orch._tracker_call_candidates(cfg)

    assert warnings == ["tracker_client_close_race"]


def test_tracker_checkout_refuses_to_rebuild_after_stop(monkeypatch):
    """The closing/closed generation flags block post-stop rebuilds."""
    import symphony.orchestrator.core as core_module
    from symphony.orchestrator.core import _TrackerClientUnavailable

    orch = _orch()
    cfg = _make_config()
    built: list[_FakeTrackerClient] = []

    def fake_build(_cfg):
        client = _FakeTrackerClient()
        built.append(client)
        return client

    monkeypatch.setattr(core_module, "build_tracker_client", fake_build)

    assert orch._tracker_call_candidates(cfg) == []
    assert len(built) == 1

    # The stop() window: closing, not yet fully closed.
    orch._tracker_client_closing = True
    with pytest.raises(_TrackerClientUnavailable):
        orch._tracker_call_candidates(cfg)
    # Fully closed past stop().
    orch._tracker_client_closed = True
    with pytest.raises(_TrackerClientUnavailable):
        orch._tracker_call_candidates(cfg)

    # No rebuild raced the shutdown — the original client is the only one.
    assert len(built) == 1


def test_record_agent_kind_bridges_swallow_close_race(monkeypatch):
    """Best-effort record calls are silently skipped when stop() races."""
    import symphony.orchestrator.core as core_module

    orch = _orch()
    cfg = _make_config()
    client = _FakeTrackerClient()
    client.closed = True
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)

    # Neither raises despite the closed pool underneath.
    orch._tracker_call_record_agent_kind(cfg, "MT-1", "codex")
    orch._tracker_call_record_last_agent_kind(cfg, "MT-1", "codex")


async def test_stop_closes_shared_client_and_gates_late_bridges(monkeypatch):
    """End to end: stop() closes the pool; late bridges fail retryably."""
    import symphony.orchestrator.core as core_module
    from symphony.orchestrator.core import _TrackerClientUnavailable

    orch = _orch()
    cfg = _make_config()
    client = _FakeTrackerClient()
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)

    assert await asyncio.to_thread(orch._tracker_call_candidates, cfg) == []
    assert not client.closed

    await orch.stop()

    assert client.closed
    with pytest.raises(_TrackerClientUnavailable):
        await asyncio.to_thread(orch._tracker_call_candidates, cfg)
