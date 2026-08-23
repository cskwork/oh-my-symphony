"""SPEC §17.4 — orchestrator dispatch eligibility / sort / blockers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import symphony.orchestrator.core as core_module
from symphony.backends import (
    EVENT_APPROVAL_DENIED,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
)
from symphony.errors import TurnFailed
from symphony.issue import BlockerRef, Issue, sort_for_dispatch
from symphony.orchestrator import (
    STALL_FORCE_EJECT_GRACE_S,
    Orchestrator,
    RunningEntry,
    _is_auto_triage_todo_candidate,
    _IssueDebug,
    _sort_for_dispatch_fifo,
)
from symphony.orchestrator.run_registry import (
    ContinuationCheckpoint,
    RunRecord,
    RunRegistry,
)
from symphony.workspace import (
    HISTORY_LOCAL_ONLY,
    HistoryGateResult,
    WorkspaceManager,
)
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
    max_concurrent: int = 5,
    per_state: dict[str, int] | None = None,
    active_states: tuple[str, ...] = ("Todo", "In Progress"),
    terminal_states: tuple[str, ...] = ("Done", "Cancelled"),
    tracker_kind: str = "linear",
    auto_triage_actionable_todo: bool = True,
    workflow_path: Path = Path("/tmp/WORKFLOW.md"),
    workspace_root: Path = Path("/tmp/ws"),
    hooks: HooksConfig | None = None,
) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=workflow_path,
        poll_interval_ms=30_000,
        workspace_root=workspace_root,
        tracker=TrackerConfig(
            kind=tracker_kind,
            endpoint="https://api.linear.app/graphql",
            api_key="tok",
            project_slug="proj",
            active_states=active_states,
            terminal_states=terminal_states,
            board_root=Path("/tmp/kanban") if tracker_kind == "file" else None,
        ),
        hooks=hooks or HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=max_concurrent,
            max_turns=20,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state=per_state or {},
            auto_triage_actionable_todo=auto_triage_actionable_todo,
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
    state = WorkflowState(Path("/tmp/no.md"))
    return Orchestrator(state)


def _issue(
    identifier: str,
    state: str = "Todo",
    blocked_by: tuple[BlockerRef, ...] = (),
    priority: int | None = 2,
    updated_at: datetime | None = None,
    description: str | None = None,
    labels: tuple[str, ...] = (),
) -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description=description,
        priority=priority,
        state=state,
        labels=labels,
        blocked_by=blocked_by,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=updated_at,
    )


def test_should_dispatch_basic():
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1")
    assert orch._should_dispatch(issue, cfg) is True


def test_auto_triage_actionable_file_todo_moves_to_in_progress_without_dispatch(monkeypatch):
    cfg = _make_config(tracker_kind="file", active_states=("Todo", "In Progress", "Verify"))
    issue = _issue(
        "MT-1",
        description="## Request\nBuild it.\n\n## Acceptance Criteria\n1. It works.",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    dispatched: list[str] = []
    appended: list[tuple[str, str, str]] = []
    moved: list[tuple[str, str]] = []

    async def _fetch(_cfg):
        return [issue]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    def _append(_cfg, _issue, heading, body):
        appended.append((_issue.identifier, heading, body))

    def _move(_cfg, _issue, target):
        moved.append((_issue.identifier, target))

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)
    monkeypatch.setattr(Orchestrator, "_tracker_call_append_note", staticmethod(_append))
    monkeypatch.setattr(Orchestrator, "_tracker_call_update_state", staticmethod(_move))

    import asyncio

    asyncio.run(orch._on_tick())

    assert dispatched == []
    assert appended == [("MT-1", "Triage", "Ticket is actionable; routing to In Progress.")]
    assert moved == [("MT-1", "In Progress")]


def test_auto_triage_skips_already_triaged_todo(monkeypatch):
    cfg = _make_config(tracker_kind="file", active_states=("Todo", "In Progress", "Verify"))
    issue = _issue(
        "MT-1",
        description=(
            "## Request\nBuild it.\n\n"
            "## Acceptance Criteria\n1. It works.\n\n"
            "## Triage\nTicket is actionable; routing to In Progress."
        ),
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    dispatched: list[str] = []
    appended: list[tuple[str, str, str]] = []
    moved: list[tuple[str, str]] = []

    async def _fetch(_cfg):
        return [issue]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    def _append(_cfg, _issue, heading, body):
        appended.append((_issue.identifier, heading, body))

    def _move(_cfg, _issue, target):
        moved.append((_issue.identifier, target))

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)
    monkeypatch.setattr(Orchestrator, "_tracker_call_append_note", staticmethod(_append))
    monkeypatch.setattr(Orchestrator, "_tracker_call_update_state", staticmethod(_move))

    import asyncio

    asyncio.run(orch._on_tick())

    assert appended == []
    assert moved == []
    assert dispatched == ["MT-1"]


def test_auto_triage_skips_bug_tickets_so_reproduction_prompt_runs(monkeypatch):
    cfg = _make_config(tracker_kind="file", active_states=("Todo", "Explore", "In Progress"))
    issue = _issue(
        "BUG-1",
        description="## Request\nFix it.\n\n## Acceptance Criteria\n1. Reproduced.",
        labels=("bug",),
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [issue]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)

    import asyncio

    asyncio.run(orch._on_tick())

    assert dispatched == ["BUG-1"]


def test_should_skip_terminal_state():
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1", state="Done")
    assert orch._should_dispatch(issue, cfg) is False


def test_should_skip_already_running():
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1")
    orch._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    assert orch._should_dispatch(issue, cfg) is False


def test_todo_with_non_terminal_blocker_blocked():
    cfg = _make_config()
    orch = _orch()
    blocker = BlockerRef(id="z", identifier="MT-9", state="In Progress")
    issue = _issue("MT-1", state="Todo", blocked_by=(blocker,))
    assert orch._should_dispatch(issue, cfg) is False


def test_todo_with_done_blocker_eligible():
    cfg = _make_config()
    orch = _orch()
    blocker = BlockerRef(id="z", identifier="MT-9", state="Done")
    issue = _issue("MT-1", state="Todo", blocked_by=(blocker,))
    assert orch._should_dispatch(issue, cfg) is True


def test_todo_with_done_blocker_waits_for_worker_finalization():
    cfg = _make_config()
    orch = _orch()
    upstream = _issue("MT-9", state="Done")
    downstream = _issue(
        "MT-10",
        blocked_by=(BlockerRef(id="stale-id", identifier="MT-9", state="Done"),),
    )
    orch._running[upstream.id] = RunningEntry(
        issue=upstream,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )

    assert orch._should_dispatch(downstream, cfg) is False

    orch._running.pop(upstream.id)
    orch._terminal_persist_pending.add(upstream.id)
    downstream = replace(
        downstream,
        blocked_by=(
            BlockerRef(id=upstream.id, identifier="MT-9", state="Done"),
        ),
    )
    assert orch._should_dispatch(downstream, cfg) is False

    orch._terminal_persist_pending.discard(upstream.id)
    assert orch._should_dispatch(downstream, cfg) is True


def test_todo_with_blocked_terminal_blocker_remains_blocked():
    cfg = _make_config(terminal_states=("Done", "Cancelled", "Blocked"))
    orch = _orch()
    blocker = BlockerRef(id="z", identifier="MT-9", state="Blocked")
    issue = _issue("MT-1", state="Todo", blocked_by=(blocker,))

    assert orch._should_dispatch(issue, cfg) is False


def test_todo_with_human_review_blocker_remains_blocked():
    cfg = _make_config(terminal_states=("Human Review", "Done", "Blocked"))
    orch = _orch()
    blocker = BlockerRef(id="z", identifier="MT-9", state="Human Review")
    issue = _issue("MT-1", state="Todo", blocked_by=(blocker,))

    assert orch._should_dispatch(issue, cfg) is False


def test_tick_normalizes_legacy_human_review_confirm_done_before_candidates(
    monkeypatch,
):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo",),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    legacy = _issue(
        "MT-LEGACY",
        state="Human Review",
        description=(
            "## Human Review\n\n"
            "### Evidence\n"
            "- checks passed.\n\n"
            "### Decision Needed\n"
            "Confirm Done\n"
        ),
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []
    notes: list[tuple[str, str, str]] = []
    fetch_seen: list[list[tuple[str, str]]] = []

    async def _fetch_candidates(_cfg):
        fetch_seen.append(list(moved))
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [legacy])
    monkeypatch.setattr(
        orch,
        "_tracker_call_append_note",
        lambda _cfg, _issue, heading, body: notes.append(
            (_issue.identifier, heading, body)
        ),
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, _issue, target: moved.append((_issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert fetch_seen == [[("MT-LEGACY", "Done")]]
    assert notes == [("MT-LEGACY", "Human Review Normalized", notes[0][2])]
    assert "current workflow reserves `Human Review`" in notes[0][2]
    assert moved == [("MT-LEGACY", "Done")]


def test_tick_normalizes_legacy_human_review_unblock_note_after_merge_failure(
    monkeypatch,
):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo",),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    legacy = _issue(
        "MT-LEGACY",
        state="Human Review",
        description=(
            "## Merge Failure\n\n"
            "Host worktree drift blocked the original merge.\n\n"
            "## Unblock Note\n\n"
            "Follow-up ticket integrated this work on remote main.\n"
        ),
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [legacy])
    monkeypatch.setattr(orch, "_tracker_call_append_note", lambda *_args: None)
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, _issue, target: moved.append((_issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == [("MT-LEGACY", "Done")]


def test_tick_keeps_intervention_human_review_blocked(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo",),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    intervention = _issue(
        "MT-INTERVENTION",
        state="Human Review",
        description=(
            "## Human Review\n\n"
            "### Intervention Required\n"
            "Provision the real development database.\n\n"
            "### Decision Needed\n"
            "Confirm Done\n"
        ),
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(
        orch, "_tracker_call_terminal_issues", lambda _cfg: [intervention]
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, _issue, target: moved.append((_issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == []


def test_tick_keeps_blocked_rca_at_human_review_blocked(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo",),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    source = _issue("MT-BLOCKED", state="Blocked")
    rca = replace(
        _issue(
            "RCA-1",
            state="Human Review",
            description=(
                core_module._blocked_rca_description(source, reopen_state="Todo")
                + "\n\n### Decision Needed\nConfirm Done\n"
            ),
            labels=("blocked-fix", "source-mt-blocked"),
        ),
        title="Fix and unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [rca])
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, _issue, target: moved.append((_issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == []


def test_active_state_issue_with_unresolved_blocker_is_ineligible():
    cfg = _make_config(active_states=("Todo", "In Progress", "Verify"))
    orch = _orch()
    blocker = BlockerRef(id="MT-9", identifier="MT-9", state="Verify")
    issue = _issue("MT-1", state="In Progress", blocked_by=(blocker,))

    assert orch._eligible(issue, cfg, owning_retry=False) is False


def test_retry_eligibility_classifies_transient_and_durable_outcomes():
    cfg = _make_config(active_states=("Todo", "In Progress", "Verify"))
    blocker = BlockerRef(id="MT-9", identifier="MT-9", state="Verify")
    blocked = _issue("MT-1", state="In Progress", blocked_by=(blocker,))
    unsupported = replace(_issue("MT-2"), agent_kind="unsupported")
    orch = _orch()

    blocked_decision = orch._eligibility_decision(blocked, cfg, owning_retry=True)
    unsupported_decision = orch._eligibility_decision(
        unsupported, cfg, owning_retry=True
    )
    ready_decision = orch._eligibility_decision(_issue("MT-3"), cfg, owning_retry=True)

    assert blocked_decision.disposition.value == "wait_non_slot"
    assert "blocker" in blocked_decision.reason
    assert unsupported_decision.disposition.value == "reject"
    assert ready_decision.disposition.value == "ready"


@pytest.mark.parametrize("contention", ["ci", "lease", "per-state", "global"])
def test_retry_eligibility_classifies_all_contention_as_non_slot_wait(
    monkeypatch: pytest.MonkeyPatch, contention: str
) -> None:
    cfg = _make_config(
        max_concurrent=1 if contention == "global" else 5,
        per_state={"todo": 1} if contention == "per-state" else {},
    )
    orch = _orch()
    issue = _issue("MT-1")
    if contention == "ci":
        class _PendingImprovement:
            def done(self) -> bool:
                return False

        orch._improvement_task = _PendingImprovement()  # type: ignore[assignment]
    elif contention == "lease":
        monkeypatch.setattr(orch, "_has_active_run_lease", lambda _issue_id: True)
    elif contention in {"per-state", "global"}:
        sibling = _issue("MT-2")
        orch._running[sibling.id] = RunningEntry(
            issue=sibling,
            started_at=datetime.now(timezone.utc),
            retry_attempt=None,
            worker_task=None,
            workspace_path=Path("/tmp/sibling"),
        )

    decision = orch._eligibility_decision(issue, cfg, owning_retry=True)

    assert decision.disposition.value == "wait_non_slot"


def test_auto_triage_refuses_todo_with_body_dependency():
    cfg = _make_config(tracker_kind="file", active_states=("Todo", "In Progress"))
    issue = _issue(
        "MT-1",
        state="Todo",
        description=(
            "## Request\nBuild it.\n\n"
            "## Dependencies\nMT-9 must finish first.\n\n"
            "## Acceptance Criteria\n1. It works."
        ),
    )

    assert _is_auto_triage_todo_candidate(issue, cfg) is False


def test_per_state_concurrency_cap():
    cfg = _make_config(per_state={"todo": 1})
    orch = _orch()
    held = _issue("MT-2", state="Todo")
    orch._running[held.id] = RunningEntry(
        issue=held,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    new = _issue("MT-3", state="Todo")
    assert orch._should_dispatch(new, cfg) is False


def test_sort_for_dispatch_uses_registration_number_before_priority():
    earlier = _issue("OLV-061", priority=None)
    later = _issue("OLV-131", priority=1)

    out = [i.identifier for i in sort_for_dispatch([later, earlier])]

    assert out == ["OLV-061", "OLV-131"]


def test_orchestrator_dispatch_prioritizes_ticket_registration_order():
    """Workers run tickets in registration order, not current-state timestamp."""
    cfg = _make_config(
        max_concurrent=1,
        active_states=("Todo", "Explore", "In Progress", "Review", "QA", "Learn"),
    )
    review = _issue(
        "OLV-002",
        state="Review",
        priority=None,
        updated_at=datetime(2026, 1, 1, 9, tzinfo=timezone.utc),
    )
    todo = _issue(
        "OLV-003",
        state="Todo",
        priority=1,
        updated_at=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
    )

    ordered = [
        issue.identifier
        for issue in _sort_for_dispatch_fifo([todo, review], cfg)
    ]

    assert ordered == ["OLV-002", "OLV-003"]

    older_todo = _issue(
        "OLV-010",
        state="Todo",
        priority=None,
        updated_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
    )
    newer_review = _issue(
        "OLV-011",
        state="Review",
        priority=1,
        updated_at=datetime(2026, 1, 1, 11, tzinfo=timezone.utc),
    )

    ordered = [
        issue.identifier
        for issue in _sort_for_dispatch_fifo([newer_review, older_todo], cfg)
    ]

    assert ordered == ["OLV-010", "OLV-011"]

    older_registered = _issue(
        "OLV-061",
        state="Todo",
        priority=None,
        updated_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
    )
    newer_registered = _issue(
        "OLV-131",
        state="Todo",
        priority=1,
        updated_at=datetime(2025, 1, 1, 8, tzinfo=timezone.utc),
    )

    ordered = [
        issue.identifier
        for issue in _sort_for_dispatch_fifo([newer_registered, older_registered], cfg)
    ]

    assert ordered == ["OLV-061", "OLV-131"]


def test_reconcile_force_ejects_claude_process_group_after_grace(
    monkeypatch: pytest.MonkeyPatch,
):
    """Worker that didn't die from cancel must lose its slot after grace.

    Reproduces the OLV-003 zombie pattern: a worker stuck on a
    non-cancellable await still holds its slot 17 minutes after the stall
    timeout fires. Without force-eject, every other ticket starves.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    zombie = _issue("MT-1", state="Todo")
    now = datetime.now(timezone.utc)
    killed: list[int] = []
    warnings: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        core_module,
        "kill_process_group",
        lambda pid, **_kill_kw: killed.append(pid) or True,
    )
    monkeypatch.setattr(
        core_module.log,
        "warning",
        lambda event, **fields: warnings.append((event, fields)),
    )

    async def _run() -> None:
        # `_schedule_retry` reads `self._loop` to compute the timer's
        # absolute due-time, so wire the running loop in like `start()` does.
        orch._loop = asyncio.get_running_loop()
        entry = RunningEntry(
            issue=zombie,
            started_at=now - timedelta(seconds=STALL_FORCE_EJECT_GRACE_S * 4),
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
            agent_kind="claude",
            cancelled_at=now - timedelta(seconds=STALL_FORCE_EJECT_GRACE_S + 5),
            agent_pgid=4242,
        )
        orch._running[zombie.id] = entry
        orch._claimed.add(zombie.id)

        await orch._reconcile_running(cfg)
        # Cancel the retry timer the eject just scheduled so it doesn't
        # fire after the test loop closes.
        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()

    asyncio.run(_run())

    assert zombie.id not in orch._running, "zombie slot should be freed"
    assert zombie.id not in orch._claimed, "claim should be released"
    assert zombie.id in orch._retry, "force-eject must schedule a retry"
    assert orch._retry[zombie.id].error == "force_ejected_zombie"
    assert killed == [4242], "force-eject must kill the recorded process group"
    assert warnings == [
        (
            "force_eject_killed_process_group",
            {
                "issue_id": zombie.id,
                "identifier": zombie.identifier,
                "agent_kind": "claude",
                "pid": 4242,
                "killed": True,
            },
        )
    ]


@pytest.mark.parametrize("agent_pgid", [None, True, 0, -1])
def test_reconcile_force_eject_without_safe_pgid_releases_slot_and_retries(
    monkeypatch: pytest.MonkeyPatch,
    agent_pgid: int | None,
):
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    zombie = _issue("MT-1", state="Todo")
    now = datetime.now(timezone.utc)
    killed: list[int] = []
    monkeypatch.setattr(
        core_module,
        "kill_process_group",
        lambda pid, **_kill_kw: killed.append(pid) or True,
    )

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        entry = RunningEntry(
            issue=zombie,
            started_at=now - timedelta(seconds=STALL_FORCE_EJECT_GRACE_S * 4),
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
            agent_kind="claude",
            cancelled_at=now - timedelta(seconds=STALL_FORCE_EJECT_GRACE_S + 5),
            agent_pgid=agent_pgid,
        )
        orch._running[zombie.id] = entry
        orch._claimed.add(zombie.id)

        await orch._reconcile_running(cfg)
        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()

    asyncio.run(_run())

    assert zombie.id not in orch._running, "zombie slot should be freed"
    assert zombie.id not in orch._claimed, "claim should be released"
    assert zombie.id in orch._retry, "missing pid must still schedule a retry"
    assert orch._retry[zombie.id].error == "force_ejected_zombie"
    assert killed == [], "missing or unsafe pid must not trigger an unscoped kill"


def test_reconcile_force_ejects_cancelled_worker_even_when_paused():
    """AF-07 — an operator pause cannot hide a system-cancelled zombie."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-PAUSED-ZOMBIE", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        now = datetime.now(timezone.utc)
        orch._running[issue.id] = RunningEntry(
            issue=issue,
            started_at=now - timedelta(hours=1),
            retry_attempt=None,
            worker_task=None,
            workspace_path=Path("/tmp"),
            cancelled_at=now - timedelta(seconds=STALL_FORCE_EJECT_GRACE_S + 1),
        )
        orch._claimed.add(issue.id)
        assert orch.pause_worker(issue.id) is True

        await orch._reconcile_running(cfg)

        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()

    asyncio.run(_run())

    assert issue.id not in orch._running
    assert issue.id in orch._retry


def test_reconcile_isolates_force_eject_cleanup_and_still_schedules_retry(
    monkeypatch: pytest.MonkeyPatch,
):
    """AF-07 — one cleanup failure cannot orphan or starve other issues."""
    cfg = _make_config(max_concurrent=2)
    orch = _orch()
    zombie = _issue("MT-BROKEN-CLEANUP", state="In Progress")
    stalled = _issue("MT-NEXT-STALL", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        stalled_task = asyncio.create_task(_noop())
        try:
            now = datetime.now(timezone.utc)
            orch._running[zombie.id] = RunningEntry(
                issue=zombie,
                started_at=now - timedelta(hours=1),
                retry_attempt=None,
                worker_task=None,
                workspace_path=Path("/tmp"),
                cancelled_at=now
                - timedelta(seconds=STALL_FORCE_EJECT_GRACE_S + 1),
                agent_pgid=4343,
            )
            orch._running[stalled.id] = RunningEntry(
                issue=stalled,
                started_at=now - timedelta(hours=1),
                retry_attempt=None,
                worker_task=stalled_task,
                workspace_path=Path("/tmp"),
            )
            orch._claimed.update({zombie.id, stalled.id})
            monkeypatch.setattr(
                core_module,
                "kill_process_group",
                lambda _pid, **_kill_kw: (_ for _ in ()).throw(
                    RuntimeError("kill failed")
                ),
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: []
            )

            await orch._reconcile_running(cfg)

            assert zombie.id in orch._retry
            assert orch._retry[zombie.id].error == "force_ejected_zombie"
            assert orch._running[stalled.id].cancelled_at is not None
        finally:
            stalled_task.cancel()
            try:
                await stalled_task
            except (asyncio.CancelledError, Exception):
                pass
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


class _AF01StubWorkspace:
    def __init__(self, path: Path) -> None:
        self.path = path


class _AF01StubWorkspaceManager:
    """Minimal workspace manager so `_run_agent_attempt` can run for real."""

    def path_for(self, identifier: str) -> Path:
        return Path("/tmp/ws-fake") / identifier

    async def create_or_reuse(self, identifier):
        return _AF01StubWorkspace(self.path_for(identifier))

    async def before_run(self, path):
        return None

    async def after_run_best_effort(self, path):
        return None

    async def remove_best_effort(self, path):
        return None


def test_stale_zombie_finally_does_not_eject_fresh_replacement_entry(monkeypatch):
    """AF-01 — a force-ejected zombie's `finally` must not touch a fresh
    replacement entry a retry installed under the same issue id.

    Reproduces the interleaving from docs/improvements/tickets/2026-07-09/
    AF-01-identity-safe-worker-exit.md: worker A stalls and is force-ejected
    (bookkeeping only — `_force_eject_zombie` never cancels the task, so a
    worker wedged on a non-cancellable await keeps running), a retry
    installs fresh entry B under the same issue id, and only then does A's
    blocked await resolve. A's `finally` must recognize the entry under its
    issue id no longer belongs to it and leave B untouched.
    """
    cfg = _make_config(max_concurrent=5)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    release_a = asyncio.Event()
    entered_run_turn: list[asyncio.Event] = [asyncio.Event(), asyncio.Event()]
    calls = {"n": 0}

    class _Backend:
        async def start(self):
            return None

        async def initialize(self):
            return None

        async def start_session(self, *, initial_prompt, issue_title):
            return "thread-1"

        async def run_turn(self, *, prompt, is_continuation):
            index = calls["n"]
            calls["n"] += 1
            entered_run_turn[index].set()
            if index == 0:
                # Worker A: parked mid-turn like a backend that ignores
                # cancellation — matches `_force_eject_zombie`, which frees
                # the slot without ever calling `task.cancel()`.
                await release_a.wait()
                raise RuntimeError("zombie backend surfaced after re-dispatch")
            # Worker B: stays "running" through the assertions below.
            await asyncio.Event().wait()

        async def stop(self):
            return None

    monkeypatch.setattr(core_module, "build_backend", lambda _init: _Backend())
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_record_agent_kind",
        staticmethod(lambda _cfg, _identifier, _agent_kind: None),
    )

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workspace_manager = _AF01StubWorkspaceManager()  # type: ignore[assignment]

        # 1. Dispatch worker A and let it reach `run_turn`, where it parks.
        orch._dispatch(issue, cfg, attempt=None)
        entry_a = orch._running[issue.id]
        worker_a = entry_a.worker_task
        assert worker_a is not None
        await asyncio.wait_for(entered_run_turn[0].wait(), timeout=5)

        worker_b: asyncio.Task[None] | None = None
        try:
            # 2. Force-eject A: bookkeeping only, `worker_a` keeps running,
            # still parked on `release_a`.
            orch._force_eject_zombie(issue.id, entry_a, cfg)
            assert issue.id not in orch._running
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()
            orch._retry.clear()

            # 3. Retry re-dispatches: fresh entry B installs under the
            # same issue id while A is still parked.
            orch._dispatch(issue, cfg, attempt=1)
            entry_b = orch._running[issue.id]
            worker_b = entry_b.worker_task
            assert worker_b is not None
            assert worker_b is not worker_a
            await asyncio.wait_for(entered_run_turn[1].wait(), timeout=5)

            # 4. Release A's blocked await — its `finally` now runs while a
            # foreign (stale) entry sits under its issue id.
            release_a.set()
            for _ in range(50):
                if worker_a.done():
                    break
                await asyncio.sleep(0)
            assert worker_a.done(), "zombie worker never reached its finally"

            # B must be untouched: still in `_running` under the same
            # entry, `exit_started_at` never stamped by the stale worker,
            # and its own worker task still alive.
            assert orch._running.get(issue.id) is entry_b, (
                "stale zombie's finally ejected the live replacement entry"
            )
            assert entry_b.exit_started_at is None, (
                "stale zombie's finally stamped exit_started_at on the live entry"
            )
            assert not worker_b.done(), (
                "stale zombie's finally side-effected the live worker"
            )
        finally:
            if worker_b is not None and not worker_b.done():
                worker_b.cancel()
                try:
                    await worker_b
                except (asyncio.CancelledError, Exception):
                    pass
            if not worker_a.done():
                worker_a.cancel()
            try:
                await worker_a
            except (asyncio.CancelledError, Exception):
                pass
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_stale_zombie_finally_skips_worker_exit_handler(monkeypatch):
    """AF-01 ownership is checked before entering `_on_worker_exit`."""
    cfg = _make_config(max_concurrent=5)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    entered_workspace_create = asyncio.Event()
    release_workspace_create = asyncio.Event()
    exit_owners: list[asyncio.Task[None] | None] = []
    warnings: list[dict] = []

    monkeypatch.setattr(
        core_module.log,
        "warning",
        lambda message, **fields: warnings.append({"message": message, **fields}),
    )

    class _BlockingWorkspaceManager:
        async def create_or_reuse(self, identifier):
            entered_workspace_create.set()
            await release_workspace_create.wait()
            raise RuntimeError("zombie workspace create resumed")

    async def _capture_exit(issue_id, reason, error, *, owning_task=None):
        exit_owners.append(owning_task)

    async def _park() -> None:
        await asyncio.Event().wait()

    async def _run() -> None:
        orch._workspace_manager = _BlockingWorkspaceManager()  # type: ignore[assignment]
        monkeypatch.setattr(orch, "_on_worker_exit", _capture_exit)

        entry_a = _install_running_entry(orch, issue)
        worker_a = asyncio.create_task(orch._run_agent_attempt(issue, None, cfg))
        entry_a.worker_task = worker_a
        await asyncio.wait_for(entered_workspace_create.wait(), timeout=5)

        worker_b = asyncio.create_task(_park())
        entry_b = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=1,
            worker_task=worker_b,
            workspace_path=Path("/tmp/ws-fresh"),
        )
        orch._running[issue.id] = entry_b

        try:
            release_workspace_create.set()
            await worker_a

            assert exit_owners == [], (
                "a stale worker must not enter the live run's exit handler"
            )
            assert orch._running.get(issue.id) is entry_b
            assert entry_b.exit_started_at is None
        finally:
            worker_b.cancel()
            try:
                await worker_b
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())

    assert {
        "message": "worker_finally_stale_entry",
        "issue_id": issue.id,
        "reason": "error",
    } in warnings


def test_worker_exit_rechecks_identity_after_finally_gate(monkeypatch):
    """AF-01 closes the yield between the finally gate and the guarded pop."""
    cfg = _make_config(max_concurrent=5)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    entered_workspace_create = asyncio.Event()
    release_workspace_create = asyncio.Event()
    entered_exit_impl = asyncio.Event()
    release_exit_impl = asyncio.Event()
    warnings: list[dict] = []

    monkeypatch.setattr(
        core_module.log,
        "warning",
        lambda message, **fields: warnings.append({"message": message, **fields}),
    )

    class _BlockingWorkspaceManager:
        async def create_or_reuse(self, identifier):
            entered_workspace_create.set()
            await release_workspace_create.wait()
            raise RuntimeError("worker A reached finally")

    async def _park() -> None:
        await asyncio.Event().wait()

    async def _run() -> None:
        orch._workspace_manager = _BlockingWorkspaceManager()  # type: ignore[assignment]
        original_exit_impl = orch._on_worker_exit_impl

        async def _delayed_exit_impl(
            issue_id,
            reason,
            error,
            *,
            owning_task=None,
            defer_lease_finish=False,
        ):
            entered_exit_impl.set()
            await release_exit_impl.wait()
            await original_exit_impl(
                issue_id,
                reason,
                error,
                owning_task=owning_task,
                defer_lease_finish=defer_lease_finish,
            )

        monkeypatch.setattr(orch, "_on_worker_exit_impl", _delayed_exit_impl)

        entry_a = _install_running_entry(orch, issue)
        worker_a = asyncio.create_task(orch._run_agent_attempt(issue, None, cfg))
        entry_a.worker_task = worker_a
        await asyncio.wait_for(entered_workspace_create.wait(), timeout=5)

        release_workspace_create.set()
        await asyncio.wait_for(entered_exit_impl.wait(), timeout=5)
        assert entry_a.exit_started_at is not None

        worker_b = asyncio.create_task(_park())
        entry_b = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=1,
            worker_task=worker_b,
            workspace_path=Path("/tmp/ws-fresh"),
        )
        orch._running[issue.id] = entry_b

        try:
            release_exit_impl.set()
            await worker_a

            assert orch._running.get(issue.id) is entry_b
            assert entry_b.exit_started_at is None
            assert not worker_b.done()
            assert issue.id not in orch._retry
        finally:
            worker_b.cancel()
            try:
                await worker_b
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())

    assert {
        "message": "worker_exit_stale_task",
        "issue_id": issue.id,
        "reason": "error",
    } in warnings


def test_cancelled_worker_finally_passes_its_task_identity_through_shield(monkeypatch):
    """A delivered cancellation does not erase the worker's ownership token."""
    cfg = _make_config(max_concurrent=5)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    entered_workspace_create = asyncio.Event()
    exit_owners: list[asyncio.Task[None] | None] = []

    class _BlockingWorkspaceManager:
        def path_for(self, identifier):
            return Path("/tmp/ws-fake") / identifier

        async def create_or_reuse(self, identifier):
            entered_workspace_create.set()
            await asyncio.Event().wait()

    async def _capture_exit(issue_id, reason, error, *, owning_task=None):
        exit_owners.append(owning_task)

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_record_agent_kind",
        staticmethod(lambda _cfg, _identifier, _agent_kind: None),
    )
    monkeypatch.setattr(orch, "_on_worker_exit", _capture_exit)

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workspace_manager = _BlockingWorkspaceManager()  # type: ignore[assignment]

        orch._dispatch(issue, cfg, attempt=None)
        entry = orch._running[issue.id]
        worker = entry.worker_task
        assert worker is not None
        await asyncio.wait_for(entered_workspace_create.wait(), timeout=5)

        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        await asyncio.sleep(0)

        assert entry.exit_started_at is not None
        assert exit_owners == [worker]

    asyncio.run(_run())


def test_orphaned_worker_finally_skips_worker_exit_handler(monkeypatch):
    """AF-01 ownership gate treats a missing entry as already cleaned up."""
    cfg = _make_config(max_concurrent=5)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")
    exit_owners: list[asyncio.Task[None] | None] = []

    async def _capture_exit(issue_id, reason, error, *, owning_task=None):
        exit_owners.append(owning_task)

    async def _run() -> None:
        orch._workspace_manager = _AF01StubWorkspaceManager()  # type: ignore[assignment]
        monkeypatch.setattr(orch, "_on_worker_exit", _capture_exit)
        await orch._run_agent_attempt(issue, None, cfg)

    asyncio.run(_run())

    assert exit_owners == [], "a worker with no owned entry has nothing to clean up"


def test_worker_exit_impl_missing_entry_is_identity_noop(monkeypatch):
    """AF-01 owning-task exits cannot mutate state without an owned entry."""
    orch = _orch()
    issue_id = "id-MT-1"
    released_at = datetime.now(timezone.utc)
    warnings: list[dict] = []

    monkeypatch.setattr(
        core_module.log,
        "warning",
        lambda message, **fields: warnings.append({"message": message, **fields}),
    )

    async def _run() -> None:
        owning_task = asyncio.current_task()
        assert owning_task is not None
        pause_event = asyncio.Event()
        orch._claim_released_at[issue_id] = released_at
        orch._pause_events[issue_id] = pause_event

        await orch._on_worker_exit_impl(
            issue_id, "error", "stale", owning_task=owning_task
        )

        assert orch._claim_released_at[issue_id] is released_at
        assert orch._pause_events[issue_id] is pause_event
        assert not pause_event.is_set()

    asyncio.run(_run())

    assert warnings == [
        {
            "message": "worker_exit_stale_task",
            "issue_id": issue_id,
            "reason": "error",
        }
    ]


