"""Phase-transition handoff between Kanban states (§16.5 fresh context).

The orchestrator must tear down the backend session and rebuild it with a
freshly rendered first-turn prompt whenever the issue changes state mid
run. Shared knowledge between phases flows only via on-disk artefacts and
the ticket body — never via accumulated backend conversation context.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import symphony.orchestrator.core as core_mod
from symphony.errors import TurnFailed
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator, RunningEntry
from symphony.orchestrator.entries import _IssueDebug
from symphony.orchestrator.run_registry import RunRegistry
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
    SUPPORTED_AGENT_KINDS,
    TrackerConfig,
    TuiConfig,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeBackend:
    """Records every call so tests can assert on counts and arguments."""

    init_id: int
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    process_pid: int | None = None

    @property
    def pid(self) -> int | None:
        """Expose the fake backend's current persistent or per-turn process."""
        return self.process_pid

    async def start(self) -> None:
        self.calls.append(("start", {}))

    async def initialize(self) -> None:
        self.calls.append(("initialize", {}))

    async def start_session(
        self, *, initial_prompt: str, issue_title: str
    ) -> None:
        self.session_id = f"fake-session-{self.init_id}"
        self.calls.append(
            (
                "start_session",
                {
                    "initial_prompt": initial_prompt,
                    "issue_title": issue_title,
                },
            )
        )

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> None:
        self.calls.append(
            (
                "run_turn",
                {"prompt": prompt, "is_continuation": is_continuation},
            )
        )

    async def stop(self) -> None:
        self.calls.append(("stop", {}))


class _FakeWorkspace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.workspace_key = "fake"
        self.created_now = True


class _FakeWorkspaceManager:
    """Minimal stand-in for `WorkspaceManager` used by `_run_agent_attempt`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.before_run_paths: list[Path] = []
        self.after_run_paths: list[Path] = []

    def path_for(self, identifier: str) -> Path:
        del identifier
        return self._path

    async def create_or_reuse(self, identifier: str) -> _FakeWorkspace:
        del identifier
        return _FakeWorkspace(self._path)

    async def before_run(self, path: Path) -> None:
        self.before_run_paths.append(path)
        return None

    async def after_run_best_effort(self, path: Path) -> None:
        self.after_run_paths.append(path)
        return None


# ---------------------------------------------------------------------------
# Config / fixtures
# ---------------------------------------------------------------------------


def _make_config(
    *,
    max_turns: int = 5,
    max_attempts: int = 3,
    active_states: tuple[str, ...] = ("Todo", "In Progress", "Verify", "Learn"),
    prompt_template: str | None = None,
    prompts: PromptConfig | None = None,
    compact_issue_context: bool = False,
) -> ServiceConfig:
    # Prompt template references {{ issue.state }} and {{ is_rewind }} so
    # the rendered first prompt is observably different across phase
    # transitions AND the rewind signal is testable end-to-end.
    template = prompt_template or (
        "issue={{ issue.identifier }} state={{ issue.state }} rewind={{ is_rewind }}"
    )
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=Path("/tmp/ws"),
        tracker=TrackerConfig(
            kind="file",  # avoids `linear_graphql_tool()` in the codex tools list
            endpoint="https://api.linear.app/graphql",
            api_key="tok",
            project_slug="proj",
            active_states=active_states,
            terminal_states=("Done", "Cancelled", "Blocked"),
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=1,
            max_turns=max_turns,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
            max_attempts=max_attempts,
            compact_issue_context=compact_issue_context,
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
        prompts=prompts or PromptConfig(),
        prompt_template=template,
    )


_CONTRACT_CLEAN_BODY = """
## Plan
- build it

## Acceptance Tests
- pytest -q

## Done Signals
- behavior visible

## Implementation
- changed source

## Self-Critique
- checked edge paths

## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| secrets | pass | n/a |

## Review
clean

## QA Evidence
- pytest -q rc=0

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| ac-1 | pytest | pass | MT-1/qa/version.log |

## Merge Status
merged

## Wiki Updates
- docs/llm-wiki/mt-1.md

