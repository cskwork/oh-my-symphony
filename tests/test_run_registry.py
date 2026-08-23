from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import symphony.orchestrator.migrations as migration_mod
import symphony.orchestrator.run_registry as run_registry_mod
from symphony.issue import Issue
from symphony.orchestrator.run_registry import ReleaseGate, RunRegistry


def _issue(identifier: str = "MT-1") -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="",
        priority=None,
        state="In Progress",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_run_registry_active_lease_blocks_second_claim(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(seconds=60))
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()

    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )

    assert run_id
    assert (
        registry.acquire_run(
            issue,
            workspace_path=tmp_path / "ws" / issue.identifier,
            attempt=None,
            attempt_kind="initial",
            agent_kind="codex",
            now=now + timedelta(seconds=1),
        )
        is None
    )
    assert registry.has_active_lease(issue.id, now=now + timedelta(seconds=1)) is True


def test_run_registry_heartbeat_persists_backend_agent_pid(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(seconds=60))
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="opencode",
        now=now,
    )
    assert run_id

    assert registry.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=1),
        backend_agent_pid=4242,
    )

    assert registry.get_run(run_id).backend_agent_pid == 4242


def test_run_registry_clear_backend_agent_pid_is_explicit(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(seconds=60))
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="opencode",
        now=now,
    )
    assert run_id
    assert registry.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=1),
        backend_agent_pid=4242,
    )

    # A normal heartbeat with no pid deliberately preserves ownership.
    assert registry.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=2),
    )
    assert registry.get_run(run_id).backend_agent_pid == 4242

    assert registry.clear_backend_agent_pid(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=3),
    )
    assert registry.get_run(run_id).backend_agent_pid is None


def test_run_registry_expires_stale_lease_before_reclaim(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(seconds=30))
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    workspace = tmp_path / "ws" / issue.identifier

    first_run = registry.acquire_run(
        issue,
        workspace_path=workspace,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )

    assert first_run
    assert registry.expire_stale(now=now + timedelta(seconds=31)) == 1
    second_run = registry.acquire_run(
        issue,
        workspace_path=workspace,
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
        now=now + timedelta(seconds=32),
    )
    assert second_run
    assert second_run != first_run
    expired = registry.get_run(first_run)
    assert expired.status == "expired"
    assert expired.failure_class == "lease_expired"
    assert registry.run_events(first_run)[-1]["event_type"] == "run_completed"
    assert registry.get_run(second_run).status == "active"


def test_run_registry_survives_reopen_and_releases_completed_run(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    workspace = tmp_path / "ws" / issue.identifier

    registry = RunRegistry(path, lease_ttl=timedelta(seconds=60))
    run_id = registry.acquire_run(
        issue,
        workspace_path=workspace,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    registry.close()

    reopened = RunRegistry(path, lease_ttl=timedelta(seconds=60))
    assert reopened.has_active_lease(issue.id, now=now + timedelta(seconds=1)) is True

    reopened.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="normal",
        now=now + timedelta(seconds=2),
    )

    assert reopened.has_active_lease(issue.id, now=now + timedelta(seconds=3)) is False
    assert reopened.get_run(run_id).status == "normal"


def test_release_evidence_identity_lookup_by_issue_id_is_exact(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    verifier = _issue("RELEASE-VERIFY-LOOKUP")
    gate = registry.replace_pending_release_gate(
        ReleaseGate(
            finalizer_identifier="APP-FINAL",
            verifier_issue_id=verifier.id,
            verifier_identifier=verifier.identifier,
            expected_contract_sha256="a" * 64,
            cycle_fingerprint="b" * 64,
            approved_fingerprint=None,
            status="pending",
            target_branch=None,
            approved_target_sha=None,
            verifier_run_id=None,
            updated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
    )

    identity = registry.get_release_evidence_identity_by_issue_id(verifier.id)

    assert identity is not None
    assert identity.issue_id == verifier.id
    assert identity.identifier == verifier.identifier
    assert identity.cycle_generation == gate.generation
    assert registry.get_release_evidence_identity_by_issue_id("missing") is None


def test_release_gate_survives_restart_approves_exact_tuple_and_invalidates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    updated_at = datetime(2026, 8, 9, 1, 2, tzinfo=timezone.utc)
    verifier = _issue("RELEASE-VERIFY-7")
    pending = ReleaseGate(
        finalizer_identifier="APP-FINAL",
        verifier_issue_id=verifier.id,
        verifier_identifier="RELEASE-VERIFY-7",
        expected_contract_sha256="b" * 64,
        cycle_fingerprint="c" * 64,
        approved_fingerprint=None,
        status="pending",
        target_branch=None,
        approved_target_sha=None,
        verifier_run_id=None,
        updated_at=updated_at,
    )

    registry = RunRegistry(path)
    pending = registry.replace_pending_release_gate(pending, now=updated_at)
    registry.close()

    reopened = RunRegistry(path)
    assert reopened.get_release_gate("APP-FINAL") == pending
    assert reopened.get_release_gate_for_verifier("RELEASE-VERIFY-7") == pending
    run_id = reopened.acquire_run(
        verifier,
        workspace_path=tmp_path / "ws" / verifier.identifier,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
        now=updated_at,
    )
    assert run_id is not None
    assert reopened.bind_release_verifier_run(
        gate=pending,
        verifier_run_id=run_id,
        now=updated_at,
    )
    assert reopened.approve_release_gate(
        finalizer_identifier="APP-FINAL",
        verifier_issue_id="wrong-issue",
        verifier_identifier="RELEASE-VERIFY-7",
        expected_contract_sha256="b" * 64,
        expected_cycle_fingerprint="c" * 64,
        expected_generation=pending.generation,
        approved_fingerprint="d" * 64,
        target_branch="main",
        target_sha="a" * 40,
        verifier_run_id=run_id,
        now=updated_at,
    ) is False
    assert reopened.approve_release_gate(
        finalizer_identifier="APP-FINAL",
        verifier_issue_id=verifier.id,
        verifier_identifier="RELEASE-VERIFY-7",
        expected_contract_sha256="b" * 64,
        expected_cycle_fingerprint="c" * 64,
        expected_generation=pending.generation,
        approved_fingerprint="d" * 64,
        target_branch="main",
        target_sha="a" * 40,
        verifier_run_id=run_id,
        now=updated_at,
    ) is True
    approved = reopened.get_release_gate("APP-FINAL")
    assert approved is not None
    assert approved.status == "approved"
    assert approved.target_branch == "main"
    assert approved.approved_target_sha == "a" * 40
    assert approved.approved_fingerprint == "d" * 64
    assert approved.verifier_run_id == run_id
    assert reopened.invalidate_release_gate("APP-FINAL") is True
    assert reopened.get_release_gate("APP-FINAL") is None


def test_release_cycle_item_binding_is_immutable_and_survives_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    issue = _issue("QUALITY-7")
    registry = RunRegistry(path)

    recorded = registry.record_release_cycle_item(
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="a" * 64,
        item_role="repair",
        item_key="frontend",
        issue=issue,
    )
    same = registry.record_release_cycle_item(
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="a" * 64,
        item_role="repair",
        item_key="frontend",
        issue=issue,
    )
    with pytest.raises(RuntimeError, match="different ticket"):
        registry.record_release_cycle_item(
            finalizer_identifier="APP-FINAL",
            cycle_fingerprint="a" * 64,
            item_role="repair",
            item_key="frontend",
            issue=_issue("QUALITY-8"),
        )
    registry.close()

    reopened = RunRegistry(path)
    persisted = reopened.get_release_cycle_item(
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="a" * 64,
        item_role="repair",
        item_key="frontend",
    )
    assert recorded.identifier == issue.identifier
    assert same.identifier == issue.identifier
    assert persisted is not None
    assert persisted.issue_id == issue.id
    assert persisted.identifier == issue.identifier


def test_old_verifier_run_cannot_bind_pending_committed_after_lease(
    tmp_path: Path,
) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(minutes=5))
    verifier = _issue("RELEASE-VERIFY-7")
    constructed_at = datetime(2026, 8, 9, 1, 0, tzinfo=timezone.utc)
    lease_started_at = constructed_at + timedelta(seconds=1)
    pending_committed_at = constructed_at + timedelta(seconds=2)
    caller_snapshot = ReleaseGate(
        finalizer_identifier="APP-FINAL",
        verifier_issue_id=verifier.id,
        verifier_identifier=verifier.identifier,
        expected_contract_sha256="b" * 64,
        cycle_fingerprint="c" * 64,
        approved_fingerprint=None,
        status="pending",
        target_branch=None,
        approved_target_sha=None,
        verifier_run_id=None,
        updated_at=constructed_at,
    )
    old_run = registry.acquire_run(
        verifier,
        workspace_path=tmp_path / "ws" / verifier.identifier,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
        now=lease_started_at,
    )
    assert old_run is not None

    committed = registry.replace_pending_release_gate(
        caller_snapshot,
        now=pending_committed_at,
    )

    assert committed.updated_at == pending_committed_at
    assert committed.generation
    assert not registry.bind_release_verifier_run(
        gate=committed,
        verifier_run_id=old_run,
        now=pending_committed_at,
    )


def test_finalizer_continuation_rebinds_after_nonterminal_run(
    tmp_path: Path,
) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(minutes=5))
    verifier = _issue("RELEASE-VERIFY-7")
    finalizer = _issue("APP-FINAL")
    now = datetime(2026, 8, 9, 1, 0, tzinfo=timezone.utc)
    pending = registry.replace_pending_release_gate(
        ReleaseGate(
            finalizer_identifier=finalizer.identifier,
            verifier_issue_id=verifier.id,
            verifier_identifier=verifier.identifier,
            expected_contract_sha256="b" * 64,
            cycle_fingerprint="c" * 64,
            approved_fingerprint=None,
            status="pending",
            target_branch=None,
            approved_target_sha=None,
            verifier_run_id=None,
            updated_at=now,
        ),
        now=now,
    )
    verifier_run = registry.acquire_run(
        verifier,
        workspace_path=tmp_path / "ws" / verifier.identifier,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
        now=now + timedelta(seconds=1),
    )
    assert verifier_run is not None
    assert registry.bind_release_verifier_run(
        gate=pending,
        verifier_run_id=verifier_run,
        now=now + timedelta(seconds=1),
    )
    assert registry.approve_release_gate(
        finalizer_identifier=finalizer.identifier,
        verifier_issue_id=verifier.id,
        verifier_identifier=verifier.identifier,
        expected_contract_sha256=pending.expected_contract_sha256,
        expected_cycle_fingerprint=pending.cycle_fingerprint,
        expected_generation=pending.generation,
        approved_fingerprint="d" * 64,
        target_branch="main",
        target_sha="a" * 40,
        verifier_run_id=verifier_run,
        now=now + timedelta(seconds=2),
    )
    assert registry.complete_run(
        issue_id=verifier.id,
        run_id=verifier_run,
        status="normal",
        now=now + timedelta(seconds=3),
    )
    approved = registry.get_release_gate(finalizer.identifier)
    assert approved is not None

    first_finalizer_run = registry.acquire_run(
        finalizer,
        workspace_path=tmp_path / "ws" / finalizer.identifier,
        attempt=None,
        attempt_kind="release-finalizer",
        agent_kind="codex",
        now=now + timedelta(seconds=4),
    )
    assert first_finalizer_run is not None
    assert registry.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=first_finalizer_run,
        now=now + timedelta(seconds=4),
    )
    assert registry.complete_run(
        issue_id=finalizer.id,
        run_id=first_finalizer_run,
        status="normal",
        now=now + timedelta(seconds=5),
    )
    first_bound = registry.get_release_gate(finalizer.identifier)
    assert first_bound is not None
    assert first_bound.finalizer_completed_at is None

    continuation_run = registry.acquire_run(
        finalizer,
        workspace_path=tmp_path / "ws" / finalizer.identifier,
        attempt=1,
        attempt_kind="continuation",
        agent_kind="codex",
        now=now + timedelta(seconds=6),
    )
    assert continuation_run is not None
    assert registry.bind_release_finalizer_run(
        gate=first_bound,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=continuation_run,
        now=now + timedelta(seconds=6),
    )
    rebound = registry.get_release_gate(finalizer.identifier)
    assert rebound is not None
    assert rebound.finalizer_run_id == continuation_run


