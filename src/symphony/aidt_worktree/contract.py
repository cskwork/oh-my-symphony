"""Closed, side-effect-free contracts for AIDT worktree ownership."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from ..workflow import ServiceConfig


AIDT_WORKTREE_SCHEMA = "aidt-worktree-v1"
AIDT_WORKTREE_ACTIVATION_SCHEMA = "aidt-worktree-activation-v1"
AIDT_WORKTREE_OWNERSHIP_SCHEMA = "aidt-worktree-ownership-v1"
AIDT_WORKTREE_ATTEMPT_SCHEMA = "aidt-worktree-attempt-v1"
AIDT_COMPLETION_AUTHORIZATION_SCHEMA = "aidt-completion-authorization-v1"
AIDT_WORKTREE_BASE_REF = "refs/remotes/origin/aidt-prd"
MAX_DURABLE_FILE_BYTES = 128 * 1024
MAX_REGISTRY_ENTRIES = 2_500
MAX_PATH_BYTES = 4_096
MAX_INT = 2_147_483_647

_CARD_KEY = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")
_SERVICE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(
    r"^(?:[0-9]{4})-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_ISSUE_TYPES = frozenset(
    {"bug", "story", "task", "sub-task", "improvement", "new feature"}
)
_ATTEMPT_KINDS = frozenset({"initial", "retry", "reacquired"})
_FAILURE_CATEGORIES = frozenset(
    {
        "attempt_backoff",
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
        "fetch_command_failed",
        "fetch_timeout",
        "identifier_invalid",
        "identity_invalid",
        "internal_error",
        "lock_timeout",
        "manifest_collision",
        "manifest_invalid",
        "manifest_too_large",
        "path_invalid",
        "persistence_failed",
        "profile_invalid",
        "protocol_invalid",
        "registry_collision",
        "registry_invalid",
        "ready",
        "scope_changed",
    }
)


class AidtWorktreeFailure(Exception):
    """Bounded failure whose message never includes filesystem or Git text."""

    def __init__(self, category: object, ref: object = None) -> None:
        safe = category if type(category) is str and category in _FAILURE_CATEGORIES else "internal_error"
        self.category = safe
        self.ref = _safe_ref(ref)
        super().__init__(safe)


@dataclass(frozen=True)
class StableMetadataPaths:
    workflow_identity: str
    root: Path
    activation: Path
    manifests: Path
    ownership_records: Path
    attempts: Path
    locks: Path


@dataclass(frozen=True)
class StableWorktreePaths(StableMetadataPaths):
    manifest: Path
    ownership: Path
    attempt: Path
    manifest_lock: Path


@dataclass(frozen=True)
class AidtWorktreeSettings:
    enabled: bool
    workflow_identity: str
    board_identity: str
    workflow_generation: str
    workflow_path: Path
    board_root: Path
    workspace_root: Path
    paths: StableMetadataPaths


@dataclass(frozen=True)
class AidtWorktreeResult:
    workspace_path: Path
    created_now: bool
    manifest_revision: int

    def __post_init__(self) -> None:
        try:
            canonical = _canonical_absolute_path(self.workspace_path)
        except AidtWorktreeFailure as exc:
            raise AidtWorktreeFailure("internal_error") from exc
        valid = (
            canonical == self.workspace_path
            and type(self.created_now) is bool
            and _revision(self.manifest_revision)
        )
        if not valid:
            raise AidtWorktreeFailure("internal_error")


class DelegateDisposition(str, Enum):
    UNMANAGED = "unmanaged"
    HANDLED = "handled"
    OWNED_PRESERVED = "owned_preserved"
    OWNED_ERROR = "owned_error"


_Value = TypeVar("_Value")


@dataclass(frozen=True)
class DelegateResult(Generic[_Value]):
    disposition: DelegateDisposition
    value: _Value | None = None
    category: str | None = None

    def __post_init__(self) -> None:
        if not _valid_delegate_shape(self.disposition, self.value, self.category):
            raise AidtWorktreeFailure("internal_error")

    @classmethod
    def unmanaged(cls) -> DelegateResult[_Value]:
        return cls(DelegateDisposition.UNMANAGED)

    @classmethod
    def handled(cls, value: _Value | None = None) -> DelegateResult[_Value]:
        return cls(DelegateDisposition.HANDLED, value=value)

    @classmethod
    def owned_preserved(cls, category: str) -> DelegateResult[_Value]:
        return cls(DelegateDisposition.OWNED_PRESERVED, category=category)

    @classmethod
    def owned_error(cls, category: str) -> DelegateResult[_Value]:
        return cls(DelegateDisposition.OWNED_ERROR, category=category)


@dataclass(frozen=True)
class CompletionAuthorization:
    schema: str
    identifier: str
    workflow_generation: str
    route_pair_digest: str
    ready_manifest_revision: int
    issue_id: str
    run_id: str
    attempt_kind: str
    owning_lease_token: str
    final_transition_identity: str
    issuer: str
    issued_at: str
    authorization_digest: str

    def __post_init__(self) -> None:
        if not _valid_authorization(self):
            raise AidtWorktreeFailure("authorization_invalid", self.identifier)


def load_aidt_worktree_settings(
    config: ServiceConfig,
) -> AidtWorktreeSettings | None:
    """Validate the closed safety profile without performing I/O."""
    raw = config.raw.get("aidt_worktree")
    if raw is None:
        return None
    if type(raw) is not dict or set(raw) != {"enabled"}:
        raise AidtWorktreeFailure("profile_invalid")
    if type(raw["enabled"]) is not bool:
        raise AidtWorktreeFailure("profile_invalid")
    if raw["enabled"] is False:
        return None
    _validate_enabled_profile(config)
    return _build_settings(config)


def change_kind_for_issue_type(issue_type: object) -> str:
    """Map the reviewed Jira type to the only allowed branch change kind."""
    if type(issue_type) is not str or _has_forbidden_text(issue_type):
        raise AidtWorktreeFailure("change_kind_invalid")
    normalized = issue_type.strip().casefold()
    if normalized not in _ISSUE_TYPES:
        raise AidtWorktreeFailure("change_kind_invalid")
    return "fix" if normalized == "bug" else "feat"


def derive_aidt_branch(coordinator: object, kind: object, change_kind: object) -> str:
    """Derive the sole branch spelling from trusted route fields."""
    key = _coordinator(coordinator)
    if (
        type(kind) is not str
        or type(change_kind) is not str
        or kind not in {"backend", "frontend"}
        or change_kind not in {"feat", "fix"}
    ):
        raise AidtWorktreeFailure("branch_invalid", key)
    prefix = f"csk-{change_kind}" if kind == "frontend" else str(change_kind)
    return f"{prefix}/{key}"


def validate_aidt_branch(
    branch: object, coordinator: object, kind: object, change_kind: object
) -> str:
    """Require byte equality with the independently derived branch."""
    expected = derive_aidt_branch(coordinator, kind, change_kind)
    try:
        encoded = branch.encode("ascii") if type(branch) is str else b""
    except UnicodeEncodeError:
        encoded = b""
    if type(branch) is not str or branch != expected or not encoded or len(encoded) > 256:
        raise AidtWorktreeFailure("branch_invalid", coordinator)
    return branch


def stable_worktree_paths(
    workflow_path: Path | str, identifier: object
) -> StableWorktreePaths:
    """Derive every stable metadata path from workflow identity and child ID."""
    workflow = _path_input(workflow_path)
    child = _child_identifier(identifier)
    metadata = stable_metadata_paths(workflow)
    lock_key = _identity_digest("aidt-manifest-lock-v1", child)
    return StableWorktreePaths(
        metadata.workflow_identity,
        metadata.root,
        metadata.activation,
        metadata.manifests,
        metadata.ownership_records,
        metadata.attempts,
        metadata.locks,
        metadata.manifests / f"{child}.json",
        metadata.ownership_records / f"{child}.json",
        metadata.attempts / f"{child}.json",
        metadata.locks / f"manifest-{lock_key}.lock",
    )


def stable_metadata_paths(workflow_path: Path | str) -> StableMetadataPaths:
    """Derive the workflow-relative ownership root without a child lookup."""
    workflow = _path_input(workflow_path)
    identity = _identity_digest("aidt-workflow-identity-v1", str(workflow))
    root = workflow.parent / ".symphony" / "aidt-worktrees-v1"
    return StableMetadataPaths(
        identity,
        root,
        root / "ACTIVATED.json",
        root / "manifests",
        root / "ownership",
        root / "attempts",
        root / "locks",
    )


def common_git_lock_path(paths: StableWorktreePaths, identity: object) -> Path:
    """Return the non-reversible common-Git lock path."""
    digest = _hex_digest(identity)
    key = _identity_digest("aidt-common-git-lock-v1", digest)
    return paths.locks / f"common-git-{key}.lock"


def contained_workspace_path(workspace_root: Path | str, identifier: object) -> Path:
    """Derive a contained worktree path; cards never supply a path."""
    root = _path_input(workspace_root)
    child = _child_identifier(identifier)
    path = root / child
    if path.parent != root:
        raise AidtWorktreeFailure("path_invalid", child)
    return path


def canonical_workflow_generation(profile: dict[str, object]) -> str:
    """Hash canonical validated safety-profile bytes."""
    try:
        encoded = json.dumps(
            profile,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AidtWorktreeFailure("profile_invalid") from exc
    return hashlib.sha256(b"aidt-workflow-generation-v1\0" + encoded).hexdigest()


def _validate_enabled_profile(config: ServiceConfig) -> None:
    try:
        from ..aidt_routing.contract import load_routing_settings

        routing = load_routing_settings(config)
    except Exception as exc:
        raise AidtWorktreeFailure("profile_invalid") from exc
    hooks = config.hooks
    hook_names = ("after_create", "before_run", "after_run", "before_remove", "after_done")
    invalid = (
        routing is None
        or config.tracker.kind != "file"
        or config.tracker.board_root is None
        or not config.tracker.board_root.is_absolute()
        or not config.workspace_root.is_absolute()
        or config.workspace_reuse_policy != "preserve"
        or any(getattr(hooks, name, None) is not None for name in hook_names)
        or getattr(config.agent, "auto_commit_on_done", None) is not False
        or getattr(config.agent, "auto_merge_on_done", None) is not False
    )
    if invalid:
        raise AidtWorktreeFailure("profile_invalid")


def _build_settings(config: ServiceConfig) -> AidtWorktreeSettings:
    workflow = _canonical_absolute_path(config.workflow_path)
    board_root = config.tracker.board_root
    if board_root is None:
        raise AidtWorktreeFailure("profile_invalid")
    board = _canonical_absolute_path(board_root)
    workspace = _canonical_absolute_path(config.workspace_root)
    paths = stable_metadata_paths(workflow)
    profile = {
        "enabled": True,
        "workflow_identity": paths.workflow_identity,
        "board_identity": _identity_digest("aidt-board-identity-v1", str(board)),
        "workspace_root": str(workspace),
        "workspace_reuse_policy": "preserve",
        "generic_hooks_absent": True,
        "auto_commit_on_done": False,
        "auto_merge_on_done": False,
    }
    return AidtWorktreeSettings(
        True,
        paths.workflow_identity,
        str(profile["board_identity"]),
        canonical_workflow_generation(profile),
        workflow,
        board,
        workspace,
        paths,
    )


def _valid_authorization(value: CompletionAuthorization) -> bool:
    scalars = (
        type(value.schema) is str and value.schema == AIDT_COMPLETION_AUTHORIZATION_SCHEMA,
        _valid_child_identifier(value.identifier),
        _valid_hex64(value.workflow_generation),
        _valid_hex64(value.route_pair_digest),
        _revision(value.ready_manifest_revision),
        type(value.issue_id) is str and value.issue_id == value.identifier,
        type(value.run_id) is str and _HEX_32.fullmatch(value.run_id) is not None,
        type(value.attempt_kind) is str and value.attempt_kind in _ATTEMPT_KINDS,
        type(value.owning_lease_token) is str and value.owning_lease_token == value.run_id,
        _valid_hex64(value.final_transition_identity),
        type(value.issuer) is str and value.issuer == "aidt-stage-controller-v1",
        type(value.issued_at) is str and _valid_timestamp(value.issued_at),
        _valid_hex64(value.authorization_digest),
    )
    return all(scalars)


def _valid_delegate_shape(
    disposition: object, value: object, category: object
) -> bool:
    if type(disposition) is not DelegateDisposition:
        return False
    if disposition is DelegateDisposition.UNMANAGED:
        return value is None and category is None
    if disposition is DelegateDisposition.HANDLED:
        return category is None
    return value is None and type(category) is str and category in _FAILURE_CATEGORIES


def _canonical_absolute_path(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or _has_forbidden_text(str(path)):
        raise AidtWorktreeFailure("path_invalid")
    canonical = path.resolve(strict=False)
    if len(str(canonical).encode("utf-8")) > MAX_PATH_BYTES:
        raise AidtWorktreeFailure("path_invalid")
    return canonical


def _path_input(value: object) -> Path:
    if type(value) is str:
        try:
            return _canonical_absolute_path(Path(value))
        except (TypeError, ValueError) as exc:
            raise AidtWorktreeFailure("path_invalid") from exc
    return _canonical_absolute_path(value)


def _coordinator(value: object) -> str:
    if type(value) is not str or len(value.encode("ascii", "ignore")) > 256:
        raise AidtWorktreeFailure("identifier_invalid")
    if _CARD_KEY.fullmatch(value) is None:
        raise AidtWorktreeFailure("identifier_invalid")
    return value


def _child_identifier(value: object) -> str:
    if not _valid_child_identifier(value):
        raise AidtWorktreeFailure("identifier_invalid")
    return str(value)


def _valid_child_identifier(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    if len(encoded) > 256:
        return False
    parts = value.split("--")
    return (
        len(parts) == 2
        and _CARD_KEY.fullmatch(parts[0]) is not None
        and len(parts[1].encode("ascii")) <= 48
        and _SERVICE_ID.fullmatch(parts[1]) is not None
    )


def _hex_digest(value: object) -> str:
    if not _valid_hex64(value):
        raise AidtWorktreeFailure("identity_invalid")
    return str(value)


def _valid_hex64(value: object) -> bool:
    return type(value) is str and _HEX_64.fullmatch(value) is not None


def _identity_digest(domain: str, value: str) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + value.encode("utf-8")).hexdigest()


def _valid_timestamp(value: str) -> bool:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        from datetime import datetime

        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _revision(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_INT


def _has_forbidden_text(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cs"} for character in value)


def _safe_ref(value: object) -> str | None:
    if _valid_child_identifier(value):
        return str(value)
    if type(value) is str and _SERVICE_ID.fullmatch(value) is not None:
        return value
    return None