## Human Review
ready
"""


def _make_issue(state: str = "Todo") -> Issue:
    return Issue(
        id="iss-1",
        identifier="MT-1",
        title="phase transition fixture",
        description=_CONTRACT_CLEAN_BODY,
        priority=2,
        state=state,
        blocked_by=(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _orch(tmp_path: Path) -> Orchestrator:
    state = WorkflowState(Path("/tmp/no.md"))
    o = Orchestrator(state)
    o._workspace_manager = _FakeWorkspaceManager(tmp_path)  # type: ignore[assignment]
    return o


def _seed_running_entry(o: Orchestrator, issue: Issue, tmp_path: Path) -> None:
    (tmp_path / "docs" / issue.identifier / "work").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / issue.identifier / "work" / "notes.md").write_text("ok")
    (tmp_path / "docs" / issue.identifier / "qa").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / issue.identifier / "qa" / "version.log").write_text("ok")
    o._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path,
    )


def _install_fake_backend(monkeypatch: pytest.MonkeyPatch) -> list[_FakeBackend]:
    """Replace `symphony.orchestrator.build_backend` with a recording factory.

    Returns the list every constructed `_FakeBackend` is appended to so a
    test can assert call ordering across the (possibly multiple) backend
    instances built within a single `_run_agent_attempt` call.
    """
    instances: list[_FakeBackend] = []

    def _factory(init: Any) -> _FakeBackend:
        backend = _FakeBackend(init_id=len(instances))
        backend.calls.append(("factory", {"agent_kind": init.cfg.agent.kind}))
        instances.append(backend)
        return backend

    monkeypatch.setattr(core_mod, "build_backend", _factory)
    return instances


def _install_state_sequence(
    monkeypatch: pytest.MonkeyPatch, states: list[str]
) -> None:
    """Walk `_refresh_issue_state` through a scripted state sequence.

    The first call returns an issue in `states[0]`, the second `states[1]`,
    and so on. Once the sequence is exhausted the worker exits via the
    inactive-state branch (we send `"Done"` last to terminate).
    """
    calls = {"i": 0}

    async def _fake_refresh(self, cfg, issue_id):  # noqa: ANN001 - test stub
        del self, cfg, issue_id
        idx = calls["i"]
        calls["i"] += 1
        next_state = states[idx] if idx < len(states) else "Done"
        return Issue(
            id="iss-1",
            identifier="MT-1",
            title="phase transition fixture",
            description=_CONTRACT_CLEAN_BODY,
            priority=2,
            state=next_state,
            blocked_by=(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(Orchestrator, "_refresh_issue_state", _fake_refresh)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_phase_transition_rebuilds_backend_with_fresh_first_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    # Turn 1 finishes in "Todo"; refresh moves to "In Progress" → triggers
    # phase transition before turn 2 runs. After turn 2 the second refresh
    # returns "Done" so the worker exits naturally.
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    fake_ws = o._workspace_manager
    assert isinstance(fake_ws, _FakeWorkspaceManager)
    assert fake_ws.after_run_paths == [tmp_path, tmp_path]

    # Two backends total: one for Todo, one for In Progress.
    assert len(instances) == 2
    # Original backend was stopped exactly once mid-loop (plus once more in
    # the finally block that targets the LATEST client). The original
    # instance therefore sees exactly one stop.
    original_stops = [c for c in instances[0].calls if c[0] == "stop"]
    assert len(original_stops) == 1

    # Both backends must have been driven through start → initialize →
    # start_session before any run_turn fires on them.
    for inst in instances:
        names = [c[0] for c in inst.calls]
        assert names.index("start") < names.index("initialize") < names.index(
            "start_session"
        )

    # Two distinct first-turn prompts captured — one per phase.
    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 2
    assert first_prompts[0] != first_prompts[1]
    # The freshly-rendered prompt reflects the new state.
    assert "state=In Progress" in first_prompts[1]
    assert "state=Todo" in first_prompts[0]

    # Post-transition run_turn must NOT be flagged as a continuation —
    # the backend has no prior context, this is its true first turn.
    second_run = [c for c in instances[1].calls if c[0] == "run_turn"]
    # Exactly one turn per backend in the scripted state sequence
    # (Todo on first backend, In Progress → Done on second). A future
    # change that accidentally double-calls run_turn on the new backend
    # would still pass the [0] checks below — pin the count.
    assert len(second_run) == 1, f"expected one run_turn on second backend, got {len(second_run)}"
    assert second_run[0][1]["is_continuation"] is False
    # And the prompt sent on that run_turn equals the freshly rendered
    # first-turn prompt (not a build_continuation_prompt body).
    assert second_run[0][1]["prompt"] == first_prompts[1]


def test_before_run_hook_reasserts_workspace_invariants_before_each_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="In Progress")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    fake_ws = o._workspace_manager
    assert isinstance(fake_ws, _FakeWorkspaceManager)
    assert fake_ws.before_run_paths == [tmp_path, tmp_path]
    assert fake_ws.after_run_paths == [tmp_path, tmp_path]


@pytest.mark.parametrize("agent_kind", ["claude", "gemini", "pi"])
def test_non_codex_explore_to_plan_rebuilds_fresh_backend_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_kind: str
) -> None:
    base_cfg = _make_config(max_turns=5)
    cfg = replace(
        base_cfg,
        agent=replace(base_cfg.agent, kind=agent_kind),
        tracker=replace(
            base_cfg.tracker,
            active_states=("Todo", "Explore", "Plan", "In Progress", "Review"),
        ),
    )
    issue = _make_issue(state="Explore")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Plan", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert [inst.calls[0] for inst in instances] == [
        ("factory", {"agent_kind": agent_kind}),
        ("factory", {"agent_kind": agent_kind}),
    ]
    first_sessions = [
        inst.session_id
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert first_sessions == ["fake-session-0", "fake-session-1"]
    assert first_sessions[0] != first_sessions[1]
    second_run = [c for c in instances[1].calls if c[0] == "run_turn"]
    assert len(second_run) == 1
    assert second_run[0][1]["is_continuation"] is False


@pytest.mark.parametrize("agent_kind", sorted(SUPPORTED_AGENT_KINDS))
def test_run_agent_attempt_uses_ticket_agent_kind_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_kind: str
) -> None:
    cfg = _make_config(max_turns=5)
    issue = Issue(
        id="iss-1",
        identifier="MT-1",
        title="ticket-level backend",
        description=None,
        priority=2,
        state="Todo",
        agent_kind=agent_kind,
        blocked_by=(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert cfg.agent.kind == "codex"
    assert instances[0].calls[0] == ("factory", {"agent_kind": agent_kind})


def test_phase_transition_uses_stage_specific_prompt_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(
        max_turns=5,
        prompt_template="LEGACY {{ issue.state }}",
        prompts=PromptConfig(
            base_template="BASE {{ issue.identifier }}",
            stage_templates={
                "todo": "TODO ONLY {{ issue.state }}",
                "in progress": "IMPLEMENT ONLY {{ issue.state }}",
            },
        ),
    )
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 2
    assert "BASE MT-1" in first_prompts[0]
    assert "TODO ONLY Todo" in first_prompts[0]
    assert "IMPLEMENT ONLY" not in first_prompts[0]
    assert "BASE MT-1" in first_prompts[1]
    assert "IMPLEMENT ONLY In Progress" in first_prompts[1]
    assert "TODO ONLY" not in first_prompts[1]
    assert "LEGACY" not in "\n".join(first_prompts)


def test_phase_transition_stops_new_backend_when_rebuild_initialize_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    async def _initialize(self_inst: _FakeBackend) -> None:
        self_inst.calls.append(("initialize", {}))
        if self_inst.init_id == 1:
            raise RuntimeError("second backend init failed")

    monkeypatch.setattr(_FakeBackend, "initialize", _initialize)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 2
    second_calls = [name for name, _ in instances[1].calls]
    assert second_calls == ["factory", "start", "initialize", "stop"]


def test_phase_transition_stops_new_backend_when_rebuild_start_session_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PR #21 regression: the `_rebuild_backend_for_phase` try/except must
    # wrap `start_session()` too, not only `initialize()`. If a later
    # refactor moves `start_session` outside the try block, the new backend
    # leaks and this test fails.
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    async def _start_session(
        self_inst: _FakeBackend, *, initial_prompt: str, issue_title: str
    ) -> None:
        self_inst.calls.append(
            (
                "start_session",
                {
                    "initial_prompt": initial_prompt,
                    "issue_title": issue_title,
                },
            )
        )
        if self_inst.init_id == 1:
            raise RuntimeError("second backend start_session failed")

    monkeypatch.setattr(_FakeBackend, "start_session", _start_session)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 2
    second_calls = [name for name, _ in instances[1].calls]
    assert second_calls == [
        "factory",
        "start",
        "initialize",
        "start_session",
        "stop",
    ]


def test_phase_transition_stop_failure_retains_old_backend_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=2)
    issue = _make_issue(state="In Progress")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    o._running[issue.id].agent_pgid = 11111
    old_client = _FakeBackend(init_id=0, process_pid=11111)
    replacements = _install_fake_backend(monkeypatch)

    async def _stop_failed() -> None:
        old_client.calls.append(("stop", {}))
        raise RuntimeError("old backend stop failed")

    monkeypatch.setattr(old_client, "stop", _stop_failed)

    async def _exercise() -> None:
        with pytest.raises(RuntimeError, match="old backend stop failed"):
            await o._rebuild_backend_for_phase(
                issue=issue,
                running_issue_id=issue.id,
                cfg=cfg,
                workspace_path=tmp_path,
                attempt=None,
                doc_language="en",
                old_client=old_client,
                is_rewind=False,
                turn_number=1,
            )

    asyncio.run(_exercise())

    assert o._running[issue.id].agent_pgid == 11111
    assert replacements == []


def test_phase_stop_failure_stays_unconfirmed_after_idempotent_final_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=2)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    registry = RunRegistry(tmp_path / "state.db")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    o._run_registry = registry
    o._running[issue.id].run_id = run_id
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])
    stop_state = {"closed": False}

    async def _start(self_inst: _FakeBackend) -> None:
        self_inst.process_pid = 11111
        self_inst.calls.append(("start", {}))

    async def _stop_once_then_noop(self_inst: _FakeBackend) -> None:
        self_inst.calls.append(("stop", {}))
        if stop_state["closed"]:
            return
        stop_state["closed"] = True
        raise RuntimeError("old backend stop failed after closing")

    async def _keep_entry(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr(_FakeBackend, "start", _start)
    monkeypatch.setattr(_FakeBackend, "stop", _stop_once_then_noop)
    monkeypatch.setattr(o, "_on_worker_exit", _keep_entry)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 1
    assert [name for name, _ in instances[0].calls].count("stop") == 2
    assert o._running[issue.id].agent_pgid == 11111
    assert registry.get_run(run_id).backend_agent_pid == 11111


def test_replacement_stop_failure_stays_unconfirmed_after_old_final_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=2)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    registry = RunRegistry(tmp_path / "state.db")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    o._run_registry = registry
    o._running[issue.id].run_id = run_id
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])
    replacement_closed = {"value": False}

    async def _start(self_inst: _FakeBackend) -> None:
        self_inst.process_pid = 11111 if self_inst.init_id == 0 else 22222
        self_inst.calls.append(("start", {}))

    async def _initialize(self_inst: _FakeBackend) -> None:
        self_inst.calls.append(("initialize", {}))
        if self_inst.init_id == 1:
            raise RuntimeError("replacement initialization failed")

    async def _stop(self_inst: _FakeBackend) -> None:
        self_inst.calls.append(("stop", {}))
        if self_inst.init_id == 0:
            self_inst.process_pid = None
            return
        replacement_closed["value"] = True
        raise RuntimeError("replacement stop failed after closing")

    async def _keep_entry(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr(_FakeBackend, "start", _start)
    monkeypatch.setattr(_FakeBackend, "initialize", _initialize)
    monkeypatch.setattr(_FakeBackend, "stop", _stop)
    monkeypatch.setattr(o, "_on_worker_exit", _keep_entry)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 2
    assert [name for name, _ in instances[0].calls].count("stop") == 2
    assert replacement_closed["value"] is True
    assert not [call for call in instances[1].calls if call[0] == "run_turn"]
    assert o._running[issue.id].agent_pgid == 22222
    assert registry.get_run(run_id).backend_agent_pid == 22222


def test_same_phase_does_not_restart_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=3)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    # Stay in Todo across turns 1 → 2, then exit by going inactive.
    _install_state_sequence(monkeypatch, ["Todo", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    # Exactly one backend ever built when the state is unchanged.
    assert len(instances) == 1
    inst = instances[0]
    # Two run_turn calls: turn 1 is_continuation=False, turn 2 True.
    run_turns = [c for c in inst.calls if c[0] == "run_turn"]
    assert len(run_turns) == 2
    assert run_turns[0][1]["is_continuation"] is False
    assert run_turns[1][1]["is_continuation"] is True
    # Only the single `finally`-block stop is observed.
    stops = [c for c in inst.calls if c[0] == "stop"]
    assert len(stops) == 1


def test_next_turn_clears_stale_per_turn_agent_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=3)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    observed_pids: list[int | None] = []

    async def _capture_pid(self_inst, *, prompt, is_continuation):  # noqa: ANN001
        self_inst.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        running = o._running[issue.id]
        observed_pids.append(running.agent_pgid)
        if len(observed_pids) == 1:
            running.agent_pgid = 11111

    monkeypatch.setattr(_FakeBackend, "run_turn", _capture_pid)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Todo", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert observed_pids == [None, None]


def test_completed_per_turn_pid_is_cleared_before_after_run_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=1)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    after_run_entered = asyncio.Event()
    release_after_run = asyncio.Event()

    async def _record_completed_pid(
        self_inst, *, prompt, is_continuation  # noqa: ANN001
    ) -> None:
        self_inst.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        o._running[issue.id].agent_pgid = 11111

    async def _block_after_run(path: Path) -> None:
        del path
        after_run_entered.set()
        await release_after_run.wait()

    monkeypatch.setattr(_FakeBackend, "run_turn", _record_completed_pid)
    fake_ws = o._workspace_manager
    assert isinstance(fake_ws, _FakeWorkspaceManager)
    monkeypatch.setattr(fake_ws, "after_run_best_effort", _block_after_run)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Done"])

    async def _exercise() -> None:
        worker = asyncio.create_task(
            o._run_agent_attempt(issue, attempt=None, cfg=cfg)
        )
        await asyncio.wait_for(after_run_entered.wait(), timeout=1)
        try:
            assert o._running[issue.id].agent_pgid is None
        finally:
            release_after_run.set()
        await worker

    asyncio.run(_exercise())


def test_completed_per_turn_pid_is_cleared_from_run_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=1)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    registry = RunRegistry(tmp_path / "state.db")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="claude",
    )
    assert run_id
    o._run_registry = registry
    o._running[issue.id].run_id = run_id
    after_run_entered = asyncio.Event()
    release_after_run = asyncio.Event()

    async def _publish_pid(
        self_inst: _FakeBackend, *, prompt: str, is_continuation: bool
    ) -> None:
        self_inst.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        await o._on_codex_event(issue.id, {"event": "turn_started", "agent_pid": 11111})

    async def _block_after_run(path: Path) -> None:
        del path
        after_run_entered.set()
        await release_after_run.wait()

    async def _keep_entry(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr(_FakeBackend, "run_turn", _publish_pid)
    monkeypatch.setattr(o, "_on_worker_exit", _keep_entry)
    fake_ws = o._workspace_manager
    assert isinstance(fake_ws, _FakeWorkspaceManager)
    monkeypatch.setattr(fake_ws, "after_run_best_effort", _block_after_run)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Done"])

    async def _exercise() -> None:
        worker = asyncio.create_task(o._run_agent_attempt(issue, attempt=None, cfg=cfg))
        await asyncio.wait_for(after_run_entered.wait(), timeout=1)
        try:
            assert o._running[issue.id].agent_pgid is None
            assert registry.get_run(run_id).backend_agent_pid is None
        finally:
            release_after_run.set()
        await worker

    asyncio.run(_exercise())


def test_failed_per_turn_clears_pid_before_failed_final_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=1)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    registry = RunRegistry(tmp_path / "state.db")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="claude",
    )
    assert run_id
    o._run_registry = registry
    o._running[issue.id].run_id = run_id

    async def _publish_pid_then_fail(
        self_inst: _FakeBackend, *, prompt: str, is_continuation: bool
    ) -> None:
        self_inst.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        await o._on_codex_event(issue.id, {"event": "turn_started", "agent_pid": 22222})
        raise TurnFailed("turn failed")

    async def _stop_failed(self_inst: _FakeBackend) -> None:
        self_inst.calls.append(("stop", {}))
        raise RuntimeError("cleanup failed")

    async def _keep_entry(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr(_FakeBackend, "run_turn", _publish_pid_then_fail)
    monkeypatch.setattr(_FakeBackend, "stop", _stop_failed)
    monkeypatch.setattr(o, "_on_worker_exit", _keep_entry)
    _install_fake_backend(monkeypatch)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert o._running[issue.id].agent_pgid is None
    assert registry.get_run(run_id).backend_agent_pid is None


def test_start_failure_records_late_pid_when_cleanup_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=1)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    registry = RunRegistry(tmp_path / "state.db")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    o._run_registry = registry
    o._running[issue.id].run_id = run_id

    async def _publish_pid_then_fail(self_inst: _FakeBackend) -> None:
        self_inst.process_pid = 33333
        self_inst.calls.append(("start", {}))
        raise RuntimeError("late start failure")

    async def _stop_failed(self_inst: _FakeBackend) -> None:
        self_inst.calls.append(("stop", {}))
        raise RuntimeError("cleanup failed")

    async def _keep_entry(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr(_FakeBackend, "start", _publish_pid_then_fail)
    monkeypatch.setattr(_FakeBackend, "stop", _stop_failed)
    monkeypatch.setattr(o, "_on_worker_exit", _keep_entry)
    _install_fake_backend(monkeypatch)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert o._running[issue.id].agent_pgid == 33333
    assert registry.get_run(run_id).backend_agent_pid == 33333


def test_persistent_backend_pid_is_registered_before_each_initialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=2)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    observed_initialize_pids: list[int | None] = []
    explicit_heartbeat_pids: list[int] = []

    async def _start(self_inst: _FakeBackend) -> None:
        self_inst.process_pid = (11111, 22222)[self_inst.init_id]
        self_inst.calls.append(("start", {}))

    async def _initialize(self_inst: _FakeBackend) -> None:
        self_inst.calls.append(("initialize", {}))
        observed_initialize_pids.append(o._running[issue.id].agent_pgid)

    def _heartbeat(
        issue_id: str,
        entry: RunningEntry,
        *,
        progress: datetime | None = None,
        backend_agent_pid: int | None = None,
    ) -> bool:
        del issue_id, entry, progress
        if backend_agent_pid is not None:
            explicit_heartbeat_pids.append(backend_agent_pid)
        return True

    monkeypatch.setattr(_FakeBackend, "start", _start)
    monkeypatch.setattr(_FakeBackend, "initialize", _initialize)
    monkeypatch.setattr(o, "_heartbeat_run_lease", _heartbeat)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert observed_initialize_pids == [11111, 22222]
    assert explicit_heartbeat_pids == [
        11111,  # initial start finally
        11111,  # first run_turn boundary
        11111,  # first run_turn finally
        22222,  # rebuilt start finally
        22222,  # second run_turn boundary
        22222,  # second run_turn finally
    ]


def test_prompt_turn_budget_continues_across_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_cfg = _make_config(
        max_turns=5,
        prompt_template="budget={{ turn_number }}/{{ max_turns }}",
    )
    cfg = replace(base_cfg, agent=replace(base_cfg.agent, max_total_turns=60))
    issue = _make_issue(state="In Progress")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    o._issue_debug[issue.id] = _IssueDebug(completed_turn_count=6)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=2, cfg=cfg))

    start_prompt = next(
        details["initial_prompt"]
        for name, details in instances[0].calls
        if name == "start_session"
    )
    run_turns = [details for name, details in instances[0].calls if name == "run_turn"]
    assert "budget=7/60" in start_prompt
    assert run_turns[0]["prompt"] == start_prompt
    assert "turn 8 of up to 60" in run_turns[1]["prompt"]


def test_phase_rebuild_prompt_keeps_lifetime_turn_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_cfg = _make_config(
        max_turns=5,
        prompt_template="budget={{ turn_number }}/{{ max_turns }}",
    )
    cfg = replace(base_cfg, agent=replace(base_cfg.agent, max_total_turns=60))
    issue = _make_issue(state="Verify")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    o._issue_debug[issue.id] = _IssueDebug(completed_turn_count=6)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=2, cfg=cfg))

    prompts = [
        details["initial_prompt"]
        for instance in instances
        for name, details in instance.calls
        if name == "start_session"
    ]
    assert "budget=7/60" in prompts[0]
    assert "budget=8/60" in prompts[1]


def test_worker_cleanup_uses_registered_running_issue_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup must pop the original running slot even if a refreshed issue
    object carries a different tracker id.

    The TUI symptom is a card stuck in retrying with
    `worker_task_finished_without_cleanup`: the worker task completed, but
    `_on_worker_exit` was called with a key that did not match `_running`.
    """
    cfg = _make_config(max_turns=2)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    o._loop = asyncio.new_event_loop()
    try:
        _seed_running_entry(o, issue, tmp_path)
        _install_fake_backend(monkeypatch)

        async def _refresh_with_different_id(self, cfg, issue_id):  # noqa: ANN001
            del self, cfg, issue_id
            return Issue(
                id="tracker-id-after-refresh",
                identifier="MT-1",
                title="phase transition fixture",
                description=None,
                priority=2,
                state="Done",
                blocked_by=(),
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

        monkeypatch.setattr(
            Orchestrator, "_refresh_issue_state", _refresh_with_different_id
        )

        o._loop.run_until_complete(o._run_agent_attempt(issue, attempt=None, cfg=cfg))
    finally:
        for retry in list(o._retry.values()):
            retry.timer_handle.cancel()
        o._loop.close()

    assert issue.id not in o._running
    assert issue.id in o._retry
    assert o._retry[issue.id].error is None


def test_phase_transition_resets_session_id_on_running_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    # Pre-fill the bookkeeping fields so we can assert they got cleared.
    entry = o._running[issue.id]
    entry.thread_id = "thr-old"
    entry.session_id = "sess-old"
    entry.turn_id = "turn-old"

    captured: dict[str, Any] = {}

    async def _capture_run_turn(self_inst, *, prompt, is_continuation):  # noqa: ANN001
        # Mirror the original `_FakeBackend.run_turn` accounting so any
        # follow-up tests that monkeypatch + assert on `inst.calls` still
        # see the call. Otherwise this stub silently drops the record and
        # makes call-count assertions fragile to test ordering.
        self_inst.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        # On the second backend's first turn, snapshot the bookkeeping
        # fields BEFORE any further code runs — they must already be None.
        if self_inst.init_id == 1 and "snapshot" not in captured:
            running = o._running[issue.id]
            captured["snapshot"] = {
                "thread_id": running.thread_id,
                "session_id": running.session_id,
                "turn_id": running.turn_id,
            }

    # Re-bind run_turn on the fake to take a snapshot. Patch the method
    # on the class so each new instance picks it up.
    monkeypatch.setattr(_FakeBackend, "run_turn", _capture_run_turn)

    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert captured.get("snapshot") == {
        "thread_id": None,
        "session_id": None,
        "turn_id": None,
    }


def test_phase_transition_resets_token_high_water_marks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """High-water marks on `RunningEntry.last_reported_*_tokens` MUST reset
    when the backend is rebuilt. Otherwise `_apply_token_totals` computes
    `max(new - old_high, 0) = 0` against the new session's absolute totals
    and silently drops every token from the new phase until cumulative
    reporting overtakes the old mark. Cumulative `codex_*_tokens` are
    explicitly NOT reset — those are per-ticket lifetime counters."""
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    entry = o._running[issue.id]
    entry.last_reported_input_tokens = 5_000
    entry.last_reported_output_tokens = 3_000
    entry.last_reported_total_tokens = 8_000
    entry.codex_input_tokens = 5_000
    entry.codex_output_tokens = 3_000
    entry.codex_total_tokens = 8_000

    captured: dict[str, Any] = {}

    async def _snapshot_on_second(self_inst, *, prompt, is_continuation):  # noqa: ANN001
        self_inst.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        if self_inst.init_id == 1 and "snapshot" not in captured:
            running = o._running[issue.id]
            captured["snapshot"] = {
                "last_reported_input_tokens": running.last_reported_input_tokens,
                "last_reported_output_tokens": running.last_reported_output_tokens,
                "last_reported_total_tokens": running.last_reported_total_tokens,
                "codex_input_tokens": running.codex_input_tokens,
                "codex_output_tokens": running.codex_output_tokens,
                "codex_total_tokens": running.codex_total_tokens,
            }

    monkeypatch.setattr(_FakeBackend, "run_turn", _snapshot_on_second)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    snap = captured.get("snapshot")
    assert snap is not None, "second backend must have run a turn"
    # High-water marks reset so the new session's first usage report is
    # not compared against the old session's cumulative high.
    assert snap["last_reported_input_tokens"] == 0
    assert snap["last_reported_output_tokens"] == 0
    assert snap["last_reported_total_tokens"] == 0
    # Per-ticket cumulative counters intentionally preserved.
    assert snap["codex_input_tokens"] == 5_000
    assert snap["codex_output_tokens"] == 3_000
    assert snap["codex_total_tokens"] == 8_000


# ---------------------------------------------------------------------------
# Rewind detection - Verify/Learn -> In Progress
# ---------------------------------------------------------------------------


def test_is_rewind_transition_pure_function() -> None:
    """Predicate covers the canonical rewind paths and rejects the rest."""
    from symphony.orchestrator import _is_rewind_transition

    assert _is_rewind_transition("verify", "in progress") is True
    assert _is_rewind_transition("learn", "in progress") is True
    # Forward transitions are NEVER rewinds.
    assert _is_rewind_transition("todo", "in progress") is False
    assert _is_rewind_transition("in progress", "verify") is False
    assert _is_rewind_transition("verify", "learn") is False
    # Same-state self-loops are not transitions at all.
    assert _is_rewind_transition("in progress", "in progress") is False
    # Backward jumps to states OTHER than In Progress are out of scope.
    assert _is_rewind_transition("verify", "todo") is False
    assert _is_rewind_transition("learn", "verify") is False


def test_is_rewind_transition_uses_configured_active_state_order() -> None:
    """AF-13 — rewind meaning comes from the configured pipeline order."""
    from symphony.orchestrator import _is_rewind_transition

    assert _is_rewind_transition(
        "QA", "In Progress", ("Todo", "In Progress", "QA")
    )
    assert _is_rewind_transition("검수", "진행", ("대기", "진행", "검수"))
    assert not _is_rewind_transition(
        "In Progress", "QA", ("Todo", "In Progress", "QA")
    )


@pytest.mark.parametrize(
    ("active_states", "later", "earlier"),
    [
        (("In Progress", "QA"), "QA", "In Progress"),
        (("진행", "검수"), "검수", "진행"),
    ],
)
def test_custom_pipeline_rewinds_increment_budget_and_block_at_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_states: tuple[str, ...],
    later: str,
    earlier: str,
) -> None:
    """AF-13 — custom and non-English rewinds cannot evade the cap."""
    cfg = _make_config(
        max_turns=5,
        max_attempts=1,
        active_states=active_states,
    )
    issue = _make_issue(state=later)
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, [earlier, later, earlier, "Done"])
    updates: list[tuple[str, str]] = []

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(
            lambda _cfg, issue_arg, target: updates.append(
                (issue_arg.state, target)
            )
        ),
    )

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert updates == [(earlier, "Blocked")]
    assert o._issue_debug[issue.id].rewind_count == 2