def test_completed_finalizer_authorization_carries_persisted_host_token(
    tmp_path: Path,
) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(minutes=5))
    verifier = _issue("RELEASE-VERIFY-7")
    finalizer = _issue("APP-FINAL")
    now = datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc)
    pending = registry.replace_pending_release_gate(
        ReleaseGate(
            finalizer_identifier=finalizer.identifier,
            verifier_issue_id=verifier.id,
            verifier_identifier=verifier.identifier,
            expected_contract_sha256="b" * 64,
            cycle_fingerprint="c" * 64,
            approved_fingerprint=None,
            status="pending",
            target_branch=None,
            approved_target_sha=None,
            verifier_run_id=None,
            updated_at=now,
        ),
        now=now,
    )
    verifier_run = registry.acquire_run(
        verifier,
        workspace_path=tmp_path / "ws" / verifier.identifier,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
        now=now + timedelta(seconds=1),
    )
    assert verifier_run is not None
    assert registry.bind_release_verifier_run(
        gate=pending,
        verifier_run_id=verifier_run,
        now=now + timedelta(seconds=1),
    )
    assert registry.approve_release_gate(
        finalizer_identifier=finalizer.identifier,
        verifier_issue_id=verifier.id,
        verifier_identifier=verifier.identifier,
        expected_contract_sha256=pending.expected_contract_sha256,
        expected_cycle_fingerprint=pending.cycle_fingerprint,
        expected_generation=pending.generation,
        approved_fingerprint="d" * 64,
        target_branch="main",
        target_sha="a" * 40,
        verifier_run_id=verifier_run,
        now=now + timedelta(seconds=2),
    )
    assert registry.complete_run(
        issue_id=verifier.id,
        run_id=verifier_run,
        status="normal",
        now=now + timedelta(seconds=3),
    )
    approved = registry.get_release_gate(finalizer.identifier)
    assert approved is not None
    finalizer_run = registry.acquire_run(
        finalizer,
        workspace_path=tmp_path / "ws" / finalizer.identifier,
        attempt=None,
        attempt_kind="release-finalizer",
        agent_kind="codex",
        now=now + timedelta(seconds=4),
    )
    assert finalizer_run is not None
    assert registry.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=finalizer_run,
        now=now + timedelta(seconds=4),
    )
    bound = registry.get_release_gate(finalizer.identifier)
    assert bound is not None
    assert not registry.mark_release_finalizer_completed(
        gate=bound,
        finalizer_issue_id=finalizer.id,
        completion_token="",
        now=now + timedelta(seconds=5),
    )
    assert not registry.mark_release_finalizer_completed(
        gate=bound,
        finalizer_issue_id=finalizer.id,
        completion_token="   ",
        now=now + timedelta(seconds=5),
    )

    completion_token = "host-ticket-version-" + "e" * 64
    assert registry.mark_release_finalizer_completed(
        gate=bound,
        finalizer_issue_id=finalizer.id,
        completion_token=completion_token,
        now=now + timedelta(seconds=5),
    )
    completed_authorization = registry.get_release_gate(finalizer.identifier)
    assert completed_authorization is not None
    assert completed_authorization.finalizer_completed_at is not None
    assert completed_authorization.finalizer_completion_token == completion_token
    assert registry.complete_run(
        issue_id=finalizer.id,
        run_id=finalizer_run,
        status="normal",
        now=now + timedelta(seconds=6),
    )

    assert not registry.release_finalizer_run_is_authorized(
        gate=bound,
        finalizer_issue_id=finalizer.id,
        allow_active=False,
        now=now + timedelta(seconds=7),
    )
    assert registry.release_finalizer_run_is_authorized(
        gate=completed_authorization,
        finalizer_issue_id=finalizer.id,
        allow_active=False,
        now=now + timedelta(seconds=7),
    )