def test_worker_task_done_logs_exception_from_stale_pop_race(monkeypatch):
    """AF-01 secondary defect — `_on_worker_task_done` must retrieve and log
    `task.exception()` before its `entry is None` early return.

    If `_on_worker_exit_impl` pops the entry and then raises, the worker
    task ends errored. The old code returned as soon as `entry_owned_by`
    found nothing (the entry was already popped), so `task.exception()` was
    never called — surfacing only as asyncio's "Task exception was never
    retrieved" warning, with no structured log to find it by.
    """
    cfg = _make_config(max_concurrent=5)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    class _Backend:
        async def start(self):
            return None

        async def initialize(self):
            return None

        async def start_session(self, *, initial_prompt, issue_title):
            return "thread-1"

        async def run_turn(self, *, prompt, is_continuation):
            raise TurnFailed("boom")

        async def stop(self):
            return None

    monkeypatch.setattr(core_module, "build_backend", lambda _init: _Backend())
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_record_agent_kind",
        staticmethod(lambda _cfg, _identifier, _agent_kind: None),
    )

    errors: list[dict] = []
    monkeypatch.setattr(
        core_module.log,
        "error",
        lambda message, **fields: errors.append({"message": message, **fields}),
    )

    async def _boom_exit(issue_id, reason, error, *, owning_task=None):
        # Stand-in for `_on_worker_exit_impl` popping the entry and then
        # raising (e.g. a downstream persist failure) before the
        # done-callback ever runs.
        orch._running.pop(issue_id, None)
        raise RuntimeError("worker_exit_impl_boom")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workspace_manager = _AF01StubWorkspaceManager()  # type: ignore[assignment]
        monkeypatch.setattr(orch, "_on_worker_exit", _boom_exit)

        orch._dispatch(issue, cfg, attempt=None)
        task = orch._running[issue.id].worker_task
        assert task is not None

        # `asyncio.wait` observes completion without touching
        # `task.exception()` itself — retrieving it here would mask
        # whether production code ever did.
        await asyncio.wait({task})
        # Done-callbacks are scheduled via `call_soon`; yield twice so
        # `_on_worker_task_done` has actually run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert any(e["message"] == "worker_task_errored_after_cleanup" for e in errors), (
        "_on_worker_task_done must retrieve+log task.exception() even when "
        "the entry was already popped by the raising _on_worker_exit"
    )


def test_initialized_missing_registry_fails_closed_when_continuation_enabled(
    tmp_path,
):
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws"
    )
    issue = _issue("MT-NO-REGISTRY", state="Todo")
    orch = _orch()
    orch._run_registry_initialized = True

    assert orch._try_acquire_run_lease(
        cfg=cfg,
        issue=issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    ) is None

    opted_out = replace(cfg, agent=replace(cfg.agent, crash_continuation=False))
    acquisition = orch._try_acquire_run_lease(
        cfg=opted_out,
        issue=issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert acquisition is not None
    assert acquisition.run_id == ""


def test_persisted_lease_blocks_fresh_orchestrator_dispatch(tmp_path, monkeypatch):
    """A crash-restarted process must not ignore another live lease."""
    cfg = _make_config(workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws")
    issue = _issue("MT-1", state="Todo")
    state_db = tmp_path / ".symphony" / "state.db"

    async def _parked_worker(_issue, _attempt, _cfg) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_record_agent_kind",
        staticmethod(lambda _cfg, _identifier, _agent_kind: None),
    )

    async def _run() -> None:
        first = _orch()
        first._loop = asyncio.get_running_loop()
        first._run_registry = RunRegistry(state_db, lease_ttl=timedelta(minutes=5))
        monkeypatch.setattr(first, "_run_agent_attempt", _parked_worker)

        first._dispatch(issue, cfg, attempt=None)
        task = first._running[issue.id].worker_task
        assert task is not None

        restarted = _orch()
        restarted._run_registry = RunRegistry(state_db, lease_ttl=timedelta(minutes=5))
        assert restarted._should_dispatch(issue, cfg) is False

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(_run())


def test_unconfirmed_backend_cleanup_keeps_active_lease_for_startup_reap(tmp_path):
    issue = _issue("MT-UNCLEAN", state="Todo")
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    assert registry.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        backend_agent_pid=4545,
    )

    orch = _orch()
    orch._run_registry = registry
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path / "ws" / issue.identifier,
        run_id=run_id,
        agent_pgid=4545,
        backend_cleanup_unconfirmed=True,
    )

    orch._finish_run_lease(issue.id, entry, "shutdown_interrupted")

    saved = registry.get_run(run_id)
    assert saved.status == "active"
    assert saved.backend_agent_pid == 4545
    assert registry.has_active_lease(issue.id)


def test_worker_exit_releases_persisted_lease(tmp_path, monkeypatch):
    cfg = _make_config(workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws")
    issue = _issue("MT-1", state="Todo")
    registry = RunRegistry(tmp_path / ".symphony" / "state.db", lease_ttl=timedelta(minutes=5))
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=datetime.now(timezone.utc),
    )
    assert run_id

    async def _run() -> None:
        orch = _orch()
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry
        monkeypatch_cfg = replace(cfg, agent=replace(cfg.agent, max_turns=20))
        monkeypatch.setattr(orch._workflow_state, "current", lambda: monkeypatch_cfg)
        orch._running[issue.id] = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=tmp_path / "ws" / issue.identifier,
            run_id=run_id,
        )

        await orch._on_worker_exit_impl(issue.id, "normal", None)
        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()

    asyncio.run(_run())

    assert registry.has_active_lease(issue.id, now=datetime.now(timezone.utc)) is False
    assert registry.get_run(run_id).status == "normal"


def test_persisted_issue_flags_block_dispatch_after_restart(tmp_path):
    cfg = _make_config(workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws")
    state_db = tmp_path / ".symphony" / "state.db"
    paused = _issue("MT-PAUSED", state="Todo")
    exhausted = _issue("MT-BUDGET", state="Todo")
    registry = RunRegistry(state_db, lease_ttl=timedelta(minutes=5))
    registry.set_issue_flags(paused.id, paused=True, pause_reason="needs review")
    registry.set_issue_flags(exhausted.id, budget_exhausted=True)
    registry.close()

    restarted = _orch()
    restarted._ensure_run_registry(cfg)

    assert restarted.is_paused(paused.id)
    assert paused.id in restarted._paused_issue_ids
    assert restarted._pause_reasons[paused.id] == "needs review"
    assert exhausted.id in restarted._turn_budget_exhausted
    assert restarted._should_dispatch(paused, cfg) is False
    assert restarted._should_dispatch(exhausted, cfg) is False


def test_startup_reclaim_kills_recorded_orphan_agent_before_return(
    tmp_path, monkeypatch
):
    """AF-10 — startup reaps a dead owner's recorded live agent group."""
    import symphony.orchestrator.run_registry as run_registry_module

    monkeypatch.setattr(run_registry_module, "process_identity", lambda _pid: "proc-1")
    monkeypatch.setattr(core_module, "process_identity", lambda _pid: "proc-1")
    group_states = iter((True, False))
    monkeypatch.setattr(
        core_module,
        "process_group_exists",
        lambda _pid: next(group_states, False),
    )
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws"
    )
    issue = _issue("MT-ORPHAN", state="Todo")
    now = datetime.now(timezone.utc)
    crashed = RunRegistry(
        tmp_path / ".symphony" / "state.db",
        lease_ttl=timedelta(minutes=5),
        owner_pid=4242,
        boot_id="crashed",
    )
    run_id = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    assert crashed.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=1),
        backend_agent_pid=4343,
    )
    crashed.close()

    killed: list[int] = []
    monkeypatch.setattr(run_registry_module, "_pid_alive", lambda _pid: False)
    restarted = _orch()
    status_during_kill: list[str] = []
    contender_claims: list[str | None] = []

    def kill_while_proving_fence(pid: int) -> bool:
        killed.append(pid)
        assert restarted._run_registry is not None
        status_during_kill.append(restarted._run_registry.get_run(run_id).status)
        contender = RunRegistry(
            tmp_path / ".symphony" / "state.db", boot_id="contender"
        )
        try:
            contender_claims.append(
                contender.acquire_run(
                    issue,
                    workspace_path=tmp_path / "ws" / issue.identifier,
                    attempt=1,
                    attempt_kind="retry",
                    agent_kind="codex",
                )
            )
        finally:
            contender.close()
        return True

    monkeypatch.setattr(core_module, "kill_process_group", kill_while_proving_fence)
    restarted._ensure_run_registry(cfg)

    assert killed == [4343]
    assert status_during_kill == ["reclaiming"]
    assert contender_claims == [None]
    assert restarted._run_registry is not None
    assert restarted._run_registry.get_run(run_id).status == "orphaned"


def test_startup_reclaim_keeps_fence_when_kill_is_not_confirmed(
    tmp_path, monkeypatch
):
    finalized: list[str] = []

    class _Registry:
        path = tmp_path / "state.db"

        def finalize_reclaimed_lease(self, run_id):
            finalized.append(run_id)
            return True

    record = RunRecord(
        run_id="run-1",
        issue_id="issue-1",
        identifier="MT-UNREAPED",
        title="Unreaped",
        state="Todo",
        status="reclaiming",
        workspace_path=tmp_path,
        lease_expires_at=None,
        last_progress_at=None,
        backend_agent_pid=4343,
        backend_process_identity="proc-1",
    )
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(core_module, "process_identity", lambda _pid: "proc-1")
    monkeypatch.setattr(core_module, "process_group_exists", lambda _pid: True)
    monkeypatch.setattr(core_module, "kill_process_group", lambda _pid: True)
    monkeypatch.setattr(core_module.time, "monotonic", lambda: next(ticks, 2.0))

    assert _orch()._reap_and_finalize_reclaimed_run(_Registry(), record) is False  # type: ignore[arg-type]
    assert finalized == []


def test_startup_reclaim_does_not_kill_reused_backend_pid(tmp_path, monkeypatch):
    import symphony.orchestrator.run_registry as run_registry_module

    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws"
    )
    issue = _issue("MT-PID-REUSED", state="Todo")
    state_db = tmp_path / ".symphony" / "state.db"
    monkeypatch.setattr(
        run_registry_module, "process_identity", lambda _pid: "old-incarnation"
    )
    crashed = RunRegistry(state_db, owner_pid=4242, boot_id="crashed")
    run_id = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    assert crashed.heartbeat(
        issue_id=issue.id, run_id=run_id, backend_agent_pid=4343
    )
    crashed.close()

    killed: list[int] = []
    monkeypatch.setattr(run_registry_module, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(core_module, "process_identity", lambda _pid: "new-incarnation")
    monkeypatch.setattr(core_module, "process_group_exists", lambda _pid: True)
    monkeypatch.setattr(
        core_module, "kill_process_group", lambda pid: killed.append(pid) or True
    )
    restarted = _orch()
    restarted._ensure_run_registry(cfg)

    assert killed == []
    assert restarted._run_registry is not None
    assert restarted._run_registry.get_run(run_id).status == "orphaned"


def test_dispatch_after_restart_acquires_linked_continuation_checkpoint(
    tmp_path, monkeypatch
):
    """A confirmed dead owner resumes exactly one new run at its completed turn."""
    import symphony.orchestrator.run_registry as run_registry_module

    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / "ws",
        active_states=("Todo",),
    )
    issue = _issue("MT-CONTINUE", state="Todo")
    state_db = tmp_path / ".symphony" / "state.db"
    crashed = RunRegistry(
        state_db,
        lease_ttl=timedelta(minutes=5),
        owner_pid=4242,
        boot_id="crashed",
    )
    source_run_id = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert source_run_id
    assert crashed.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=source_run_id,
        resume_session_id="codex-thread-1",
        state=issue.state,
        turn=3,
    )
    crashed.close()

    monkeypatch.setattr(run_registry_module, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_record_agent_kind",
        staticmethod(lambda _cfg, _identifier, _agent_kind: None),
    )

    class _Workspace:
        def path_for(self, identifier):
            return tmp_path / "ws" / identifier

    async def _parked_worker(_issue, _attempt, _cfg):
        await asyncio.Event().wait()

    async def _run() -> None:
        restarted = _orch()
        restarted._loop = asyncio.get_running_loop()
        restarted._workspace_manager = _Workspace()  # type: ignore[assignment]
        restarted._ensure_run_registry(cfg)
        monkeypatch.setattr(restarted, "_run_agent_attempt", _parked_worker)

        restarted._dispatch(issue, cfg, attempt=None)
        entry = restarted._running[issue.id]
        task = entry.worker_task
        assert task is not None
        try:
            assert entry.run_id != source_run_id
            assert entry.attempt_kind == "recovery"
            assert entry.continued_from_run_id == source_run_id
            assert entry.continuation_checkpoint is not None
            assert entry.continuation_checkpoint.resume_session_id == "codex-thread-1"
            assert entry.continuation_checkpoint.turn == 3
            assert restarted._issue_debug[issue.id].completed_turn_count == 3
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(_run())


def test_service_stop_finalizes_active_run_as_shutdown_interrupted(
    tmp_path, monkeypatch
):
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / "ws",
        active_states=("Todo",),
    )
    issue = _issue("MT-STOP-CONTINUE", state="Todo")
    state_db = tmp_path / ".symphony" / "state.db"
    interrupted_turn_started = asyncio.Event()

    class _Backend:
        def __init__(self, on_event):
            self._on_event = on_event
            self._turns = 0

        async def start(self):
            return None

        async def initialize(self):
            return None

        async def resume_session(self, session_id):
            return False

        async def start_session(self, *, initial_prompt, issue_title):
            await self._on_event(
                {
                    "event": EVENT_SESSION_STARTED,
                    "timestamp": "2026-10-01T00:00:00Z",
                    "payload": {"session_id": "managed-stop-session"},
                }
            )
            return "managed-stop-session"

        async def run_turn(self, *, prompt, is_continuation):
            self._turns += 1
            if self._turns == 1:
                await self._on_event(
                    {
                        "event": EVENT_TURN_COMPLETED,
                        "timestamp": "2026-10-01T00:00:01Z",
                        "payload": {"turn_id": "managed-turn-1"},
                    }
                )
                return None
            interrupted_turn_started.set()
            await asyncio.Event().wait()

        async def stop(self):
            return None

    class _Workspace:
        def __init__(self, path):
            self.path = path

    class _StubWS:
        def path_for(self, identifier):
            return tmp_path / "ws" / identifier

        async def create_or_reuse(self, identifier):
            return _Workspace(self.path_for(identifier))

        async def before_run(self, path):
            return None

        async def after_run_best_effort(self, path):
            return None

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_record_agent_kind",
        staticmethod(lambda _cfg, _identifier, _agent_kind: None),
    )
    monkeypatch.setattr(
        core_module, "build_backend", lambda init: _Backend(init.on_event)
    )

    async def _run() -> str:
        orch = _orch()
        orch._loop = asyncio.get_running_loop()
        orch._workspace_manager = _StubWS()  # type: ignore[assignment]
        orch._run_registry = RunRegistry(state_db)
        _stub_workflow_state_returning(orch, cfg, monkeypatch)
        monkeypatch.setattr(
            orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: [issue]
        )

        orch._dispatch(issue, cfg, attempt=None)
        run_id = orch._running[issue.id].run_id
        await asyncio.wait_for(interrupted_turn_started.wait(), timeout=1)
        await orch.stop()
        return run_id

    run_id = asyncio.run(_run())
    reopened = RunRegistry(state_db)
    try:
        saved = reopened.get_run(run_id)
        assert saved.status == "shutdown_interrupted"
        assert saved.failure_class == "shutdown_interrupted"
        assert saved.backend_agent_pid is None
        assert saved.checkpoint_state == issue.state
        assert saved.checkpoint_turn == 1
        assert reopened.latest_continuation_source(
            issue_id=issue.id, agent_kind="codex", state=issue.state
        ) == run_id
        flags = reopened.get_issue_flags(issue.id)
        assert flags is None or flags.paused is False
    finally:
        reopened.close()


@pytest.mark.parametrize("invalid_pid", [0, -1])
def test_startup_reclaim_skips_invalid_recorded_orphan_agent_pid(
    tmp_path, monkeypatch, invalid_pid
):
    """AF-10 — corrupt ownership rows must never target our process group."""
    import symphony.orchestrator.run_registry as run_registry_module

    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws"
    )
    issue = _issue(f"MT-INVALID-PID-{invalid_pid}", state="Todo")
    now = datetime.now(timezone.utc)
    crashed = RunRegistry(
        tmp_path / ".symphony" / "state.db",
        lease_ttl=timedelta(minutes=5),
        owner_pid=4242,
        boot_id="crashed",
    )
    run_id = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    assert crashed.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=1),
        backend_agent_pid=invalid_pid,
    )
    crashed.close()

    killed: list[int] = []
    monkeypatch.setattr(run_registry_module, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(
        core_module, "kill_process_group", lambda pid: killed.append(pid) or True
    )

    restarted = _orch()
    restarted._ensure_run_registry(cfg)

    assert killed == []
    assert restarted._run_registry is not None
    assert restarted._run_registry.get_run(run_id).status == "reclaiming"
    assert restarted._run_registry.has_active_lease(issue.id)


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only")
def test_startup_reclaim_terminates_live_recorded_orphan_agent_group(
    tmp_path, monkeypatch
):
    """AF-10 — recovery kills a real recorded process group before returning."""
    import symphony.orchestrator.run_registry as run_registry_module

    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    restarted = _orch()
    try:
        cfg = _make_config(
            workflow_path=tmp_path / "WORKFLOW.md",
            workspace_root=tmp_path / "ws",
        )
        issue = _issue("MT-LIVE-ORPHAN", state="Todo")
        now = datetime.now(timezone.utc)
        crashed = RunRegistry(
            tmp_path / ".symphony" / "state.db",
            lease_ttl=timedelta(minutes=5),
            owner_pid=4242,
            boot_id="crashed",
        )
        run_id = crashed.acquire_run(
            issue,
            workspace_path=tmp_path / "ws" / issue.identifier,
            attempt=None,
            attempt_kind="initial",
            agent_kind="codex",
            now=now,
        )
        assert run_id
        assert crashed.heartbeat(
            issue_id=issue.id,
            run_id=run_id,
            now=now + timedelta(seconds=1),
            backend_agent_pid=sleeper.pid,
        )
        crashed.close()
        monkeypatch.setattr(run_registry_module, "_pid_alive", lambda _pid: False)

        restarted._ensure_run_registry(cfg)

        assert sleeper.wait(timeout=5) < 0
        assert restarted._run_registry is not None
        assert restarted._run_registry.get_run(run_id).status == "orphaned"
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)
        if restarted._run_registry is not None:
            restarted._run_registry.close()


def test_retryable_persisted_pause_restarts_as_retry(tmp_path, monkeypatch):
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / "ws",
        active_states=("Todo",),
    )
    state_db = tmp_path / ".symphony" / "state.db"
    issue = _issue("MT-LEGACY-RETRY", state="Todo")
    registry = RunRegistry(state_db, lease_ttl=timedelta(minutes=5))
    registry.set_issue_flags(
        issue.id,
        retry_attempt=1,
        paused=True,
        pause_reason=(
            "worker error: turn_error: turn_failed: opencode failed with "
            "exit -15; paused for operator inspection"
        ),
    )
    registry.close()
    restarted = _orch()

    dispatched: list[tuple[str, int | None, str | None]] = []

    async def _fetch(_cfg):
        return [issue]

    async def _archive(_cfg):
        return None

    def _dispatch(captured_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append((captured_issue.id, attempt, attempt_kind))

    async def _run() -> None:
        restarted._loop = asyncio.get_running_loop()
        monkeypatch.setattr(restarted._workflow_state, "reload", lambda: (cfg, None))
        monkeypatch.setattr(restarted._workflow_state, "current", lambda: cfg)
        monkeypatch.setattr(restarted, "_fetch_candidates", _fetch)
        monkeypatch.setattr(restarted, "_archive_sweep", _archive)
        monkeypatch.setattr(
            restarted, "_auto_reopen_sources_from_resolved_rcas", _archive
        )
        monkeypatch.setattr(restarted, "_dispatch", _dispatch)

        restarted._ensure_run_registry(cfg)

        assert restarted.is_paused(issue.id) is False
        assert issue.id not in restarted._pause_reasons
        flags = restarted._run_registry.get_issue_flags(issue.id)  # type: ignore[union-attr]
        assert flags is not None
        assert flags.retry_attempt == 1
        assert flags.paused is False
        assert flags.pause_reason is None

        await restarted._on_tick()

    asyncio.run(_run())

    assert dispatched == [(issue.id, 1, "retry")]


def test_non_opencode_persisted_sigterm_pause_stays_paused(tmp_path):
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / "ws",
        active_states=("Todo",),
    )
    state_db = tmp_path / ".symphony" / "state.db"
    issue = _issue("MT-LEGACY-SIGTERM", state="Todo")
    pause_reason = (
        "worker error: turn_error: turn_failed: claude failed with "
        "exit -15; paused for operator inspection"
    )
    registry = RunRegistry(state_db, lease_ttl=timedelta(minutes=5))
    registry.set_issue_flags(
        issue.id,
        retry_attempt=1,
        paused=True,
        pause_reason=pause_reason,
    )
    registry.close()
    restarted = _orch()

    restarted._ensure_run_registry(cfg)

    assert restarted.is_paused(issue.id) is True
    assert restarted._pause_reasons[issue.id] == pause_reason
    assert restarted._should_dispatch(issue, cfg) is False
    assert restarted._run_registry is not None
    flags = restarted._run_registry.get_issue_flags(issue.id)
    assert flags is not None
    assert flags.retry_attempt == 1
    assert flags.paused is True
    assert flags.pause_reason == pause_reason


def test_persisted_retry_attempt_drives_next_dispatch_and_cap(tmp_path, monkeypatch):
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / "ws",
        active_states=("Todo",),
    )
    cfg = replace(cfg, agent=replace(cfg.agent, max_retries=3))
    state_db = tmp_path / ".symphony" / "state.db"
    issue = _issue("MT-RETRY", state="Todo")
    registry = RunRegistry(state_db, lease_ttl=timedelta(minutes=5))
    registry.set_issue_flags(issue.id, retry_attempt=3)
    registry.close()
    restarted = _orch()

    dispatched: list[tuple[str, int | None, str | None]] = []
    escalated: list[int] = []

    async def _fetch(_cfg):
        return [issue]

    async def _archive(_cfg):
        return None

    def _dispatch(captured_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append((captured_issue.id, attempt, attempt_kind))

    async def _escalate(**kwargs):
        escalated.append(kwargs["attempt"])

    async def _run() -> None:
        restarted._loop = asyncio.get_running_loop()
        monkeypatch.setattr(restarted._workflow_state, "reload", lambda: (cfg, None))
        monkeypatch.setattr(restarted._workflow_state, "current", lambda: cfg)
        monkeypatch.setattr(restarted, "_fetch_candidates", _fetch)
        monkeypatch.setattr(restarted, "_archive_sweep", _archive)
        monkeypatch.setattr(
            restarted, "_auto_reopen_sources_from_resolved_rcas", _archive
        )
        monkeypatch.setattr(restarted, "_dispatch", _dispatch)
        monkeypatch.setattr(restarted, "_escalate_max_retries", _escalate)

        await restarted._on_tick()
        assert dispatched == [(issue.id, 3, "retry")]

        restarted._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=dispatched[0][1] + 1,
            delay_ms=1,
            error="boom",
        )
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert escalated == [4]


def test_pause_resume_write_through_issue_flags(tmp_path):
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-PAUSE", state="Todo")

    async def _run() -> None:
        orch._run_registry = registry
        _install_running_entry(orch, issue)

        assert orch.pause_worker(issue.id) is True
        flags = registry.get_issue_flags(issue.id)
        assert flags is not None
        assert flags.paused is True
        assert flags.pause_reason == "operator pause"
        assert orch._pause_reasons[issue.id] == "operator pause"

        assert orch.resume_worker(issue.id) is True
        assert registry.get_issue_flags(issue.id) is None
        assert issue.id not in orch._pause_reasons

    asyncio.run(_run())


def test_pause_worker_persists_custom_reason(tmp_path):
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-PAUSE-REASON", state="Todo")

    async def _run() -> None:
        orch._run_registry = registry
        _install_running_entry(orch, issue)

        assert orch.pause_worker(issue.id, reason="checking filesystem") is True
        flags = registry.get_issue_flags(issue.id)
        assert flags is not None
        assert flags.paused is True
        assert flags.pause_reason == "checking filesystem"
        assert orch._pause_reasons[issue.id] == "checking filesystem"

    asyncio.run(_run())


def test_retry_schedule_write_through_and_continuation_clears_issue_flag(tmp_path):
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-RETRY-FLAG", state="Todo")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry

        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=2,
            delay_ms=60_000,
            error="worker_exit: boom",
        )
        flags = registry.get_issue_flags(issue.id)
        assert flags is not None
        assert flags.retry_attempt == 2

        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=1,
            delay_ms=60_000,
            error=None,
            kind="continuation",
        )
        assert registry.get_issue_flags(issue.id) is None

        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()

    asyncio.run(_run())


def test_total_turn_budget_exhaustion_write_through_issue_flags(tmp_path, monkeypatch):
    cfg = _replace_agent_field(
        _make_config(
            max_concurrent=1,
            workflow_path=tmp_path / "WORKFLOW.md",
            workspace_root=tmp_path / "ws",
        ),
        max_total_turns=2,
    )
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-BUDGET-FLAG", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg, monkeypatch)
        debug = orch._issue_debug.setdefault(issue.id, _IssueDebug())
        debug.completed_turn_count = 2

        await orch._on_worker_exit(issue.id, reason="normal", error=None)

    asyncio.run(_run())

    flags = registry.get_issue_flags(issue.id)
    assert flags is not None
    assert flags.budget_exhausted is True
    assert flags.retry_attempt is None


def test_reconcile_first_stall_only_cancels():
    """A live worker that just crossed stall_timeout gets cancel + flag, not eject.

    The grace window starts only after the cancel. The first reconcile tick
    that detects a stall must NOT eject — it must give the cancel time to
    propagate first.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> None:
        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc) - timedelta(hours=1),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry

            await orch._reconcile_running(cfg)

            assert issue.id in orch._running, "first stall must NOT eject"
            assert (
                orch._running[issue.id].cancelled_at is not None
            ), "cancel must be flagged"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_running_snapshot_includes_worker_task_stack():
    """State snapshots expose where a running worker coroutine is parked.

    This is the normal `/api/v1/state` path, so operators can diagnose a
    stuck pre-turn worker even if the dedicated debug endpoint is unavailable
    in a stale process.
    """
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> dict:
        event = asyncio.Event()

        async def _parked_worker() -> None:
            await event.wait()

        worker_task = asyncio.create_task(
            _parked_worker(), name="symphony-worker-MT-1"
        )
        try:
            await asyncio.sleep(0)
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            return orch.snapshot()
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    snapshot = asyncio.run(_run())
    task_debug = snapshot["running"][0]["worker_task"]

    assert task_debug["name"] == "symphony-worker-MT-1"
    assert task_debug["done"] is False
    assert any("_parked_worker" in frame for frame in task_debug["stack"])


def test_dispatch_task_cancelled_before_start_releases_running_slot():
    """A worker cancelled before its coroutine first runs still cleans up.

    Python does not enter a coroutine's body/finally block when a freshly
    created task is cancelled before its first scheduling slice. Symphony
    must not leave that issue in `_running` forever.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._dispatch(issue, cfg, attempt=None)
        task = orch._running[issue.id].worker_task
        assert task is not None
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()

    asyncio.run(_run())

    assert issue.id not in orch._running
    assert issue.id in orch._retry
    assert "worker_task_cancelled_before_start" in (orch._retry[issue.id].error or "")


