"""Strict canonical persistence for AIDT worktree ownership metadata."""

from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_hex
from typing import Any, Callable, Iterator, Mapping, TypeVar, cast

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX is a frozen capability.
    fcntl = None  # type: ignore[assignment]

from .contract import (
    AIDT_WORKTREE_ACTIVATION_SCHEMA,
    AIDT_WORKTREE_ATTEMPT_SCHEMA,
    AIDT_WORKTREE_BASE_REF,
    AIDT_WORKTREE_OWNERSHIP_SCHEMA,
    AIDT_WORKTREE_SCHEMA,
    MAX_DURABLE_FILE_BYTES,
    MAX_REGISTRY_ENTRIES,
    AidtWorktreeFailure,
    StableMetadataPaths,
    StableWorktreePaths,
    _FAILURE_CATEGORIES,
    _HEX_64,
    _SERVICE_ID,
    _canonical_absolute_path,
    _revision,
    _valid_child_identifier,
    _valid_timestamp,
    derive_aidt_branch,
)


_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "manifest_revision",
        "state",
        "identifier",
        "coordinator",
        "service",
        "kind",
        "workflow_identity",
        "board_identity",
        "workspace_root",
        "workspace_path",
        "catalog_checkout",
        "canonical_service_root",
        "common_git_identity",
        "object_format",
        "route_pair_digest",
        "repository_binding_digest",
        "route_fingerprint",
        "coordinator_fingerprint",
        "source_revision",
        "catalog_revision",
        "branch",
        "base_ref",
        "base_sha",
        "route_scope",
        "pre_proof",
        "post_proof",
        "removal_proof",
        "created_at",
        "updated_at",
    }
)
_SCOPE_KEYS = frozenset(
    {
        "identifier",
        "coordinator",
        "service",
        "kind",
        "issue_type",
        "change_kind",
        "route_pair_digest",
        "route_fingerprint",
        "coordinator_fingerprint",
        "source_revision",
        "catalog_revision",
        "checkout_revision",
        "repository_binding_digest",
    }
)
_SNAPSHOT_KEYS = frozenset(
    {
        "phase",
        "observed_at",
        "repository_binding_digest",
        "root_head",
        "root_symbolic_digest",
        "root_status_digest",
        "root_content_digest",
        "root_content_count",
        "root_content_bytes",
        "registry_digest",
        "registry_count",
        "protected_digest",
        "protected_count",
        "refs_digest",
        "refs_count",
        "base_ref_sha",
        "target_ref_sha",
        "target_registration_digest",
    }
)
_PRE_KEYS = frozenset({"s0", "s1", "fetch_delta_digest"})
_POST_KEYS = frozenset(
    {
        "s2",
        "create_delta_digest",
        "ticket_head",
        "registration_digest",
        "clean_at_create",
        "no_upstream",
    }
)
_REMOVAL_KEYS = frozenset(
    {
        "authority_digest",
        "pre_snapshot",
        "post_snapshot",
        "remove_delta_digest",
        "retained_branch_sha",
    }
)
_ACTIVATION_KEYS = frozenset(
    {"schema", "registry_revision", "workflow_identity", "created_at", "updated_at"}
)
_OWNERSHIP_KEYS = frozenset(
    {
        "schema",
        "record_revision",
        "identifier",
        "service",
        "workspace_root",
        "workspace_path",
        "manifest_path",
        "route_pair_digest",
        "manifest_revision",
        "tombstone",
        "created_at",
        "updated_at",
    }
)
_ATTEMPT_KEYS = frozenset(
    {
        "schema",
        "record_revision",
        "identifier",
        "route_pair_digest",
        "workflow_generation",
        "category",
        "disposition",
        "attempt",
        "retry_at",
        "mutation_phase",
        "manifest_revision",
        "created_at",
        "updated_at",
    }
)
_PHASES = frozenset({"s0", "s1", "s2", "resume", "cleanup_pre", "cleanup_post"})
_MUTATION_PHASES = frozenset({"none", "prepared", "added", "removing"})
_STATES = ("prepared", "ready", "removing", "removed")
_DISPOSITIONS = frozenset({"backoff", "manual", "ready"})
_RETRYABLE = frozenset({"lock_timeout", "fetch_timeout", "fetch_command_failed"})
_INITIAL_BACKOFF_CATEGORIES = frozenset({"attempt_backoff", "scope_changed"})
_ACTIVE_BACKOFF_CATEGORIES = _INITIAL_BACKOFF_CATEGORIES | _RETRYABLE
_DELAYS = (30, 120, 600)
_LOCK_NAME = re.compile(r"^(?:manifest|common-git)-[0-9a-f]{64}\.lock$")


@dataclass(frozen=True)
class RouteScope:
    identifier: str
    coordinator: str
    service: str
    kind: str
    issue_type: str
    change_kind: str
    route_pair_digest: str
    route_fingerprint: str
    coordinator_fingerprint: str
    source_revision: str
    catalog_revision: str
    checkout_revision: str
    repository_binding_digest: str

    def __post_init__(self) -> None:
        if not _valid_scope(self):
            raise AidtWorktreeFailure("manifest_invalid", self.identifier)


@dataclass(frozen=True)
class RepositorySnapshot:
    phase: str
    observed_at: str
    repository_binding_digest: str
    root_head: str
    root_symbolic_digest: str
    root_status_digest: str
    root_content_digest: str
    root_content_count: int
    root_content_bytes: int
    registry_digest: str
    registry_count: int
    protected_digest: str
    protected_count: int
    refs_digest: str
    refs_count: int
    base_ref_sha: str
    target_ref_sha: str | None
    target_registration_digest: str | None

    def __post_init__(self) -> None:
        if not _valid_snapshot(self):
            raise AidtWorktreeFailure("manifest_invalid")


@dataclass(frozen=True)
class PreProof:
    s0: RepositorySnapshot
    s1: RepositorySnapshot
    fetch_delta_digest: str

    def __post_init__(self) -> None:
        valid = (
            type(self.s0) is RepositorySnapshot
            and type(self.s1) is RepositorySnapshot
            and self.s0.phase == "s0"
            and self.s1.phase == "s1"
            and _digest(self.fetch_delta_digest)
        )
        if not valid:
            raise AidtWorktreeFailure("manifest_invalid")