@pytest.mark.parametrize(
    ("run_case", "expected_approval"),
    [
        ("fake", False),
        ("completed", False),
        ("expired", False),
        ("reclaiming", False),
        ("foreign", False),
        ("active", True),
    ],
)
def test_approve_release_gate_requires_exact_active_bound_run(
    tmp_path: Path,
    run_case: str,
    expected_approval: bool,
) -> None:
    path = tmp_path / "state.db"
    lease_ttl = timedelta(seconds=10)
    now = datetime(2026, 8, 9, 1, 0, tzinfo=timezone.utc)
    verifier = _issue("RELEASE-VERIFY-7")
    registry = RunRegistry(
        path,
        lease_ttl=lease_ttl,
        owner_pid=4242,
        boot_id="bound-run-owner",
    )
    pending = registry.replace_pending_release_gate(
        ReleaseGate(
            finalizer_identifier="APP-FINAL",
            verifier_issue_id=verifier.id,
            verifier_identifier=verifier.identifier,
            expected_contract_sha256="b" * 64,
            cycle_fingerprint="c" * 64,
            approved_fingerprint=None,
            status="pending",
            target_branch=None,
            approved_target_sha=None,
            verifier_run_id=None,
            updated_at=now,
        ),
        now=now,
    )
    bound_run = registry.acquire_run(
        verifier,
        workspace_path=tmp_path / "ws" / verifier.identifier,
        attempt=None,
        attempt_kind="release-verification",
        agent_kind="codex",
        now=now + timedelta(seconds=1),
    )
    assert bound_run is not None
    assert registry.bind_release_verifier_run(
        gate=pending,
        verifier_run_id=bound_run,
        now=now + timedelta(seconds=1),
    )

    submitted_run = bound_run
    approval_time = now + timedelta(seconds=2)
    if run_case == "fake":
        submitted_run = "missing-run-id"
    elif run_case == "completed":
        assert registry.complete_run(
            issue_id=verifier.id,
            run_id=bound_run,
            status="normal",
            now=approval_time,
        )
    elif run_case == "expired":
        approval_time = now + timedelta(seconds=12)
        assert registry.expire_stale(now=approval_time) == 1
    elif run_case == "reclaiming":
        registry.close()
        registry = RunRegistry(
            path,
            lease_ttl=lease_ttl,
            owner_pid=5252,
            boot_id="reclaimer",
        )
        reclaimed = registry.reclaim_dead_owner_leases(
            now=approval_time,
            pid_alive=lambda _pid: False,
        )
        assert [record.run_id for record in reclaimed] == [bound_run]
        assert registry.get_run(bound_run).status == "reclaiming"
    elif run_case == "foreign":
        assert registry.complete_run(
            issue_id=verifier.id,
            run_id=bound_run,
            status="normal",
            now=approval_time,
        )
        approval_time = now + timedelta(seconds=3)
        foreign_run = registry.acquire_run(
            verifier,
            workspace_path=tmp_path / "ws" / verifier.identifier,
            attempt=1,
            attempt_kind="foreign-verification",
            agent_kind="codex",
            now=approval_time,
        )
        assert foreign_run is not None
        submitted_run = foreign_run

    approved = registry.approve_release_gate(
        finalizer_identifier=pending.finalizer_identifier,
        verifier_issue_id=pending.verifier_issue_id,
        verifier_identifier=pending.verifier_identifier,
        expected_contract_sha256=pending.expected_contract_sha256,
        expected_cycle_fingerprint=pending.cycle_fingerprint,
        expected_generation=pending.generation,
        approved_fingerprint="d" * 64,
        target_branch="main",
        target_sha="a" * 40,
        verifier_run_id=submitted_run,
        now=approval_time,
    )

    assert approved is expected_approval
    persisted = registry.get_release_gate("APP-FINAL")
    assert persisted is not None
    assert persisted.status == ("approved" if expected_approval else "pending")
    if run_case == "expired":
        assert registry.get_run(bound_run).status == "expired"