def test_verify_rewind_renders_is_rewind_in_first_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the state goes Verify -> In Progress mid-worker, the rebuilt
    backend's first-turn prompt must carry `is_rewind=True` so WORKFLOW
    templates can branch the retry preamble."""
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Verify")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)

    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 2, "rewind must trigger a backend rebuild"
    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 2
    # Initial prompt was rendered before any phase transition was known.
    assert "rewind=False" in first_prompts[0]
    # Post-rewind prompt carries the True signal — the WORKFLOW template
    # can now branch its retry preamble on it.
    assert "rewind=True" in first_prompts[1]
    assert "state=In Progress" in first_prompts[1]


def test_forward_transition_does_not_set_is_rewind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Todo -> In Progress (forward) must NOT flip is_rewind."""
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)

    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    first_prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(first_prompts) == 2
    assert "rewind=False" in first_prompts[0]
    assert "rewind=False" in first_prompts[1]


def test_contract_validation_uses_fresh_ticket_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(
        max_turns=4,
        active_states=("In Progress", "Verify"),
    )
    issue = _make_issue(state="In Progress")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    _install_fake_backend(monkeypatch)
    good_body = _CONTRACT_CLEAN_BODY
    refreshes = [
        replace(issue, state="Verify", description=""),
        replace(issue, state="Verify", description=good_body),
        replace(issue, state="Done", description=good_body),
    ]

    async def _refresh(_self, _cfg, _issue_id):  # noqa: ANN001
        return refreshes.pop(0)

    notes: list[tuple[str, str]] = []
    updates: list[str] = []

    # Both the minimal (state-only) and full-body refresh helpers pull
    # from the same scripted sequence. The orchestrator now uses
    # `_refresh_issue_full` for the contract preflight specifically (so
    # description is hydrated against the live tracker body), and
    # `_refresh_issue_state` for the cheap post-turn state poll.
    monkeypatch.setattr(Orchestrator, "_refresh_issue_state", _refresh)
    monkeypatch.setattr(Orchestrator, "_refresh_issue_full", _refresh)
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_append_note",
        staticmethod(lambda _cfg, _issue, heading, body: notes.append((heading, body))),
    )
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(lambda _cfg, _issue, target: updates.append(target)),
    )

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert notes == []
    assert updates == []