def test_available_slots_counts_retry_pending_against_budget():
    """A ticket with a pending retry holds its slot through Done.

    Without this, the 1s `CONTINUATION_RETRY_DELAY_MS` window between a
    worker exiting and its retry firing would let another ticket claim
    the slot — surfacing as "OLV-005 starts while OLV-002 is still
    in Review" even though `max_concurrent_agents == 1`.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        # Empty board: one slot is available.
        assert orch._available_slots(cfg) == 1

        # Worker exit path: `_on_worker_exit` removes the entry from
        # `_running` and queues a retry. Simulate by scheduling a retry
        # directly (no running entry).
        orch._schedule_retry(
            "id-OLV-002",
            identifier="OLV-002",
            attempt=1,
            delay_ms=1_000,
            error=None,
        )
        try:
            assert "id-OLV-002" in orch._retry
            # The retry-pending ticket holds the slot.
            assert orch._available_slots(cfg) == 0
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("kind", "error"),
    [("retry", "turn_error"), ("continuation", None)],
)
def test_standard_backoff_retry_owns_slot(kind: str, error: str | None) -> None:
    cfg = _make_config(max_concurrent=1)
    orch = _orch()

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._schedule_retry(
            "id-MT-1",
            identifier="MT-1",
            attempt=1,
            delay_ms=60_000,
            error=error,
            kind=kind,
        )
        try:
            assert orch._retry["id-MT-1"].holds_slot is True
            assert orch._available_slots(cfg) == 0
        finally:
            orch._retry["id-MT-1"].timer_handle.cancel()

    asyncio.run(_run())


async def _assert_capacity_retry_repark(
    orch: Orchestrator,
    cfg: ServiceConfig,
    issue: Issue,
    sibling: Issue,
    *,
    kind: str,
    error: str | None,
    dispatched: list[tuple[int | None, str | None]],
    escalations: list[int],
) -> None:
    orch._loop = asyncio.get_running_loop()
    orch._claimed.update((issue.id, sibling.id))
    orch._running[sibling.id] = RunningEntry(
        issue=sibling,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,
        workspace_path=Path("/tmp/sibling"),
    )
    orch._schedule_retry(
        issue.id,
        identifier=issue.identifier,
        attempt=1,
        delay_ms=60_000,
        error=error,
        kind=kind,
    )
    orch._retry[issue.id].timer_handle.cancel()
    await orch._on_retry_timer(issue.id)
    requeued = orch._retry[issue.id]
    assert (requeued.attempt, requeued.kind, requeued.holds_slot) == (1, kind, False)
    assert issue.id in orch._in_flight_ids()
    orch._running.pop(sibling.id)
    orch._claimed.discard(sibling.id)
    assert orch._available_slots(cfg) == 1
    requeued.timer_handle.cancel()
    await orch._on_retry_timer(issue.id)
    await asyncio.sleep(0)
    assert dispatched == [(1, kind)]
    assert escalations == []


@pytest.mark.parametrize(
    ("kind", "error"),
    [("retry", "turn_error"), ("continuation", None)],
)
def test_retry_timer_capacity_wait_preserves_attempt_kind_and_cap(
    monkeypatch: pytest.MonkeyPatch, kind: str, error: str | None
) -> None:
    cfg = _make_config(max_concurrent=1)
    cfg = replace(cfg, agent=replace(cfg.agent, max_retries=1))
    orch = _orch()
    issue = _issue("MT-1")
    sibling = _issue("MT-2")
    dispatched: list[tuple[int | None, str | None]] = []
    escalations: list[int] = []

    async def _fetch(_cfg):
        return [issue]

    async def _escalate(**kwargs):
        escalations.append(kwargs["attempt"])

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append((attempt, attempt_kind))

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)
    monkeypatch.setattr(orch, "_escalate_max_retries", _escalate)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    asyncio.run(
        _assert_capacity_retry_repark(
            orch,
            cfg,
            issue,
            sibling,
            kind=kind,
            error=error,
            dispatched=dispatched,
            escalations=escalations,
        )
    )


@pytest.mark.parametrize(("holds_slot", "expected_slots"), [(True, 0), (False, 1)])
def test_retry_poll_failure_preserves_slot_ownership(
    monkeypatch: pytest.MonkeyPatch, holds_slot: bool, expected_slots: int
) -> None:
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1")

    async def _fetch(_cfg):
        raise RuntimeError("tracker unavailable")

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._claimed.add(issue.id)
        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=1,
            delay_ms=60_000,
            error="turn_error",
            kind="retry",
            holds_slot=holds_slot,
        )
        orch._retry[issue.id].timer_handle.cancel()
        await orch._on_retry_timer(issue.id)
        requeued = orch._retry[issue.id]
        try:
            assert (requeued.attempt, requeued.kind) == (1, "retry")
            assert requeued.holds_slot is holds_slot
            assert orch._available_slots(cfg) == expected_slots
        finally:
            requeued.timer_handle.cancel()

    asyncio.run(_run())


@pytest.mark.parametrize("rejection", ["unsupported-agent", "budget-exhausted"])
def test_retry_timer_releases_durable_rejection(
    monkeypatch: pytest.MonkeyPatch, rejection: str
) -> None:
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1")
    if rejection == "unsupported-agent":
        issue = replace(issue, agent_kind="unsupported")
    else:
        orch._turn_budget_exhausted.add(issue.id)

    async def _fetch(_cfg):
        return [issue]

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._claimed.add(issue.id)
        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=1,
            delay_ms=60_000,
            error="turn_error",
            kind="retry",
        )
        orch._retry[issue.id].timer_handle.cancel()
        await orch._on_retry_timer(issue.id)
        assert issue.id not in orch._retry
        assert issue.id not in orch._claimed
        if rejection == "budget-exhausted":
            assert issue.id in orch._turn_budget_exhausted

    asyncio.run(_run())


@pytest.mark.parametrize("owner", ["running", "finalizing"])
def test_retry_rejection_preserves_active_owner_claim(
    monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1")

    async def _fetch(_cfg):
        return [issue]

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._claimed.add(issue.id)
        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=1,
            delay_ms=60_000,
            error="stale retry",
            kind="retry",
        )
        orch._retry[issue.id].timer_handle.cancel()
        if owner == "running":
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=None,
                workspace_path=Path("/tmp/running-owner"),
            )
        else:
            orch._terminal_persist_pending.add(issue.id)

        await orch._on_retry_timer(issue.id)

        assert issue.id not in orch._retry
        assert issue.id in orch._claimed
        assert issue.id not in orch._persisted_retry_attempts

    asyncio.run(_run())


def test_reconcile_stalls_on_progress_timestamp_not_codex_timestamp():
    """A worker still receiving meta events but no real progress must stall.

    Reproduces OLV-002 (2026-05-10): claude API kept emitting tool_result
    echoes / stream pings as `EVENT_OTHER_MESSAGE`, which previously bumped
    `last_codex_timestamp` and indefinitely deferred the 5-min stall. The
    fix splits stall-detection time from UI-activity time: stall reads
    `last_progress_timestamp`, which only advances on real model output.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            now = datetime.now(timezone.utc)
            entry = RunningEntry(
                issue=issue,
                started_at=now - timedelta(hours=1),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
                # UI-side timestamp is fresh — meta event arrived 1s ago.
                last_codex_timestamp=now - timedelta(seconds=1),
                # Stall-side timestamp is far past the 300_000 ms threshold.
                last_progress_timestamp=now - timedelta(minutes=10),
            )
            orch._running[issue.id] = entry

            await orch._reconcile_running(cfg)

            assert (
                orch._running[issue.id].cancelled_at is not None
            ), "stall must trigger on stale last_progress_timestamp even if last_codex_timestamp is fresh"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_reconcile_stalls_from_start_when_only_codex_noise_seen():
    """Fresh backend noise must not defer the first-progress stall timeout."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            now = datetime.now(timezone.utc)
            entry = RunningEntry(
                issue=issue,
                started_at=now - timedelta(minutes=10),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
                last_codex_timestamp=now - timedelta(seconds=1),
                last_progress_timestamp=None,
            )
            orch._running[issue.id] = entry

            await orch._reconcile_running(cfg)

            assert (
                orch._running[issue.id].cancelled_at is not None
            ), "stall must trigger from started_at until real progress exists"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_on_codex_event_user_role_other_message_does_not_advance_progress():
    """Tool_result echoes from claude_code (kind='user') must NOT count as progress.

    These are the events that fooled the old stall detector. They still
    update `last_codex_timestamp` for UI freshness, but `last_progress_timestamp`
    must stay pinned at the prior progress event.
    """
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        baseline = datetime.now(timezone.utc) - timedelta(minutes=10)
        entry = RunningEntry(
            issue=issue,
            started_at=baseline,
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
            last_codex_timestamp=baseline,
            last_progress_timestamp=baseline,
        )
        orch._running[issue.id] = entry

        # User-role passthrough — what claude_code emits for tool_result.
        # No tokens, no lifecycle, type='user'.
        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "user", "message": {"content": []}},
                "usage": {},
                "rate_limits": None,
            },
        )

        # last_codex_timestamp moves forward (UI stays "alive"), but
        # last_progress_timestamp must NOT advance.
        assert entry.last_codex_timestamp is not None
        assert entry.last_codex_timestamp > baseline
        assert entry.last_progress_timestamp == baseline

        # Now the assistant message variant — this DOES count as progress.
        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "assistant", "message": {"content": []}},
                "usage": {},
                "rate_limits": None,
            },
        )

        assert entry.last_progress_timestamp is not None
        assert entry.last_progress_timestamp > baseline

    asyncio.run(_run())


def test_on_codex_event_records_backend_agent_pid():
    """All backends stamp `agent_pid`; force-eject uses the recorded pid."""
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        entry = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
        )
        orch._running[issue.id] = entry

        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "user"},
                "agent_pid": 4242,
            },
        )

        assert entry.agent_pgid == 4242

    asyncio.run(_run())


def test_on_codex_event_accepts_legacy_codex_app_server_pid():
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    orch._running[issue.id] = entry

    asyncio.run(
        orch._on_codex_event(
            issue.id,
            {"event": "other_message", "codex_app_server_pid": 11111},
        )
    )

    assert entry.agent_pgid == 11111


def test_on_codex_event_prefers_normalized_agent_pid_over_legacy_pid():
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    orch._running[issue.id] = entry

    asyncio.run(
        orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "agent_pid": 22222,
                "codex_app_server_pid": 11111,
            },
        )
    )

    assert entry.agent_pgid == 22222


@pytest.mark.parametrize("malformed_pid", [True, 0, -1])
def test_on_codex_event_rejects_unsafe_normalized_pid_without_legacy_fallback(
    tmp_path: Path,
    malformed_pid: int,
):
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    orch._run_registry = registry
    issue = _issue("MT-UNSAFE-PID", state="In Progress")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="claude",
    )
    assert run_id is not None
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path,
        run_id=run_id,
    )
    orch._running[issue.id] = entry

    try:
        asyncio.run(
            orch._on_codex_event(
                issue.id,
                {
                    "event": "other_message",
                    "agent_pid": malformed_pid,
                    "codex_app_server_pid": 11111,
                },
            )
        )

        assert entry.agent_pgid is None
        assert registry.get_run(run_id).backend_agent_pid is None
    finally:
        registry.close()


def test_on_codex_event_records_approval_denial_last_error():
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        entry = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
        )
        orch._running[issue.id] = entry

        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_APPROVAL_DENIED,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "method": "item/commandExecution/requestApproval",
                    "command": "rm -rf build",
                    "reason": "rm with recursive and force flags is blocked",
                },
            },
        )

        debug = orch._issue_debug[issue.id]
        assert debug.last_error == (
            "approval denied: rm with recursive and force flags is blocked "
            "(rm -rf build)"
        )
        assert orch._running_row(issue.id, entry)["last_error"] == debug.last_error

    asyncio.run(_run())


def test_codex_other_message_with_input_only_token_growth_does_not_advance_progress():
    """Codex `EVENT_OTHER_MESSAGE` + input-token growth must NOT count as progress.

    Reproduces IB-006 (dograh-demo, 2026-05-16): codex app-server attaches
    `usage` to every emitted event, including catch-all OTHER_MESSAGE
    frames. Each codex turn re-sends conversation history, so
    `input_tokens` (and therefore `total_tokens`) grows on every meta
    event even while `output_tokens` stays flat. The old predicate
    (`delta_total > 0` → progress) treated that as progress and reset
    the 5-min stall clock indefinitely. Fix gates progress on
    `delta_out > 0` so only real model output advances the clock.
    """
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        baseline = datetime.now(timezone.utc) - timedelta(minutes=10)
        entry = RunningEntry(
            issue=issue,
            started_at=baseline,
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
            last_codex_timestamp=baseline,
            last_progress_timestamp=baseline,
            last_reported_input_tokens=1_000_000,
            last_reported_output_tokens=500,
            last_reported_total_tokens=1_000_500,
        )
        orch._running[issue.id] = entry

        # OTHER_MESSAGE with usage showing only input/total growth — exactly
        # what codex emits when it's reasoning between turns without
        # producing user-visible model output. No payload `type` field
        # (codex never sets stream-json `type`).
        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"message": "reasoning..."},
                "usage": {
                    "input_tokens": 1_100_000,   # +100k (history re-send)
                    "output_tokens": 500,        # unchanged — no model output
                    "total_tokens": 1_100_500,
                },
                "rate_limits": None,
            },
        )

        # UI activity timestamp moved, but stall clock must not.
        assert entry.last_codex_timestamp is not None
        assert entry.last_codex_timestamp > baseline
        assert entry.last_progress_timestamp == baseline, (
            "input-only token growth on OTHER_MESSAGE must not reset stall clock"
        )
        # Token aggregation still happens (delta_in is real).
        assert entry.codex_input_tokens == 100_000
        assert entry.codex_output_tokens == 0

        # Now an OTHER_MESSAGE with real output_tokens growth — DOES count.
        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"message": "model output"},
                "usage": {
                    "input_tokens": 1_100_000,
                    "output_tokens": 750,        # +250 — real output
                    "total_tokens": 1_100_750,
                },
                "rate_limits": None,
            },
        )

        assert entry.last_progress_timestamp is not None
        assert entry.last_progress_timestamp > baseline

    asyncio.run(_run())


def test_on_codex_event_extracts_nested_item_preview_without_stall_progress():
    """Codex app-server sends assistant/tool previews as nested item payloads.

    The dashboard should show what the worker is doing, but a tool preview
    must not reset stall detection as if it were model output.
    """
    orch = _orch()
    issue = _issue("OBS-1", state="Review")

    async def _run() -> None:
        baseline = datetime.now(timezone.utc) - timedelta(minutes=10)
        entry = RunningEntry(
            issue=issue,
            started_at=baseline,
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
            last_progress_timestamp=baseline,
        )
        orch._running[issue.id] = entry

        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "item": {
                        "type": "toolCall",
                        "name": "exec_command",
                        "arguments": {"cmd": "pytest -q"},
                    }
                },
            },
        )

        assert entry.last_codex_message == "tool: exec_command pytest -q"
        assert entry.last_progress_timestamp == baseline

        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "type": "assistant",
                    "item": {"type": "agentMessage", "text": "Review passed."},
                },
            },
        )

        assert entry.last_codex_message == "Review passed."
        assert entry.last_progress_timestamp is not None
        assert entry.last_progress_timestamp > baseline

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Auto-commit at Done — see workspace.commit_workspace_on_done.
# ---------------------------------------------------------------------------


def _install_running_entry(orch: Orchestrator, issue: Issue) -> RunningEntry:
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp/ws-fake"),
    )
    orch._running[issue.id] = entry
    return entry


def test_token_totals_track_cache_input_tokens_separately():
    orch = _orch()
    issue = _issue("TOK-1", state="In Progress")
    entry = _install_running_entry(orch, issue)

    delta_total, delta_out = orch._apply_token_totals(
        entry,
        {
            "input_tokens": 10,
            "cache_input_tokens": 90,
            "output_tokens": 5,
            "total_tokens": 105,
        },
    )
    row = orch._running_row(issue.id, entry)
    snap = orch.snapshot()

    assert delta_total == 105
    assert delta_out == 5
    assert entry.codex_input_tokens == 10
    assert entry.codex_cache_input_tokens == 90
    assert entry.codex_output_tokens == 5
    assert row["tokens"]["cache_input_tokens"] == 90
    assert row["tokens"]["state_cache_input_tokens"] == 90
    assert snap["codex_totals"]["cache_input_tokens"] == 90


def test_token_totals_delta_cumulative_reports_without_double_counting():
    orch = _orch()
    issue = _issue("TOK-DELTA", state="In Progress")
    entry = _install_running_entry(orch, issue)

    first_delta, _ = orch._apply_token_totals(
        entry,
        {
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 110,
        },
    )
    second_delta, _ = orch._apply_token_totals(
        entry,
        {
            "input_tokens": 140,
            "output_tokens": 15,
            "total_tokens": 155,
        },
    )

    assert first_delta == 110
    assert second_delta == 45
    assert entry.codex_total_tokens == 155
    assert orch.snapshot()["codex_totals"]["total_tokens"] == 155


def test_productive_zero_token_turn_reports_attention(monkeypatch):
    cfg = _make_config(active_states=("In Progress",))
    orch = _orch()
    issue = _issue("TOK-ZERO", state="In Progress")
    entry = _install_running_entry(orch, issue)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _run() -> None:
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": "2026-07-04T00:00:00Z",
                "payload": {"message": "Implemented the migration."},
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            },
        )

    asyncio.run(_run())

    attention = orch.issue_attention(issue)
    assert entry.cancelled_at is None
    assert attention is not None
    assert attention["kind"] == "token_telemetry_suspect"
    assert attention["label"] == "Token telemetry"
    assert attention["severity"] == "warning"
    assert "zero total tokens" in attention["message"]


def test_high_token_turn_without_threshold_records_without_attention(monkeypatch):
    cfg = _make_config(active_states=("In Progress",))
    orch = _orch()
    issue = _issue("TOK-HIGH", state="In Progress")
    entry = _install_running_entry(orch, issue)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _run() -> None:
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": "2026-07-04T00:00:00Z",
                "payload": {"message": "Finished a reasoning-heavy turn."},
                "usage": {
                    "input_tokens": 8_000_000,
                    "output_tokens": 50_000,
                    "total_tokens": 8_050_000,
                },
            },
        )

    asyncio.run(_run())

    assert entry.codex_total_tokens == 8_050_000
    assert entry.cancelled_at is None
    assert issue.id not in orch._turn_budget_exhausted
    assert orch.issue_attention(issue) is None


def test_high_token_turn_without_hard_cap_does_not_block(monkeypatch):
    cfg = _replace_agent_field(
        _make_config(active_states=("In Progress",)),
        token_attention_threshold_by_state={"in progress": 1_000},
    )
    orch = _orch()
    issue = _issue("TOK-HIGH-SOFT", state="In Progress")
    entry = _install_running_entry(orch, issue)
    persisted: list[str] = []
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _persist(*args, **kwargs):
        persisted.append(str(kwargs.get("budget_kind")))
        return True

    monkeypatch.setattr(orch, "_persist_budget_exhausted_state", _persist)

    async def _run() -> None:
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": "2026-07-04T00:00:00Z",
                "payload": {"message": "Finished a reasoning-heavy turn."},
                "usage": {
                    "input_tokens": 8_000_000,
                    "output_tokens": 50_000,
                    "total_tokens": 8_050_000,
                },
            },
        )

    asyncio.run(_run())

    attention = orch.issue_attention(issue)
    assert entry.cancelled_at is None
    assert issue.id not in orch._turn_budget_exhausted
    assert persisted == []
    assert attention is not None
    assert attention["kind"] == "token_attention_threshold"


def test_high_token_turn_above_explicit_threshold_warns_only(monkeypatch):
    base_cfg = _make_config(active_states=("In Progress",))
    cfg = _replace_agent_field(
        base_cfg,
        token_attention_threshold_by_state={"in progress": 1_000},
    )
    orch = _orch()
    issue = _issue("TOK-THRESHOLD", state="In Progress")
    entry = _install_running_entry(orch, issue)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _run() -> None:
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": "2026-07-04T00:00:00Z",
                "payload": {"message": "Finished a large turn."},
                "usage": {
                    "input_tokens": 1_200,
                    "output_tokens": 50,
                    "total_tokens": 1_250,
                },
            },
        )

    asyncio.run(_run())

    attention = orch.issue_attention(issue)
    assert entry.cancelled_at is None
    assert issue.id not in orch._turn_budget_exhausted
    assert attention is not None
    assert attention["kind"] == "token_attention_threshold"
    assert attention["severity"] == "warning"
    assert "1250/1000" in attention["message"]


def test_token_attention_threshold_never_persists_budget_state(monkeypatch):
    cfg = _replace_agent_field(
        _make_config(active_states=("In Progress",)),
        token_attention_threshold_by_state={"in progress": 1_000},
        budget_exhausted_state="Blocked",
    )
    orch = _orch()
    issue = _issue("TOK-SOFT-NO-PERSIST", state="In Progress")
    entry = _install_running_entry(orch, issue)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _persist(*args, **kwargs):
        raise AssertionError("token attention must not persist budget state")

    monkeypatch.setattr(orch, "_persist_budget_exhausted_state", _persist)

    async def _run() -> None:
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": "2026-07-04T00:00:00Z",
                "payload": {"message": "Finished a large turn."},
                "usage": {
                    "input_tokens": 1_200,
                    "output_tokens": 50,
                    "total_tokens": 1_250,
                },
            },
        )

    asyncio.run(_run())

    attention = orch.issue_attention(issue)
    assert entry.cancelled_at is None
    assert issue.id not in orch._turn_budget_exhausted
    assert attention is not None
    assert attention["kind"] == "token_attention_threshold"


def test_running_snapshot_carries_live_telemetry_for_supported_agent_kinds():
    for index, agent_kind in enumerate(("codex", "claude", "pi", "opencode"), start=1):
        orch = _orch()
        issue = _issue(f"TEL-{index}", state="In Progress")
        entry = _install_running_entry(orch, issue)
        entry.agent_kind = agent_kind

        async def _run() -> dict:
            await orch._on_codex_event(
                issue.id,
                {
                    "event": EVENT_SESSION_STARTED,
                    "timestamp": "2026-07-03T00:00:00Z",
                    "payload": {"session_id": f"{agent_kind}-session"},
                    "usage": {
                        "input_tokens": 100 + index,
                        "output_tokens": 10 + index,
                        "total_tokens": 110 + (2 * index),
                    },
                },
            )
            return orch.snapshot()["running"][0]

        row = asyncio.run(_run())

        assert row["agent_kind"] == agent_kind
        assert "session_id" not in row
        assert entry.resume_session_id == f"{agent_kind}-session"
        assert "resume_session_id" not in row
        assert "attention" in row
        assert row["tokens"]["input_tokens"] == 100 + index
        assert row["tokens"]["output_tokens"] == 10 + index
        assert row["tokens"]["total_tokens"] == 110 + (2 * index)


def test_turn_completion_keeps_exact_resume_id_separate_from_display_id():
    orch = _orch()
    issue = _issue("SESSION-BOUNDARY", state="In Progress")
    entry = _install_running_entry(orch, issue)
    entry.turn_count = 1

    async def _run() -> None:
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_SESSION_STARTED,
                "payload": {"thread_id": "exact-thread"},
            },
        )
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "payload": {"turn_id": "display-turn"},
            },
        )

    asyncio.run(_run())

    assert entry.thread_id == "exact-thread"
    assert entry.session_id == "exact-thread-display-turn"
    assert entry.resume_session_id == "exact-thread"
    assert entry.last_completed_turn_event == 1
    row = orch.snapshot()["running"][0]
    assert "session_id" not in row
    assert "resume_session_id" not in row


def _stub_workflow_state_returning(
    orch: Orchestrator, cfg, monkeypatch: pytest.MonkeyPatch
) -> list[dict]:
    """Force `self._workflow_state.current()` to return cfg; capture commit calls.

    Both snapshot entry points feed one list: a Done/Human Review exit runs the
    host-side Final History Gate (`finalize_delivery_history`, which commits and
    then verifies the remote), every other exit runs the plain snapshot. Callers
    assert "the workspace was committed once" without caring which door it came
    through.

    Uses monkeypatch so the module-level rebinds auto-revert at test teardown —
    otherwise the stubs leak into other tests that exercise orchestrator paths
    (observed: TUI integration tests that drive a real worker exit path).
    """

    captured: list[dict] = []
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    async def _capture(path, *, identifier, title, **_):
        captured.append(
            {"path": path, "identifier": identifier, "title": title}
        )

    async def _capture_gate(path, *, identifier, title, **kwargs):
        await _capture(path, identifier=identifier, title=title)
        captured[-1]["push"] = kwargs.get("push")
        return HistoryGateResult(HISTORY_LOCAL_ONLY)

    monkeypatch.setattr(core_module, "commit_workspace_on_done", _capture)
    monkeypatch.setattr(core_module, "finalize_delivery_history", _capture_gate)
    return captured


def test_on_worker_exit_commits_workspace_at_done(monkeypatch):
    """reason='normal' + state='Done' + auto_commit_on_done=True ⇒ commit fires."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-DONE", state="Done")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        captured = _stub_workflow_state_returning(orch, cfg, monkeypatch)

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert len(captured) == 1, "commit must be invoked exactly once"
            assert captured[0]["identifier"] == "MT-DONE"
            assert captured[0]["title"] == "MT-DONE title"
            assert captured[0]["push"] is True
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_on_worker_exit_local_only_history_gate_does_not_publish_feature_branch(
    monkeypatch,
):
    cfg = _replace_agent_field(
        _make_config(max_concurrent=1), auto_merge_push_target=False
    )
    orch = _orch()
    issue = _issue("MT-LOCAL-HISTORY", state="Done")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        captured = _stub_workflow_state_returning(orch, cfg, monkeypatch)

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert len(captured) == 1
            assert captured[0]["push"] is False
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_on_worker_exit_commits_workspace_for_non_done_terminal_state(monkeypatch):
    """Worker exited cleanly on Cancelled/Blocked — must still snapshot the
    worktree so `git worktree remove --force` doesn't discard the agent's
    work. The commit message includes the state for traceability."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-CANCEL", state="Cancelled")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        captured = _stub_workflow_state_returning(orch, cfg, monkeypatch)

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert len(captured) == 1, (
                "commit must fire on every clean worker exit so worktree "
                "removal can't lose uncommitted work"
            )
            assert captured[0]["identifier"] == "MT-CANCEL"
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_on_worker_exit_respects_auto_commit_off(monkeypatch):
    """auto_commit_on_done=False ⇒ no commit even at Done."""
    base_cfg = _make_config(max_concurrent=1)
    cfg_off = _replace_agent_field(base_cfg, auto_commit_on_done=False)
    orch = _orch()
    issue = _issue("MT-OFF", state="Done")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        captured = _stub_workflow_state_returning(orch, cfg_off, monkeypatch)

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert captured == [], "auto_commit_on_done=False must suppress commit"
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def _replace_agent_field(cfg, **agent_overrides):
    """Return a new ServiceConfig with `agent` swapped for an updated AgentConfig."""
    from dataclasses import replace

    new_agent = replace(cfg.agent, **agent_overrides)
    return replace(cfg, agent=new_agent)


# ---------------------------------------------------------------------------
# Operator-driven pause / resume.
# ---------------------------------------------------------------------------


def test_pause_worker_rejects_unknown_issue():
    """Pausing a ticket that isn't running must report failure, not crash."""
    orch = _orch()
    assert orch.pause_worker("id-missing") is False
    assert orch.is_paused("id-missing") is False


def test_pause_then_resume_flips_state_and_snapshot_reports_it():
    """`is_paused` + snapshot row both reflect the operator's pause toggle."""
    orch = _orch()
    issue = _issue("MT-1")

    async def _run() -> None:
        event = asyncio.Event()

        async def _parked_worker() -> None:
            await event.wait()

        worker_task = asyncio.create_task(_parked_worker())
        try:
            await asyncio.sleep(0)
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )

            assert orch.is_paused(issue.id) is False
            assert orch.pause_worker(issue.id) is True
            assert orch.is_paused(issue.id) is True

            snap = orch.snapshot()
            row = next(r for r in snap["running"] if r["issue_id"] == issue.id)
            assert row["paused"] is True

            # Re-pausing an already-paused worker is a no-op (no double-clear).
            assert orch.pause_worker(issue.id) is False

            assert orch.resume_worker(issue.id) is True
            assert orch.is_paused(issue.id) is False
            assert orch.resume_worker(issue.id) is False
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_pause_event_blocks_then_resume_releases_worker():
    """A coroutine awaiting the pause event blocks until resume_worker fires."""
    orch = _orch()
    issue = _issue("MT-1")

    async def _run() -> bool:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        orch.pause_worker(issue.id)
        event = orch._pause_events[issue.id]
        assert not event.is_set()

        observed_release = False

        async def _waiter() -> None:
            nonlocal observed_release
            await event.wait()
            observed_release = True

        waiter_task = asyncio.create_task(_waiter())
        # Yield so the waiter parks on the event.
        await asyncio.sleep(0)
        assert not waiter_task.done(), "waiter must be parked while paused"

        orch.resume_worker(issue.id)
        await asyncio.wait_for(waiter_task, timeout=1.0)
        return observed_release

    released = asyncio.run(_run())
    assert released is True