def test_run_registry_reclaims_dead_owner_lease_before_ttl(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()

    crashed = RunRegistry(
        path, lease_ttl=timedelta(minutes=5), owner_pid=4242, boot_id="crashed-boot"
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
    crashed.close()

    fresh = RunRegistry(path, lease_ttl=timedelta(minutes=5), boot_id="fresh-boot")
    # Owner process still alive -> honor the lease until TTL.
    assert (
        fresh.reclaim_dead_owner_leases(
            now=now + timedelta(seconds=1), pid_alive=lambda _pid: True
        )
        == []
    )
    assert fresh.has_active_lease(issue.id, now=now + timedelta(seconds=1)) is True
    # Owner process dead -> reclaim immediately, well before TTL.
    reclaimed = fresh.reclaim_dead_owner_leases(
        now=now + timedelta(seconds=2), pid_alive=lambda _pid: False
    )
    assert [r.run_id for r in reclaimed] == [run_id]
    assert fresh.has_active_lease(issue.id, now=now + timedelta(seconds=3)) is True
    assert fresh.get_run(run_id).status == "reclaiming"
    assert (
        fresh.acquire_run(
            issue,
            workspace_path=tmp_path / "ws" / issue.identifier,
            attempt=1,
            attempt_kind="retry",
            agent_kind="codex",
            now=now + timedelta(seconds=3),
        )
        is None
    )
    assert fresh.finalize_reclaimed_lease(run_id, now=now + timedelta(seconds=4))
    assert fresh.has_active_lease(issue.id, now=now + timedelta(seconds=5)) is False
    orphaned = fresh.get_run(run_id)
    assert orphaned.status == "orphaned"
    assert orphaned.failure_class == "orphaned"
    assert fresh.run_events(run_id)[-1]["event_type"] == "run_completed"


def test_run_registry_retries_interrupted_reclaim_after_reopen(tmp_path: Path) -> None:
    """AF-10 — a crash between claim and kill keeps the lease fenced."""
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    crashed = RunRegistry(path, owner_pid=4242, boot_id="crashed")
    run_id = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    crashed.close()

    first_recovery = RunRegistry(path, boot_id="first-recovery")
    assert [
        record.run_id
        for record in first_recovery.reclaim_dead_owner_leases(
            now=now + timedelta(seconds=1), pid_alive=lambda _pid: False
        )
    ] == [run_id]
    assert first_recovery.get_run(run_id).status == "reclaiming"
    first_recovery.close()

    retry_recovery = RunRegistry(path, boot_id="retry-recovery")
    assert [
        record.run_id
        for record in retry_recovery.reclaim_dead_owner_leases(
            now=now + timedelta(seconds=2), pid_alive=lambda _pid: True
        )
    ] == [run_id]
    assert retry_recovery.has_active_lease(
        issue.id, now=now + timedelta(seconds=2)
    )
    assert retry_recovery.finalize_reclaimed_lease(
        run_id, now=now + timedelta(seconds=3)
    )
    assert retry_recovery.get_run(run_id).status == "orphaned"


def test_run_registry_reclaim_skips_own_boot(tmp_path: Path) -> None:
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    registry = RunRegistry(
        tmp_path / "state.db",
        lease_ttl=timedelta(minutes=5),
        owner_pid=999_999,
        boot_id="my-boot",
    )
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    assert (
        registry.reclaim_dead_owner_leases(
            now=now + timedelta(seconds=1), pid_alive=lambda _pid: False
        )
        == []
    )
    assert registry.has_active_lease(issue.id, now=now + timedelta(seconds=1)) is True


def test_run_registry_migrates_legacy_schema_and_reclaims_null_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            issue_id TEXT NOT NULL,
            identifier TEXT NOT NULL,
            title TEXT NOT NULL,
            state TEXT NOT NULL,
            attempt INTEGER,
            attempt_kind TEXT NOT NULL,
            agent_kind TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            lease_expires_at TEXT,
            last_progress_at TEXT,
            completed_at TEXT
        )
        """
    )
    future = (now + timedelta(minutes=5)).isoformat()
    conn.execute(
        """
        INSERT INTO runs VALUES (
            'legacy-run', 'id-MT-1', 'MT-1', 'MT-1 title', 'In Progress',
            NULL, 'initial', 'codex', '/tmp/ws', 'active', ?, ?, ?, ?, NULL
        )
        """,
        (now.isoformat(), now.isoformat(), future, now.isoformat()),
    )
    conn.commit()
    conn.close()

    registry = RunRegistry(path, lease_ttl=timedelta(minutes=5), boot_id="fresh")
    reclaimed = registry.reclaim_dead_owner_leases(
        now=now + timedelta(seconds=1), pid_alive=lambda _pid: False
    )
    assert [r.run_id for r in reclaimed] == ["legacy-run"]
    assert registry.finalize_reclaimed_lease(
        "legacy-run", now=now + timedelta(seconds=2)
    )
    assert registry.has_active_lease("id-MT-1", now=now + timedelta(seconds=2)) is False


def test_v4_release_gate_migration_backfills_generation_and_evidence_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    for version, name in (
        (1, "baseline_runs_and_issue_flags"),
        (2, "governed_workflow_ledger"),
        (3, "release_gate_authority"),
        (4, "release_finalizer_run_binding"),
    ):
        conn.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (version, name, "2026-08-09T00:00:00+00:00"),
        )
    conn.execute(
        """
        CREATE TABLE release_gates (
            finalizer_identifier TEXT PRIMARY KEY,
            verifier_issue_id TEXT NOT NULL,
            verifier_identifier TEXT NOT NULL,
            expected_contract_sha256 TEXT NOT NULL,
            cycle_fingerprint TEXT NOT NULL,
            approved_fingerprint TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending', 'approved')),
            target_branch TEXT,
            approved_target_sha TEXT,
            verifier_run_id TEXT,
            finalizer_run_id TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO release_gates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                "PENDING-FINAL",
                "id-PENDING-VERIFY",
                "PENDING-VERIFY",
                "a" * 64,
                "b" * 64,
                None,
                "pending",
                None,
                None,
                "old-pending-run",
                None,
                "2026-08-09T00:00:00+00:00",
            ),
            (
                "APPROVED-FINAL",
                "id-APPROVED-VERIFY",
                "APPROVED-VERIFY",
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "approved",
                "main",
                "f" * 40,
                "old-approved-run",
                "old-finalizer-run",
                "2026-08-09T00:00:00+00:00",
            ),
        ),
    )
    conn.commit()
    conn.close()

    registry = RunRegistry(path)

    assert registry.applied_migrations == (5, 6, 7, 8)
    assert len(list(tmp_path.glob("state.db.backup-*"))) == 1
    for finalizer, verifier in (
        ("PENDING-FINAL", "PENDING-VERIFY"),
        ("APPROVED-FINAL", "APPROVED-VERIFY"),
    ):
        gate = registry.get_release_gate(finalizer)
        assert gate is not None
        assert gate.status == "pending"
        assert gate.generation
        assert gate.approved_fingerprint is None
        assert gate.target_branch is None
        assert gate.approved_target_sha is None
        assert gate.verifier_run_id is None
        assert gate.finalizer_run_id is None
        identity = registry.get_release_evidence_identity(verifier)
        assert identity is not None
        assert identity.finalizer_identifier == finalizer
        assert identity.cycle_generation == gate.generation
        assert identity.retired is False


