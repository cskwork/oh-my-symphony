"""Host lifecycle integration for app-release verifier transitions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import shutil
import subprocess
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from symphony.errors import SymphonyError
from symphony.issue import Issue
from symphony.orchestrator.core import Orchestrator, _ReleaseCycleWriteResult
from symphony.orchestrator.release_contracts import (
    ReleaseValidationResult,
    validate_release_contract,
)
from symphony.orchestrator.release_cycle import (
    ReleaseCycleService,
    release_fingerprint_label,
    release_group_label,
    release_repair_description,
    release_ticket_version_token,
)
from symphony.orchestrator.run_registry import (
    ReleaseGate,
    RunRegistry,
    registry_path_for_workflow,
)
from symphony.trackers.file import FileBoardTracker, parse_ticket_file

from tests.test_orchestrator_contract_integration import (
    _TicketMutatingBackend,
    _install_file_tracker_backend,
    _make_file_tracker_config,
    _orch,
    _seed_running_entry,
)
from tests.test_release_contracts import _git, _write_valid_release
from tests._win_skips import requires_symlink_privilege


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.test")
    # Pin the repo to the line-ending behavior CI runs with: a host-wide
    # `core.autocrlf=true` (common on Windows) rewrites blobs/worktrees and
    # breaks the byte-exact contract comparisons these tests assert on.
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "app.txt").write_text("v1\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        ".symphony/\nkanban/\n", encoding="utf-8"
    )
    _git(repo, "add", "app.txt", ".gitignore")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "branch", "symphony/APP-1")
    _write_valid_release(repo)
    return repo


def _setup_board(repo: Path):
    board = repo / "kanban"
    cfg = _make_file_tracker_config(
        board_root=board,
        active_states=(
            "Intake",
            "Research",
            "Plan",
            "Review",
            "Build",
            "QA",
            "Verify",
            "Document",
        ),
        max_turns=2,
    )
    cfg = replace(
        cfg,
        workflow_path=repo / "WORKFLOW.md",
        agent=replace(
            cfg.agent,
            stage_contracts="off",
            auto_merge_target_branch="main",
            auto_commit_on_done=False,
            auto_merge_on_done=False,
        ),
    )
    tracker = FileBoardTracker(cfg.tracker)
    ticket_path = tracker.create(
        identifier="VERIFY-1",
        title="Application release verifier",
        state="Verify",
        priority=2,
        labels=["App-Release"],
        description="Verifier body.\n",
        agent_kind="codex",
        request="APP-REQUEST",
    )
    tracker.create(
        identifier="UNRELATED-1",
        title="Unrelated delivery dependency",
        state="Done",
        description="Independent blocker.\n",
        request="APP-REQUEST",
    )
    tracker.create(
        identifier="APP-FINAL",
        title="Application release finalizer",
        state="Document",
        labels=["app-release-finalizer"],
        description="Final delivery ticket.\n",
        blocked_by=["VERIFY-1", "UNRELATED-1"],
        request="APP-REQUEST",
    )
    issue = tracker.fetch_issue_full_by_id("VERIFY-1")
    assert issue is not None
    tracker.close()
    return cfg, ticket_path, issue


def _release_worker_workspace(repo: Path) -> Path:
    """Create a worker worktree with the host board mounted exactly."""
    worker = repo.parent / "release-worker"
    _git(
        repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "symphony/RELEASE-WORKSPACE",
        str(worker),
        "main",
    )
    diff = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    if diff:
        subprocess.run(
            ["git", "apply", "--binary"],
            cwd=worker,
            input=diff,
            check=True,
        )
    shutil.copytree(repo / "docs", worker / "docs", dirs_exist_ok=True)
    (worker / "kanban").symlink_to(repo / "kanban", target_is_directory=True)
    return worker


def _run_verify_transition(
    *,
    repo: Path,
    cfg,
    ticket_path: Path,
    issue: Issue,
    monkeypatch: pytest.MonkeyPatch,
    transition_state: str = "Document",
) -> Orchestrator:
    _install_file_tracker_backend(
        monkeypatch,
        ticket_path=ticket_path,
        transitions=[(transition_state, issue.description or "")],
    )
    worker = _release_worker_workspace(repo)
    orchestrator = _orch(worker)
    authority = orchestrator._prepare_release_dispatch(issue, cfg)
    assert authority.gate is not None
    registry = orchestrator._run_registry
    assert registry is not None
    run_id = registry.acquire_run(
        authority.issue,
        workspace_path=worker,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
    )
    assert run_id is not None
    assert registry.bind_release_verifier_run(
        gate=authority.gate,
        verifier_run_id=run_id,
    )
    _seed_running_entry(orchestrator, authority.issue, worker)
    running = orchestrator._running[issue.id]
    running.run_id = run_id
    running.known_app_release = True
    running.known_release_cycle_verifier = True
    running.release_gate_finalizer = authority.gate.finalizer_identifier
    running.release_gate_expected_contract_sha256 = (
        authority.gate.expected_contract_sha256
    )
    running.release_gate_cycle_fingerprint = authority.gate.cycle_fingerprint
    running.release_gate_generation = authority.gate.generation
    running.release_authority_resolved = True
    asyncio.run(
        orchestrator._run_agent_attempt(authority.issue, attempt=None, cfg=cfg)
    )
    return orchestrator


def _mutate_evidence(repo: Path, mutate) -> None:
    path = repo / "docs" / "VERIFY-1" / "qa" / "release-evidence.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    mutate(evidence)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def _sync_native_statuses(repo: Path) -> None:
    qa = repo / "docs" / "VERIFY-1" / "qa"
    evidence_path = qa / "release-evidence.json"
    native_path = qa / "native-results.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    native = json.loads(native_path.read_text(encoding="utf-8"))
    statuses = {item["id"]: item["status"] for item in evidence["checks"]}
    for check in native["checks"]:
        check["status"] = statuses[check["id"]]
    native_path.write_text(json.dumps(native, indent=2) + "\n", encoding="utf-8")
    evidence["runner"]["results_sha256"] = hashlib.sha256(
        native_path.read_bytes()
    ).hexdigest()
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def _required_release_gate(orchestrator: Orchestrator) -> ReleaseGate:
    registry = orchestrator._run_registry
    assert registry is not None
    gate = registry.get_release_gate("APP-FINAL")
    assert gate is not None
    return gate


def _seed_active_release_verifier(
    *,
    orchestrator: Orchestrator,
    cfg,
    issue: Issue,
    workspace_path: Path,
) -> tuple[ReleaseGate, str]:
    authority = orchestrator._prepare_release_dispatch(issue, cfg)
    assert authority.gate is not None
    registry = orchestrator._run_registry
    assert registry is not None
    run_id = registry.acquire_run(
        authority.issue,
        workspace_path=workspace_path,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
    )
    assert run_id is not None
    assert registry.bind_release_verifier_run(
        gate=authority.gate,
        verifier_run_id=run_id,
    )
    _seed_running_entry(orchestrator, authority.issue, workspace_path)
    running = orchestrator._running[issue.id]
    running.run_id = run_id
    running.known_app_release = True
    running.known_release_cycle_verifier = True
    running.release_gate_finalizer = authority.gate.finalizer_identifier
    running.release_gate_expected_contract_sha256 = (
        authority.gate.expected_contract_sha256
    )
    running.release_gate_cycle_fingerprint = authority.gate.cycle_fingerprint
    running.release_gate_generation = authority.gate.generation
    running.release_authority_resolved = True
    return authority.gate, run_id


def _approve_current_release_gate(
    *,
    orchestrator: Orchestrator,
    cfg,
    gate: ReleaseGate,
) -> tuple[ReleaseGate, ReleaseValidationResult]:
    validation = validate_release_contract(
        workspace_root=cfg.workflow_path.parent,
        repository_root=cfg.workflow_path.parent,
        verifier_ticket=gate.verifier_identifier,
        configured_target_branch=cfg.agent.auto_merge_target_branch,
    )
    assert validation.passed
    registry = orchestrator._run_registry
    assert registry is not None
    tracker = FileBoardTracker(cfg.tracker)
    verifier = tracker.fetch_issue_full_by_id(gate.verifier_identifier)
    tracker.close()
    assert verifier is not None
    verifier_run_id = registry.acquire_run(
        verifier,
        workspace_path=cfg.workflow_path.parent,
        attempt=None,
        attempt_kind="release-test-approval",
        agent_kind="codex",
    )
    assert verifier_run_id is not None
    assert registry.bind_release_verifier_run(
        gate=gate,
        verifier_run_id=verifier_run_id,
    )
    assert registry.approve_release_gate(
        finalizer_identifier=gate.finalizer_identifier,
        verifier_issue_id=gate.verifier_issue_id,
        verifier_identifier=gate.verifier_identifier,
        expected_contract_sha256=gate.expected_contract_sha256,
        expected_cycle_fingerprint=gate.cycle_fingerprint,
        expected_generation=gate.generation,
        approved_fingerprint=validation.fingerprint,
        target_branch=validation.target_branch,
        target_sha=validation.target_sha,
        verifier_run_id=verifier_run_id,
    )
    approved = registry.get_release_gate(gate.finalizer_identifier)
    assert approved is not None
    assert registry.complete_run(
        issue_id=gate.verifier_issue_id,
        run_id=verifier_run_id,
        status="release-test-approved",
    )
    return approved, validation


def _complete_current_release_finalizer(
    *,
    orchestrator: Orchestrator,
    cfg,
    approved: ReleaseGate,
) -> tuple[ReleaseGate, Issue, str]:
    registry = orchestrator._run_registry
    assert registry is not None
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(approved.verifier_identifier, state="Done")
    finalizer = tracker.fetch_issue_full_by_id(approved.finalizer_identifier)
    tracker.close()
    assert finalizer is not None
    finalizer_run_id = registry.acquire_run(
        finalizer,
        workspace_path=cfg.workflow_path.parent,
        attempt=None,
        attempt_kind="release-finalizer",
        agent_kind="codex",
    )
    assert finalizer_run_id is not None
    assert registry.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=finalizer_run_id,
    )
    bound = registry.get_release_gate(approved.finalizer_identifier)
    assert bound is not None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(finalizer.identifier, state="Done")
    terminal_finalizer = tracker.fetch_issue_full_by_id(finalizer.identifier)
    tracker.close()
    assert terminal_finalizer is not None
    completion_token = release_ticket_version_token(cfg, finalizer.identifier)
    assert registry.mark_release_finalizer_completed(
        gate=bound,
        finalizer_issue_id=finalizer.id,
        completion_token=completion_token,
    )
    completed = registry.get_release_gate(approved.finalizer_identifier)
    assert completed is not None
    assert completed.finalizer_completion_token == completion_token
    assert registry.complete_run(
        issue_id=finalizer.id,
        run_id=finalizer_run_id,
        status="normal",
    )
    return completed, terminal_finalizer, completion_token


@requires_symlink_privilege
def test_app_release_gate_is_active_on_deep_custom_board_with_stage_contracts_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)

    _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
    )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Document"
    assert "## App Release Gate Failure" not in body


@requires_symlink_privilege
def test_evidence_error_rewinds_without_creating_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    _mutate_evidence(repo, lambda evidence: evidence.__setitem__("target_sha", "0" * 40))

    _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
    )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Verify"
    assert "## App Release Gate Failure" in body
    assert not list((repo / "kanban").glob("QUALITY-*.md"))
    assert not list((repo / "kanban").glob("RELEASE-VERIFY-*.md"))


def test_atomic_release_rewind_cannot_persist_note_without_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(issue.identifier, state="Document")
    advanced = tracker.fetch_issue_full_by_id(issue.identifier)
    tracker.close()
    assert advanced is not None

    def fail_update_fields(self, identifier: str, **fields: object) -> Path:
        del self, identifier, fields
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(FileBoardTracker, "update_fields", fail_update_fields)

    with pytest.raises(OSError, match="atomic replace failure"):
        ReleaseCycleService(cfg).rewind_transition(
            issue=advanced,
            producing_state="Verify",
            note_body="stale release evidence",
        )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Document"
    assert "## App Release Gate Failure" not in body


@requires_symlink_privilege
def test_stale_evidence_verify_to_terminal_is_gated_before_worker_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    _mutate_evidence(repo, lambda evidence: evidence.__setitem__("target_sha", "0" * 40))
    _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
        transition_state="Done",
    )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Verify"
    assert "## App Release Gate Failure" in body


@requires_symlink_privilege
def test_initial_app_release_signal_survives_persisted_label_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    _mutate_evidence(repo, lambda evidence: evidence.__setitem__("target_sha", "0" * 40))
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", labels=[])
    tracker.close()

    _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
    )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Verify"
    assert "## App Release Gate Failure" in body


@requires_symlink_privilege
def test_verify_to_active_is_gated_before_total_turn_budget_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    cfg = replace(cfg, agent=replace(cfg.agent, max_total_turns=1))
    _mutate_evidence(repo, lambda evidence: evidence.__setitem__("target_sha", "0" * 40))

    _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
    )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Verify"
    assert "## App Release Gate Failure" in body


@requires_symlink_privilege
def test_red_checks_group_repairs_create_fresh_verifier_and_link_finalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)

    _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
    )

    tracker = FileBoardTracker(cfg.tracker)
    issues = tracker.scan_all()
    source = next(item for item in issues if item.identifier == "VERIFY-1")
    repairs = [item for item in issues if "quality-fix" in item.labels]
    verifiers = [
        item
        for item in issues
        if item.identifier != source.identifier
        and "release-cycle-verifier" in item.labels
    ]
    finalizer = next(item for item in issues if item.identifier == "APP-FINAL")
    tracker.close()

    assert source.state == "Document"
    assert len(repairs) == 1
    assert repairs[0].state == "Build"
    assert repairs[0].agent_kind == "codex"
    assert "feature-check" in (repairs[0].description or "")
    assert "menu control is inert" in (repairs[0].description or "")
    assert len(verifiers) == 1
    assert verifiers[0].state == "Verify"
    assert any(
        label.startswith("release-contract-sha256-") for label in verifiers[0].labels
    )
    assert {blocker.identifier for blocker in verifiers[0].blocked_by} == {
        repairs[0].identifier
    }
    assert {blocker.identifier for blocker in finalizer.blocked_by} == {
        verifiers[0].identifier,
        "UNRELATED-1",
    }
    assert "VERIFY-1" not in {
        blocker.identifier for blocker in finalizer.blocked_by
    }


@requires_symlink_privilege
def test_concurrent_worker_and_reconcile_accept_one_exact_green_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    worker = _release_worker_workspace(repo)
    orchestrator = _orch(worker)
    _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=verifier,
        workspace_path=worker,
    )
    entry = orchestrator._running[verifier.id]
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(verifier.identifier, state="Done")
    terminal_verifier = tracker.fetch_issue_full_by_id(verifier.identifier)
    tracker.close()
    assert terminal_verifier is not None

    validation_started = threading.Event()
    allow_validation = threading.Event()
    second_lock_requested = threading.Event()
    validation_calls: list[str] = []
    lock_requests = 0
    real_transition_lock = orchestrator._app_release_transition_lock

    def observe_transition_lock(issue_id: str) -> asyncio.Lock | None:
        nonlocal lock_requests
        lock_requests += 1
        if lock_requests == 2:
            second_lock_requested.set()
        return real_transition_lock(issue_id)

    def blocking_validation(**kwargs: object) -> ReleaseValidationResult:
        validation_calls.append(str(kwargs["verifier_ticket"]))
        if len(validation_calls) == 1:
            validation_started.set()
            assert allow_validation.wait(timeout=5)
        return validate_release_contract(**kwargs)

    monkeypatch.setattr(
        orchestrator,
        "_app_release_transition_lock",
        observe_transition_lock,
    )
    monkeypatch.setattr(
        "symphony.orchestrator.core.validate_release_contract",
        blocking_validation,
    )

    async def exercise_race() -> tuple[Issue, bool]:
        async def worker_post_turn() -> tuple[Issue, bool]:
            result = await orchestrator._enforce_app_release_transition(
                cfg=cfg,
                issue=terminal_verifier,
                workspace_path=worker,
                producing_state="Verify",
                known_app_release=True,
            )
            entry.issue = result[0]
            return result

        worker_task = asyncio.create_task(worker_post_turn())
        assert await asyncio.to_thread(validation_started.wait, 2)
        reconcile_task = asyncio.create_task(
            orchestrator._reconcile_one(
                terminal_verifier,
                entry,
                cfg,
                active={state.lower() for state in cfg.tracker.active_states},
                terminal={state.lower() for state in cfg.tracker.terminal_states},
                now=datetime.now(timezone.utc),
                recent_grace_s=3600,
            )
        )
        assert await asyncio.to_thread(second_lock_requested.wait, 2)
        assert validation_calls == [verifier.identifier]
        assert not reconcile_task.done()
        allow_validation.set()
        worker_result, _ = await asyncio.wait_for(
            asyncio.gather(worker_task, reconcile_task),
            timeout=5,
        )
        return worker_result

    transitioned, rewound = asyncio.run(exercise_race())

    assert not rewound
    assert transitioned.state == "Done"
    assert validation_calls == [verifier.identifier, verifier.identifier]
    gate = _required_release_gate(orchestrator)
    assert gate.status == "approved"
    assert gate.verifier_run_id == entry.run_id
    tracker = FileBoardTracker(cfg.tracker)
    persisted = tracker.fetch_issue_full_by_id(verifier.identifier)
    tracker.close()
    assert persisted is not None and persisted.state == "Done"


@requires_symlink_privilege
def test_concurrent_worker_and_reconcile_create_one_red_release_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    worker = _release_worker_workspace(repo)
    orchestrator = _orch(worker)
    original_gate, _run_id = _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=verifier,
        workspace_path=worker,
    )
    entry = orchestrator._running[verifier.id]
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(verifier.identifier, state="Done")
    terminal_verifier = tracker.fetch_issue_full_by_id(verifier.identifier)
    tracker.close()
    assert terminal_verifier is not None

    validation_started = threading.Event()
    allow_validation = threading.Event()
    second_lock_requested = threading.Event()
    validation_calls: list[str] = []
    lifecycle_calls: list[str] = []
    lock_requests = 0
    real_transition_lock = orchestrator._app_release_transition_lock
    real_reconcile_cycle = Orchestrator._tracker_call_reconcile_release_cycle

    def observe_transition_lock(issue_id: str) -> asyncio.Lock | None:
        nonlocal lock_requests
        lock_requests += 1
        if lock_requests == 2:
            second_lock_requested.set()
        return real_transition_lock(issue_id)

    def blocking_validation(**kwargs: object) -> ReleaseValidationResult:
        validation_calls.append(str(kwargs["verifier_ticket"]))
        if len(validation_calls) == 1:
            validation_started.set()
            assert allow_validation.wait(timeout=5)
        return validate_release_contract(**kwargs)

    def count_reconcile_cycle(*args: object, **kwargs: object):
        lifecycle_calls.append(args[1].identifier)
        return real_reconcile_cycle(*args, **kwargs)

    monkeypatch.setattr(
        orchestrator,
        "_app_release_transition_lock",
        observe_transition_lock,
    )
    monkeypatch.setattr(
        "symphony.orchestrator.core.validate_release_contract",
        blocking_validation,
    )
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_reconcile_release_cycle",
        staticmethod(count_reconcile_cycle),
    )

    async def exercise_race() -> tuple[Issue, bool]:
        async def worker_post_turn() -> tuple[Issue, bool]:
            result = await orchestrator._enforce_app_release_transition(
                cfg=cfg,
                issue=terminal_verifier,
                workspace_path=worker,
                producing_state="Verify",
                known_app_release=True,
            )
            entry.issue = result[0]
            return result

        worker_task = asyncio.create_task(worker_post_turn())
        assert await asyncio.to_thread(validation_started.wait, 2)
        reconcile_task = asyncio.create_task(
            orchestrator._reconcile_one(
                terminal_verifier,
                entry,
                cfg,
                active={state.lower() for state in cfg.tracker.active_states},
                terminal={state.lower() for state in cfg.tracker.terminal_states},
                now=datetime.now(timezone.utc),
                recent_grace_s=3600,
            )
        )
        assert await asyncio.to_thread(second_lock_requested.wait, 2)
        assert validation_calls == [verifier.identifier]
        assert not reconcile_task.done()
        allow_validation.set()
        worker_result, _ = await asyncio.wait_for(
            asyncio.gather(worker_task, reconcile_task),
            timeout=5,
        )
        return worker_result

    transitioned, rewound = asyncio.run(exercise_race())

    assert not rewound
    assert transitioned.state == "Done"
    assert validation_calls == [verifier.identifier]
    assert lifecycle_calls == [verifier.identifier]
    registry = orchestrator._run_registry
    assert registry is not None
    retired = registry.get_release_evidence_identity(verifier.identifier)
    assert retired is not None
    assert retired.retired
    assert retired.cycle_generation == original_gate.generation
    gate = _required_release_gate(orchestrator)
    assert gate.status == "pending"
    assert gate.verifier_identifier != verifier.identifier
    tracker = FileBoardTracker(cfg.tracker)
    issues = tracker.scan_all()
    tracker.close()
    assert len([item for item in issues if "quality-fix" in item.labels]) == 1
    assert len(
        [
            item
            for item in issues
            if item.identifier != verifier.identifier
            and "release-cycle-verifier" in item.labels
        ]
    ) == 1
    assert entry.release_verifier_handoff_complete

    # A serialized loser can observe the winner's retired identity, but that
    # readback alone must not grant the ephemeral completion outcome.
    entry.release_verifier_handoff_complete = False
    loser_issue, loser_rewound = asyncio.run(
        orchestrator._enforce_app_release_transition(
            cfg=cfg,
            issue=terminal_verifier,
            workspace_path=worker,
            producing_state="Verify",
            known_app_release=True,
            running_entry=entry,
        )
    )
    assert loser_issue.state == "Done"
    assert not loser_rewound
    assert not entry.release_verifier_handoff_complete


def test_pending_gate_callback_uses_worker_owned_registry_before_relink(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    validation = validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket=issue.identifier,
        configured_target_branch="main",
    )
    registry_path = registry_path_for_workflow(cfg.workflow_path)
    owner_registry = RunRegistry(registry_path)
    # Open the owner connection on this thread so accidentally closing over
    # it in the worker callback deterministically raises ProgrammingError.
    assert owner_registry.get_release_gate("APP-FINAL") is None

    def persist_pending(verifier: Issue) -> None:
        tracker = FileBoardTracker(cfg.tracker)
        try:
            finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
        finally:
            tracker.close()
        assert finalizer is not None
        assert {item.identifier for item in finalizer.blocked_by} == {
            "VERIFY-1",
            "UNRELATED-1",
        }
        callback_registry = RunRegistry(registry_path)
        try:
            callback_registry.replace_pending_release_gate(
                ReleaseGate(
                    finalizer_identifier="APP-FINAL",
                    verifier_issue_id=verifier.id,
                    verifier_identifier=verifier.identifier,
                    expected_contract_sha256=validation.contract_sha256,
                    cycle_fingerprint=validation.fingerprint,
                    approved_fingerprint=None,
                    status="pending",
                    target_branch=None,
                    approved_target_sha=None,
                    verifier_run_id=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        finally:
            callback_registry.close()

    result = asyncio.run(
        asyncio.to_thread(
            Orchestrator._tracker_call_reconcile_release_cycle,
            cfg,
            issue,
            validation,
            "codex",
            before_finalizer_relink=persist_pending,
        )
    )

    gate = owner_registry.get_release_gate("APP-FINAL")
    owner_registry.close()
    assert result.passed and gate is not None
    assert gate.status == "pending"
    assert gate.verifier_identifier == result.verifier_identifier


def test_finalizer_relink_preserves_other_release_lineage_blocker(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    validation = validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    tracker = FileBoardTracker(cfg.tracker)
    tracker.create(
        identifier="OTHER-FINAL",
        title="Other release finalizer",
        state="Document",
        labels=["app-release-finalizer"],
        description="Other finalizer.\n",
    )
    tracker.create(
        identifier="OTHER-VERIFY",
        title="Other release verifier",
        state="Verify",
        labels=[
            "app-release",
            "release-cycle-verifier",
            "release-finalizer-other-final",
        ],
        description="Other verifier.\n",
    )
    tracker.update_fields(
        "APP-FINAL",
        blocked_by=["VERIFY-1", "UNRELATED-1", "OTHER-VERIFY"],
    )
    tracker.update_fields("OTHER-FINAL", blocked_by=["OTHER-VERIFY"])
    tracker.close()

    result = Orchestrator._tracker_call_reconcile_release_cycle(
        cfg, issue, validation, "codex"
    )

    tracker = FileBoardTracker(cfg.tracker)
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    other_finalizer = tracker.fetch_issue_full_by_id("OTHER-FINAL")
    tracker.close()
    assert result.passed and finalizer is not None and other_finalizer is not None
    assert {blocker.identifier for blocker in finalizer.blocked_by} == {
        "UNRELATED-1",
        "OTHER-VERIFY",
        result.verifier_identifier,
    }
    assert {blocker.identifier for blocker in other_finalizer.blocked_by} == {
        "OTHER-VERIFY"
    }


@requires_symlink_privilege
def test_host_authority_restores_worker_changed_contract_hash_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(
        "VERIFY-1",
        labels=[
            "app-release",
            "release-cycle-verifier",
            "release-contract-sha256-" + "0" * 64,
        ],
    )
    issue = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert issue is not None

    _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
    )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Document"
    assert "## App Release Gate Failure" not in body
    labels = {str(label) for label in front["labels"]}
    assert "release-contract-sha256-" + hashlib.sha256(
        (repo / "release-contract.yaml").read_bytes()
    ).hexdigest() in labels
    assert "release-contract-sha256-" + "0" * 64 not in labels


def test_remote_app_release_transition_fails_before_lifecycle_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)
    remote_cfg = replace(cfg, tracker=replace(cfg.tracker, kind="linear"))
    orchestrator = _orch(repo)
    advanced = replace(issue, state="Document")
    lifecycle_called = False

    async def refresh_full(_cfg, _issue_id: str):
        return advanced

    def reconcile(*_args, **_kwargs):
        nonlocal lifecycle_called
        lifecycle_called = True
        return _ReleaseCycleWriteResult(False, error="must not run")

    monkeypatch.setattr(orchestrator, "_refresh_issue_full", refresh_full)
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_reconcile_release_cycle",
        staticmethod(reconcile),
    )

    with pytest.raises(SymphonyError, match="require tracker.kind=file"):
        asyncio.run(
            orchestrator._enforce_app_release_transition(
                cfg=remote_cfg,
                issue=advanced,
                workspace_path=repo,
                producing_state="Verify",
                known_app_release=True,
            )
        )

    assert lifecycle_called is False


def test_remote_app_release_is_refused_before_execution_across_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    remote_cfg = replace(cfg, tracker=replace(cfg.tracker, kind="linear"))
    orchestrator = _orch(repo)
    lease_calls = 0

    def acquire(**_kwargs):
        nonlocal lease_calls
        lease_calls += 1
        return "unexpected-run"

    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", acquire)

    orchestrator._dispatch(issue, remote_cfg, attempt=None)
    orchestrator._dispatch(issue, remote_cfg, attempt=1)

    front, _body = parse_ticket_file(ticket_path)
    assert lease_calls == 0
    assert orchestrator._running == {}
    assert front["state"] == "Verify"


def test_release_cycle_retry_is_idempotent(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    validation = validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    assert validation.repairable_failures

    first = Orchestrator._tracker_call_reconcile_release_cycle(
        cfg, issue, validation, "codex"
    )
    second = Orchestrator._tracker_call_reconcile_release_cycle(
        cfg, issue, validation, "codex"
    )

    tracker = FileBoardTracker(cfg.tracker)
    issues = tracker.scan_all()
    tracker.close()
    assert first.passed and second.passed
    assert first.repair_identifiers == second.repair_identifiers
    assert first.verifier_identifier == second.verifier_identifier
    assert len([item for item in issues if "quality-fix" in item.labels]) == 1
    assert len(
        [
            item
            for item in issues
            if item.identifier != issue.identifier
            and "release-cycle-verifier" in item.labels
        ]
    ) == 1


def test_partial_fresh_verifier_is_fully_reconciled(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    validation = validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    tracker = FileBoardTracker(cfg.tracker)
    partial_id, _ = tracker.create_with_next_identifier(
        "RELEASE-VERIFY",
        title="partial verifier",
        state="Build",
        labels=[
            "app-release",
            "release-cycle-verifier",
            release_fingerprint_label(validation.fingerprint),
        ],
        description="partial verifier body\n",
        agent_kind="gemini",
        request=issue.request,
    )
    tracker.close()

    result = Orchestrator._tracker_call_reconcile_release_cycle(
        cfg, issue, validation, "opencode"
    )

    tracker = FileBoardTracker(cfg.tracker)
    verifier = tracker.fetch_issue_full_by_id(partial_id)
    tracker.close()
    assert result.passed and verifier is not None
    assert verifier.state == "Verify"
    assert verifier.agent_kind == "opencode"
    assert "Fresh application release verification" in (verifier.description or "")
    assert "rebase this evidence-only branch" in (verifier.description or "")
    assert "never merges it into the target" in (verifier.description or "")
    assert "partial verifier body" in (verifier.description or "")
    assert any(
        label == "release-contract-sha256-" + validation.contract_sha256
        for label in verifier.labels
    )
    assert {blocker.identifier for blocker in verifier.blocked_by} == set(
        result.repair_identifiers
    )


@requires_symlink_privilege
def test_repair_children_use_source_issue_backend_over_workflow_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    captured: list[str] = []
    worker = _release_worker_workspace(repo)
    orchestrator = _orch(worker)
    source = replace(issue, agent_kind="opencode", state="Document")
    _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=issue,
        workspace_path=worker,
    )
    entry = orchestrator._running[issue.id]
    entry.issue = source
    real_reconcile = Orchestrator._tracker_call_reconcile_release_cycle

    async def refresh_full(_cfg, _issue_id: str):
        return source

    def reconcile(
        _cfg,
        _issue,
        _validation,
        source_agent_kind: str,
        *,
        before_finalizer_relink=None,
    ):
        captured.append(source_agent_kind)
        return real_reconcile(
            _cfg,
            _issue,
            _validation,
            source_agent_kind,
            before_finalizer_relink=before_finalizer_relink,
        )

    monkeypatch.setattr(orchestrator, "_refresh_issue_full", refresh_full)
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_reconcile_release_cycle",
        staticmethod(reconcile),
    )

    _result, rewound = asyncio.run(
        orchestrator._enforce_app_release_transition(
            cfg=cfg,
            issue=source,
            workspace_path=worker,
            producing_state="Verify",
            known_app_release=True,
        )
    )

    assert rewound is False
    assert captured == ["opencode"]


@requires_symlink_privilege
def test_partial_cycle_write_fails_closed_then_reconciles_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    original = Orchestrator._tracker_call_reconcile_release_cycle

    def partial_write(
        write_cfg,
        source: Issue,
        validation,
        source_agent_kind: str,
        *,
        before_finalizer_relink=None,
    ) -> _ReleaseCycleWriteResult:
        tracker = FileBoardTracker(write_cfg.tracker)
        group = validation.repairable_failures[0].repair_group
        tracker.create_with_next_identifier(
            "QUALITY",
            title=f"Release quality repair: {group}",
            state="Done",
            labels=[
                "quality-fix",
                release_fingerprint_label(validation.fingerprint),
                release_group_label(group),
            ],
            description=release_repair_description(
                source=source,
                source_agent_kind=source_agent_kind,
                result=validation,
                repair_group=group,
                failures=tuple(
                    failure
                    for failure in validation.repairable_failures
                    if failure.repair_group == group
                ),
            )
            + "\npartial durable write\n",
            agent_kind="gemini",
            request="OTHER-REQUEST",
        )
        tracker.close()
        return _ReleaseCycleWriteResult(False, error="simulated finalizer update failure")

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_reconcile_release_cycle",
        staticmethod(partial_write),
    )
    _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
    )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Verify"
    assert "simulated finalizer update failure" in body

    validation = validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    reconciled = original(cfg, issue, validation, "codex")
    tracker = FileBoardTracker(cfg.tracker)
    issues = tracker.scan_all()
    tracker.close()

    assert reconciled.passed
    repairs = [item for item in issues if "quality-fix" in item.labels]
    assert len(repairs) == 1
    assert "Contract SHA-256" in (repairs[0].description or "")
    assert "feature-check" in (repairs[0].description or "")
    assert "partial durable write" in (repairs[0].description or "")
    assert repairs[0].state == "Build"
    assert repairs[0].agent_kind == "codex"
    assert repairs[0].request == issue.request
    assert {
        "quality-fix",
        release_fingerprint_label(validation.fingerprint),
        release_group_label(
            validation.repairable_failures[0].repair_group
        ),
    }.issubset(set(repairs[0].labels))
    assert len(
        [
            item
            for item in issues
            if item.identifier != issue.identifier
            and "release-cycle-verifier" in item.labels
        ]
    ) == 1


def test_release_cycle_registry_identity_survives_repair_and_verifier_label_loss(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    validation = validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket=issue.identifier,
        configured_target_branch="main",
    )
    assert validation.repairable_failures

    def fail_after_items_are_recorded(_verifier: Issue) -> None:
        raise OSError("simulated finalizer relink failure")

    partial = Orchestrator._tracker_call_reconcile_release_cycle(
        cfg,
        issue,
        validation,
        "codex",
        before_finalizer_relink=fail_after_items_are_recorded,
    )
    assert not partial.passed
    assert "simulated finalizer relink failure" in partial.error

    tracker = FileBoardTracker(cfg.tracker)
    first_issues = tracker.scan_all()
    repair = next(item for item in first_issues if "quality-fix" in item.labels)
    verifier = next(
        item
        for item in first_issues
        if item.identifier != issue.identifier
        and "release-cycle-verifier" in item.labels
    )
    tracker.update_fields(repair.identifier, labels=["quality-fix"])
    tracker.update_fields(
        verifier.identifier,
        labels=["app-release", "release-cycle-verifier"],
    )
    tracker.close()

    reconciled = Orchestrator._tracker_call_reconcile_release_cycle(
        cfg, issue, validation, "codex"
    )
    tracker = FileBoardTracker(cfg.tracker)
    final_issues = tracker.scan_all()
    tracker.close()

    assert reconciled.passed
    assert reconciled.repair_identifiers == (repair.identifier,)
    assert reconciled.verifier_identifier == verifier.identifier
    assert len(
        [candidate for candidate in final_issues if "quality-fix" in candidate.labels]
    ) == 1
    assert len(
        [
            candidate
            for candidate in final_issues
            if candidate.identifier != issue.identifier
            and "release-cycle-verifier" in candidate.labels
        ]
    ) == 1
    restored_repair = next(
        candidate for candidate in final_issues if candidate.identifier == repair.identifier
    )
    restored_verifier = next(
        candidate
        for candidate in final_issues
        if candidate.identifier == verifier.identifier
    )
    repair_group = validation.repairable_failures[0].repair_group
    assert {
        "quality-fix",
        release_fingerprint_label(validation.fingerprint),
        release_group_label(repair_group),
    }.issubset({label.lower() for label in restored_repair.labels})
    assert {
        "app-release",
        "release-cycle-verifier",
        release_fingerprint_label(validation.fingerprint),
    }.issubset({label.lower() for label in restored_verifier.labels})


@pytest.mark.parametrize(
    ("crash_prefix", "item_role", "item_key"),
    (
        ("QUALITY-", "repair", "frontend"),
        ("RELEASE-VERIFY-", "verifier", "fresh-verifier"),
    ),
)
def test_release_cycle_precreate_reservation_survives_crash_and_label_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_prefix: str,
    item_role: str,
    item_key: str,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    validation = validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket=issue.identifier,
        configured_target_branch="main",
    )
    assert validation.repairable_failures
    if item_role == "repair":
        item_key = validation.repairable_failures[0].repair_group

    original_create = FileBoardTracker.create
    crashed = False

    def create_then_crash(
        tracker: FileBoardTracker, **kwargs: object
    ) -> Path:
        nonlocal crashed
        path = original_create(tracker, **kwargs)  # type: ignore[arg-type]
        identifier = str(kwargs["identifier"])
        if not crashed and identifier.startswith(crash_prefix):
            crashed = True
            raise OSError("simulated process loss after board create")
        return path

    monkeypatch.setattr(FileBoardTracker, "create", create_then_crash)
    partial = Orchestrator._tracker_call_reconcile_release_cycle(
        cfg, issue, validation, "codex"
    )
    assert not partial.passed
    assert "simulated process loss after board create" in partial.error
    assert crashed

    registry = RunRegistry(registry_path_for_workflow(cfg.workflow_path))
    reserved = registry.get_release_cycle_item(
        finalizer_identifier=validation.finalizer_ticket,
        cycle_fingerprint=validation.fingerprint,
        item_role=item_role,
        item_key=item_key,
    )
    registry.close()
    assert reserved is not None

    tracker = FileBoardTracker(cfg.tracker)
    created = tracker.fetch_issue_full_by_id(reserved.identifier)
    assert created is not None
    tracker.update_fields(created.identifier, labels=["worker-edited"])
    tracker.close()
    monkeypatch.setattr(FileBoardTracker, "create", original_create)

    reconciled = Orchestrator._tracker_call_reconcile_release_cycle(
        cfg, issue, validation, "codex"
    )
    tracker = FileBoardTracker(cfg.tracker)
    final_issues = tracker.scan_all()
    restored = tracker.fetch_issue_full_by_id(reserved.identifier)
    tracker.close()

    assert reconciled.passed
    assert restored is not None
    assert len(
        [candidate for candidate in final_issues if candidate.identifier == reserved.identifier]
    ) == 1
    if item_role == "repair":
        assert "quality-fix" in {label.lower() for label in restored.labels}
        assert reconciled.repair_identifiers == (reserved.identifier,)
    else:
        assert "release-cycle-verifier" in {
            label.lower() for label in restored.labels
        }
        assert reconciled.verifier_identifier == reserved.identifier


def test_concurrent_release_cycle_reservations_create_one_ticket_per_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    validation = validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket=issue.identifier,
        configured_target_branch="main",
    )
    assert validation.repairable_failures
    registry = RunRegistry(registry_path_for_workflow(cfg.workflow_path))
    registry.close()

    original_reserve = RunRegistry.reserve_release_cycle_item
    reservation_barrier = threading.Barrier(2)

    def reserve_together(
        registry: RunRegistry, **kwargs: object
    ):
        if kwargs.get("item_role") == "repair":
            reservation_barrier.wait(timeout=5)
        return original_reserve(registry, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        RunRegistry,
        "reserve_release_cycle_item",
        reserve_together,
    )
    results: list[_ReleaseCycleWriteResult] = []
    errors: list[BaseException] = []

    def reconcile() -> None:
        try:
            results.append(
                Orchestrator._tracker_call_reconcile_release_cycle(
                    cfg, issue, validation, "codex"
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion surfaces it
            errors.append(exc)

    workers = [threading.Thread(target=reconcile) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert len(results) == 2
    assert all(result.passed for result in results)
    assert results[0].repair_identifiers == results[1].repair_identifiers
    assert results[0].verifier_identifier == results[1].verifier_identifier

    tracker = FileBoardTracker(cfg.tracker)
    issues = tracker.scan_all()
    tracker.close()
    assert len([candidate for candidate in issues if "quality-fix" in candidate.labels]) == 1
    assert len(
        [
            candidate
            for candidate in issues
            if candidate.identifier != issue.identifier
            and "release-cycle-verifier" in candidate.labels
        ]
    ) == 1


def test_host_release_authority_persists_initial_gate_before_run_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)
    orchestrator = _orch(repo)
    observed: list[ReleaseGate] = []

    def observe_lease(**_kwargs: object) -> None:
        gate = _required_release_gate(orchestrator)
        assert gate.status == "pending"
        assert gate.verifier_issue_id == issue.id
        assert gate.verifier_identifier == issue.identifier
        observed.append(gate)
        return None

    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", observe_lease)

    orchestrator._dispatch(issue, cfg, attempt=None)

    assert len(observed) == 1
    assert orchestrator._running == {}


def test_host_release_authority_survives_restart_and_label_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)
    first = _orch(repo)
    monkeypatch.setattr(first, "_try_acquire_run_lease", lambda **_kwargs: None)

    first._dispatch(issue, cfg, attempt=None)
    original_gate = _required_release_gate(first)
    assert first._run_registry is not None
    first._run_registry.close()
    first._run_registry = None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(issue.identifier, labels=[])
    stripped = tracker.fetch_issue_full_by_id(issue.identifier)
    tracker.close()
    assert stripped is not None
    assert stripped.labels == ()

    restarted = _orch(repo)
    leased_issues: list[Issue] = []

    def observe_lease(**kwargs: object) -> None:
        leased = kwargs["issue"]
        assert isinstance(leased, Issue)
        leased_issues.append(leased)
        return None

    monkeypatch.setattr(restarted, "_try_acquire_run_lease", observe_lease)

    restarted._dispatch(stripped, cfg, attempt=None)

    restarted_gate = _required_release_gate(restarted)
    assert restarted_gate == original_gate
    assert len(leased_issues) == 1
    restored_labels = {label.lower() for label in leased_issues[0].labels}
    assert "app-release" in restored_labels
    assert "release-cycle-verifier" in restored_labels
    assert (
        "release-contract-sha256-" + original_gate.expected_contract_sha256
        in restored_labels
    )


def test_host_release_authority_reverifies_restarted_approved_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    first = _orch(repo)
    authority = first._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=first,
        cfg=cfg,
        gate=authority.gate,
    )
    assert first._run_registry is not None
    first._run_registry.close()
    first._run_registry = None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Document", labels=[])
    continued = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert continued is not None

    restarted = _orch(repo)
    leased: list[Issue] = []
    monkeypatch.setattr(
        restarted,
        "_try_acquire_run_lease",
        lambda **kwargs: leased.append(kwargs["issue"]) or None,
    )

    restarted._dispatch(continued, cfg, attempt=None)

    assert len(leased) == 1
    assert leased[0].state == "Verify"
    persisted_gate = _required_release_gate(restarted)
    assert persisted_gate.status == "pending"
    assert persisted_gate.generation != approved.generation
    assert persisted_gate.verifier_issue_id == approved.verifier_issue_id
    assert persisted_gate.verifier_identifier == approved.verifier_identifier


def test_app_release_label_cannot_authorize_outside_verify_without_registry(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, _verifier = _setup_board(repo)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.create(
        identifier="APP-OUTSIDE",
        title="Unbound application evidence",
        state="Document",
        labels=["app-release", "release-cycle-verifier"],
        description="Worker-editable labels are not release authority.\n",
    )
    outside = tracker.fetch_issue_full_by_id("APP-OUTSIDE")
    tracker.close()
    assert outside is not None

    orchestrator = _orch(repo)
    with pytest.raises(SymphonyError, match="outside Verify"):
        orchestrator._prepare_release_dispatch(outside, cfg)

    assert orchestrator._run_registry is not None
    assert (
        orchestrator._run_registry.get_release_gate_for_verifier("APP-OUTSIDE")
        is None
    )


def test_initial_pending_gate_reopens_terminal_finalizer_before_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("APP-FINAL", state="Done")
    tracker.close()
    orchestrator = _orch(repo)
    observed: list[str] = []

    def observe_lease(**_kwargs: object) -> None:
        gate = _required_release_gate(orchestrator)
        tracker = FileBoardTracker(cfg.tracker)
        try:
            finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
        finally:
            tracker.close()
        assert gate.status == "pending"
        assert finalizer is not None
        observed.append(finalizer.state)
        return None

    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", observe_lease)

    orchestrator._dispatch(verifier, cfg, attempt=None)

    assert observed == ["Document"]
    assert orchestrator._running == {}


def test_existing_pending_gate_reopens_terminal_finalizer_before_first_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    assert authority.gate.status == "pending"
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("APP-FINAL", state="Done")
    current_verifier = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert current_verifier is not None
    observed: list[str] = []

    def observe_lease(**_kwargs: object) -> None:
        tracker = FileBoardTracker(cfg.tracker)
        try:
            finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
        finally:
            tracker.close()
        assert finalizer is not None
        observed.append(finalizer.state)
        return None

    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", observe_lease)

    orchestrator._dispatch(current_verifier, cfg, attempt=None)

    assert observed == ["Document"]


def test_host_release_authority_ignores_unlabeled_release_verify_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, _issue = _setup_board(repo)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.create(
        identifier="RELEASE-VERIFY-99",
        title="Ordinary prefixed ticket",
        state="Verify",
        labels=[],
        description="No application release role.\n",
    )
    ordinary = tracker.fetch_issue_full_by_id("RELEASE-VERIFY-99")
    tracker.close()
    assert ordinary is not None

    orchestrator = _orch(repo)
    leased: list[Issue] = []

    def observe_lease(**kwargs: object) -> None:
        candidate = kwargs["issue"]
        assert isinstance(candidate, Issue)
        leased.append(candidate)
        return None

    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", observe_lease)

    orchestrator._dispatch(ordinary, cfg, attempt=None)

    assert [candidate.identifier for candidate in leased] == ["RELEASE-VERIFY-99"]
    assert orchestrator._run_registry is not None
    assert (
        orchestrator._run_registry.get_release_gate_for_verifier(
            "RELEASE-VERIFY-99"
        )
        is None
    )


@pytest.mark.parametrize("operation", ["read", "write"])
def test_host_release_authority_registry_failure_refuses_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)
    orchestrator = _orch(repo)
    lease_calls: list[str] = []

    def fail_registry(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError(f"simulated release gate {operation} failure")

    if operation == "read":
        authority = orchestrator._prepare_release_dispatch(issue, cfg)
        assert authority.gate is not None
        tracker = FileBoardTracker(cfg.tracker)
        tracker.update_fields(issue.identifier, labels=[])
        issue = tracker.fetch_issue_full_by_id(issue.identifier)
        tracker.close()
        assert issue is not None
        assert issue.labels == ()
        monkeypatch.setattr(RunRegistry, "get_release_gate_for_verifier", fail_registry)
    else:
        monkeypatch.setattr(RunRegistry, "replace_pending_release_gate", fail_registry)
    monkeypatch.setattr(
        orchestrator,
        "_try_acquire_run_lease",
        lambda **_kwargs: lease_calls.append("lease") or None,
    )

    orchestrator._dispatch(issue, cfg, attempt=None)

    assert lease_calls == []
    assert orchestrator._running == {}


def test_host_release_authority_lease_write_failure_refuses_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)
    orchestrator = _orch(repo)

    def fail_acquire(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("simulated release lease write failure")

    monkeypatch.setattr(RunRegistry, "acquire_run", fail_acquire)

    orchestrator._dispatch(issue, cfg, attempt=None)

    assert orchestrator._running == {}
    assert _required_release_gate(orchestrator).status == "pending"


def test_host_release_authority_heartbeat_error_loses_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(issue, cfg)
    assert authority.gate is not None
    registry = orchestrator._run_registry
    assert registry is not None
    run_id = registry.acquire_run(
        authority.issue,
        workspace_path=repo,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
    )
    assert run_id is not None
    _seed_running_entry(orchestrator, authority.issue, repo)
    running = orchestrator._running[issue.id]
    running.run_id = run_id
    running.known_app_release = True
    running.known_release_cycle_verifier = True
    running.release_authority_resolved = True

    def fail_heartbeat(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("simulated release heartbeat failure")

    monkeypatch.setattr(registry, "heartbeat", fail_heartbeat)

    assert not orchestrator._heartbeat_run_lease(issue.id, running)
    assert running.lease_lost


@pytest.mark.parametrize(
    ("authority_case", "expects_lease"),
    [
        ("approved-current", True),
        ("pending", False),
        ("missing-verifier-blocker", False),
        ("peer-verifier-lease", False),
        ("changed-target-sha", False),
        ("changed-contract-hash", False),
    ],
)
def test_host_release_authority_finalizer_dispatch_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority_case: str,
    expects_lease: bool,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    gate = authority.gate
    registry = orchestrator._run_registry
    assert registry is not None

    if authority_case == "changed-contract-hash":
        gate = replace(
            gate,
            expected_contract_sha256="0" * 64,
            cycle_fingerprint="1" * 64,
        )
        gate = registry.replace_pending_release_gate(gate)

    if authority_case != "pending":
        gate, _validation = _approve_current_release_gate(
            orchestrator=orchestrator,
            cfg=cfg,
            gate=gate,
        )

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done")
    if authority_case == "missing-verifier-blocker":
        tracker.update_fields("APP-FINAL", blocked_by=["UNRELATED-1"])
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert finalizer is not None

    if authority_case == "peer-verifier-lease":
        run_id = registry.acquire_run(
            authority.issue,
            workspace_path=repo,
            attempt=None,
            attempt_kind="release-verification",
            agent_kind="codex",
        )
        assert run_id is not None
    elif authority_case == "changed-target-sha":
        (repo / "app.txt").write_text("v2\n", encoding="utf-8")
        _git(repo, "add", "app.txt")
        _git(repo, "commit", "-m", "target changed after release approval")

    lease_calls: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_try_acquire_run_lease",
        lambda **_kwargs: lease_calls.append("lease") or None,
    )

    orchestrator._dispatch(finalizer, cfg, attempt=None)

    assert (lease_calls == ["lease"]) is expects_lease
    assert orchestrator._running == {}


@requires_symlink_privilege
def test_host_release_authority_green_verifier_approves_exact_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    worker = _release_worker_workspace(repo)
    orchestrator = _orch(worker)
    authority = orchestrator._prepare_release_dispatch(issue, cfg)
    assert authority.gate is not None
    registry = orchestrator._run_registry
    assert registry is not None
    verifier_run_id = registry.acquire_run(
        authority.issue,
        workspace_path=worker,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
    )
    assert verifier_run_id is not None
    assert registry.bind_release_verifier_run(
        gate=authority.gate,
        verifier_run_id=verifier_run_id,
    )
    _install_file_tracker_backend(
        monkeypatch,
        ticket_path=ticket_path,
        transitions=[("Done", authority.issue.description or "")],
    )
    _seed_running_entry(orchestrator, authority.issue, worker)
    running = orchestrator._running[issue.id]
    running.run_id = verifier_run_id
    running.known_app_release = True
    running.known_release_cycle_verifier = True
    running.release_gate_finalizer = authority.gate.finalizer_identifier
    running.release_gate_expected_contract_sha256 = (
        authority.gate.expected_contract_sha256
    )
    running.release_gate_cycle_fingerprint = authority.gate.cycle_fingerprint
    running.release_gate_generation = authority.gate.generation
    running.release_authority_resolved = True

    asyncio.run(
        orchestrator._run_agent_attempt(authority.issue, attempt=None, cfg=cfg)
    )

    gate = _required_release_gate(orchestrator)
    validation = validate_release_contract(
        workspace_root=worker,
        repository_root=repo,
        verifier_ticket=issue.identifier,
        configured_target_branch="main",
        board_root=cfg.tracker.board_root,
    )
    assert gate.status == "approved"
    assert gate.approved_fingerprint == validation.fingerprint
    assert gate.target_branch == validation.target_branch
    assert gate.approved_target_sha == validation.target_sha
    assert gate.verifier_run_id == verifier_run_id


def test_runtime_rejects_verify_only_release_topology_before_gate_or_lease(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)
    cfg = replace(
        cfg,
        tracker=replace(cfg.tracker, active_states=("Verify",)),
    )
    orchestrator = _orch(repo)

    with pytest.raises(
        SymphonyError,
        match="active Verify and finalizer lanes",
    ):
        orchestrator._prepare_release_dispatch(issue, cfg)

    registry = orchestrator._run_registry
    assert registry is not None
    assert registry.get_release_gate("APP-FINAL") is None
    assert registry.active_leases() == []


def test_host_release_authority_rejects_green_without_active_exact_run_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(issue, cfg)
    assert authority.gate is not None
    backends = _install_file_tracker_backend(
        monkeypatch,
        ticket_path=ticket_path,
        transitions=[("Done", authority.issue.description or "")],
    )
    _seed_running_entry(orchestrator, authority.issue, repo)
    running = orchestrator._running[issue.id]
    running.run_id = "not-an-active-registry-run"
    running.known_app_release = True
    running.known_release_cycle_verifier = True
    running.release_gate_finalizer = authority.gate.finalizer_identifier
    running.release_gate_expected_contract_sha256 = (
        authority.gate.expected_contract_sha256
    )
    running.release_gate_cycle_fingerprint = authority.gate.cycle_fingerprint
    running.release_gate_generation = authority.gate.generation
    running.release_authority_resolved = True

    asyncio.run(
        orchestrator._run_agent_attempt(authority.issue, attempt=None, cfg=cfg)
    )

    front, body = parse_ticket_file(ticket_path)
    assert front["state"] == "Verify"
    assert body == "Verifier body."
    assert backends == []
    assert _required_release_gate(orchestrator).status == "pending"
    assert _required_release_gate(orchestrator).status == "pending"


@pytest.mark.parametrize("failed_state", ["Blocked", "Failed", "Rejected"])
def test_host_release_authority_failed_terminal_verifier_blocks_finalizer(
    tmp_path: Path, failed_state: str
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    cfg = replace(
        cfg,
        tracker=replace(
            cfg.tracker,
            terminal_states=tuple(
                dict.fromkeys((*cfg.tracker.terminal_states, failed_state))
            ),
        ),
    )
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state=failed_state)
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert finalizer is not None

    with pytest.raises(SymphonyError, match="not successfully terminal"):
        orchestrator._prepare_release_dispatch(finalizer, cfg)


def test_host_release_authority_green_verifier_skips_delivery_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, issue = _setup_board(repo)
    cfg = replace(cfg, agent=replace(cfg.agent, auto_merge_on_done=True))
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(issue, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    done_issue = replace(authority.issue, state="Done")
    _seed_running_entry(orchestrator, done_issue, repo)
    running = orchestrator._running[issue.id]
    running.known_app_release = True
    running.known_release_cycle_verifier = True
    running.release_gate_finalizer = approved.finalizer_identifier
    running.release_gate_expected_contract_sha256 = (
        approved.expected_contract_sha256
    )
    running.release_gate_cycle_fingerprint = approved.cycle_fingerprint
    running.release_gate_generation = approved.generation
    running.release_authority_resolved = True
    monkeypatch.setattr(orchestrator._workflow_state, "current", lambda: cfg)
    merge_calls: list[str] = []
    after_done_calls: list[str] = []
    remove_calls: list[Path] = []

    async def capture_merge(*_args: object, **_kwargs: object) -> bool:
        merge_calls.append(issue.identifier)
        return True

    async def capture_after_done(*_args: object, **_kwargs: object) -> None:
        after_done_calls.append(issue.identifier)

    async def capture_remove(path: Path) -> None:
        remove_calls.append(path)

    monkeypatch.setattr(orchestrator, "_auto_merge_done_gate_or_block", capture_merge)
    monkeypatch.setattr(
        orchestrator, "_after_done_then_remove_per_policy", capture_after_done
    )
    assert orchestrator._workspace_manager is not None
    monkeypatch.setattr(
        orchestrator._workspace_manager, "remove", capture_remove, raising=False
    )

    asyncio.run(orchestrator._on_worker_exit(issue.id, "normal", None))

    assert merge_calls == []
    assert after_done_calls == []
    assert remove_calls == [repo]


def test_host_release_authority_finalizer_rechecks_before_delivery_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    cfg = replace(cfg, agent=replace(cfg.agent, auto_merge_on_done=True))
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done")
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert finalizer is not None

    registry = orchestrator._run_registry
    assert registry is not None
    finalizer_run_id = registry.acquire_run(
        finalizer,
        workspace_path=repo,
        attempt=None,
        attempt_kind="release-finalizer-test",
        agent_kind="codex",
    )
    assert finalizer_run_id is not None
    assert registry.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=finalizer_run_id,
    )
    rebound = registry.get_release_gate(approved.finalizer_identifier)
    assert rebound is not None
    approved = rebound

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("APP-FINAL", state="Done")
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert finalizer is not None

    (repo / "app.txt").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "target changed during finalizer run")

    _seed_running_entry(orchestrator, finalizer, repo)
    running = orchestrator._running[finalizer.id]
    running.run_id = finalizer_run_id
    running.known_app_release_finalizer = True
    running.release_gate_finalizer = approved.finalizer_identifier
    running.release_gate_expected_contract_sha256 = (
        approved.expected_contract_sha256
    )
    running.release_gate_cycle_fingerprint = approved.cycle_fingerprint
    running.release_gate_generation = approved.generation
    running.release_finalizer_rewind_state = "Document"
    running.release_authority_resolved = True
    monkeypatch.setattr(orchestrator._workflow_state, "current", lambda: cfg)
    merge_calls: list[str] = []
    after_done_calls: list[str] = []

    async def capture_merge(*_args: object, **_kwargs: object) -> bool:
        merge_calls.append(finalizer.identifier)
        return True

    async def capture_after_done(*_args: object, **_kwargs: object) -> None:
        after_done_calls.append(finalizer.identifier)

    monkeypatch.setattr(orchestrator, "_auto_merge_done_gate_or_block", capture_merge)
    monkeypatch.setattr(
        orchestrator, "_after_done_then_remove_per_policy", capture_after_done
    )

    asyncio.run(orchestrator._on_worker_exit(finalizer.id, "normal", None))

    tracker = FileBoardTracker(cfg.tracker)
    persisted_finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert persisted_finalizer is not None
    assert persisted_finalizer.state == "Document"
    assert _required_release_gate(orchestrator).status == "pending"
    assert merge_calls == []
    assert after_done_calls == []


def test_host_release_authority_rewind_exhaustion_never_moves_to_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    cfg = replace(
        cfg,
        tracker=replace(cfg.tracker, terminal_states=("Done",)),
        agent=replace(cfg.agent, max_attempts=1),
    )
    _mutate_evidence(repo, lambda evidence: evidence.__setitem__("target_sha", "0" * 40))
    orchestrator = _orch(repo)

    async def run_attempt(current: Issue) -> Issue:
        _install_file_tracker_backend(
            monkeypatch,
            ticket_path=ticket_path,
            transitions=[("Done", current.description or "")],
        )
        _seed_running_entry(orchestrator, current, repo)
        await orchestrator._run_agent_attempt(current, attempt=None, cfg=cfg)
        for retry in list(orchestrator._retry.values()):
            retry.timer_handle.cancel()
        orchestrator._retry.clear()
        tracker = FileBoardTracker(cfg.tracker)
        try:
            persisted = tracker.fetch_issue_full_by_id(issue.identifier)
        finally:
            tracker.close()
        assert persisted is not None
        return persisted

    async def run_twice() -> Issue:
        first = await run_attempt(issue)
        assert first.state == "Verify"
        return await run_attempt(first)

    exhausted = asyncio.run(run_twice())

    assert exhausted.state == "Verify"


@pytest.mark.parametrize(
    ("terminal_identifier", "expected_state"),
    [("VERIFY-1", "Verify"), ("APP-FINAL", "Document")],
)
def test_startup_reopens_unapproved_terminal_release_without_workspace(
    tmp_path: Path,
    terminal_identifier: str,
    expected_state: str,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, _issue = _setup_board(repo)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(terminal_identifier, state="Done")
    tracker.close()
    orchestrator = _orch(repo)
    assert orchestrator._workspace_manager is not None
    setattr(orchestrator._workspace_manager, "_path", repo / "missing-workspace")

    asyncio.run(orchestrator._startup_terminal_cleanup(cfg))

    tracker = FileBoardTracker(cfg.tracker)
    persisted = tracker.fetch_issue_full_by_id(terminal_identifier)
    tracker.close()
    assert persisted is not None
    assert persisted.state == expected_state
    assert "Startup" in (persisted.description or "")


@requires_symlink_privilege
def test_stale_finalizer_cannot_adopt_reapproved_cycle_between_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, verifier = _setup_board(repo)
    finalizer_workspace = tmp_path / "finalizer-workspace"
    _git(tmp_path, "clone", str(repo), str(finalizer_workspace))
    (finalizer_workspace / "kanban").symlink_to(
        repo / "kanban", target_is_directory=True
    )
    orchestrator = _orch(finalizer_workspace)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    assert approved.target_branch is not None
    assert approved.approved_target_sha is not None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done")
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert finalizer is not None

    instances = _install_file_tracker_backend(
        monkeypatch,
        ticket_path=repo / "kanban" / "APP-FINAL.md",
        transitions=[],
    )
    original_run_turn = _TicketMutatingBackend.run_turn
    turn_count = 0

    async def supersede_after_first_turn(
        backend: _TicketMutatingBackend,
        *,
        prompt: str,
        is_continuation: bool,
    ) -> None:
        nonlocal turn_count
        await original_run_turn(
            backend,
            prompt=prompt,
            is_continuation=is_continuation,
        )
        turn_count += 1
        if turn_count != 1:
            return

        registry = orchestrator._run_registry
        assert registry is not None
        stale_run = orchestrator._running[finalizer.id].run_id
        assert registry.complete_run(
            issue_id=finalizer.id,
            run_id=stale_run,
            status="superseded-release-cycle",
        )

        cycle_tracker = FileBoardTracker(cfg.tracker)
        cycle_tracker.create(
            identifier="VERIFY-2",
            title="Fresh release verifier",
            state="Done",
            labels=["app-release", "release-cycle-verifier"],
            description="Fresh GREEN verification.\n",
            request=verifier.request,
        )
        cycle_tracker.update_fields(
            "APP-FINAL", blocked_by=["VERIFY-2", "UNRELATED-1"]
        )
        fresh_verifier = cycle_tracker.fetch_issue_full_by_id("VERIFY-2")
        cycle_tracker.close()
        assert fresh_verifier is not None

        pending = registry.replace_pending_release_gate(
            replace(
                approved,
                verifier_issue_id=fresh_verifier.id,
                verifier_identifier=fresh_verifier.identifier,
                cycle_fingerprint="f" * 64,
                approved_fingerprint=None,
                status="pending",
                target_branch=None,
                approved_target_sha=None,
                verifier_run_id=None,
                finalizer_run_id=None,
                finalizer_completed_at=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        verifier_run = registry.acquire_run(
            fresh_verifier,
            workspace_path=repo,
            attempt=None,
            attempt_kind="fresh-release-verification",
            agent_kind="codex",
        )
        assert verifier_run is not None
        assert registry.bind_release_verifier_run(
            gate=pending,
            verifier_run_id=verifier_run,
        )
        assert registry.approve_release_gate(
            finalizer_identifier=pending.finalizer_identifier,
            verifier_issue_id=pending.verifier_issue_id,
            verifier_identifier=pending.verifier_identifier,
            expected_contract_sha256=pending.expected_contract_sha256,
            expected_cycle_fingerprint=pending.cycle_fingerprint,
            expected_generation=pending.generation,
            approved_fingerprint="e" * 64,
            target_branch=approved.target_branch,
            target_sha=approved.approved_target_sha,
            verifier_run_id=verifier_run,
        )
        assert registry.complete_run(
            issue_id=fresh_verifier.id,
            run_id=verifier_run,
            status="normal",
        )

    monkeypatch.setattr(_TicketMutatingBackend, "run_turn", supersede_after_first_turn)

    async def run_finalizer() -> None:
        orchestrator._dispatch(finalizer, cfg, attempt=None)
        entry = orchestrator._running.get(finalizer.id)
        assert entry is not None and entry.worker_task is not None
        await entry.worker_task

    asyncio.run(run_finalizer())

    current = _required_release_gate(orchestrator)
    assert current.status == "approved"
    assert current.generation != approved.generation
    assert turn_count == 1
    assert sum(
        1
        for backend in instances
        for call, _payload in backend.calls
        if call == "run_turn"
    ) == 1


def test_reapproved_finalizer_refuses_stale_prior_cycle_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    stale_workspace = tmp_path / "stale-finalizer-workspace"
    stale_workspace.mkdir()
    _git(stale_workspace, "init", "-b", "main")
    _git(stale_workspace, "config", "user.name", "Release Test")
    _git(stale_workspace, "config", "user.email", "release@example.test")
    (stale_workspace / "stale.txt").write_text("prior cycle\n", encoding="utf-8")
    _git(stale_workspace, "add", "stale.txt")
    _git(stale_workspace, "commit", "-m", "unrelated prior release cycle")

    orchestrator = _orch(stale_workspace)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done")
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert finalizer is not None

    instances = _install_file_tracker_backend(
        monkeypatch,
        ticket_path=repo / "kanban" / "APP-FINAL.md",
        transitions=[],
    )

    async def run_finalizer() -> None:
        orchestrator._dispatch(finalizer, cfg, attempt=None)
        entry = orchestrator._running.get(finalizer.id)
        assert entry is not None and entry.worker_task is not None
        await entry.worker_task

    asyncio.run(run_finalizer())

    assert all(
        call != "run_turn"
        for backend in instances
        for call, _payload in backend.calls
    )
    tracker = FileBoardTracker(cfg.tracker)
    persisted = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert persisted is not None
    assert persisted.state == "Document"


@requires_symlink_privilege
def test_superseded_verifier_label_loss_restart_remains_evidence_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, verifier = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    first = _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=verifier,
        monkeypatch=monkeypatch,
    )
    assert first._run_registry is not None
    identity = first._run_registry.get_release_evidence_identity("VERIFY-1")
    assert identity is not None and identity.retired
    first._run_registry.close()
    first._run_registry = None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done", labels=[])
    stripped = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert stripped is not None and stripped.labels == ()

    restarted = _orch(repo)
    guarded, evidence_only, stopped = asyncio.run(
        restarted._startup_release_terminal_guard(cfg, stripped)
    )

    assert evidence_only is True
    assert stopped is False
    assert guarded.state == "Done"
    tracker = FileBoardTracker(cfg.tracker)
    persisted = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert persisted is not None and persisted.state == "Done"


def test_startup_rewinds_terminal_finalizer_without_completed_bound_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    assert approved.finalizer_run_id is None
    assert approved.finalizer_completed_at is None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done")
    tracker.update_fields("APP-FINAL", state="Done")
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert finalizer is not None
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_terminal_issues",
        lambda _cfg: [finalizer],
    )
    assert orchestrator._workspace_manager is not None
    setattr(orchestrator._workspace_manager, "_path", repo / "missing-workspace")

    asyncio.run(orchestrator._startup_terminal_cleanup(cfg))

    tracker = FileBoardTracker(cfg.tracker)
    persisted = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert persisted is not None
    assert persisted.state == "Document"
    assert "Startup" in (persisted.description or "")


def test_completed_finalizer_redispatch_invalidates_old_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    completed, _terminal_finalizer, _completion_token = (
        _complete_current_release_finalizer(
            orchestrator=orchestrator,
            cfg=cfg,
            approved=approved,
        )
    )

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(completed.finalizer_identifier, state="Document")
    active_finalizer = tracker.fetch_issue_full_by_id(completed.finalizer_identifier)
    tracker.close()
    assert active_finalizer is not None
    lease_requests: list[str] = []

    def capture_lease(**kwargs: object) -> None:
        requested = kwargs["issue"]
        assert isinstance(requested, Issue)
        lease_requests.append(requested.identifier)
        return None

    monkeypatch.setattr(orchestrator, "_try_acquire_run_lease", capture_lease)

    orchestrator._dispatch(active_finalizer, cfg, attempt=None)

    current = _required_release_gate(orchestrator)
    assert current.status == "pending"
    assert current.generation != completed.generation
    assert current.approved_fingerprint is None
    assert current.finalizer_run_id is None
    assert current.finalizer_completed_at is None
    assert current.finalizer_completion_token is None
    tracker = FileBoardTracker(cfg.tracker)
    persisted_verifier = tracker.fetch_issue_full_by_id(
        completed.verifier_identifier
    )
    persisted_finalizer = tracker.fetch_issue_full_by_id(
        completed.finalizer_identifier
    )
    tracker.close()
    assert persisted_verifier is not None
    assert persisted_finalizer is not None
    assert persisted_verifier.state == "Verify"
    assert persisted_finalizer.state == "Document"
    assert lease_requests == []


def test_finalizer_completion_refuses_ticket_changed_after_terminal_guard(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    registry = orchestrator._run_registry
    assert registry is not None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(approved.verifier_identifier, state="Done")
    finalizer = tracker.fetch_issue_full_by_id(approved.finalizer_identifier)
    tracker.close()
    assert finalizer is not None
    finalizer_run_id = registry.acquire_run(
        finalizer,
        workspace_path=repo,
        attempt=None,
        attempt_kind="completion-race",
        agent_kind="codex",
    )
    assert finalizer_run_id is not None
    assert registry.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=finalizer_run_id,
    )
    bound = registry.get_release_gate(approved.finalizer_identifier)
    assert bound is not None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(finalizer.identifier, state="Done")
    terminal = tracker.fetch_issue_full_by_id(finalizer.identifier)
    tracker.close()
    assert terminal is not None
    guarded, completion_token = orchestrator._guard_release_finalizer_with_version(
        cfg=cfg,
        issue=terminal,
        gate=bound,
        rewind_state="Document",
        expected_run_id=finalizer_run_id,
        require_run_authority=True,
    )

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(finalizer.identifier, state="Document")
    tracker.close()
    with pytest.raises(
        SymphonyError,
        match="changed before completion proof",
    ):
        orchestrator._mark_release_finalizer_completed(
            cfg=cfg,
            issue=guarded,
            gate=bound,
            completion_token=completion_token,
            rewind_state="Document",
        )

    current = _required_release_gate(orchestrator)
    assert current.status == "pending"
    assert current.generation != bound.generation
    assert current.finalizer_run_id is None
    assert current.finalizer_completed_at is None
    assert current.finalizer_completion_token is None
    tracker = FileBoardTracker(cfg.tracker)
    persisted_verifier = tracker.fetch_issue_full_by_id(
        approved.verifier_identifier
    )
    persisted_finalizer = tracker.fetch_issue_full_by_id(
        approved.finalizer_identifier
    )
    tracker.close()
    assert persisted_verifier is not None
    assert persisted_finalizer is not None
    assert persisted_verifier.state == "Verify"
    assert persisted_finalizer.state == "Document"


def test_startup_rejects_replayed_completed_finalizer_ticket_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    first = _orch(repo)
    authority = first._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=first,
        cfg=cfg,
        gate=authority.gate,
    )
    completed, _terminal_finalizer, completion_token = (
        _complete_current_release_finalizer(
            orchestrator=first,
            cfg=cfg,
            approved=approved,
        )
    )
    assert first._run_registry is not None
    first._run_registry.close()
    first._run_registry = None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(completed.finalizer_identifier, state="Document")
    tracker.update_fields(completed.finalizer_identifier, state="Done")
    replayed_terminal = tracker.fetch_issue_full_by_id(completed.finalizer_identifier)
    tracker.close()
    assert replayed_terminal is not None
    assert (
        release_ticket_version_token(cfg, completed.finalizer_identifier)
        != completion_token
    )

    restarted = _orch(repo)
    monkeypatch.setattr(
        restarted,
        "_tracker_call_terminal_issues",
        lambda _cfg: [replayed_terminal],
    )
    cleanup_claims: list[str] = []

    def capture_cleanup_claim(**kwargs: object) -> None:
        issue = kwargs["issue"]
        assert isinstance(issue, Issue)
        cleanup_claims.append(issue.identifier)
        return None

    monkeypatch.setattr(restarted, "_try_acquire_run_lease", capture_cleanup_claim)
    removed: list[Path] = []

    async def capture_remove(path: Path) -> None:
        removed.append(path)

    assert restarted._workspace_manager is not None
    monkeypatch.setattr(
        restarted._workspace_manager,
        "remove",
        capture_remove,
        raising=False,
    )

    asyncio.run(restarted._startup_terminal_cleanup(cfg))

    current = _required_release_gate(restarted)
    assert current.status == "pending"
    assert current.generation != completed.generation
    assert current.approved_fingerprint is None
    assert current.finalizer_run_id is None
    assert current.finalizer_completed_at is None
    assert current.finalizer_completion_token is None
    tracker = FileBoardTracker(cfg.tracker)
    persisted_verifier = tracker.fetch_issue_full_by_id(
        completed.verifier_identifier
    )
    persisted_finalizer = tracker.fetch_issue_full_by_id(
        completed.finalizer_identifier
    )
    tracker.close()
    assert persisted_verifier is not None
    assert persisted_finalizer is not None
    assert persisted_verifier.state == "Verify"
    assert persisted_finalizer.state == "Document"
    assert cleanup_claims == []
    assert removed == []


def test_startup_preserves_workspace_for_active_peer_finalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    approval_orchestrator = _orch(repo)
    authority = approval_orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=approval_orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    assert approval_orchestrator._run_registry is not None
    approval_orchestrator._run_registry.close()
    approval_orchestrator._run_registry = None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done")
    tracker.update_fields("APP-FINAL", state="Done")
    finalizer = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    assert finalizer is not None

    peer = RunRegistry(registry_path_for_workflow(cfg.workflow_path))
    peer_run = peer.acquire_run(
        finalizer,
        workspace_path=tmp_path / "peer-finalizer-workspace",
        attempt=None,
        attempt_kind="release-finalizer",
        agent_kind="codex",
    )
    assert peer_run is not None
    assert peer.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=peer_run,
    )

    workspace = tmp_path / "peer-finalizer-workspace"
    workspace.mkdir()
    restarted = _orch(workspace)
    monkeypatch.setattr(
        restarted,
        "_tracker_call_terminal_issues",
        lambda _cfg: [finalizer],
    )
    removed: list[Path] = []

    async def capture_remove(path: Path) -> None:
        removed.append(path)

    assert restarted._workspace_manager is not None
    monkeypatch.setattr(
        restarted._workspace_manager,
        "remove",
        capture_remove,
        raising=False,
    )

    asyncio.run(restarted._startup_terminal_cleanup(cfg))

    tracker = FileBoardTracker(cfg.tracker)
    persisted = tracker.fetch_issue_full_by_id("APP-FINAL")
    tracker.close()
    peer.close()
    assert persisted is not None
    assert persisted.state == "Done"
    assert removed == []


def test_startup_cleanup_does_not_remove_after_peer_binds_new_release_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    approval_orchestrator = _orch(repo)
    authority = approval_orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=approval_orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    assert approval_orchestrator._run_registry is not None
    approval_orchestrator._run_registry.close()
    approval_orchestrator._run_registry = None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done")
    terminal_verifier = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert terminal_verifier is not None

    workspace = tmp_path / "verifier-workspace"
    workspace.mkdir()
    restarted = _orch(workspace)
    monkeypatch.setattr(
        restarted,
        "_tracker_call_terminal_issues",
        lambda _cfg: [terminal_verifier],
    )
    removed: list[Path] = []

    async def capture_remove(path: Path) -> None:
        removed.append(path)

    assert restarted._workspace_manager is not None
    monkeypatch.setattr(
        restarted._workspace_manager,
        "remove",
        capture_remove,
        raising=False,
    )

    peer = RunRegistry(registry_path_for_workflow(cfg.workflow_path))
    original_guard = restarted._startup_release_terminal_guard
    peer_runs: list[str] = []

    async def guard_then_start_peer(
        guard_cfg, guard_issue: Issue
    ) -> tuple[Issue, bool, bool]:
        guarded = await original_guard(guard_cfg, guard_issue)
        assert guarded[1:] == (True, False)
        pending = peer.replace_pending_release_gate(
            replace(
                approved,
                approved_fingerprint=None,
                status="pending",
                target_branch=None,
                approved_target_sha=None,
                verifier_run_id=None,
                finalizer_run_id=None,
                finalizer_completed_at=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        tracker = FileBoardTracker(cfg.tracker)
        tracker.update_fields("VERIFY-1", state="Verify")
        reopened = tracker.fetch_issue_full_by_id("VERIFY-1")
        tracker.close()
        assert reopened is not None
        peer_run = peer.acquire_run(
            reopened,
            workspace_path=workspace,
            attempt=1,
            attempt_kind="fresh-release-verification",
            agent_kind="codex",
        )
        assert peer_run is not None
        assert peer.bind_release_verifier_run(
            gate=pending,
            verifier_run_id=peer_run,
        )
        peer_runs.append(peer_run)
        return guarded

    monkeypatch.setattr(
        restarted,
        "_startup_release_terminal_guard",
        guard_then_start_peer,
    )

    asyncio.run(restarted._startup_terminal_cleanup(cfg))

    assert len(peer_runs) == 1
    assert peer.has_active_lease(terminal_verifier.id)
    current = peer.get_release_gate("APP-FINAL")
    assert current is not None
    assert current.status == "pending"
    assert current.verifier_run_id == peer_runs[0]
    peer.close()
    assert removed == []


def test_startup_cleanup_claim_blocks_release_gate_replacement(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    orchestrator = _orch(repo)
    authority = orchestrator._prepare_release_dispatch(verifier, cfg)
    assert authority.gate is not None
    approved, _validation = _approve_current_release_gate(
        orchestrator=orchestrator,
        cfg=cfg,
        gate=authority.gate,
    )
    registry = orchestrator._run_registry
    assert registry is not None

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done")
    terminal_verifier = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert terminal_verifier is not None
    cleanup_run = registry.acquire_run(
        terminal_verifier,
        workspace_path=tmp_path / "verifier-workspace",
        attempt=None,
        attempt_kind="startup-release-evidence-cleanup",
        agent_kind="codex",
    )
    assert cleanup_run is not None

    replacement = replace(
        approved,
        approved_fingerprint=None,
        status="pending",
        target_branch=None,
        approved_target_sha=None,
        verifier_run_id=None,
        finalizer_run_id=None,
        finalizer_completed_at=None,
        updated_at=datetime.now(timezone.utc),
    )
    with pytest.raises(RuntimeError, match="cleanup or foreign lease must finish"):
        registry.replace_pending_release_gate(replacement)

    assert registry.has_active_lease(terminal_verifier.id)
    assert registry.get_release_gate("APP-FINAL") == approved


@requires_symlink_privilege
def test_normal_release_worker_exit_holds_lease_until_terminal_cleanup_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    worker = _release_worker_workspace(repo)
    orchestrator = _orch(worker)
    _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=verifier,
        workspace_path=worker,
    )
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(verifier.identifier, state="Done")
    terminal_verifier = tracker.fetch_issue_full_by_id(verifier.identifier)
    tracker.close()
    assert terminal_verifier is not None
    monkeypatch.setattr(orchestrator._workflow_state, "current", lambda: cfg)
    peer = RunRegistry(registry_path_for_workflow(cfg.workflow_path))
    removed: list[Path] = []

    async def exercise_exit() -> None:
        transitioned, rewound = await orchestrator._enforce_app_release_transition(
            cfg=cfg,
            issue=terminal_verifier,
            workspace_path=worker,
            producing_state="Verify",
            known_app_release=True,
        )
        assert not rewound
        orchestrator._running[verifier.id].issue = transitioned
        remove_started = asyncio.Event()
        allow_remove = asyncio.Event()

        async def blocking_remove(path: Path) -> None:
            removed.append(path)
            remove_started.set()
            await allow_remove.wait()

        assert orchestrator._workspace_manager is not None
        monkeypatch.setattr(
            orchestrator._workspace_manager,
            "remove",
            blocking_remove,
            raising=False,
        )
        exit_task = asyncio.create_task(
            orchestrator._on_worker_exit(verifier.id, "normal", None)
        )
        await asyncio.wait_for(remove_started.wait(), timeout=1)
        try:
            assert peer.has_active_lease(verifier.id)
            assert (
                peer.acquire_run(
                    transitioned,
                    workspace_path=tmp_path / "contender",
                    attempt=1,
                    attempt_kind="contending-release-verification",
                    agent_kind="codex",
                )
                is None
            )
        finally:
            allow_remove.set()
            await asyncio.wait_for(exit_task, timeout=1)
        assert not peer.has_active_lease(verifier.id)

    try:
        asyncio.run(exercise_exit())
    finally:
        peer.close()

    assert removed == [worker]


@requires_symlink_privilege
def test_reconcile_terminal_release_holds_lease_until_cleanup_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    worker = _release_worker_workspace(repo)
    orchestrator = _orch(worker)
    _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=verifier,
        workspace_path=worker,
    )
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(verifier.identifier, state="Done")
    terminal_verifier = tracker.fetch_issue_full_by_id(verifier.identifier)
    tracker.close()
    assert terminal_verifier is not None
    monkeypatch.setattr(orchestrator._workflow_state, "current", lambda: cfg)
    peer = RunRegistry(registry_path_for_workflow(cfg.workflow_path))
    removed: list[Path] = []

    async def exercise_reconcile() -> None:
        remove_started = asyncio.Event()
        allow_remove = asyncio.Event()
        worker_started = asyncio.Event()
        wait_for_cancel = asyncio.Event()

        async def blocking_remove(path: Path) -> None:
            removed.append(path)
            remove_started.set()
            await allow_remove.wait()

        assert orchestrator._workspace_manager is not None
        monkeypatch.setattr(
            orchestrator._workspace_manager,
            "remove",
            blocking_remove,
            raising=False,
        )
        entry = orchestrator._running[verifier.id]

        async def worker() -> None:
            worker_started.set()
            try:
                await wait_for_cancel.wait()
            except asyncio.CancelledError:
                entry.exit_started_at = datetime.now(timezone.utc)
                await orchestrator._on_worker_exit(
                    verifier.id,
                    "normal",
                    None,
                    owning_task=asyncio.current_task(),
                )

        worker_task = asyncio.create_task(worker())
        entry.worker_task = worker_task
        await asyncio.wait_for(worker_started.wait(), timeout=1)
        reconcile_task = asyncio.create_task(
            orchestrator._reconcile_one(
                terminal_verifier,
                entry,
                cfg,
                active={state.lower() for state in cfg.tracker.active_states},
                terminal={state.lower() for state in cfg.tracker.terminal_states},
                now=datetime.now(timezone.utc),
                recent_grace_s=0,
            )
        )
        await asyncio.wait_for(remove_started.wait(), timeout=1)
        try:
            await asyncio.sleep(0)
            assert peer.has_active_lease(verifier.id)
            assert not worker_task.done()
            assert not entry.workspace_cleanup_finished.is_set()
        finally:
            allow_remove.set()
            await asyncio.wait_for(reconcile_task, timeout=1)
            await asyncio.wait_for(worker_task, timeout=1)
        assert not peer.has_active_lease(verifier.id)

    try:
        asyncio.run(exercise_reconcile())
    finally:
        peer.close()

    assert removed == [worker]


def test_reconcile_late_app_release_label_rewinds_before_cleanup_or_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, _verifier = _setup_board(repo)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", labels=[])
    ordinary_verifier = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert ordinary_verifier is not None
    assert ordinary_verifier.labels == ()

    orchestrator = _orch(repo)
    orchestrator._ensure_run_registry(cfg)
    registry = orchestrator._run_registry
    assert registry is not None
    run_id = registry.acquire_run(
        ordinary_verifier,
        workspace_path=repo,
        attempt=None,
        attempt_kind="ordinary-verify",
        agent_kind="codex",
    )
    assert run_id is not None
    _seed_running_entry(orchestrator, ordinary_verifier, repo)
    entry = orchestrator._running[ordinary_verifier.id]
    entry.run_id = run_id
    entry.release_authority_resolved = True

    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields("VERIFY-1", state="Done", labels=["app-release"])
    late_labeled = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert late_labeled is not None
    remove_calls: list[Path] = []
    merge_calls: list[str] = []
    delivery_calls: list[str] = []

    async def capture_remove(path: Path) -> None:
        remove_calls.append(path)

    async def capture_merge(*_args: object, **_kwargs: object) -> bool:
        merge_calls.append("merge")
        return True

    async def capture_delivery(*_args: object, **_kwargs: object) -> None:
        delivery_calls.append("delivery")

    assert orchestrator._workspace_manager is not None
    monkeypatch.setattr(
        orchestrator._workspace_manager,
        "remove",
        capture_remove,
        raising=False,
    )
    monkeypatch.setattr(
        orchestrator,
        "_auto_merge_done_gate_or_block",
        capture_merge,
    )
    monkeypatch.setattr(
        orchestrator,
        "_after_done_then_remove_per_policy",
        capture_delivery,
    )

    asyncio.run(
        orchestrator._reconcile_one(
            late_labeled,
            entry,
            cfg,
            active={state.lower() for state in cfg.tracker.active_states},
            terminal={state.lower() for state in cfg.tracker.terminal_states},
            now=datetime.now(timezone.utc),
            recent_grace_s=0,
        )
    )

    tracker = FileBoardTracker(cfg.tracker)
    persisted = tracker.fetch_issue_full_by_id("VERIFY-1")
    tracker.close()
    assert persisted is not None
    assert persisted.state == "Verify"
    assert "added after this run acquired an ordinary lease" in (
        persisted.description or ""
    )
    gate = registry.get_release_gate("APP-FINAL")
    assert gate is not None
    assert gate.status == "pending"
    assert gate.verifier_run_id is None
    assert registry.has_active_lease(ordinary_verifier.id)
    assert remove_calls == []
    assert merge_calls == []
    assert delivery_calls == []
    assert registry.complete_run(
        issue_id=ordinary_verifier.id,
        run_id=run_id,
        status="late-release-label-rewound",
    )


def test_pending_contract_hash_drift_replaces_generation_and_requires_fresh_run(
    tmp_path: Path,
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, _ticket_path, verifier = _setup_board(repo)
    orchestrator = _orch(repo)
    original_gate, original_run_id = _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=verifier,
        workspace_path=repo,
    )
    contract_path = repo / "release-contract.yaml"
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8") + "\n# policy drift\n",
        encoding="utf-8",
    )
    current_contract_hash = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    tracker = FileBoardTracker(cfg.tracker)
    tracker.update_fields(verifier.identifier, state="Done")
    terminal_verifier = tracker.fetch_issue_full_by_id(verifier.identifier)
    tracker.close()
    assert terminal_verifier is not None

    rewound_issue, rewound = asyncio.run(
        orchestrator._enforce_app_release_transition(
            cfg=cfg,
            issue=terminal_verifier,
            workspace_path=repo,
            producing_state="Verify",
            known_app_release=True,
        )
    )

    assert rewound
    assert rewound_issue.state == "Verify"
    registry = orchestrator._run_registry
    assert registry is not None
    refreshed_gate = registry.get_release_gate("APP-FINAL")
    assert refreshed_gate is not None
    assert refreshed_gate.status == "pending"
    assert refreshed_gate.generation != original_gate.generation
    assert refreshed_gate.expected_contract_sha256 == current_contract_hash
    assert refreshed_gate.verifier_run_id is None
    assert not registry.release_verifier_run_is_authorized(
        gate=refreshed_gate,
        verifier_issue_id=verifier.id,
    )
    assert registry.has_active_lease(verifier.id)
    assert registry.complete_run(
        issue_id=verifier.id,
        run_id=original_run_id,
        status="stale-contract-generation",
    )

    fresh_run_id = registry.acquire_run(
        rewound_issue,
        workspace_path=repo,
        attempt=1,
        attempt_kind="fresh-contract-verification",
        agent_kind="codex",
    )
    assert fresh_run_id is not None
    assert fresh_run_id != original_run_id
    assert registry.bind_release_verifier_run(
        gate=refreshed_gate,
        verifier_run_id=fresh_run_id,
    )
    assert registry.release_verifier_run_is_authorized(
        gate=replace(refreshed_gate, verifier_run_id=fresh_run_id),
        verifier_issue_id=verifier.id,
    )
    assert registry.complete_run(
        issue_id=verifier.id,
        run_id=fresh_run_id,
        status="fresh-contract-run-proven",
    )



@requires_symlink_privilege
def test_red_verifier_handoff_exits_normal_and_releases_its_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)
    cfg = replace(cfg, agent=replace(cfg.agent, auto_commit_on_done=True))

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "menu control is inert"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    backends = _install_file_tracker_backend(
        monkeypatch,
        ticket_path=ticket_path,
        transitions=[("Document", issue.description or "")],
    )
    worker = _release_worker_workspace(repo)
    orchestrator = _orch(worker)
    orchestrator._workflow_state._config = cfg
    original_gate, run_id = _seed_active_release_verifier(
        orchestrator=orchestrator,
        cfg=cfg,
        issue=issue,
        workspace_path=worker,
    )
    entry = orchestrator._running[issue.id]
    snapshots: list[Path] = []
    removals: list[Path] = []

    async def record_snapshot(path: Path, **_kwargs: object) -> None:
        snapshots.append(path)

    async def record_remove(path: Path) -> None:
        removals.append(path)

    async def forbidden_delivery(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("historical verifier must not enter delivery")

    monkeypatch.setattr(
        "symphony.orchestrator.core.commit_workspace_on_done", record_snapshot
    )
    manager = orchestrator._workspace_manager
    assert manager is not None
    monkeypatch.setattr(manager, "remove", record_remove, raising=False)
    monkeypatch.setattr(
        orchestrator, "_auto_merge_done_gate_or_block", forbidden_delivery
    )

    async def exercise() -> None:
        orchestrator._loop = asyncio.get_running_loop()
        await orchestrator._run_agent_attempt(entry.issue, attempt=None, cfg=cfg)

    asyncio.run(exercise())

    registry = orchestrator._run_registry
    assert registry is not None
    retired = registry.get_release_evidence_identity_by_issue_id(issue.id)
    assert retired is not None
    assert retired.retired
    assert retired.cycle_generation == original_gate.generation
    assert registry.get_run(run_id).status == "normal"
    assert entry.release_verifier_handoff_complete
    assert len(backends) == 1
    assert len([call for call in backends[0].calls if call[0] == "run_turn"]) == 1
    assert issue.id not in orchestrator._running
    assert issue.id not in orchestrator._retry
    assert issue.id not in orchestrator._paused_issue_ids
    assert issue.id not in orchestrator._persisted_retry_attempts
    assert registry.get_issue_flags(issue.id) is None
    assert orchestrator._dispatch_state.available_slots(1) == 1
    assert snapshots == [worker]
    assert removals == [worker]


@requires_symlink_privilege
def test_restart_recovers_only_exact_completed_red_handoff_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _setup_repo(tmp_path)
    cfg, ticket_path, issue = _setup_board(repo)

    def fail_feature(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        evidence["runner"]["exit_code"] = 1

    _mutate_evidence(repo, fail_feature)
    _sync_native_statuses(repo)
    completed = _run_verify_transition(
        repo=repo,
        cfg=cfg,
        ticket_path=ticket_path,
        issue=issue,
        monkeypatch=monkeypatch,
    )
    registry = completed._run_registry
    assert registry is not None
    current_gate = registry.get_release_gate("APP-FINAL")
    assert current_gate is not None
    assert current_gate.verifier_issue_id != issue.id
    registry.set_issue_flags(
        issue.id,
        retry_attempt=2,
        budget_exhausted=True,
        paused=True,
        pause_reason="worker error: release_authority_error",
    )
    registry.close()
    completed._run_registry = None

    restarted = _orch(repo)
    restarted._ensure_run_registry(cfg)
    recovered_registry = restarted._run_registry
    assert recovered_registry is not None
    recovered = recovered_registry.get_issue_flags(issue.id)
    assert recovered is not None
    assert recovered.retry_attempt is None
    assert recovered.paused is False
    assert recovered.pause_reason is None
    assert recovered.budget_exhausted is True
    assert issue.id not in restarted._persisted_retry_attempts
    assert issue.id not in restarted._paused_issue_ids
    assert issue.id in restarted._turn_budget_exhausted

    tracker = FileBoardTracker(cfg.tracker)
    source = tracker.fetch_issue_full_by_id(issue.identifier)
    current_verifier = tracker.fetch_issue_full_by_id(current_gate.verifier_identifier)
    assert source is not None
    assert current_verifier is not None
    decision = restarted._eligibility_ownership_decision(
        source, cfg, owning_retry=True
    )
    assert decision is not None
    assert "historical release verifier" in decision.reason

    # A manual hold with no retry is not crash residue and must survive.
    recovered_registry.set_issue_flags(
        issue.id,
        retry_attempt=None,
        paused=True,
        pause_reason="manual release review",
    )
    recovered_registry.close()
    restarted._run_registry = None
    manual_restart = _orch(repo)
    manual_restart._ensure_run_registry(cfg)
    manual_registry = manual_restart._run_registry
    assert manual_registry is not None
    manual_flags = manual_registry.get_issue_flags(issue.id)
    assert manual_flags is not None
    assert manual_flags.retry_attempt is None
    assert manual_flags.paused is True
    assert manual_flags.pause_reason == "manual release review"

    # A retry is preserved when the finalizer no longer proves the exact
    # old -> new relink, and a genuinely current verifier is never recovered
    # merely because another issue has retired evidence.
    tracker.update_fields(
        "APP-FINAL",
        blocked_by=[issue.identifier, "UNRELATED-1"],
    )
    tracker.close()
    manual_registry.set_issue_flags(
        issue.id,
        retry_attempt=3,
        paused=True,
        pause_reason="worker error: release_authority_error",
    )
    manual_registry.set_issue_flags(
        current_gate.verifier_issue_id,
        retry_attempt=4,
        paused=True,
        pause_reason="worker error: lost lease",
    )
    manual_registry.close()
    manual_restart._run_registry = None

    negative_restart = _orch(repo)
    negative_restart._ensure_run_registry(cfg)
    negative_registry = negative_restart._run_registry
    assert negative_registry is not None
    unrelated_change = negative_registry.get_issue_flags(issue.id)
    lost_lease = negative_registry.get_issue_flags(current_gate.verifier_issue_id)
    assert unrelated_change is not None
    assert unrelated_change.retry_attempt == 3
    assert unrelated_change.paused is True
    assert unrelated_change.budget_exhausted is True
    assert lost_lease is not None
    assert lost_lease.retry_attempt == 4
    assert lost_lease.paused is True

    def unreadable_identity(_issue_id: str):
        raise sqlite3.OperationalError("registry unavailable")

    monkeypatch.setattr(
        negative_registry,
        "get_release_evidence_identity_by_issue_id",
        unreadable_identity,
    )
    unreadable = negative_restart._eligibility_ownership_decision(
        source, cfg, owning_retry=False
    )
    assert unreadable is not None
    assert unreadable.reason == "release evidence authority could not be read"

    negative_restart._run_registry = None
    negative_restart._last_registry_error = "open: registry unavailable"
    unopened = negative_restart._eligibility_ownership_decision(
        source, cfg, owning_retry=False
    )
    assert unopened is not None
    assert unopened.reason == "release evidence authority registry is unavailable"