@dataclass(frozen=True)
class PostProof:
    s2: RepositorySnapshot
    create_delta_digest: str
    ticket_head: str
    registration_digest: str
    clean_at_create: bool
    no_upstream: bool

    def __post_init__(self) -> None:
        valid = (
            type(self.s2) is RepositorySnapshot
            and self.s2.phase == "s2"
            and _digest(self.create_delta_digest)
            and _sha1(self.ticket_head)
            and _digest(self.registration_digest)
            and self.clean_at_create is True
            and self.no_upstream is True
        )
        if not valid:
            raise AidtWorktreeFailure("manifest_invalid")


@dataclass(frozen=True)
class RemovalProof:
    authority_digest: str
    pre_snapshot: RepositorySnapshot
    post_snapshot: RepositorySnapshot | None
    remove_delta_digest: str | None
    retained_branch_sha: str

    def __post_init__(self) -> None:
        if not _valid_removal(self):
            raise AidtWorktreeFailure("manifest_invalid")


@dataclass(frozen=True)
class AidtWorktreeManifest:
    schema: str
    manifest_revision: int
    state: str
    identifier: str
    coordinator: str
    service: str
    kind: str
    workflow_identity: str
    board_identity: str
    workspace_root: str
    workspace_path: str
    catalog_checkout: str
    canonical_service_root: str
    common_git_identity: str
    object_format: str
    route_pair_digest: str
    repository_binding_digest: str
    route_fingerprint: str
    coordinator_fingerprint: str
    source_revision: str
    catalog_revision: str
    branch: str
    base_ref: str
    base_sha: str
    route_scope: RouteScope
    pre_proof: PreProof
    post_proof: PostProof | None
    removal_proof: RemovalProof | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not _valid_manifest(self):
            raise AidtWorktreeFailure("manifest_invalid", self.identifier)


@dataclass(frozen=True)
class ActivationRecord:
    schema: str
    registry_revision: int
    workflow_identity: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        valid = (
            self.schema == AIDT_WORKTREE_ACTIVATION_SCHEMA
            and _revision(self.registry_revision)
            and _digest(self.workflow_identity)
            and _timestamp_pair(self.created_at, self.updated_at)
        )
        if not valid:
            raise AidtWorktreeFailure("registry_invalid")


@dataclass(frozen=True)
class OwnershipRecord:
    schema: str
    record_revision: int
    identifier: str
    service: str
    workspace_root: str
    workspace_path: str
    manifest_path: str
    route_pair_digest: str
    manifest_revision: int
    tombstone: bool
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not _valid_ownership(self):
            raise AidtWorktreeFailure("registry_invalid", self.identifier)


@dataclass(frozen=True)
class AttemptRecord:
    schema: str
    record_revision: int
    identifier: str
    route_pair_digest: str
    workflow_generation: str
    category: str
    disposition: str
    attempt: int
    retry_at: str | None
    mutation_phase: str
    manifest_revision: int | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not _valid_attempt(self):
            raise AidtWorktreeFailure("registry_invalid", self.identifier)


@dataclass(frozen=True)
class RegistryDiscovery:
    activation: ActivationRecord
    identifiers: frozenset[str]
    ownership: Mapping[str, OwnershipRecord]
    attempts: Mapping[str, AttemptRecord]
    manifests: Mapping[str, AidtWorktreeManifest]


@dataclass(frozen=True)
class AttemptAdmission:
    admitted: bool
    action: str
    record: AttemptRecord


@dataclass(frozen=True)
class DurabilityResult:
    directory_fsync: str