def test_v5_release_completion_without_ticket_token_is_invalidated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO schema_migrations VALUES (?, ?, ?)",
        (
            (1, "baseline_runs_and_issue_flags", "2026-08-09T00:00:00+00:00"),
            (2, "governed_workflow_ledger", "2026-08-09T00:00:00+00:00"),
            (3, "release_gate_authority", "2026-08-09T00:00:00+00:00"),
            (4, "release_finalizer_run_binding", "2026-08-09T00:00:00+00:00"),
            (5, "release_provenance", "2026-08-09T00:00:00+00:00"),
        ),
    )
    conn.execute(
        """
        CREATE TABLE release_gates (
            finalizer_identifier TEXT PRIMARY KEY,
            verifier_issue_id TEXT NOT NULL,
            verifier_identifier TEXT NOT NULL,
            expected_contract_sha256 TEXT NOT NULL,
            cycle_fingerprint TEXT NOT NULL,
            approved_fingerprint TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending', 'approved')),
            target_branch TEXT,
            approved_target_sha TEXT,
            verifier_run_id TEXT,
            finalizer_run_id TEXT,
            generation TEXT NOT NULL DEFAULT '',
            finalizer_completed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE release_evidence_issues (
            issue_id TEXT PRIMARY KEY,
            identifier TEXT NOT NULL UNIQUE,
            finalizer_identifier TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('verifier', 'finalizer')),
            cycle_generation TEXT NOT NULL,
            retired INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO release_gates VALUES (
            ?, ?, ?, ?, ?, ?, 'approved', 'main', ?, ?, ?, ?, ?, ?
        )
        """,
        (
            "APP-FINAL",
            "id-VERIFY-1",
            "VERIFY-1",
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "d" * 40,
            "verifier-run",
            "finalizer-run",
            "old-generation",
            "2026-08-09T00:01:00+00:00",
            "2026-08-09T00:01:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO release_evidence_issues VALUES (
            ?, ?, ?, 'verifier', ?, 0, ?, ?
        )
        """,
        (
            "id-VERIFY-1",
            "VERIFY-1",
            "APP-FINAL",
            "old-generation",
            "2026-08-09T00:00:00+00:00",
            "2026-08-09T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    registry = RunRegistry(path)
    gate = registry.get_release_gate("APP-FINAL")
    evidence = registry.get_release_evidence_identity("VERIFY-1")

    assert registry.applied_migrations == (6, 7, 8)
    assert gate is not None and evidence is not None
    assert gate.status == "pending"
    assert gate.generation and gate.generation != "old-generation"
    assert gate.approved_fingerprint is None
    assert gate.verifier_run_id is None
    assert gate.finalizer_run_id is None
    assert gate.finalizer_completed_at is None
    assert gate.finalizer_completion_token is None
    assert evidence.cycle_generation == gate.generation
    assert list(tmp_path.glob("state.db.backup-*"))


def test_concurrent_v4_migration_applies_each_pending_version_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO schema_migrations VALUES (?, ?, ?)",
        (
            (1, "baseline_runs_and_issue_flags", "2026-08-09T00:00:00+00:00"),
            (2, "governed_workflow_ledger", "2026-08-09T00:00:00+00:00"),
            (3, "release_gate_authority", "2026-08-09T00:00:00+00:00"),
            (4, "release_finalizer_run_binding", "2026-08-09T00:00:00+00:00"),
        ),
    )
    conn.execute(
        """
        CREATE TABLE release_gates (
            finalizer_identifier TEXT PRIMARY KEY,
            verifier_issue_id TEXT NOT NULL,
            verifier_identifier TEXT NOT NULL,
            expected_contract_sha256 TEXT NOT NULL,
            cycle_fingerprint TEXT NOT NULL,
            approved_fingerprint TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending', 'approved')),
            target_branch TEXT,
            approved_target_sha TEXT,
            verifier_run_id TEXT,
            finalizer_run_id TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO release_gates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "APP-FINAL",
            "id-VERIFY-1",
            "VERIFY-1",
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "approved",
            "main",
            "d" * 40,
            "old-verifier-run",
            "old-finalizer-run",
            "2026-08-09T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    original_current_schema_version = migration_mod.current_schema_version
    optimistic_read_barrier = threading.Barrier(2)
    thread_state = threading.local()

    def synchronized_current_schema_version(connection: sqlite3.Connection) -> int:
        version = original_current_schema_version(connection)
        if version == 4 and not getattr(thread_state, "passed_barrier", False):
            thread_state.passed_barrier = True
            optimistic_read_barrier.wait(timeout=5)
        return version

    monkeypatch.setattr(
        migration_mod,
        "current_schema_version",
        synchronized_current_schema_version,
    )
    results: list[tuple[tuple[int, ...], str]] = []
    errors: list[BaseException] = []

    def start_registry() -> None:
        try:
            registry = RunRegistry(path)
            gate = registry.get_release_gate("APP-FINAL")
            assert gate is not None
            results.append((registry.applied_migrations, gate.generation))
            registry.close()
        except BaseException as exc:  # test thread must report to the parent
            errors.append(exc)

    starters = [
        threading.Thread(target=start_registry, name=f"registry-starter-{index}")
        for index in range(2)
    ]
    for starter in starters:
        starter.start()
    for starter in starters:
        starter.join(timeout=10)

    assert not any(starter.is_alive() for starter in starters)
    assert errors == []
    assert len(results) == 2
    applied_versions = sorted(
        version
        for applied, _generation in results
        for version in applied
    )
    assert applied_versions == [5, 6, 7, 8]
    generations = {generation for _applied, generation in results}
    assert len(generations) == 1
    assert next(iter(generations))

    conn = sqlite3.connect(path)
    migration_rows = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = 5"
    ).fetchone()
    stored_generations = conn.execute(
        "SELECT COUNT(DISTINCT generation) FROM release_gates"
    ).fetchone()
    evidence_rows = conn.execute(
        "SELECT COUNT(*) FROM release_evidence_issues"
    ).fetchone()
    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    conn.close()
    assert migration_rows == (1,)
    assert stored_generations == (1,)
    assert evidence_rows == (1,)
    assert integrity == ("ok",)


def test_run_registry_persists_issue_flags_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    registry = RunRegistry(path)

    registry.set_issue_flags(
        "id-MT-1",
        retry_attempt=3,
        budget_exhausted=True,
        paused=True,
        pause_reason="operator pause",
        now=now,
    )
    registry.close()

    reopened = RunRegistry(path)
    flags = reopened.get_issue_flags("id-MT-1")

    assert flags is not None
    assert flags.issue_id == "id-MT-1"
    assert flags.retry_attempt == 3
    assert flags.budget_exhausted is True
    assert flags.paused is True
    assert flags.pause_reason == "operator pause"
    assert flags.updated_at == now


def test_run_registry_clears_issue_flags_independently(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    registry.set_issue_flags(
        "id-MT-1",
        retry_attempt=2,
        budget_exhausted=True,
        paused=True,
        pause_reason="needs inspection",
    )

    registry.clear_issue_flags("id-MT-1", retry_attempt=True, paused=True)
    flags = registry.get_issue_flags("id-MT-1")

    assert flags is not None
    assert flags.retry_attempt is None
    assert flags.budget_exhausted is True
    assert flags.paused is False
    assert flags.pause_reason is None

    registry.clear_issue_flags("id-MT-1", budget_exhausted=True)
    assert registry.get_issue_flags("id-MT-1") is None


def test_recent_runs_empty(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")

    assert registry.recent_runs() == []


def test_recent_runs_orders_newest_first_and_filters_issue(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    first = _issue("MT-1")
    second = _issue("MT-2")
    now = datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc)

    run_1 = registry.acquire_run(
        first,
        workspace_path=tmp_path / "ws" / first.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_1
    registry.complete_run(
        issue_id=first.id,
        run_id=run_1,
        status="normal",
        now=now + timedelta(seconds=1),
    )
    run_2 = registry.acquire_run(
        second,
        workspace_path=tmp_path / "ws" / second.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="claude",
        now=now + timedelta(seconds=2),
    )
    assert run_2
    registry.complete_run(
        issue_id=second.id,
        run_id=run_2,
        status="force_ejected_zombie",
        now=now + timedelta(seconds=3),
    )
    run_3 = registry.acquire_run(
        first,
        workspace_path=tmp_path / "ws" / first.identifier,
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
        now=now + timedelta(seconds=4),
    )
    assert run_3

    recent = registry.recent_runs()
    assert [r.run_id for r in recent] == [run_3, run_2, run_1]
    assert recent[1].status == "force_ejected_zombie"
    assert recent[1].attempt_kind == "initial"
    assert recent[1].agent_kind == "claude"
    assert recent[1].completed_at == now + timedelta(seconds=3)

    filtered = registry.recent_runs(issue_id=first.id)
    assert [r.run_id for r in filtered] == [run_3, run_1]

    identifier_filtered = registry.recent_runs(issue_id=first.identifier)
    assert [r.run_id for r in identifier_filtered] == [run_3, run_1]


def test_recent_runs_limit_clamped(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    now = datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc)
    for index in range(3):
        issue = _issue(f"MT-{index}")
        run_id = registry.acquire_run(
            issue,
            workspace_path=tmp_path / "ws" / issue.identifier,
            attempt=None,
            attempt_kind="initial",
            agent_kind="codex",
            now=now + timedelta(seconds=index),
        )
        assert run_id
        registry.complete_run(
            issue_id=issue.id,
            run_id=run_id,
            status="normal",
            now=now + timedelta(seconds=index, milliseconds=500),
        )

    assert len(registry.recent_runs(limit=0)) == 1
    assert len(registry.recent_runs(limit=-10)) == 1
    assert len(registry.recent_runs(limit=500)) == 3


def test_run_explorer_v7_summary_events_and_diagnostic_are_bounded(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue("EXP-1")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "EXP-1",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert run_id is not None
    assert registry.schema_version() == 8

    registry.append_attempt_event(
        run_id=run_id,
        event_type="turn_completed",
        payload={
            "turn": 1,
            "message": "assistant output must not persist",
            "input_tokens": 11,
            "cache_input_tokens": 3,
            "output_tokens": 5,
            "total_tokens": 19,
            "ignored_raw_payload": {"tool_transcript": "must-not-survive"},
        },
    )
    registry.append_attempt_event(
        run_id=run_id,
        event_type="workspace_updated",
        payload={"turn": 1, "commit_sha": "a" * 40},
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="turn_error",
        state="Verify",
        input_tokens=11,
        cache_input_tokens=3,
        output_tokens=5,
        total_tokens=19,
        failure_class="TurnFailed",
        failure_message="api_key=top-secret failed",
    )

    detail = registry.run_detail(run_id)
    assert detail["run"]["title"] == issue.title
    assert detail["run"]["state"] == "Verify"
    assert detail["run"]["branch_name"] == f"symphony/{issue.identifier}"
    assert detail["run"]["commit_sha"] == "a" * 40
    assert detail["run"]["tokens"] == {
        "input": 11, "cache": 3, "output": 5, "total": 19
    }
    assert detail["run"]["failure_class"] == "TurnFailed"
    assert "top-secret" not in detail["run"]["failure_message"]
    assert detail["events"][0]["event_type"] == "run_acquired"
    completed = detail["events"][-1]
    encoded = json.dumps(completed["payload"])
    all_events = json.dumps(detail["events"])
    assert "assistant output must not persist" not in all_events
    assert "ignored_raw_payload" not in all_events
    assert len(encoded.encode()) <= 4096

    diagnostic = registry.diagnostic_json(run_id)
    assert diagnostic["schema_version"] == 1
    assert diagnostic["run"] == detail["run"]
    assert diagnostic["events"] == detail["events"]


def test_attempt_events_drop_oldest_deterministically(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue("EXP-2")
    run_id = registry.acquire_run(
        issue, workspace_path=tmp_path, attempt=None,
        attempt_kind="initial", agent_kind="claude"
    )
    assert run_id is not None
    for turn in range(250):
        registry.append_attempt_event(
            run_id=run_id, event_type="turn_started", payload={"turn": turn}
        )
    events = registry.run_events(run_id)
    assert len(events) == 200
    # run_acquired and the earliest turn events were evicted first.
    assert events[0]["payload"]["turn"] == 50
    assert events[-1]["payload"]["turn"] == 249


def test_recent_runs_supports_compatible_explorer_filters(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    first = _issue("SEARCH-1")
    second = _issue("SEARCH-2")
    first_run = registry.acquire_run(
        first, workspace_path=tmp_path, attempt=None,
        attempt_kind="initial", agent_kind="codex"
    )
    assert first_run
    registry.complete_run(issue_id=first.id, run_id=first_run, status="normal")
    second_run = registry.acquire_run(
        second, workspace_path=tmp_path, attempt=None,
        attempt_kind="initial", agent_kind="claude"
    )
    assert second_run
    registry.complete_run(issue_id=second.id, run_id=second_run, status="turn_error")

    assert [r.run_id for r in registry.recent_runs(query="search-1")] == [first_run]
    assert [r.run_id for r in registry.recent_runs(query="codex")] == [first_run]
    assert [r.run_id for r in registry.recent_runs(query="turn_error")] == [second_run]
    assert [r.run_id for r in registry.recent_runs(status="turn_error")] == [second_run]
    assert [r.run_id for r in registry.recent_runs(agent="codex")] == [first_run]


def test_attempt_events_reject_unknown_event_types(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue("EXP-3")
    run_id = registry.acquire_run(
        issue, workspace_path=tmp_path, attempt=None,
        attempt_kind="initial", agent_kind="codex"
    )
    assert run_id
    with pytest.raises(ValueError):
        registry.append_attempt_event(
            run_id=run_id, event_type="raw_tool_transcript", payload={"raw": "secret"}
        )

def test_run_completion_survives_diagnostic_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue("EXP-4")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id

    def fail_diagnostic_write(**_kwargs: object) -> None:
        raise sqlite3.OperationalError("diagnostic table unavailable")

    monkeypatch.setattr(registry, "_append_attempt_event_locked", fail_diagnostic_write)

    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="normal",
        state="Done",
    )
    completed = registry.get_run(run_id)
    assert completed.status == "normal"
    assert completed.completed_at is not None
    assert not registry.has_active_lease(issue.id)

def test_run_acquisition_survives_diagnostic_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = RunRegistry(tmp_path / "state.db")

    def fail_diagnostic_write(**_kwargs: object) -> None:
        raise sqlite3.OperationalError("diagnostic table unavailable")

    monkeypatch.setattr(registry, "_append_attempt_event_locked", fail_diagnostic_write)
    issue = _issue("EXP-5")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )

    assert run_id
    assert registry.has_active_lease(issue.id)

def test_run_completion_clamps_diagnostic_counters_to_sqlite_range(
    tmp_path: Path,
) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue("EXP-6")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id

    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="normal",
        total_tokens=10**100,
    )
    assert registry.get_run(run_id).total_tokens == (2**63) - 1

def test_diagnostic_append_drops_immediately_on_writer_contention(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    registry = RunRegistry(db_path)
    issue = _issue("EXP-7")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            registry.append_attempt_event(
                run_id=run_id,
                event_type="turn_started",
                payload={"turn": 1},
            )
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert time.monotonic() - started < 1.0

def test_terminal_diagnostic_retention_prunes_old_events_and_excerpt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_registry_mod, "MAX_RUNS_WITH_DIAGNOSTIC_EVENTS", 2)
    registry = RunRegistry(tmp_path / "state.db")
    run_ids: list[str] = []
    for index in range(3):
        issue = _issue(f"RET-{index}")
        run_id = registry.acquire_run(
            issue,
            workspace_path=tmp_path / issue.identifier,
            attempt=None,
            attempt_kind="initial",
            agent_kind="codex",
        )
        assert run_id
        assert registry.complete_run(
            issue_id=issue.id,
            run_id=run_id,
            status="worker_exit",
            failure_class="worker_exit",
            failure_message=f"bounded excerpt {index}",
            now=datetime(2026, 8, 1, 0, index, tzinfo=timezone.utc),
        )
        run_ids.append(run_id)

    assert registry.run_events(run_ids[0]) == []
    assert registry.get_run(run_ids[0]).failure_message is None
    assert registry.run_events(run_ids[1])
    assert registry.run_events(run_ids[2])

def test_deleting_run_cleans_attempt_events(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue("EXP-8")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    assert registry.run_events(run_id)

    registry._connect().execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    remaining = registry._connect().execute(
        "SELECT COUNT(*) FROM attempt_events WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert remaining is not None
    assert remaining[0] == 0

def test_terminal_run_rejects_late_event_and_summary_mutation(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue("EXP-9")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="normal",
        total_tokens=1,
    )
    event_count = len(registry.run_events(run_id))

    assert not registry.append_attempt_event(
        run_id=run_id,
        event_type="turn_completed",
        payload={"turn": 99, "total_tokens": 999},
    )
    assert registry.get_run(run_id).total_tokens == 1
    assert len(registry.run_events(run_id)) == event_count

def test_v7_migration_preserves_unknown_tokens_for_historical_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path, isolation_level=None)
    original_migrations = migration_mod.MIGRATIONS
    monkeypatch.setattr(migration_mod, "MIGRATIONS", original_migrations[:6])
    migration_mod.apply_migrations(conn, path)
    conn.execute(
        """
        INSERT INTO runs (
            run_id, issue_id, identifier, title, state, attempt, attempt_kind,
            agent_kind, workspace_path, status, started_at, updated_at,
            lease_expires_at, last_progress_at, completed_at, owner_pid,
            owner_boot_id, backend_agent_pid
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL)
        """,
        (
            "f" * 32,
            "id-HIST-1",
            "HIST-1",
            "historical run",
            "Done",
            "initial",
            "codex",
            str(tmp_path),
            "normal",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:01:00+00:00",
            "2026-01-01T00:01:00+00:00",
            "2026-01-01T00:01:00+00:00",
            123,
            "old-boot",
        ),
    )
    conn.close()
    monkeypatch.setattr(migration_mod, "MIGRATIONS", original_migrations)

    migrated = RunRegistry(path).get_run("f" * 32)
    assert migrated.input_tokens is None
    assert migrated.cache_input_tokens is None
    assert migrated.output_tokens is None
    assert migrated.total_tokens is None


# Feature 2: durable crash continuation.

def test_checkpoint_completed_turn_is_owner_fenced_bounded_and_private(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    issue = _issue("CONT-1")
    owner = RunRegistry(path, owner_pid=4242, boot_id="owner")
    run_id = owner.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id is not None

    foreign = RunRegistry(path, owner_pid=4343, boot_id="foreign")
    assert not foreign.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=run_id,
        resume_session_id="private-session",
        state=issue.state,
        turn=1,
        now=now + timedelta(seconds=1),
    )
    assert owner.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=run_id,
        resume_session_id="private-session",
        state=issue.state,
        turn=1,
        now=now + timedelta(seconds=2),
    )
    # Older and duplicate turns cannot replace the latest completed boundary.
    assert not owner.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=run_id,
        resume_session_id="replacement-session",
        state=issue.state,
        turn=1,
        now=now + timedelta(seconds=3),
    )
    for invalid_session_id in ("x" * 513, "session\nforged"):
        with pytest.raises(ValueError):
            owner.checkpoint_completed_turn(
                issue_id=issue.id,
                run_id=run_id,
                resume_session_id=invalid_session_id,
                state=issue.state,
                turn=2,
            )

    record = owner.get_run(run_id)
    assert record.checkpoint_state == issue.state
    assert record.checkpoint_turn == 1
    assert record.checkpointed_at == now + timedelta(seconds=2)
    encoded_detail = json.dumps(owner.run_detail(run_id))
    encoded_diagnostic = json.dumps(owner.diagnostic_json(run_id))
    assert "private-session" not in encoded_detail
    assert "private-session" not in encoded_diagnostic
    assert "resume_session_id" not in encoded_detail
    assert owner.run_detail(run_id)["run"]["checkpoint"] == {
        "state": issue.state,
        "turn": 1,
        "checkpointed_at": (now + timedelta(seconds=2)).isoformat(),
    }


def test_rejected_cli_recovery_consumes_source_then_allows_fresh_run(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    issue = _issue("CONT-REJECTED")
    registry = RunRegistry(path)
    predecessor = registry.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="claude",
        now=now,
    )
    assert predecessor is not None
    assert registry.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=predecessor,
        resume_session_id="private-session",
        state=issue.state,
        turn=1,
        now=now + timedelta(seconds=1),
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=predecessor,
        status="shutdown_interrupted",
        now=now + timedelta(seconds=2),
    )
    recovered = registry.acquire_continuation_run(
        issue,
        continued_from_run_id=predecessor,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="recovery",
        agent_kind="claude",
        now=now + timedelta(seconds=3),
    )
    assert recovered is not None
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=recovered.run_id,
        status="turn_error",
        now=now + timedelta(seconds=4),
    )
    assert registry.latest_continuation_source(
        issue_id=issue.id, agent_kind="claude", state=issue.state
    ) is None

    fresh = registry.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=1,
        attempt_kind="retry",
        agent_kind="claude",
        now=now + timedelta(seconds=5),
    )
    assert fresh is not None
    assert registry.get_run(fresh).continued_from_run_id is None


def test_ticket_edit_after_checkpoint_forces_fresh_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    issue = _issue("CONT-EDIT")
    registry = RunRegistry(path)
    predecessor = registry.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert predecessor is not None
    assert registry.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=predecessor,
        resume_session_id="private-session",
        state=issue.state,
        turn=1,
        now=now + timedelta(seconds=1),
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=predecessor,
        status="shutdown_interrupted",
        now=now + timedelta(seconds=2),
    )
    edited = replace(issue, updated_at=now + timedelta(seconds=3))

    assert registry.latest_continuation_source(
        issue_id=edited.id,
        agent_kind="codex",
        state=edited.state,
        issue_updated_at=edited.updated_at,
    ) is None
    assert registry.acquire_continuation_run(
        edited,
        continued_from_run_id=predecessor,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="recovery",
        agent_kind="codex",
        now=now + timedelta(seconds=4),
    ) is None