def test_contract_validation_uses_raw_ticket_body_when_prompt_compacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(
        max_turns=4,
        active_states=("In Progress", "Verify"),
        compact_issue_context=True,
        prompt_template=(
            "state={{ issue.state }} rewind={{ is_rewind }}\n"
            "{{ issue.description }}"
        ),
    )
    compact_only_body = """\
## Acceptance Criteria

- Compact prompt keeps this scope.

## Contract Failure
Prior failure context for the prompt only.
"""
    issue = _make_issue(state="In Progress")
    issue = replace(issue, description=compact_only_body)
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    full_body = _CONTRACT_CLEAN_BODY
    refreshes = [
        replace(issue, state="Verify", description=compact_only_body),
        replace(issue, state="Verify", description=full_body),
        replace(issue, state="Done", description=full_body),
    ]

    async def _refresh(_self, _cfg, _issue_id):  # noqa: ANN001
        return refreshes.pop(0)

    notes: list[tuple[str, str]] = []
    updates: list[str] = []

    monkeypatch.setattr(Orchestrator, "_refresh_issue_state", _refresh)
    monkeypatch.setattr(Orchestrator, "_refresh_issue_full", _refresh)
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_append_note",
        staticmethod(lambda _cfg, _issue, heading, body: notes.append((heading, body))),
    )
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(lambda _cfg, _issue, target: updates.append(target)),
    )

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert len(prompts) == 2
    assert "Compact prompt keeps this scope." in prompts[0]
    assert "## Plan" not in prompts[0]
    assert notes == []
    assert updates == []