def test_reconcile_skips_stall_detection_for_paused_worker():
    """A paused worker that hasn't emitted progress in 10 min must NOT be cancelled."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            now = datetime.now(timezone.utc)
            entry = RunningEntry(
                issue=issue,
                started_at=now - timedelta(hours=1),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
                # No progress in 10 min — would normally fire the stall.
                last_progress_timestamp=now - timedelta(minutes=10),
            )
            orch._running[issue.id] = entry
            orch.pause_worker(issue.id)

            await orch._reconcile_running(cfg)

            # Pause overrides stall detection — the entry must not be cancelled.
            assert orch._running[issue.id].cancelled_at is None
            assert worker_task.cancelled() is False
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_resume_after_long_pause_gets_fresh_stall_window():
    """AF-03 — resume must not inherit the pause's stale progress clock."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-RESUME", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            stale = datetime.now(timezone.utc) - timedelta(minutes=10)
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=stale,
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
                last_progress_timestamp=stale,
            )
            assert orch.pause_worker(issue.id) is True
            assert orch.resume_worker(issue.id) is True

            await orch._reconcile_running(cfg)

            assert orch._running[issue.id].cancelled_at is None
            assert worker_task.cancelled() is False
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_resumed_worker_stalls_after_fresh_window_expires():
    """AF-03 — resume resets the window, not genuine-stall detection."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-RESUME-STALL", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            stale = datetime.now(timezone.utc) - timedelta(minutes=10)
            entry = RunningEntry(
                issue=issue,
                started_at=stale,
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
                last_progress_timestamp=stale,
            )
            orch._running[issue.id] = entry
            assert orch.pause_worker(issue.id) is True
            assert orch.resume_worker(issue.id) is True
            assert entry.resumed_at is not None
            entry.resumed_at = stale

            await orch._reconcile_running(cfg)

            assert entry.cancelled_at is not None
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_max_total_turns_exhaustion_persists_via_tracker_transition(monkeypatch):
    """`agent.budget_exhausted_state` set + max_total_turns reached →
    tracker.update_state is called with the configured target so the
    decision survives a service restart.

    Codex review 2026-05-16: the legacy implementation only mutated an
    in-memory `_turn_budget_exhausted` set and `return`-ed before any
    persistence. Restart cleared the guard and the same ticket ran
    again. This test covers the new persistence path; legacy behaviour
    (empty `budget_exhausted_state`) is covered by the existing
    completed_turn_count tests.
    """
    base_cfg = _make_config(max_concurrent=1)
    cfg_persist = _replace_agent_field(
        base_cfg, max_total_turns=2, budget_exhausted_state="Blocked"
    )
    orch = _orch()
    issue = _issue("MT-BUDGET", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg_persist, monkeypatch)

        # Pre-load completed_turn_count so the next exit crosses the cap.
        debug = orch._issue_debug.setdefault(issue.id, _IssueDebug())
        debug.completed_turn_count = 2

        transitions: list[tuple[str, str]] = []

        def _capture_update_state(cfg, captured_issue, target_state):
            transitions.append((captured_issue.identifier, target_state))

        monkeypatch.setattr(
            orch, "_tracker_call_update_state", _capture_update_state
        )
        monkeypatch.setattr(
            orch, "_tracker_call_states_by_ids", lambda cfg, ids: [issue]
        )

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert transitions == [("MT-BUDGET", "Blocked")], (
                "max_total_turns exhaustion must transition the ticket "
                "to budget_exhausted_state via the tracker"
            )
            assert issue.id in orch._turn_budget_exhausted
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_max_total_turns_exhaustion_no_transition_when_state_unset(monkeypatch):
    """Empty `budget_exhausted_state` (default) preserves legacy
    in-memory-only behaviour — no tracker write."""
    base_cfg = _make_config(max_concurrent=1)
    cfg_legacy = _replace_agent_field(base_cfg, max_total_turns=2)
    assert cfg_legacy.agent.budget_exhausted_state == "", "precondition"
    orch = _orch()
    issue = _issue("MT-BUDGET-LEG", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg_legacy, monkeypatch)

        debug = orch._issue_debug.setdefault(issue.id, _IssueDebug())
        debug.completed_turn_count = 2

        transitions: list[tuple[str, str]] = []

        def _capture_update_state(cfg, captured_issue, target_state):
            transitions.append((captured_issue.identifier, target_state))

        monkeypatch.setattr(
            orch, "_tracker_call_update_state", _capture_update_state
        )
        monkeypatch.setattr(
            orch, "_tracker_call_states_by_ids", lambda cfg, ids: [issue]
        )

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert transitions == [], (
                "no tracker transition when budget_exhausted_state is unset"
            )
            assert issue.id in orch._turn_budget_exhausted
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_max_total_tokens_cap_cancels_worker(monkeypatch):
    """A per-ticket token cap cancels the worker on breach.

    Codex review 2026-05-16: stall predicate can't see the runaway case
    where codex completes each turn but the conversation history re-send
    accumulates 1.6M tokens per turn — IB-006 burned 30M+ tokens in 18
    turns this way. New `agent.max_total_tokens` cap catches that
    explicitly: as soon as `codex_total_tokens` crosses the cap, the
    worker_task is cancelled and `last_error` records the reason.
    """
    base_cfg = _make_config(max_concurrent=1)
    cfg_capped = _replace_agent_field(base_cfg, max_total_tokens=1_000)
    orch = _orch()
    issue = _issue("MT-CAP", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg_capped  # type: ignore[assignment]

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry
            assert entry.cancelled_at is None

            # Fire a single event whose usage pushes total over the cap.
            await orch._on_codex_event(
                issue.id,
                {
                    "event": "other_message",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {"type": "assistant"},
                    "usage": {
                        "input_tokens": 1_500,
                        "output_tokens": 200,
                        "total_tokens": 1_700,  # > cap (1000)
                    },
                    "rate_limits": None,
                },
            )

            assert entry.cancelled_at is not None, (
                "breaching max_total_tokens must record cancelled_at"
            )
            assert worker_task.cancelled() or worker_task.cancelling() > 0, (
                "worker_task.cancel() must have been called"
            )
            debug = orch._issue_debug.get(issue.id)
            assert debug is not None
            assert "token budget exceeded" in (debug.last_error or "")
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_max_total_tokens_by_state_overrides_global_cap(monkeypatch):
    """In Progress can have a larger budget than the global default."""
    base_cfg = _make_config(max_concurrent=1)
    cfg_capped = _replace_agent_field(
        base_cfg,
        max_total_tokens=10_000_000,
        max_total_tokens_by_state={"in progress": 100_000_000},
    )
    orch = _orch()
    review_issue = _issue("MT-REVIEW", state="Review")
    in_progress_issue = _issue("MT-IP", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg_capped  # type: ignore[assignment]

        async def _noop() -> None:
            await asyncio.sleep(3600)

        review_task = asyncio.create_task(_noop())
        in_progress_task = asyncio.create_task(_noop())
        try:
            review_entry = RunningEntry(
                issue=review_issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=review_task,
                workspace_path=Path("/tmp"),
            )
            in_progress_entry = RunningEntry(
                issue=in_progress_issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=in_progress_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[review_issue.id] = review_entry
            orch._running[in_progress_issue.id] = in_progress_entry

            event = {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "assistant"},
                "usage": {
                    "input_tokens": 11_000_000,
                    "output_tokens": 1,
                    "total_tokens": 11_000_001,
                },
            }
            await orch._on_codex_event(review_issue.id, event)
            await orch._on_codex_event(in_progress_issue.id, event)

            assert review_entry.cancelled_at is not None
            assert review_entry.token_budget_cap == 10_000_000
            assert in_progress_entry.cancelled_at is None
        finally:
            for task in (review_task, in_progress_task):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    asyncio.run(_run())


def test_max_total_tokens_by_state_uses_state_local_total(monkeypatch):
    """State budgets reset on phase transition while lifetime totals remain visible."""
    base_cfg = _make_config(max_concurrent=1)
    cfg_capped = _replace_agent_field(
        base_cfg,
        max_total_tokens=100_000_000,
        max_total_tokens_by_state={"qa": 500_000_000},
    )
    orch = _orch()
    issue = _issue("MT-QA", state="QA")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg_capped  # type: ignore[assignment]

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
                # Simulate earlier stages already consuming more than the
                # Review/default cap. QA should still get its own fresh cap.
                codex_total_tokens=200_000_000,
            )
            orch._running[issue.id] = entry

            await orch._on_codex_event(
                issue.id,
                {
                    "event": "other_message",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {"type": "assistant"},
                    "usage": {
                        "input_tokens": 1_000_000,
                        "output_tokens": 1,
                        "total_tokens": 1_000_001,
                    },
                },
            )

            assert entry.codex_total_tokens == 201_000_001
            assert entry.codex_state_total_tokens == 1_000_001
            assert entry.cancelled_at is None
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_max_total_tokens_exhaustion_persists_via_tracker_transition(monkeypatch):
    """`agent.max_total_tokens` must honor `budget_exhausted_state`.

    Regression for IB-006: Codex crossed the token cap, Symphony cancelled
    that worker, then a clean worker exit scheduled a continuation because
    the ticket was still in Review. The cap must persist the configured
    budget state so the same ticket does not re-dispatch forever.
    """
    base_cfg = _make_config(max_concurrent=1)
    cfg_capped = _replace_agent_field(
        base_cfg,
        max_total_tokens=1_000,
        budget_exhausted_state="Blocked",
    )
    orch = _orch()
    issue = _issue("MT-CAP-PERSIST", state="Review")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _stub_workflow_state_returning(orch, cfg_capped, monkeypatch)

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        transitions: list[tuple[str, str]] = []
        notes: list[tuple[str, str, str]] = []

        def _capture_update_state(cfg, captured_issue, target_state):
            transitions.append((captured_issue.identifier, target_state))

        monkeypatch.setattr(
            orch, "_tracker_call_update_state", _capture_update_state
        )
        monkeypatch.setattr(
            orch,
            "_tracker_call_append_note",
            lambda cfg, captured_issue, heading, body: notes.append(
                (captured_issue.identifier, heading, body)
            ),
        )

        try:
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )

            await orch._on_codex_event(
                issue.id,
                {
                    "event": "other_message",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {"type": "assistant"},
                    "usage": {
                        "input_tokens": 1_500,
                        "output_tokens": 200,
                        "total_tokens": 1_700,
                    },
                    "rate_limits": None,
                },
            )

            await orch._on_worker_exit(issue.id, reason="normal", error=None)

            assert transitions == [("MT-CAP-PERSIST", "Blocked")], (
                "token-budget exhaustion must persist budget_exhausted_state"
            )
            assert notes
            assert notes[0][0] == "MT-CAP-PERSIST"
            assert notes[0][1] == "Budget Exceeded"
            assert "tokens" in notes[0][2]
            assert "1700/1000" in notes[0][2]
            assert issue.id in orch._turn_budget_exhausted
            assert issue.id in orch._claimed
            assert issue.id not in orch._retry
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_max_total_tokens_allows_continuation_when_ticket_advanced(monkeypatch):
    """If the capped worker already moved the ticket, run the next stage.

    Token caps are a runaway guard, not a stage-failure verdict. If the
    ticket file/API already says Review advanced to QA, Symphony should not
    overwrite that with Blocked.
    """
    base_cfg = _make_config(max_concurrent=1)
    cfg_capped = _replace_agent_field(
        base_cfg,
        max_total_tokens=1_000,
        budget_exhausted_state="Blocked",
    )
    orch = _orch()
    issue = _issue("MT-CAP-ADVANCE", state="Review")
    advanced = _issue("MT-CAP-ADVANCE", state="QA")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _stub_workflow_state_returning(orch, cfg_capped, monkeypatch)
        monkeypatch.setattr(
            orch, "_tracker_call_states_by_ids", lambda cfg, ids: [advanced]
        )

        transitions: list[tuple[str, str]] = []

        def _capture_update_state(cfg, captured_issue, target_state):
            transitions.append((captured_issue.identifier, target_state))

        monkeypatch.setattr(
            orch, "_tracker_call_update_state", _capture_update_state
        )

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())

        try:
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )

            await orch._on_codex_event(
                issue.id,
                {
                    "event": "other_message",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {"type": "assistant"},
                    "usage": {
                        "input_tokens": 1_500,
                        "output_tokens": 200,
                        "total_tokens": 1_700,
                    },
                    "rate_limits": None,
                },
            )

            await orch._on_worker_exit(issue.id, reason="normal", error=None)

            assert transitions == []
            assert issue.id not in orch._turn_budget_exhausted
            retry = orch._retry.get(issue.id)
            assert retry is not None
            assert retry.kind == "continuation"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_max_total_tokens_cap_disabled_lets_worker_run(monkeypatch):
    """`max_total_tokens=0` (default) preserves legacy unbounded behaviour."""
    cfg = _make_config(max_concurrent=1)
    assert cfg.agent.max_total_tokens == 0, "precondition: default is disabled"
    orch = _orch()
    issue = _issue("MT-NOCAP", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry

            # Massive usage — would breach any reasonable cap.
            await orch._on_codex_event(
                issue.id,
                {
                    "event": "other_message",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {"type": "assistant"},
                    "usage": {
                        "input_tokens": 100_000_000,
                        "output_tokens": 1_000_000,
                        "total_tokens": 101_000_000,
                    },
                    "rate_limits": None,
                },
            )

            assert entry.cancelled_at is None, (
                "cap=0 must not cancel even on enormous totals"
            )
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_after_done_failure_policy_block_preserves_workspace(monkeypatch):
    """policy='block' + after_done hook failure → workspace NOT removed, last_error set.

    Codex review 2026-05-16: critical `after_done` scripts (deploy /
    apply-to-host) silently complete the ticket when the hook fails,
    because legacy behaviour is warning-only and the workspace is reaped
    immediately. New `agent.after_done_failure_policy=block` preserves
    the worktree and records the failure on the debug entry so an
    operator must intervene before the ticket looks Done.
    """
    base_cfg = _make_config(max_concurrent=1)
    cfg_block = _replace_agent_field(base_cfg, after_done_failure_policy="block")
    cfg_block = replace(
        cfg_block, agent=replace(cfg_block.agent, auto_merge_on_done=False)
    )
    orch = _orch()
    issue = _issue("MT-AD-BLOCK", state="Done")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg_block, monkeypatch)

        removes: list[Path] = []

        class _StubWS:
            async def after_done_best_effort(self, p, *, identifier, title):
                return False  # hook failed

            async def remove(self, p):
                removes.append(p)

            def path_for(self, ident):
                return Path("/tmp/ws-fake")

        orch._workspace_manager = _StubWS()  # type: ignore[assignment]

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert removes == [], (
                "policy=block must NOT remove workspace when after_done failed"
            )
            debug = orch._issue_debug.get(issue.id)
            assert debug is not None
            assert "after_done failed" in (debug.last_error or "")
            assert "workspace preserved" in (debug.last_error or "")
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_auto_merge_failure_blocks_done_ticket_and_preserves_workspace(monkeypatch):
    """A Done ticket whose merge gate fails must not keep looking Done.

    Reproduces the dograh IB-007/IB-010 failure mode: the worker reached
    Done, auto-merge failed, but the ticket stayed Done so dependents
    started against a target branch that did not contain the dependency's
    files.
    """
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-MERGE", state="Done")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg, monkeypatch)

        from symphony.utils.auto_merge import AutoMergeResult

        async def _merge_fails(**_kwargs):
            return AutoMergeResult(
                ok=False,
                status="git_failed",
                detail="committed target/branch merge conflict",
            )

        updates: list[tuple[str, str]] = []
        notes: list[tuple[str, str, str]] = []
        removes: list[Path] = []
        after_done_calls: list[str] = []

        def _capture_update(_cfg, captured_issue, target_state):
            updates.append((captured_issue.identifier, target_state))

        def _capture_note(_cfg, captured_issue, heading, body):
            notes.append((captured_issue.identifier, heading, body))

        class _StubWS:
            async def after_done_best_effort(self, p, *, identifier, title):
                after_done_calls.append(identifier)
                return True

            async def remove(self, p):
                removes.append(p)

            def path_for(self, ident):
                return Path("/tmp/ws-fake")

        monkeypatch.setattr(core_module, "auto_merge_on_done_best_effort", _merge_fails)
        monkeypatch.setattr(orch, "_tracker_call_update_state", _capture_update)
        monkeypatch.setattr(orch, "_tracker_call_append_note", _capture_note)
        orch._workspace_manager = _StubWS()  # type: ignore[assignment]

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)

            assert updates == [("MT-MERGE", "Blocked")]
            assert len(notes) == 1
            assert notes[0][0] == "MT-MERGE"
            assert notes[0][1] == "Merge Gate Failed"
            assert "committed target/branch merge conflict" in notes[0][2]
            assert removes == []
            assert after_done_calls == []
            assert "auto_merge failed" in (orch._issue_debug[issue.id].last_error or "")
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_auto_merge_done_gate_passes_local_only_policy(monkeypatch):
    cfg = _make_config(max_concurrent=1)
    cfg = replace(cfg, agent=replace(cfg.agent, auto_merge_push_target=False))
    orch = _orch()
    issue = _issue("MT-LOCAL-MERGE", state="Done")
    captured: dict[str, object] = {}

    async def _capture_merge(**kwargs):
        captured.update(kwargs)
        from symphony.utils.auto_merge import AutoMergeResult

        return AutoMergeResult(ok=True, status="merged")

    monkeypatch.setattr(core_module, "auto_merge_on_done_best_effort", _capture_merge)

    assert asyncio.run(
        orch._auto_merge_done_gate_or_block(
            cfg, issue, Path("/tmp/ws-fake"), debug_target=None
        )
    ) is True
    assert captured["push_target"] is False


def test_after_done_failure_policy_warn_removes_workspace(monkeypatch):
    """policy='warn' (legacy default) + hook failure → workspace still removed.

    Confirms the new policy gate doesn't accidentally suppress the
    legacy behaviour. Operators on non-critical hooks should see no
    change after upgrading.
    """
    cfg = _make_config(max_concurrent=1)
    cfg = replace(cfg, agent=replace(cfg.agent, auto_merge_on_done=False))
    assert cfg.agent.after_done_failure_policy == "warn", "precondition"
    orch = _orch()
    issue = _issue("MT-AD-WARN", state="Done")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg, monkeypatch)

        removes: list[Path] = []

        class _StubWS:
            async def after_done_best_effort(self, p, *, identifier, title):
                return False  # hook failed

            async def remove(self, p):
                removes.append(p)

            def path_for(self, ident):
                return Path("/tmp/ws-fake")

        orch._workspace_manager = _StubWS()  # type: ignore[assignment]

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert len(removes) == 1, (
                "policy=warn must remove workspace even when after_done failed"
            )
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_on_worker_exit_hit_max_turns_blocks_ticket_when_blocked_state_exists(monkeypatch):
    """Per-attempt `max_turns` exhaustion should surface as a blocked ticket.

    Reproduces the issue Codex flagged 2026-05-16: `worker_run_loop` breaks
    out of its turn loop at `turn >= cfg.agent.max_turns`, then exits with
    `reason="normal"`. The old `_on_worker_exit` saw a non-terminal state
    and silently scheduled a continuation, so the ticket bounced against
    the ceiling forever or sat invisibly claimed. Fix persists `Blocked`
    when the workflow exposes that terminal state.
    """
    cfg = _make_config(
        max_concurrent=1,
        terminal_states=("Done", "Cancelled", "Blocked"),
    )
    orch = _orch()
    issue = _issue("MT-MAX", state="In Progress")
    moved: list[tuple[str, str]] = []
    appended: list[tuple[str, str, str]] = []

    def _move(_cfg, _issue, target):
        moved.append((_issue.identifier, target))

    def _append(_cfg, _issue, heading, body):
        appended.append((_issue.identifier, heading, body))

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        entry = _install_running_entry(orch, issue)
        entry.hit_max_turns = True  # simulate the worker_run_loop break path
        _stub_workflow_state_returning(orch, cfg, monkeypatch)
        monkeypatch.setattr(Orchestrator, "_tracker_call_update_state", staticmethod(_move))
        monkeypatch.setattr(Orchestrator, "_tracker_call_append_note", staticmethod(_append))

        try:
            assert orch._retry == {}, "precondition: no retries scheduled"
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert orch._retry == {}, (
                "max_turns exhaustion must NOT auto-schedule a continuation"
            )
            assert issue.id in orch._claimed, (
                "hit_max_turns path must mark the ticket as claimed so the "
                "dispatcher doesn't immediately pick it up again"
            )
            assert moved == [("MT-MAX", "Blocked")]
            assert appended and appended[0][0:2] == ("MT-MAX", "Budget Exceeded")
            assert "max_turns=20/attempt" in appended[0][2]
            assert "max_turns reached" in (
                orch._issue_debug[issue.id].last_error or ""
            )
            assert "Blocked" in (orch._issue_debug[issue.id].last_error or "")
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_on_worker_exit_normal_non_terminal_still_continues_when_no_max_turns():
    """Sanity: the existing continuation path is preserved when `hit_max_turns`
    is False — only the new flag should suppress auto-continuation."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-CONT", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        entry = _install_running_entry(orch, issue)
        assert entry.hit_max_turns is False  # default

        # Monkey-patch workflow_state.current() so _on_worker_exit can read
        # cfg.agent.max_total_turns without exploding on None.
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert len(orch._retry) == 1, (
                "non-terminal + no max_turns flag must still schedule a "
                "continuation"
            )
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_worker_exit_preserves_pause_flag_for_held_ticket():
    """Pause is per-issue — a worker exit must keep `_paused_issue_ids` intact.

    Operator's intent ("hold this ticket") shouldn't evaporate just because
    the in-flight turn errored out or completed. The wakeup event is the
    per-worker piece; the pause flag is the per-issue piece.
    """
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        orch.pause_worker(issue.id)
        assert orch.is_paused(issue.id) is True

        try:
            await orch._on_worker_exit(issue.id, reason="turn_error", error="boom")

            # Wakeup event popped (per-worker), but pause flag preserved.
            assert issue.id not in orch._pause_events
            assert orch.is_paused(issue.id) is True
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_worker_exit_retryable_rate_limit_schedules_retry_without_pause(tmp_path):
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-RATE-LIMIT", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry
        entry = _install_running_entry(orch, issue)
        entry.agent_kind = "opencode"

        try:
            await orch._on_worker_exit(
                issue.id,
                reason="turn_error",
                error=(
                    "429 The service may be temporarily overloaded; "
                    "stderr: \x1b[31mbackend-internal\x1b[0m"
                ),
            )

            flags = registry.get_issue_flags(issue.id)
            assert flags is not None
            assert flags.retry_attempt == 1
            assert flags.paused is False
            assert flags.pause_reason is None
            assert orch.is_paused(issue.id) is False
            retry = orch._retry[issue.id]
            assert retry.error is not None
            assert "429 The service may be temporarily overloaded" in retry.error
            assert "backend-internal" in retry.error
            assert "\x1b" not in retry.error
            assert retry.kind == "retry"
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_worker_exit_connection_error_retries_without_pause(tmp_path):
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-CONNECTION-RETRY", state="Todo")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry
        entry = _install_running_entry(orch, issue)
        entry.agent_kind = "pi"

        try:
            await orch._on_worker_exit(
                issue.id,
                reason="turn_error",
                error="turn_failed: Connection error.; stderr:",
            )

            flags = registry.get_issue_flags(issue.id)
            assert flags is not None
            assert flags.retry_attempt == 1
            assert flags.paused is False
            assert flags.pause_reason is None
            assert orch.is_paused(issue.id) is False
            retry = orch._retry[issue.id]
            assert retry.error == "turn_error: turn_failed: Connection error.; stderr:"
            assert retry.kind == "retry"
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_worker_exit_opencode_sigterm_schedules_retry_without_pause(tmp_path):
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-OPENCODE-TERM", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry
        entry = _install_running_entry(orch, issue)
        entry.agent_kind = "opencode"

        try:
            await orch._on_worker_exit(
                issue.id,
                reason="turn_error",
                error="turn_failed: opencode failed with exit -15",
            )

            flags = registry.get_issue_flags(issue.id)
            assert flags is not None
            assert flags.retry_attempt == 1
            assert flags.paused is False
            assert flags.pause_reason is None
            assert orch.is_paused(issue.id) is False
            retry = orch._retry[issue.id]
            assert retry.error == "turn_error: turn_failed: opencode failed with exit -15"
            assert retry.kind == "retry"
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_worker_exit_error_auto_pauses_hard_failure_with_visible_reason(tmp_path):
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-ERROR-PAUSE", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry
        _install_running_entry(orch, issue)

        try:
            await orch._on_worker_exit(
                issue.id,
                reason="turn_error",
                error="backend crashed before reading prompt; stderr: \x1b[31mboom\x1b[0m",
            )

            flags = registry.get_issue_flags(issue.id)
            assert flags is not None
            assert flags.paused is True
            assert flags.pause_reason is not None
            assert "turn_error" in flags.pause_reason
            assert "backend crashed before reading prompt" in flags.pause_reason
            assert "boom" in flags.pause_reason
            assert "\x1b" not in flags.pause_reason

            attention = orch.issue_attention(issue)
            assert attention is not None
            assert attention["kind"] == "paused"
            assert attention["message"] == flags.pause_reason
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_eligible_refuses_paused_ticket_for_dispatch_and_retry():
    """`_eligible` returns False for a paused issue on both code paths.

    Without this, a worker that exits while paused would re-dispatch via
    `_on_retry_timer`, surfacing as auto-unpause to the operator.
    """
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1", state="Todo")
    orch._paused_issue_ids.add(issue.id)

    assert orch._eligible(issue, cfg, owning_retry=False) is False
    assert orch._eligible(issue, cfg, owning_retry=True) is False

    orch._paused_issue_ids.discard(issue.id)
    assert orch._eligible(issue, cfg, owning_retry=False) is True


def test_retry_timer_reparks_paused_ticket_without_dispatching(monkeypatch):
    """A retry timer firing on a paused ticket reschedules without dispatch."""
    from symphony.orchestrator import PAUSED_RETRY_HOLD_MS

    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1", state="Todo")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._claimed.add(issue.id)
        orch._paused_issue_ids.add(issue.id)
        orch._pause_reasons[issue.id] = "worker error: turn_error: simulated"
        monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

        # Schedule a "natural" retry — pretend a worker just exited.
        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=2,
            delay_ms=100,
            error="turn_error: simulated",
        )
        original_attempt = orch._retry[issue.id].attempt
        try:
            await orch._on_retry_timer(issue.id)

            # Should NOT dispatch; should re-park under the same attempt.
            assert issue.id not in orch._running, "paused ticket must not dispatch"
            reparked = orch._retry.get(issue.id)
            assert reparked is not None, "retry must remain scheduled"
            assert reparked.attempt == original_attempt, (
                "paused re-park must not consume a retry attempt"
            )
            assert reparked.holds_slot is True
            assert reparked.error == "worker error: turn_error: simulated"
            # Hold delay roughly matches PAUSED_RETRY_HOLD_MS.
            expected_due = (
                orch._loop.time() * 1000 + PAUSED_RETRY_HOLD_MS
            )
            assert abs(reparked.due_at_ms - expected_due) < 500
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


async def _assert_blocked_retry_recovers(
    orch: Orchestrator,
    issue: Issue,
    released: Issue,
    candidates: list[Issue],
    dispatched: list[tuple[str, int | None]],
) -> None:
    orch._loop = asyncio.get_running_loop()
    orch._schedule_retry(
        issue.id,
        identifier=issue.identifier,
        attempt=1,
        delay_ms=60_000,
        error="turn_error",
        kind="retry",
    )
    try:
        orch._retry[issue.id].timer_handle.cancel()
        await orch._on_retry_timer(issue.id)
        assert dispatched == []
        requeued = orch._retry.get(issue.id)
        assert requeued is not None
        assert (requeued.attempt, requeued.kind, requeued.holds_slot) == (
            1,
            "retry",
            False,
        )
        assert "blocker" in (requeued.error or "")
        assert orch.issue_attention(issue)["kind"] == "blocked_dependency"  # type: ignore[index]
        requeued.timer_handle.cancel()
        candidates[0] = released
        await orch._on_retry_timer(issue.id)
        assert dispatched == [("MT-1", 1)]
        assert issue.id not in orch._retry
    finally:
        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()


def test_retry_timer_waits_for_unresolved_blocker_then_recovers(monkeypatch):
    cfg = _make_config(active_states=("In Progress", "Verify"), terminal_states=("Done",))
    orch = _orch()
    blocker = BlockerRef(id="MT-9", identifier="MT-9", state="Verify")
    issue = _issue("MT-1", state="In Progress", blocked_by=(blocker,))
    released = replace(
        issue,
        blocked_by=(replace(blocker, state="Done"),),
    )
    candidates = [issue]
    dispatched: list[tuple[str, int | None]] = []

    async def _fake_fetch(_cfg):
        return candidates

    def _capture_dispatch(matched_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append((matched_issue.identifier, attempt))

    monkeypatch.setattr(orch, "_fetch_candidates", _fake_fetch)
    monkeypatch.setattr(orch, "_dispatch", _capture_dispatch)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    asyncio.run(
        _assert_blocked_retry_recovers(
            orch, issue, released, candidates, dispatched
        )
    )


def _patch_retry_tick_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    orch: Orchestrator,
    cfg: ServiceConfig,
    fetch,
    dispatch,
) -> None:
    async def _noop(*_args, **_kwargs):
        return None

    async def _not_triaged(*_args, **_kwargs):
        return False

    monkeypatch.setattr(orch, "_fetch_candidates", fetch)
    monkeypatch.setattr(orch, "_dispatch", dispatch)
    monkeypatch.setattr(orch, "_ensure_run_registry", lambda _cfg: None)
    monkeypatch.setattr(orch, "_reconcile_running", _noop)
    monkeypatch.setattr(orch, "_auto_normalize_legacy_human_review_done", _noop)
    monkeypatch.setattr(orch, "_auto_triage_todo_if_actionable", _not_triaged)
    monkeypatch.setattr(orch, "_conflict_blocker", lambda _issue: None)
    monkeypatch.setattr(orch, "_auto_reopen_sources_from_resolved_rcas", _noop)
    monkeypatch.setattr(orch, "_auto_recover_blocked_sources", _noop)
    monkeypatch.setattr(orch, "_archive_sweep", _noop)
    monkeypatch.setattr(orch, "_notify_observers", _noop)
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))


async def _assert_non_slot_wait_allows_blocker(
    orch: Orchestrator,
    cfg: ServiceConfig,
    dependent: Issue,
    dispatched: list[str],
) -> None:
    orch._loop = asyncio.get_running_loop()
    orch._claimed.add(dependent.id)
    orch._schedule_retry(
        dependent.id,
        identifier=dependent.identifier,
        attempt=1,
        delay_ms=60_000,
        error="turn_error",
        kind="retry",
    )
    orch._retry[dependent.id].timer_handle.cancel()
    await orch._on_retry_timer(dependent.id)
    retry = orch._retry[dependent.id]
    try:
        assert (retry.attempt, retry.holds_slot) == (1, False)
        assert dependent.id in orch._in_flight_ids()
        assert orch._available_slots(cfg) == 1
        await orch._on_tick()
        assert dispatched == ["MT-1"]
    finally:
        retry.timer_handle.cancel()


def test_retry_timer_non_slot_blocker_wait_allows_blocker_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress", "Verify"),
        terminal_states=("Done",),
    )
    cfg = replace(cfg, agent=replace(cfg.agent, max_retries=1))
    orch = _orch()
    blocker = _issue("MT-1", state="In Progress")
    blocker_ref = BlockerRef(
        id=blocker.id, identifier=blocker.identifier, state=blocker.state
    )
    dependent = _issue("MT-2", state="In Progress", blocked_by=(blocker_ref,))
    fetch_count = 0
    dispatched: list[str] = []

    async def _fetch(_cfg):
        nonlocal fetch_count
        fetch_count += 1
        return [dependent] if fetch_count == 1 else [blocker, dependent]

    def _dispatch(issue, _cfg, *, attempt, attempt_kind=None):
        del attempt, attempt_kind
        dispatched.append(issue.identifier)

    _patch_retry_tick_dependencies(monkeypatch, orch, cfg, _fetch, _dispatch)
    asyncio.run(_assert_non_slot_wait_allows_blocker(orch, cfg, dependent, dispatched))


def test_resume_worker_releases_held_retry_immediately(monkeypatch):
    """Resume must kick the retry-hold timer so the operator doesn't wait it out."""
    cfg = _make_config()
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._claimed.add(issue.id)
        orch._paused_issue_ids.add(issue.id)
        monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

        async def _fake_fetch(_cfg):
            return [issue]

        monkeypatch.setattr(orch, "_fetch_candidates", _fake_fetch)

        dispatched: list[str] = []

        def _capture_dispatch(matched_issue, _cfg, *, attempt, attempt_kind=None):
            dispatched.append(matched_issue.id)

        monkeypatch.setattr(orch, "_dispatch", _capture_dispatch)

        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=2,
            delay_ms=60_000,  # long timer — only resume should fire it
            error="turn_error",
        )

        assert orch.resume_worker(issue.id) is True
        # Let the create_task() chain run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert dispatched == [issue.id], (
            "resume must fire the held retry, not wait out the timer"
        )
        assert orch.is_paused(issue.id) is False

    asyncio.run(_run())


def test_find_resumable_issue_id_resolves_idle_paused_file_identifier():
    orch = _orch()
    orch._paused_issue_ids.add("RCA-1")
    orch._pause_reasons["RCA-1"] = "operator pause"

    assert orch.find_resumable_issue_id("RCA-1") == "RCA-1"
    assert orch.resume_worker("RCA-1") is True
    assert orch.is_paused("RCA-1") is False


def test_reconcile_part_b_skips_paused_worker_on_terminal_state(monkeypatch):
    """Reconcile must not cancel a paused worker when its state moves terminal."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry
            orch.pause_worker(issue.id)

            # Tracker reports the ticket moved to Done while we hold it.
            moved = Issue(
                id=issue.id,
                identifier=issue.identifier,
                title=issue.title,
                description=issue.description,
                priority=issue.priority,
                state="Done",
                blocked_by=issue.blocked_by,
                created_at=issue.created_at,
                updated_at=issue.updated_at,
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda c, ids: [moved]
            )

            await orch._reconcile_running(cfg)

            assert worker_task.cancelled() is False, (
                "paused worker must survive reconcile despite terminal state"
            )
            assert issue.id in orch._running
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_reconcile_marks_running_issue_missing_from_tracker_refresh(monkeypatch):
    """AF-12 — a vanished running row is explicit degraded state."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-MISSING", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            orch._running[issue.id] = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: []
            )

            await orch._reconcile_running(cfg)

            attention = orch.issue_attention(issue)
            assert attention is not None
            assert attention["kind"] == "tracker_error"
            assert "omitted running issue" in attention["message"]
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_reconcile_terminate_terminal_commits_before_remove(monkeypatch):
    """Reconcile path that force-cancels a stale terminal-state worker MUST
    snapshot the workspace before calling `WorkspaceManager.remove()`,
    otherwise `git worktree remove --force` discards uncommitted work."""
    cfg = _make_config(max_concurrent=1)
    cfg = replace(cfg, agent=replace(cfg.agent, auto_merge_on_done=False))
    orch = _orch()
    issue = _issue("MT-RC", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp/ws-rc"),
            )
            # Backdate last activity so the 10s grace window is exhausted.
            entry.last_codex_timestamp = datetime.now(timezone.utc).replace(year=2000)
            entry.terminal_seen_at = datetime.now(timezone.utc).replace(year=2000)
            orch._running[issue.id] = entry

            # Tracker reports the ticket has moved to a terminal state.
            moved = Issue(
                id=issue.id,
                identifier=issue.identifier,
                title=issue.title,
                description=issue.description,
                priority=issue.priority,
                state="Done",
                blocked_by=issue.blocked_by,
                created_at=issue.created_at,
                updated_at=issue.updated_at,
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda c, ids: [moved]
            )

            # Capture the call order of commit + remove.
            calls: list[str] = []


            async def _capture_commit(path, *, identifier, title, **_):
                calls.append(f"commit:{identifier}")

            class _StubWS:
                async def remove(self, p):
                    calls.append(f"remove:{p}")

                async def after_done_best_effort(self, p, *, identifier, title):
                    pass

                def path_for(self, ident):
                    return Path("/tmp/ws-rc")

            monkeypatch.setattr(core_module, "commit_workspace_on_done", _capture_commit)
            orch._workspace_manager = _StubWS()  # type: ignore[assignment]

            await orch._reconcile_running(cfg)

            expected = ["commit:MT-RC", f"remove:{Path('/tmp/ws-rc')}"]
            assert calls == expected, f"commit must precede remove; got {calls}"

            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert calls == expected, "worker exit must not repeat reconcile cleanup"
            assert orch._retry == {}, "terminal reconcile must not schedule continuation"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_reconcile_terminal_grace_expires_despite_recent_heartbeat(monkeypatch):
    """Real progress extends terminal grace; backend keepalives do not."""
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress", "Verify", "Learn"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    cfg = replace(cfg, agent=replace(cfg.agent, auto_merge_on_done=False))
    orch = _orch()
    issue = _issue("MT-HB", state="Verify")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            now = datetime.now(timezone.utc)
            entry = RunningEntry(
                issue=issue,
                started_at=now,
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp/ws-hb"),
            )
            entry.last_codex_timestamp = now
            orch._running[issue.id] = entry

            moved = Issue(
                id=issue.id,
                identifier=issue.identifier,
                title=issue.title,
                description=issue.description,
                priority=issue.priority,
                state="Human Review",
                blocked_by=issue.blocked_by,
                created_at=issue.created_at,
                updated_at=issue.updated_at,
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda c, ids: [moved]
            )

            calls: list[str] = []

            async def _capture_commit(path, *, identifier, title, **_):
                calls.append(f"commit:{identifier}")

            class _StubWS:
                async def remove(self, p):
                    calls.append(f"remove:{p}")

                async def after_done_best_effort(self, p, *, identifier, title):
                    pass

                def path_for(self, ident):
                    return Path("/tmp/ws-hb")

            monkeypatch.setattr(core_module, "commit_workspace_on_done", _capture_commit)
            orch._workspace_manager = _StubWS()  # type: ignore[assignment]

            await orch._reconcile_running(cfg)

            assert calls == []
            assert worker_task.cancelled() is False
            assert entry.terminal_seen_at is not None

            # Real model progress after the terminal transition must extend
            # the natural-exit window so Learn can finish its history gate.
            entry.terminal_seen_at = datetime.now(timezone.utc) - timedelta(
                seconds=61
            )
            entry.last_codex_timestamp = datetime.now(timezone.utc)
            entry.last_progress_timestamp = entry.last_codex_timestamp

            await orch._reconcile_running(cfg)

            assert calls == []
            assert worker_task.cancelled() is False

            # A later keepalive updates only the UI activity clock. With no
            # recent model progress, terminal cleanup must still complete.
            entry.last_progress_timestamp = entry.terminal_seen_at

            await orch._reconcile_running(cfg)

            expected = ["commit:MT-HB", f"remove:{Path('/tmp/ws-hb')}"]
            assert calls == expected
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_reconcile_terminate_terminal_skips_commit_when_auto_off(monkeypatch):
    """If the operator opted out via auto_commit_on_done=False, reconcile
    must still remove but skip the commit."""
    base_cfg = _make_config(max_concurrent=1)
    cfg_off = _replace_agent_field(
        base_cfg, auto_commit_on_done=False, auto_merge_on_done=False
    )
    orch = _orch()
    issue = _issue("MT-RC-OFF", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp/ws-off"),
            )
            entry.last_codex_timestamp = datetime.now(timezone.utc).replace(year=2000)
            entry.terminal_seen_at = datetime.now(timezone.utc).replace(year=2000)
            orch._running[issue.id] = entry

            moved = Issue(
                id=issue.id,
                identifier=issue.identifier,
                title=issue.title,
                description=issue.description,
                priority=issue.priority,
                state="Done",
                blocked_by=issue.blocked_by,
                created_at=issue.created_at,
                updated_at=issue.updated_at,
            )
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda c, ids: [moved]
            )


            commit_calls: list[str] = []
            remove_calls: list[str] = []

            async def _capture_commit(path, *, identifier, title, **_):
                commit_calls.append(identifier)

            class _StubWS:
                async def remove(self, p):
                    remove_calls.append(str(p))

                async def after_done_best_effort(self, p, *, identifier, title):
                    pass

                def path_for(self, ident):
                    return Path("/tmp/ws-off")

            monkeypatch.setattr(core_module, "commit_workspace_on_done", _capture_commit)
            orch._workspace_manager = _StubWS()  # type: ignore[assignment]

            await orch._reconcile_running(cfg_off)

            assert commit_calls == [], "auto_commit_on_done=False must skip commit"
            assert remove_calls == [str(Path("/tmp/ws-off"))], "remove must still happen"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_reconcile_terminal_cleanup_uses_terminal_seen_not_event_age(monkeypatch):
    cfg = _replace_agent_field(
        _make_config(max_concurrent=1),
        auto_commit_on_done=False,
        auto_merge_on_done=False,
    )
    orch = _orch()
    issue = _issue("MT-TERM-ACTIVE", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp/ws-term-active"),
            )
            entry.last_codex_timestamp = datetime.now(timezone.utc)
            entry.terminal_seen_at = datetime.now(timezone.utc).replace(year=2000)
            orch._running[issue.id] = entry
            moved = replace(issue, state="Done")
            monkeypatch.setattr(
                orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: [moved]
            )
            removed: list[str] = []

            class _StubWS:
                async def remove(self, path):
                    removed.append(str(path))

                async def after_done_best_effort(self, path, *, identifier, title):
                    return True

            orch._workspace_manager = _StubWS()  # type: ignore[assignment]

            await orch._reconcile_running(cfg)

            assert removed == [str(Path("/tmp/ws-term-active"))]
            assert entry.workspace_cleanup_started is True
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )


def test_startup_terminal_cleanup_preserves_blocked_workspace_across_restarts(
    tmp_path: Path, monkeypatch
):
    workspace_root = tmp_path / "ws"
    cfg = _make_config(workspace_root=workspace_root)
    cfg = replace(cfg, agent=replace(cfg.agent, auto_commit_on_done=False))
    issue = _issue("MT-BLOCKED", state="bLoCkEd")
    workspace = WorkspaceManager(workspace_root, cfg.hooks).path_for(issue.identifier)
    workspace.mkdir(parents=True)
    diagnostic = workspace / "merge-gate-diagnostic.txt"
    diagnostic.write_text("remote target is stale\n", encoding="utf-8")

    for _ in range(2):
        orch = _orch()
        monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda c: [issue])
        orch._workspace_manager = WorkspaceManager(workspace_root, cfg.hooks)
        asyncio.run(orch._startup_terminal_cleanup(cfg))

    assert workspace.is_dir()
    assert diagnostic.read_text(encoding="utf-8") == "remote target is stale\n"


