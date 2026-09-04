"""Deep (8-lane) preset — orchestrator-level coverage (review F-05 / F-23).

Before this file the deep preset had *only* prompt-text greps: no test ever
loaded a deep board into `build_service_config`, dispatched a deep ticket, or
walked its spawned DAG. That is how F-05 (no merge/branch story) and F-23 (no
post-apply validation) reached a release candidate.

What is covered here:

* `apply_lane_preset("deep")` produces a WORKFLOW.md that `build_service_config`
  accepts and that passes the stage-turn-budget preflight.
* A deep request ticket walks `Intake → Research → Plan → Review → Done`
  inside one dispatch against a real `FileBoardTracker` and a mock backend.
* The Plan lane's spawned DAG is gated the way the merge contract claims:
  `BUILD-1` is not dispatchable while the request ticket is open, and becomes
  dispatchable once the request ticket reaches `Done` (Review's PASS).
* The deep board's merge contract is checked by `symphony doctor`.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import symphony.orchestrator.core as core_mod
from symphony.cli.doctor import check_deep_preset_merge_contract
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator, RunningEntry
from symphony.trackers.file import (
    FileBoardTracker,
    parse_ticket_file,
    write_ticket_atomic,
)
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    PrimeAgentConfig,
    PromptConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
    TuiConfig,
    WorkflowState,
    build_service_config,
    load_workflow,
)
from symphony.workflow.mutate import apply_lane_preset
from symphony.workflow.preflight import stage_turn_budget_error
from symphony.workflow.presets import DEEP_PRESET, guess_lane_preset


_DEEP_ACTIVE = DEEP_PRESET.active_states


# ---------------------------------------------------------------------------
# preset round-trip: apply → load → preflight
# ---------------------------------------------------------------------------


def _seed_workflow(tmp_path: Path) -> Path:
    # F-11: `apply_lane_preset` refuses a board that lacks the preset's
    # prompt bodies, so start from a board that carries the shipped set.
    source = Path(__file__).resolve().parents[1] / "docs" / "symphony-prompts"
    target = tmp_path / "docs" / "symphony-prompts"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    path = tmp_path / "WORKFLOW.md"
    path.write_text(
        "\n".join(
            [
                "---",
                "tracker:",
                "  kind: file",
                "  board_root: ./kanban",
                "  active_states: [Todo, In Progress, Verify, Document]",
                "  terminal_states: [Human Review, Done, Blocked, Archive]",
                "agent:",
                "  kind: codex",
                "  max_turns: 100",
                "server:",
                "  port: 9412",
                "---",
                "fallback prompt",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "kanban").mkdir()
    return path


def test_deep_preset_board_loads_and_passes_preflight(tmp_path: Path) -> None:
    workflow_path = _seed_workflow(tmp_path)

    apply_lane_preset(workflow_path, "deep")

    cfg = build_service_config(load_workflow(workflow_path))
    assert guess_lane_preset(cfg.tracker.active_states) == "deep"
    assert stage_turn_budget_error(cfg) is None, (
        "deep board's max_turns does not cover its 8 active lanes"
    )
    assert check_deep_preset_merge_contract(cfg).status == "pass"


def test_deep_preset_board_flags_a_broken_merge_contract(tmp_path: Path) -> None:
    workflow_path = _seed_workflow(tmp_path)
    apply_lane_preset(workflow_path, "deep")
    cfg = build_service_config(load_workflow(workflow_path))

    broken = replace(
        cfg,
        agent=replace(
            cfg.agent,
            feature_base_branch="main",
            auto_merge_target_branch="release",
        ),
    )

    assert check_deep_preset_merge_contract(broken).status == "fail"


# ---------------------------------------------------------------------------
# orchestrator e2e — one deep request ticket, mock backend
# ---------------------------------------------------------------------------


@dataclass
class _DeepBackend:
    """Mock backend that walks the ticket file through the deep lanes."""

    ticket_path: Path
    transitions: list[tuple[str, str]]
    board_root: Path
    spawn_at_state: str | None = None
    spawned: list[str] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def start(self) -> None:
        self.calls.append(("start", {}))

    async def initialize(self) -> None:
        self.calls.append(("initialize", {}))

    async def start_session(self, *, initial_prompt: str, issue_title: str) -> None:
        self.calls.append(("start_session", {"issue_title": issue_title}))

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> None:
        self.calls.append(("run_turn", {"is_continuation": is_continuation}))
        if not self.transitions:
            return
        new_state, body = self.transitions.pop(0)
        front, _ = parse_ticket_file(self.ticket_path)
        current = str(front.get("state", ""))
        if self.spawn_at_state and current == self.spawn_at_state:
            self._spawn_dag()
        front["state"] = new_state
        front["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_ticket_atomic(self.ticket_path, front, body)

    def _spawn_dag(self) -> None:
        """What `deep/plan.md` tells the agent to run via `symphony board new`."""
        tracker = FileBoardTracker(_tracker_cfg(self.board_root))
        tracker.create(
            identifier="BUILD-1",
            title="slice one",
            state="Build",
            description="## Acceptance Criteria\n\n- WHEN x THEN y",
            blocked_by=["REQ-1"],
            request="REQ-A",
        )
        tracker.create(
            identifier="VERIFY-1",
            title="re-prove all claims",
            state="Verify",
            description="verify",
            blocked_by=["BUILD-1"],
            request="REQ-A",
        )
        self.spawned = ["BUILD-1", "VERIFY-1"]

    async def stop(self) -> None:
        self.calls.append(("stop", {}))


class _FakeWorkspace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.workspace_key = "fake"
        self.created_now = True


class _FakeWorkspaceManager:
    def __init__(self, path: Path) -> None:
        self._path = path

    def path_for(self, identifier: str) -> Path:
        del identifier
        return self._path

    async def create_or_reuse(self, identifier: str) -> _FakeWorkspace:
        del identifier
        return _FakeWorkspace(self._path)

    async def before_run(self, path: Path) -> None:
        del path

    async def after_run_best_effort(self, path: Path) -> None:
        del path


def _tracker_cfg(board_root: Path) -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=_DEEP_ACTIVE,
        terminal_states=("Done", "Human Review", "Blocked", "Cancelled"),
        board_root=board_root,
    )


def _deep_config(board_root: Path) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=board_root.parent / "ws",
        tracker=_tracker_cfg(board_root),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=1,
            max_turns=12,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
            max_attempts=3,
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
            command="claude -p",
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
        tui=TuiConfig(language="en", visible_lanes=8),
        prompts=PromptConfig(),
        prompt_template="issue={{ issue.identifier }} state={{ issue.state }}",
    )


def test_deep_request_ticket_walks_intake_to_done_and_spawns_its_dag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board_root = tmp_path / "kanban"
    board_root.mkdir()
    ticket_path = board_root / "REQ-1.md"
    write_ticket_atomic(
        ticket_path,
        {
            "id": "REQ-1",
            "identifier": "REQ-1",
            "title": "deep request",
            "state": "Intake",
            "priority": 2,
            "request": "REQ-A",
            "created_at": "2026-01-01T00:00:00Z",
        },
        "## Brief\n\nship the thing",
    )
    cfg = _deep_config(board_root)

    backends: list[_DeepBackend] = []
    script = [
        ("Research", "## Brief\n\nship the thing"),
        ("Plan", "## Research\n\nevidence"),
        ("Review", "## Plan Summary\n\nBUILD-1, VERIFY-1"),
        ("Done", "## Objections\n\nnone; verdict: PASS"),
    ]

    def _factory(init: Any) -> _DeepBackend:
        backend = _DeepBackend(
            ticket_path=ticket_path,
            transitions=script,
            board_root=board_root,
            spawn_at_state="Plan",
        )
        backends.append(backend)
        return backend

    monkeypatch.setattr(core_mod, "build_backend", _factory)

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    orch._workspace_manager = _FakeWorkspaceManager(workspace_path)  # type: ignore[assignment]
    issue = Issue(
        id="REQ-1",
        identifier="REQ-1",
        title="deep request",
        description="## Brief\n\nship the thing",
        priority=2,
        state="Intake",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    orch._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=workspace_path,
    )

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    front, _ = parse_ticket_file(ticket_path)
    assert front["state"] == "Done", (
        "deep request ticket did not walk Intake -> Done; ended at "
        f"{front['state']!r}"
    )
    # One backend rebuild per lane transition: the deep preset genuinely
    # exercises the multi-lane in-run path (which is where F-01 lived).
    assert len(backends) >= 4
    tracker = FileBoardTracker(_tracker_cfg(board_root))
    board = {issue.identifier: issue for issue in tracker.scan_all()}
    assert {"BUILD-1", "VERIFY-1"} <= set(board), "Plan lane spawned no DAG"
    assert [b.identifier for b in board["BUILD-1"].blocked_by] == ["REQ-1"]
    assert [b.identifier for b in board["VERIFY-1"].blocked_by] == ["BUILD-1"]


def test_deep_build_ticket_is_released_only_after_the_request_reaches_done(
    tmp_path: Path,
) -> None:
    """Merge contract, gating half: Review's PASS (request -> Done) releases builds."""
    board_root = tmp_path / "kanban"
    board_root.mkdir()
    cfg = _deep_config(board_root)
    tracker = FileBoardTracker(_tracker_cfg(board_root))
    tracker.create(identifier="REQ-1", title="request", state="Review")
    tracker.create(
        identifier="BUILD-1",
        title="slice",
        state="Build",
        blocked_by=["REQ-1"],
    )
    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))

    blocked = {i.identifier: i for i in tracker.scan_all()}["BUILD-1"]
    decision = orch._eligibility_decision(blocked, cfg, owning_retry=False)
    assert decision.disposition is not core_mod._EligibilityDisposition.READY
    assert "blocker unresolved" in decision.reason

    tracker.transition("REQ-1", "Done")
    released = {i.identifier: i for i in tracker.scan_all()}["BUILD-1"]
    decision = orch._eligibility_decision(released, cfg, owning_retry=False)
    assert decision.disposition is core_mod._EligibilityDisposition.READY


