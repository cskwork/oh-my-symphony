"""Frontier 003 RED contract for Core-owned AIDT worktree integration."""

from __future__ import annotations

import asyncio
import socket
import sys
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import symphony.orchestrator.core as core_module
import symphony.workspace as workspace_module
from symphony.aidt_routing import filter_routing_candidates
from symphony.aidt_routing.contract import AidtRoutingResult
from symphony.aidt_worktree import (
    AidtProvisioningAdmission,
    AidtRunGuard,
    AidtWorktreeFailure,
    AidtWorktreeHealth,
    DelegateDisposition,
    DelegateResult,
)
from symphony.errors import TurnFailed
from symphony.issue import Issue
from symphony.jira_intake import JiraIntakeResult
from symphony.orchestrator import Orchestrator
from symphony.orchestrator.entries import RetryEntry, RunningEntry

from .test_orchestrator_dispatch import _make_config


_GENERATION = "a" * 64
_PAIR = "b" * 64
_CHILD = "A20-1--viewer-api"
_MISSING = object()


@dataclass(frozen=True)
class _FakeGeneration:
    revision: int
    config: Any
    workflow_generation: str


class _StaticState:
    def __init__(self, config: Any, *, error: Exception | None = None) -> None:
        self.path = config.workflow_path
        self.config = config
        self.error = error

    def reload(self) -> tuple[Any, Exception | None]:
        return self.config, self.error

    def current(self) -> Any:
        return self.config


def _config(
    tmp_path: Path,
    *,
    root: str = "workspaces",
    worktree_enabled: bool = True,
) -> Any:
    cfg = _make_config(
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / root,
        active_states=("Ready", "In Progress"),
        terminal_states=("Done", "Cancelled", "Blocked"),
    )
    return replace(
        cfg,
        agent=replace(
            cfg.agent,
            auto_commit_on_done=False,
            auto_merge_on_done=False,
        ),
        raw={
            "aidt_worktree": {"enabled": worktree_enabled},
            "aidt_routing": {"enabled": True},
        },
    )


def _issue(identifier: str = _CHILD, *, state: str = "Ready") -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="fixture",
        priority=1,
        state=state,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _admission(*, revision: int = 7) -> AidtProvisioningAdmission:
    return AidtProvisioningAdmission(_CHILD, _GENERATION, _PAIR, revision, "provision")


def _generation(config: Any, *, revision: int = 1) -> _FakeGeneration:
    return _FakeGeneration(revision, config, _GENERATION)


def _guard(path: Path, *, revision: int = 7) -> AidtRunGuard:
    return AidtRunGuard(_CHILD, _GENERATION, _PAIR, revision, 2, path.resolve())


def _routing_result(*, provisionable: bool = True) -> AidtRoutingResult:
    nominated = frozenset({_CHILD}) if provisionable else frozenset()
    return AidtRoutingResult(
        True,
        True,
        frozenset({_CHILD, "A20-1"}),
        1,
        0,
        1,
        0,
        "success",
        provisionable_child_identifiers=nominated,
    )


class _FakeRuntime:
    def __init__(self, events: list[str], generation: Any) -> None:
        self.events = events
        self.generation = generation
        self._next_generation: Any = _MISSING
        self.expected_identifier = _CHILD
        self.admission_result: DelegateResult[Any] = DelegateResult.handled(_admission())
        self.publish_error: Exception | None = None
        self.health = AidtWorktreeHealth(
            True, "ready", _GENERATION, 1, 2, 0, 0, None, None, None
        )
        self.path = (generation.config.workspace_root / _CHILD).resolve()
        self.guard = _guard(self.path)

    def queue_generation(self, generation: Any) -> None:
        self._next_generation = generation

    def publish(self, config: Any) -> Any:
        self.events.append("publish")
        if self.publish_error is not None:
            raise self.publish_error
        generation = self.generation
        if self._next_generation is not _MISSING:
            generation = self._next_generation
            self._next_generation = _MISSING
        assert generation.config is config
        self.generation = generation
        return generation

    def reject_reload(self, category: str = "profile_invalid") -> None:
        self.events.append(f"reject:{category}")

    def admit_candidate(self, generation: Any, identifier: str) -> DelegateResult[Any]:
        assert generation is self.generation
        assert identifier == self.expected_identifier
        self.events.append("admit")
        return self.admission_result

    def health_snapshot(self) -> AidtWorktreeHealth:
        self.events.append("health")
        return self.health

    def path_for(self, generation: Any, identifier: str) -> DelegateResult[Path]:
        assert generation is self.generation
        assert identifier == self.expected_identifier
        self.events.append("runtime:path")
        return DelegateResult.handled(self.path)

    def create_or_reuse(self, generation: Any, admission: Any) -> DelegateResult[Any]:
        assert generation is self.generation
        assert admission.identifier == self.expected_identifier
        self.events.append("runtime:create")
        return DelegateResult.handled(SimpleNamespace(path=self.path, guard=self.guard))

    def before_run(self, generation: Any, guard: Any) -> DelegateResult[None]:
        assert generation is self.generation
        assert guard is self.guard
        self.events.append("runtime:guard")
        return DelegateResult.handled()


