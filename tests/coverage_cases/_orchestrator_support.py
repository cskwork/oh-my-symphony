# ruff: noqa: F401
"""Behavior-focused edge coverage for orchestration and persistence policies."""

from __future__ import annotations

import asyncio

import ctypes

import json

import sqlite3

import subprocess

import sys

from dataclasses import dataclass, replace

from datetime import datetime, timedelta, timezone

from pathlib import Path, PurePosixPath

from types import SimpleNamespace

from typing import Any, cast

import pytest

import yaml

from symphony.artifacts import ArtifactRecord, CollectResult

from symphony.errors import ConfigValidationError, SymphonyError

from symphony.issue import BlockerRef, Issue

from symphony.orchestrator import contracts

from symphony.orchestrator import core as core_module

from symphony.orchestrator import diagnostics

from symphony.orchestrator import helpers

from symphony.orchestrator import migrations

from symphony.orchestrator import parsing

from symphony.orchestrator import release_contracts

from symphony.orchestrator import release_cycle

from symphony.orchestrator import run_registry

from symphony.orchestrator import scheduler

from symphony.orchestrator.run_registry import (
    ContinuationCheckpoint,
    ReleaseEvidenceIdentity,
    ReleaseGate,
    ReleaseCycleItem,
    RunRecord,
    RunRegistry,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _issue(
    identifier: str = "APP-1",
    *,
    issue_id: str | None = None,
    state: str = "Todo",
    title: str | None = None,
    description: str | None = "",
    labels: tuple[str, ...] = (),
    blocked_by: tuple[BlockerRef, ...] = (),
    agent_kind: str | None = None,
    request: str | None = None,
) -> Issue:
    return Issue(
        id=issue_id or f"id-{identifier}",
        identifier=identifier,
        title=title or f"{identifier} title",
        description=description,
        priority=None,
        state=state,
        labels=labels,
        blocked_by=blocked_by,
        created_at=NOW,
        updated_at=NOW,
        agent_kind=agent_kind,
        request=request,
    )


def _record(**changes: Any) -> RunRecord:
    values: dict[str, Any] = {
        "run_id": "run-1",
        "issue_id": "id-APP-1",
        "identifier": "APP-1",
        "title": "Application",
        "state": "Verify",
        "status": "active",
        "workspace_path": Path("workspace/APP-1"),
        "lease_expires_at": NOW + timedelta(minutes=1),
        "last_progress_at": NOW,
        "started_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return RunRecord(**values)


def _cfg(
    *,
    active: tuple[str, ...] = ("Todo", "In Progress", "Verify", "Document"),
    terminal: tuple[str, ...] = ("Done", "Blocked", "Human Review", "Archive"),
    tracker_kind: str = "file",
) -> Any:
    return SimpleNamespace(
        workflow_path=Path("/repo/WORKFLOW.md"),
        tracker=SimpleNamespace(
            kind=tracker_kind,
            active_states=active,
            terminal_states=terminal,
            archive_state="Archive",
            board_root=Path("/repo/kanban"),
        ),
        agent=SimpleNamespace(
            budget_exhausted_state="",
            auto_triage_actionable_todo=True,
            auto_merge_target_branch="main",
        ),
    )


def _gate(status: str = "pending") -> ReleaseGate:
    return ReleaseGate(
        finalizer_identifier="APP-FINAL",
        verifier_issue_id="id-VERIFY-1",
        verifier_identifier="VERIFY-1",
        expected_contract_sha256="a" * 64,
        cycle_fingerprint="cycle-1",
        approved_fingerprint=None,
        status=status,
        target_branch=None,
        approved_target_sha=None,
        verifier_run_id=None,
        updated_at=NOW,
    )


@dataclass(frozen=True)
class _AgentChoice:
    kind: str

    def kind_for_state(self, _state: str, pin: str | None) -> str:
        return pin or "claude"


@dataclass(frozen=True)
class _AgentChoiceConfig:
    agent: _AgentChoice


class _AlterConnection:
    def __init__(self, message: str) -> None:
        self.message = message

    def execute(self, sql: str) -> Any:
        if sql.startswith("PRAGMA"):
            return SimpleNamespace(fetchall=lambda: [])
        raise sqlite3.OperationalError(self.message)


class _FakeKernel:
    def __init__(self, *, handle: int, exit_ok: bool = True, exit_code: int = 259):
        self.handle = handle
        self.exit_ok = exit_ok
        self.exit_code = exit_code
        self.closed: list[int] = []

    def OpenProcess(self, *_args: Any) -> int:
        return self.handle

    def GetExitCodeProcess(self, _handle: int, pointer: Any) -> bool:
        pointer._obj.value = self.exit_code
        return self.exit_ok

    def CloseHandle(self, handle: int) -> None:
        self.closed.append(handle)


class _SqlFaultConnection:
    """Delegate to SQLite while injecting one deterministic operational fault."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        needle: str,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.connection = connection
        self.needle = needle
        self.error = error or sqlite3.OperationalError("injected database fault")
        self.triggered = False

    @property
    def in_transaction(self) -> bool:
        return self.connection.in_transaction

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        normalized = " ".join(sql.split())
        if not self.triggered and self.needle in normalized:
            self.triggered = True
            raise self.error
        return self.connection.execute(sql, parameters)

    def close(self) -> None:
        self.connection.close()


def _active_registry_run(
    tmp_path: Path, *, identifier: str = "VERIFY-1"
) -> tuple[RunRegistry, Issue, str]:
    registry = RunRegistry(tmp_path / f"{identifier}.db")
    issue = _issue(identifier, state="Verify")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    assert run_id is not None
    return registry, issue, run_id


def _continuation_registry_source(
    tmp_path: Path,
) -> tuple[RunRegistry, Issue, str]:
    registry, issue, run_id = _active_registry_run(tmp_path, identifier="CONTINUE-1")
    assert registry.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=run_id,
        resume_session_id="private-session",
        state=issue.state,
        turn=1,
        now=NOW,
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="shutdown_interrupted",
        now=NOW + timedelta(seconds=1),
    )
    return registry, issue, run_id


def _approved_core_gate(**changes: Any) -> ReleaseGate:
    gate = replace(
        _gate(),
        status="approved",
        generation="generation",
        approved_fingerprint="fingerprint",
        target_branch="main",
        approved_target_sha="b" * 40,
        verifier_run_id="verifier-run",
        finalizer_run_id="finalizer-run",
    )
    return replace(gate, **changes)


def _finalizer_and_verifier() -> tuple[Issue, Issue]:
    verifier = _issue("VERIFY-1", state="Done")
    finalizer = _issue(
        "APP-FINAL",
        state="Document",
        labels=("app-release-finalizer",),
        blocked_by=(BlockerRef(verifier.id, verifier.identifier, verifier.state),),
    )
    return finalizer, verifier


def _configure_finalizer_guard(
    monkeypatch: pytest.MonkeyPatch,
    orchestrator: core_module.Orchestrator,
    *,
    finalizer: Issue | None,
    verifier: Issue | None,
    authority: bool = True,
) -> None:
    def fetch(_cfg: Any, identifier: str) -> Issue | None:
        if finalizer is not None and identifier == finalizer.identifier:
            return finalizer
        if verifier is not None and identifier == verifier.identifier:
            return verifier
        return None

    monkeypatch.setattr(orchestrator, "_tracker_call_fetch_issue_full_by_id", fetch)

    def registry_call(_cfg: Any, op: str, _fn: Any) -> bool:
        if op == "check_verifier_lease":
            return False
        return authority

    monkeypatch.setattr(orchestrator, "_release_registry_call", registry_call)


def _release_entry(issue: Issue, **changes: Any) -> Any:
    values: dict[str, Any] = {
        "issue": issue,
        "known_app_release": True,
        "known_release_cycle_verifier": True,
        "known_app_release_finalizer": False,
        "run_id": "verifier-run",
        "release_gate_finalizer": "APP-FINAL",
        "release_gate_expected_contract_sha256": "a" * 64,
        "release_gate_cycle_fingerprint": "cycle-1",
        "release_gate_generation": "generation",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _lease_entry(issue: Issue) -> Any:
    return SimpleNamespace(
        issue=issue,
        run_id="run-1",
        release_authority_resolved=False,
        known_app_release=False,
        known_release_cycle_verifier=False,
        known_app_release_finalizer=False,
        lease_lost=False,
        agent_pgid=42,
        workspace_path=Path("workspace"),
        retry_attempt=None,
        agent_kind="codex",
        worker_task=None,
        cancelled_at=None,
    )


def _artifact_cfg(tmp_path: Path, *, enabled: bool = True, ttl_days: int = 30) -> Any:
    cfg = _cfg()
    cfg.workflow_path = tmp_path / "WORKFLOW.md"
    cfg.tracker.board_root = tmp_path / "kanban"
    cfg.artifacts = SimpleNamespace(
        enabled=enabled,
        dir=".deliverables",
        ttl_days=ttl_days,
    )
    return cfg


def _artifact_record(name: str = "report final.pdf") -> ArtifactRecord:
    return ArtifactRecord(
        name=name,
        title="Release report",
        summary="Verified output",
        content_type="application/pdf",
        byte_size=2_048,
        sha256="a" * 64,
        collected_at="2026-08-24T12:00:00Z",
        run_id="run-1",
        turn=2,
    )


class _TicketClient:
    def __init__(self, issues: list[Issue | None]) -> None:
        self.issues = iter(issues)
        self.closed = False
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def fetch_issue_full_by_id(self, _identifier: str) -> Issue | None:
        return next(self.issues)

    def update_fields(self, identifier: str, **fields: Any) -> None:
        self.updates.append((identifier, fields))

    def close(self) -> None:
        self.closed = True


def _cycle_validation(
    *, with_failure: bool
) -> release_contracts.ReleaseValidationResult:
    failures: tuple[release_contracts.RepairableFailure, ...] = ()
    if with_failure:
        failures = (
            release_contracts.RepairableFailure(
                check_id="visual-check",
                repair_group="ui",
                description="Visual quality",
                expected="aligned",
                actual="misaligned",
                repro="open the page",
            ),
        )
    return release_contracts.ReleaseValidationResult(
        passed=False,
        evidence_errors=(),
        repairable_failures=failures,
        target_branch="main",
        target_sha="b" * 40,
        contract_sha256="c" * 64,
        fingerprint="fingerprint",
        finalizer_ticket="APP-FINAL",
        note_text="release did not pass",
    )


class _CycleRegistry:
    def __init__(
        self,
        *,
        repair: ReleaseCycleItem | None = None,
        verifier: ReleaseCycleItem | None = None,
    ) -> None:
        self.repair = repair
        self.verifier = verifier
        self.closed = False

    def get_release_cycle_item(
        self, *, item_role: str, **_kwargs: Any
    ) -> ReleaseCycleItem | None:
        return self.repair if item_role == "repair" else self.verifier

    def record_release_cycle_item(
        self, *, item_role: str, item_key: str, issue: Issue, **_kwargs: Any
    ) -> ReleaseCycleItem:
        item = ReleaseCycleItem(
            finalizer_identifier="APP-FINAL",
            cycle_fingerprint="fingerprint",
            item_role=item_role,
            item_key=item_key,
            issue_id=issue.id,
            identifier=issue.identifier,
            recorded_at=NOW,
            updated_at=NOW,
        )
        if item_role == "repair":
            self.repair = item
        else:
            self.verifier = item
        return item

    def reserve_release_cycle_item(
        self, *, item_role: str, item_key: str, identifier: str, **_kwargs: Any
    ) -> ReleaseCycleItem:
        item = ReleaseCycleItem(
            finalizer_identifier="APP-FINAL",
            cycle_fingerprint="fingerprint",
            item_role=item_role,
            item_key=item_key,
            issue_id=f"reserved:{identifier}",
            identifier=identifier,
            recorded_at=NOW,
            updated_at=NOW,
        )
        if item_role == "repair":
            self.repair = item
        else:
            self.verifier = item
        return item

    def close(self) -> None:
        self.closed = True


def _cycle_item(role: str, identifier: str, key: str) -> ReleaseCycleItem:
    return ReleaseCycleItem(
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="fingerprint",
        item_role=role,
        item_key=key,
        issue_id=f"id-{identifier}",
        identifier=identifier,
        recorded_at=NOW,
        updated_at=NOW,
    )


def _run_cycle_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: Any,
    registry: _CycleRegistry,
    source: Issue,
    validation: release_contracts.ReleaseValidationResult,
) -> release_cycle.ReleaseCycleWriteResult:
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: client)
    monkeypatch.setattr(release_cycle, "RunRegistry", lambda _path: registry)
    result = release_cycle.ReleaseCycleService(_cfg()).reconcile(
        source, validation, "codex"
    )
    assert registry.closed
    return result


def _valid_cycle_verifier(source: Issue) -> Issue:
    validation = _cycle_validation(with_failure=False)
    return _issue(
        "VERIFY-NEW",
        state="Verify",
        description=release_cycle.release_verifier_description(
            source=source, result=validation
        ),
        labels=(
            "app-release",
            "release-cycle-verifier",
            "release-fingerprint-fingerprint",
            f"release-contract-sha256-{'c' * 64}",
            "release-finalizer-app-final",
        ),
        agent_kind="codex",
        request=source.request,
    )


def _configure_tick_until_validation(
    orchestrator: core_module.Orchestrator,
    cfg: Any,
    *,
    reload_error: Exception | None = None,
    registry: Any = None,
) -> list[str]:
    subject = cast(Any, orchestrator)
    observed: list[str] = []
    subject._schedule_snapshot = {}
    subject._workflow_state = SimpleNamespace(
        reload=lambda: (None, reload_error) if reload_error else (cfg, None),
        current=lambda: cfg,
    )
    subject._workspace_manager = None
    subject._run_registry = registry
    subject._ensure_run_registry_async = lambda _cfg: asyncio.sleep(0)
    subject._heartbeat_running_leases = lambda: None
    subject._reclaim_dead_owner_runs_async = lambda _registry: asyncio.sleep(0)
    subject._registry_guard = lambda *_a: 1
    subject._reconcile_running = lambda _cfg: asyncio.sleep(0)
    subject._in_flight_ids = lambda: set()
    subject._dispatch_state = SimpleNamespace(
        prune_claims_not_in=lambda _ids: set(), running={}, retry={}
    )
    subject._claim_released_at = {}
    subject._notify_observers = lambda: asyncio.sleep(
        0, result=observed.append("notified")
    )
    return observed


def _worker_preflight_entry(issue: Issue) -> Any:
    return SimpleNamespace(
        issue=issue,
        release_authority_resolved=False,
        known_app_release=False,
        known_release_cycle_verifier=False,
        known_app_release_finalizer=False,
        release_gate_finalizer="",
        release_gate_expected_contract_sha256="",
        release_gate_cycle_fingerprint="",
        release_gate_generation="",
        release_finalizer_rewind_state="",
        resume_session_id=None,
        worker_task=None,
        exit_started_at=None,
        workspace_path=Path("/"),
    )


def _runtime_entry(issue: Issue, **changes: Any) -> Any:
    entry = core_module.RunningEntry(
        issue=issue,
        started_at=NOW,
        retry_attempt=None,
        worker_task=None,
        workspace_path=Path(f"workspace/{issue.identifier}"),
        agent_kind="codex",
        run_id="run-1",
        release_authority_resolved=True,
    )
    for name, value in changes.items():
        setattr(entry, name, value)
    return entry


def _exit_cfg() -> Any:
    cfg = _cfg()
    cfg.agent.auto_commit_on_done = False
    cfg.agent.auto_merge_push_target = False
    cfg.agent.max_total_turns = 100
    cfg.agent.max_turns = 5
    cfg.agent.no_stage_change_action = "block"
    cfg.agent.max_total_tokens = 0
    cfg.agent.max_total_tokens_by_state = {}
    return cfg


def _configure_exit_subject(
    orchestrator: core_module.Orchestrator,
    entry: Any,
    cfg: Any,
) -> tuple[Any, list[str]]:
    subject = cast(Any, orchestrator)
    issue_id = entry.issue.id
    notifications: list[str] = []
    subject._dispatch_state = SimpleNamespace(
        running={issue_id: entry},
        claimed=set(),
        retry={},
        persisted_retry_attempts={issue_id: 2},
        turn_budget_exhausted=set(),
        cancel_pending_retry=lambda target: notifications.append(f"cancel:{target}"),
    )
    subject._app_release_transition_locks = {}
    subject._claim_released_at = {}
    subject._pause_events = {}
    subject._paused_issue_ids = set()
    subject._pause_reasons = {}
    subject._totals = SimpleNamespace(seconds_running=0.0)
    subject._issue_debug = {}
    subject._stats = None
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    subject._workspace_manager = None
    subject._finish_run_lease = lambda *_a, **_k: None
    subject._clear_issue_flags = lambda *_a, **_k: None
    subject._set_issue_flags = lambda *_a, **_k: None
    subject._notify_observers = lambda: asyncio.sleep(
        0, result=notifications.append("notified")
    )
    return subject, notifications


def _release_validation(
    *,
    passed: bool,
    evidence_errors: tuple[str, ...] = (),
    with_failure: bool = False,
    contract_sha256: str = "a" * 64,
    finalizer_ticket: str = "APP-FINAL",
    fingerprint: str = "fingerprint",
    target_branch: str = "main",
    target_sha: str = "b" * 40,
) -> release_contracts.ReleaseValidationResult:
    base = _cycle_validation(with_failure=with_failure)
    return replace(
        base,
        passed=passed,
        evidence_errors=evidence_errors,
        contract_sha256=contract_sha256,
        finalizer_ticket=finalizer_ticket,
        fingerprint=fingerprint,
        target_branch=target_branch,
        target_sha=target_sha,
    )


class _WorkerLoopBackend:
    def __init__(self, *, resume_error: Exception | None = None) -> None:
        self.resume_error = resume_error
        self.pid = None
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")

    async def initialize(self) -> None:
        self.calls.append("initialize")

    async def start_session(self, **_kwargs: Any) -> None:
        self.calls.append("start_session")

    async def resume_session(self, _session_id: str) -> bool:
        self.calls.append("resume_session")
        if self.resume_error is not None:
            raise self.resume_error
        return True

    async def run_turn(self, **_kwargs: Any) -> None:
        self.calls.append("run_turn")

    async def stop(self) -> None:
        self.calls.append("stop")


class _WorkerLoopManager:
    def __init__(self, path: Path, *, fail_second_before: bool = False) -> None:
        self.path = path
        self.fail_second_before = fail_second_before
        self.before_calls = 0
        self.after_calls = 0

    async def create_or_reuse(self, _identifier: str) -> Any:
        return SimpleNamespace(path=self.path)

    async def before_run(self, _path: Path) -> None:
        self.before_calls += 1
        if self.fail_second_before and self.before_calls == 2:
            raise OSError("second-turn hook failed")

    async def after_run_best_effort(self, _path: Path) -> None:
        self.after_calls += 1


def _configure_worker_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    issue: Issue,
    entry: Any,
    backend: _WorkerLoopBackend,
    manager: _WorkerLoopManager | None = None,
) -> tuple[core_module.Orchestrator, Any, list[tuple[str, str | None]], list[str]]:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path,
        tracker=SimpleNamespace(
            kind="file",
            active_states=("Todo", "Verify", "Document"),
            terminal_states=("Done", "Blocked", "Human Review"),
            board_root=tmp_path / "kanban",
        ),
        agent=SimpleNamespace(
            kind="codex",
            max_total_turns=20,
            max_attempts=3,
            auto_merge_on_done=False,
            compact_issue_context=False,
            max_turns=5,
            max_state_turns=0,
            crash_continuation=False,
        ),
        tui=SimpleNamespace(language="en"),
        prompt_template_for_state=lambda _state: "work on {{ issue.identifier }}",
    )
    subject._dispatch_state = SimpleNamespace(
        running={issue.id: entry}, entry_foreign_to=lambda *_a: False
    )
    subject._workspace_manager = manager or _WorkerLoopManager(tmp_path)
    subject._pause_events = {}
    subject._issue_debug = {}
    subject._run_registry = None
    subject._stopping = False
    exits: list[tuple[str, str | None]] = []
    events: list[str] = []

    async def worker_exit(
        _issue_id: str, outcome: str, error: str | None, **_kwargs: Any
    ) -> None:
        exits.append((outcome, error))

    monkeypatch.setattr(
        core_module, "_config_for_issue_agent", lambda _cfg, _issue: cfg
    )
    monkeypatch.setattr(core_module, "render_skill_block", lambda *_a: "")
    monkeypatch.setattr(
        core_module, "build_first_turn_prompt", lambda **_k: ("first prompt", {})
    )
    monkeypatch.setattr(orchestrator, "_build_agent_backend", lambda _init: backend)
    monkeypatch.setattr(orchestrator, "_apply_dispatch_env", lambda **_k: None)
    monkeypatch.setattr(orchestrator, "_sync_backend_agent_pid", lambda *_a: None)
    monkeypatch.setattr(orchestrator, "_token_ema_for_state", lambda _state: 0)
    monkeypatch.setattr(orchestrator, "_token_budget_for_state", lambda *_a: 0)
    monkeypatch.setattr(orchestrator, "_ticket_prompt_path", lambda *_a: None)
    monkeypatch.setattr(orchestrator, "_prompt_artifacts_dir", lambda *_a: "")
    monkeypatch.setattr(
        orchestrator, "_collect_ticket_artifacts", lambda *_a, **_k: asyncio.sleep(0)
    )
    monkeypatch.setattr(
        orchestrator,
        "_append_run_event",
        lambda _entry, event_type, *_a: events.append(event_type),
    )
    monkeypatch.setattr(orchestrator, "_max_state_turns_for_state", lambda *_a: 0)
    monkeypatch.setattr(orchestrator, "_record_stats_transition", lambda *_a: None)
    monkeypatch.setattr(orchestrator, "_on_worker_exit", worker_exit)
    return orchestrator, cfg, exits, events


__all__ = [name for name in globals() if not name.startswith("__")]