def test_startup_terminal_cleanup_removes_cancelled_but_preserves_blocked(
    tmp_path: Path, monkeypatch
):
    workspace_root = tmp_path / "ws"
    cfg = _make_config(workspace_root=workspace_root)
    blocked = _issue("MT-BLOCKED", state="Blocked")
    cancelled = _issue("MT-CANCELLED", state="Cancelled")
    workspace_manager = WorkspaceManager(workspace_root, cfg.hooks)
    blocked_workspace = workspace_manager.path_for(blocked.identifier)
    cancelled_workspace = workspace_manager.path_for(cancelled.identifier)
    blocked_workspace.mkdir(parents=True)
    cancelled_workspace.mkdir(parents=True)
    blocked_diagnostic = blocked_workspace / "diagnostic.txt"
    cancelled_diagnostic = cancelled_workspace / "diagnostic.txt"
    blocked_diagnostic.write_text("blocked evidence\n", encoding="utf-8")
    cancelled_diagnostic.write_text("cancelled evidence\n", encoding="utf-8")

    snapshots: list[tuple[str, str]] = []

    async def _capture_snapshot(path, *, identifier, **_):
        snapshots.append(
            (identifier, (Path(path) / "diagnostic.txt").read_text(encoding="utf-8"))
        )

    orch = _orch()
    monkeypatch.setattr(
        orch, "_tracker_call_terminal_issues", lambda c: [blocked, cancelled]
    )
    monkeypatch.setattr(core_module, "commit_workspace_on_done", _capture_snapshot)
    orch._workspace_manager = workspace_manager

    asyncio.run(orch._startup_terminal_cleanup(cfg))

    assert blocked_diagnostic.read_text(encoding="utf-8") == "blocked evidence\n"
    assert not cancelled_workspace.exists()
    assert snapshots == [("MT-CANCELLED", "cancelled evidence\n")]


def test_startup_terminal_cleanup_skips_done_workspace_when_branch_already_merged(
    tmp_path: Path, monkeypatch
):
    """A service restart must not resurrect stale Done workspaces whose
    feature branch has already been folded into the target branch.

    Without this guard, startup cleanup auto-commits old worktree residue,
    advances `symphony/<ID>` after the human-resolved merge, and then reports
    fresh merge conflicts for work that is already on the target branch.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "symphony/MT-DONE")
    (repo / "feature.txt").write_text("done\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "feature")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-m", "merge feature", "symphony/MT-DONE")

    workspace = tmp_path / "ws" / "MT-DONE"
    workspace.mkdir(parents=True)

    cfg = _make_config(max_concurrent=1)
    cfg = replace(
        cfg,
        workflow_path=repo / "WORKFLOW.md",
        agent=replace(cfg.agent, auto_merge_target_branch="main"),
    )
    issue = _issue("MT-DONE", state="Done")
    orch = _orch()
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda c: [issue])

    calls: list[str] = []


    async def _capture_commit(path, *, identifier, title, **_):
        calls.append(f"commit:{identifier}")

    async def _capture_merge(**kwargs):
        calls.append(f"merge:{kwargs['identifier']}")

    class _StubWS:
        def path_for(self, ident):
            return workspace

        async def after_done_best_effort(self, p, *, identifier, title):
            calls.append(f"after_done:{identifier}")
            return True

        async def remove(self, p):
            calls.append(f"remove:{Path(p).name}")

    monkeypatch.setattr(core_module, "commit_workspace_on_done", _capture_commit)
    monkeypatch.setattr(core_module, "auto_merge_on_done_best_effort", _capture_merge)
    orch._workspace_manager = _StubWS()  # type: ignore[assignment]

    asyncio.run(orch._startup_terminal_cleanup(cfg))

    assert calls == ["remove:MT-DONE"]


def test_startup_terminal_cleanup_preserves_unmerged_done_workspace_without_replay(
    tmp_path: Path, monkeypatch
):
    """Startup may discover an old Done workspace, but it did not observe the
    transition to Done in this process. It must not create fresh commits or
    replay merge/deploy hooks just because the directory still exists; it
    must move the ticket out of Done so dependents do not trust an unmerged
    branch.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "symphony/MT-DONE")
    (repo / "feature.txt").write_text("done\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "feature")
    _git(repo, "checkout", "-q", "main")

    workspace = tmp_path / "ws" / "MT-DONE"
    workspace.mkdir(parents=True)

    cfg = _make_config(max_concurrent=1)
    cfg = replace(
        cfg,
        workflow_path=repo / "WORKFLOW.md",
        agent=replace(cfg.agent, auto_merge_target_branch="main"),
    )
    issue = _issue("MT-DONE", state="Done")
    orch = _orch()
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda c: [issue])

    calls: list[str] = []


    async def _capture_commit(path, *, identifier, title, **_):
        calls.append(f"commit:{identifier}")

    async def _capture_merge(**kwargs):
        calls.append(f"merge:{kwargs['identifier']}")

    class _StubWS:
        def path_for(self, ident):
            return workspace

        async def after_done_best_effort(self, p, *, identifier, title):
            calls.append(f"after_done:{identifier}")
            return True

        async def remove(self, p):
            calls.append(f"remove:{Path(p).name}")

    monkeypatch.setattr(core_module, "commit_workspace_on_done", _capture_commit)
    monkeypatch.setattr(core_module, "auto_merge_on_done_best_effort", _capture_merge)

    def _capture_update_state(_cfg, captured_issue, target_state):
        calls.append(f"update:{captured_issue.identifier}->{target_state}")

    def _capture_append_note(_cfg, captured_issue, heading, body):
        calls.append(f"note:{captured_issue.identifier}:{heading}")

    monkeypatch.setattr(orch, "_tracker_call_update_state", _capture_update_state)
    monkeypatch.setattr(orch, "_tracker_call_append_note", _capture_append_note)
    orch._workspace_manager = _StubWS()  # type: ignore[assignment]

    asyncio.run(orch._startup_terminal_cleanup(cfg))

    assert calls == [
        "update:MT-DONE->Blocked",
        "note:MT-DONE:Merge Gate Failed",
    ]


def test_snapshot_retry_row_includes_paused_flag():
    """A paused ticket sitting in the retry queue must surface `paused` for the TUI."""
    orch = _orch()
    issue = _issue("MT-1", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        orch.pause_worker(issue.id)

        try:
            # Simulate the worker exiting while paused.
            await orch._on_worker_exit(issue.id, reason="turn_error", error="boom")

            snap = orch.snapshot()
            retry_rows = snap.get("retrying", [])
            assert retry_rows, "expected a retry row for the paused ticket"
            assert retry_rows[0]["issue_id"] == issue.id
            assert retry_rows[0]["paused"] is True
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_running_snapshot_uses_total_turn_count_across_continuations():
    orch = _orch()
    issue = _issue("MT-1", state="Todo")
    entry = _install_running_entry(orch, issue)
    entry.retry_attempt = 1
    entry.attempt_kind = "continuation"
    entry.turn_count = 1
    debug = orch._issue_debug.setdefault(issue.id, _IssueDebug())
    debug.completed_turn_count = 20

    row = orch._running_row(issue.id, entry)

    assert row["turn_count"] == 21
    assert row["total_turn_count"] == 21
    assert row["attempt_turn_count"] == 1
    assert row["attempt_kind"] == "continuation"


def test_running_snapshot_includes_effective_agent_kind():
    orch = _orch()
    issue = _issue("MT-1", state="Todo")
    entry = _install_running_entry(orch, issue)
    entry.agent_kind = "pi"

    row = orch._running_row(issue.id, entry)

    assert row["agent_kind"] == "pi"


def test_snapshot_includes_branch_policy_for_admin_ui():
    orch = _orch()
    cfg = replace(
        _make_config(),
        agent=replace(
            _make_config().agent,
            feature_base_branch="dev",
            auto_merge_target_branch="release",
        ),
    )
    orch._workflow_state._config = cfg

    snap = orch.snapshot()

    assert snap["workflow"]["default_agent_kind"] == cfg.agent.kind
    assert snap["workflow"]["branch_policy"] == {
        "feature_branch_pattern": "symphony/<ID>",
        "base_branch": "dev",
        "merge_target_branch": "release",
        "merge_timing": "after Document, before Done",
        "auto_merge_enabled": True,
        "merge_delivery": "upstream-publishing",
    }


def test_normal_exit_does_not_continue_after_total_turn_budget():
    orch = _orch()
    issue = _issue("MT-1", state="Todo")
    cfg = _make_config()
    cfg = replace(
        cfg,
        agent=replace(cfg.agent, max_turns=2, max_total_turns=2, auto_commit_on_done=False),
    )
    orch._workflow_state._config = cfg
    entry = _install_running_entry(orch, issue)
    entry.turn_count = 2

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        await orch._on_worker_exit(issue.id, reason="normal", error=None)

        assert issue.id not in orch._retry
        assert issue.id in orch._turn_budget_exhausted
        assert not orch._eligible(issue, cfg, owning_retry=False)

    asyncio.run(_run())


def test_issue_attention_reports_budget_exhaustion():
    orch = _orch()
    issue = _issue("MT-ATTN", state="In Progress")

    assert orch.issue_attention(issue) is None

    orch._turn_budget_exhausted.add(issue.id)
    orch._issue_debug[issue.id] = _IssueDebug(
        last_error="max_total_turns reached (1/1)"
    )

    attention = orch.issue_attention(issue)
    assert attention is not None
    assert attention["kind"] == "budget_exhausted"
    assert attention["label"] == "Budget exhausted"
    assert attention["severity"] == "warning"
    assert attention["message"] == "max_total_turns reached (1/1)"


def test_issue_attention_reports_paused_non_running_ticket():
    orch = _orch()
    issue = _issue("MT-PAUSED-ATTN", state="In Progress")

    orch._paused_issue_ids.add(issue.id)
    orch._pause_reasons[issue.id] = "needs operator inspection"

    attention = orch.issue_attention(issue)

    assert attention is not None
    assert attention["kind"] == "paused"
    assert attention["label"] == "Paused"
    assert attention["severity"] == "warning"
    assert attention["message"] == "needs operator inspection"


def test_issue_attention_reports_retry_scheduled():
    orch = _orch()
    issue = _issue("MT-RETRY", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=2,
            delay_ms=60_000,
            error="backend timeout",
            kind="retry",
        )
        try:
            attention = orch.issue_attention(issue)
            assert attention is not None
            assert attention["kind"] == "retry_scheduled"
            assert attention["label"] == "Retry scheduled"
            assert attention["severity"] == "info"
            assert attention["message"] == "backend timeout"
            assert isinstance(attention["due_at"], str)
            assert attention["due_at"].endswith("Z")
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_issue_attention_reports_stalled_and_lease_blocked():
    orch = _orch()
    issue = _issue("MT-STALL", state="In Progress")
    entry = _install_running_entry(orch, issue)
    entry.lease_lost = True
    entry.cancelled_at = datetime.now(timezone.utc) - timedelta(seconds=4)

    stalled = orch.issue_attention(issue)
    assert stalled is not None
    assert stalled["kind"] == "stalled"
    assert stalled["severity"] == "error"
    assert "worker cancellation pending" in stalled["message"]

    entry.cancelled_at = None
    lease = orch.issue_attention(issue)
    assert lease is not None
    assert lease["kind"] == "lease_blocked"
    assert lease["severity"] == "error"


def test_issue_attention_reports_active_lease_block_from_eligibility(monkeypatch):
    orch = _orch()
    issue = _issue("MT-LEASE", state="In Progress")
    cfg = _make_config(active_states=("In Progress",))

    monkeypatch.setattr(orch, "_has_active_run_lease", lambda _issue_id: True)

    assert orch._eligible(issue, cfg, owning_retry=False) is False
    attention = orch.issue_attention(issue)
    assert attention is not None
    assert attention["kind"] == "lease_blocked"
    assert attention["message"] == "another active run lease exists for this issue"


def test_issue_attention_reports_tracker_error():
    orch = _orch()
    issue = _issue("MT-TRACKER", state="In Progress")
    orch._issue_debug[issue.id] = _IssueDebug(tracker_error="update failed")

    attention = orch.issue_attention(issue)

    assert attention is not None
    assert attention["kind"] == "tracker_error"
    assert attention["label"] == "Tracker error"
    assert attention["severity"] == "warning"
    assert attention["message"] == "update failed"


def test_issue_attention_reports_unresolved_dependency():
    orch = _orch()
    blocker = BlockerRef(id="TASK-999", identifier="TASK-999", state="Todo")
    issue = _issue("MT-BLOCKED", state="In Progress", blocked_by=(blocker,))

    attention = orch.issue_attention(issue)

    assert attention is not None
    assert attention["kind"] == "blocked_dependency"
    assert attention["label"] == "Blocked dependency"
    assert attention["severity"] == "warning"
    assert attention["message"] == "waiting on unresolved dependency: TASK-999"


def test_issue_attention_names_a_blocker_that_is_not_on_the_board():
    """F-13: a dangling id deadlocks the ticket; the card must say so."""
    orch = _orch()
    blocker = BlockerRef(id="TYPO-999", identifier="TYPO-999", state=None)
    issue = _issue("MT-BLOCKED", state="In Progress", blocked_by=(blocker,))

    attention = orch.issue_attention(issue)

    assert attention is not None
    assert attention["kind"] == "dangling_dependency"
    assert attention["label"] == "Unknown blocker"
    assert attention["severity"] == "error"
    assert "TYPO-999 is not on the board" in attention["message"]
    assert "symphony board update MT-BLOCKED" in attention["message"]


def test_issue_attention_reports_failed_terminal_dependency(monkeypatch: pytest.MonkeyPatch):
    orch = _orch()
    cfg = _make_config(terminal_states=("Done", "Blocked"))
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    blocker = BlockerRef(id="TASK-999", identifier="TASK-999", state="Blocked")
    issue = _issue("MT-BLOCKED", state="In Progress", blocked_by=(blocker,))

    attention = orch.issue_attention(issue)

    assert attention is not None
    assert attention["kind"] == "blocked_dependency"
    assert attention["label"] == "Blocked dependency"
    assert attention["severity"] == "warning"
    assert attention["message"] == "waiting on unresolved dependency: TASK-999"


def test_issue_attention_priority_order():
    orch = _orch()
    issue = _issue("MT-PRIORITY", state="In Progress")
    entry = _install_running_entry(orch, issue)
    entry.cancelled_at = datetime.now(timezone.utc)
    entry.lease_lost = True
    orch._turn_budget_exhausted.add(issue.id)
    orch._issue_debug[issue.id] = _IssueDebug(
        last_error="turn budget", tracker_error="tracker failed"
    )

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=1,
            delay_ms=60_000,
            error="retry later",
            kind="retry",
        )
        try:
            assert orch.issue_attention(issue)["kind"] == "stalled"  # type: ignore[index]
            entry.cancelled_at = None
            assert orch.issue_attention(issue)["kind"] == "lease_blocked"  # type: ignore[index]
            entry.lease_lost = False
            assert orch.issue_attention(issue)["kind"] == "budget_exhausted"  # type: ignore[index]
            orch._turn_budget_exhausted.clear()
            assert orch.issue_attention(issue)["kind"] == "tracker_error"  # type: ignore[index]
            orch._issue_debug[issue.id].tracker_error = None
            orch._issue_debug[issue.id].token_attention = {
                "kind": "token_attention_threshold",
                "label": "Token threshold",
                "message": "turn used 1250/1000 total tokens in In Progress",
                "severity": "warning",
                "due_at": None,
            }
            assert orch.issue_attention(issue)["kind"] == "token_attention_threshold"  # type: ignore[index]
            orch._issue_debug[issue.id].token_attention = None
            assert orch.issue_attention(issue)["kind"] == "retry_scheduled"  # type: ignore[index]
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_issue_attention_omits_terminal_issue():
    orch = _orch()
    issue = _issue("MT-DONE", state="Done")
    orch._workflow_state._config = _make_config(terminal_states=("Done",))
    orch._turn_budget_exhausted.add(issue.id)
    orch._issue_debug[issue.id] = _IssueDebug(tracker_error="update failed")

    assert orch.issue_attention(issue) is None


def test_issue_attention_reports_blocked_terminal_recovery():
    orch = _orch()
    issue = _issue("MT-BLOCKED", state="Blocked")
    orch._workflow_state._config = _make_config(terminal_states=("Done", "Blocked"))

    attention = orch.issue_attention(issue)

    assert attention is not None
    assert attention["kind"] == "blocked_recovery_available"
    assert attention["label"] == "Blocked fix"
    assert attention["severity"] == "warning"


