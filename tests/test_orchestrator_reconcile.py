"""R8 — reconcile per-issue isolation, drift cleanup, escalation durability."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

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
        await task
    except asyncio.CancelledError:
        pass


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
    orch._running[first.id] = _entry(first, task_one, Path("/tmp/ws/MT-1"))
    orch._running[second.id] = _entry(second, task_two, Path("/tmp/ws/MT-2"))
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