def test_newer_fresh_attempt_invalidates_an_unconsumed_checkpoint(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    issue = _issue("CONT-OPT-OUT")
    registry = RunRegistry(path)
    predecessor = registry.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert predecessor is not None
    assert registry.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=predecessor,
        resume_session_id="old-private-session",
        state=issue.state,
        turn=1,
        now=now + timedelta(seconds=1),
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=predecessor,
        status="shutdown_interrupted",
        now=now + timedelta(seconds=2),
    )
    assert registry.latest_continuation_source(
        issue_id=issue.id, agent_kind="codex", state=issue.state
    ) == predecessor

    # This models agent.crash_continuation=false: the next dispatch starts a
    # normal run rather than consuming the private predecessor checkpoint.
    fresh = registry.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now + timedelta(seconds=3),
    )
    assert fresh is not None
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=fresh,
        status="normal",
        now=now + timedelta(seconds=4),
    )

    assert registry.latest_continuation_source(
        issue_id=issue.id, agent_kind="codex", state=issue.state
    ) is None
    assert registry.acquire_continuation_run(
        issue,
        continued_from_run_id=predecessor,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="recovery",
        agent_kind="codex",
        now=now + timedelta(seconds=5),
    ) is None


def test_acquire_continuation_run_is_atomic_fail_closed_and_inherits_checkpoint(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    issue = _issue("CONT-2")
    crashed = RunRegistry(path, owner_pid=4242, boot_id="crashed")
    predecessor = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert predecessor is not None
    assert crashed.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=predecessor,
        resume_session_id="resume-me",
        state=issue.state,
        turn=3,
        now=now + timedelta(seconds=1),
    )
    assert crashed.heartbeat(
        issue_id=issue.id,
        run_id=predecessor,
        backend_agent_pid=7777,
        now=now + timedelta(seconds=2),
    )
    crashed.close()

    recovery = RunRegistry(path, owner_pid=4343, boot_id="recovery")
    reclaimed = recovery.reclaim_dead_owner_leases(
        now=now + timedelta(minutes=10), pid_alive=lambda _pid: False
    )
    assert [record.run_id for record in reclaimed] == [predecessor]
    assert reclaimed[0].backend_agent_pid == 7777
    assert recovery.finalize_reclaimed_lease(
        predecessor, now=now + timedelta(minutes=10, seconds=1)
    )
    assert recovery.get_run(predecessor).backend_agent_pid is None
    assert recovery.latest_continuation_source(
        issue_id=issue.id, agent_kind="codex", state=issue.state
    ) == predecessor
    assert recovery.latest_continuation_source(
        issue_id=issue.id, agent_kind="claude", state=issue.state
    ) is None

    wrong_state = Issue(**{**issue.__dict__, "state": "Verify"})
    for candidate_issue, candidate_agent in (
        (_issue("OTHER"), "codex"),
        (wrong_state, "codex"),
        (issue, "claude"),
    ):
        assert recovery.acquire_continuation_run(
            candidate_issue,
            continued_from_run_id=predecessor,
            workspace_path=tmp_path / candidate_issue.identifier,
            attempt=1,
            attempt_kind="retry",
            agent_kind=candidate_agent,
            now=now + timedelta(minutes=10, seconds=2),
        ) is None

    acquired = recovery.acquire_continuation_run(
        issue,
        continued_from_run_id=predecessor,
        workspace_path=tmp_path / issue.identifier,
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
        now=now + timedelta(minutes=10, seconds=3),
    )
    assert acquired is not None
    assert acquired.continued_from_run_id == predecessor
    assert acquired.checkpoint.resume_session_id == "resume-me"
    assert acquired.checkpoint.state == issue.state
    assert acquired.checkpoint.turn == 3
    assert acquired.run_id != predecessor

    successor = recovery.get_run(acquired.run_id)
    assert successor.status == "active"
    assert successor.continued_from_run_id == predecessor
    assert successor.checkpoint_state == issue.state
    assert successor.checkpoint_turn == 3
    assert recovery.run_detail(acquired.run_id)["run"]["continued_from_run_id"] == predecessor
    # The partial unique link and active-issue fence make the checkpoint one-shot.
    assert recovery.acquire_continuation_run(
        issue,
        continued_from_run_id=predecessor,
        workspace_path=tmp_path / issue.identifier,
        attempt=2,
        attempt_kind="retry",
        agent_kind="codex",
        now=now + timedelta(minutes=10, seconds=4),
    ) is None
    assert recovery.latest_continuation_source(
        issue_id=issue.id, agent_kind="codex", state=issue.state
    ) is None