def test_contract_failure_rebuilds_at_producing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(
        max_turns=4,
        active_states=("In Progress", "Verify"),
    )
    issue = _make_issue(state="In Progress")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    stale_verify = replace(issue, state="Verify", description="")
    refreshes = [
        stale_verify,  # after the In Progress turn moves forward
        stale_verify,  # contract preflight still sees the failing body
        stale_verify,  # tracker read after update can be stale on remote trackers
        replace(issue, state="Done", description=""),
    ]

    async def _refresh(_self, _cfg, _issue_id):  # noqa: ANN001
        return refreshes.pop(0)

    updates: list[str] = []

    # Same dual monkeypatch as the sibling test — see its comment.
    monkeypatch.setattr(Orchestrator, "_refresh_issue_state", _refresh)
    monkeypatch.setattr(Orchestrator, "_refresh_issue_full", _refresh)
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_append_note",
        staticmethod(lambda _cfg, _issue, _heading, _body: None),
    )
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(lambda _cfg, _issue, target: updates.append(target)),
    )

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    prompts = [
        call[1]["initial_prompt"]
        for inst in instances
        for call in inst.calls
        if call[0] == "start_session"
    ]
    assert updates == ["In Progress"]
    assert "state=In Progress" in prompts[1]
    assert "rewind=True" in prompts[1]