class _FakeManager:
    def __init__(
        self,
        root: Path,
        events: list[str],
        generation: Any,
        admission: AidtProvisioningAdmission,
        *,
        identifier: str = _CHILD,
    ) -> None:
        self.root = root.resolve()
        self.events = events
        self.generation = generation
        self.admission = admission
        self.identifier = identifier
        self.path = (self.root / identifier).resolve()
        self.guard = _guard(self.path, revision=admission.attempt_record_revision)
        self.remove_result: DelegateResult[None] = DelegateResult.owned_preserved(
            "authorization_invalid"
        )
        self.create_error = False
        self.before_error_at: int | None = None
        self.before_count = 0
        self.authorizations: list[object | None] = []

    def path_for(self, identifier: str, *, aidt_generation: Any = None) -> Path:
        assert identifier == self.identifier
        self.events.append("path" if aidt_generation is not None else "generic:path")
        if aidt_generation is not None:
            assert aidt_generation is self.generation
        return self.path

    async def create_or_reuse(
        self,
        identifier: str,
        *,
        aidt_generation: Any = None,
        aidt_admission: Any = None,
    ) -> Any:
        specialized = aidt_generation is not None or aidt_admission is not None
        self.events.append("create" if specialized else "generic:create")
        if self.create_error:
            raise _owned_error(identifier)
        if specialized:
            assert aidt_generation is self.generation
            assert aidt_admission is self.admission
        return SimpleNamespace(
            path=self.path,
            workspace_key=_CHILD,
            created_now=True,
            aidt_guard=self.guard if specialized else None,
        )

    async def before_run(
        self,
        path: Path,
        *,
        aidt_generation: Any = None,
        aidt_guard: Any = None,
    ) -> None:
        specialized = aidt_generation is not None or aidt_guard is not None
        self.events.append("guard" if specialized else "generic:guard")
        self.before_count += 1
        if self.before_error_at == self.before_count:
            raise _owned_error(self.identifier)
        if specialized:
            assert path == self.path
            assert aidt_generation is self.generation
            assert aidt_guard is self.guard

    async def after_run_best_effort(self, _path: Path) -> None:
        self.events.append("generic:after_run")

    async def after_done_best_effort(self, *_args: object, **_kwargs: object) -> bool:
        self.events.append("generic:after_done")
        return True

    async def remove(
        self,
        _path: Path,
        *,
        identifier: str | None = None,
        aidt_generation: Any = None,
        authorization: object | None = None,
        lease: object | None = None,
    ) -> DelegateResult[None] | None:
        del lease
        if identifier is not None or aidt_generation is not None:
            self.events.append("terminal_guard")
            self.authorizations.append(authorization)
            assert authorization is None
            assert identifier is not None
            assert aidt_generation is self.generation
            return self.remove_result
        self.events.append("generic:remove")
        return None

    def update_hooks(self, *_args: object, **_kwargs: object) -> None:
        return None

    def update_reuse_policy(self, _policy: str) -> None:
        return None

    def update_hook_env(self, _env: dict[str, str]) -> None:
        return None


class _TraceBackend:
    def __init__(self, events: list[str], *, fail_turn: bool = True) -> None:
        self.events = events
        self.fail_turn = fail_turn
        self.turns = 0

    async def start(self) -> None:
        self.events.append("backend")

    async def initialize(self) -> None:
        return None

    async def start_session(self, **_kwargs: object) -> None:
        return None

    async def run_turn(self, **_kwargs: object) -> None:
        self.turns += 1
        self.events.append(f"turn:{self.turns}")
        if self.fail_turn:
            raise TurnFailed("fixture stop")

    async def stop(self) -> None:
        self.events.append("backend:stop")


class _CancellableTaskSpy:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.cancelled = False

    def cancel(self) -> bool:
        self.events.append("worker:cancel")
        self.cancelled = True
        return True


class _RetryFlagRegistry:
    def __init__(self, attempt: int) -> None:
        self.flags: dict[str, Any] = {
            "retry_attempt": attempt,
            "paused": True,
            "pause_reason": "operator pause",
        }
        self.clear_calls: list[tuple[str, bool, bool]] = []

    def clear_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: bool,
        budget_exhausted: bool,
        paused: bool,
    ) -> None:
        del budget_exhausted
        self.clear_calls.append((issue_id, retry_attempt, paused))
        if retry_attempt:
            self.flags["retry_attempt"] = None
        if paused:
            self.flags.update(paused=False, pause_reason=None)


def _owned_error(identifier: str) -> Exception:
    error_type = getattr(
        workspace_module,
        "AidtWorkspaceOperationError",
        AidtWorktreeFailure,
    )
    return error_type("scope_changed", identifier)


def _attach_runtime(orchestrator: Orchestrator, runtime: Any, generation: Any) -> None:
    setattr(orchestrator, "_aidt_worktree_runtime", runtime)
    setattr(orchestrator, "_aidt_worktree_generation", generation)


def _owned_entry(
    issue: Issue,
    manager: _FakeManager,
    generation: Any,
    admission: AidtProvisioningAdmission,
) -> RunningEntry:
    entry = RunningEntry(
        issue,
        datetime.now(timezone.utc),
        None,
        None,
        manager.path,
        run_id="1" * 32,
    )
    entry.workspace_manager = manager  # type: ignore[attr-defined]
    entry.aidt_generation = generation  # type: ignore[attr-defined]
    entry.aidt_admission = admission  # type: ignore[attr-defined]
    entry.aidt_guard = manager.guard  # type: ignore[attr-defined]
    entry.aidt_workflow_generation = _GENERATION  # type: ignore[attr-defined]
    entry.aidt_route_pair_digest = _PAIR  # type: ignore[attr-defined]
    entry.aidt_attempt_record_revision = admission.attempt_record_revision  # type: ignore[attr-defined]
    entry.aidt_owned_failure = False  # type: ignore[attr-defined]
    entry.aidt_failure_category = None  # type: ignore[attr-defined]
    return entry


def _aidt_entry_state(entry: RunningEntry) -> tuple[Any, ...]:
    return tuple(
        getattr(entry, name, _MISSING)
        for name in (
            "workspace_manager",
            "aidt_generation",
            "aidt_admission",
            "aidt_guard",
            "aidt_workflow_generation",
            "aidt_route_pair_digest",
            "aidt_attempt_record_revision",
            "aidt_owned_failure",
            "aidt_failure_category",
        )
    )