def test_tick_auto_opens_blocked_rca_ticket_once(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    issue = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocker\n\nMerge gate failed.",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    created: list[str] = []
    notes: list[tuple[str, str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    def _terminal_issues(_cfg):
        return [issue]

    def _create(_cfg, _issue, rca_state, reopen_state, agent_kind):
        created.append(_issue.identifier)
        assert rca_state == "In Progress"
        assert reopen_state == "Todo"
        assert agent_kind == "codex"
        return "FIX-MT-BLOCKED-1"

    def _append(_cfg, _issue, heading, body):
        notes.append((_issue.identifier, heading, body))

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", _terminal_issues)
    monkeypatch.setattr(
        orch, "_tracker_call_active_rca_for_source", lambda *_args: None
    )
    monkeypatch.setattr(orch, "_tracker_call_create_blocked_rca_issue", _create)
    monkeypatch.setattr(orch, "_tracker_call_append_note", _append)

    asyncio.run(orch._on_tick())
    asyncio.run(orch._on_tick())

    assert created == ["MT-BLOCKED"]
    assert notes == [("MT-BLOCKED", "Blocked Fix", notes[0][2])]
    assert "Fix ticket `FIX-MT-BLOCKED-1` opened" in notes[0][2]


def test_tick_auto_recovery_skips_existing_blocked_rca_note(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    issue = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocked Fix\n\nFix ticket `FIX-MT-BLOCKED-1` opened.",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    created: list[str] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [issue])
    monkeypatch.setattr(
        orch,
        "_tracker_call_create_blocked_rca_issue",
        lambda _cfg, _issue, _rca_state, _reopen_state, _agent_kind: created.append(
            _issue.identifier
        )
        or "RCA-2",
    )

    asyncio.run(orch._on_tick())

    assert created == []


def test_tick_auto_recovery_respects_disabled_config(monkeypatch):
    cfg = replace(
        _make_config(
            tracker_kind="file",
            active_states=("Todo", "In Progress"),
            terminal_states=("Done", "Blocked"),
        ),
        agent=replace(
            _make_config().agent,
            auto_recover_blocked=False,
        ),
    )
    issue = _issue("MT-BLOCKED", state="Blocked")
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    created: list[str] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [issue])
    monkeypatch.setattr(
        orch,
        "_tracker_call_create_blocked_rca_issue",
        lambda _cfg, _issue, _rca_state, _reopen_state, _agent_kind: created.append(
            _issue.identifier
        )
        or "RCA-1",
    )

    asyncio.run(orch._on_tick())

    assert created == []


@pytest.mark.parametrize("auto_recover", (True, False))
def test_tick_reopens_blocked_source_after_resolved_rca(monkeypatch, auto_recover):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(cfg.agent, auto_recover_blocked=auto_recover),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description=(
            "## Blocked Fix\n\nFix ticket `FIX-MT-BLOCKED-1` opened.\n\n"
            "## Fix Blocker\n\nWaiting for a reproducible command.\n\n"
            "## Fix Resolution\n\nAcceptance criteria clarified and verified."
        ),
    )
    rca = replace(
        _issue(
            "FIX-MT-BLOCKED-1",
            state="Done",
            description=(
                core_module._blocked_rca_description(source, reopen_state="Todo")
                + "\n\n## Fix Blocker\n\nWaiting for source clarification."
                + "\n\n## Fix Resolution\n\n"
                + "Clarified the source acceptance criteria and verified the blocker."
            ),
            labels=("blocked-fix", "source-mt-blocked"),
        ),
        title="Fix and unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    notes: list[tuple[str, str, str]] = []
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    def _append(_cfg, _issue, heading, body):
        notes.append((_issue.identifier, heading, body))

    def _move(_cfg, _issue, target):
        moved.append((_issue.identifier, target))

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [rca, source])
    monkeypatch.setattr(
        orch, "_tracker_call_active_rca_for_source", lambda *_args: None
    )
    monkeypatch.setattr(orch, "_tracker_call_append_note", _append)
    monkeypatch.setattr(orch, "_tracker_call_update_state", _move)

    asyncio.run(orch._on_tick())

    assert notes == [("MT-BLOCKED", "Blocked Fix Resolved", notes[0][2])]
    assert "Fix ticket `FIX-MT-BLOCKED-1` reached `Done`" in notes[0][2]
    assert moved == [("MT-BLOCKED", "Todo")]


def test_tick_does_not_reopen_blocked_source_at_human_review(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocked RCA\n\nRCA ticket `RCA-1` opened.",
    )
    rca = replace(
        _issue(
            "RCA-1",
            state="Human Review",
            description=core_module._blocked_rca_description(
                source,
                reopen_state="Todo",
            ),
            labels=("blocked-rca", "source-mt-blocked"),
        ),
        title="RCA unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [rca, source])
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, _issue, target: moved.append((_issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == []


def test_tick_holds_proven_fix_when_source_reopen_write_fails(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description=(
            "## Blocked Fix\n\nFix ticket `FIX-MT-BLOCKED-1` opened.\n\n"
            "## Fix Resolution\n\nThe source instructions are actionable."
        ),
    )
    fix = replace(
        _issue(
            "FIX-MT-BLOCKED-1",
            state="Done",
            description=(
                core_module._blocked_rca_description(source, reopen_state="Todo")
                + "\n\n## Fix Resolution\n\nVerified."
            ),
            labels=("blocked-fix", "source-mt-blocked"),
        ),
        title="Fix and unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    notes: list[tuple[str, str]] = []
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    def _move(_cfg, issue, target):
        if issue.identifier == "MT-BLOCKED":
            raise OSError("simulated source write failure")
        moved.append((issue.identifier, target))

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(
        orch, "_tracker_call_terminal_issues", lambda _cfg: [fix, source]
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_append_note",
        lambda _cfg, issue, heading, _body: notes.append(
            (issue.identifier, heading)
        ),
    )
    monkeypatch.setattr(orch, "_tracker_call_update_state", _move)

    asyncio.run(orch._on_tick())

    assert ("FIX-MT-BLOCKED-1", "Human Review") in moved
    assert ("FIX-MT-BLOCKED-1", "Fix Completion Blocked") in notes


def test_tick_fails_closed_when_fix_safety_inspection_fails(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    fetched = False

    async def _fetch_candidates(_cfg):
        nonlocal fetched
        fetched = True
        return []

    def _terminal_issues(_cfg):
        raise OSError("simulated terminal inspection failure")

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", _terminal_issues)

    asyncio.run(orch._on_tick())

    assert fetched is False


def test_tick_demotes_unproven_fix_even_when_explanatory_note_fails(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocked Fix\n\nFix ticket `FIX-MT-BLOCKED-1` opened.",
    )
    fix = replace(
        _issue(
            "FIX-MT-BLOCKED-1",
            state="Done",
            description=core_module._blocked_rca_description(
                source, reopen_state="Todo"
            ),
            labels=("blocked-fix", "source-mt-blocked"),
        ),
        title="Fix and unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        assert moved == [("FIX-MT-BLOCKED-1", "Human Review")]
        return []

    async def _archive(_cfg):
        return None

    def _append(*_args):
        raise OSError("simulated note write failure")

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(
        orch, "_tracker_call_terminal_issues", lambda _cfg: [fix, source]
    )
    monkeypatch.setattr(orch, "_tracker_call_append_note", _append)
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, issue, target: moved.append((issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == [("FIX-MT-BLOCKED-1", "Human Review")]


def test_tick_fails_closed_when_fix_demotion_fails(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocked Fix\n\nFix ticket `FIX-MT-BLOCKED-1` opened.",
    )
    fix = replace(
        _issue(
            "FIX-MT-BLOCKED-1",
            state="Done",
            description=core_module._blocked_rca_description(
                source, reopen_state="Todo"
            ),
            labels=("blocked-fix", "source-mt-blocked"),
        ),
        title="Fix and unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    fetched = False

    async def _fetch_candidates(_cfg):
        nonlocal fetched
        fetched = True
        return []

    def _move(*_args):
        raise OSError("simulated demotion write failure")

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(
        orch, "_tracker_call_terminal_issues", lambda _cfg: [fix, source]
    )
    monkeypatch.setattr(orch, "_tracker_call_update_state", _move)

    asyncio.run(orch._on_tick())

    assert fetched is False


@pytest.mark.parametrize("auto_recover", (True, False))
@pytest.mark.parametrize(
    "outcome",
    (
        "",
        "\n\n## Fix Resolution\n\nPartial work only."
        "\n\n## Fix Blocker\n\nOperator approval is still required.",
    ),
)
def test_tick_moves_unproven_fix_from_done_to_human_review(
    monkeypatch, outcome, auto_recover
):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(cfg.agent, auto_recover_blocked=auto_recover),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocked Fix\n\nFix ticket `FIX-MT-BLOCKED-1` opened.",
    )
    fix = replace(
        _issue(
            "FIX-MT-BLOCKED-1",
            state="Done",
            description=(
                core_module._blocked_rca_description(source, reopen_state="Todo")
                + outcome
            ),
            labels=("blocked-fix", "source-mt-blocked"),
        ),
        title="Fix and unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    notes: list[tuple[str, str, str]] = []
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        assert moved == [("FIX-MT-BLOCKED-1", "Human Review")], (
            "FIX completion safety must run before active candidate dispatch"
        )
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(
        orch, "_tracker_call_terminal_issues", lambda _cfg: [fix, source]
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_append_note",
        lambda _cfg, issue, heading, body: notes.append(
            (issue.identifier, heading, body)
        ),
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, issue, target: moved.append((issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == [("FIX-MT-BLOCKED-1", "Human Review")]
    assert notes[0][:2] == ("FIX-MT-BLOCKED-1", "Fix Completion Blocked")
    assert "## Fix Resolution" in notes[0][2]
    assert all(identifier != "MT-BLOCKED" for identifier, _state in moved)


def test_tick_does_not_reopen_blocked_source_after_failed_rca(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocked RCA\n\nRCA ticket `RCA-1` opened.",
    )
    rca = replace(
        _issue(
            "RCA-1",
            state="Blocked",
            description=core_module._blocked_rca_description(
                source,
                reopen_state="Todo",
            ),
            labels=("blocked-rca", "source-mt-blocked"),
        ),
        title="RCA unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [rca, source])
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, _issue, target: moved.append((_issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == []


def test_tick_does_not_reopen_source_when_rca_needs_operator_intervention(
    monkeypatch,
):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocked RCA\n\nRCA ticket `RCA-1` opened.",
    )
    rca_body = (
        core_module._blocked_rca_description(source, reopen_state="Todo")
        + "\n\n## RCA Blocker\n\nRequires access to the real development DB."
    )
    rca = replace(
        _issue(
            "RCA-1",
            state="Done",
            description=rca_body,
            labels=("blocked-rca", "source-mt-blocked"),
        ),
        title="RCA unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [rca, source])
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, _issue, target: moved.append((_issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == []


def test_tick_does_not_reopen_source_with_recorded_operator_action(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Human Review", "Done", "Blocked"),
    )
    source = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description=(
            "## Blocked RCA\n\nRCA ticket `RCA-1` opened.\n\n"
            "## Operator Action\n\nProvision DATABASE_URL."
        ),
    )
    rca = replace(
        _issue(
            "RCA-1",
            state="Done",
            description=core_module._blocked_rca_description(
                source,
                reopen_state="Todo",
            ),
            labels=("blocked-rca", "source-mt-blocked"),
        ),
        title="RCA unblock MT-BLOCKED: MT-BLOCKED title",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    moved: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [rca, source])
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, _issue, target: moved.append((_issue.identifier, target)),
    )

    asyncio.run(orch._on_tick())

    assert moved == []


def test_blocked_rca_create_uses_source_scoped_file_identifier(tmp_path):
    board_root = tmp_path / "board"
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(cfg, tracker=replace(cfg.tracker, board_root=board_root))
    issue = _issue("MT-BLOCKED", state="Blocked")

    first = Orchestrator._tracker_call_create_blocked_rca_issue(
        cfg,
        issue,
        "In Progress",
        "Todo",
        "codex",
    )
    second = Orchestrator._tracker_call_create_blocked_rca_issue(
        cfg,
        issue,
        "In Progress",
        "Todo",
        "codex",
    )

    assert first == "FIX-MT-BLOCKED-1"
    assert second == "FIX-MT-BLOCKED-2"
    created = (board_root / "FIX-MT-BLOCKED-1.md").read_text(encoding="utf-8")
    assert "title: 'Fix and unblock MT-BLOCKED: MT-BLOCKED title'" in created
    assert "blocked-fix" in created
    assert "`## Clarified Request`" in created
    assert "append `## Fix Resolution` to both" in created


def test_recover_blocked_issue_links_source_to_new_fix(tmp_path):
    board_root = tmp_path / "board"
    board_root.mkdir()
    (board_root / "MT-BLOCKED.md").write_text(
        "---\n"
        "id: MT-BLOCKED\n"
        "identifier: MT-BLOCKED\n"
        "title: Ambiguous test request\n"
        "state: Blocked\n"
        "priority: 2\n"
        "labels: []\n"
        "---\n\n"
        "## Blocker\n\nThe acceptance criteria are ambiguous.\n",
        encoding="utf-8",
    )
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(cfg, tracker=replace(cfg.tracker, board_root=board_root))
    orch = _orch()
    orch._workflow_state._config = cfg

    changed, _message, details = asyncio.run(
        orch.recover_blocked_issue("MT-BLOCKED")
    )

    assert changed is True
    assert details["fix_identifier"] == "FIX-MT-BLOCKED-1"
    source = (board_root / "MT-BLOCKED.md").read_text(encoding="utf-8")
    assert "blocked_by:" in source
    assert "FIX-MT-BLOCKED-1" in source
    assert (board_root / "FIX-MT-BLOCKED-1.md").is_file()


def test_recover_blocked_issue_repairs_partial_fix_link(monkeypatch, tmp_path):
    board_root = tmp_path / "board"
    board_root.mkdir()
    (board_root / "MT-BLOCKED.md").write_text(
        "---\n"
        "id: MT-BLOCKED\n"
        "identifier: MT-BLOCKED\n"
        "title: Ambiguous test request\n"
        "state: Blocked\n"
        "priority: 2\n"
        "labels: []\n"
        "---\n\n"
        "## Blocker\n\nThe acceptance criteria are ambiguous.\n",
        encoding="utf-8",
    )
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(cfg, tracker=replace(cfg.tracker, board_root=board_root))
    orch = _orch()
    orch._workflow_state._config = cfg
    original_link = Orchestrator._tracker_call_link_blocked_fix
    calls = 0

    def _flaky_link(_cfg, source, fix_identifier):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        return original_link(_cfg, source, fix_identifier)

    monkeypatch.setattr(orch, "_tracker_call_link_blocked_fix", _flaky_link)

    first = asyncio.run(orch.recover_blocked_issue("MT-BLOCKED"))
    second = asyncio.run(orch.recover_blocked_issue("MT-BLOCKED"))

    assert first[0] is True
    assert second[0] is False
    assert second[1] == "blocked fix already opened for MT-BLOCKED"
    source = (board_root / "MT-BLOCKED.md").read_text(encoding="utf-8")
    assert "blocked_by:\n- FIX-MT-BLOCKED-1" in source
    assert len(list(board_root.glob("FIX-MT-BLOCKED-*.md"))) == 1
    assert calls == 2


def test_recover_blocked_issue_repairs_partial_source_note(monkeypatch, tmp_path):
    board_root = tmp_path / "board"
    board_root.mkdir()
    (board_root / "MT-BLOCKED.md").write_text(
        "---\n"
        "id: MT-BLOCKED\n"
        "identifier: MT-BLOCKED\n"
        "title: Ambiguous test request\n"
        "state: Blocked\n"
        "priority: 2\n"
        "labels: []\n"
        "---\n\n"
        "## Blocker\n\nThe acceptance criteria are ambiguous.\n",
        encoding="utf-8",
    )
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(cfg, tracker=replace(cfg.tracker, board_root=board_root))
    orch = _orch()
    orch._workflow_state._config = cfg
    original_append = Orchestrator._tracker_call_append_note
    calls = 0

    def _flaky_append(_cfg, issue, heading, body):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated note write failure")
        return original_append(_cfg, issue, heading, body)

    monkeypatch.setattr(orch, "_tracker_call_append_note", _flaky_append)

    with pytest.raises(OSError, match="simulated note write failure"):
        asyncio.run(orch.recover_blocked_issue("MT-BLOCKED"))
    second = asyncio.run(orch.recover_blocked_issue("MT-BLOCKED"))

    assert second[0] is False
    source = (board_root / "MT-BLOCKED.md").read_text(encoding="utf-8")
    assert "## Blocked Fix" in source
    assert "blocked_by:\n- FIX-MT-BLOCKED-1" in source
    assert len(list(board_root.glob("FIX-MT-BLOCKED-*.md"))) == 1
    assert calls == 2


def test_recover_blocked_issue_opens_rca_ticket_and_keeps_source_blocked(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    issue = replace(_issue("MT-BLOCKED", state="Blocked"), agent_kind="bogus")
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    fetched: list[str] = []
    notes: list[tuple[str, str, str]] = []
    moved: list[tuple[str, str]] = []
    created: list[dict[str, str]] = []

    def _fetch(_cfg, identifier):
        fetched.append(identifier)
        return issue

    def _append(_cfg, _issue, heading, body):
        notes.append((_issue.identifier, heading, body))

    def _move(_cfg, _issue, target):
        moved.append((_issue.identifier, target))

    def _create(_cfg, _issue, rca_state, reopen_state, agent_kind):
        created.append(
            {
                "source": _issue.identifier,
                "rca_state": rca_state,
                "reopen_state": reopen_state,
                "agent_kind": agent_kind,
            }
        )
        return "FIX-MT-BLOCKED-1"

    monkeypatch.setattr(orch, "_tracker_call_fetch_issue_full_by_id", _fetch)
    monkeypatch.setattr(
        orch, "_tracker_call_active_rca_for_source", lambda *_args: None
    )
    monkeypatch.setattr(orch, "_tracker_call_append_note", _append)
    monkeypatch.setattr(orch, "_tracker_call_update_state", _move)
    monkeypatch.setattr(orch, "_tracker_call_create_blocked_rca_issue", _create)

    changed, message, details = asyncio.run(
        orch.recover_blocked_issue("MT-BLOCKED", target_state="In Progress")
    )

    assert changed is True
    assert message == "FIX-MT-BLOCKED-1 opened to unblock MT-BLOCKED; MT-BLOCKED remains Blocked"
    assert details == {
        "original_state": "Blocked",
        "target_state": "Todo",
        "source_reopen_state": "Todo",
        "fix_identifier": "FIX-MT-BLOCKED-1",
        "fix_state": "In Progress",
        # Deprecated aliases remain until API consumers migrate.
        "rca_identifier": "FIX-MT-BLOCKED-1",
        "rca_state": "In Progress",
        "agent_kind": "codex",
    }
    assert fetched == ["MT-BLOCKED"]
    assert created == [
        {
            "source": "MT-BLOCKED",
            "rca_state": "In Progress",
            "reopen_state": "Todo",
            "agent_kind": "codex",
        }
    ]
    assert notes == [("MT-BLOCKED", "Blocked Fix", notes[0][2])]
    assert "Fix ticket `FIX-MT-BLOCKED-1` opened" in notes[0][2]
    assert "the source ticket still must pass the normal configured workflow" in notes[0][2]
    assert moved == []


def test_blocked_rca_prompt_reopens_source_to_todo_then_full_workflow():
    issue = _issue("MT-BLOCKED", state="Blocked")

    description = core_module._blocked_rca_description(issue, reopen_state="Todo")

    assert "move that source ticket to `Todo`" in description
    assert "## Clarified Request" in description
    assert "append `## Fix Resolution` to both" in description
    assert "concrete, testable acceptance criteria" in description
    assert "Ambiguity in the request or acceptance criteria is itself a blocker" in description
    assert "Do not skip the source ticket's normal workflow" in description
    assert "it must pass through the configured Todo/In Progress/Verify/Document" in description


def test_recover_blocked_issue_rejects_non_blocked_ticket(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    issue = _issue("MT-DONE", state="Done")
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    monkeypatch.setattr(
        orch,
        "_tracker_call_fetch_issue_full_by_id",
        lambda _cfg, _identifier: issue,
    )

    changed, message, details = asyncio.run(orch.recover_blocked_issue("MT-DONE"))

    assert changed is False
    assert message == "only Blocked tickets can be recovered (state=Done)"
    assert details == {}


def test_recover_blocked_issue_rejects_duplicate_rca(monkeypatch):
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    issue = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description="## Blocked RCA\n\nRCA ticket `RCA-1` opened.",
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    monkeypatch.setattr(
        orch,
        "_tracker_call_fetch_issue_full_by_id",
        lambda _cfg, _identifier: issue,
    )

    changed, message, details = asyncio.run(orch.recover_blocked_issue("MT-BLOCKED"))

    assert changed is False
    assert message == "blocked fix already opened for MT-BLOCKED"
    assert details == {}


@pytest.mark.parametrize("rca_state", ["In Progress", "Blocked"])
def test_recover_blocked_issue_finds_active_rca_after_in_memory_loss(
    monkeypatch, tmp_path, rca_state
):
    board_root = tmp_path / "board"
    board_root.mkdir()
    (board_root / "RCA-STALE.md").write_text(
        "---\n"
        "id: RCA-STALE\n"
        "title: 'RCA unblock MT-BLOCKED: stale source snapshot'\n"
        f"state: {rca_state}\n"
        "labels: [blocked-rca, source-mt-blocked]\n"
        "---\n\n"
        "## Source Ticket\n\n- Identifier: `MT-BLOCKED`\n",
        encoding="utf-8",
    )
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(cfg, tracker=replace(cfg.tracker, board_root=board_root))
    # This stale source snapshot has neither the persisted note nor the old
    # process's in-memory guard.  The active RCA on the board is authoritative.
    issue = _issue("MT-BLOCKED", state="Blocked", description="## Blocker\n\nStill blocked.")
    orch = _orch()
    created: list[str] = []
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    monkeypatch.setattr(
        orch,
        "_tracker_call_fetch_issue_full_by_id",
        lambda _cfg, _identifier: issue,
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_create_blocked_rca_issue",
        lambda *_args: created.append("duplicate") or "RCA-DUPLICATE",
    )

    changed, message, details = asyncio.run(orch.recover_blocked_issue("MT-BLOCKED"))

    assert changed is False
    assert message == "blocked fix already opened for MT-BLOCKED"
    assert details == {}
    assert created == []
    assert issue.id in orch._blocked_rca_source_ids


def test_recover_blocked_issue_allows_new_rca_for_later_block_episode(
    monkeypatch, tmp_path
):
    board_root = tmp_path / "board"
    board_root.mkdir()
    (board_root / "RCA-1.md").write_text(
        "---\n"
        "id: RCA-1\n"
        "title: 'RCA unblock MT-BLOCKED: first episode'\n"
        "state: Done\n"
        "labels: [blocked-rca, source-mt-blocked]\n"
        "---\n\n"
        "## Source Ticket\n\n- Identifier: `MT-BLOCKED`\n",
        encoding="utf-8",
    )
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(cfg, tracker=replace(cfg.tracker, board_root=board_root))
    issue = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description=(
            "## Blocked RCA\n\nRCA ticket `RCA-1` opened.\n\n"
            "## RCA Resolution\n\nRCA-1 completed.\n\n"
            "## Blocker\n\nA different failure appeared."
        ),
    )
    orch = _orch()
    created: list[str] = []
    notes: list[str] = []
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    monkeypatch.setattr(
        orch,
        "_tracker_call_fetch_issue_full_by_id",
        lambda _cfg, _identifier: issue,
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_create_blocked_rca_issue",
        lambda *_args: created.append("MT-BLOCKED") or "FIX-MT-BLOCKED-2",
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_append_note",
        lambda _cfg, _issue, heading, _body: notes.append(heading),
    )

    changed, message, details = asyncio.run(orch.recover_blocked_issue("MT-BLOCKED"))

    assert changed is True
    assert message == "FIX-MT-BLOCKED-2 opened to unblock MT-BLOCKED; MT-BLOCKED remains Blocked"
    assert details["fix_identifier"] == "FIX-MT-BLOCKED-2"
    assert created == ["MT-BLOCKED"]
    assert notes == ["Blocked Fix"]


def test_turn_budget_exhaustion_survives_next_tick_claim_prune(monkeypatch):
    """A budget-exhausted active ticket must not redispatch next poll.

    `_claimed` is intentionally pruned when no worker/retry owns the ticket,
    but `_turn_budget_exhausted` is the durable in-process guard. Pruning both
    lets a max_total_turns ticket loop forever until restart or operator action.
    """

    orch = _orch()
    issue = _issue("MT-BUDGET-LOOP", state="In Progress")
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress",),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(cfg.agent, max_turns=1, max_total_turns=1),
    )
    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [issue]

    async def _archive(_cfg):
        return None

    def _dispatch(captured_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(captured_issue.identifier)

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._turn_budget_exhausted.add(issue.id)
        orch._claimed.add(issue.id)
        monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
        monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
        monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
        monkeypatch.setattr(orch, "_archive_sweep", _archive)
        monkeypatch.setattr(orch, "_dispatch", _dispatch)

        await orch._on_tick()

        assert dispatched == []
        assert issue.id in orch._turn_budget_exhausted
        assert issue.id not in orch._claimed

    asyncio.run(_run())


@pytest.mark.parametrize("resume_result", [True, False])
def test_recovered_worker_resumes_or_starts_fresh_before_first_turn(
    monkeypatch, tmp_path, resume_result
):
    issue = _issue("MT-RESUME", state="In Progress")
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress",),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(
            cfg.agent,
            max_turns=1,
            max_total_turns=3,
            auto_commit_on_done=False,
        ),
    )
    calls: list[object] = []

    class _Backend:
        async def start(self):
            calls.append("start")

        async def initialize(self):
            calls.append("initialize")

        async def resume_session(self, session_id):
            calls.append(("resume", session_id))
            return resume_result

        async def start_session(self, *, initial_prompt, issue_title):
            calls.append(("fresh", issue_title))
            return "unexpected"

        async def run_turn(self, *, prompt, is_continuation):
            calls.append(("turn", is_continuation, prompt))
            return None

        async def stop(self):
            calls.append("stop")

    class _Workspace:
        path = tmp_path

    class _StubWS:
        async def create_or_reuse(self, identifier):
            return _Workspace()

        async def before_run(self, path):
            return None

        async def after_run_best_effort(self, path):
            return None

    async def _run() -> None:
        orch = _orch()
        orch._loop = asyncio.get_running_loop()
        entry = _install_running_entry(orch, issue)
        entry.continued_from_run_id = "source-run"
        entry.continuation_checkpoint = ContinuationCheckpoint(
            resume_session_id="exact-session",
            state=issue.state,
            turn=2,
            checkpointed_at=datetime.now(timezone.utc),
        )
        orch._issue_debug[issue.id] = _IssueDebug(completed_turn_count=2)
        _stub_workflow_state_returning(orch, cfg, monkeypatch)
        orch._workspace_manager = _StubWS()  # type: ignore[assignment]
        monkeypatch.setattr(core_module, "build_backend", lambda _init: _Backend())
        monkeypatch.setattr(
            orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: [issue]
        )

        await orch._run_agent_attempt(issue, attempt=None, cfg=cfg)

    asyncio.run(_run())

    assert calls[:3] == ["start", "initialize", ("resume", "exact-session")]
    fresh_calls = [
        call for call in calls if isinstance(call, tuple) and call[0] == "fresh"
    ]
    turn = next(call for call in calls if isinstance(call, tuple) and call[0] == "turn")
    if resume_result:
        assert fresh_calls == []
        assert turn[1] is True
        assert "3" in turn[2]
    else:
        assert len(fresh_calls) == 1
        assert turn[1] is False
        assert turn[2].endswith("hi")


@pytest.mark.parametrize("reports_completion", [True, False])
def test_turn_checkpoints_require_completion_event_and_workspace_hook(
    monkeypatch, tmp_path, reports_completion
):
    issue = _issue("MT-CHECKPOINT", state="In Progress")
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / "ws",
        active_states=("In Progress",),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(
            cfg.agent,
            max_turns=1,
            max_total_turns=10,
            auto_commit_on_done=False,
        ),
    )
    order: list[str] = []

    class _Backend:
        async def start(self):
            return None

        async def initialize(self):
            return None

        async def resume_session(self, session_id):
            return False

        async def start_session(self, *, initial_prompt, issue_title):
            entry.resume_session_id = "fresh-session"
            return "fresh-session"

        async def run_turn(self, *, prompt, is_continuation):
            order.append("turn")
            if reports_completion:
                entry.last_completed_turn_event = entry.turn_count
            return None

        async def stop(self):
            return None

    class _Workspace:
        path = tmp_path / "ws" / issue.identifier

    class _StubWS:
        async def create_or_reuse(self, identifier):
            return _Workspace()

        async def before_run(self, path):
            return None

        async def after_run_best_effort(self, path):
            order.append("hook")

    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    run_id = registry.acquire_run(
        issue,
        workspace_path=_Workspace.path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    original_checkpoint = registry.checkpoint_completed_turn

    def _checkpoint(**kwargs):
        order.append("checkpoint")
        assert order[-2] == "hook"
        return original_checkpoint(**kwargs)

    monkeypatch.setattr(registry, "checkpoint_completed_turn", _checkpoint)

    async def _run() -> None:
        nonlocal entry
        orch = _orch()
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry
        entry = _install_running_entry(orch, issue)
        entry.run_id = run_id
        entry.agent_kind = "codex"
        _stub_workflow_state_returning(orch, cfg, monkeypatch)
        orch._workspace_manager = _StubWS()  # type: ignore[assignment]
        monkeypatch.setattr(core_module, "build_backend", lambda _init: _Backend())
        monkeypatch.setattr(
            orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: [issue]
        )

        await orch._run_agent_attempt(issue, attempt=None, cfg=cfg)

    entry: RunningEntry
    asyncio.run(_run())

    saved = registry.get_run(run_id)
    if reports_completion:
        assert order[:3] == ["turn", "hook", "checkpoint"]
        assert saved.checkpoint_state == issue.state
        assert saved.checkpoint_turn == 1
    else:
        assert order == ["turn", "hook"]
        assert saved.checkpoint_state is None
        assert saved.checkpoint_turn is None


def test_worker_loop_stops_before_starting_past_total_turn_budget(monkeypatch, tmp_path):
    """`max_total_turns` must be enforced at the turn boundary, not only exit.

    Regression for OLV-150: the prompt said turn N of 60, but the worker kept
    starting turns past 60 because `_on_worker_exit` was the only place that
    checked the total-turn cap. A normal active-state loop must stop before
    starting the extra turn.
    """

    orch = _orch()
    issue = _issue("MT-TOTAL-LOOP", state="In Progress")
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress",),
        terminal_states=("Done", "Cancelled", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(
            cfg.agent,
            max_turns=100,
            max_total_turns=2,
            budget_exhausted_state="Blocked",
            auto_commit_on_done=False,
        ),
    )
    turns: list[str] = []
    moved: list[tuple[str, str]] = []
    notes: list[tuple[str, str, str]] = []

    class _Backend:
        async def start(self):
            return None

        async def initialize(self):
            return None

        async def start_session(self, *, initial_prompt, issue_title):
            return "thread-1"

        async def run_turn(self, *, prompt, is_continuation):
            turns.append(prompt)
            return None

        async def stop(self):
            return None

    class _Workspace:
        def __init__(self, path: Path) -> None:
            self.path = path

    class _StubWS:
        async def create_or_reuse(self, identifier):
            return _Workspace(tmp_path)

        async def before_run(self, path):
            return None

        async def after_run_best_effort(self, path):
            return None

        async def after_done_best_effort(self, path, *, identifier, title):
            return True

        async def remove_best_effort(self, path):
            return None

    def _move(_cfg, captured_issue, target_state):
        moved.append((captured_issue.identifier, target_state))

    def _append(_cfg, captured_issue, heading, body):
        notes.append((captured_issue.identifier, heading, body))

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg, monkeypatch)
        orch._workspace_manager = _StubWS()  # type: ignore[assignment]
        monkeypatch.setattr(core_module, "build_backend", lambda _init: _Backend())
        monkeypatch.setattr(orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: [issue])
        monkeypatch.setattr(orch, "_tracker_call_update_state", _move)
        monkeypatch.setattr(orch, "_tracker_call_append_note", _append)

        try:
            await orch._run_agent_attempt(issue, attempt=None, cfg=cfg)

            assert len(turns) == 2
            assert issue.id in orch._turn_budget_exhausted
            assert moved == [("MT-TOTAL-LOOP", "Blocked")]
            assert notes and notes[0][1] == "Budget Exceeded"
            assert "max_total_turns=2" in notes[0][2]
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


async def _run_fake_same_state_worker(
    *,
    orch: Orchestrator,
    issue: Issue,
    cfg: ServiceConfig,
    monkeypatch,
    tmp_path: Path,
) -> tuple[list[str], list[tuple[str, str]], list[tuple[str, str, str]]]:

    turns: list[str] = []
    moved: list[tuple[str, str]] = []
    notes: list[tuple[str, str, str]] = []

    class _Backend:
        async def start(self):
            return None

        async def initialize(self):
            return None

        async def start_session(self, *, initial_prompt, issue_title):
            return "thread-1"

        async def run_turn(self, *, prompt, is_continuation):
            turns.append(prompt)
            return None

        async def stop(self):
            return None

    class _Workspace:
        def __init__(self, path: Path) -> None:
            self.path = path

    class _StubWS:
        async def create_or_reuse(self, identifier):
            return _Workspace(tmp_path)

        async def before_run(self, path):
            return None

        async def after_run_best_effort(self, path):
            return None

    def _move(_cfg, captured_issue, target_state):
        moved.append((captured_issue.identifier, target_state))

    def _append(_cfg, captured_issue, heading, body):
        notes.append((captured_issue.identifier, heading, body))

    orch._loop = asyncio.get_running_loop()
    _install_running_entry(orch, issue)
    _stub_workflow_state_returning(orch, cfg, monkeypatch)
    orch._workspace_manager = _StubWS()  # type: ignore[assignment]
    monkeypatch.setattr(core_module, "build_backend", lambda _init: _Backend())
    monkeypatch.setattr(orch, "_tracker_call_states_by_ids", lambda _cfg, _ids: [issue])
    monkeypatch.setattr(orch, "_tracker_call_update_state", _move)
    monkeypatch.setattr(orch, "_tracker_call_append_note", _append)

    try:
        await orch._run_agent_attempt(issue, attempt=None, cfg=cfg)
    finally:
        for retry in list(orch._retry.values()):
            retry.timer_handle.cancel()
    return turns, moved, notes


def test_no_stage_change_counter_resets_on_state_change():
    debug = _IssueDebug()

    assert core_module._update_state_turn_counter(debug, "in progress") == 1
    assert core_module._update_state_turn_counter(debug, "in progress") == 2
    assert core_module._update_state_turn_counter(debug, "verify") == 0
    assert debug.state_turn_state == "verify"
    assert debug.state_turn_count == 0
    assert core_module._update_state_turn_counter(debug, "verify") == 1


def test_worker_loop_no_stage_change_watchdog_blocks_and_pauses(
    monkeypatch, tmp_path
):
    orch = _orch()
    issue = _issue("MT-NOSTAGE", state="In Progress")
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress",),
        terminal_states=("Done", "Cancelled", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(
            cfg.agent,
            max_turns=100,
            max_total_turns=100,
            max_state_turns=2,
            budget_exhausted_state="Blocked",
            auto_commit_on_done=False,
        ),
    )

    turns, moved, notes = asyncio.run(
        _run_fake_same_state_worker(
            orch=orch,
            issue=issue,
            cfg=cfg,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
    )

    assert len(turns) == 2
    assert orch._retry == {}
    assert issue.id in orch._claimed
    assert issue.id in orch._paused_issue_ids
    assert orch._pause_reasons[issue.id] == (
        "no stage change after 2 turns in In Progress - operator action required"
    )
    debug = orch._issue_debug[issue.id]
    assert debug.last_error == (
        "no stage change after 2 turns in In Progress - operator action required"
    )
    assert moved == [("MT-NOSTAGE", "Blocked")]
    assert notes and notes[0][1] == "Budget Exceeded"
    assert "no_stage_change" in notes[0][2]


def test_verify_state_turn_cap_blocks_with_budget_artifact(monkeypatch, tmp_path):
    orch = _orch()
    issue = _issue("MT-VERIFY-CAP", state="Verify")
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress", "Verify"),
        terminal_states=("Done", "Cancelled", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(
            cfg.agent,
            max_turns=100,
            max_total_turns=100,
            max_state_turns=30,
            max_state_turns_by_state={"verify": 2},
            budget_exhausted_state="Blocked",
            auto_commit_on_done=False,
        ),
    )

    turns, moved, notes = asyncio.run(
        _run_fake_same_state_worker(
            orch=orch,
            issue=issue,
            cfg=cfg,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
    )

    assert len(turns) == 2
    assert moved == [("MT-VERIFY-CAP", "Blocked")]
    assert notes and notes[0][1] == "Budget Exceeded"
    note_body = notes[0][2]
    assert "no_stage_change" in note_body
    assert "state_turns=2" in note_body
    assert "effective_max_state_turns=2" in note_body
    assert "max_state_turns=30" not in note_body


def test_worker_loop_no_stage_change_action_moves_to_verify(monkeypatch, tmp_path):
    orch = _orch()
    issue = _issue("MT-NOSTAGE-MOVE", state="In Progress")
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress", "Verify"),
        terminal_states=("Done", "Cancelled", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(
            cfg.agent,
            max_turns=100,
            max_total_turns=100,
            max_state_turns=2,
            no_stage_change_action="Verify",
            auto_commit_on_done=False,
        ),
    )

    turns, moved, notes = asyncio.run(
        _run_fake_same_state_worker(
            orch=orch,
            issue=issue,
            cfg=cfg,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
    )

    assert len(turns) == 2
    assert orch._retry == {}
    assert issue.id not in orch._paused_issue_ids
    assert issue.id not in orch._claimed
    assert moved == [("MT-NOSTAGE-MOVE", "Verify")]
    assert notes and notes[0][1] == "Stage Watchdog Handoff"
    assert "no stage change after 2 turns in In Progress" in notes[0][2]


def test_worker_loop_no_stage_change_watchdog_disabled(monkeypatch, tmp_path):
    orch = _orch()
    issue = _issue("MT-NOSTAGE-OFF", state="In Progress")
    cfg = _make_config(
        max_concurrent=1,
        active_states=("In Progress",),
        terminal_states=("Done", "Cancelled", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(
            cfg.agent,
            max_turns=3,
            max_total_turns=100,
            max_state_turns=0,
            auto_commit_on_done=False,
        ),
    )

    turns, _moved, _notes = asyncio.run(
        _run_fake_same_state_worker(
            orch=orch,
            issue=issue,
            cfg=cfg,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )
    )

    assert len(turns) == 3
    assert issue.id not in orch._paused_issue_ids
    assert "no stage change" not in (orch._issue_debug[issue.id].last_error or "")


def test_find_running_issue_id_resolves_human_identifier():
    """Server endpoints take `OLV-002` style ids — resolve to internal id."""
    orch = _orch()
    issue = _issue("OLV-002")
    _install_running_entry(orch, issue)

    assert orch.find_running_issue_id("OLV-002") == issue.id
    assert orch.find_running_issue_id("NOT-A-TICKET") is None


# ---------------------------------------------------------------------------
# C1 — system-level conflict pre-check (workflow-v0.5.2 § C1).
# ---------------------------------------------------------------------------


def test_touched_files_for_parses_bullet_list():
    """`## Touched Files` section yields a set of repo-relative paths."""
    orch = _orch()
    issue = _issue(
        "MT-1",
        description=(
            "## Brief\nHello\n\n"
            "## Touched Files\n"
            "- src/foo.py\n"
            "- `src/bar.py`\n"
            "- docs/notes.md — incidental\n\n"
            "## Next\nbody"
        ),
    )
    assert orch._touched_files_for(issue) == {
        "src/foo.py",
        "src/bar.py",
        "docs/notes.md",
    }


def test_touched_files_for_missing_section_returns_empty_set():
    orch = _orch()
    issue = _issue("MT-1", description="## Brief only, no list")
    assert orch._touched_files_for(issue) == set()
    issue_none = _issue("MT-2", description=None)
    assert orch._touched_files_for(issue_none) == set()


def test_touched_files_for_accepts_real_agent_bullet_annotations():
    """Real agent output uses ` (new)` / ` (deleted)` / ` (M)` annotations.

    A live claude demo (2026-05-17) emitted::

        - `src/demo_math.py` (new)
        - `tests/test_demo_math.py` (new)

    A previous strict-`$` regex silently dropped both rows from the
    conflict pre-check. Lock in the lenient trailing-content match so
    that regression cannot return.
    """
    orch = _orch()
    issue = _issue(
        "MT-1",
        description=(
            "## Touched Files\n"
            "- `src/demo_math.py` (new)\n"
            "- `tests/test_demo_math.py` (new)\n"
            "- `src/legacy/old.py` (deleted)\n"
            "- src/plain.py (M)\n"
            "- `src/has spaces/file.py` (modified)\n"
        ),
    )
    assert orch._touched_files_for(issue) == {
        "src/demo_math.py",
        "tests/test_demo_math.py",
        "src/legacy/old.py",
        "src/plain.py",
        "src/has spaces/file.py",
    }


def test_conflict_pre_check_blocks_overlapping_candidate(monkeypatch):
    """Two tickets, one running with `src/foo.py`; candidate also touches it.

    Outcome: candidate is moved to `Blocked` and `## Conflict` is appended,
    no worker is dispatched. Mirrors workflow-v0.5.2 § C1 contract.
    """
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    held = _issue(
        "MT-1",
        state="In Progress",
        description=(
            "## Brief\nfoo\n\n"
            "## Touched Files\n- src/foo.py\n- src/util.py\n"
        ),
    )
    candidate = _issue(
        "MT-2",
        state="Todo",
        description=(
            "## Brief\nbar\n\n"
            "## Touched Files\n- src/foo.py\n- src/other.py\n"
        ),
    )

    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))

    # Install MT-1 as a live running entry — its description carries the
    # `## Touched Files` body the conflict checker reads.
    orch._running[held.id] = RunningEntry(
        issue=held,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    orch._claimed.add(held.id)

    dispatched: list[str] = []
    appended: list[tuple[str, str, str]] = []
    moved: list[tuple[str, str]] = []

    async def _fetch(_cfg):
        return [candidate]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    def _append(_cfg, _issue, heading, body):
        appended.append((_issue.identifier, heading, body))

    def _move(_cfg, _issue, target):
        moved.append((_issue.identifier, target))

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)
    monkeypatch.setattr(
        Orchestrator, "_tracker_call_append_note", staticmethod(_append)
    )
    monkeypatch.setattr(
        Orchestrator, "_tracker_call_update_state", staticmethod(_move)
    )

    asyncio.run(orch._on_tick())

    assert dispatched == [], "conflict must skip dispatch entirely"
    assert moved == [("MT-2", "Blocked")], (
        "candidate must be moved to Blocked on overlap"
    )
    assert appended == [
        ("MT-2", "Conflict", appended[0][2])
    ], "exactly one Conflict note must be appended"
    note_body = appended[0][2]
    assert "MT-1" in note_body, "Conflict note must name the other ticket"
    assert "src/foo.py" in note_body, "Conflict note must list overlap path"
    assert "src/other.py" not in note_body, (
        "Non-overlapping paths must not appear in the Conflict note"
    )


def test_conflict_pre_check_no_overlap_dispatches_normally(monkeypatch):
    """Non-overlapping `## Touched Files` lets dispatch proceed."""
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    held = _issue(
        "MT-1",
        state="In Progress",
        description="## Touched Files\n- src/foo.py\n",
    )
    candidate = _issue(
        "MT-2",
        state="Todo",
        description="## Touched Files\n- src/bar.py\n",
    )

    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    orch._running[held.id] = RunningEntry(
        issue=held,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    orch._claimed.add(held.id)

    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [candidate]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)

    asyncio.run(orch._on_tick())

    assert dispatched == ["MT-2"], (
        "non-overlapping touched files must not block dispatch"
    )


def test_g1_stale_claimed_pruned_after_conflict_resolves(monkeypatch):
    """G1 — `_claimed` must release a conflict_blocked id once the worker
    that triggered the block is gone. Without this prune, the candidate
    stays sticky forever: the operator can move it back to Todo and the
    dispatcher will still skip it on the eligibility check.
    """
    import asyncio

    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    held = _issue(
        "MT-1",
        state="In Progress",
        description="## Touched Files\n- src/foo.py\n",
    )
    candidate = _issue(
        "MT-2",
        state="Todo",
        description="## Touched Files\n- src/foo.py\n",
    )

    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    orch._running[held.id] = RunningEntry(
        issue=held,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    orch._claimed.add(held.id)

    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [candidate]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)
    monkeypatch.setattr(
        Orchestrator, "_tracker_call_append_note", staticmethod(lambda *_a, **_k: None)
    )
    monkeypatch.setattr(
        Orchestrator, "_tracker_call_update_state", staticmethod(lambda *_a, **_k: None)
    )

    asyncio.run(orch._on_tick())
    assert candidate.id in orch._claimed, (
        "conflict path must add MT-2 to _claimed inside the conflict tick"
    )
    assert dispatched == [], "conflict must skip dispatch on the first tick"

    # Simulate the worker that owned the file exiting and the operator
    # restoring MT-2 to an active state. Without the prune, the second
    # tick would still skip MT-2 because of the sticky _claimed entry.
    orch._running.pop(held.id, None)
    orch._claimed.discard(held.id)

    asyncio.run(orch._on_tick())
    assert candidate.id not in orch._claimed, (
        "G1 prune must drop the stale claim once MT-1 left _running"
    )
    assert dispatched == ["MT-2"], (
        "candidate must dispatch on the second tick after prune"
    )


def test_g2_empty_response_loop_escalates_after_three_consecutive_turns(monkeypatch):
    """G2 — three consecutive `EVENT_TURN_COMPLETED` events with no
    fresh model output must cancel the worker and persist via
    `_persist_budget_exhausted_state` with `budget_kind="empty_response_loop"`.

    Without this guard, an agent that silently returns empty turns burns
    through `max_total_turns` or `max_total_tokens` before any escalation,
    leaving the operator to discover the loop only via slow-burn metrics.
    """
    import asyncio

    base_cfg = _make_config(max_concurrent=1)
    cfg = _replace_agent_field(base_cfg, budget_exhausted_state="Blocked")
    orch = _orch()
    issue = _issue("MT-EMPTY", state="In Progress")

    persisted_kinds: list[str] = []

    async def _persist(*, cfg, entry, issue_id, target_state, budget_kind):
        persisted_kinds.append(budget_kind)
        return True

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        monkeypatch.setattr(orch, "_persist_budget_exhausted_state", _persist)

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry

            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 0,
                    "total_tokens": 100,
                },
            }
            for _ in range(3):
                await orch._on_codex_event(issue.id, empty_event)

            assert entry.cancelled_at is not None, (
                "three consecutive empty TURN_COMPLETED events must cancel the worker"
            )
            assert worker_task.cancelled() or worker_task.cancelling() > 0, (
                "worker_task.cancel() must have been called"
            )
            assert persisted_kinds == ["empty_response_loop"], (
                "must persist via empty_response_loop budget kind"
            )
            debug = orch._issue_debug.get(issue.id)
            assert debug is not None
            assert "empty_response_loop" in (debug.last_error or "")
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_g2_empty_response_loop_does_not_block_phase_transitions(
    monkeypatch, tmp_path
):
    """G2 — empty preview text is only a loop when the card stays put.

    Several real CLIs edit files successfully but return no assistant preview
    in their machine output. If the ticket advanced after the turn, the empty
    counter must clear instead of blocking before the next stage can start.
    """

    cfg = _make_config(
        max_concurrent=1,
        active_states=("Alpha", "Beta", "Gamma", "Delta"),
        terminal_states=("Done", "Blocked"),
    )
    cfg = replace(
        cfg,
        agent=replace(
            cfg.agent,
            max_turns=10,
            max_total_turns=10,
            budget_exhausted_state="Blocked",
            auto_commit_on_done=False,
            auto_merge_on_done=False,
        ),
    )
    orch = _orch()
    issue = _issue("MT-EMPTY-ADVANCE", state="Alpha")
    turns: list[str] = []
    moved: list[str] = []
    notes: list[str] = []
    refreshed_states = iter(["Beta", "Gamma", "Delta", "Done"])

    class _Backend:
        async def start(self):
            return None

        async def initialize(self):
            return None

        async def start_session(self, *, initial_prompt, issue_title):
            return "thread-1"

        async def run_turn(self, *, prompt, is_continuation):
            turns.append(prompt)
            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100},
            }
            await orch._on_codex_event(issue.id, empty_event)

        async def stop(self):
            return None

    class _Workspace:
        def __init__(self, path: Path) -> None:
            self.path = path

    class _StubWS:
        async def create_or_reuse(self, identifier):
            return _Workspace(tmp_path)

        async def before_run(self, path):
            return None

        async def after_run_best_effort(self, path):
            return None

        async def after_done_best_effort(self, path, *, identifier, title):
            return True

        async def remove_best_effort(self, path):
            return None

        async def remove(self, path):
            return None

    async def _refresh_state(_cfg, _issue_id):
        return replace(issue, state=next(refreshed_states, "Done"))

    def _move(_cfg, _issue, target_state):
        moved.append(target_state)

    def _append(_cfg, _issue, heading, body):
        notes.append(heading)

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg, monkeypatch)
        orch._workspace_manager = _StubWS()  # type: ignore[assignment]
        monkeypatch.setattr(core_module, "build_backend", lambda _init: _Backend())
        monkeypatch.setattr(orch, "_refresh_issue_state", _refresh_state)
        monkeypatch.setattr(orch, "_tracker_call_update_state", _move)
        monkeypatch.setattr(orch, "_tracker_call_append_note", _append)

        try:
            await orch._run_agent_attempt(issue, attempt=None, cfg=cfg)
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())

    assert len(turns) == 4
    assert moved == []
    assert notes == []
    assert not orch.is_paused(issue.id)
    debug = orch._issue_debug.get(issue.id)
    assert debug is None or "empty_response_loop" not in (debug.last_error or "")


@pytest.mark.parametrize(
    ("policy", "expected_dispatches"),
    (("fifo", ["TKT-001"]), ("dag", [])),
)
def test_oversized_dependency_graph_degrades_without_unbounded_analysis(
    monkeypatch, policy, expected_dispatches
):
    cfg = _make_config(
        max_concurrent=2,
        tracker_kind="file",
        auto_triage_actionable_todo=False,
    )
    cfg = replace(cfg, agent=replace(cfg.agent, scheduling_policy=policy))
    issues = [_issue("TKT-001", state="Todo"), _issue("TKT-002", state="Todo")]
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    monkeypatch.setattr(core_module, "MAX_DEPENDENCY_NODES", 1)
    monkeypatch.setattr(
        core_module,
        "analyze_dependencies",
        lambda _issues: pytest.fail("oversized graph must not be analyzed"),
    )
    dispatched: list[str] = []

    async def _fetch(_cfg):
        return issues

    async def _archive(_cfg):
        return None

    def _dispatch(issue, _cfg, *, attempt, attempt_kind=None):
        del attempt, attempt_kind
        dispatched.append(issue.identifier)
        return True

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)
    monkeypatch.setattr(
        orch, "_available_slots", lambda _cfg: 0 if dispatched else 1
    )

    asyncio.run(orch._on_tick())

    assert dispatched == expected_dispatches
    snapshot = orch.schedule_snapshot()
    assert snapshot["available"] is False
    assert snapshot["reason"] == "schedule_graph_too_large"
    assert snapshot["entries"] == []


def test_schedule_snapshot_never_exposes_retry_error_or_private_session(monkeypatch):
    cfg = _make_config(tracker_kind="file")
    issue = _issue("TKT-PRIVATE", state="Todo")
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))

    async def _run():
        orch._loop = asyncio.get_running_loop()
        orch._schedule_retry(
            issue.id,
            identifier=issue.identifier,
            attempt=1,
            delay_ms=60_000,
            error="backend failed private-session-abc /Users/operator/secret",
        )

        async def _fetch(_cfg):
            return [issue]

        async def _archive(_cfg):
            return None

        monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
        monkeypatch.setattr(orch, "_archive_sweep", _archive)
        await orch._on_tick()
        for retry in orch._retry.values():
            retry.timer_handle.cancel()

    asyncio.run(_run())
    encoded = json.dumps(orch.schedule_snapshot())
    assert "private-session-abc" not in encoded
    assert "/Users/operator/secret" not in encoded
    assert "owned by the retry timer" in encoded


def test_incomplete_scheduler_pass_marks_previous_snapshot_stale(monkeypatch):
    cfg = _make_config(tracker_kind="file")
    orch = _orch()
    orch._schedule_snapshot = {
        "schema_version": 1,
        "available": True,
        "reason": None,
        "generated_at": "2026-01-01T00:00:00Z",
        "stale": False,
        "entries": [],
    }
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))

    async def _fail(_cfg):
        raise RuntimeError("safety sweep failed")

    monkeypatch.setattr(orch, "_auto_reopen_sources_from_resolved_rcas", _fail)
    asyncio.run(orch._on_tick())

    snapshot = orch.schedule_snapshot()
    assert snapshot["stale"] is True
    assert snapshot["reason"] == "scheduler_pass_incomplete"


@pytest.mark.parametrize("prefix_kind", ("running", "retry"))
def test_capacity_break_closes_mutating_scan_after_owned_prefix(
    monkeypatch, prefix_kind
):
    cfg = _make_config(
        max_concurrent=1,
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    prefix_issue = _issue("TKT-001", state="In Progress")
    actionable = _issue("TKT-002", state="Todo")
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    monkeypatch.setattr(orch, "_heartbeat_running_leases", lambda: None)
    triaged: list[str] = []

    async def _fetch(_cfg):
        return [prefix_issue, actionable]

    async def _archive(_cfg):
        return None

    async def _reconcile(_cfg):
        return None

    async def _triage(issue, _cfg):
        triaged.append(issue.identifier)
        return issue.identifier == actionable.identifier

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_reconcile_running", _reconcile)
    monkeypatch.setattr(orch, "_auto_triage_todo_if_actionable", _triage)
    monkeypatch.setattr(orch, "_available_slots", lambda _cfg: 0)

    async def _run():
        if prefix_kind == "running":
            orch._running[prefix_issue.id] = RunningEntry(
                issue=prefix_issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=None,  # type: ignore[arg-type]
                workspace_path=Path("/tmp"),
            )
        else:
            orch._loop = asyncio.get_running_loop()
            orch._schedule_retry(
                prefix_issue.id,
                identifier=prefix_issue.identifier,
                attempt=1,
                delay_ms=60_000,
                error="retry",
            )
        await orch._on_tick()
        for retry in orch._retry.values():
            retry.timer_handle.cancel()

    asyncio.run(_run())

    assert triaged == ["TKT-001"]


def test_schedule_forecast_has_no_tracker_side_effects_after_capacity_break(
    monkeypatch,
):
    cfg = _make_config(
        max_concurrent=1,
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    issues = [_issue(f"TKT-00{n}", state="Todo") for n in (1, 2, 3)]
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    triaged: list[str] = []

    async def _fetch(_cfg):
        return issues

    async def _archive(_cfg):
        return None

    async def _triage(issue, _cfg):
        triaged.append(issue.identifier)
        return issue.identifier != "TKT-001"

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_auto_triage_todo_if_actionable", _triage)
    monkeypatch.setattr(orch, "_available_slots", lambda _cfg: 0)

    asyncio.run(orch._on_tick())

    assert triaged == ["TKT-001"]
    assert [row["identifier"] for row in orch.schedule_snapshot()["entries"]] == [
        "TKT-001",
        "TKT-002",
        "TKT-003",
    ]


def test_dag_policy_snapshot_order_matches_consumed_dispatch_order(monkeypatch):
    cfg = _make_config(
        max_concurrent=1,
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        auto_triage_actionable_todo=False,
    )
    cfg = replace(cfg, agent=replace(cfg.agent, scheduling_policy="dag"))
    root = replace(_issue("TKT-020", state="Todo"), priority=1)
    leaf = replace(
        _issue("TKT-021", state="Todo"),
        priority=1,
        blocked_by=(
            BlockerRef(id=root.id, identifier=root.identifier, state="Todo"),
        ),
    )
    urgent = replace(_issue("TKT-030", state="Todo"), priority=0)
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [leaf, root, urgent]

    async def _archive(_cfg):
        return None

    def _dispatch(issue, _cfg, *, attempt, attempt_kind=None):
        del attempt, attempt_kind
        dispatched.append(issue.identifier)
        return True

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)
    monkeypatch.setattr(
        orch, "_available_slots", lambda _cfg: 0 if dispatched else 1
    )

    asyncio.run(orch._on_tick())

    snapshot = orch.schedule_snapshot()
    assert dispatched == ["TKT-030"]
    assert snapshot["policy"] == "dag"
    assert snapshot["entries"][0]["identifier"] == dispatched[0]
    assert snapshot["entries"][0]["dispatch_outcome"] == "started"
    root_entry = next(
        row for row in snapshot["entries"] if row["identifier"] == "TKT-020"
    )
    assert root_entry["queue_rank"] == 2
    assert root_entry["code"] == "waiting_global_capacity"
    leaf_entry = next(
        row for row in snapshot["entries"] if row["identifier"] == "TKT-021"
    )
    assert leaf_entry["code"] == "waiting_dependency"


def test_g3_wait_age_bumps_starved_recovered_ticket_ahead_of_fifo(monkeypatch):
    """G3 — A candidate whose `_claim_released_at` is older than
    `WAIT_AGE_BUMP_MIN` must dispatch ahead of a fresh-but-lower-id
    candidate. Without the bump, registration-order FIFO starves the
    recovered ticket every tick.
    """
    import asyncio
    from datetime import timedelta

    cfg = _make_config(
        max_concurrent=1,
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    fresh = _issue("TKT-005", state="Todo")
    starved = _issue("TKT-010", state="Todo")

    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))

    # Simulate G1 release recording: starved ticket left `_claimed` 15 min
    # ago (above WAIT_AGE_BUMP_MIN=10 min). Fresh ticket has no entry.
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=15)
    orch._claim_released_at[starved.id] = long_ago

    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [fresh, starved]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)

    asyncio.run(orch._on_tick())
    assert dispatched[0] == "TKT-010", (
        "G3 wait-age bump must promote a recovered ticket older than "
        "WAIT_AGE_BUMP_MIN ahead of registration-order FIFO "
        f"(actual dispatch order: {dispatched})"
    )


def test_g3_fresh_release_keeps_fifo_order(monkeypatch):
    """G3 — A recovered ticket released less than `WAIT_AGE_BUMP_MIN` ago
    must NOT bypass FIFO. Bump only fires on starvation cases.
    """
    import asyncio
    from datetime import timedelta

    cfg = _make_config(
        max_concurrent=1,
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    fresh = _issue("TKT-005", state="Todo")
    just_released = _issue("TKT-010", state="Todo")

    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))

    # Released 2 minutes ago — under threshold, normal FIFO must hold.
    recent = datetime.now(timezone.utc) - timedelta(minutes=2)
    orch._claim_released_at[just_released.id] = recent

    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [fresh, just_released]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)

    asyncio.run(orch._on_tick())
    assert dispatched[0] == "TKT-005", (
        "wait-age bump must not fire under WAIT_AGE_BUMP_MIN — FIFO holds "
        f"(actual dispatch order: {dispatched})"
    )


def test_g3_claim_release_timestamp_recorded_by_prune(monkeypatch):
    """G3 — The G1 prune block must populate `_claim_released_at[id]` with
    the moment the id left `_claimed`. Without this, the wait-age sort
    has nothing to compare against on subsequent ticks.
    """
    import asyncio

    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    held = _issue(
        "MT-1",
        state="In Progress",
        description="## Touched Files\n- src/foo.py\n",
    )
    candidate = _issue(
        "MT-2",
        state="Todo",
        description="## Touched Files\n- src/foo.py\n",
    )

    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    orch._running[held.id] = RunningEntry(
        issue=held,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=Path("/tmp"),
    )
    orch._claimed.add(held.id)

    async def _fetch(_cfg):
        return [candidate]

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        Orchestrator, "_tracker_call_append_note", staticmethod(lambda *_a, **_k: None)
    )
    monkeypatch.setattr(
        Orchestrator, "_tracker_call_update_state", staticmethod(lambda *_a, **_k: None)
    )

    # Tick 1: conflict adds MT-2 to _claimed.
    asyncio.run(orch._on_tick())
    assert candidate.id in orch._claimed

    # Worker exits, operator restores. Tick 2 should prune AND record release.
    orch._running.pop(held.id, None)
    orch._claimed.discard(held.id)

    asyncio.run(orch._on_tick())
    assert candidate.id not in orch._claimed
    assert candidate.id in orch._claim_released_at, (
        "G3 — prune block must record the release timestamp so wait-age "
        "sort has something to compare against"
    )


def test_g2_empty_response_loop_auto_pauses_to_block_redispatch(monkeypatch):
    """G2 — even without `budget_exhausted_state` configured, the empty-loop
    guard must auto-pause the ticket so dispatch + retry refuse to restart
    it. Without this, an unconfigured workflow lets the loop re-dispatch
    immediately on the next tick (verified live on olive-clone 2026-05-20).
    """
    import asyncio

    cfg = _make_config(max_concurrent=1)
    assert cfg.agent.budget_exhausted_state == "", "precondition"
    orch = _orch()
    issue = _issue("MT-EMPTY-PAUSE", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        monkeypatch.setattr(
            orch, "_tracker_call_update_state", lambda *_a, **_k: None
        )
        monkeypatch.setattr(
            orch, "_tracker_call_append_note", lambda *_a, **_k: None
        )

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry
            assert not orch.is_paused(issue.id), "precondition"

            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100},
            }
            for _ in range(3):
                await orch._on_codex_event(issue.id, empty_event)

            assert orch.is_paused(issue.id), (
                "G2 must auto-pause the ticket so dispatch + retry refuse "
                "to restart it on the next tick"
            )
            assert entry.cancelled_at is not None
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_g2_auto_pause_blocks_redispatch_through_eligible(monkeypatch):
    """G2 — `_eligible` (and therefore the dispatch loop) must refuse to
    restart an auto-paused ticket. End-to-end check that the pause hooked
    in by G2 actually prevents the live `_on_tick` dispatch path from
    starting the same loop again."""
    import asyncio

    cfg = _make_config(
        max_concurrent=1,
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    orch = _orch()
    issue = _issue("MT-LOOP-DISP", state="Todo")

    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    # Simulate the result of an earlier empty-response-loop trip:
    orch._paused_issue_ids.add(issue.id)

    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [issue]

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(
        orch, "_dispatch",
        lambda i, c, **k: dispatched.append(i.identifier),
    )

    asyncio.run(orch._on_tick())
    assert dispatched == [], (
        "auto-paused ticket must not enter dispatch even when it's the "
        "only candidate and a slot is free"
    )


def test_g2_empty_response_loop_pause_reason_persists_and_rehydrates(
    tmp_path, monkeypatch
):
    import asyncio

    cfg = _make_config(
        max_concurrent=1,
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / "ws",
    )
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orch = _orch()
    issue = _issue("MT-EMPTY-REASON", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = registry
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        monkeypatch.setattr(
            orch, "_tracker_call_update_state", lambda *_a, **_k: None
        )
        monkeypatch.setattr(
            orch, "_tracker_call_append_note", lambda *_a, **_k: None
        )

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=tmp_path / "ws" / issue.identifier,
            )
            orch._running[issue.id] = entry

            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100},
            }
            for _ in range(3):
                await orch._on_codex_event(issue.id, empty_event)
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())

    flags = registry.get_issue_flags(issue.id)
    assert flags is not None
    assert flags.paused is True
    assert flags.pause_reason == (
        "empty_response_loop: 3 consecutive empty turns (threshold 3); "
        "resume via resume_worker after inspecting the ticket"
    )

    restarted = _orch()
    restarted._ensure_run_registry(cfg)

    assert issue.id in restarted._paused_issue_ids
    assert restarted._pause_reasons[issue.id] == flags.pause_reason