def test_rewind_budget_blocks_fourth_rewind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=12, max_attempts=3)
    issue = _make_issue(state="Verify")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(
        monkeypatch,
            [
                "In Progress",  # rewind 1
                "Verify",
                "In Progress",  # rewind 2
                "Verify",
                "In Progress",  # rewind 3
                "Verify",
                "In Progress",  # rewind 4 => Blocked, no rebuild
                "Done",
        ],
    )
    updates: list[tuple[str, str]] = []

    def _capture_update(_cfg, issue_arg, target_state):  # noqa: ANN001
        updates.append((issue_arg.state, target_state))

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(_capture_update),
    )

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert updates == [("In Progress", "Blocked")]
    assert o._issue_debug[issue.id].rewind_count == 4
    assert issue.id not in o._running
    assert issue.id not in o._retry
    # Initial Review plus six allowed phase rebuilds. The fourth rewind is
    # blocked before a new In Progress backend is created.
    assert len(instances) == 7


def test_rewind_budget_zero_disables_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=10, max_attempts=0)
    issue = _make_issue(state="Verify")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(
        monkeypatch,
        ["In Progress", "Verify", "In Progress", "Verify", "In Progress", "Done"],
    )
    updates: list[tuple[str, str]] = []

    def _capture_update(_cfg, issue_arg, target_state):  # noqa: ANN001
        updates.append((issue_arg.state, target_state))

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(_capture_update),
    )

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert updates == []
    assert o._issue_debug[issue.id].rewind_count == 3