def test_graceful_shutdown_checkpoint_requires_cleared_backend_pid(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    issue = _issue("CONT-3")
    registry = RunRegistry(tmp_path / "state.db", owner_pid=4242, boot_id="owner")
    predecessor = registry.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert predecessor is not None
    assert registry.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=predecessor,
        resume_session_id="resume-graceful",
        state=issue.state,
        turn=2,
        now=now + timedelta(seconds=1),
    )
    assert registry.heartbeat(
        issue_id=issue.id,
        run_id=predecessor,
        backend_agent_pid=9999,
        now=now + timedelta(seconds=2),
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=predecessor,
        status="shutdown_interrupted",
        now=now + timedelta(seconds=3),
    )
    assert registry.acquire_continuation_run(
        issue,
        continued_from_run_id=predecessor,
        workspace_path=tmp_path / issue.identifier,
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
        now=now + timedelta(seconds=4),
    ) is None

    with sqlite3.connect(registry.path) as conn:
        conn.execute(
            "UPDATE runs SET backend_agent_pid = NULL WHERE run_id = ?",
            (predecessor,),
        )
    acquired = registry.acquire_continuation_run(
        issue,
        continued_from_run_id=predecessor,
        workspace_path=tmp_path / issue.identifier,
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
        now=now + timedelta(seconds=5),
    )
    assert acquired is not None