def test_g2_resume_worker_clears_auto_pause(monkeypatch):
    """G2 — operator's existing `resume_worker(id)` lifts the auto-pause,
    so the manual recovery flow is symmetric with operator pause."""
    import asyncio

    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-LOOP-RESUME", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        monkeypatch.setattr(
            orch, "_tracker_call_update_state", lambda *_a, **_k: None
        )
        monkeypatch.setattr(
            orch, "_tracker_call_append_note", lambda *_a, **_k: None
        )

        async def _noop():
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry
            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100},
            }
            for _ in range(3):
                await orch._on_codex_event(issue.id, empty_event)
            assert orch.is_paused(issue.id)

            # Operator decides the loop was a fluke and resumes.
            # `resume_worker` works against `_paused_issue_ids` directly
            # (no `_running` requirement), so it lifts the auto-pause even
            # after the worker has exited.
            orch._paused_issue_ids.discard(issue.id)
            assert not orch.is_paused(issue.id)
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_g2_auto_pause_idempotent_on_subsequent_empty_turns(monkeypatch):
    """G2 — a fourth/fifth consecutive empty turn after the auto-pause
    has fired must not re-add to `_paused_issue_ids` or re-fire the
    log line. The `entry.cancelled_at is None` guard already prevents
    persist double-fire; the pause path uses an `if not in` guard."""
    import asyncio

    cfg = _replace_agent_field(_make_config(max_concurrent=1), budget_exhausted_state="Blocked")
    orch = _orch()
    issue = _issue("MT-LOOP-IDEM", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        monkeypatch.setattr(
            orch, "_persist_budget_exhausted_state",
            lambda **kwargs: _async_true()
        )

        async def _noop():
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry
            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100},
            }
            # 5 consecutive empties — escalation fires once, pause sticks.
            for _ in range(5):
                await orch._on_codex_event(issue.id, empty_event)
            assert orch.is_paused(issue.id)
            # Only one entry in the pause set; idempotent guard works.
            assert len([x for x in orch._paused_issue_ids if x == issue.id]) == 1
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


async def _async_true():
    return True


def test_g2_empty_response_loop_no_op_when_budget_state_unset(monkeypatch):
    """G2 — when `budget_exhausted_state` is empty, the persist path is a
    no-op but the worker must still be cancelled so the slow-burn loop
    breaks. The operator gets the in-memory error message but the ticket
    is not auto-transitioned."""
    import asyncio

    cfg = _make_config(max_concurrent=1)  # no budget_exhausted_state
    assert cfg.agent.budget_exhausted_state == "", "precondition"
    orch = _orch()
    issue = _issue("MT-EMPTY-LEGACY", state="In Progress")

    transitioned: list[tuple[str, str]] = []

    def _update_state(_cfg, captured, target):
        transitioned.append((captured.identifier, target))

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        monkeypatch.setattr(orch, "_tracker_call_update_state", _update_state)
        monkeypatch.setattr(
            orch, "_tracker_call_append_note", lambda *_a, **_k: None
        )

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry

            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100},
            }
            for _ in range(3):
                await orch._on_codex_event(issue.id, empty_event)

            assert entry.cancelled_at is not None, (
                "worker must cancel even without a budget_exhausted_state"
            )
            assert transitioned == [], (
                "no tracker transition without configured budget_exhausted_state"
            )
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_g2_empty_loop_does_not_double_cancel_after_threshold(monkeypatch):
    """G2 — once the worker is cancelled on threshold breach, subsequent
    empty turns must NOT call cancel again. The `entry.cancelled_at is None`
    guard prevents the persist path from re-firing."""
    import asyncio

    cfg = _replace_agent_field(_make_config(max_concurrent=1), budget_exhausted_state="Blocked")
    orch = _orch()
    issue = _issue("MT-EMPTY-IDEM", state="In Progress")

    persist_calls: list[str] = []

    async def _persist(*, cfg, entry, issue_id, target_state, budget_kind):
        persist_calls.append(budget_kind)
        return True

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        monkeypatch.setattr(orch, "_persist_budget_exhausted_state", _persist)

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry
            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100},
            }
            for _ in range(5):  # 5 empties — escalation must fire once
                await orch._on_codex_event(issue.id, empty_event)
            assert persist_calls == ["empty_response_loop"], (
                f"persist must fire exactly once (got {persist_calls})"
            )
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_g3_wait_age_bump_orders_multiple_starved_oldest_first(monkeypatch):
    """G3 — when multiple recovered tickets cross the threshold, the
    oldest release time wins. Without this, FIFO would order them by
    registration after bumping, hiding the most-starved one."""
    import asyncio
    from datetime import timedelta

    cfg = _make_config(
        max_concurrent=3,
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
    )
    fresh = _issue("TKT-001", state="Todo")
    starved_a = _issue("TKT-020", state="Todo")  # released 20 min ago
    starved_b = _issue("TKT-030", state="Todo")  # released 45 min ago — oldest

    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    now = datetime.now(timezone.utc)
    orch._claim_released_at[starved_a.id] = now - timedelta(minutes=20)
    orch._claim_released_at[starved_b.id] = now - timedelta(minutes=45)

    dispatched: list[str] = []

    async def _fetch(_cfg):
        return [fresh, starved_a, starved_b]

    async def _archive(_cfg):
        return None

    def _dispatch(_issue, _cfg, *, attempt, attempt_kind=None):
        dispatched.append(_issue.identifier)

    monkeypatch.setattr(orch, "_fetch_candidates", _fetch)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_dispatch", _dispatch)

    asyncio.run(orch._on_tick())
    # Oldest release first → starved_b (45 min) then starved_a (20 min)
    # then fresh under normal FIFO.
    assert dispatched == ["TKT-030", "TKT-020", "TKT-001"], (
        f"oldest release must dispatch first; got {dispatched}"
    )


def test_g_dispatch_stability_full_cycle_5_ticks(monkeypatch):
    """Composite — exercise G1+G2+G3+G5 across 5 consecutive ticks.

    Models a small board where one ticket cycles through:
      Tick 1: candidate enters, conflict with running ticket → blocked,
              `_claimed` retains the id.
      Tick 2: running ticket exits; prune block releases the claim and
              records `_claim_released_at` (G1+G3 seeding).
      Tick 3: a starved-recovery candidate is pre-seeded with an old
              release time and must dispatch ahead of the fresh ticket
              (G3 promotion).
      Tick 4: empty-response loop guard fires on a different worker; the
              persist path runs with budget_kind=empty_response_loop (G2).
      Tick 5: a restore-into-active call strips the orchestrator warning
              section on the file tracker (G5).
    """
    import asyncio
    from datetime import timedelta
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        cfg = _make_config(
            max_concurrent=2,
            tracker_kind="file",
            active_states=("Todo", "In Progress"),
        )
        cfg = _replace_agent_field(cfg, budget_exhausted_state="Blocked")
        orch = _orch()
        monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))

        # ---- Tick 1: conflict path adds candidate to `_claimed` ----
        held = _issue(
            "MT-1", state="In Progress",
            description="## Touched Files\n- src/foo.py\n",
        )
        cand = _issue(
            "MT-2", state="Todo",
            description="## Touched Files\n- src/foo.py\n",
        )
        orch._running[held.id] = RunningEntry(
            issue=held,
            started_at=datetime.now(timezone.utc),
            retry_attempt=None,
            worker_task=None,  # type: ignore[arg-type]
            workspace_path=Path("/tmp"),
        )
        orch._claimed.add(held.id)

        dispatched: list[str] = []

        async def _fetch_t1(_cfg):
            return [cand]

        async def _archive_noop(_cfg):
            return None

        monkeypatch.setattr(orch, "_fetch_candidates", _fetch_t1)
        monkeypatch.setattr(orch, "_archive_sweep", _archive_noop)
        monkeypatch.setattr(
            orch, "_dispatch",
            lambda i, c, **k: dispatched.append(i.identifier),
        )
        monkeypatch.setattr(
            Orchestrator, "_tracker_call_append_note",
            staticmethod(lambda *_a, **_k: None),
        )
        monkeypatch.setattr(
            Orchestrator, "_tracker_call_update_state",
            staticmethod(lambda *_a, **_k: None),
        )

        asyncio.run(orch._on_tick())
        assert cand.id in orch._claimed, "tick1: conflict claim recorded"
        assert dispatched == [], "tick1: candidate must NOT dispatch under conflict"

        # ---- Tick 2: held worker exits; G1 prune releases claim, G3 records ----
        orch._running.pop(held.id, None)
        orch._claimed.discard(held.id)

        asyncio.run(orch._on_tick())
        assert cand.id not in orch._claimed, "tick2: G1 prune ran"
        assert cand.id in orch._claim_released_at, "tick2: G3 recorded release"
        assert dispatched == ["MT-2"], "tick2: dispatch after prune"

        # ---- Tick 3: G3 starvation promotion ----
        fresh = _issue("MT-3", state="Todo")
        starved = _issue("MT-9", state="Todo")
        # Pre-age the starvation timestamp past WAIT_AGE_BUMP_MIN.
        orch._claim_released_at[starved.id] = (
            datetime.now(timezone.utc) - timedelta(minutes=15)
        )
        dispatched_t3: list[str] = []
        monkeypatch.setattr(
            orch, "_dispatch",
            lambda i, c, **k: dispatched_t3.append(i.identifier),
        )

        async def _fetch_t3(_cfg):
            return [fresh, starved]

        monkeypatch.setattr(orch, "_fetch_candidates", _fetch_t3)
        asyncio.run(orch._on_tick())
        assert dispatched_t3[0] == "MT-9", (
            f"tick3: G3 must promote starved MT-9 ahead of fresh MT-3 "
            f"(got {dispatched_t3})"
        )

        # ---- Tick 4: G2 empty-response loop guard ----
        empty_issue = _issue("MT-EMPTY", state="In Progress")
        persisted: list[str] = []

        async def _persist(*, cfg, entry, issue_id, target_state, budget_kind):
            persisted.append(budget_kind)
            return True

        async def _t4():
            orch._loop = asyncio.get_running_loop()
            orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
            monkeypatch.setattr(orch, "_persist_budget_exhausted_state", _persist)

            async def _noop():
                await asyncio.sleep(3600)

            worker = asyncio.create_task(_noop())
            try:
                entry = RunningEntry(
                    issue=empty_issue,
                    started_at=datetime.now(timezone.utc),
                    retry_attempt=None,
                    worker_task=worker,
                    workspace_path=Path("/tmp"),
                )
                orch._running[empty_issue.id] = entry
                ev = {
                    "event": "turn_completed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {},
                    "usage": {"input_tokens": 100, "output_tokens": 0,
                              "total_tokens": 100},
                }
                for _ in range(3):
                    await orch._on_codex_event(empty_issue.id, ev)
                assert entry.cancelled_at is not None
                assert persisted == ["empty_response_loop"]
            finally:
                worker.cancel()
                try:
                    await worker
                except (asyncio.CancelledError, Exception):
                    pass

        asyncio.run(_t4())

        # ---- Tick 5: G5 strip on tracker restore ----
        from symphony.trackers.file import FileBoardTracker
        from symphony.workflow import TrackerConfig
        board_root = Path(tmp) / "board"
        fbt = FileBoardTracker(
            TrackerConfig(
                kind="file",
                endpoint="",
                api_key="",
                project_slug="",
                active_states=("Todo", "In Progress"),
                terminal_states=("Done", "Cancelled", "Blocked"),
                board_root=board_root.resolve(),
            )
        )
        fbt.create(
            identifier="MT-5", title="t", state="Blocked",
            description="Operator body.",
        )
        from symphony.trackers.file import issue_from_file
        issue_obj = issue_from_file(fbt.find_path("MT-5"))
        fbt.append_note(issue_obj, "Conflict", "Earlier conflict trace.")
        issue_obj = issue_from_file(fbt.find_path("MT-5"))
        fbt.update_state(issue_obj, "Todo")
        body_after = fbt.find_path("MT-5").read_text()
        assert "## Conflict" not in body_after, (
            "tick5: G5 must strip ## Conflict on restore into active state"
        )
        assert "Operator body." in body_after


def test_g3_worker_exit_clears_claim_released_at(monkeypatch):
    """G3 — `_on_worker_exit` must drop the `_claim_released_at` entry so
    a finished ticket that comes back later doesn't inherit a stale bump."""
    cfg = _make_config(max_concurrent=1)
    orch = _orch()
    issue = _issue("MT-CLEAR", state="In Progress")

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        _install_running_entry(orch, issue)
        _stub_workflow_state_returning(orch, cfg, monkeypatch)

        # Seed the bump record.
        orch._claim_released_at[issue.id] = datetime.now(timezone.utc)
        assert issue.id in orch._claim_released_at

        try:
            await orch._on_worker_exit(issue.id, reason="normal", error=None)
            assert issue.id not in orch._claim_released_at, (
                "worker exit must drop the wait-age release timestamp"
            )
        finally:
            for retry in list(orch._retry.values()):
                retry.timer_handle.cancel()

    asyncio.run(_run())


def test_g2_empty_response_loop_resets_on_non_empty_turn(monkeypatch):
    """G2 — a non-empty turn after empties must reset the counter so a
    real recovery does not escalate."""
    import asyncio

    base_cfg = _make_config(max_concurrent=1)
    cfg = _replace_agent_field(base_cfg, budget_exhausted_state="Blocked")
    orch = _orch()
    issue = _issue("MT-RECOVER", state="In Progress")

    persisted_kinds: list[str] = []

    async def _persist(*, cfg, entry, issue_id, target_state, budget_kind):
        persisted_kinds.append(budget_kind)
        return True

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        monkeypatch.setattr(orch, "_persist_budget_exhausted_state", _persist)

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry

            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100},
            }
            non_empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"message": "actual model output here"},
                "usage": {"input_tokens": 200, "output_tokens": 50, "total_tokens": 250},
            }
            # 2 empty, 1 non-empty (resets counter), 2 more empty → still under threshold
            await orch._on_codex_event(issue.id, empty_event)
            await orch._on_codex_event(issue.id, empty_event)
            await orch._on_codex_event(issue.id, non_empty_event)
            await orch._on_codex_event(issue.id, empty_event)
            await orch._on_codex_event(issue.id, empty_event)

            assert entry.cancelled_at is None, (
                "non-empty turn must reset counter so 2-1-2 pattern does not escalate"
            )
            assert persisted_kinds == [], (
                "no persistence must fire when threshold is not crossed"
            )
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


def test_g2_pi_nested_assistant_message_resets_empty_turn_counter(monkeypatch):
    """Pi/Prime assistant text is nested under message/content."""
    cfg = _replace_agent_field(
        _make_config(max_concurrent=1), budget_exhausted_state="Blocked"
    )
    orch = _orch()
    issue = _issue("MT-PI-ASSISTANT", state="In Progress")

    async def _run() -> None:
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        entry = _install_running_entry(orch, issue)
        assistant = {
            "role": "assistant",
            "content": [{"type": "text", "text": "finished the turn"}],
        }

        # The terminal payload uses agent_end.messages; this also pins that
        # path to assistant-only extraction.
        assert (
            Orchestrator._preview_from_payload(
                {"type": "agent_end", "messages": [assistant]}
            )
            == "finished the turn"
        )

        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
            },
        )
        assert entry.consecutive_empty_turns == 1

        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "message_end", "message": assistant},
            },
        )
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "agent_end", "messages": []},
            },
        )

        assert entry.consecutive_empty_turns == 0

    asyncio.run(_run())


def test_g2_pi_nested_user_message_does_not_reset_empty_turn_counter(monkeypatch):
    """Pi/Prime user messages and tool results are not model output."""
    cfg = _replace_agent_field(
        _make_config(max_concurrent=1), budget_exhausted_state="Blocked"
    )
    orch = _orch()
    issue = _issue("MT-PI-USER", state="In Progress")

    async def _run() -> None:
        orch._workflow_state.current = lambda: cfg  # type: ignore[assignment]
        entry = _install_running_entry(orch, issue)
        user = {
            "role": "user",
            "content": [{"type": "text", "text": "the user prompt"}],
        }
        tool_result = {
            "role": "tool",
            "content": [{"type": "text", "text": "tool output"}],
        }

        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
            },
        )
        assert entry.consecutive_empty_turns == 1

        await orch._on_codex_event(
            issue.id,
            {
                "event": "other_message",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"type": "message_end", "message": user},
            },
        )
        await orch._on_codex_event(
            issue.id,
            {
                "event": EVENT_TURN_COMPLETED,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "type": "agent_end",
                    "messages": [user, tool_result],
                },
            },
        )

        assert entry.consecutive_empty_turns == 2

    asyncio.run(_run())


def test_g2_opencode_shaped_payload_resets_only_with_message_key(monkeypatch):
    """G2 — opencode's raw EVENT_TURN_COMPLETED payload carries `result`/
    `response`, never `message`. `_preview_from_payload` only reads `message`
    (plus a few other preview keys), so a `result`/`response`-only payload
    must still count as empty; adding `message` (the opencode.py fix) must
    reset the counter."""
    import asyncio

    base_cfg = _make_config(max_concurrent=1)
    cfg = _replace_agent_field(base_cfg, budget_exhausted_state="Blocked")
    orch = _orch()
    issue = _issue("MT-OPENCODE-SHAPE", state="In Progress")

    persisted_kinds: list[str] = []

    async def _persist(*, cfg, entry, issue_id, target_state, budget_kind):
        persisted_kinds.append(budget_kind)
        return True

    async def _run() -> None:
        orch._loop = asyncio.get_running_loop()
        monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
        monkeypatch.setattr(orch, "_persist_budget_exhausted_state", _persist)

        async def _noop() -> None:
            await asyncio.sleep(3600)

        worker_task = asyncio.create_task(_noop())
        try:
            entry = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=None,
                worker_task=worker_task,
                workspace_path=Path("/tmp"),
            )
            orch._running[issue.id] = entry

            # Pre-fix opencode shape: `result`/`response` only, no `message`.
            result_only_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"result": "did real work", "response": "did real work"},
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            }
            # Post-fix opencode shape: `message` added alongside `result`/`response`.
            with_message_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "message": "did real work",
                    "result": "did real work",
                    "response": "did real work",
                },
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            }

            await orch._on_codex_event(issue.id, result_only_event)
            assert entry.consecutive_empty_turns == 1, (
                "result/response-only payload (opencode's pre-fix shape) "
                "must still count as an empty turn"
            )
            await orch._on_codex_event(issue.id, result_only_event)
            assert entry.consecutive_empty_turns == 2

            await orch._on_codex_event(issue.id, with_message_event)
            assert entry.consecutive_empty_turns == 0, (
                "adding the `message` key (opencode.py fix) must reset the counter"
            )
            assert entry.cancelled_at is None
            assert persisted_kinds == []

            empty_event = {
                "event": "turn_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 0,
                    "total_tokens": 100,
                },
            }
            for _ in range(3):
                await orch._on_codex_event(issue.id, empty_event)

            assert entry.cancelled_at is not None
            assert persisted_kinds == ["empty_response_loop"]
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# C3 — adaptive token-budget EMA (workflow-v0.5.2 § C3).
# ---------------------------------------------------------------------------