# ---------------------------------------------------------------------------
# dark-factory cycle: an approved chat intent is what enters Intake
# ---------------------------------------------------------------------------


def test_approved_intent_ticket_walks_the_deep_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`file_intent_request` produces a request ticket the deep pipeline runs.

    The chat gate files the ticket server-side; from there the walk is the
    same as any deep request: Intake -> Research -> Plan -> Review -> Done,
    with the Plan lane spawning the Build/Verify DAG.
    """
    from symphony.intent import IntentAction, file_intent_request

    from tests.test_chat_intent import INTENT_BODY

    board_root = tmp_path / "kanban"
    board_root.mkdir()
    cfg = replace(_deep_config(board_root), workflow_path=tmp_path / "WORKFLOW.md")
    action = IntentAction(
        action_id="intent-" + "a" * 32,
        slug="todo-app",
        title="Build a Todo app",
        track="full",
        intent=INTENT_BODY,
    )
    filed = file_intent_request(
        cfg, action, approved_at="2026-09-05T10:00:00Z", session_id="20260905-100000-abcdef"
    )
    assert filed["identifier"] == "REQ-1" and filed["state"] == "Intake"
    ticket_path = Path(filed["path"])
    assert (tmp_path / ".sdlc" / "work" / "todo-app" / "intent.md").is_file()
    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Intake" and front["request"] == "todo-app"
    assert "## Problem" in body and "## Track\n\nfull" in body

    script = [
        ("Research", body + "\n\n## Brief\n\nfeature, full track"),
        ("Plan", "## Research\n\nevidence"),
        ("Review", "## Plan Summary\n\nBUILD-1, VERIFY-1"),
        ("Done", "## Objections\n\nnone; verdict: PASS"),
    ]
    backends: list[_DeepBackend] = []

    def _factory(init: Any) -> _DeepBackend:
        backend = _DeepBackend(
            ticket_path=ticket_path,
            transitions=script,
            board_root=board_root,
            spawn_at_state="Plan",
        )
        backends.append(backend)
        return backend

    monkeypatch.setattr(core_mod, "build_backend", _factory)
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    orch._workspace_manager = _FakeWorkspaceManager(workspace_path)  # type: ignore[assignment]
    tracker = FileBoardTracker(_tracker_cfg(board_root))
    [issue] = [i for i in tracker.scan_all() if i.identifier == "REQ-1"]
    orch._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=workspace_path,
    )

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    front, _ = parse_ticket_file(ticket_path)
    assert front["state"] == "Done", f"ended at {front['state']!r}"
    assert len(backends) >= 4
    board = {i.identifier: i for i in tracker.scan_all()}
    assert {"BUILD-1", "VERIFY-1"} <= set(board)


def test_deep_intake_prompt_consumes_the_approved_intent() -> None:
    root = Path(__file__).resolve().parents[1] / "docs" / "symphony-prompts" / "file" / "deep"
    intake = (root / "intake.md").read_text(encoding="utf-8")
    assert "Do NOT re-ask" in intake
    assert "## Track" in intake and "`micro`" in intake
    assert "set state to `Plan` directly" in intake
    base = (root / "base.md").read_text(encoding="utf-8")
    assert "approved intent" in base and "single human gate" in base