def canonical_json_bytes(value: object) -> bytes:
    """Encode one strict JSON value using the frozen durable representation."""
    raw = asdict(cast(Any, value)) if is_dataclass(value) else value
    _validate_json_tree(raw)
    try:
        text = json.dumps(
            raw,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        encoded = (text + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AidtWorktreeFailure("manifest_invalid") from exc
    if len(encoded) > MAX_DURABLE_FILE_BYTES:
        raise AidtWorktreeFailure("manifest_too_large")
    return encoded


def decode_canonical_json(data: bytes) -> object:
    """Decode canonical JSON while rejecting duplicate keys before parsing."""
    if type(data) is not bytes:
        raise AidtWorktreeFailure("manifest_invalid")
    if len(data) > MAX_DURABLE_FILE_BYTES:
        raise AidtWorktreeFailure("manifest_too_large")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AidtWorktreeFailure("manifest_invalid") from exc
    if canonical_json_bytes(value) != data:
        raise AidtWorktreeFailure("manifest_invalid")
    return value


def manifest_from_dict(value: object) -> AidtWorktreeManifest:
    raw = _exact_dict(value, _MANIFEST_KEYS)
    try:
        return AidtWorktreeManifest(
            **{
                **raw,
                "route_scope": _scope_from_dict(raw["route_scope"]),
                "pre_proof": _pre_from_dict(raw["pre_proof"]),
                "post_proof": _nullable_post(raw["post_proof"]),
                "removal_proof": _nullable_removal(raw["removal_proof"]),
            }
        )
    except (TypeError, AidtWorktreeFailure) as exc:
        raise AidtWorktreeFailure("manifest_invalid") from exc


def read_manifest(path: Path) -> AidtWorktreeManifest:
    return manifest_from_dict(decode_canonical_json(_read_regular(path)))


def read_optional_manifest(path: Path) -> AidtWorktreeManifest | None:
    """Return no manifest only when the exact path does not exist."""
    return _read_optional_record(path, read_manifest)


def read_activation(path: Path) -> ActivationRecord:
    try:
        raw = _exact_dict(decode_canonical_json(_read_regular(path)), _ACTIVATION_KEYS)
        return ActivationRecord(**raw)
    except (TypeError, AidtWorktreeFailure) as exc:
        raise AidtWorktreeFailure("registry_invalid") from exc


def read_ownership(path: Path) -> OwnershipRecord:
    try:
        raw = _exact_dict(decode_canonical_json(_read_regular(path)), _OWNERSHIP_KEYS)
        return OwnershipRecord(**raw)
    except (TypeError, AidtWorktreeFailure) as exc:
        raise AidtWorktreeFailure("registry_invalid") from exc


def read_optional_ownership(path: Path) -> OwnershipRecord | None:
    """Return no ownership record only for an absent exact path."""
    return _read_optional_record(path, read_ownership)


def read_attempt(path: Path) -> AttemptRecord:
    try:
        raw = _exact_dict(decode_canonical_json(_read_regular(path)), _ATTEMPT_KEYS)
        return AttemptRecord(**raw)
    except (TypeError, AidtWorktreeFailure) as exc:
        raise AidtWorktreeFailure("registry_invalid") from exc


def read_optional_attempt(path: Path) -> AttemptRecord | None:
    """Return no attempt record only for an absent exact path."""
    return _read_optional_record(path, read_attempt)


def persist_manifest(
    path: Path, record: AidtWorktreeManifest, *, expected_revision: int | None
) -> DurabilityResult:
    current = _cas_current(path, expected_revision, read_manifest)
    if current is None:
        if record.manifest_revision != 1 or record.state != "prepared":
            raise AidtWorktreeFailure("manifest_invalid", record.identifier)
    else:
        _validate_manifest_transition(current, record)
    return _atomic_replace(path, canonical_json_bytes(record))


def persist_ownership(
    path: Path, record: OwnershipRecord, *, expected_revision: int | None
) -> DurabilityResult:
    current = _cas_current(path, expected_revision, read_ownership)
    _validate_sidecar_revision(current, record.record_revision)
    if current is not None and current.created_at != record.created_at:
        raise AidtWorktreeFailure("registry_invalid", record.identifier)
    return _atomic_replace(path, canonical_json_bytes(record))


def persist_attempt(
    path: Path, record: AttemptRecord, *, expected_revision: int | None
) -> DurabilityResult:
    current = _cas_current(path, expected_revision, read_attempt)
    _validate_sidecar_revision(current, record.record_revision)
    if current is not None and current.created_at != record.created_at:
        raise AidtWorktreeFailure("registry_invalid", record.identifier)
    return _atomic_replace(path, canonical_json_bytes(record))


def activate_registry(
    paths: StableMetadataPaths, workflow_identity: str, now: str
) -> ActivationRecord:
    """Create one idempotent activation marker and the closed metadata layout."""
    if not _digest(workflow_identity) or not _valid_timestamp(now):
        raise AidtWorktreeFailure("registry_invalid")
    _require_directory(paths.root.parent.parent)
    for directory in (
        paths.root.parent,
        paths.root,
        paths.manifests,
        paths.ownership_records,
        paths.attempts,
        paths.locks,
    ):
        _ensure_directory(directory)
    if _lexists(paths.activation):
        existing = read_activation(paths.activation)
        if existing.workflow_identity != workflow_identity:
            raise AidtWorktreeFailure("identity_invalid")
        return existing
    record = ActivationRecord(AIDT_WORKTREE_ACTIVATION_SCHEMA, 1, workflow_identity, now, now)
    _atomic_replace(paths.activation, canonical_json_bytes(record), require_absent=True)
    return record


def discover_registry(root: Path) -> RegistryDiscovery:
    """Boundedly discover every durable child guard after first activation."""
    _require_directory(root)
    activation = read_activation(root / "ACTIVATED.json")
    expected = {"ACTIVATED.json", "manifests", "ownership", "attempts", "locks"}
    if {entry.name for entry in os.scandir(root)} != expected:
        raise AidtWorktreeFailure("registry_invalid")
    manifests = _scan_records(root / "manifests", read_manifest)
    ownership = _scan_records(root / "ownership", read_ownership)
    attempts = _scan_records(root / "attempts", read_attempt)
    _scan_locks(root / "locks")
    if not set(ownership).issubset(manifests):
        raise AidtWorktreeFailure("registry_invalid")
    for identifier, record in ownership.items():
        expected = root / "manifests" / f"{identifier}.json"
        if Path(record.manifest_path) != expected:
            raise AidtWorktreeFailure("registry_invalid")
    identifiers = frozenset(set(manifests) | set(ownership) | set(attempts))
    return RegistryDiscovery(activation, identifiers, ownership, attempts, manifests)


def registry_recognizes_identifier(root: Path, identifier: object) -> bool:
    """Recognize durable ownership before parsing possibly corrupt records."""
    if not _valid_child_identifier(identifier):
        return False
    if not _lexists(root):
        return False
    _require_directory(root)
    read_activation(root / "ACTIVATED.json")
    folded = str(identifier).casefold()
    for name in ("manifests", "ownership", "attempts"):
        directory = root / name
        _require_directory(directory)
        entries = list(os.scandir(directory))
        if len(entries) > MAX_REGISTRY_ENTRIES:
            raise AidtWorktreeFailure("registry_invalid")
        if any(_record_stem(entry.name).casefold() == folded for entry in entries):
            return True
    return False


def registry_recognizes_path(root: Path, candidate: Path) -> bool:
    """Recognize stable metadata and recorded worktree paths, including tombstones."""
    if not _lexists(root):
        return False
    _require_directory(root)
    canonical_root = _canonical_absolute_path(root)
    canonical_candidate = _canonical_absolute_path(candidate)
    if canonical_candidate == canonical_root or canonical_root in canonical_candidate.parents:
        return True
    discovery = discover_registry(canonical_root)
    return any(Path(record.workspace_path) == canonical_candidate for record in discovery.ownership.values())


@contextmanager
def advisory_lock(path: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
    """Acquire a kernel-released POSIX lock without stale-file stealing."""
    if fcntl is None or not Path("/usr/bin/false").is_file():
        raise AidtWorktreeFailure("capability_unsupported")
    if (
        type(timeout_seconds) not in {int, float}
        or not math.isfinite(timeout_seconds)
        or not 0 <= timeout_seconds <= 30
    ):
        raise AidtWorktreeFailure("lock_timeout")
    fd = _open_lock(path)
    try:
        _wait_for_lock(fd, float(timeout_seconds))
        yield
    finally:
        try:
            lock_module = cast(Any, fcntl)
            lock_module.flock(fd, lock_module.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def ordered_worktree_locks(
    common_git_lock: Path, manifest_lock: Path, *, timeout_seconds: float = 5.0
) -> Iterator[None]:
    """Acquire common-Git first and manifest second on every lifecycle path."""
    with advisory_lock(common_git_lock, timeout_seconds=timeout_seconds):
        with advisory_lock(manifest_lock, timeout_seconds=timeout_seconds):
            yield


def evaluate_attempt_admission(
    record: AttemptRecord,
    expected_revision: int,
    route_pair_digest: str,
    workflow_generation: str,
    now: datetime,
    *,
    scope_attested: bool = False,
) -> AttemptAdmission:
    """Evaluate the durable clock without allowing one revision twice."""
    instant = _utc_second(now)
    if record.record_revision != expected_revision:
        raise AidtWorktreeFailure("cas_mismatch", record.identifier)
    if not _digest(route_pair_digest) or not _digest(workflow_generation):
        raise AidtWorktreeFailure("identity_invalid", record.identifier)
    if (record.route_pair_digest, record.workflow_generation) != (
        route_pair_digest,
        workflow_generation,
    ):
        return _reset_attempt_scope(record, route_pair_digest, workflow_generation, instant, scope_attested)
    return _admit_matching_scope(record, instant)


def admit_attempt(
    paths: StableWorktreePaths,
    expected_revision: int,
    route_pair_digest: str,
    workflow_generation: str,
    now: datetime,
    *,
    scope_attested: bool = False,
) -> AttemptAdmission:
    """Atomically consume one persisted attempt revision before Git work."""
    with advisory_lock(paths.manifest_lock):
        record = read_attempt(paths.attempt)
        admission = evaluate_attempt_admission(
            record,
            expected_revision,
            route_pair_digest,
            workflow_generation,
            now,
            scope_attested=scope_attested,
        )
        if admission.record != record:
            persist_attempt(
                paths.attempt,
                admission.record,
                expected_revision=record.record_revision,
            )
        return admission


def next_failure_record(
    record: AttemptRecord,
    category: str,
    mutation_phase: str,
    manifest_revision: int | None,
    now: datetime,
) -> AttemptRecord:
    """Persist retry only before intent; all post-intent failures are manual."""
    instant = _monotonic_attempt_time(record, now)
    if (
        type(category) is not str
        or type(mutation_phase) is not str
        or category not in _FAILURE_CATEGORIES
        or mutation_phase not in _MUTATION_PHASES
        or not 1 <= record.attempt <= 3
    ):
        raise AidtWorktreeFailure("internal_error", record.identifier)
    retryable = category in _RETRYABLE and mutation_phase == "none" and record.attempt < 3
    retry_at = _format_utc(instant + timedelta(seconds=_DELAYS[record.attempt - 1])) if retryable else None
    return AttemptRecord(
        record.schema,
        record.record_revision + 1,
        record.identifier,
        record.route_pair_digest,
        record.workflow_generation,
        category,
        "backoff" if retryable else "manual",
        record.attempt,
        retry_at,
        mutation_phase,
        manifest_revision,
        record.created_at,
        _format_utc(instant),
    )


def initial_attempt_record(
    identifier: str,
    route_pair_digest: str,
    workflow_generation: str,
    now: datetime,
) -> AttemptRecord:
    """Create the first due attempt record for one attested route scope."""
    instant = _utc_second(now)
    timestamp = _format_utc(instant)
    return AttemptRecord(
        AIDT_WORKTREE_ATTEMPT_SCHEMA,
        1,
        identifier,
        route_pair_digest,
        workflow_generation,
        "attempt_backoff",
        "backoff",
        0,
        timestamp,
        "none",
        None,
        timestamp,
        timestamp,
    )


def advance_attempt_phase(
    record: AttemptRecord,
    mutation_phase: str,
    manifest_revision: int,
    now: datetime,
) -> AttemptRecord:
    """Advance durable intent without changing attempt ownership or scope."""
    instant = _monotonic_attempt_time(record, now)
    if not _valid_phase_transition(record, mutation_phase, manifest_revision):
        raise AidtWorktreeFailure("internal_error", record.identifier)
    timestamp = _format_utc(instant)
    return AttemptRecord(
        record.schema,
        record.record_revision + 1,
        record.identifier,
        record.route_pair_digest,
        record.workflow_generation,
        record.category,
        record.disposition,
        record.attempt,
        timestamp if record.disposition == "backoff" else None,
        mutation_phase,
        manifest_revision,
        record.created_at,
        timestamp,
    )


def ready_attempt_record(
    record: AttemptRecord,
    manifest_revision: int,
    now: datetime,
) -> AttemptRecord:
    """Close one successful provision or resume as dispatchable ready."""
    instant = _monotonic_attempt_time(record, now)
    if not _valid_ready_source(record, manifest_revision):
        raise AidtWorktreeFailure("internal_error", record.identifier)
    return AttemptRecord(
        record.schema,
        record.record_revision + 1,
        record.identifier,
        record.route_pair_digest,
        record.workflow_generation,
        "ready",
        "ready",
        record.attempt,
        None,
        "added",
        manifest_revision,
        record.created_at,
        _format_utc(instant),
    )


def _valid_scope(value: RouteScope) -> bool:
    try:
        expected_branch = derive_aidt_branch(value.coordinator, value.kind, value.change_kind)
    except AidtWorktreeFailure:
        return False
    prefix = value.identifier.split("--") if type(value.identifier) is str else []
    return all(
        (
            _valid_child_identifier(value.identifier),
            len(prefix) == 2 and prefix[0] == value.coordinator and prefix[1] == value.service,
            type(value.service) is str and _service(value.service),
            type(value.issue_type) is str
            and value.issue_type in {"bug", "story", "task", "sub-task", "improvement", "new feature"},
            value.change_kind == ("fix" if value.issue_type == "bug" else "feat"),
            expected_branch.endswith(value.coordinator),
            all(_digest(item) for item in _scope_digests(value)),
            _sha1(value.checkout_revision),
        )
    )


def _scope_digests(value: RouteScope) -> tuple[object, ...]:
    return (
        value.route_pair_digest,
        value.route_fingerprint,
        value.coordinator_fingerprint,
        value.source_revision,
        value.catalog_revision,
        value.repository_binding_digest,
    )


def _valid_snapshot(value: RepositorySnapshot) -> bool:
    target_shape = _valid_snapshot_target_shape(value)
    return all(
        (
            type(value.phase) is str and value.phase in _PHASES,
            type(value.observed_at) is str and _valid_timestamp(value.observed_at),
            all(_digest(item) for item in _snapshot_digests(value)),
            _sha1(value.root_head),
            _sha1(value.base_ref_sha),
            value.target_ref_sha is None or _sha1(value.target_ref_sha),
            value.target_registration_digest is None or _digest(value.target_registration_digest),
            target_shape,
            _count(value.root_content_count, 10_000),
            _count(value.root_content_bytes, 536_870_912),
            _count(value.registry_count, 2_500),
            _count(value.protected_count, 2_500),
            _count(value.refs_count, 2_500),
        )
    )


def _snapshot_digests(value: RepositorySnapshot) -> tuple[object, ...]:
    return (
        value.repository_binding_digest,
        value.root_symbolic_digest,
        value.root_status_digest,
        value.root_content_digest,
        value.registry_digest,
        value.protected_digest,
        value.refs_digest,
    )


def _valid_snapshot_target_shape(value: RepositorySnapshot) -> bool:
    if value.phase in {"s0", "s1"}:
        return value.target_ref_sha is None and value.target_registration_digest is None
    if value.phase in {"s2", "resume", "cleanup_pre"}:
        return value.target_ref_sha is not None and value.target_registration_digest is not None
    if value.phase == "cleanup_post":
        return value.target_ref_sha is not None and value.target_registration_digest is None
    return False


def _valid_removal(value: RemovalProof) -> bool:
    if type(value.pre_snapshot) is not RepositorySnapshot:
        return False
    if value.post_snapshot is not None and type(value.post_snapshot) is not RepositorySnapshot:
        return False
    complete = value.post_snapshot is not None or value.remove_delta_digest is not None
    complete_pair = (value.post_snapshot is None) == (value.remove_delta_digest is None)
    return all(
        (
            _digest(value.authority_digest),
            value.pre_snapshot.phase == "cleanup_pre",
            complete_pair,
            not complete or value.post_snapshot is not None and value.post_snapshot.phase == "cleanup_post",
            value.remove_delta_digest is None or _digest(value.remove_delta_digest),
            _sha1(value.retained_branch_sha),
        )
    )


def _valid_manifest(value: AidtWorktreeManifest) -> bool:
    state_shape = {
        "prepared": (None, None),
        "ready": (PostProof, None),
        "removing": (PostProof, RemovalProof),
        "removed": (PostProof, RemovalProof),
    }
    if type(value.state) is not str or value.state not in state_shape:
        return False
    post_type, removal_type = state_shape[value.state]
    post_matches = value.post_proof is None if post_type is None else type(value.post_proof) is post_type
    removal_matches = value.removal_proof is None if removal_type is None else type(value.removal_proof) is removal_type
    shapes = post_matches and removal_matches
    if value.state == "removing" and value.removal_proof is not None:
        shapes = shapes and value.removal_proof.post_snapshot is None
    if value.state == "removed" and value.removal_proof is not None:
        shapes = shapes and value.removal_proof.post_snapshot is not None
    return (
        shapes
        and type(value.route_scope) is RouteScope
        and type(value.pre_proof) is PreProof
        and _valid_manifest_scalars(value)
        and _valid_manifest_scope_equality(value)
    )


def _valid_manifest_scalars(value: AidtWorktreeManifest) -> bool:
    try:
        workspace_root = _canonical_absolute_path(Path(value.workspace_root))
        workspace_path = _canonical_absolute_path(Path(value.workspace_path))
        service_root = _canonical_absolute_path(Path(value.canonical_service_root))
    except (AidtWorktreeFailure, TypeError):
        return False
    return all(
        (
            value.schema == AIDT_WORKTREE_SCHEMA,
            _revision(value.manifest_revision),
            value.manifest_revision == _STATES.index(value.state) + 1,
            _valid_child_identifier(value.identifier),
            value.kind in {"backend", "frontend"},
            _service(value.service),
            all(_digest(item) for item in _manifest_digests(value)),
            _catalog_checkout(value.catalog_checkout),
            value.object_format == "sha1",
            value.base_ref == AIDT_WORKTREE_BASE_REF,
            _sha1(value.base_sha),
            Path(value.workspace_root) == workspace_root,
            Path(value.workspace_path) == workspace_path,
            Path(value.canonical_service_root) == service_root,
            workspace_path == workspace_root / value.identifier,
            service_root == Path(value.canonical_service_root),
            _timestamp_pair(value.created_at, value.updated_at),
        )
    )


def _manifest_digests(value: AidtWorktreeManifest) -> tuple[object, ...]:
    return (
        value.workflow_identity,
        value.board_identity,
        value.common_git_identity,
        value.route_pair_digest,
        value.repository_binding_digest,
        value.route_fingerprint,
        value.coordinator_fingerprint,
        value.source_revision,
        value.catalog_revision,
    )


def _valid_manifest_scope_equality(value: AidtWorktreeManifest) -> bool:
    scope = value.route_scope
    expected = (
        value.identifier,
        value.coordinator,
        value.service,
        value.kind,
        value.route_pair_digest,
        value.repository_binding_digest,
        value.route_fingerprint,
        value.coordinator_fingerprint,
        value.source_revision,
        value.catalog_revision,
        value.base_sha,
    )
    actual = (
        scope.identifier,
        scope.coordinator,
        scope.service,
        scope.kind,
        scope.route_pair_digest,
        scope.repository_binding_digest,
        scope.route_fingerprint,
        scope.coordinator_fingerprint,
        scope.source_revision,
        scope.catalog_revision,
        scope.checkout_revision,
    )
    return expected == actual and value.branch == derive_aidt_branch(scope.coordinator, scope.kind, scope.change_kind)


def _valid_ownership(value: OwnershipRecord) -> bool:
    try:
        root = _canonical_absolute_path(Path(value.workspace_root))
        workspace = _canonical_absolute_path(Path(value.workspace_path))
        manifest = _canonical_absolute_path(Path(value.manifest_path))
    except (AidtWorktreeFailure, TypeError):
        return False
    return all(
        (
            value.schema == AIDT_WORKTREE_OWNERSHIP_SCHEMA,
            _revision(value.record_revision),
            _valid_child_identifier(value.identifier),
            _service(value.service),
            Path(value.workspace_root) == root,
            Path(value.workspace_path) == workspace,
            Path(value.manifest_path) == manifest,
            workspace == root / value.identifier,
            manifest.name == f"{value.identifier}.json",
            _digest(value.route_pair_digest),
            _revision(value.manifest_revision),
            type(value.tombstone) is bool,
            _timestamp_pair(value.created_at, value.updated_at),
        )
    )


def _valid_attempt(value: AttemptRecord) -> bool:
    if type(value.disposition) is not str or type(value.mutation_phase) is not str:
        return False
    return all(
        (
            value.schema == AIDT_WORKTREE_ATTEMPT_SCHEMA,
            _revision(value.record_revision),
            _valid_child_identifier(value.identifier),
            _digest(value.route_pair_digest),
            _digest(value.workflow_generation),
            type(value.category) is str and value.category in _FAILURE_CATEGORIES,
            value.disposition in _DISPOSITIONS,
            _count(value.attempt, 3),
            value.retry_at is None
            or type(value.retry_at) is str and _valid_timestamp(value.retry_at),
            _bounded_retry_time(value),
            value.mutation_phase in _MUTATION_PHASES,
            value.manifest_revision is None or _revision(value.manifest_revision),
            _timestamp_pair(value.created_at, value.updated_at),
            _valid_attempt_state(value),
        )
    )


def _valid_attempt_state(value: AttemptRecord) -> bool:
    if value.disposition == "backoff":
        return _valid_backoff_attempt(value)
    if value.disposition == "manual":
        return _valid_manual_attempt(value)
    if value.disposition == "ready":
        return _valid_ready_attempt(value)
    return False


def _valid_backoff_attempt(value: AttemptRecord) -> bool:
    if value.category not in _ACTIVE_BACKOFF_CATEGORIES or value.retry_at is None:
        return False
    if value.attempt == 0:
        initial = value.category in _INITIAL_BACKOFF_CATEGORIES
        return initial and value.retry_at == value.updated_at and _phase_revision(value, "none")
    if not 1 <= value.attempt <= 3 or not _active_phase_revision(value):
        return False
    consumed = value.category in _INITIAL_BACKOFF_CATEGORIES or value.mutation_phase != "none"
    return not consumed or value.retry_at == value.updated_at


def _valid_manual_attempt(value: AttemptRecord) -> bool:
    excluded = _INITIAL_BACKOFF_CATEGORIES | frozenset({"ready"})
    if value.category in excluded or value.retry_at is not None:
        return False
    if not 1 <= value.attempt <= 3:
        return False
    if value.category == "attempt_exhausted" and value.attempt != 3:
        return False
    return _manual_phase_revision(value)


def _valid_ready_attempt(value: AttemptRecord) -> bool:
    ready_shape = _phase_revision(value, "added", 2) or _phase_revision(
        value, "removing", 3
    )
    return (
        value.category == "ready"
        and value.retry_at is None
        and 1 <= value.attempt <= 3
        and ready_shape
    )


def _active_phase_revision(value: AttemptRecord) -> bool:
    return any(
        (
            _phase_revision(value, "none"),
            _phase_revision(value, "prepared", 1),
            _phase_revision(value, "added", 1),
        )
    )


def _manual_phase_revision(value: AttemptRecord) -> bool:
    return any(
        (
            _active_phase_revision(value),
            _phase_revision(value, "added", 2),
            _phase_revision(value, "removing", 3),
        )
    )


def _phase_revision(
    value: AttemptRecord, phase: str, revision: int | None = None
) -> bool:
    return value.mutation_phase == phase and value.manifest_revision == revision


def _scope_from_dict(value: object) -> RouteScope:
    raw = _exact_dict(value, _SCOPE_KEYS)
    return RouteScope(**raw)


def _snapshot_from_dict(value: object) -> RepositorySnapshot:
    raw = _exact_dict(value, _SNAPSHOT_KEYS)
    return RepositorySnapshot(**raw)


def _pre_from_dict(value: object) -> PreProof:
    raw = _exact_dict(value, _PRE_KEYS)
    return PreProof(_snapshot_from_dict(raw["s0"]), _snapshot_from_dict(raw["s1"]), raw["fetch_delta_digest"])


def _post_from_dict(value: object) -> PostProof:
    raw = _exact_dict(value, _POST_KEYS)
    return PostProof(
        _snapshot_from_dict(raw["s2"]),
        raw["create_delta_digest"],
        raw["ticket_head"],
        raw["registration_digest"],
        raw["clean_at_create"],
        raw["no_upstream"],
    )


def _removal_from_dict(value: object) -> RemovalProof:
    raw = _exact_dict(value, _REMOVAL_KEYS)
    post = None if raw["post_snapshot"] is None else _snapshot_from_dict(raw["post_snapshot"])
    return RemovalProof(
        raw["authority_digest"],
        _snapshot_from_dict(raw["pre_snapshot"]),
        post,
        raw["remove_delta_digest"],
        raw["retained_branch_sha"],
    )


def _nullable_post(value: object) -> PostProof | None:
    return None if value is None else _post_from_dict(value)


def _nullable_removal(value: object) -> RemovalProof | None:
    return None if value is None else _removal_from_dict(value)


_Record = TypeVar("_Record")


def _read_optional_record(
    path: Path, reader: Callable[[Path], _Record]
) -> _Record | None:
    """Read one exact registry entry after a bounded collision scan."""
    if not path.name.endswith(".json") or not _valid_child_identifier(_record_stem(path.name)):
        raise AidtWorktreeFailure("registry_invalid")
    try:
        path.parent.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AidtWorktreeFailure("registry_invalid") from exc
    _require_directory(path.parent)
    key = _entry_collision_key(path.name)
    matches = _optional_collision_matches(path.parent, key)
    if len(matches) > 1 or matches and matches[0] != path.name:
        raise AidtWorktreeFailure("registry_collision")
    if not matches:
        if _lexists(path):
            raise AidtWorktreeFailure("registry_collision")
        return None
    return reader(path)


def _optional_collision_matches(directory: Path, key: str) -> tuple[str, ...]:
    try:
        entries = os.scandir(directory)
    except OSError as exc:
        raise AidtWorktreeFailure("registry_invalid") from exc
    matches: list[str] = []
    try:
        for count, entry in enumerate(entries, start=1):
            if count > MAX_REGISTRY_ENTRIES:
                raise AidtWorktreeFailure("registry_invalid")
            if _entry_collision_key(entry.name) == key:
                matches.append(entry.name)
    except OSError as exc:
        raise AidtWorktreeFailure("registry_invalid") from exc
    finally:
        entries.close()
    return tuple(matches)


def _entry_collision_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _cas_current(
    path: Path, expected: int | None, reader: Callable[[Path], _Record]
) -> _Record | None:
    exists = _lexists(path)
    if expected is None:
        if exists:
            raise AidtWorktreeFailure("cas_mismatch")
        return None
    if not _revision(expected) or not exists:
        raise AidtWorktreeFailure("cas_mismatch")
    current = reader(path)
    revision = (
        getattr(current, "record_revision")
        if hasattr(current, "record_revision")
        else getattr(current, "manifest_revision", None)
    )
    if revision != expected:
        raise AidtWorktreeFailure("cas_mismatch")
    return current


def _validate_manifest_transition(
    current: AidtWorktreeManifest, record: AidtWorktreeManifest
) -> None:
    index = _STATES.index(current.state)
    next_state = _STATES[index + 1] if index + 1 < len(_STATES) else None
    invariant_keys = _MANIFEST_KEYS - {
        "manifest_revision",
        "state",
        "post_proof",
        "removal_proof",
        "updated_at",
    }
    old, new = asdict(current), asdict(record)
    unchanged = all(old[key] == new[key] for key in invariant_keys)
    if next_state != record.state or record.manifest_revision != current.manifest_revision + 1 or not unchanged:
        raise AidtWorktreeFailure("manifest_invalid", record.identifier)
    if current.post_proof is not None and current.post_proof != record.post_proof:
        raise AidtWorktreeFailure("manifest_invalid", record.identifier)


def _validate_sidecar_revision(current: object | None, revision: int) -> None:
    expected = 1 if current is None else cast(int, getattr(current, "record_revision")) + 1
    if revision != expected:
        raise AidtWorktreeFailure("cas_mismatch")


def _atomic_replace(
    path: Path, data: bytes, *, require_absent: bool = False
) -> DurabilityResult:
    _require_directory(path.parent)
    if require_absent and _lexists(path):
        raise AidtWorktreeFailure("cas_mismatch")
    temporary = path.parent / f".{path.name}.{token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = -1
    try:
        fd = os.open(temporary, flags, 0o600)
        _write_all(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        return DurabilityResult(_fsync_directory(path.parent))
    except AidtWorktreeFailure:
        raise
    except OSError as exc:
        raise AidtWorktreeFailure("durability_failed") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if _lexists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _read_regular(path: Path) -> bytes:
    before = _regular_identity(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise AidtWorktreeFailure("manifest_invalid") from exc
    try:
        data = _read_bounded(fd)
        after_stat = os.fstat(fd)
    finally:
        os.close(fd)
    after = _identity_from_stat(after_stat)
    if before != after or before != _regular_identity(path):
        raise AidtWorktreeFailure("manifest_invalid")
    return data


def _regular_identity(path: Path) -> tuple[int, int, int, int, int]:
    try:
        value = path.lstat()
    except OSError as exc:
        raise AidtWorktreeFailure("manifest_invalid") from exc
    if not stat.S_ISREG(value.st_mode) or stat.S_IMODE(value.st_mode) != 0o600:
        raise AidtWorktreeFailure("manifest_invalid")
    if value.st_size > MAX_DURABLE_FILE_BYTES:
        raise AidtWorktreeFailure("manifest_too_large")
    return _identity_from_stat(value)


def _identity_from_stat(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_mode)


def _read_bounded(fd: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65_536, MAX_DURABLE_FILE_BYTES + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_DURABLE_FILE_BYTES:
            raise AidtWorktreeFailure("manifest_too_large")


def _write_all(fd: int, data: bytes) -> None:
    position = 0
    while position < len(data):
        written = os.write(fd, data[position:])
        if written <= 0:
            raise AidtWorktreeFailure("durability_failed")
        position += written


def _fsync_directory(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
        return "supported"
    except OSError as exc:
        unsupported = {errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}
        if exc.errno in unsupported:
            return "unsupported"
        raise
    finally:
        os.close(fd)


def _scan_records(
    directory: Path, reader: Callable[[Path], _Record]
) -> dict[str, _Record]:
    _require_directory(directory)
    entries = list(os.scandir(directory))
    if len(entries) > MAX_REGISTRY_ENTRIES:
        raise AidtWorktreeFailure("registry_invalid")
    records: dict[str, _Record] = {}
    folded: set[str] = set()
    for entry in entries:
        stem = _record_stem(entry.name)
        collision_key = unicodedata.normalize("NFC", stem).casefold()
        if collision_key in folded:
            raise AidtWorktreeFailure("registry_collision")
        folded.add(collision_key)
        if not entry.name.endswith(".json") or not _valid_child_identifier(stem):
            raise AidtWorktreeFailure("registry_invalid")
        record = reader(Path(entry.path))
        if getattr(record, "identifier", None) != stem:
            raise AidtWorktreeFailure("registry_invalid")
        records[stem] = record
    return records


def _record_stem(name: str) -> str:
    return name[:-5] if name.endswith(".json") else name


def _scan_locks(directory: Path) -> None:
    _require_directory(directory)
    entries = list(os.scandir(directory))
    if len(entries) > MAX_REGISTRY_ENTRIES:
        raise AidtWorktreeFailure("registry_invalid")
    for entry in entries:
        try:
            value = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise AidtWorktreeFailure("registry_invalid") from exc
        valid = (
            _LOCK_NAME.fullmatch(entry.name) is not None
            and stat.S_ISREG(value.st_mode)
            and stat.S_IMODE(value.st_mode) == 0o600
        )
        if not valid:
            raise AidtWorktreeFailure("registry_invalid")


def _ensure_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise AidtWorktreeFailure("durability_failed") from exc
    _require_directory(path)


def _require_directory(path: Path) -> None:
    try:
        value = path.lstat()
    except OSError as exc:
        raise AidtWorktreeFailure("registry_invalid") from exc
    if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise AidtWorktreeFailure("registry_invalid")


def _open_lock(path: Path) -> int:
    _require_directory(path.parent)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, 0o600)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            os.close(fd)
            raise AidtWorktreeFailure("capability_unsupported")
        return fd
    except OSError as exc:
        raise AidtWorktreeFailure("capability_unsupported") from exc


def _wait_for_lock(fd: int, timeout: float) -> None:
    if fcntl is None:
        raise AidtWorktreeFailure("capability_unsupported")
    lock_module = cast(Any, fcntl)
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_module.flock(fd, lock_module.LOCK_EX | lock_module.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise AidtWorktreeFailure("lock_timeout")
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _admit_matching_scope(record: AttemptRecord, now: datetime) -> AttemptAdmission:
    if record.disposition == "manual":
        return AttemptAdmission(False, "manual", record)
    if record.disposition == "ready":
        return AttemptAdmission(True, "resume", record)
    if now < _parse_utc(record.updated_at):
        return AttemptAdmission(False, "backoff", record)
    retry_at = _parse_utc(cast(str, record.retry_at))
    if now < retry_at:
        return AttemptAdmission(False, "backoff", record)
    if record.attempt >= 3:
        updated = _attempt_update(record, "attempt_exhausted", "manual", None, record.attempt, now)
        return AttemptAdmission(False, "manual", updated)
    updated = _attempt_update(
        record, record.category, "backoff", _format_utc(now), record.attempt + 1, now
    )
    return AttemptAdmission(True, "provision", updated)


def _valid_phase_transition(
    record: AttemptRecord, mutation_phase: str, manifest_revision: int
) -> bool:
    if mutation_phase == "prepared":
        source = record.mutation_phase == "none" and record.manifest_revision is None
        return source and manifest_revision == 1 and _active_provision_attempt(record)
    if mutation_phase == "added":
        source = record.mutation_phase == "prepared" and record.manifest_revision == 1
        return source and manifest_revision == 1 and _active_provision_attempt(record)
    if mutation_phase != "removing":
        return False
    source = (
        record.disposition == "ready"
        and record.category == "ready"
        and record.mutation_phase == "added"
        and record.manifest_revision == 2
        and record.retry_at is None
    )
    return source and 1 <= record.attempt <= 3 and manifest_revision == 3


def _valid_ready_source(record: AttemptRecord, manifest_revision: int) -> bool:
    source = (
        record.mutation_phase in {"prepared", "added"}
        and record.manifest_revision == 1
    )
    return source and manifest_revision == 2 and _active_provision_attempt(record)


def _active_provision_attempt(record: AttemptRecord) -> bool:
    return (
        record.disposition == "backoff"
        and record.category in _ACTIVE_BACKOFF_CATEGORIES
        and record.retry_at == record.updated_at
        and 1 <= record.attempt <= 3
    )


def _reset_attempt_scope(
    record: AttemptRecord,
    pair: str,
    generation: str,
    now: datetime,
    attested: bool,
) -> AttemptAdmission:
    if not attested:
        raise AidtWorktreeFailure("identity_invalid", record.identifier)
    now = _monotonic_attempt_time(record, now)
    updated = AttemptRecord(
        record.schema,
        record.record_revision + 1,
        record.identifier,
        pair,
        generation,
        "scope_changed",
        "backoff",
        0,
        _format_utc(now),
        "none",
        None,
        record.created_at,
        _format_utc(now),
    )
    return AttemptAdmission(False, "scope_reset", updated)


def _attempt_update(
    record: AttemptRecord,
    category: str,
    disposition: str,
    retry_at: str | None,
    attempt: int,
    now: datetime,
) -> AttemptRecord:
    now = _monotonic_attempt_time(record, now)
    return AttemptRecord(
        record.schema,
        record.record_revision + 1,
        record.identifier,
        record.route_pair_digest,
        record.workflow_generation,
        category,
        disposition,
        attempt,
        retry_at,
        record.mutation_phase,
        record.manifest_revision,
        record.created_at,
        _format_utc(now),
    )


def _exact_dict(value: object, keys: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise AidtWorktreeFailure("manifest_invalid")
    return cast(dict[str, Any], value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _validate_json_tree(value: object) -> None:
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is str:
        if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
            raise AidtWorktreeFailure("manifest_invalid")
        return
    if type(value) is list:
        for item in value:
            _validate_json_tree(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for key, item in value.items():
            _validate_json_tree(key)
            _validate_json_tree(item)
        return
    raise AidtWorktreeFailure("manifest_invalid")


def _digest(value: object) -> bool:
    return type(value) is str and _HEX_64.fullmatch(value) is not None


def _sha1(value: object) -> bool:
    return type(value) is str and len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def _service(value: object) -> bool:
    return type(value) is str and len(value.encode("ascii", "ignore")) <= 48 and _SERVICE_ID.fullmatch(value) is not None


def _catalog_checkout(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if not 1 <= len(encoded) <= 256:
        return False
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        return False
    return "/" not in value and "\\" not in value and value not in {".", ".."}


def _bounded_retry_time(value: AttemptRecord) -> bool:
    if value.retry_at is None:
        return True
    if type(value.retry_at) is not str:
        return False
    try:
        updated = _parse_utc(value.updated_at)
        retry_at = _parse_utc(value.retry_at)
    except AidtWorktreeFailure:
        return False
    return updated <= retry_at <= updated + timedelta(seconds=600)


def _count(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _timestamp_pair(created: object, updated: object) -> bool:
    if not (
        type(created) is str
        and type(updated) is str
        and _valid_timestamp(created)
        and _valid_timestamp(updated)
    ):
        return False
    return _parse_utc(created) <= _parse_utc(updated)


def _utc_second(value: datetime) -> datetime:
    valid = (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
        and value.microsecond == 0
    )
    if not valid:
        raise AidtWorktreeFailure("clock_invalid")
    return value.astimezone(timezone.utc)


def _monotonic_attempt_time(record: AttemptRecord, value: datetime) -> datetime:
    instant = _utc_second(value)
    if instant < _parse_utc(record.updated_at):
        raise AidtWorktreeFailure("clock_invalid", record.identifier)
    return instant


def _parse_utc(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise AidtWorktreeFailure("clock_invalid") from exc


def _format_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise AidtWorktreeFailure("manifest_invalid") from exc
    return True