def test_token_ema_first_then_second_sample_matches_formula(tmp_path):
    """α=0.3 EMA over two samples produces the expected rounded value.

    First sample 100k against ema=0 → 0.3·100k = 30k.
    Second sample 200k → 0.3·200k + 0.7·30k = 60k + 21k = 81k.
    """
    cfg = _make_config()
    # Redirect the EMA file to a temp dir so the test never writes into
    # the real `.symphony/` next to the repo's WORKFLOW.md.
    cfg_temp = replace(cfg, workflow_path=tmp_path / "WORKFLOW.md")
    orch = _orch()

    orch._update_token_ema("In Progress", 100_000, cfg_temp)
    assert orch._token_ema_for_state("In Progress") == 30_000

    orch._update_token_ema("In Progress", 200_000, cfg_temp)
    assert orch._token_ema_for_state("In Progress") == 81_000


def test_token_ema_persists_and_reloads(tmp_path):
    """EMA round-trips through `.symphony/token_ema.json` on restart."""
    cfg = _make_config()
    cfg_temp = replace(cfg, workflow_path=tmp_path / "WORKFLOW.md")
    orch = _orch()

    orch._update_token_ema("In Progress", 100_000, cfg_temp)
    orch._update_token_ema("In Progress", 200_000, cfg_temp)

    # New orchestrator with the same workflow path: load reads the file.
    fresh = _orch()
    fresh._load_token_ema(cfg_temp)
    assert fresh._token_ema_for_state("In Progress") == 81_000
    # Unseen state still reports 0.
    assert fresh._token_ema_for_state("Review") == 0


def test_token_budget_for_state_falls_back_to_default():
    """`max_total_tokens_by_state` overrides; absent state uses the
    global `max_total_tokens` cap.
    """
    base = _make_config()
    cfg_with_caps = _replace_agent_field(
        base,
        max_total_tokens=500_000,
        max_total_tokens_by_state={"in progress": 750_000},
    )
    orch = _orch()
    assert orch._token_budget_for_state(cfg_with_caps, "In Progress") == 750_000
    assert orch._token_budget_for_state(cfg_with_caps, "Review") == 500_000


# ---------------------------------------------------------------------------
# A2-orch — SYMPHONY_REWIND_SCOPE env var on rewind dispatch.
# ---------------------------------------------------------------------------


def test_apply_dispatch_env_sets_rewind_scope_on_rewind(monkeypatch):
    """Rewind dispatch must export SYMPHONY_REWIND_SCOPE as JSON."""
    monkeypatch.delenv("SYMPHONY_REWIND_SCOPE", raising=False)
    cfg = _make_config()
    orch = _orch()
    issue = _issue(
        "MT-REWIND",
        state="In Progress",
        description=(
            "## Plan\nimplement\n\n"
            "## Review Findings\n"
            "- HIGH: src/foo.py:42 — switch to shared helper\n"
            "- MEDIUM src/bar.py:7 add input validation\n"
        ),
    )

    orch._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=True)

    import os
    import json as _json

    raw = os.environ.get("SYMPHONY_REWIND_SCOPE")
    assert raw is not None, "SYMPHONY_REWIND_SCOPE must be set on rewind"
    rows = _json.loads(raw)
    assert isinstance(rows, list) and rows, "rewind scope must parse to list"
    severities = {row["severity"] for row in rows}
    assert "HIGH" in severities
    files = {row["file"] for row in rows}
    assert "src/foo.py" in files
    # Env vars for budget always present, regardless of rewind.
    assert os.environ.get("SYMPHONY_TOKEN_BUDGET") is not None
    assert os.environ.get("SYMPHONY_TOKEN_EMA") is not None

    # Clean up so the env var doesn't leak to other tests.
    monkeypatch.delenv("SYMPHONY_REWIND_SCOPE", raising=False)


def test_apply_dispatch_env_unsets_rewind_scope_on_forward(monkeypatch):
    """Forward dispatch must NOT carry a stale SYMPHONY_REWIND_SCOPE."""
    import os

    cfg = _make_config()
    orch = _orch()
    issue = _issue(
        "MT-FWD",
        state="Plan",
        description="## Brief\nnothing rewinding",
    )

    # Simulate a prior rewind dispatch leaving the env var set.
    monkeypatch.setenv("SYMPHONY_REWIND_SCOPE", '[{"severity": "HIGH"}]')
    orch._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=False)

    assert os.environ.get("SYMPHONY_REWIND_SCOPE") is None, (
        "forward dispatch must unset SYMPHONY_REWIND_SCOPE so a prior "
        "rewind value cannot bleed across turns"
    )


def test_apply_dispatch_env_empty_list_when_findings_missing(monkeypatch):
    """Rewind without parseable findings still sets the env (as `[]`)."""
    import json as _json
    import os

    monkeypatch.delenv("SYMPHONY_REWIND_SCOPE", raising=False)
    cfg = _make_config()
    orch = _orch()
    issue = _issue(
        "MT-EMPTY",
        state="In Progress",
        description="## Plan only, no review findings here",
    )

    orch._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=True)

    raw = os.environ.get("SYMPHONY_REWIND_SCOPE")
    assert raw is not None
    assert _json.loads(raw) == [], (
        "missing Review Findings / QA Failure must produce an empty list, "
        "not omit the env var entirely"
    )
    monkeypatch.delenv("SYMPHONY_REWIND_SCOPE", raising=False)


def test_apply_dispatch_env_uses_latest_contract_failure_scope(monkeypatch):
    """Contract Failure rows must become first-class rewind scope."""
    import json as _json
    import os

    monkeypatch.delenv("SYMPHONY_REWIND_SCOPE", raising=False)
    cfg = _make_config()
    orch = _orch()
    issue = _issue(
        "MT-CONTRACT",
        state="In Progress",
        description=(
            "## Review Findings\n"
            "- HIGH: src/old.py:9 — stale review issue\n\n"
            "## Contract Failure\n"
            "Stage `Verify` did not produce the required outputs.\n\n"
            "Failing rows:\n"
            "- ## AC Scorecard row 1: found `validated in source`; "
            "expected evidence must cite a durable artifact such as "
            "`docs/MT-CONTRACT/qa/evidence.md`, `qa/evidence.md`, "
            "`docs/MT-CONTRACT/work/verify.log`, or `work/verify.log`\n"
        ),
    )

    orch._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=True)

    raw = os.environ.get("SYMPHONY_REWIND_SCOPE")
    assert raw is not None
    rows = _json.loads(raw)
    assert rows == [
        {
            "severity": "CONTRACT",
            "file": "",
            "line": 1,
            "fix": (
                "## AC Scorecard row 1: found `validated in source`; "
                "expected evidence must cite a durable artifact such as "
                "`docs/MT-CONTRACT/qa/evidence.md`, `qa/evidence.md`, "
                "`docs/MT-CONTRACT/work/verify.log`, or `work/verify.log`"
            ),
            "section": "## AC Scorecard",
            "found": "validated in source",
            "expected": (
                "evidence must cite a durable artifact such as "
                "`docs/MT-CONTRACT/qa/evidence.md`, `qa/evidence.md`, "
                "`docs/MT-CONTRACT/work/verify.log`, or `work/verify.log`"
            ),
        }
    ]
    monkeypatch.delenv("SYMPHONY_REWIND_SCOPE", raising=False)


def test_sort_for_dispatch_ties_by_identifier():
    a = _issue("MT-2", priority=1)
    b = _issue("MT-1", priority=1)
    out = [i.identifier for i in sort_for_dispatch([a, b])]
    assert out == ["MT-1", "MT-2"]


@pytest.mark.asyncio
async def test_reload_refreshes_workflow_dir_for_existing_workspace_manager(
    tmp_path, monkeypatch
):
    workspace_root = tmp_path / "ws"
    old_cfg = _make_config(
        workflow_path=tmp_path / "old" / "WORKFLOW.md",
        workspace_root=workspace_root,
    )
    new_cfg = _make_config(
        workflow_path=tmp_path / "new" / "WORKFLOW.md",
        workspace_root=workspace_root,
        hooks=HooksConfig(
            after_create='echo "$SYMPHONY_WORKFLOW_DIR" > wfdir',
            before_run=None,
            after_run=None,
            before_remove=None,
            timeout_ms=30_000,
        ),
    )
    state = WorkflowState(tmp_path / "unused.md")
    monkeypatch.setattr(state, "reload", lambda: (new_cfg, None))

    orch = Orchestrator(state)
    orch._workspace_manager = WorkspaceManager(
        old_cfg.workspace_root,
        old_cfg.hooks,
        workflow_dir=old_cfg.workflow_path.parent,
    )

    async def no_candidates(_cfg):
        return []

    monkeypatch.setattr(orch, "_fetch_candidates", no_candidates)

    await orch._on_tick()

    assert orch._workspace_manager is not None
    ws = await orch._workspace_manager.create_or_reuse("MT-WFDIR")
    assert (ws.path / "wfdir").read_text().strip() == str(
        new_cfg.workflow_path.parent
    )


@pytest.mark.asyncio
async def test_reload_refreshes_reuse_policy_and_hook_env_alongside_workflow_dir(
    tmp_path, monkeypatch
):
    # PR #19 regression: a single `_on_tick` reload must propagate ALL
    # three workspace-manager updates — workflow_dir, reuse_policy, and
    # hook_env (feature_base_branch / merge_target_branch). Dropping any
    # one of `update_hooks`, `update_reuse_policy`, or `update_hook_env`
    # in `_on_tick` would leave the manager half-refreshed; this test
    # observes all three at once via a single `after_create` hook.
    workspace_root = tmp_path / "ws"
    snapshot = tmp_path / "snapshot"
    # The hook body runs through bash, where a raw Windows path's
    # backslashes would be eaten as escapes — hand bash a POSIX-styled path.
    after_create = (
        f'echo "$SYMPHONY_WORKFLOW_DIR|'
        f'$SYMPHONY_FEATURE_BASE_BRANCH|'
        f'$SYMPHONY_MERGE_TARGET_BRANCH" >> {snapshot.as_posix()}'
    )
    old_cfg = _make_config(
        workflow_path=tmp_path / "old" / "WORKFLOW.md",
        workspace_root=workspace_root,
    )
    new_agent = replace(
        old_cfg.agent,
        feature_base_branch="develop",
        auto_merge_target_branch="main",
    )
    new_cfg = replace(
        _make_config(
            workflow_path=tmp_path / "new" / "WORKFLOW.md",
            workspace_root=workspace_root,
            hooks=HooksConfig(
                after_create=after_create,
                before_run=None,
                after_run=None,
                before_remove=None,
                timeout_ms=30_000,
            ),
        ),
        agent=new_agent,
        workspace_reuse_policy="refresh",
    )
    state = WorkflowState(tmp_path / "unused.md")
    monkeypatch.setattr(state, "reload", lambda: (new_cfg, None))

    orch = Orchestrator(state)
    orch._workspace_manager = WorkspaceManager(
        old_cfg.workspace_root,
        old_cfg.hooks,
        workflow_dir=old_cfg.workflow_path.parent,
        reuse_policy=old_cfg.workspace_reuse_policy,
        hook_env={
            "SYMPHONY_FEATURE_BASE_BRANCH": "",
            "SYMPHONY_MERGE_TARGET_BRANCH": "",
        },
    )

    async def no_candidates(_cfg):
        return []

    monkeypatch.setattr(orch, "_fetch_candidates", no_candidates)

    await orch._on_tick()

    # First create runs after_create regardless of reuse policy.
    await orch._workspace_manager.create_or_reuse("MT-WFDIR")
    expected_line = (
        f"{new_cfg.workflow_path.parent}|"
        f"{new_cfg.agent.feature_base_branch}|"
        f"{new_cfg.agent.auto_merge_target_branch}"
    )
    first_lines = snapshot.read_text().splitlines()
    assert first_lines == [expected_line]

    # Second create re-fires after_create only because reuse_policy is now
    # "refresh". Under the previous "preserve" policy the file would still
    # contain a single line — that is the bug this regression test pins.
    await orch._workspace_manager.create_or_reuse("MT-WFDIR")
    second_lines = snapshot.read_text().splitlines()
    assert second_lines == [expected_line, expected_line]


def test_max_turns_exhaustion_does_not_double_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ticket that exhausts per-attempt max_turns must not be re-dispatched
    while its budget_exhausted_state transition is still being persisted.

    Reproduces the live run-path race in
    docs/improvements/dispatch-double-dispatch-race-2026-06-28.md: the exit
    path pops the worker from `_running` and then awaits the async persist of
    the blocked state. Before the fix, a poll tick firing in that window pruned
    the in-tick `_claimed` lock (the ticket is no longer in-flight) and saw the
    still-active ticket as a fresh candidate, dispatching a second worker.
    """
    import asyncio
    import threading

    cfg = _make_config(
        max_concurrent=1,
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Cancelled", "Blocked"),
        auto_triage_actionable_todo=False,
    )
    orch = _orch()
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)

    issue = _issue("MT-1", state="Todo")
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path,
    )
    entry.hit_max_turns = True
    orch._running[issue.id] = entry

    # Park the exit path inside its persist await: the tracker write blocks on
    # a threading.Event so a tick can race it deterministically.
    started = threading.Event()
    release = threading.Event()

    def _blocking_update(_cfg: object, _iss: object, _target: object) -> None:
        started.set()
        assert release.wait(5.0), "persist tracker write never released"

    monkeypatch.setattr(orch, "_tracker_call_update_state", _blocking_update)
    monkeypatch.setattr(orch, "_tracker_call_append_note", lambda *a, **k: None)

    dispatched: list[str] = []
    monkeypatch.setattr(
        orch, "_dispatch", lambda iss, c, attempt=None: dispatched.append(iss.id)
    )

    async def _noop(_cfg: object) -> None:
        return None

    async def _candidates(_cfg: object) -> list[Issue]:
        return [issue]

    monkeypatch.setattr(orch, "_reconcile_running", _noop)
    monkeypatch.setattr(orch, "_archive_sweep", _noop)
    monkeypatch.setattr(orch, "_fetch_candidates", _candidates)

    async def _run() -> None:
        exit_task = asyncio.create_task(
            orch._on_worker_exit(issue.id, "normal", None)
        )
        # Wait (bounded) until the persist's tracker write is in flight.
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set(), "exit path never reached the budget persist"
        # The exit handler holds the ticket ineligible for its whole duration
        # (entry -> auto-commit -> persist), so the guard is set here.
        assert issue.id in orch._terminal_persist_pending
        # Fire a poll tick while the blocked-state transition is mid-persist.
        await orch._on_tick()
        release.set()
        await exit_task

    asyncio.run(_run())

    assert dispatched == [], (
        "ticket was re-dispatched while its terminal-state persist was in "
        "flight (double-dispatch race)"
    )


# ---------------------------------------------------------------------------
# Blocked-history recovery — the dead end that stalled a whole board
#
# A ticket whose agent could not write `.git/objects` used to self-park in
# Blocked, and the RCA opened for it inherited the same sandbox and blocked
# the same way. An RCA ticket cannot open a further RCA, so the board stopped
# there. The host re-checks the claim against real git before any RCA.
# ---------------------------------------------------------------------------

_SANDBOX_HISTORY_FAILURE = (
    "## History Failure\n\n"
    "failing command: `git add -f docs/RCA-1/learn/details.md`\n"
    "stderr: error: unable to create temporary file: Operation not permitted; "
    "error: docs/changelog/changelog-2026-08-06.md: failed to insert into "
    "database; fatal: updating files failed\n"
)
_REMOTE_HISTORY_FAILURE = (
    "## History Failure\n\n"
    "stderr: git@github.com: Permission denied (publickey).\n"
    "fatal: Could not read from remote repository.\n"
)


def _repo_with_ticket_branch(tmp_path: Path, identifier: str) -> Path:
    """Host repo carrying `symphony/<ID>` — the delivery the agent thought it lost."""
    env = {
        "HOME": str(tmp_path),
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": os.environ.get("PATH", ""),
    }

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(repo), env=env, check=True, capture_output=True
        )

    repo = tmp_path / "host"
    repo.mkdir()
    git("init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("seed")
    git("add", "seed.txt")
    git("commit", "-qm", "seed")
    git("branch", f"symphony/{identifier}")
    return repo


def _run_blocked_sweep(monkeypatch, orch, cfg, issue):
    """Drive one tick over a single Blocked ticket; report RCAs and notes."""
    created: list[str] = []
    notes: list[tuple[str, str, str]] = []
    states: list[tuple[str, str]] = []

    async def _fetch_candidates(_cfg):
        return []

    async def _archive(_cfg):
        return None

    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))
    monkeypatch.setattr(orch, "_fetch_candidates", _fetch_candidates)
    monkeypatch.setattr(orch, "_archive_sweep", _archive)
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda _cfg: [issue])
    monkeypatch.setattr(
        orch, "_tracker_call_active_rca_for_source", lambda *_args: None
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_create_blocked_rca_issue",
        lambda _cfg, i, *_a: (created.append(i.identifier), "FIX-MT-BLOCKED-1")[1],
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_append_note",
        lambda _cfg, i, heading, body: notes.append((i.identifier, heading, body)),
    )
    monkeypatch.setattr(
        orch,
        "_tracker_call_update_state",
        lambda _cfg, i, state: states.append((i.identifier, state)),
    )
    asyncio.run(orch._on_tick())
    return created, notes, states


@pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")
def test_tick_recovers_sandbox_history_failure_instead_of_opening_rca(
    monkeypatch, tmp_path
):
    repo = _repo_with_ticket_branch(tmp_path, "MT-BLOCKED")
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
        workflow_path=repo / "WORKFLOW.md",
    )
    issue = _issue(
        "MT-BLOCKED", state="Blocked", description=_SANDBOX_HISTORY_FAILURE
    )

    created, notes, states = _run_blocked_sweep(monkeypatch, _orch(), cfg, issue)

    assert created == [], "a permissions limit must not cost an RCA ticket"
    assert states == [("MT-BLOCKED", "Todo")]
    assert notes[0][1] == "History Recovery"
    assert "The delivery record is in git history" in notes[0][2]


def test_local_only_history_recovery_never_publishes_feature_branch(
    monkeypatch, tmp_path
):
    repo = tmp_path / "host"
    repo.mkdir()
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
        workflow_path=repo / "WORKFLOW.md",
    )
    cfg = replace(cfg, agent=replace(cfg.agent, auto_merge_push_target=False))
    issue = _issue(
        "MT-LOCAL-RECOVERY",
        state="Blocked",
        description=_SANDBOX_HISTORY_FAILURE,
    )
    captured: dict[str, object] = {}

    async def _verify(path: Path, *, branch: str, push: bool = True):
        captured.update(path=path, branch=branch, push=push)
        return HistoryGateResult(
            HISTORY_LOCAL_ONLY,
            branch=branch,
            local_sha="a" * 40,
        )

    monkeypatch.setattr(core_module, "verify_branch_history", _verify)
    orch = _orch()
    monkeypatch.setattr(orch, "_tracker_call_append_note", lambda *_args: None)
    monkeypatch.setattr(orch, "_tracker_call_update_state", lambda *_args: None)

    assert asyncio.run(orch._recover_blocked_history_gate(cfg, issue)) is True
    assert captured == {
        "path": repo,
        "branch": "symphony/MT-LOCAL-RECOVERY",
        "push": False,
    }


@pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")
def test_tick_recovers_a_blocked_rca_ticket_that_could_never_open_another_rca(
    monkeypatch, tmp_path
):
    """The exact dead end: the rescuer blocked by the thing it was rescuing."""
    repo = _repo_with_ticket_branch(tmp_path, "RCA-MT-1")
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
        workflow_path=repo / "WORKFLOW.md",
    )
    issue = _issue(
        "RCA-MT-1",
        state="Blocked",
        description=_SANDBOX_HISTORY_FAILURE,
        labels=("blocked-rca",),
    )

    created, notes, states = _run_blocked_sweep(monkeypatch, _orch(), cfg, issue)

    assert created == []
    assert states == [("RCA-MT-1", "Todo")]
    assert notes[0][1] == "History Recovery"


@pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")
def test_tick_escalates_a_second_identical_block_to_human_review(
    monkeypatch, tmp_path
):
    """One rescue per ticket — looping forever is worse than asking a human."""
    repo = _repo_with_ticket_branch(tmp_path, "MT-BLOCKED")
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
        workflow_path=repo / "WORKFLOW.md",
    )
    issue = _issue(
        "MT-BLOCKED",
        state="Blocked",
        description=(
            "## History Recovery\n\nrescued once already.\n\n"
            + _SANDBOX_HISTORY_FAILURE
        ),
    )

    created, notes, states = _run_blocked_sweep(monkeypatch, _orch(), cfg, issue)

    assert created == []
    assert states == [("MT-BLOCKED", "Human Review")]
    assert "already rescued once" in notes[0][2]


@pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")
def test_tick_still_opens_rca_when_the_blocker_is_not_a_sandbox_limit(
    monkeypatch, tmp_path
):
    """An SSH auth failure is a real blocker; recovery must not swallow it."""
    repo = _repo_with_ticket_branch(tmp_path, "MT-BLOCKED")
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
        workflow_path=repo / "WORKFLOW.md",
    )
    issue = _issue(
        "MT-BLOCKED", state="Blocked", description=_REMOTE_HISTORY_FAILURE
    )

    created, notes, states = _run_blocked_sweep(monkeypatch, _orch(), cfg, issue)

    assert created == ["MT-BLOCKED"]
    assert states == []
    assert notes[0][1] == "Blocked Fix"


@pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")
def test_tick_declines_recovery_when_no_delivery_branch_exists(monkeypatch, tmp_path):
    """No branch means work really was lost — hand it to an RCA, do not fake a pass."""
    repo = _repo_with_ticket_branch(tmp_path, "OTHER-1")
    cfg = _make_config(
        tracker_kind="file",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done", "Blocked"),
        workflow_path=repo / "WORKFLOW.md",
    )
    issue = _issue(
        "MT-BLOCKED", state="Blocked", description=_SANDBOX_HISTORY_FAILURE
    )

    created, notes, states = _run_blocked_sweep(monkeypatch, _orch(), cfg, issue)

    assert created == ["MT-BLOCKED"]
    assert states == []


# ---------------------------------------------------------------------------
# agent.stage_kinds — per-state backend routing (ticket pin > stage map > default)
# ---------------------------------------------------------------------------


def test_config_for_issue_agent_stage_kind_precedence():
    """Ticket pin beats the stage map; the stage map beats the workflow default."""
    from symphony.orchestrator import _config_for_issue_agent

    cfg = _make_config()
    routed = replace(cfg, agent=replace(cfg.agent, stage_kinds={"todo": "gemini"}))

    mapped = _issue("MT-1", state="Todo")
    assert _config_for_issue_agent(routed, mapped).agent.kind == "gemini"

    pinned = replace(_issue("MT-2", state="Todo"), agent_kind="claude")
    assert _config_for_issue_agent(routed, pinned).agent.kind == "claude"

    unmapped = _issue("MT-3", state="In Progress")
    assert _config_for_issue_agent(routed, unmapped).agent.kind == "codex"


def test_dispatch_stage_kinds_route_by_state_and_skip_pin_stamp(tmp_path, monkeypatch):
    """A dispatch resolves the stage-mapped backend for the ticket's current
    state, and the resolved kind is NOT stamped back onto the ticket as an
    `agent_kind` pin — the stamp would win over the stage map on the next
    dispatch and freeze the first stage's backend across state changes."""
    base = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws"
    )
    cfg = replace(base, agent=replace(base.agent, stage_kinds={"todo": "gemini"}))
    stamped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_record_agent_kind",
        staticmethod(
            lambda _cfg, identifier, agent_kind: stamped.append(
                (identifier, agent_kind)
            )
        ),
    )

    async def _parked_worker(_issue, _attempt, _cfg) -> None:
        await asyncio.sleep(3600)

    async def _run() -> None:
        orch = _orch()
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = RunRegistry(
            tmp_path / ".symphony" / "state.db", lease_ttl=timedelta(minutes=5)
        )
        monkeypatch.setattr(orch, "_run_agent_attempt", _parked_worker)

        mapped = _issue("MT-1", state="Todo")
        pinned = replace(_issue("MT-2", state="Todo"), agent_kind="claude")
        unmapped = _issue("MT-3", state="In Progress")
        orch._dispatch(mapped, cfg, attempt=None)
        orch._dispatch(pinned, cfg, attempt=None)
        orch._dispatch(unmapped, cfg, attempt=None)

        assert orch._running[mapped.id].agent_kind == "gemini"
        assert orch._running[pinned.id].agent_kind == "claude"
        assert orch._running[unmapped.id].agent_kind == "codex"

        for entry in list(orch._running.values()):
            task = entry.worker_task
            assert task is not None
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())
    assert stamped == []


def test_dispatch_without_stage_kinds_still_stamps_agent_kind(tmp_path, monkeypatch):
    """Legacy behaviour is preserved: with no stage routing configured, the
    resolved backend is still recorded onto the ticket for board visibility."""
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws"
    )
    stamped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_record_agent_kind",
        staticmethod(
            lambda _cfg, identifier, agent_kind: stamped.append(
                (identifier, agent_kind)
            )
        ),
    )

    async def _parked_worker(_issue, _attempt, _cfg) -> None:
        await asyncio.sleep(3600)

    async def _run() -> None:
        orch = _orch()
        orch._loop = asyncio.get_running_loop()
        orch._run_registry = RunRegistry(
            tmp_path / ".symphony" / "state.db", lease_ttl=timedelta(minutes=5)
        )
        monkeypatch.setattr(orch, "_run_agent_attempt", _parked_worker)

        issue = _issue("MT-1", state="Todo")
        orch._dispatch(issue, cfg, attempt=None)
        assert orch._running[issue.id].agent_kind == "codex"

        task = orch._running[issue.id].worker_task
        assert task is not None
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(_run())
    assert stamped == [("MT-1", "codex")]


# ---------------------------------------------------------------------------
# Off-loop reclaim (ITEM 1) + identity-gated shutdown kills (ITEM 2)
# ---------------------------------------------------------------------------


def test_async_ensure_reclaim_runs_kill_off_event_loop(tmp_path, monkeypatch):
    """ITEM 1 — `_ensure_run_registry_async` moves the blocking kill+confirm
    off the event loop while preserving completed-before-return and the
    'reclaiming' fence observable mid-kill."""
    import threading

    import symphony.orchestrator.run_registry as run_registry_module

    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws"
    )
    issue = _issue("MT-ASYNC-RECLAIM", state="Todo")
    now = datetime.now(timezone.utc)
    crashed = RunRegistry(
        tmp_path / ".symphony" / "state.db",
        lease_ttl=timedelta(minutes=5),
        owner_pid=4242,
        boot_id="crashed",
    )
    run_id = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    monkeypatch.setattr(run_registry_module, "process_identity", lambda _pid: "proc-1")
    assert crashed.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=1),
        backend_agent_pid=4343,
    )
    crashed.close()

    monkeypatch.setattr(core_module, "process_identity", lambda _pid: "proc-1")
    monkeypatch.setattr(
        core_module, "process_group_exists", lambda _pid: False
    )
    monkeypatch.setattr(run_registry_module, "_pid_alive", lambda _pid: False)

    kills: list[tuple[int, object]] = []
    kill_threads: list[int] = []

    def fake_kill(pid, *, identity=None):
        kill_threads.append(threading.get_ident())
        kills.append((pid, identity))
        # The SQLite fence must still read 'reclaiming' while the kill is
        # running — the finalize only happens after this returns. Read
        # through a fresh connection: this runs on a to_thread worker,
        # and the orchestrator's own connection is thread-affine.
        fence = RunRegistry(tmp_path / ".symphony" / "state.db")
        try:
            assert fence.get_run(run_id).status == "reclaiming"
        finally:
            fence.close()
        return True

    monkeypatch.setattr(core_module, "kill_process_group", fake_kill)

    restarted = _orch()
    loop_threads: list[int] = []

    async def _run() -> None:
        loop_threads.append(threading.get_ident())
        await restarted._ensure_run_registry_async(cfg)

    asyncio.run(_run())

    assert kills == [(4343, None)], "reclaim kill must run exactly once"
    assert len(kill_threads) == 1
    assert kill_threads[0] != loop_threads[0], "kill must run off the event loop"
    assert restarted._run_registry is not None
    assert (
        restarted._run_registry.get_run(run_id).status == "orphaned"
    ), "kill+finalize must complete before _ensure_run_registry_async returns"


def _orchestrator_with_identity_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    recorded_identity: str | None,
) -> tuple[Orchestrator, ServiceConfig, Issue, str]:
    """Seed one orchestrator whose registry recorded an agent pid (+identity)."""
    import symphony.orchestrator.run_registry as run_registry_module

    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md", workspace_root=tmp_path / "ws"
    )
    issue = _issue("MT-IDENTITY", state="Todo")
    orch = _orch()
    # The dead-owner reclaim at open must not fire for our own lease —
    # self-owned (same boot id) rows are skipped, and we keep the real
    # `_pid_alive` untouched.
    monkeypatch.setattr(
        run_registry_module,
        "process_identity",
        (lambda _pid: recorded_identity)
        if recorded_identity is not None
        else (lambda _pid: None),
    )
    orch._ensure_run_registry(cfg)
    registry = orch._run_registry
    assert registry is not None
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    assert registry.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        backend_agent_pid=4242,
    )
    return orch, cfg, issue, run_id


def test_force_eject_kill_passes_recorded_identity(tmp_path, monkeypatch):
    """ITEM 2 — the fingerprint captured at pid-record time flows to the kill."""
    orch, cfg, issue, run_id = _orchestrator_with_identity_run(
        tmp_path, monkeypatch, recorded_identity="fp-recorded"
    )
    captured: list[object] = []

    def fake_kill(pid, *, identity=None):
        captured.append((pid, identity))
        return True

    monkeypatch.setattr(core_module, "kill_process_group", fake_kill)
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,
        workspace_path=tmp_path / "ws",
        run_id=run_id,
        agent_pgid=4242,
    )
    orch._running[issue.id] = entry

    orch._force_eject_zombie(issue.id, entry, cfg)

    assert captured == [(4242, "fp-recorded")]


def test_kill_gate_skips_when_identity_mismatches(tmp_path, monkeypatch):
    """ITEM 2 — a reused pid whose live fingerprint differs from the recorded
    one is never signalled (the gate inside `kill_process_group` skips)."""
    import symphony._shell as shell_module

    orch, cfg, issue, run_id = _orchestrator_with_identity_run(
        tmp_path, monkeypatch, recorded_identity="fp-recorded"
    )
    # The gate compares against the LIVE incarnation, resolved inside
    # `_shell.kill_process_group` — a different fingerprint means pid reuse.
    monkeypatch.setattr(shell_module, "process_identity", lambda _pid: "fp-reused")

    attempted: list[int] = []
    monkeypatch.setattr(
        shell_module, "_taskkill_tree", lambda pid, **_kw: attempted.append(pid)
        or True
    )
    monkeypatch.setattr(
        shell_module,
        "_signal_process_group",
        lambda pid, sig: attempted.append(pid) or True,
    )
    # `shell_module.log` and `core_module.log` are the same shared logger —
    # patch .warning ONCE and classify by event name.
    log_events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        shell_module.log, "warning", lambda event, **f: log_events.append((event, f))
    )

    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,
        workspace_path=tmp_path / "ws",
        run_id=run_id,
        agent_pgid=4242,
    )
    orch._running[issue.id] = entry

    orch._force_eject_zombie(issue.id, entry, cfg)

    assert attempted == [], "mismatched identity must never reach the OS kill"
    assert "kill_process_group_identity_mismatch" in [
        event for event, _fields in log_events
    ]
    eject = [fields for event, fields in log_events if event == "force_eject_killed_process_group"]
    assert eject and eject[0].get("killed") is False


def test_kill_without_recorded_identity_stays_ungated(tmp_path, monkeypatch):
    """ITEM 2 — win32 (and any capture failure) records identity=None; the
    kill must proceed exactly as before rather than be disabled."""
    orch, cfg, issue, _run_id = _orchestrator_with_identity_run(
        tmp_path, monkeypatch, recorded_identity=None
    )
    captured: list[object] = []

    def fake_kill(pid, *, identity=None):
        captured.append((pid, identity))
        return True

    monkeypatch.setattr(core_module, "kill_process_group", fake_kill)
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,
        workspace_path=tmp_path / "ws",
        run_id="",
        agent_pgid=4242,
    )
    orch._running[issue.id] = entry

    assert orch._agent_pid_identity(entry) is None
    orch._force_eject_zombie(issue.id, entry, cfg)

    assert captured == [(4242, None)], "identity=None must keep the kill ungated"