def test_run_agent_attempt_handles_orphaned_running_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker survives a missing `_running` entry instead of raising KeyError.

    Regression for the OLV-002 cascade where `self._running[running_issue_id]`
    direct-subscript access would raise `KeyError('OLV-002')` if a race popped
    the entry between dispatch and the first-await completion. The fix routes
    a missing entry to the `orphaned` outcome so the outer finally pops
    cleanly (None pop) and no `worker_task_finished_without_cleanup` cascade
    fires.
    """
    cfg = _make_config(max_turns=2)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    # Deliberately do NOT seed a running entry — simulate the race where
    # something popped it before `_run_agent_attempt` resumed from its
    # first await.
    instances = _install_fake_backend(monkeypatch)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    # Orphan path returns before `build_backend` runs.
    assert len(instances) == 0
    assert issue.id not in o._running


def test_dispatch_registers_running_entry_before_eager_worker_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python 3.12 eager tasks may run the worker before `_dispatch` returns."""

    eager_factory = getattr(asyncio, "eager_task_factory", None)
    if eager_factory is None:
        pytest.skip("asyncio.eager_task_factory requires Python 3.12+")

    cfg = _make_config(max_turns=1)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Done"])

    loop = asyncio.new_event_loop()
    loop.set_task_factory(eager_factory)
    o._loop = loop
    try:
        async def _drive_dispatch() -> None:
            o._dispatch(issue, cfg, attempt=None)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        loop.run_until_complete(_drive_dispatch())
    finally:
        for retry in list(o._retry.values()):
            retry.timer_handle.cancel()
        loop.close()

    assert issue.id not in o._running
    assert issue.id in o._retry
    assert o._retry[issue.id].error is None


