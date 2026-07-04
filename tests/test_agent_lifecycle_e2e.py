"""End-to-end Symphony agent lifecycle: Todo -> Human Review golden path.

Existing tests cover *one* phase transition at a time. This file walks an
issue through the full canonical 4-active-state pipeline
(Todo -> In Progress -> Verify -> Learn -> Human Review) and
asserts the *expected outputs at each phase boundary*:

1. A fresh backend is built per phase (state changes => session rebuild).
2. Each phase's first prompt uses the stage-specific prompt template.
3. The freshly rendered first prompt reaches `start_session` (not `run_turn`).
4. `run_turn` on the freshly built backend carries `is_continuation=False`.
5. Session ids are distinct across all 4 active phases.
6. `WorkspaceManager.after_run_best_effort` fires once per phase.
7. On the final Human Review refresh the worker exits cleanly, the running slot
   is released, and no retry entry survives.

The test deliberately uses the same `_FakeBackend` + `_FakeWorkspaceManager`
shape as `test_orchestrator_phase_transition.py` so a future refactor of
those helpers ripples both places at once.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from symphony import orchestrator as orch_mod
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator, RunningEntry
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    PromptConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
    TuiConfig,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Fixtures (mirrors test_orchestrator_phase_transition.py shape)
# ---------------------------------------------------------------------------


CANONICAL_ACTIVE_STATES = (
    "Todo",
    "In Progress",
    "Verify",
    "Learn",
)
TERMINAL_STATES = ("Human Review", "Done", "Cancelled", "Blocked")


@dataclass
class _FakeBackend:
    init_id: int
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    session_id: str = ""

    async def start(self) -> None:
        self.calls.append(("start", {}))

    async def initialize(self) -> None:
        self.calls.append(("initialize", {}))

    async def start_session(self, *, initial_prompt: str, issue_title: str) -> None:
        self.session_id = f"session-{self.init_id}"
        self.calls.append(
            ("start_session", {"initial_prompt": initial_prompt, "issue_title": issue_title})
        )

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> None:
        self.calls.append(("run_turn", {"prompt": prompt, "is_continuation": is_continuation}))

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
        self.after_run_paths: list[Path] = []

    def path_for(self, identifier: str) -> Path:
        del identifier
        return self._path

    async def create_or_reuse(self, identifier: str) -> _FakeWorkspace:
        del identifier
        return _FakeWorkspace(self._path)

    async def before_run(self, path: Path) -> None:
        del path
        return None

    async def after_run_best_effort(self, path: Path) -> None:
        self.after_run_paths.append(path)
        return None


def _make_lifecycle_config(*, max_turns: int = 12) -> ServiceConfig:
    """Config wired with stage_templates for every canonical state.

    The orchestrator runs `run_turn` then `_refresh_issue_state`; a state
    change rebuilds the backend before the next turn. `max_turns` must be
    >= number of phase iterations or the worker exits early via the
    `worker_max_turns_exhausted` path before the lifecycle finishes.
    """

    base_template = "BASE id={{ issue.identifier }}"
    stage_templates: dict[str, str] = {
        "todo": "TODO_BODY state={{ issue.state }}",
        "in progress": "INPROGRESS_BODY state={{ issue.state }}",
        "verify": "VERIFY_BODY state={{ issue.state }}",
        "learn": "LEARN_BODY state={{ issue.state }}",
    }
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.lifecycle.md"),
        poll_interval_ms=30_000,
        workspace_root=Path("/tmp/ws"),
        tracker=TrackerConfig(
            kind="file",
            endpoint="https://example.invalid/graphql",
            api_key="tok",
            project_slug="proj",
            active_states=CANONICAL_ACTIVE_STATES,
            terminal_states=TERMINAL_STATES,
            board_root=Path("/tmp/kanban-noop"),
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=1,
            max_turns=max_turns,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
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
        server=ServerConfig(port=None),
        tui=TuiConfig(language="en", visible_lanes=5),
        prompts=PromptConfig(base_template=base_template, stage_templates=stage_templates),
        prompt_template="LEGACY {{ issue.state }}",
    )


# A ticket body satisfying the 4-stage contract evaluator. Without these
# sections every In Progress->Verify or Verify->Learn transition would trip
# `evaluate_contract`. We are testing lifecycle wiring, not contract parsing.
_CONTRACT_CLEAN_BODY = (
    "## Plan\n"
    "- build it\n"
    "\n"
    "## Acceptance Tests\n"
    "- pytest -q\n"
    "\n"
    "## Done Signals\n"
    "- behavior visible\n"
    "\n"
    "## Implementation\n"
    "- changed source\n"
    "\n"
    "## Self-Critique\n"
    "- checked edge paths\n"
    "\n"
    "## Security Audit\n"
    "| check | verdict | evidence |\n"
    "| --- | --- | --- |\n"
    "| secrets | pass | qa/security.md |\n"
    "\n"
    "## Review\n"
    "Clean pass.\n"
    "\n"
    "## QA Evidence\n"
    "All green.\n"
    "\n"
    "## AC Scorecard\n"
    "| signal | source | result | evidence |\n"
    "| --- | --- | --- | --- |\n"
    "| ac-1 | pytest | pass | LIFE-1/qa/version.log |\n"
    "\n"
    "## Merge Status\n"
    "merged\n"
    "\n"
    "## Wiki Updates\n"
    "- docs/llm-wiki/lifecycle.md\n"
    "\n"
    "## Human Review\n"
    "ready for operator confirmation\n"
)


def _make_issue(state: str = "Todo") -> Issue:
    return Issue(
        id="iss-life-1",
        identifier="LIFE-1",
        title="lifecycle fixture",
        description=_CONTRACT_CLEAN_BODY,
        priority=2,
        state=state,
        blocked_by=(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _orch(tmp_path: Path) -> Orchestrator:
    o = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    o._workspace_manager = _FakeWorkspaceManager(tmp_path)  # type: ignore[assignment]
    return o


def _seed_running(o: Orchestrator, issue: Issue, tmp_path: Path) -> None:
    (tmp_path / "docs" / issue.identifier / "work").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / issue.identifier / "work" / "notes.md").write_text("ok")
    (tmp_path / "docs" / issue.identifier / "qa").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / issue.identifier / "qa" / "version.log").write_text("ok")
    (tmp_path / "docs" / issue.identifier / "qa" / "security.md").write_text("ok")
    o._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path,
    )


def _install_backend_factory(monkeypatch: pytest.MonkeyPatch) -> list[_FakeBackend]:
    instances: list[_FakeBackend] = []

    def _factory(init: Any) -> _FakeBackend:
        b = _FakeBackend(init_id=len(instances))
        b.calls.append(("factory", {"agent_kind": init.cfg.agent.kind}))
        instances.append(b)
        return b

    monkeypatch.setattr(orch_mod, "build_backend", _factory)
    return instances


def _install_state_walk(monkeypatch: pytest.MonkeyPatch, states: list[str]) -> dict[str, Any]:
    # `_refresh_issue_state` advances the walk one step per turn boundary.
    # `_refresh_issue_full` may be called inside the contract path BEFORE
    # the next state-walk step; it must return the *current* state (the
    # value most recently emitted by `_refresh_issue_state`) without
    # consuming the next walk slot. Otherwise the lifecycle silently
    # skips a phase (Review -> Learn instead of Review -> QA -> Learn).
    counter: dict[str, Any] = {"i": 0, "last": "Todo"}

    def _build(state: str) -> Issue:
        return Issue(
            id="iss-life-1",
            identifier="LIFE-1",
            title="lifecycle fixture",
            description=_CONTRACT_CLEAN_BODY,
            priority=2,
            state=state,
            blocked_by=(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    async def _refresh(self, cfg, issue_id):  # noqa: ANN001
        del self, cfg, issue_id
        idx = counter["i"]
        counter["i"] += 1
        next_state = states[idx] if idx < len(states) else "Done"
        counter["last"] = next_state
        return _build(next_state)

    async def _refresh_full(self, cfg, issue_id):  # noqa: ANN001
        del self, cfg, issue_id
        return _build(counter["last"])

    monkeypatch.setattr(Orchestrator, "_refresh_issue_state", _refresh)
    monkeypatch.setattr(Orchestrator, "_refresh_issue_full", _refresh_full)
    return counter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_full_todo_to_done_pipeline_rebuilds_backend_per_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each phase transition produces a brand-new backend session whose
    first prompt is the freshly rendered stage template."""

    cfg = _make_lifecycle_config(max_turns=12)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running(o, issue, tmp_path)
    instances = _install_backend_factory(monkeypatch)
    # After the first run_turn (Todo), the scripted refresh walks through
    # every remaining canonical state and then exits at Human Review.
    _install_state_walk(monkeypatch, ["In Progress", "Verify", "Learn", "Human Review"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    # 4 active phases => 4 backends total (Todo, In Progress, Verify, Learn).
    assert len(instances) == 4, f"expected 4 backends, got {len(instances)}"

    # Every backend went through factory -> start -> initialize ->
    # start_session before any run_turn (no race / no skipped lifecycle).
    for idx, inst in enumerate(instances):
        names = [name for name, _ in inst.calls]
        assert names[0] == "factory", f"backend {idx} missing factory"
        assert names.index("start") < names.index("initialize"), f"backend {idx} init order"
        assert names.index("initialize") < names.index("start_session"), (
            f"backend {idx} session order"
        )
        assert "run_turn" in names, f"backend {idx} never ran a turn"

    # Each first-turn prompt threads through the per-stage template.
    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 4
    expected_stage_markers = [
        "TODO_BODY state=Todo",
        "INPROGRESS_BODY state=In Progress",
        "VERIFY_BODY state=Verify",
        "LEARN_BODY state=Learn",
    ]
    for prompt, marker in zip(first_prompts, expected_stage_markers):
        assert "BASE id=LIFE-1" in prompt, f"missing shared base header in {marker}"
        assert marker in prompt, f"expected stage marker {marker!r} in prompt"
    # The legacy single-template field is never the source of truth when
    # stage_templates is configured.
    assert "LEGACY" not in "\n\n".join(first_prompts)

    # Session ids are distinct per phase (rebuild really happened).
    session_ids = [inst.session_id for inst in instances]
    assert len(set(session_ids)) == 4, f"expected 4 unique session ids, got {session_ids}"

    # Phase-transition rule: when the backend was just rebuilt, the
    # first run_turn must NOT be flagged as continuation.
    for idx, inst in enumerate(instances):
        runs = [c for c in inst.calls if c[0] == "run_turn"]
        assert len(runs) == 1, f"backend {idx} expected 1 run_turn, got {len(runs)}"
        assert runs[0][1]["is_continuation"] is False, (
            f"backend {idx} first turn must be fresh, not continuation"
        )

    # Workspace `after_run` fires once per backend (i.e. once per phase).
    fake_ws = o._workspace_manager
    assert isinstance(fake_ws, _FakeWorkspaceManager)
    assert len(fake_ws.after_run_paths) == 4
    assert all(p == tmp_path for p in fake_ws.after_run_paths)

    # Clean exit: no zombie running slot, no surprise retry.
    assert issue.id not in o._running
    assert issue.id not in o._retry


def test_lifecycle_stops_each_intermediate_backend_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every intermediate backend is `.stop()`-ed once when its phase ends.

    The final backend is also stopped — by the `finally` block in
    `_run_agent_attempt`. So the total `stop` count equals the number of
    backends built.
    """

    cfg = _make_lifecycle_config(max_turns=12)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running(o, issue, tmp_path)
    instances = _install_backend_factory(monkeypatch)
    _install_state_walk(monkeypatch, ["In Progress", "Verify", "Learn", "Human Review"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    stop_counts = [sum(1 for c in inst.calls if c[0] == "stop") for inst in instances]
    assert stop_counts == [1, 1, 1, 1], stop_counts


def test_lifecycle_renders_verify_template_after_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial walk: confirm Verify gets the correct body when born mid-pipeline."""

    cfg = _make_lifecycle_config(max_turns=12)
    issue = _make_issue(state="In Progress")
    o = _orch(tmp_path)
    _seed_running(o, issue, tmp_path)
    instances = _install_backend_factory(monkeypatch)
    _install_state_walk(monkeypatch, ["Verify", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 2
    prompts = [
        c[1]["initial_prompt"]
        for inst in instances
        for c in inst.calls
        if c[0] == "start_session"
    ]
    assert "INPROGRESS_BODY state=In Progress" in prompts[0]
    assert "VERIFY_BODY state=Verify" in prompts[1]
    assert "INPROGRESS_BODY" not in prompts[1]


def test_lifecycle_done_callback_uses_registered_running_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker cleanup is keyed by the originally registered running id,
    not the tracker id observed on the final refresh — preserving the
    `_on_worker_task_done` identity invariant the project memory tracks.
    """

    cfg = _make_lifecycle_config(max_turns=12)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running(o, issue, tmp_path)
    _install_backend_factory(monkeypatch)

    # Walk the lifecycle; the FINAL refresh returns a *different* tracker
    # id alongside the Done state. If cleanup keyed off that id the
    # original running slot would leak.
    real_walk = ["In Progress", "Verify", "Learn"]
    counter = {"i": 0}

    async def _refresh(self, cfg, issue_id):  # noqa: ANN001
        del self, cfg, issue_id
        idx = counter["i"]
        counter["i"] += 1
        if idx < len(real_walk):
            return Issue(
                id="iss-life-1",
                identifier="LIFE-1",
                title="lifecycle fixture",
                description=None,
                priority=2,
                state=real_walk[idx],
                blocked_by=(),
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        return Issue(
            id="iss-life-1-RENAMED-BY-TRACKER",
            identifier="LIFE-1",
            title="lifecycle fixture",
            description=None,
            priority=2,
            state="Done",
            blocked_by=(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(Orchestrator, "_refresh_issue_state", _refresh)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    # The original key MUST be released — slot accounting must not leak.
    assert issue.id not in o._running
    # And the worker should have exited cleanly (no retry pending).
    assert issue.id not in o._retry