def test_continuation_acquisition_race_has_exactly_one_successor(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    issue = _issue("CONT-RACE")
    crashed = RunRegistry(path, owner_pid=4242, boot_id="crashed")
    predecessor = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert predecessor is not None
    assert crashed.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=predecessor,
        resume_session_id="race-session",
        state=issue.state,
        turn=1,
        now=now + timedelta(seconds=1),
    )
    crashed.close()
    recovery = RunRegistry(path, boot_id="recovery")
    assert recovery.reclaim_dead_owner_leases(
        now=now + timedelta(seconds=2), pid_alive=lambda _pid: False
    )
    assert recovery.finalize_reclaimed_lease(predecessor, now=now + timedelta(seconds=3))
    recovery.close()

    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def acquire(index: int) -> None:
        try:
            contender = RunRegistry(path, boot_id=f"contender-{index}")
            barrier.wait()
            results.append(
                contender.acquire_continuation_run(
                    issue,
                    continued_from_run_id=predecessor,
                    workspace_path=tmp_path / issue.identifier,
                    attempt=1,
                    attempt_kind="retry",
                    agent_kind="codex",
                    now=now + timedelta(seconds=4),
                )
            )
            contender.close()
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=acquire, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sum(result is not None for result in results) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runs WHERE continued_from_run_id = ?",
            (predecessor,),
        ).fetchone() == (1,)


# ---------------------------------------------------------------------------
# _pid_alive: POSIX mapping + real win32 probe (ITEM 3)
# ---------------------------------------------------------------------------


def test_pid_alive_win32_defaults_alive_without_win32_ctypes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable Win32 API must never authorize duplicate dispatch."""
    import ctypes

    monkeypatch.delattr(ctypes, "WinDLL", raising=False)
    monkeypatch.delattr(ctypes, "get_last_error", raising=False)

    assert run_registry_mod._pid_alive_win32(1234) is True


def test_pid_alive_posix_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The POSIX branch maps os.kill outcomes: ProcessLookupError -> dead,
    other OSError -> conservatively alive, clean -> alive."""
    import sys

    monkeypatch.setattr(sys, "platform", "linux")

    def _with_kill(fake) -> bool:
        monkeypatch.setattr(run_registry_mod.os, "kill", fake)
        return run_registry_mod._pid_alive(1234)

    def _raise(exc: BaseException):
        def _inner(_pid, _sig):
            raise exc

        return _inner

    def _ok(_pid, _sig):
        return None

    assert _with_kill(_raise(ProcessLookupError())) is False
    assert _with_kill(_raise(PermissionError())) is True
    assert _with_kill(_ok) is True


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="win32 liveness probe exercises the real Win32 API surface",
)
def test_pid_alive_win32_probe_on_real_processes() -> None:
    """OpenProcess/GetExitCodeProcess probe against real child processes:
    a live child reads alive; an exited-but-handle-held child and a
    terminated+reaped child read dead; an unknown pid reads dead."""
    import subprocess

    live = subprocess.Popen(
        ["ping", "-n", "30", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert run_registry_mod._pid_alive(live.pid) is True
    finally:
        live.kill()
        live.wait()

    exited = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(0.2)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    time.sleep(0.8)  # exited, but the Popen handle keeps the pid resolvable
    try:
        assert run_registry_mod._pid_alive(exited.pid) is False
    finally:
        exited.wait()

    assert run_registry_mod._pid_alive(live.pid) is False, (
        "reaped child pid must read dead"
    )
    assert run_registry_mod._pid_alive(4_000_000) is False, (
        "never-existing pid must read dead"
    )