def test_done_callback_ignores_stale_task_for_replaced_running_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `worker_task.add_done_callback` from a previously-finished worker
    must not pop a freshly-dispatched entry that happens to share the same
    issue id.

    Why: `_on_worker_exit` yields once at `await self._notify_observers()`.
    The continuation retry timer is only 1s away (`CONTINUATION_RETRY_DELAY_MS`),
    so a race exists where a new `_dispatch` installs a fresh entry under
    the same key BEFORE the original worker's task object reaches `done`.
    When that stale task's callback finally fires, it must verify the
    registered entry still belongs to it. Symptom is `state=Review,
    runtime=retrying, error=worker_task_finished_without_cleanup` because
    the stale callback ejects the live entry.
    """

    o = _orch(tmp_path)
    issue = _make_issue(state="Review")

    exit_calls: list[tuple[str, str, str | None]] = []

    async def _track_exit(self_inst, issue_id, reason, error):  # noqa: ANN001
        del self_inst
        exit_calls.append((issue_id, reason, error))

    monkeypatch.setattr(Orchestrator, "_on_worker_exit", _track_exit)

    loop = asyncio.new_event_loop()
    o._loop = loop
    try:
        # Build a real done-but-not-cancelled task to mimic a worker that
        # ran its `finally` cleanly. Its entry was already popped by the
        # legitimate cleanup path.
        async def _ok() -> None:
            return None

        task1 = loop.create_task(_ok())
        loop.run_until_complete(task1)
        assert task1.done() and not task1.cancelled() and task1.exception() is None

        # Race: a fresh dispatch installs entry2 under the same key. We use
        # a never-running placeholder task so we control its lifecycle.
        async def _pending() -> None:
            await asyncio.sleep(3600)

        async def _race_window() -> None:
            entry2 = RunningEntry(
                issue=issue,
                started_at=datetime.now(timezone.utc),
                retry_attempt=1,
                worker_task=loop.create_task(_pending()),
                workspace_path=tmp_path,
            )
            o._running[issue.id] = entry2
            try:
                # Stale callback for the already-finished task1 fires from
                # inside a running loop, exactly mirroring the production
                # `add_done_callback` invocation context.
                o._on_worker_task_done(issue.id, task1)
                # Drain any task the callback may have queued so a buggy
                # implementation has a chance to clobber `_running`.
                await asyncio.sleep(0)
                await asyncio.sleep(0)

                assert o._running.get(issue.id) is entry2
                assert exit_calls == [], (
                    "stale done-callback wrongly fired _on_worker_exit: "
                    f"{exit_calls!r}"
                )
            finally:
                entry2.worker_task.cancel()
                await asyncio.gather(entry2.worker_task, return_exceptions=True)

        loop.run_until_complete(_race_window())
    finally:
        for retry in list(o._retry.values()):
            retry.timer_handle.cancel()
        loop.close()


def test_done_callback_ignores_task_already_in_exit_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker finishing while its `finally` cleanup is already underway must
    not be reclassified as `worker_task_finished_without_cleanup`.

    Cancellation can land on `_run_agent_attempt` while it is awaiting exit
    cleanup. The cleanup path is legitimate; the done callback is only a
    fallback for tasks whose coroutine never reached that path at all.
    """

    o = _orch(tmp_path)
    issue = _make_issue(state="Review")

    exit_calls: list[tuple[str, str, str | None]] = []

    async def _track_exit(self_inst, issue_id, reason, error):  # noqa: ANN001
        del self_inst
        exit_calls.append((issue_id, reason, error))

    monkeypatch.setattr(Orchestrator, "_on_worker_exit", _track_exit)

    loop = asyncio.new_event_loop()
    o._loop = loop
    try:
        async def _ok() -> None:
            return None

        task = loop.create_task(_ok())
        loop.run_until_complete(task)
        assert task.done() and not task.cancelled() and task.exception() is None

        entry = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=1,
            worker_task=task,
            workspace_path=tmp_path,
        )
        entry.exit_started_at = datetime.now(timezone.utc)
        o._running[issue.id] = entry

        async def _drive_callback() -> None:
            o._on_worker_task_done(issue.id, task)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        loop.run_until_complete(_drive_callback())
    finally:
        for retry in list(o._retry.values()):
            retry.timer_handle.cancel()
        loop.close()

    assert o._running.get(issue.id) is entry
    assert exit_calls == []