def _isolate_tick(orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(core_module, "validate_for_dispatch", lambda _cfg: None)
    monkeypatch.setattr(orchestrator, "_ensure_run_registry", lambda _cfg: None)
    monkeypatch.setattr(orchestrator, "_heartbeat_running_leases", lambda: None)
    monkeypatch.setattr(orchestrator, "_reconcile_running", noop)
    monkeypatch.setattr(orchestrator, "_auto_normalize_legacy_human_review_done", noop)
    monkeypatch.setattr(orchestrator, "_auto_reopen_sources_from_resolved_rcas", noop)
    monkeypatch.setattr(orchestrator, "_auto_recover_blocked_sources", noop)
    monkeypatch.setattr(orchestrator, "_archive_sweep", noop)
    monkeypatch.setattr(orchestrator, "_maybe_schedule_continuous_improvement", lambda _cfg: None)
    monkeypatch.setattr(orchestrator, "_notify_observers", noop)
    monkeypatch.setattr(orchestrator, "_poll_jira_intake", _disabled_intake)


async def _disabled_intake(_cfg: object) -> core_module.JiraIntakePoll:
    result = JiraIntakeResult(False, 0, 0)
    return core_module.JiraIntakePoll(False, "disabled", result)


async def _run_worker(
    orchestrator: Orchestrator,
    issue: Issue,
    cfg: Any,
    entry: RunningEntry,
) -> None:
    orchestrator._running[issue.id] = entry

    async def ignore_exit(*_args: object, **_kwargs: object) -> None:
        return None

    orchestrator._on_worker_exit = ignore_exit  # type: ignore[method-assign]
    await orchestrator._run_agent_attempt(issue, None, cfg)


async def _initial_guard_trace(
    cfg: Any,
    issue: Issue,
) -> tuple[list[str], int, list[str], str | None]:
    events: list[str] = []
    generation = _generation(cfg)
    admission = _admission()
    manager = _FakeManager(cfg.workspace_root, events, generation, admission)
    manager.before_error_at = 2
    backend = _TraceBackend(events, fail_turn=False)
    state: Any = _StaticState(cfg)

    def backend_builder(_init: Any) -> Any:
        return backend

    orchestrator = Orchestrator(state, build_backend=backend_builder)
    orchestrator._workspace_manager = manager  # type: ignore[assignment]
    entry = _owned_entry(issue, manager, generation, admission)
    orchestrator._refresh_issue_state = lambda *_a: _async_value(issue)  # type: ignore[method-assign]

    error: str | None = None
    try:
        await _run_worker(orchestrator, issue, cfg, entry)
    except Exception as exc:  # pragma: no cover - asserted as bounded evidence
        error = type(exc).__name__
    return events[:5], backend.turns, _forbidden_generic(events), error


async def _rebuild_guard_trace(
    cfg: Any,
    issue: Issue,
) -> tuple[list[str], int, int, str | None]:
    events: list[str] = []
    generation = _generation(cfg)
    admission = _admission()
    captured = _FakeManager(cfg.workspace_root, events, generation, admission)
    current = _FakeManager(cfg.workspace_root / "current", events, generation, admission)
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    orchestrator._workspace_manager = current  # type: ignore[assignment]
    orchestrator._running[issue.id] = _owned_entry(
        issue, captured, generation, admission
    )
    rebuilt = _TraceBackend(events, fail_turn=False)
    orchestrator._build_backend_override = lambda _init: rebuilt  # type: ignore[assignment]
    old_client: Any = _TraceBackend(events, fail_turn=False)
    error: str | None = None
    try:
        await orchestrator._rebuild_backend_for_phase(
            issue=issue, running_issue_id=issue.id, cfg=cfg,
            workspace_path=captured.path, attempt=None, doc_language="en",
            old_client=old_client,
            is_rewind=False, turn_number=2,
        )
    except Exception as exc:  # pragma: no cover - asserted as bounded evidence
        error = type(exc).__name__
    return events, captured.before_count, current.before_count, error


def _ordered_worker(orchestrator: Orchestrator, events: list[str]) -> Any:
    async def worker(current: Issue, _attempt: int | None, _cfg: Any) -> None:
        entry: Any = orchestrator._running[current.id]
        events.append("entry")
        workspace = await entry.workspace_manager.create_or_reuse(  # type: ignore[attr-defined]
            current.identifier,
            aidt_generation=entry.aidt_generation,  # type: ignore[attr-defined]
            aidt_admission=entry.aidt_admission,  # type: ignore[attr-defined]
        )
        await entry.workspace_manager.before_run(  # type: ignore[attr-defined]
            workspace.path,
            aidt_generation=entry.aidt_generation,  # type: ignore[attr-defined]
            aidt_guard=workspace.aidt_guard,
        )
        events.append("backend")

    return worker


def _unmanaged_entry(issue: Issue, manager: _FakeManager, generation: Any) -> RunningEntry:
    entry = RunningEntry(
        issue, datetime.now(timezone.utc), None, None, manager.path, run_id="1" * 32
    )
    entry.workspace_manager = manager  # type: ignore[attr-defined]
    entry.aidt_generation = generation  # type: ignore[attr-defined]
    return entry


def _forbidden_generic(events: list[str]) -> list[str]:
    return [event for event in events if event.startswith("generic:")]


def _deny(events: list[str], label: str) -> Any:
    def denied(*_args: object, **_kwargs: object) -> Any:
        events.append(f"forbidden:{label}")
        raise AssertionError(f"forbidden {label} operation")

    return denied


class _HealthRuntime(_FakeRuntime):
    _forbidden_attributes = frozenset(
        {
            "filesystem",
            "git",
            "registry",
            "route",
            "tracker",
            "clock",
            "network",
            "jira",
            "backend",
        }
    )

    def __getattr__(self, name: str) -> Any:
        if name in self._forbidden_attributes:
            self.events.append(f"forbidden:runtime:{name}")
            raise AssertionError(f"forbidden runtime attribute access: {name}")
        raise AttributeError(name)


def _install_reconcile_mutation_traces(
    orchestrator: Orchestrator,
    events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def record(label: str) -> Any:
        def append_event(*_args: Any, **_kwargs: Any) -> None:
            events.append(f"generic:{label}")

        return append_event

    def async_record(label: str) -> Any:
        def append_event(*_args: Any, **_kwargs: Any) -> Any:
            return _async_value(events.append(f"generic:{label}"))

        return append_event

    monkeypatch.setattr(core_module, "commit_workspace_on_done", async_record("commit"))
    monkeypatch.setattr(core_module, "auto_merge_on_done_best_effort", async_record("merge"))
    monkeypatch.setattr(orchestrator, "_schedule_retry", record("retry"))
    monkeypatch.setattr(
        Orchestrator, "_tracker_call_update_state", staticmethod(record("tracker"))
    )


def _assert_half_pairs_rejected_before_dispatch_effects(
    orchestrator: Orchestrator,
    issue: Issue,
    cfg: Any,
    generation: Any,
    admission: AidtProvisioningAdmission,
    events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch: Any = orchestrator._dispatch
    with monkeypatch.context() as scoped:
        scoped.setattr(orchestrator, "_try_acquire_run_lease", _deny(events, "lease"))
        scoped.setattr(core_module.asyncio, "create_task", _deny(events, "task"))
        for keywords in (
            {"aidt_generation": generation},
            {"aidt_admission": admission},
        ):
            with pytest.raises(Exception) as caught:
                dispatch(issue, cfg, attempt=None, **keywords)
            assert not isinstance(caught.value, TypeError)
    assert events == []


async def _real_manager_owned_barrier(
    cfg: Any,
    tmp_path: Path,
    generation: Any,
    admission: AidtProvisioningAdmission,
    events: list[str],
) -> tuple[Any, _FakeRuntime]:
    runtime = _FakeRuntime(events, generation)
    hooks = replace(
        cfg.hooks,
        after_create="forbidden generic create hook",
        before_run="forbidden generic before-run hook",
    )
    manager_factory: Any = workspace_module.WorkspaceManager
    manager: Any = manager_factory(
        tmp_path / "generic-root", hooks, aidt_runtime=runtime
    )
    manager._run_hook = _deny(events, "generic-hook")
    path = manager.path_for(_CHILD, aidt_generation=generation)
    workspace = await manager.create_or_reuse(
        _CHILD, aidt_generation=generation, aidt_admission=admission
    )
    await manager.before_run(
        path, aidt_generation=generation, aidt_guard=workspace.aidt_guard
    )
    manager.path = path
    manager.guard = workspace.aidt_guard
    assert events == ["runtime:path", "runtime:create", "runtime:guard"]
    assert not (manager.root / _CHILD).exists()
    assert not (manager.root / ".symphony-workspace-owners").exists()
    return manager, runtime


async def _exercise_real_unmanaged_dispatch(
    cfg: Any,
    issue: Issue,
    monkeypatch: pytest.MonkeyPatch,
    *,
    retry: RetryEntry | None = None,
) -> tuple[list[str], Orchestrator, _FakeManager, Any]:
    events: list[str] = []
    generation = _generation(cfg)
    runtime = _FakeRuntime(events, generation)
    runtime.expected_identifier = issue.identifier
    runtime.admission_result = DelegateResult.unmanaged()
    manager = _FakeManager(
        cfg.workspace_root, events, generation, _admission(), identifier=issue.identifier
    )
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    _attach_runtime(orchestrator, runtime, generation)
    orchestrator._workspace_manager = manager  # type: ignore[assignment]

    async def worker(current: Issue, _attempt: int | None, _cfg: Any) -> None:
        entry: Any = orchestrator._running[current.id]
        if getattr(entry, "workspace_manager", None) is not manager:
            events.append("entry:uncaptured-manager")
            return
        events.append(f"entry:{entry.attempt_kind}")

    monkeypatch.setattr(orchestrator, "_run_agent_attempt", worker)
    monkeypatch.setattr(orchestrator, "_on_worker_task_done", lambda *_args: None)
    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", lambda **_kw: events.append("lease") or "1" * 32)
    monkeypatch.setattr(Orchestrator, "_tracker_call_record_agent_kind", staticmethod(lambda *_a: None))
    monkeypatch.setattr(orchestrator, "_fetch_candidates", lambda _cfg: _async_value([issue]))
    if retry is None:
        _isolate_tick(orchestrator, monkeypatch)
        monkeypatch.setattr(core_module, "run_aidt_routing", lambda *_a, **_k: _routing_result())
        monkeypatch.setattr(orchestrator, "_available_slots", lambda _cfg: 1)
        monkeypatch.setattr(orchestrator, "_should_dispatch", lambda *_a: True)
        monkeypatch.setattr(orchestrator, "_conflict_blocker", lambda _issue: None)
        await orchestrator._on_tick()
    else:
        monkeypatch.setattr(orchestrator, "_eligibility_decision", lambda *_a, **_k: SimpleNamespace(disposition=core_module._EligibilityDisposition.READY))
        await orchestrator._process_retry(retry, cfg)
    await asyncio.sleep(0)
    return events, orchestrator, manager, generation


def _install_health_io_denials(
    orchestrator: Orchestrator,
    events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("exists", "is_file", "read_text", "stat"):
        monkeypatch.setattr(Path, name, _deny(events, "filesystem"))
    for name in ("socket", "create_connection"):
        monkeypatch.setattr(socket, name, _deny(events, "network"))
    monkeypatch.setattr(urllib.request, "urlopen", _deny(events, "network"))
    monkeypatch.setattr(workspace_module.subprocess, "run", _deny(events, "git"))
    monkeypatch.setattr(core_module, "run_aidt_routing", _deny(events, "route"))
    monkeypatch.setattr(core_module, "build_tracker_client", _deny(events, "tracker"))
    monkeypatch.setattr(orchestrator, "_ensure_run_registry", _deny(events, "registry"))
    monkeypatch.setattr(orchestrator, "_poll_jira_intake", _deny(events, "jira"))
    monkeypatch.setattr(orchestrator, "_build_agent_backend", _deny(events, "backend"))


async def _start_with_default_runtime(
    state: _StaticState,
    runtime: _FakeRuntime,
    events: list[str],
    created: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Orchestrator:
    def runtime_factory(path: Path, *_args: object, **_kwargs: object) -> Any:
        assert path == state.path
        events.append("runtime:init")
        return runtime

    def manager_factory(
        root: Path, *_args: object, aidt_runtime: Any = None, **_kwargs: object
    ) -> Any:
        events.append("manager")
        manager = SimpleNamespace(
            root=root.resolve(),
            runtime=aidt_runtime,
            generation=aidt_runtime.generation,
        )
        created.append(manager)
        return manager

    monkeypatch.delitem(sys.modules, "symphony.aidt_worktree.provisioner", raising=False)
    monkeypatch.delitem(sys.modules, "symphony.aidt_worktree.git_state", raising=False)
    monkeypatch.setattr(core_module, "AidtWorktreeRuntime", runtime_factory, raising=False)
    monkeypatch.setattr(core_module, "WorkspaceManager", manager_factory)
    orchestrator = Orchestrator(state)  # type: ignore[arg-type]
    assert events == ["runtime:init"]
    monkeypatch.setattr(orchestrator, "_load_token_ema", lambda _cfg: None)
    monkeypatch.setattr(orchestrator, "_load_done_count", lambda _cfg: None)
    monkeypatch.setattr(core_module, "stats_store_for", lambda _path: None)
    monkeypatch.setattr(orchestrator, "_ensure_run_registry", lambda _cfg: None)
    monkeypatch.setattr(
        orchestrator, "_startup_terminal_cleanup", lambda _cfg: _async_value(None)
    )
    monkeypatch.setattr(orchestrator, "_spawn_tick_loop", lambda: None)
    await orchestrator.start()
    assert events == ["runtime:init", "publish", "manager"]
    assert created[0].runtime is runtime
    assert getattr(orchestrator, "_aidt_worktree_generation") is runtime.generation
    assert "symphony.aidt_worktree.provisioner" not in sys.modules
    assert "symphony.aidt_worktree.git_state" not in sys.modules
    return orchestrator


def _trace_candidate_gates(
    orchestrator: Orchestrator,
    events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def traced_filter(*args: Any, **kwargs: Any) -> list[Issue]:
        events.append("filter")
        return filter_routing_candidates(*args, **kwargs)

    def available_slots(_cfg: Any) -> int:
        label = "slot" if "slot" not in events else "post-candidate-slot"
        events.append(label)
        return 1 if label == "slot" else 0

    monkeypatch.setattr(core_module, "filter_routing_candidates", traced_filter)
    monkeypatch.setattr(orchestrator, "_available_slots", available_slots)
    monkeypatch.setattr(
        orchestrator,
        "_should_dispatch",
        lambda _issue, _cfg: events.append("eligibility") or True,
    )
    monkeypatch.setattr(
        orchestrator,
        "_conflict_blocker",
        lambda _issue: events.append("conflict") or None,
    )


async def test_process_runtime_identity_survives_workspace_manager_root_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path, worktree_enabled=False)
    next_cfg = _config(tmp_path, root="replacement", worktree_enabled=False)
    state = _StaticState(cfg)
    events: list[str] = []
    initial_generation = _generation(cfg)
    replacement_generation = _generation(next_cfg, revision=2)
    runtime = _FakeRuntime(events, initial_generation)
    created: list[Any] = []
    orchestrator = await _start_with_default_runtime(
        state, runtime, events, created, monkeypatch
    )
    assert created[0].generation is initial_generation

    state.config = next_cfg
    runtime.queue_generation(replacement_generation)
    events.clear()
    _isolate_tick(orchestrator, monkeypatch)
    monkeypatch.setattr(core_module, "run_aidt_routing", lambda *_a, **_k: _routing_result())
    monkeypatch.setattr(orchestrator, "_fetch_candidates", lambda _cfg: _async_value([]))

    await orchestrator._on_tick()

    assert events == ["publish", "manager"]
    assert len(created) == 2 and created[1].runtime is runtime
    assert runtime is getattr(orchestrator._workspace_manager, "runtime")
    assert getattr(orchestrator, "_aidt_worktree_runtime", runtime) is runtime
    assert getattr(orchestrator, "_aidt_worktree_generation") is replacement_generation
    assert runtime.generation is replacement_generation
    assert initial_generation is not replacement_generation
    assert initial_generation.config is cfg
    assert replacement_generation.config is next_cfg


async def _async_value(value: Any) -> Any:
    return value


async def test_failed_generation_publication_keeps_manager_and_denies_candidate_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_cfg = _config(tmp_path, root="prior-workspaces")
    rejected_cfg = _config(
        tmp_path,
        root="rejected-workspaces",
        worktree_enabled=False,
    )
    events: list[str] = []
    generation = _generation(prior_cfg)
    runtime = _FakeRuntime(events, generation)
    runtime.publish_error = AidtWorktreeFailure("profile_invalid")
    manager = _FakeManager(
        prior_cfg.workspace_root,
        events,
        generation,
        _admission(),
    )
    orchestrator = Orchestrator(_StaticState(rejected_cfg))  # type: ignore[arg-type]
    _attach_runtime(orchestrator, runtime, generation)
    orchestrator._workspace_manager = manager  # type: ignore[assignment]
    _isolate_tick(orchestrator, monkeypatch)
    monkeypatch.setattr(
        orchestrator,
        "_heartbeat_running_leases",
        lambda: events.append("heartbeat"),
    )
    monkeypatch.setattr(
        orchestrator,
        "_fetch_candidates",
        lambda _cfg: _async_value(events.append("fetch")),
    )

    await orchestrator._on_tick()

    assert events == ["publish", "reject:profile_invalid"]
    assert orchestrator._workspace_manager is manager
    assert getattr(orchestrator, "_aidt_worktree_generation") is generation


async def test_provisionable_child_is_filtered_then_admitted_before_slot_or_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    events: list[str] = []
    generation = _generation(cfg)
    runtime = _FakeRuntime(events, generation)
    admission = _admission()
    runtime.admission_result = DelegateResult.handled(admission)
    manager = _FakeManager(cfg.workspace_root, events, generation, admission)
    issue = _issue()
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    _attach_runtime(orchestrator, runtime, generation)
    orchestrator._workspace_manager = manager  # type: ignore[assignment]
    _isolate_tick(orchestrator, monkeypatch)
    _trace_candidate_gates(orchestrator, events, monkeypatch)
    monkeypatch.setattr(core_module, "run_aidt_routing", lambda *_a, **_k: _routing_result())
    monkeypatch.setattr(orchestrator, "_fetch_candidates", lambda _cfg: _async_value([issue]))
    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", lambda **_kw: events.append("lease") or "1" * 32)
    monkeypatch.setattr(orchestrator, "_run_agent_attempt", _ordered_worker(orchestrator, events))
    monkeypatch.setattr(orchestrator, "_on_worker_task_done", lambda *_args: None)
    monkeypatch.setattr(Orchestrator, "_tracker_call_record_agent_kind", staticmethod(lambda *_a: None))

    await orchestrator._on_tick()
    await asyncio.sleep(0)

    assert events == [
        "publish", "filter", "admit", "slot", "eligibility", "conflict", "path",
        "lease", "post-candidate-slot", "entry", "create", "guard", "backend",
    ]


async def test_owned_candidate_disposition_never_reaches_generic_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue()
    for result in (
        DelegateResult.owned_preserved("attempt_backoff"),
        DelegateResult.owned_error("scope_changed"),
    ):
        events: list[str] = []
        generation = _generation(cfg)
        runtime = _FakeRuntime(events, generation)
        runtime.admission_result = result
        orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
        _attach_runtime(orchestrator, runtime, generation)
        orchestrator._workspace_manager = _FakeManager(  # type: ignore[assignment]
            cfg.workspace_root, events, generation, _admission()
        )
        _isolate_tick(orchestrator, monkeypatch)
        monkeypatch.setattr(core_module, "run_aidt_routing", lambda *_a, **_k: _routing_result())
        monkeypatch.setattr(orchestrator, "_fetch_candidates", lambda _cfg: _async_value([issue]))
        monkeypatch.setattr(
            orchestrator,
            "_available_slots",
            lambda _cfg: events.append("post-candidate-slot") or 0,
        )
        for name in ("_should_dispatch", "_conflict_blocker", "_dispatch"):
            monkeypatch.setattr(orchestrator, name, _deny(events, name))
        monkeypatch.setattr(orchestrator, "_schedule_retry", _deny(events, "retry"))
        monkeypatch.setattr(
            Orchestrator, "_tracker_call_update_state", staticmethod(_deny(events, "tracker"))
        )

        await orchestrator._on_tick()

        assert events == ["publish", "admit", "post-candidate-slot"]
        assert issue.id not in orchestrator._retry


async def test_dispatch_captures_manager_generation_pair_and_attempt_revision_before_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue()
    events: list[str] = []
    generation = _generation(cfg)
    admission = _admission(revision=11)
    manager = _FakeManager(cfg.workspace_root, events, generation, admission)
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    orchestrator._workspace_manager = manager  # type: ignore[assignment]
    task_snapshots: list[tuple[Any, ...] | None] = []
    worker_snapshots: list[tuple[Any, ...]] = []
    legacy_entry = RunningEntry(
        issue, datetime.now(timezone.utc), None, None, manager.path
    )
    legacy_defaults = _aidt_entry_state(legacy_entry)
    _assert_half_pairs_rejected_before_dispatch_effects(
        orchestrator, issue, cfg, generation, admission, events, monkeypatch
    )

    async def worker(current: Issue, _attempt: int | None, _cfg: Any) -> None:
        worker_snapshots.append(_aidt_entry_state(orchestrator._running[current.id]))

    def create_task_spy(coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
        installed = orchestrator._running.get(issue.id)
        task_snapshots.append(
            None if installed is None else _aidt_entry_state(installed)
        )
        return asyncio.get_running_loop().create_task(coro, name=name)

    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", lambda **_kw: "1" * 32)
    monkeypatch.setattr(orchestrator, "_run_agent_attempt", worker)
    monkeypatch.setattr(orchestrator, "_on_worker_task_done", lambda *_args: None)
    monkeypatch.setattr(core_module.asyncio, "create_task", create_task_spy)
    monkeypatch.setattr(Orchestrator, "_tracker_call_record_agent_kind", staticmethod(lambda *_a: None))

    dispatch: Any = orchestrator._dispatch
    dispatch(
        issue, cfg, attempt=None, aidt_generation=generation, aidt_admission=admission
    )
    expected = (
        manager, generation, admission, None, _GENERATION, _PAIR, 11, False, None
    )
    assert task_snapshots == [expected]
    await asyncio.sleep(0)

    assert legacy_defaults == (None, None, None, None, None, None, None, False, None)
    assert worker_snapshots == [expected]


async def test_initial_attempt_uses_captured_manager_create_guard_before_backend(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue(state="In Progress")
    events: list[str] = []
    generation = _generation(cfg)
    admission = _admission()
    captured, runtime = await _real_manager_owned_barrier(
        cfg, tmp_path, generation, admission, events
    )
    events.clear()
    forbidden = _FakeManager(tmp_path / "wrong", events, generation, admission)
    backend = _TraceBackend(events)
    orchestrator = Orchestrator(_StaticState(cfg), build_backend=lambda _init: backend)  # type: ignore[arg-type]
    orchestrator._workspace_manager = forbidden  # type: ignore[assignment]
    entry = _owned_entry(issue, captured, generation, admission)
    entry.aidt_guard = None  # type: ignore[attr-defined]

    await _run_worker(orchestrator, issue, cfg, entry)

    assert _forbidden_generic(events) == []
    assert events[:3] == ["runtime:create", "runtime:guard", "backend"]
    assert entry.aidt_guard is runtime.guard  # type: ignore[attr-defined]


async def test_reload_between_create_and_before_run_blocks_backend_without_fallback(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue(state="In Progress")
    events: list[str] = []
    generation = _generation(cfg)
    admission = _admission()
    manager = _FakeManager(cfg.workspace_root, events, generation, admission)
    manager.before_error_at = 1
    backend = _TraceBackend(events)
    orchestrator = Orchestrator(_StaticState(cfg), build_backend=lambda _init: backend)  # type: ignore[arg-type]
    orchestrator._workspace_manager = manager  # type: ignore[assignment]
    entry = _owned_entry(issue, manager, generation, admission)

    await _run_worker(orchestrator, issue, cfg, entry)

    assert events == ["create", "guard"]
    assert entry.aidt_owned_failure is True  # type: ignore[attr-defined]
    assert entry.aidt_failure_category == "scope_changed"  # type: ignore[attr-defined]


async def test_reload_between_turns_rechecks_the_captured_guard(
    tmp_path: Path,
) -> None:
    cfg = replace(_config(tmp_path), agent=replace(_config(tmp_path).agent, max_turns=2))
    issue = _issue(state="In Progress")
    initial_trace = await _initial_guard_trace(cfg, issue)
    rebuild_trace = await _rebuild_guard_trace(cfg, issue)

    assert (initial_trace, rebuild_trace) == (
        (["create", "guard", "backend", "turn:1", "guard"], 1, [], None),
        (["backend:stop", "guard", "backend"], 1, 0, None),
    )


async def test_timer_retry_uses_fresh_runtime_admission_and_preserves_attempt_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue()
    events: list[str] = []
    generation = _generation(cfg, revision=2)
    runtime = _FakeRuntime(events, generation)
    admission = _admission(revision=13)
    runtime.admission_result = DelegateResult.handled(admission)
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    _attach_runtime(orchestrator, runtime, generation)
    retry = _retry_entry(issue, kind="continuation")
    dispatched: list[dict[str, Any]] = []
    monkeypatch.setattr(orchestrator, "_fetch_candidates", lambda _cfg: _async_value([issue]))
    monkeypatch.setattr(orchestrator, "_eligibility_decision", lambda *_a, **_k: SimpleNamespace(disposition=core_module._EligibilityDisposition.READY))
    monkeypatch.setattr(orchestrator, "_dispatch", lambda *_a, **kwargs: dispatched.append(kwargs))

    await orchestrator._process_retry(retry, cfg)

    assert events == ["admit"]
    assert dispatched == [{
        "attempt": retry.attempt, "attempt_kind": "continuation",
        "aidt_generation": generation, "aidt_admission": admission,
    }]


def _retry_entry(issue: Issue, *, kind: str = "retry") -> RetryEntry:
    loop = asyncio.get_running_loop()
    handle = loop.call_later(3600, lambda: None)
    handle.cancel()
    return RetryEntry(issue.id, issue.identifier, 3, 0, handle, kind=kind)


async def _owned_retry_release_outcome(
    cfg: Any,
    issue: Issue,
    result: DelegateResult[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, ...]:
    events: list[str] = []
    generation = _generation(cfg)
    runtime = _FakeRuntime(events, generation)
    runtime.admission_result = result
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    _attach_runtime(orchestrator, runtime, generation)
    retry = _retry_entry(issue)
    orchestrator._claimed.add(issue.id)
    orchestrator._persisted_retry_attempts[issue.id] = retry.attempt
    orchestrator._paused_issue_ids.add(issue.id)
    orchestrator._pause_reasons[issue.id] = "operator pause"
    debug = orchestrator._issue_debug.setdefault(issue.id, core_module._IssueDebug())
    debug.current_retry_attempt, debug.current_attempt_kind = retry.attempt, retry.kind
    registry = _RetryFlagRegistry(retry.attempt)
    orchestrator._run_registry = registry  # type: ignore[assignment]
    durable_sentinel = object()
    runtime.durable_state = durable_sentinel  # type: ignore[attr-defined]
    monkeypatch.setattr(orchestrator, "_fetch_candidates", lambda _cfg: _async_value([issue]))
    monkeypatch.setattr(orchestrator, "_eligibility_decision", lambda *_a, **_k: SimpleNamespace(disposition=core_module._EligibilityDisposition.READY))
    for name in ("_repark_retry", "_dispatch", "_schedule_retry"):
        monkeypatch.setattr(orchestrator, name, _deny(events, name))
    monkeypatch.setattr(Orchestrator, "_tracker_call_update_state", staticmethod(_deny(events, "tracker")))
    monkeypatch.setattr(Orchestrator, "_tracker_call_append_note", staticmethod(_deny(events, "tracker-note")))
    error: str | None = None
    try:
        await orchestrator._process_retry(retry, cfg)
    except Exception as exc:  # pragma: no cover - asserted as bounded evidence
        error = f"{type(exc).__name__}: {exc}"
    return (
        events, issue.id in orchestrator._claimed,
        orchestrator._persisted_retry_attempts.get(issue.id), issue.id in orchestrator._retry,
        set(orchestrator._paused_issue_ids), dict(orchestrator._pause_reasons),
        (debug.current_retry_attempt, debug.current_attempt_kind), dict(registry.flags),
        list(registry.clear_calls),
        getattr(runtime, "durable_state", _MISSING) is durable_sentinel, error,
    )


async def test_timer_retry_owned_disposition_releases_generic_retry_without_repark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue()
    outcomes = []
    for result in (
        DelegateResult.owned_preserved("attempt_manual"),
        DelegateResult.owned_error("scope_changed"),
    ):
        outcomes.append(
            await _owned_retry_release_outcome(cfg, issue, result, monkeypatch)
        )

    expected = (
        ["admit"], False, None, False, {issue.id}, {issue.id: "operator pause"},
        (3, "retry"),
        {"retry_attempt": None, "paused": True, "pause_reason": "operator pause"},
        [(issue.id, True, False)], True, None,
    )
    assert outcomes == [expected, expected]


async def test_specialized_create_or_guard_failure_schedules_no_generic_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue(state="In Progress")
    for phase in ("create", "guard"):
        events: list[str] = []
        generation = _generation(cfg)
        admission = _admission()
        manager = _FakeManager(cfg.workspace_root, events, generation, admission)
        manager.create_error = phase == "create"
        manager.before_error_at = 1 if phase == "guard" else None
        orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
        orchestrator._workspace_manager = manager  # type: ignore[assignment]
        entry = _owned_entry(issue, manager, generation, admission)
        orchestrator._running[issue.id] = entry
        monkeypatch.setattr(orchestrator, "_schedule_retry", lambda *_a, **_k: events.append("retry"))
        monkeypatch.setattr(
            Orchestrator,
            "_tracker_call_update_state",
            staticmethod(lambda *_a, **_k: events.append("tracker:update")),
        )
        monkeypatch.setattr(
            Orchestrator,
            "_tracker_call_append_note",
            staticmethod(lambda *_a, **_k: events.append("tracker:note")),
        )
        retry_state = dict(orchestrator._persisted_retry_attempts)

        await orchestrator._run_agent_attempt(issue, None, cfg)

        assert "retry" not in events
        assert not any(event.startswith("tracker:") for event in events)
        assert issue.id not in orchestrator._retry
        assert orchestrator._persisted_retry_attempts == retry_state
        assert orchestrator._issue_debug[issue.id].last_error == "scope_changed"


async def test_worker_exit_done_owned_preserved_skips_commit_merge_hooks_and_remove(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _config(tmp_path)
    cfg = replace(
        base,
        agent=replace(
            base.agent,
            auto_commit_on_done=True,
            auto_merge_on_done=True,
        ),
    )
    issue = _issue(state="Done")
    for result in (
        DelegateResult.owned_preserved("authorization_invalid"),
        DelegateResult.owned_error("scope_changed"),
    ):
        events, orchestrator, entry, captured = _terminal_fixture(cfg, issue)
        captured.remove_result = result
        current = _FakeManager(
            tmp_path / "current", events, getattr(entry, "aidt_generation"), _admission()
        )
        orchestrator._workspace_manager = current  # type: ignore[assignment]
        orchestrator._running[issue.id] = entry
        monkeypatch.setattr(core_module, "commit_workspace_on_done", _deny(events, "commit"))
        monkeypatch.setattr(core_module, "auto_merge_on_done_best_effort", _deny(events, "merge"))
        monkeypatch.setattr(orchestrator, "_schedule_retry", _deny(events, "retry"))
        monkeypatch.setattr(
            Orchestrator,
            "_tracker_call_update_state",
            staticmethod(_deny(events, "tracker")),
        )

        await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

        assert events == ["terminal_guard"]
        assert captured.authorizations == [None] and current.authorizations == []
        assert issue.id not in orchestrator._retry


def _terminal_fixture(cfg: Any, issue: Issue) -> tuple[list[str], Orchestrator, RunningEntry, _FakeManager]:
    events: list[str] = []
    generation = _generation(cfg)
    admission = _admission()
    manager = _FakeManager(cfg.workspace_root, events, generation, admission)
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    orchestrator._workspace_manager = manager  # type: ignore[assignment]
    _attach_runtime(orchestrator, _FakeRuntime(events, generation), generation)
    return events, orchestrator, _owned_entry(issue, manager, generation, admission), manager


async def test_reconcile_terminal_owned_preserved_leaves_cleanup_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _config(tmp_path)
    cfg = replace(
        base,
        agent=replace(
            base.agent,
            auto_commit_on_done=True,
            auto_merge_on_done=True,
        ),
    )
    issue = _issue(state="Done")
    outcomes: list[
        tuple[list[str], bool, bool, list[object | None], list[object | None]]
    ] = []
    for result in (
        DelegateResult.owned_preserved("authorization_invalid"),
        DelegateResult.owned_error("scope_changed"),
    ):
        events, orchestrator, entry, captured = _terminal_fixture(cfg, issue)
        captured.remove_result = result
        current = _FakeManager(
            tmp_path / "current", events, getattr(entry, "aidt_generation"), _admission()
        )
        if result.disposition is DelegateDisposition.OWNED_ERROR:
            orchestrator._workspace_manager = current  # type: ignore[assignment]
        worker_task = _CancellableTaskSpy(events)
        entry.worker_task = worker_task  # type: ignore[assignment]
        entry.terminal_seen_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        _install_reconcile_mutation_traces(orchestrator, events, monkeypatch)

        await orchestrator._reconcile_one(
            issue, entry, cfg, active={"ready"}, terminal={"done"},
            now=datetime.now(timezone.utc), recent_grace_s=0,
        )
        outcomes.append((
            list(events), worker_task.cancelled, entry.workspace_cleanup_started,
            list(captured.authorizations), list(current.authorizations),
        ))

    assert outcomes == [
        (["terminal_guard", "worker:cancel"], True, False, [None], []),
        (["terminal_guard", "worker:cancel"], True, False, [None], []),
    ]


async def test_reconcile_inactive_owned_preserved_leaves_cleanup_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue(state="Removed")
    events, orchestrator, entry, _manager = _terminal_fixture(cfg, issue)
    monkeypatch.setattr(core_module, "commit_workspace_on_done", lambda *_a, **_k: _async_value(events.append("generic:commit")))

    await orchestrator._reconcile_one(
        issue, entry, cfg, active={"ready"}, terminal={"done"},
        now=datetime.now(timezone.utc), recent_grace_s=0,
    )

    assert events == ["terminal_guard"]
    assert entry.workspace_cleanup_started is False


async def test_startup_terminal_owned_preserved_skips_every_generic_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue(state="Cancelled")
    events, orchestrator, _entry, manager = _terminal_fixture(cfg, issue)
    manager.path.mkdir(parents=True)
    monkeypatch.setattr(orchestrator, "_tracker_call_terminal_issues", lambda _cfg: [issue])
    monkeypatch.setattr(core_module, "commit_workspace_on_done", lambda *_a, **_k: _async_value(events.append("generic:commit")))

    await orchestrator._startup_terminal_cleanup(cfg)

    assert events == ["path", "terminal_guard"]
    assert manager.path.exists()


async def test_production_terminal_paths_never_issue_completion_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    done = _issue(state="Done")
    events, orchestrator, entry, manager = _terminal_fixture(cfg, done)
    orchestrator._running[done.id] = entry
    monkeypatch.setattr(core_module, "commit_workspace_on_done", lambda *_a, **_k: _async_value(None))
    await orchestrator._on_worker_exit_impl(done.id, "normal", None)

    cancelled = _issue(state="Cancelled")
    manager.path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orchestrator, "_tracker_call_terminal_issues", lambda _cfg: [cancelled])
    await orchestrator._startup_terminal_cleanup(cfg)

    assert manager.authorizations == [None, None]
    assert "terminal_guard" in events


async def test_unmanaged_initial_retry_worker_and_terminal_paths_keep_existing_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    issue = _issue("LOCAL-1", state="In Progress")
    initial, _initial_orch, _initial_manager, _ = await _exercise_real_unmanaged_dispatch(
        cfg, issue, monkeypatch
    )
    retry_issue = _issue("LOCAL-2", state="In Progress")
    retry = _retry_entry(retry_issue, kind="continuation")
    retried, _retry_orch, _retry_manager, _ = await _exercise_real_unmanaged_dispatch(
        cfg, retry_issue, monkeypatch, retry=retry
    )
    assert initial == ["publish", "admit", "generic:path", "lease", "entry:initial"]
    assert retried == ["admit", "generic:path", "lease", "entry:continuation"]

    events: list[str] = []
    generation = _generation(cfg)
    runtime = _HealthRuntime(events, generation)
    runtime.expected_identifier = issue.identifier
    runtime.admission_result = DelegateResult.unmanaged()
    admission = _admission()
    manager = _FakeManager(
        cfg.workspace_root, events, generation, admission, identifier=issue.identifier
    )
    manager.remove_result = DelegateResult.unmanaged()
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    _attach_runtime(orchestrator, runtime, generation)
    orchestrator._workspace_manager = manager  # type: ignore[assignment]
    entry = _unmanaged_entry(issue, manager, generation)
    backend = _TraceBackend(events)
    orchestrator._build_backend_override = lambda _init: backend  # type: ignore[assignment]
    await _run_worker(orchestrator, issue, cfg, entry)

    done = replace(issue, state="Done")
    entry.issue = done
    orchestrator._running[issue.id] = entry
    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert events == [
        "generic:create", "generic:guard",
        "backend", "turn:1", "backend:stop", "generic:after_run", "terminal_guard",
        "generic:after_done", "generic:remove",
    ]
    assert runtime.admission_result.disposition is DelegateDisposition.UNMANAGED


def test_health_serializes_only_the_bounded_worktree_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    events: list[str] = []
    generation = _generation(cfg)
    runtime = _FakeRuntime(events, generation)
    runtime.hostile_path = "/private/TOP-SECRET"  # type: ignore[attr-defined]
    orchestrator = Orchestrator(_StaticState(cfg))  # type: ignore[arg-type]
    _attach_runtime(orchestrator, runtime, generation)
    with monkeypatch.context() as scoped:
        _install_health_io_denials(orchestrator, events, scoped)
        health = orchestrator.health()

    assert health["aidt_worktree"] == {
        "enabled": True,
        "status": "ready",
        "workflow_generation": _GENERATION,
        "create_count": 1,
        "resume_count": 2,
        "failure_count": 0,
        "consecutive_failures": 0,
        "last_category": None,
        "last_ref": None,
        "last_success_at": None,
    }
    assert events == ["health"]
    assert "TOP-SECRET" not in repr(health)
