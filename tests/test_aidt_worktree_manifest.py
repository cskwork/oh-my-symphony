"""Durable wire, registry, lock, and attempt-clock contracts."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from symphony.aidt_worktree.contract import (
    AIDT_WORKTREE_ACTIVATION_SCHEMA,
    AIDT_WORKTREE_ATTEMPT_SCHEMA,
    AIDT_WORKTREE_BASE_REF,
    AIDT_WORKTREE_OWNERSHIP_SCHEMA,
    AIDT_WORKTREE_SCHEMA,
    MAX_DURABLE_FILE_BYTES,
    MAX_REGISTRY_ENTRIES,
    AidtWorktreeFailure,
    common_git_lock_path,
    stable_worktree_paths,
)
import symphony.aidt_worktree.manifest as manifest_module
from symphony.aidt_worktree.manifest import (
    AidtWorktreeManifest,
    AttemptRecord,
    OwnershipRecord,
    PostProof,
    PreProof,
    RemovalProof,
    RepositorySnapshot,
    RouteScope,
    activate_registry,
    advance_attempt_phase,
    admit_attempt,
    advisory_lock,
    canonical_json_bytes,
    decode_canonical_json,
    discover_registry,
    evaluate_attempt_admission,
    initial_attempt_record,
    manifest_from_dict,
    next_failure_record,
    persist_attempt,
    persist_manifest,
    persist_ownership,
    read_attempt,
    read_manifest,
    read_optional_attempt,
    read_optional_manifest,
    read_optional_ownership,
    read_ownership,
    ready_attempt_record,
    registry_recognizes_identifier,
    registry_recognizes_path,
)


SHA1 = "a" * 40
DIGEST = "b" * 64
NOW = "2026-07-21T01:02:03Z"
ACTIVE_BACKOFF_CATEGORIES = (
    "attempt_backoff",
    "scope_changed",
    "lock_timeout",
    "fetch_timeout",
    "fetch_command_failed",
)
MANUAL_ONLY_CATEGORIES = (
    "attempt_exhausted",
    "attempt_manual",
    "authorization_invalid",
    "base_invalid",
    "binding_invalid",
    "branch_invalid",
    "cap_exceeded",
    "capability_unsupported",
    "card_invalid",
    "cas_mismatch",
    "catalog_invalid",
    "change_kind_invalid",
    "clock_invalid",
    "collision",
    "content_invalid",
    "durability_failed",
    "identifier_invalid",
    "identity_invalid",
    "internal_error",
    "manifest_collision",
    "manifest_invalid",
    "manifest_too_large",
    "path_invalid",
    "persistence_failed",
    "profile_invalid",
    "protocol_invalid",
    "registry_collision",
    "registry_invalid",
)


def _scope() -> RouteScope:
    return RouteScope(
        identifier="A20-1188--viewer-api",
        coordinator="A20-1188",
        service="viewer-api",
        kind="backend",
        issue_type="bug",
        change_kind="fix",
        route_pair_digest="1" * 64,
        route_fingerprint="2" * 64,
        coordinator_fingerprint="3" * 64,
        source_revision="4" * 64,
        catalog_revision="5" * 64,
        checkout_revision=SHA1,
        repository_binding_digest="6" * 64,
    )


def _snapshot(phase: str, *, target: bool = False) -> RepositorySnapshot:
    retained_after_remove = phase == "cleanup_post"
    return RepositorySnapshot(
        phase=phase,
        observed_at=NOW,
        repository_binding_digest="6" * 64,
        root_head=SHA1,
        root_symbolic_digest=DIGEST,
        root_status_digest=DIGEST,
        root_content_digest=DIGEST,
        root_content_count=2,
        root_content_bytes=12,
        registry_digest=DIGEST,
        registry_count=1,
        protected_digest=DIGEST,
        protected_count=0,
        refs_digest=DIGEST,
        refs_count=4,
        base_ref_sha=SHA1,
        target_ref_sha=SHA1 if target or retained_after_remove else None,
        target_registration_digest=DIGEST if target else None,
    )


def _pre() -> PreProof:
    return PreProof(_snapshot("s0"), _snapshot("s1"), DIGEST)


def _post() -> PostProof:
    return PostProof(_snapshot("s2", target=True), DIGEST, SHA1, DIGEST, True, True)


def _manifest(tmp_path: Path, state: str = "prepared") -> AidtWorktreeManifest:
    post = None if state == "prepared" else _post()
    removal = None
    if state in {"removing", "removed"}:
        removal = RemovalProof(
            authority_digest="7" * 64,
            pre_snapshot=_snapshot("cleanup_pre", target=True),
            post_snapshot=_snapshot("cleanup_post") if state == "removed" else None,
            remove_delta_digest="8" * 64 if state == "removed" else None,
            retained_branch_sha=SHA1,
        )
    revisions = {"prepared": 1, "ready": 2, "removing": 3, "removed": 4}
    scope = _scope()
    return AidtWorktreeManifest(
        schema=AIDT_WORKTREE_SCHEMA,
        manifest_revision=revisions[state],
        state=state,
        identifier=scope.identifier,
        coordinator=scope.coordinator,
        service=scope.service,
        kind=scope.kind,
        workflow_identity="9" * 64,
        board_identity="a" * 64,
        workspace_root=str((tmp_path / "workspaces").resolve()),
        workspace_path=str((tmp_path / "workspaces" / scope.identifier).resolve()),
        catalog_checkout="viewer-api",
        canonical_service_root=str((tmp_path / "viewer-api").resolve()),
        common_git_identity="b" * 64,
        object_format="sha1",
        route_pair_digest=scope.route_pair_digest,
        repository_binding_digest=scope.repository_binding_digest,
        route_fingerprint=scope.route_fingerprint,
        coordinator_fingerprint=scope.coordinator_fingerprint,
        source_revision=scope.source_revision,
        catalog_revision=scope.catalog_revision,
        branch="fix/A20-1188",
        base_ref=AIDT_WORKTREE_BASE_REF,
        base_sha=SHA1,
        route_scope=scope,
        pre_proof=_pre(),
        post_proof=post,
        removal_proof=removal,
        created_at=NOW,
        updated_at=NOW,
    )


def _ownership(tmp_path: Path, revision: int = 1) -> OwnershipRecord:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    return OwnershipRecord(
        schema=AIDT_WORKTREE_OWNERSHIP_SCHEMA,
        record_revision=revision,
        identifier=_scope().identifier,
        service="viewer-api",
        workspace_root=str((tmp_path / "workspaces").resolve()),
        workspace_path=str((tmp_path / "workspaces" / _scope().identifier).resolve()),
        manifest_path=str(paths.manifest),
        route_pair_digest="1" * 64,
        manifest_revision=revision,
        tombstone=False,
        created_at=NOW,
        updated_at=NOW,
    )


def _attempt(
    disposition: str = "backoff", *, retry_at: str | None = NOW, attempt: int = 1
) -> AttemptRecord:
    shapes = {
        "backoff": ("fetch_timeout", "none", None),
        "manual": ("collision", "none", None),
        "ready": ("ready", "added", 2),
    }
    category, mutation_phase, manifest_revision = shapes[disposition]
    return AttemptRecord(
        schema=AIDT_WORKTREE_ATTEMPT_SCHEMA,
        record_revision=1,
        identifier=_scope().identifier,
        route_pair_digest="1" * 64,
        workflow_generation="2" * 64,
        category=category,
        disposition=disposition,
        attempt=attempt,
        retry_at=retry_at,
        mutation_phase=mutation_phase,
        manifest_revision=manifest_revision,
        created_at=NOW,
        updated_at=NOW,
    )


def test_manifest_schema_round_trips_as_one_canonical_byte_string(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    encoded = canonical_json_bytes(manifest)

    assert encoded.endswith(b"\n")
    assert encoded == canonical_json_bytes(manifest_from_dict(asdict(manifest)))
    assert decode_canonical_json(encoded) == asdict(manifest)
    assert manifest_from_dict(json.loads(encoded)) == manifest


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: {**raw, "unknown": 1},
        lambda raw: {key: value for key, value in raw.items() if key != "state"},
        lambda raw: {**raw, "manifest_revision": True},
        lambda raw: {**raw, "state": "READY"},
        lambda raw: {**raw, "post_proof": asdict(_post())},
        lambda raw: {**raw, "base_sha": "A" * 40},
    ],
)
def test_manifest_rejects_unknown_missing_nonexact_and_wrong_state_shape(
    tmp_path: Path, mutation
) -> None:
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        manifest_from_dict(mutation(asdict(_manifest(tmp_path))))


def test_manifest_dtos_totalize_wrong_scalar_and_noncanonical_path_types(
    tmp_path: Path,
) -> None:
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        replace(_scope(), issue_type=[])
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        replace(_manifest(tmp_path), workspace_root=str(tmp_path / "x" / ".." / "workspaces"))
    with pytest.raises(AidtWorktreeFailure, match="registry_invalid"):
        replace(_attempt(), retry_at=1)


def test_canonical_decoder_rejects_duplicate_noncanonical_and_oversized_json() -> None:
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        decode_canonical_json(b'{"a":1,"a":2}\n')
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        decode_canonical_json(b'{"b":2, "a":1}\n')
    with pytest.raises(AidtWorktreeFailure, match="manifest_too_large"):
        decode_canonical_json(b" " * (MAX_DURABLE_FILE_BYTES + 1))


def test_manifest_cas_is_0600_atomic_and_transition_closed(tmp_path: Path) -> None:
    path = tmp_path / "manifests" / f"{_scope().identifier}.json"
    path.parent.mkdir()
    prepared = _manifest(tmp_path)

    persist_manifest(path, prepared, expected_revision=None)
    before = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_manifest(path) == prepared

    with pytest.raises(AidtWorktreeFailure, match="cas_mismatch"):
        persist_manifest(path, _manifest(tmp_path, "ready"), expected_revision=8)
    assert path.read_bytes() == before

    persist_manifest(path, _manifest(tmp_path, "ready"), expected_revision=1)
    assert read_manifest(path).state == "ready"
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        persist_manifest(path, _manifest(tmp_path, "removed"), expected_revision=2)


def test_all_four_manifest_state_shapes_have_exact_monotonic_revisions(tmp_path: Path) -> None:
    states = [_manifest(tmp_path, state) for state in ("prepared", "ready", "removing", "removed")]

    assert [(item.state, item.manifest_revision) for item in states] == [
        ("prepared", 1),
        ("ready", 2),
        ("removing", 3),
        ("removed", 4),
    ]
    assert states[2].removal_proof is not None
    assert states[2].removal_proof.post_snapshot is None
    assert states[3].removal_proof is not None
    assert states[3].removal_proof.post_snapshot is not None
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        replace(states[0], manifest_revision=2)


def test_snapshot_caps_and_exact_bool_types_are_binding(tmp_path: Path) -> None:
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        replace(_snapshot("s0"), root_content_count=10_001)
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        replace(_snapshot("s0"), root_content_bytes=536_870_913)
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        replace(_post(), clean_at_create=1)


def test_manifest_reader_rejects_symlink_and_permissive_mode(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(canonical_json_bytes(_manifest(tmp_path)))
    target.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        read_manifest(link)
    target.chmod(0o644)
    with pytest.raises(AidtWorktreeFailure, match="manifest_invalid"):
        read_manifest(target)


def test_activation_and_discovery_guard_all_durable_child_names(tmp_path: Path) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activation = activate_registry(paths, "9" * 64, NOW)
    persist_manifest(paths.manifest, _manifest(tmp_path), expected_revision=None)
    persist_ownership(paths.ownership, _ownership(tmp_path), expected_revision=None)
    persist_attempt(paths.attempt, _attempt(), expected_revision=None)

    discovered = discover_registry(paths.root)
    assert activation.schema == AIDT_WORKTREE_ACTIVATION_SCHEMA
    assert discovered.identifiers == frozenset({_scope().identifier})
    assert read_ownership(paths.ownership).identifier == _scope().identifier
    assert read_attempt(paths.attempt).record_revision == 1

    alias = paths.ownership_records / "a20-1188--viewer-api.json"
    alias.write_bytes(canonical_json_bytes(_ownership(tmp_path)))
    alias.chmod(0o600)
    names = [entry.name for entry in os.scandir(paths.ownership_records)]
    if len(names) == 1:  # The host filesystem itself folded the alias.
        assert discover_registry(paths.root).identifiers == frozenset({_scope().identifier})
    else:
        with pytest.raises(AidtWorktreeFailure, match="registry_collision"):
            discover_registry(paths.root)


def test_registry_guard_recognizes_metadata_workspace_and_removed_tombstone(
    tmp_path: Path,
) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    assert not registry_recognizes_identifier(paths.root, _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    persist_manifest(paths.manifest, _manifest(tmp_path), expected_revision=None)
    persist_manifest(paths.manifest, _manifest(tmp_path, "ready"), expected_revision=1)
    persist_manifest(paths.manifest, _manifest(tmp_path, "removing"), expected_revision=2)
    persist_manifest(paths.manifest, _manifest(tmp_path, "removed"), expected_revision=3)
    tombstone = replace(_ownership(tmp_path), tombstone=True, manifest_revision=4)
    persist_ownership(paths.ownership, tombstone, expected_revision=None)

    assert registry_recognizes_identifier(paths.root, _scope().identifier)
    assert registry_recognizes_path(paths.root, paths.manifest)
    assert registry_recognizes_path(paths.root, Path(tombstone.workspace_path))
    assert not registry_recognizes_path(paths.root, (tmp_path / "generic").resolve())


def test_registry_discovery_rejects_symlink_unknown_and_missing_manifest(tmp_path: Path) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    persist_ownership(paths.ownership, _ownership(tmp_path), expected_revision=None)

    with pytest.raises(AidtWorktreeFailure, match="registry_invalid"):
        discover_registry(paths.root)
    paths.ownership.unlink()
    (paths.manifests / "unexpected.tmp").write_text("x", encoding="utf-8")
    with pytest.raises(AidtWorktreeFailure, match="registry_invalid"):
        discover_registry(paths.root)


def test_advisory_lock_times_out_without_stealing_or_deleting(tmp_path: Path) -> None:
    lock_path = tmp_path / "locks" / "manifest.lock"
    lock_path.parent.mkdir()

    with advisory_lock(lock_path, timeout_seconds=0.1):
        with pytest.raises(AidtWorktreeFailure, match="lock_timeout"):
            with advisory_lock(lock_path, timeout_seconds=0.01):
                pass
    assert lock_path.exists()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    with pytest.raises(AidtWorktreeFailure, match="lock_timeout"):
        with advisory_lock(lock_path, timeout_seconds=float("nan")):
            pass


def test_lock_identities_are_nonreversible_and_common_git_precedes_manifest(
    tmp_path: Path,
) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    common = common_git_lock_path(paths, "e" * 64)

    assert common.parent == paths.locks == paths.manifest_lock.parent
    assert common.name.startswith("common-git-")
    assert _scope().identifier not in paths.manifest_lock.name
    assert "e" * 64 not in common.name


def test_attempt_clock_distinguishes_manual_backoff_due_and_ready() -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    future = replace(_attempt(), retry_at="2026-07-21T01:02:33Z")
    waiting = evaluate_attempt_admission(future, 1, "1" * 64, "2" * 64, now)
    assert waiting.admitted is False and waiting.action == "backoff"

    due = evaluate_attempt_admission(_attempt(), 1, "1" * 64, "2" * 64, now)
    assert due.admitted is True and due.action == "provision"
    assert due.record.record_revision == 2 and due.record.attempt == 2

    manual = replace(_attempt("manual", retry_at=None), category="collision")
    assert not evaluate_attempt_admission(manual, 1, "1" * 64, "2" * 64, now).admitted
    ready = _attempt("ready", retry_at=None, attempt=1)
    admitted = evaluate_attempt_admission(ready, 1, "1" * 64, "2" * 64, now)
    assert admitted.admitted is True and admitted.action == "resume"
    assert admitted.record == ready


def test_attempt_admission_rejects_bad_revision_clock_and_unattested_scope() -> None:
    record = _attempt()
    now = datetime(2026, 7, 21, 1, 2, 3)
    with pytest.raises(AidtWorktreeFailure, match="clock_invalid"):
        evaluate_attempt_admission(record, 1, "1" * 64, "2" * 64, now)
    with pytest.raises(AidtWorktreeFailure, match="cas_mismatch"):
        evaluate_attempt_admission(
            record, 2, "1" * 64, "2" * 64, now.replace(tzinfo=timezone.utc)
        )
    with pytest.raises(AidtWorktreeFailure, match="identity_invalid"):
        evaluate_attempt_admission(
            record, 1, "3" * 64, "2" * 64, now.replace(tzinfo=timezone.utc)
        )


def test_retry_delays_are_bounded_and_post_intent_failure_is_manual() -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    first = replace(_attempt(), attempt=1)
    retry = next_failure_record(first, "fetch_timeout", "none", None, now)
    assert retry.disposition == "backoff"
    retry_time = datetime.strptime(retry.retry_at or "", "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert timedelta(0) < retry_time - now <= timedelta(seconds=600)

    prepared = next_failure_record(first, "fetch_timeout", "prepared", 1, now)
    assert prepared.disposition == "manual" and prepared.retry_at is None


def test_invalid_ready_evidence_persists_truthful_manual_failure(
    tmp_path: Path,
) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    ready = _attempt("ready", retry_at=None, attempt=3)
    persist_attempt(paths.attempt, ready, expected_revision=None)
    now = datetime(2026, 7, 21, 1, 2, 4, tzinfo=timezone.utc)

    failed = next_failure_record(
        ready, "registry_invalid", ready.mutation_phase, ready.manifest_revision, now
    )

    assert (
        failed.record_revision,
        failed.category,
        failed.disposition,
        failed.retry_at,
        failed.mutation_phase,
        failed.manifest_revision,
    ) == (2, "registry_invalid", "manual", None, "added", 2)
    assert (
        failed.attempt,
        failed.identifier,
        failed.route_pair_digest,
        failed.workflow_generation,
        failed.created_at,
    ) == (
        ready.attempt,
        ready.identifier,
        ready.route_pair_digest,
        ready.workflow_generation,
        ready.created_at,
    )
    assert failed.updated_at == "2026-07-21T01:02:04Z"
    persist_attempt(paths.attempt, failed, expected_revision=ready.record_revision)
    assert read_attempt(paths.attempt) == failed


def test_attempt_persistence_uses_revision_cas(tmp_path: Path) -> None:
    path = tmp_path / "attempts" / f"{_scope().identifier}.json"
    path.parent.mkdir()
    record = _attempt()
    persist_attempt(path, record, expected_revision=None)

    with pytest.raises(AidtWorktreeFailure, match="cas_mismatch"):
        persist_attempt(path, replace(record, record_revision=2), expected_revision=7)
    persist_attempt(path, replace(record, record_revision=2), expected_revision=1)
    assert read_attempt(path).record_revision == 2


def test_durable_due_admission_consumes_revision_before_return(tmp_path: Path) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    persist_attempt(paths.attempt, _attempt(), expected_revision=None)
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)

    admission = admit_attempt(paths, 1, "1" * 64, "2" * 64, now)

    assert admission.admitted is True
    assert admission.record.record_revision == 2
    assert read_attempt(paths.attempt).record_revision == 2
    with pytest.raises(AidtWorktreeFailure, match="cas_mismatch"):
        admit_attempt(paths, 1, "1" * 64, "2" * 64, now)


@pytest.mark.parametrize("record_kind", ["manifest", "ownership", "attempt"])
def test_optional_readers_return_none_only_for_collision_free_enoent_and_read_exact(
    tmp_path: Path, record_kind: str
) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    path, record, reader, writer = _optional_record_case(paths, tmp_path, record_kind)

    assert reader(path) is None
    writer(path, record, expected_revision=None)
    assert reader(path) == record


@pytest.mark.parametrize("record_kind", ["manifest", "ownership", "attempt"])
def test_optional_readers_reject_casefold_alias_on_every_host_filesystem(
    tmp_path: Path, record_kind: str
) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    path, record, reader, _writer = _optional_record_case(paths, tmp_path, record_kind)
    alias = path.with_name(path.name.casefold())
    alias.write_bytes(canonical_json_bytes(record))
    alias.chmod(0o600)

    with pytest.raises(AidtWorktreeFailure, match="registry_collision"):
        reader(path)


@pytest.mark.parametrize("record_kind", ["manifest", "ownership", "attempt"])
@pytest.mark.parametrize("fault", ["symlink", "mode", "directory", "malformed"])
def test_optional_readers_never_treat_invalid_existing_records_as_absent(
    tmp_path: Path, record_kind: str, fault: str
) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    path, record, reader, _writer = _optional_record_case(paths, tmp_path, record_kind)
    _write_optional_fault(path, record, fault)

    with pytest.raises(AidtWorktreeFailure, match="(?:manifest|registry)_invalid"):
        reader(path)


@pytest.mark.parametrize("elapsed_seconds", [0, 1, 600])
def test_due_admission_and_active_phases_remain_valid_after_elapsed_time(
    elapsed_seconds: int,
) -> None:
    start = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    due = start + timedelta(seconds=elapsed_seconds)
    initial = initial_attempt_record(_scope().identifier, "1" * 64, "2" * 64, start)

    admission = evaluate_attempt_admission(initial, 1, "1" * 64, "2" * 64, due)
    assert admission.admitted is True and admission.record.attempt == 1
    assert admission.record.retry_at == admission.record.updated_at == _timestamp(due)

    prepared_at = due + timedelta(seconds=1)
    prepared = advance_attempt_phase(admission.record, "prepared", 1, prepared_at)
    assert prepared.retry_at == prepared.updated_at == _timestamp(prepared_at)
    added_at = prepared_at + timedelta(seconds=1)
    added = advance_attempt_phase(prepared, "added", 1, added_at)
    ready = ready_attempt_record(added, 2, added_at + timedelta(seconds=1))
    removing = advance_attempt_phase(ready, "removing", 3, added_at + timedelta(seconds=2))

    assert (prepared.mutation_phase, prepared.manifest_revision) == ("prepared", 1)
    assert (added.mutation_phase, added.manifest_revision) == ("added", 1)
    assert (ready.disposition, ready.mutation_phase, ready.manifest_revision) == (
        "ready", "added", 2
    )
    assert (removing.disposition, removing.mutation_phase, removing.manifest_revision) == (
        "ready", "removing", 3
    )


def test_backward_clock_blocks_admission_and_every_durable_attempt_transition() -> None:
    start = datetime(2026, 7, 21, 1, 2, 13, tzinfo=timezone.utc)
    backward = start - timedelta(seconds=5)
    initial = initial_attempt_record(_scope().identifier, "1" * 64, "2" * 64, start)

    blocked = evaluate_attempt_admission(initial, 1, "1" * 64, "2" * 64, backward)
    assert blocked.admitted is False and blocked.record == initial
    admitted = evaluate_attempt_admission(initial, 1, "1" * 64, "2" * 64, start)
    with pytest.raises(AidtWorktreeFailure, match="clock_invalid"):
        advance_attempt_phase(admitted.record, "prepared", 1, backward)
    with pytest.raises(AidtWorktreeFailure, match="clock_invalid"):
        next_failure_record(admitted.record, "fetch_timeout", "none", None, backward)


@pytest.mark.parametrize("attempt_count", [1, 2, 3])
def test_attempt_constructors_enforce_exact_source_phase_and_manifest_revisions(
    attempt_count: int,
) -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    initial = initial_attempt_record(_scope().identifier, "1" * 64, "2" * 64, now)
    first = evaluate_attempt_admission(initial, 1, "1" * 64, "2" * 64, now).record
    admitted = replace(first, attempt=attempt_count)
    prepared = advance_attempt_phase(admitted, "prepared", 1, now)
    added = advance_attempt_phase(prepared, "added", 1, now)
    ready_from_prepared = ready_attempt_record(prepared, 2, now)
    ready_from_added = ready_attempt_record(added, 2, now)

    assert initial.attempt == 0 and admitted.attempt == attempt_count
    assert ready_from_prepared.record_revision == prepared.record_revision + 1
    assert ready_from_added.record_revision == added.record_revision + 1
    assert replace(ready_from_prepared, record_revision=ready_from_added.record_revision) == ready_from_added
    removing = advance_attempt_phase(ready_from_added, "removing", 3, now)
    assert removing.record_revision == ready_from_added.record_revision + 1


def test_initial_attempt_constructor_is_exact_and_rejects_invalid_inputs() -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    record = initial_attempt_record(_scope().identifier, "1" * 64, "2" * 64, now)

    assert (
        record.schema,
        record.record_revision,
        record.category,
        record.disposition,
        record.attempt,
        record.mutation_phase,
        record.manifest_revision,
    ) == (
        AIDT_WORKTREE_ATTEMPT_SCHEMA,
        1,
        "attempt_backoff",
        "backoff",
        0,
        "none",
        None,
    )
    assert record.created_at == record.updated_at == record.retry_at == NOW
    invalid_calls = [
        lambda: initial_attempt_record("a20-1188--viewer-api", "1" * 64, "2" * 64, now),
        lambda: initial_attempt_record(_scope().identifier, "bad", "2" * 64, now),
        lambda: initial_attempt_record(
            _scope().identifier, "1" * 64, "2" * 64, now.replace(tzinfo=None)
        ),
    ]
    for call in invalid_calls:
        with pytest.raises(AidtWorktreeFailure, match="(?:registry|clock)_invalid"):
            call()


def test_attempt_constructors_reject_manual_attempt_zero_ready_and_arbitrary_revisions() -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    initial = initial_attempt_record(_scope().identifier, "1" * 64, "2" * 64, now)
    admitted = evaluate_attempt_admission(initial, 1, "1" * 64, "2" * 64, now).record
    manual = replace(admitted, category="collision", disposition="manual", retry_at=None)
    prepared = advance_attempt_phase(admitted, "prepared", 1, now)
    added = advance_attempt_phase(prepared, "added", 1, now)
    ready = ready_attempt_record(added, 2, now)

    invalid_phase_calls = [
        lambda: advance_attempt_phase(initial, "prepared", 1, now),
        lambda: advance_attempt_phase(manual, "prepared", 1, now),
        lambda: advance_attempt_phase(admitted, "prepared", 99, now),
        lambda: advance_attempt_phase(added, "removing", 3, now),
        lambda: advance_attempt_phase(ready, "removing", 2, now),
    ]
    invalid_ready_calls = [
        lambda: ready_attempt_record(initial, 2, now),
        lambda: ready_attempt_record(manual, 2, now),
        lambda: ready_attempt_record(ready, 2, now),
        lambda: ready_attempt_record(prepared, 1, now),
        lambda: ready_attempt_record(added, 99, now),
    ]
    for call in [*invalid_phase_calls, *invalid_ready_calls]:
        with pytest.raises(AidtWorktreeFailure, match="internal_error"):
            call()


def test_helper_generated_attempts_persist_only_through_exact_cas(tmp_path: Path) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    initial = initial_attempt_record(_scope().identifier, "1" * 64, "2" * 64, now)
    persist_attempt(paths.attempt, initial, expected_revision=None)
    admitted = evaluate_attempt_admission(initial, 1, "1" * 64, "2" * 64, now).record
    persist_attempt(paths.attempt, admitted, expected_revision=1)
    prepared = advance_attempt_phase(admitted, "prepared", 1, now)

    with pytest.raises(AidtWorktreeFailure, match="cas_mismatch"):
        persist_attempt(paths.attempt, prepared, expected_revision=1)
    persist_attempt(paths.attempt, prepared, expected_revision=2)
    assert read_optional_attempt(paths.attempt) == prepared


def test_manual_only_categories_cannot_persist_as_backoff_or_promote() -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    for category in MANUAL_ONLY_CATEGORIES:
        with pytest.raises(AidtWorktreeFailure, match="registry_invalid"):
            invalid = replace(_attempt(), category=category)
            prepared = advance_attempt_phase(invalid, "prepared", 1, now)
            ready_attempt_record(prepared, 2, now)


@pytest.mark.parametrize("category", MANUAL_ONLY_CATEGORIES)
def test_canonical_attempt_reader_rejects_manual_only_backoff_category(
    tmp_path: Path, category: str
) -> None:
    path = tmp_path / "attempts" / f"{_scope().identifier}.json"
    path.parent.mkdir()
    raw = asdict(_attempt())
    raw["category"] = category
    path.write_bytes(canonical_json_bytes(raw))
    path.chmod(0o600)

    with pytest.raises(AidtWorktreeFailure, match="registry_invalid"):
        read_optional_attempt(path)


@pytest.mark.parametrize("category", ACTIVE_BACKOFF_CATEGORIES)
def test_exact_active_backoff_categories_prepare_add_and_become_ready(
    category: str,
) -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    active = replace(_attempt(), category=category)
    prepared = advance_attempt_phase(active, "prepared", 1, now)
    added = advance_attempt_phase(prepared, "added", 1, now)
    ready = ready_attempt_record(added, 2, now)

    assert ready.category == ready.disposition == "ready"
    assert (ready.attempt, ready.mutation_phase, ready.manifest_revision) == (1, "added", 2)


def test_waiting_retry_is_valid_but_cannot_advance_before_due_admission() -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    waiting = replace(_attempt(), retry_at="2026-07-21T01:02:33Z")

    admission = evaluate_attempt_admission(
        waiting, waiting.record_revision, "1" * 64, "2" * 64, now
    )
    assert admission.action == "backoff" and admission.record == waiting
    with pytest.raises(AidtWorktreeFailure, match="internal_error"):
        advance_attempt_phase(waiting, "prepared", 1, now)


def test_active_mutation_phases_require_consumed_retry_clock_shape() -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    prepared = advance_attempt_phase(_attempt(), "prepared", 1, now)

    with pytest.raises(AidtWorktreeFailure, match="registry_invalid"):
        replace(prepared, retry_at="2026-07-21T01:02:33Z")


def test_attempt_zero_is_valid_only_for_initial_or_scope_reset() -> None:
    now = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
    initial = initial_attempt_record(_scope().identifier, "1" * 64, "2" * 64, now)
    source = _attempt("manual", retry_at=None)
    reset = evaluate_attempt_admission(
        source,
        source.record_revision,
        "3" * 64,
        "4" * 64,
        now,
        scope_attested=True,
    ).record

    assert (initial.category, initial.attempt) == ("attempt_backoff", 0)
    assert (reset.category, reset.attempt) == ("scope_changed", 0)
    for category in ("lock_timeout", "fetch_timeout", "fetch_command_failed"):
        with pytest.raises(AidtWorktreeFailure, match="registry_invalid"):
            replace(_attempt(), category=category, attempt=0)


@pytest.mark.parametrize("record_kind", ["manifest", "ownership", "attempt"])
def test_optional_readers_stop_at_cap_plus_one_without_overread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_kind: str,
) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", _scope().identifier)
    activate_registry(paths, "9" * 64, NOW)
    path, _record, reader, _writer = _optional_record_case(paths, tmp_path, record_kind)
    entries = _CapPlusOneEntries()
    monkeypatch.setattr(manifest_module.os, "scandir", lambda _path: entries)

    with pytest.raises(AidtWorktreeFailure, match="registry_invalid"):
        reader(path)
    assert entries.pulled == MAX_REGISTRY_ENTRIES + 1
    assert entries.closed is True


def _optional_record_case(
    paths: Any, tmp_path: Path, kind: str
) -> tuple[Path, object, Any, Any]:
    if kind == "manifest":
        return paths.manifest, _manifest(tmp_path), read_optional_manifest, persist_manifest
    if kind == "ownership":
        return paths.ownership, _ownership(tmp_path), read_optional_ownership, persist_ownership
    return paths.attempt, _attempt(), read_optional_attempt, persist_attempt


def _write_optional_fault(path: Path, record: object, fault: str) -> None:
    if fault == "directory":
        path.mkdir()
        return
    if fault == "symlink":
        target = path.with_name("target.json")
        target.write_bytes(canonical_json_bytes(record))
        target.chmod(0o600)
        path.symlink_to(target)
        return
    path.write_bytes(b"{}\n" if fault == "malformed" else canonical_json_bytes(record))
    path.chmod(0o644 if fault == "mode" else 0o600)


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class _CapPlusOneEntries:
    def __init__(self) -> None:
        self.pulled = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        self.pulled += 1
        if self.pulled <= MAX_REGISTRY_ENTRIES + 1:
            return SimpleNamespace(name=f"unrelated-{self.pulled}.json")
        raise AssertionError("optional reader overread cap-plus-one sentinel")

    def close(self) -> None:
        self.closed = True
