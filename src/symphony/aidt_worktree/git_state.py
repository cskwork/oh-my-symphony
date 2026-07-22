"""Bounded Git observation and mutation primitives for AIDT worktrees."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import IO, TYPE_CHECKING, Any, Literal
from urllib.parse import unquote_to_bytes, urlsplit

from .contract import AidtWorktreeFailure

if TYPE_CHECKING:
    from .manifest import RepositorySnapshot


GIT_STDOUT_CAP = 1_048_576
GIT_STDERR_CAP = 65_536
GIT_LOCAL_TIMEOUT_SECONDS = 10.0
GIT_FETCH_TIMEOUT_SECONDS = 30.0

GIT_GLOBAL_OPTIONS = (
    "--no-optional-locks",
    "--no-replace-objects",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "credential.helper=",
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.https.allow=always",
    "-c",
    "protocol.ssh.allow=always",
    "-c",
    "protocol.file.allow=never",
    "-c",
    "filter.lfs.process=",
    "-c",
    "filter.lfs.smudge=",
    "-c",
    "filter.lfs.required=false",
)
FETCH_ARGV = (
    "git",
    *GIT_GLOBAL_OPTIONS,
    "fetch",
    "--no-tags",
    "--no-recurse-submodules",
    "--no-write-fetch-head",
    "origin",
    "+refs/heads/aidt-prd:refs/remotes/origin/aidt-prd",
)

_VISIBLE_ASCII = re.compile(r"^[\x21-\x7e]+$")
_SERVICE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_BRANCH = re.compile(r"^(?:csk-)?(?:feat|fix)/A20-[1-9][0-9]*$")
_GIT_MODES = frozenset(
    {b"000000", b"040000", b"100644", b"100755", b"120000", b"160000"}
)
_ORDINARY_XY = frozenset(
    {
        b".M", b".T", b".D", b"M.", b"MM", b"MT", b"MD", b"T.",
        b"TM", b"TT", b"TD", b"A.", b"AM", b"AT", b"AD", b"D.",
    }
)
_UNMERGED_XY = frozenset({b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU"})


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    stdout_overflow: bool = False
    stderr_overflow: bool = False


BinaryRunner = Callable[
    [tuple[str, ...], Path, Mapping[str, str], float, int, int],
    GitCommandResult,
]
BindingObserver = Callable[[], object]


@dataclass(frozen=True, repr=False)
class RepositoryIdentity:
    service_ref: str
    service_root: Path
    git_directory: Path
    common_git_directory: Path
    object_directory: Path
    object_format: str
    origin_digest: str
    top_level_identity: str
    common_git_identity: str
    object_identity: str

    def __repr__(self) -> str:
        return (
            "RepositoryIdentity("
            f"service_ref={self.service_ref!r}, object_format={self.object_format!r})"
        )


@dataclass(frozen=True)
class StatusEntry:
    kind: str
    path: str
    original_path: str | None = None


@dataclass(frozen=True)
class RefRecord:
    name: str
    sha: str
    upstream: str | None


@dataclass(frozen=True)
class WorktreeRegistration:
    path: Path
    head: str
    branch: str | None
    detached: bool
    locked: bool
    prunable: bool


@dataclass(frozen=True, repr=False)
class RepositoryState:
    snapshot: RepositorySnapshot
    refs: tuple[RefRecord, ...]
    registrations: tuple[WorktreeRegistration, ...]
    target_branch: str
    target_path: Path
    target_upstream: str | None

    def __repr__(self) -> str:
        return (
            "RepositoryState("
            f"phase={self.snapshot.phase!r}, refs={len(self.refs)!r}, "
            f"registrations={len(self.registrations)!r})"
        )


@dataclass(frozen=True)
class FetchResult:
    base_sha: str
    repository_binding_digest: str


@dataclass(frozen=True)
class TicketWorktreeState:
    path: Path
    head: str
    branch: str
    status_digest: str
    clean: bool
    no_upstream: bool


@dataclass(frozen=True)
class PreparedRecoveryProof:
    state: RepositoryState
    ticket: TicketWorktreeState | None
    create_delta_digest: str | None

    def __post_init__(self) -> None:
        if not _valid_result_state_type(self.state):
            raise AidtWorktreeFailure("protocol_invalid")
        absent = (
            self.state.snapshot.phase == "s1"
            and _valid_absent_result_state(self.state)
            and self.ticket is None
            and self.create_delta_digest is None
        )
        exact = (
            self.state.snapshot.phase == "s2"
            and self.state.snapshot.target_ref_sha == self.state.snapshot.base_ref_sha
            and _valid_complete_result_state(self.state)
            and _valid_result_ticket(self.state, self.ticket, clean=True)
            and self.create_delta_digest == _result_delta_digest(
                "aidt-create-delta-v1", self.state
            )
        )
        valid = absent or exact
        if not valid:
            raise AidtWorktreeFailure("protocol_invalid")


@dataclass(frozen=True)
class ReadyRecoveryProof:
    state: RepositoryState
    ticket: TicketWorktreeState

    def __post_init__(self) -> None:
        valid = (
            _valid_result_state_type(self.state)
            and self.state.snapshot.phase in ("resume", "cleanup_pre")
            and _valid_complete_result_state(self.state)
            and _valid_result_ticket(
                self.state,
                self.ticket,
                clean=True if self.state.snapshot.phase == "cleanup_pre" else None,
            )
        )
        if not valid:
            raise AidtWorktreeFailure("protocol_invalid")


@dataclass(frozen=True)
class RemovedRecoveryProof:
    state: RepositoryState
    remove_delta_digest: str

    def __post_init__(self) -> None:
        valid = (
            _valid_result_state_type(self.state)
            and self.state.snapshot.phase == "cleanup_post"
            and _valid_removed_result_state(self.state)
            and self.remove_delta_digest == _result_delta_digest(
                "aidt-remove-delta-v1", self.state
            )
        )
        if not valid:
            raise AidtWorktreeFailure("protocol_invalid")


class TargetArtifactDisposition(str, Enum):
    ABSENT = "absent"
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"


@dataclass
class _Capture:
    cap: int
    data: bytearray = field(default_factory=bytearray)
    overflow: bool = False


@dataclass(frozen=True)
class _ContentItem:
    path: str
    kind: str
    digest: str
    size: int


@dataclass
class _ContentBudget:
    items: dict[str, _ContentItem] = field(default_factory=dict)
    directories: dict[str, tuple[int, int, int, int, int]] = field(
        default_factory=dict
    )
    total_bytes: int = 0

    def add(self, item: _ContentItem) -> None:
        if item.path in self.items:
            raise AidtWorktreeFailure("content_invalid")
        count = len(self.items) + 1
        total = self.total_bytes + item.size
        if count > 10_000 or total > 536_870_912:
            raise AidtWorktreeFailure("cap_exceeded")
        self.items[item.path] = item
        self.total_bytes = total


def git_environment() -> Mapping[str, str]:
    """Return the complete environment allowed to reach a Git child."""
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/usr/bin/false",
        "SSH_ASKPASS": "/usr/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    system_root = os.environ.get("SYSTEMROOT")
    if system_root is not None:
        environment["SYSTEMROOT"] = system_root
    return MappingProxyType(environment)


def default_binary_runner(
    argv: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
    timeout: float,
    stdout_cap: int,
    stderr_cap: int,
) -> GitCommandResult:
    """Run one binary without a shell and kill its process group on overflow."""
    process = _spawn_binary(argv, cwd, environment)
    stdout = _Capture(stdout_cap)
    stderr = _Capture(stderr_cap)
    threads = (
        _reader(process.stdout, stdout, process),
        _reader(process.stderr, stderr, process),
    )
    timed_out = _wait(process, timeout)
    for thread in threads:
        thread.join()
    return GitCommandResult(
        process.returncode,
        bytes(stdout.data),
        bytes(stderr.data),
        timed_out,
        stdout.overflow,
        stderr.overflow,
    )


def _spawn_binary(
    argv: tuple[str, ...], cwd: Path, environment: Mapping[str, str]
) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        raise AidtWorktreeFailure("protocol_invalid") from None


def _reader(
    stream: IO[Any] | None, capture: _Capture, process: subprocess.Popen[bytes]
) -> threading.Thread:
    if stream is None:
        raise AidtWorktreeFailure("internal_error")
    thread = threading.Thread(
        target=_capture_stream,
        args=(stream, capture, process),
        daemon=True,
    )
    thread.start()
    return thread


def _capture_stream(
    stream: IO[Any], capture: _Capture, process: subprocess.Popen[bytes]
) -> None:
    while True:
        remaining = capture.cap - len(capture.data)
        chunk = stream.read(min(65_536, max(remaining + 1, 1)))
        if not chunk:
            return
        if len(chunk) <= remaining:
            capture.data.extend(chunk)
            continue
        capture.data.extend(chunk[: max(remaining, 0)])
        capture.overflow = True
        _kill(process)
        return


def _wait(process: subprocess.Popen[bytes], timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
        return False
    except subprocess.TimeoutExpired:
        _kill(process)
        process.wait()
        return True


def _kill(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def observe_repository_identity(
    service_root: Path,
    service_id: str,
    *,
    runner: BinaryRunner | None = None,
) -> RepositoryIdentity:
    """Bind canonical top/common/object directories and the approved origin."""
    root = _canonical_directory(service_root)
    ref = _service_ref(service_id)
    execute = runner or default_binary_runner
    top = _read_git_path(root, ("rev-parse", "--show-toplevel"), execute)
    git_dir = _read_git_path(root, ("rev-parse", "--absolute-git-dir"), execute)
    common = _read_git_path(
        root, ("rev-parse", "--path-format=absolute", "--git-common-dir"), execute
    )
    objects = _read_git_path(
        root, ("rev-parse", "--path-format=absolute", "--git-path", "objects"), execute
    )
    if top != root or objects != common / "objects":
        raise AidtWorktreeFailure("identity_invalid", ref)
    object_format = _read_git_scalar(root, ("rev-parse", "--show-object-format"), execute)
    if object_format != "sha1":
        raise AidtWorktreeFailure("identity_invalid", ref)
    origin = _read_git_scalar(root, ("remote", "get-url", "--all", "origin"), execute)
    return _repository_identity(ref, root, git_dir, common, objects, object_format, origin)


def observe_repository_state(
    identity: RepositoryIdentity,
    phase: str,
    repository_binding_digest: str,
    branch: str,
    workspace_path: Path,
    observed_at: str,
    *,
    runner: BinaryRunner | None = None,
) -> RepositoryState:
    """Observe one bounded phase with stable dirty-content proof."""
    _validate_state_inputs(identity, repository_binding_digest, branch, workspace_path, observed_at)
    execute = runner or default_binary_runner
    current = observe_repository_identity(identity.service_root, identity.service_ref[8:], runner=execute)
    if current != identity:
        raise AidtWorktreeFailure("identity_invalid", identity.service_ref)
    registrations = parse_worktree_porcelain(
        _git_output(identity.service_root, ("worktree", "list", "--porcelain", "-z"), execute)
    )
    refs = parse_ref_listing(
        _git_output(
            identity.service_root,
            ("for-each-ref", "--format=%(refname)%09%(objectname)%09%(upstream)", "refs"),
            execute,
        )
    )
    return _build_repository_state(
        identity, phase, repository_binding_digest, branch, workspace_path,
        observed_at, refs, registrations, execute,
    )


def prove_prepared_recovery(
    identity: RepositoryIdentity,
    persisted_s1: RepositorySnapshot,
    current_binding_digest: str,
    branch: str,
    workspace_path: Path,
    observed_at: str,
    *,
    runner: BinaryRunner | None = None,
) -> PreparedRecoveryProof:
    """Prove either exact persisted S1 absence or the completed clean add."""
    _validate_recovery_snapshot(persisted_s1, "s1", target=False)
    _validate_recovery_inputs(
        identity, current_binding_digest, branch, workspace_path, observed_at
    )
    if current_binding_digest != persisted_s1.repository_binding_digest:
        raise AidtWorktreeFailure("binding_invalid")
    execute = runner or default_binary_runner
    refs, registrations = _observe_recovery_collections(identity, execute)
    ref, registration = _target_artifacts(refs, registrations, branch, workspace_path)
    path_witness = _path_shape(workspace_path)
    if ref is not None or registration is not None or path_witness is not None:
        return _prove_completed_prepared(
            identity, persisted_s1, current_binding_digest, branch,
            workspace_path, observed_at, refs, registrations, ref,
            registration, path_witness, execute,
        )
    state = _build_repository_state(
        identity, "s1", current_binding_digest, branch, workspace_path,
        observed_at, refs, registrations, execute,
    )
    _require_recovery_invariants(persisted_s1, state.snapshot)
    _require_collection_projection(persisted_s1, refs, registrations)
    _close_recovery_bracket(
        identity, refs, registrations, workspace_path, branch,
        path_witness, None, execute,
    )
    return PreparedRecoveryProof(state, None, None)


def _prove_completed_prepared(
    identity: RepositoryIdentity,
    persisted: RepositorySnapshot,
    binding: str,
    branch: str,
    path: Path,
    observed_at: str,
    refs: tuple[RefRecord, ...],
    registrations: tuple[WorktreeRegistration, ...],
    ref: RefRecord | None,
    registration: WorktreeRegistration | None,
    path_witness: int | None,
    runner: BinaryRunner,
) -> PreparedRecoveryProof:
    ticket = _require_current_target(
        ref, registration, path, branch, persisted.base_ref_sha, runner, clean=True
    )
    state = _build_repository_state(
        identity, "s2", binding, branch, path, observed_at, refs, registrations, runner
    )
    _require_recovery_invariants(persisted, state.snapshot)
    target_ref = f"refs/heads/{branch}"
    projected_refs = tuple(item for item in refs if item.name != target_ref)
    projected_regs = _without_target_registration(registrations, branch, path)
    _require_collection_projection(persisted, projected_refs, projected_regs)
    ticket = _close_recovery_bracket(
        identity, refs, registrations, path, branch, path_witness, ticket, runner
    )
    return PreparedRecoveryProof(
        state, ticket, _target_delta_digest("aidt-create-delta-v1", registration)
    )


def prove_ready_recovery(
    identity: RepositoryIdentity,
    persisted_s2: RepositorySnapshot,
    current_binding_digest: str,
    branch: str,
    workspace_path: Path,
    observed_at: str,
    *,
    phase: Literal["resume", "cleanup_pre"],
    runner: BinaryRunner | None = None,
) -> ReadyRecoveryProof:
    """Prove a complete target is the persisted creation or its descendant."""
    _validate_recovery_snapshot(persisted_s2, "s2", target=True)
    if type(phase) is not str or phase not in ("resume", "cleanup_pre"):
        raise AidtWorktreeFailure("protocol_invalid")
    _validate_recovery_inputs(
        identity, current_binding_digest, branch, workspace_path, observed_at
    )
    if current_binding_digest != persisted_s2.repository_binding_digest:
        raise AidtWorktreeFailure("binding_invalid")
    if persisted_s2.target_ref_sha != persisted_s2.base_ref_sha:
        raise AidtWorktreeFailure("base_invalid")
    persisted_target = _persisted_canonical_target(persisted_s2, branch, workspace_path)
    execute = runner or default_binary_runner
    refs, registrations = _observe_recovery_collections(identity, execute)
    ref, registration = _target_artifacts(refs, registrations, branch, workspace_path)
    path_witness = _path_shape(workspace_path)
    current_sha = ref.sha if ref is not None else persisted_target[0].sha
    ticket = _require_current_target(
        ref, registration, workspace_path, branch, current_sha, execute,
        clean=True if phase == "cleanup_pre" else None,
    )
    if not base_is_ancestor(
        workspace_path, persisted_s2.base_ref_sha, ticket.head, runner=execute
    ):
        raise AidtWorktreeFailure("base_invalid")
    state = _build_repository_state(
        identity, phase, current_binding_digest, branch, workspace_path,
        observed_at, refs, registrations, execute,
    )
    _require_recovery_invariants(persisted_s2, state.snapshot)
    _require_ready_projection(persisted_s2, state, persisted_target)
    ticket = _close_recovery_bracket(
        identity, refs, registrations, workspace_path, branch,
        path_witness, ticket, execute,
    )
    return ReadyRecoveryProof(state, ticket)


def prove_removed_recovery(
    identity: RepositoryIdentity,
    persisted_cleanup_pre: RepositorySnapshot,
    retained_branch_sha: str,
    current_binding_digest: str,
    branch: str,
    workspace_path: Path,
    observed_at: str,
    *,
    runner: BinaryRunner | None = None,
) -> RemovedRecoveryProof:
    """Prove physical removal while the authorized local branch remains."""
    _validate_recovery_snapshot(persisted_cleanup_pre, "cleanup_pre", target=True)
    _validate_recovery_inputs(
        identity, current_binding_digest, branch, workspace_path, observed_at
    )
    if type(retained_branch_sha) is not str or _SHA1.fullmatch(retained_branch_sha) is None:
        raise AidtWorktreeFailure("base_invalid")
    if retained_branch_sha != persisted_cleanup_pre.target_ref_sha:
        raise AidtWorktreeFailure("base_invalid")
    if current_binding_digest != persisted_cleanup_pre.repository_binding_digest:
        raise AidtWorktreeFailure("binding_invalid")
    target = _persisted_canonical_target(
        persisted_cleanup_pre, branch, workspace_path
    )
    execute = runner or default_binary_runner
    refs, registrations = _observe_recovery_collections(identity, execute)
    ref, registration = _target_artifacts(refs, registrations, branch, workspace_path)
    path_witness = _path_shape(workspace_path)
    if ref != target[0] or registration is not None or path_witness is not None:
        raise AidtWorktreeFailure("collision")
    state = _build_repository_state(
        identity, "cleanup_post", current_binding_digest, branch, workspace_path,
        observed_at, refs, registrations, execute,
    )
    _require_recovery_invariants(persisted_cleanup_pre, state.snapshot)
    _require_removed_projection(persisted_cleanup_pre, state, target[1])
    _close_recovery_bracket(
        identity, refs, registrations, workspace_path, branch,
        path_witness, None, execute,
    )
    digest = _target_delta_digest("aidt-remove-delta-v1", target[1])
    return RemovedRecoveryProof(state, digest)


def fetch_production_base(
    identity: RepositoryIdentity,
    expected_origin_digest: str,
    expected_revision: str,
    expected_binding_digest: str,
    observe_binding: BindingObserver,
    *,
    runner: BinaryRunner | None = None,
) -> FetchResult:
    """Request the sole production fetch and recompute repository binding."""
    _validate_expected_binding(expected_origin_digest, expected_revision, expected_binding_digest)
    execute = runner or default_binary_runner
    _preflight_repository(identity, execute)
    current_origin = _read_git_scalar(
        identity.service_root, ("remote", "get-url", "--all", "origin"), execute
    )
    if canonical_origin_digest(current_origin) != expected_origin_digest:
        raise AidtWorktreeFailure("binding_invalid", identity.service_ref)
    try:
        result = execute(
            FETCH_ARGV,
            identity.service_root,
            git_environment(),
            GIT_FETCH_TIMEOUT_SECONDS,
            GIT_STDOUT_CAP,
            GIT_STDERR_CAP,
        )
    except Exception:
        raise AidtWorktreeFailure("fetch_command_failed", identity.service_ref) from None
    _check_fetch_result(result, identity.service_ref)
    actual = _read_sha(
        identity.service_root,
        ("rev-parse", "--verify", "refs/remotes/origin/aidt-prd^{commit}"),
        execute,
    )
    if actual != expected_revision:
        raise AidtWorktreeFailure("base_invalid", identity.service_ref)
    verify_service_binding(expected_revision, expected_binding_digest, observe_binding)
    return FetchResult(actual, expected_binding_digest)


def verify_service_binding(
    expected_revision: str,
    expected_binding_digest: str,
    observe_binding: BindingObserver,
) -> None:
    """Recompute the shared routing observer and require exact equality."""
    _validate_expected_binding("0" * 64, expected_revision, expected_binding_digest)
    try:
        observed = observe_binding()
        revision = getattr(observed, "checkout_revision")
        digest = getattr(observed, "repository_binding_digest")
    except Exception:
        raise AidtWorktreeFailure("binding_invalid") from None
    if revision != expected_revision or digest != expected_binding_digest:
        raise AidtWorktreeFailure("binding_invalid")


def _validate_expected_binding(origin: object, revision: object, digest: object) -> None:
    valid = (
        type(origin) is str
        and re.fullmatch(r"[0-9a-f]{64}", origin) is not None
        and type(revision) is str
        and _SHA1.fullmatch(revision) is not None
        and type(digest) is str
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
    )
    if not valid:
        raise AidtWorktreeFailure("binding_invalid")


def _check_fetch_result(result: object, ref: str) -> None:
    if not _valid_result(result):
        raise AidtWorktreeFailure("protocol_invalid", ref)
    assert isinstance(result, GitCommandResult)
    if result.timed_out:
        raise AidtWorktreeFailure("fetch_timeout", ref)
    if result.stdout_overflow or result.stderr_overflow:
        raise AidtWorktreeFailure("cap_exceeded", ref)
    if len(result.stdout) > GIT_STDOUT_CAP or len(result.stderr) > GIT_STDERR_CAP:
        raise AidtWorktreeFailure("cap_exceeded", ref)
    if result.returncode != 0:
        raise AidtWorktreeFailure("fetch_command_failed", ref)


def add_worktree(
    identity: RepositoryIdentity,
    branch: str,
    workspace_path: Path,
    base_sha: str,
    *,
    runner: BinaryRunner | None = None,
) -> None:
    """Run the only allowed worktree creation command."""
    path = _validate_mutation_inputs(branch, workspace_path, base_sha, must_exist=False)
    execute = runner or default_binary_runner
    _preflight_repository(identity, execute)
    argv = (
        "git", *GIT_GLOBAL_OPTIONS, "worktree", "add", "--no-track", "-b",
        branch, str(path), base_sha,
    )
    _run_mutation(identity, argv, execute)


def remove_worktree(
    identity: RepositoryIdentity,
    workspace_path: Path,
    *,
    runner: BinaryRunner | None = None,
) -> None:
    """Run plain worktree removal without force, prune, or branch deletion."""
    path = _canonical_existing_worktree(workspace_path)
    execute = runner or default_binary_runner
    _preflight_repository(identity, execute)
    argv = ("git", *GIT_GLOBAL_OPTIONS, "worktree", "remove", str(path))
    _run_mutation(identity, argv, execute)


def _run_mutation(
    identity: RepositoryIdentity, argv: tuple[str, ...], runner: BinaryRunner
) -> None:
    try:
        result = runner(
            argv,
            identity.service_root,
            git_environment(),
            GIT_LOCAL_TIMEOUT_SECONDS,
            GIT_STDOUT_CAP,
            GIT_STDERR_CAP,
        )
    except Exception:
        raise AidtWorktreeFailure("protocol_invalid", identity.service_ref) from None
    _checked_output(result, GIT_STDOUT_CAP, allow_stderr=True)


def _validate_mutation_inputs(
    branch: object, workspace_path: object, base_sha: object, *, must_exist: bool
) -> Path:
    if type(branch) is not str or _BRANCH.fullmatch(branch) is None:
        raise AidtWorktreeFailure("branch_invalid")
    if type(base_sha) is not str or _SHA1.fullmatch(base_sha) is None:
        raise AidtWorktreeFailure("base_invalid")
    path = _validated_absolute_path(workspace_path)
    if must_exist:
        return _canonical_existing_worktree(path)
    if _lexists(path):
        raise AidtWorktreeFailure("collision")
    _canonical_directory(path.parent)
    return path


def _canonical_existing_worktree(path: object) -> Path:
    return _canonical_directory(_validated_absolute_path(path))


def _validated_absolute_path(path: object) -> Path:
    if not isinstance(path, Path):
        raise AidtWorktreeFailure("path_invalid")
    try:
        raw = str(path).encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise AidtWorktreeFailure("path_invalid") from None
    canonical = Path(os.path.abspath(path))
    if canonical != path or not 0 < len(raw) <= 4_096:
        raise AidtWorktreeFailure("path_invalid")
    if any(byte < 32 or byte == 127 for byte in raw):
        raise AidtWorktreeFailure("path_invalid")
    return path


def _preflight_repository(identity: RepositoryIdentity, runner: BinaryRunner) -> None:
    if type(identity) is not RepositoryIdentity:
        raise AidtWorktreeFailure("identity_invalid")
    _check_posix_capabilities()
    current = observe_repository_identity(
        identity.service_root, identity.service_ref[8:], runner=runner
    )
    if current != identity:
        raise AidtWorktreeFailure("identity_invalid", identity.service_ref)
    output = _git_output(
        identity.service_root,
        ("config", "--local", "--null", "--name-only", "--list"),
        runner,
    )
    _reject_unsafe_config(output)
    _reject_executable_hooks(identity.common_git_directory / "hooks")


def _check_posix_capabilities() -> None:
    try:
        false_mode = Path("/usr/bin/false").lstat().st_mode
        null_mode = Path("/dev/null").lstat().st_mode
        import fcntl  # noqa: F401
    except (ImportError, OSError):
        raise AidtWorktreeFailure("capability_unsupported") from None
    if not stat.S_ISREG(false_mode) or not os.access("/usr/bin/false", os.X_OK):
        raise AidtWorktreeFailure("capability_unsupported")
    if not stat.S_ISCHR(null_mode):
        raise AidtWorktreeFailure("capability_unsupported")


def _reject_unsafe_config(output: bytes) -> None:
    if not output:
        return
    if not output.endswith(b"\0"):
        raise AidtWorktreeFailure("protocol_invalid")
    keys = output[:-1].split(b"\0")
    if len(keys) > 4_096 or any(not key for key in keys):
        raise AidtWorktreeFailure("cap_exceeded")
    for raw in keys:
        key = _ascii_field(raw).casefold()
        unsafe = (
            key in {"core.hookspath", "core.sshcommand", "include.path"}
            or (key.startswith("includeif.") and key.endswith(".path"))
            or (key.startswith("filter.") and key.endswith((".process", ".smudge", ".clean")))
            or (key.startswith("remote.") and key.endswith(".uploadpack"))
        )
        if unsafe:
            raise AidtWorktreeFailure("protocol_invalid")


def _reject_executable_hooks(hooks: Path) -> None:
    opened = _open_hooks_directory(hooks)
    if opened is None:
        return
    descriptor, before = opened
    try:
        entries = _scan_hook_entries(descriptor)
        after = hooks.lstat()
    except AidtWorktreeFailure:
        raise
    except OSError:
        raise AidtWorktreeFailure("protocol_invalid") from None
    finally:
        os.close(descriptor)
    if _stat_key(after) != _stat_key(before):
        raise AidtWorktreeFailure("protocol_invalid")
    for name, mode in entries:
        if stat.S_ISLNK(mode):
            raise AidtWorktreeFailure("protocol_invalid")
        if not name.endswith(".sample") and stat.S_ISREG(mode) and mode & 0o111:
            raise AidtWorktreeFailure("protocol_invalid")


def _open_hooks_directory(hooks: Path) -> tuple[int, os.stat_result] | None:
    flags = _directory_open_flags()
    try:
        before = hooks.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise AidtWorktreeFailure("protocol_invalid") from None
    descriptor = -1
    try:
        descriptor = os.open(hooks, flags)
        opened = os.fstat(descriptor)
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise AidtWorktreeFailure("protocol_invalid") from None
    if not stat.S_ISDIR(before.st_mode) or _stat_key(opened) != _stat_key(before):
        os.close(descriptor)
        raise AidtWorktreeFailure("protocol_invalid")
    return descriptor, before


def _scan_hook_entries(descriptor: int) -> list[tuple[str, int]]:
    try:
        with os.scandir(descriptor) as stream:
            return _bounded_hook_entries(stream)
    except AidtWorktreeFailure:
        raise
    except OSError:
        raise AidtWorktreeFailure("protocol_invalid") from None


def _bounded_hook_entries(stream: Any) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    for entry in stream:
        if len(entries) >= 2_500:
            raise AidtWorktreeFailure("cap_exceeded")
        try:
            value = entry.stat(follow_symlinks=False)
        except OSError:
            raise AidtWorktreeFailure("protocol_invalid") from None
        entries.append((entry.name, value.st_mode))
    return entries


def observe_ticket_worktree(
    workspace_path: Path,
    branch: str,
    *,
    runner: BinaryRunner | None = None,
) -> TicketWorktreeState:
    """Verify exact ticket HEAD/branch/status/upstream without mutation."""
    path = _canonical_existing_worktree(workspace_path)
    if type(branch) is not str or _BRANCH.fullmatch(branch) is None:
        raise AidtWorktreeFailure("branch_invalid")
    execute = runner or default_binary_runner
    top = _read_git_path(path, ("rev-parse", "--show-toplevel"), execute)
    head = _read_sha(path, ("rev-parse", "--verify", "HEAD^{commit}"), execute)
    symbolic = _read_git_scalar(path, ("symbolic-ref", "-q", "HEAD"), execute)
    if top != path or symbolic != f"refs/heads/{branch}":
        raise AidtWorktreeFailure("identity_invalid")
    status = _git_output(
        path,
        ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored=matching"),
        execute,
    )
    parse_status_porcelain_v2(status)
    upstream = _git_output(
        path,
        ("for-each-ref", "--format=%(upstream)", f"refs/heads/{branch}"),
        execute,
        4_096,
    )
    no_upstream = _optional_scalar(upstream) is None
    return TicketWorktreeState(
        path, head, branch, _digest_bytes("aidt-ticket-status-v1", status), not status, no_upstream
    )


def base_is_ancestor(
    workspace_path: Path,
    base_sha: str,
    head_sha: str,
    *,
    runner: BinaryRunner | None = None,
) -> bool:
    """Prove frozen base ancestry without changing refs or checkout state."""
    path = _canonical_existing_worktree(workspace_path)
    if _SHA1.fullmatch(base_sha) is None or _SHA1.fullmatch(head_sha) is None:
        raise AidtWorktreeFailure("base_invalid")
    result = _run_git(
        path,
        ("merge-base", "--is-ancestor", base_sha, head_sha),
        runner or default_binary_runner,
        128,
    )
    if not _valid_result(result):
        raise AidtWorktreeFailure("protocol_invalid")
    assert isinstance(result, GitCommandResult)
    bounded = len(result.stdout) <= 128 and len(result.stderr) <= GIT_STDERR_CAP
    invalid = result.timed_out or result.stdout_overflow or result.stderr_overflow
    if not bounded or invalid or result.stdout or result.stderr:
        raise AidtWorktreeFailure("protocol_invalid")
    if result.returncode not in {0, 1}:
        raise AidtWorktreeFailure("protocol_invalid")
    return result.returncode == 0


def _optional_scalar(output: bytes) -> str | None:
    raw = output[:-1] if output.endswith(b"\n") else output
    if not raw:
        return None
    value = _decode_scalar(output)
    if not _valid_ref(value):
        raise AidtWorktreeFailure("protocol_invalid")
    return value


def classify_target_artifacts(
    identity: RepositoryIdentity,
    branch: str,
    workspace_path: Path,
    expected_sha: str,
    *,
    runner: BinaryRunner | None = None,
) -> TargetArtifactDisposition:
    """Classify prepared/removing recovery as absent, exact, or ambiguous."""
    _validate_target_query(branch, workspace_path, expected_sha)
    execute = runner or default_binary_runner
    refs = parse_ref_listing(
        _git_output(identity.service_root, ("for-each-ref", "--format=%(refname)%09%(objectname)%09%(upstream)", "refs"), execute)
    )
    registrations = parse_worktree_porcelain(
        _git_output(identity.service_root, ("worktree", "list", "--porcelain", "-z"), execute)
    )
    if _has_remote_target_ref(refs, branch):
        return TargetArtifactDisposition.AMBIGUOUS
    ref, registration = _target_artifacts(refs, registrations, branch, workspace_path)
    exists = _lexists(workspace_path)
    if ref is None and registration is None and not exists:
        return TargetArtifactDisposition.ABSENT
    exact = _exact_target_shape(ref, registration, workspace_path, branch, expected_sha, exists)
    return TargetArtifactDisposition.EXACT if exact else TargetArtifactDisposition.AMBIGUOUS


def _validate_target_query(branch: object, path: object, sha: object) -> None:
    if type(branch) is not str or _BRANCH.fullmatch(branch) is None:
        raise AidtWorktreeFailure("branch_invalid")
    if type(sha) is not str or _SHA1.fullmatch(sha) is None:
        raise AidtWorktreeFailure("base_invalid")
    _validated_absolute_path(path)


def _exact_target_shape(
    ref: RefRecord | None,
    registration: WorktreeRegistration | None,
    path: Path,
    branch: str,
    sha: str,
    exists: bool,
) -> bool:
    return (
        exists
        and ref is not None
        and ref.sha == sha
        and ref.upstream is None
        and registration is not None
        and registration.path == path
        and registration.branch == f"refs/heads/{branch}"
        and registration.head == sha
        and not registration.detached
        and not registration.locked
        and not registration.prunable
    )


def validate_fetch_delta(before: RepositoryState, after: RepositoryState) -> str:
    """Permit only fixed remote-tracking-ref movement from S0 to S1."""
    if before.snapshot.phase != "s0" or after.snapshot.phase != "s1":
        raise AidtWorktreeFailure("protocol_invalid")
    if not _same_root(before, after) or before.registrations != after.registrations:
        raise AidtWorktreeFailure("identity_invalid")
    fixed = "refs/remotes/origin/aidt-prd"
    if _without_ref(before.refs, fixed) != _without_ref(after.refs, fixed):
        raise AidtWorktreeFailure("identity_invalid")
    if before.snapshot.target_ref_sha is not None or after.snapshot.target_ref_sha is not None:
        raise AidtWorktreeFailure("collision")
    payload = {"before": before.snapshot.base_ref_sha, "after": after.snapshot.base_ref_sha}
    return _digest("aidt-fetch-delta-v1", payload)


def validate_create_delta(before: RepositoryState, after: RepositoryState) -> str:
    """Permit one exact local branch plus one exact registration from S1 to S2."""
    if before.snapshot.phase != "s1" or after.snapshot.phase != "s2" or not _same_root(before, after):
        raise AidtWorktreeFailure("identity_invalid")
    target_ref = f"refs/heads/{before.target_branch}"
    if before.snapshot.base_ref_sha != after.snapshot.base_ref_sha:
        raise AidtWorktreeFailure("base_invalid")
    if _without_ref(before.refs, target_ref) != _without_ref(after.refs, target_ref):
        raise AidtWorktreeFailure("identity_invalid")
    if _without_registration(before) != _without_registration(after):
        raise AidtWorktreeFailure("identity_invalid")
    created = _created_target(after, target_ref)
    if created is None or created.head != before.snapshot.base_ref_sha:
        raise AidtWorktreeFailure("collision")
    payload = {"branch": target_ref, "path": str(created.path), "sha": created.head}
    return _digest("aidt-create-delta-v1", payload)


def validate_remove_delta(before: RepositoryState, after: RepositoryState) -> str:
    """Permit only disappearance of the exact target registration."""
    if before.snapshot.phase != "cleanup_pre" or after.snapshot.phase != "cleanup_post":
        raise AidtWorktreeFailure("protocol_invalid")
    if not _same_root(before, after) or before.refs != after.refs:
        raise AidtWorktreeFailure("identity_invalid")
    removed = _created_target(before, f"refs/heads/{before.target_branch}")
    if removed is None or _without_registration(before) != after.registrations:
        raise AidtWorktreeFailure("identity_invalid")
    payload = {"branch": removed.branch, "path": str(removed.path), "sha": removed.head}
    return _digest("aidt-remove-delta-v1", payload)


def _same_root(before: RepositoryState, after: RepositoryState) -> bool:
    left = before.snapshot
    right = after.snapshot
    return (
        before.target_branch == after.target_branch
        and before.target_path == after.target_path
        and left.repository_binding_digest == right.repository_binding_digest
        and left.root_head == right.root_head
        and left.root_symbolic_digest == right.root_symbolic_digest
        and left.root_status_digest == right.root_status_digest
        and left.root_content_digest == right.root_content_digest
        and left.root_content_count == right.root_content_count
        and left.root_content_bytes == right.root_content_bytes
        and left.protected_digest == right.protected_digest
        and left.protected_count == right.protected_count
    )


def _without_ref(refs: tuple[RefRecord, ...], name: str) -> tuple[RefRecord, ...]:
    return tuple(item for item in refs if item.name != name)


def _without_registration(state: RepositoryState) -> tuple[WorktreeRegistration, ...]:
    name = f"refs/heads/{state.target_branch}"
    return tuple(item for item in state.registrations if item.path != state.target_path and item.branch != name)


def _created_target(state: RepositoryState, name: str) -> WorktreeRegistration | None:
    refs = [item for item in state.refs if item.name == name and item.upstream is None]
    registrations = [
        item for item in state.registrations
        if item.path == state.target_path
        and item.branch == name
        and not item.detached
        and not item.locked
        and not item.prunable
    ]
    if len(refs) != 1 or len(registrations) != 1 or refs[0].sha != registrations[0].head:
        return None
    return registrations[0]


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise AidtWorktreeFailure("path_invalid") from None
    return True


def _build_repository_state(
    identity: RepositoryIdentity,
    phase: str,
    binding_digest: str,
    branch: str,
    workspace_path: Path,
    observed_at: str,
    refs: tuple[RefRecord, ...],
    registrations: tuple[WorktreeRegistration, ...],
    runner: BinaryRunner,
) -> RepositoryState:
    from .manifest import RepositorySnapshot

    head = _read_sha(identity.service_root, ("rev-parse", "--verify", "HEAD^{commit}"), runner)
    symbolic = _read_symbolic_head(identity.service_root, runner)
    status_digest, content_digest, content_count, content_bytes = _root_proof(
        identity, registrations, runner
    )
    target_ref, registration = _target_artifacts(refs, registrations, branch, workspace_path)
    base = _required_ref(refs, "refs/remotes/origin/aidt-prd")
    snapshot = RepositorySnapshot(
        phase, observed_at, binding_digest, head, _digest("aidt-root-symbolic-v1", symbolic),
        status_digest, content_digest, content_count, content_bytes,
        _registry_digest(registrations), len(registrations),
        _protected_digest(registrations), len(_protected_registrations(registrations)),
        _refs_digest(refs), len(refs), base.sha,
        target_ref.sha if target_ref is not None else None,
        _registration_digest(registration) if registration is not None else None,
    )
    return RepositoryState(
        snapshot, refs, registrations, branch, workspace_path,
        target_ref.upstream if target_ref is not None else None,
    )


def _observe_recovery_collections(
    identity: RepositoryIdentity,
    runner: BinaryRunner,
) -> tuple[tuple[RefRecord, ...], tuple[WorktreeRegistration, ...]]:
    current = observe_repository_identity(
        identity.service_root, identity.service_ref[8:], runner=runner
    )
    if current != identity:
        raise AidtWorktreeFailure("identity_invalid", identity.service_ref)
    registrations = parse_worktree_porcelain(
        _git_output(
            identity.service_root, ("worktree", "list", "--porcelain", "-z"), runner
        )
    )
    refs = parse_ref_listing(
        _git_output(
            identity.service_root,
            ("for-each-ref", "--format=%(refname)%09%(objectname)%09%(upstream)", "refs"),
            runner,
        )
    )
    return refs, registrations


def _close_recovery_bracket(
    identity: RepositoryIdentity,
    refs: tuple[RefRecord, ...],
    registrations: tuple[WorktreeRegistration, ...],
    path: Path,
    branch: str,
    path_witness: int | None,
    ticket: TicketWorktreeState | None,
    runner: BinaryRunner,
) -> TicketWorktreeState | None:
    closing = _observe_recovery_collections(identity, runner)
    if _path_shape(path) != path_witness:
        raise AidtWorktreeFailure("collision")
    if closing != (refs, registrations):
        raise AidtWorktreeFailure("identity_invalid")
    if ticket is None:
        return None
    current = observe_ticket_worktree(path, branch, runner=runner)
    if _path_shape(path) != path_witness:
        raise AidtWorktreeFailure("collision")
    _require_same_ticket(ticket, current)
    return current


def _require_same_ticket(
    before: TicketWorktreeState, after: TicketWorktreeState
) -> None:
    identity = ("path", "head", "branch", "no_upstream")
    if any(getattr(before, key) != getattr(after, key) for key in identity):
        raise AidtWorktreeFailure("collision")
    if (before.status_digest, before.clean) != (after.status_digest, after.clean):
        raise AidtWorktreeFailure("content_invalid")


def _path_shape(path: Path) -> int | None:
    try:
        return stat.S_IFMT(path.lstat().st_mode)
    except FileNotFoundError:
        return None
    except OSError:
        raise AidtWorktreeFailure("path_invalid") from None


def _validate_recovery_snapshot(
    snapshot: object, phase: str, *, target: bool
) -> None:
    from .manifest import RepositorySnapshot

    if type(snapshot) is not RepositorySnapshot or snapshot.phase != phase:
        raise AidtWorktreeFailure("protocol_invalid")
    ref_present = snapshot.target_ref_sha is not None
    registration_present = snapshot.target_registration_digest is not None
    valid = (
        ref_present and registration_present
        if target
        else not ref_present and not registration_present
    )
    if not valid:
        raise AidtWorktreeFailure("protocol_invalid")


def _validate_recovery_inputs(
    identity: object,
    binding_digest: object,
    branch: object,
    workspace_path: object,
    observed_at: object,
) -> None:
    if type(identity) is not RepositoryIdentity:
        raise AidtWorktreeFailure("protocol_invalid")
    if type(branch) is not str or _BRANCH.fullmatch(branch) is None:
        raise AidtWorktreeFailure("branch_invalid")
    _validated_absolute_path(workspace_path)
    valid = (
        type(binding_digest) is str
        and re.fullmatch(r"[0-9a-f]{64}", binding_digest) is not None
        and type(observed_at) is str
        and _TIMESTAMP.fullmatch(observed_at) is not None
    )
    if not valid:
        raise AidtWorktreeFailure("protocol_invalid")


def _require_recovery_invariants(
    persisted: RepositorySnapshot, current: RepositorySnapshot
) -> None:
    if persisted.repository_binding_digest != current.repository_binding_digest:
        raise AidtWorktreeFailure("binding_invalid")
    if persisted.base_ref_sha != current.base_ref_sha:
        raise AidtWorktreeFailure("base_invalid")
    identity = ("root_head", "root_symbolic_digest", "protected_digest", "protected_count")
    if any(getattr(persisted, key) != getattr(current, key) for key in identity):
        raise AidtWorktreeFailure("identity_invalid")
    content = (
        "root_status_digest", "root_content_digest", "root_content_count",
        "root_content_bytes",
    )
    if any(getattr(persisted, key) != getattr(current, key) for key in content):
        raise AidtWorktreeFailure("content_invalid")


def _require_collection_projection(
    persisted: RepositorySnapshot,
    refs: tuple[RefRecord, ...],
    registrations: tuple[WorktreeRegistration, ...],
) -> None:
    same_refs = (
        persisted.refs_digest == _refs_digest(refs)
        and persisted.refs_count == len(refs)
    )
    same_registry = (
        persisted.registry_digest == _registry_digest(registrations)
        and persisted.registry_count == len(registrations)
    )
    if not same_refs or not same_registry:
        raise AidtWorktreeFailure("identity_invalid")


def _require_current_target(
    ref: RefRecord | None,
    registration: WorktreeRegistration | None,
    path: Path,
    branch: str,
    sha: str,
    runner: BinaryRunner,
    *,
    clean: bool | None,
) -> TicketWorktreeState:
    expected_ref, expected_registration = _canonical_target(branch, path, sha)
    if ref != expected_ref or registration != expected_registration or not _lexists(path):
        raise AidtWorktreeFailure("collision")
    ticket = observe_ticket_worktree(path, branch, runner=runner)
    if ticket.head != sha or not ticket.no_upstream:
        raise AidtWorktreeFailure("collision")
    if clean is True and not ticket.clean:
        raise AidtWorktreeFailure("content_invalid")
    return ticket


def _canonical_target(
    branch: str, path: Path, sha: str
) -> tuple[RefRecord, WorktreeRegistration]:
    name = f"refs/heads/{branch}"
    return (
        RefRecord(name, sha, None),
        WorktreeRegistration(path, sha, name, False, False, False),
    )


def _persisted_canonical_target(
    snapshot: RepositorySnapshot, branch: str, path: Path
) -> tuple[RefRecord, WorktreeRegistration]:
    if snapshot.target_ref_sha is None:
        raise AidtWorktreeFailure("protocol_invalid")
    target = _canonical_target(branch, path, snapshot.target_ref_sha)
    if snapshot.target_registration_digest != _registration_digest(target[1]):
        raise AidtWorktreeFailure("identity_invalid")
    return target


def _require_ready_projection(
    persisted: RepositorySnapshot,
    current: RepositoryState,
    persisted_target: tuple[RefRecord, WorktreeRegistration],
) -> None:
    name = persisted_target[0].name
    refs = tuple(item for item in current.refs if item.name != name) + (persisted_target[0],)
    registrations = _without_target_registration(
        current.registrations, current.target_branch, current.target_path
    ) + (persisted_target[1],)
    _require_collection_projection(persisted, refs, registrations)


def _require_removed_projection(
    persisted: RepositorySnapshot,
    current: RepositoryState,
    persisted_registration: WorktreeRegistration,
) -> None:
    registrations = current.registrations + (persisted_registration,)
    _require_collection_projection(persisted, current.refs, registrations)


def _without_target_registration(
    registrations: tuple[WorktreeRegistration, ...], branch: str, path: Path
) -> tuple[WorktreeRegistration, ...]:
    name = f"refs/heads/{branch}"
    return tuple(
        item for item in registrations if item.path != path and item.branch != name
    )


def _target_delta_digest(domain: str, registration: WorktreeRegistration | None) -> str:
    if registration is None or registration.branch is None:
        raise AidtWorktreeFailure("collision")
    payload = {
        "branch": registration.branch,
        "path": str(registration.path),
        "sha": registration.head,
    }
    return _digest(domain, payload)


def _valid_absent_result_state(state: RepositoryState) -> bool:
    name = f"refs/heads/{state.target_branch}"
    candidates = (
        any(item.name == name for item in state.refs)
        or any(
            item.path == state.target_path or item.branch == name
            for item in state.registrations
        )
    )
    return (
        state.snapshot.target_ref_sha is None
        and state.snapshot.target_registration_digest is None
        and state.target_upstream is None
        and not candidates
    )


def _valid_result_state_type(state: object) -> bool:
    if type(state) is not RepositoryState:
        return False
    try:
        return (
            _valid_result_snapshot(state.snapshot)
            and _valid_result_state_scalars(state)
            and _valid_result_collections(state)
            and _valid_result_snapshot_binding(state)
        )
    except Exception:
        return False


def _valid_result_snapshot(snapshot: object) -> bool:
    from .manifest import RepositorySnapshot

    if type(snapshot) is not RepositorySnapshot:
        return False
    try:
        fields = RepositorySnapshot.__dataclass_fields__
        rebuilt = RepositorySnapshot(**{
            name: getattr(snapshot, name) for name in fields
        })
    except Exception:
        return False
    return rebuilt == snapshot


def _valid_result_state_scalars(state: RepositoryState) -> bool:
    upstream = state.target_upstream
    return (
        type(state.target_branch) is str
        and _BRANCH.fullmatch(state.target_branch) is not None
        and isinstance(state.target_path, Path)
        and _validated_absolute_path(state.target_path) == state.target_path
        and (
            upstream is None
            or type(upstream) is str and _valid_ref(upstream)
        )
    )


def _valid_result_collections(state: RepositoryState) -> bool:
    refs = state.refs
    registrations = state.registrations
    if type(refs) is not tuple or type(registrations) is not tuple:
        return False
    valid = (
        len(refs) <= 2_500
        and len(registrations) <= 2_500
        and all(_valid_result_ref(item) for item in refs)
        and all(_valid_result_registration(item) for item in registrations)
    )
    return valid and _unique_result_collections(refs, registrations)


def _valid_result_ref(value: object) -> bool:
    return (
        type(value) is RefRecord
        and type(value.name) is str
        and _valid_ref(value.name)
        and type(value.sha) is str
        and _SHA1.fullmatch(value.sha) is not None
        and (
            value.upstream is None
            or type(value.upstream) is str and _valid_ref(value.upstream)
        )
    )


def _valid_result_registration(value: object) -> bool:
    if type(value) is not WorktreeRegistration or not isinstance(value.path, Path):
        return False
    branch = value.branch
    return (
        _validated_absolute_path(value.path) == value.path
        and type(value.head) is str
        and _SHA1.fullmatch(value.head) is not None
        and (branch is None or type(branch) is str and _valid_ref(branch))
        and type(value.detached) is bool
        and type(value.locked) is bool
        and type(value.prunable) is bool
        and (branch is None) == value.detached
    )


def _unique_result_collections(
    refs: tuple[RefRecord, ...],
    registrations: tuple[WorktreeRegistration, ...],
) -> bool:
    names = [item.name for item in refs]
    paths = [item.path for item in registrations]
    branches = [item.branch for item in registrations if item.branch is not None]
    return (
        len(names) == len(set(names))
        and len(paths) == len(set(paths))
        and len(branches) == len(set(branches))
    )


def _valid_result_snapshot_binding(state: RepositoryState) -> bool:
    snapshot = state.snapshot
    protected = _protected_registrations(state.registrations)
    target = _target_artifacts(
        state.refs, state.registrations, state.target_branch, state.target_path
    )
    base = _required_ref(state.refs, "refs/remotes/origin/aidt-prd")
    ref, registration = target
    return all(
        (
            snapshot.refs_digest == _refs_digest(state.refs),
            snapshot.refs_count == len(state.refs),
            snapshot.registry_digest == _registry_digest(state.registrations),
            snapshot.registry_count == len(state.registrations),
            snapshot.protected_digest == _protected_digest(state.registrations),
            snapshot.protected_count == len(protected),
            snapshot.base_ref_sha == base.sha,
            snapshot.target_ref_sha == (ref.sha if ref is not None else None),
            snapshot.target_registration_digest
            == (_registration_digest(registration) if registration is not None else None),
            state.target_upstream == (ref.upstream if ref is not None else None),
        )
    )


def _valid_complete_result_state(state: RepositoryState) -> bool:
    created = _created_target(state, f"refs/heads/{state.target_branch}")
    return (
        created is not None
        and created.head == state.snapshot.target_ref_sha
        and _registration_digest(created) == state.snapshot.target_registration_digest
        and state.target_upstream is None
    )


def _valid_removed_result_state(state: RepositoryState) -> bool:
    name = f"refs/heads/{state.target_branch}"
    refs = [item for item in state.refs if item.name == name]
    candidates = [
        item for item in state.registrations
        if item.path == state.target_path or item.branch == name
    ]
    return (
        len(refs) == 1
        and refs[0].sha == state.snapshot.target_ref_sha
        and refs[0].upstream is None
        and state.snapshot.target_registration_digest is None
        and state.target_upstream is None
        and not candidates
    )


def _valid_result_ticket(
    state: RepositoryState, ticket: object, *, clean: bool | None
) -> bool:
    empty = _digest_bytes("aidt-ticket-status-v1", b"")
    return (
        type(ticket) is TicketWorktreeState
        and isinstance(ticket.path, Path)
        and ticket.path == state.target_path
        and _validated_absolute_path(ticket.path) == ticket.path
        and type(ticket.branch) is str
        and _BRANCH.fullmatch(ticket.branch) is not None
        and ticket.branch == state.target_branch
        and type(ticket.head) is str
        and _SHA1.fullmatch(ticket.head) is not None
        and ticket.head == state.snapshot.target_ref_sha
        and type(ticket.status_digest) is str
        and re.fullmatch(r"[0-9a-f]{64}", ticket.status_digest) is not None
        and ticket.no_upstream is True
        and type(ticket.clean) is bool
        and type(ticket.no_upstream) is bool
        and ticket.clean == (ticket.status_digest == empty)
        and (clean is None or ticket.clean is clean)
    )


def _result_delta_digest(domain: str, state: RepositoryState) -> str | None:
    name = f"refs/heads/{state.target_branch}"
    if state.snapshot.target_ref_sha is None:
        return None
    registration = WorktreeRegistration(
        state.target_path, state.snapshot.target_ref_sha, name, False, False, False
    )
    return _target_delta_digest(domain, registration)


def _validate_state_inputs(
    identity: object,
    binding_digest: object,
    branch: object,
    workspace_path: object,
    observed_at: object,
) -> None:
    valid = (
        type(identity) is RepositoryIdentity
        and type(binding_digest) is str
        and re.fullmatch(r"[0-9a-f]{64}", binding_digest) is not None
        and type(branch) is str
        and _BRANCH.fullmatch(branch) is not None
        and isinstance(workspace_path, Path)
        and workspace_path.is_absolute()
        and Path(os.path.abspath(workspace_path)) == workspace_path
        and type(observed_at) is str
        and _TIMESTAMP.fullmatch(observed_at) is not None
    )
    if not valid:
        raise AidtWorktreeFailure("protocol_invalid")


def _read_sha(root: Path, command: tuple[str, ...], runner: BinaryRunner) -> str:
    value = _read_git_scalar(root, command, runner)
    if _SHA1.fullmatch(value) is None:
        raise AidtWorktreeFailure("protocol_invalid")
    return value


def _read_symbolic_head(root: Path, runner: BinaryRunner) -> str | None:
    result = _run_git(root, ("symbolic-ref", "-q", "HEAD"), runner, 4_096)
    if not _valid_result(result):
        raise AidtWorktreeFailure("protocol_invalid")
    assert isinstance(result, GitCommandResult)
    if result.returncode == 1 and not result.stdout and not result.stderr:
        return None
    output = _checked_output(result, 4_096, allow_stderr=False)
    value = _decode_scalar(output)
    if not _valid_ref(value):
        raise AidtWorktreeFailure("protocol_invalid")
    return value


def _root_proof(
    identity: RepositoryIdentity,
    registrations: tuple[WorktreeRegistration, ...],
    runner: BinaryRunner,
) -> tuple[str, str, int, int]:
    command = ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored=matching")
    first = _git_output(identity.service_root, command, runner)
    entries = parse_status_porcelain_v2(first)
    index_before = _hash_index(identity.git_directory / "index")
    content_digest, count, total, directories = _content_proof(
        identity.service_root, entries, registrations, index_before, first
    )
    index_after = _hash_index(identity.git_directory / "index")
    second = _git_output(identity.service_root, command, runner)
    if first != second or index_before != index_after:
        raise AidtWorktreeFailure("content_invalid", identity.service_ref)
    _validate_directory_witnesses(identity.service_root, directories)
    return _digest_bytes("aidt-root-status-v1", first), content_digest, count, total


def _content_proof(
    root: Path,
    entries: tuple[StatusEntry, ...],
    registrations: tuple[WorktreeRegistration, ...],
    index_digest: str,
    status: bytes,
) -> tuple[str, int, int, tuple[tuple[str, tuple[int, int, int, int, int]], ...]]:
    budget = _ContentBudget()
    forbidden = _registered_relative_paths(root, registrations)
    ignored_directories = _ignored_directory_paths(status)
    for entry in sorted(entries, key=lambda item: item.path.encode("utf-8")):
        _reject_registered_content(entry.path, forbidden)
        if entry.kind == "ignored" and entry.path in ignored_directories:
            _walk_ignored(root, entry.path, forbidden, budget)
        else:
            budget.add(_hash_content_node(root, entry.path, entry.kind))
    payload = {
        "index_digest": index_digest,
        "items": [item.__dict__ for item in budget.items.values()],
    }
    directories = tuple(sorted(budget.directories.items()))
    return (
        _digest("aidt-root-content-v1", payload),
        len(budget.items),
        budget.total_bytes,
        directories,
    )


def _ignored_directory_paths(status: bytes) -> frozenset[str]:
    return frozenset(
        _status_path(record[2:])
        for record in _nul_records(status, "content_invalid")
        if record.startswith(b"! ") and record[2:].endswith(b"/")
    )


def _registered_relative_paths(
    root: Path, registrations: tuple[WorktreeRegistration, ...]
) -> tuple[str, ...]:
    values: list[str] = []
    for registration in registrations:
        try:
            relative = registration.path.relative_to(root)
        except ValueError:
            continue
        text = relative.as_posix()
        if text != ".":
            values.append(text)
    return tuple(sorted(values))


def _reject_registered_content(path: str, forbidden: tuple[str, ...]) -> None:
    if any(path == item or path.startswith(item + "/") for item in forbidden):
        raise AidtWorktreeFailure("content_invalid")


def _walk_ignored(
    root: Path,
    relative: str,
    forbidden: tuple[str, ...],
    budget: _ContentBudget,
) -> None:
    directory = root / relative
    descriptor, before = _open_ignored_directory(root, relative)
    try:
        budget.add(
            _ContentItem(
                relative, "directory", _digest_bytes("aidt-directory-v1", b""), 0
            )
        )
        entries = _scan_ignored_directory(
            descriptor, 10_000 - len(budget.items)
        )
        for name, is_directory in entries:
            child = _status_path(f"{relative}/{name}".encode("utf-8"))
            _reject_registered_content(child, forbidden)
            if is_directory:
                _walk_ignored(root, child, forbidden, budget)
            else:
                budget.add(_hash_content_node(root, child, "ignored"))
        after = directory.lstat()
    except AidtWorktreeFailure:
        raise
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None
    finally:
        os.close(descriptor)
    if _stat_key(after) != _stat_key(before):
        raise AidtWorktreeFailure("content_invalid")
    budget.directories[relative] = _stat_key(before)


def _validate_directory_witnesses(
    root: Path,
    witnesses: tuple[tuple[str, tuple[int, int, int, int, int]], ...],
) -> None:
    try:
        for relative, expected in witnesses:
            _validate_parent_chain(root, relative)
            current = (root / relative).lstat()
            if not stat.S_ISDIR(current.st_mode) or _stat_key(current) != expected:
                raise AidtWorktreeFailure("content_invalid")
    except AidtWorktreeFailure:
        raise
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None


def _open_ignored_directory(
    root: Path, relative: str
) -> tuple[int, os.stat_result]:
    _validate_parent_chain(root, relative)
    directory = root / relative
    flags = _directory_open_flags()
    try:
        before = directory.lstat()
        descriptor = os.open(directory, flags)
        opened = os.fstat(descriptor)
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None
    if not stat.S_ISDIR(before.st_mode) or _stat_key(opened) != _stat_key(before):
        os.close(descriptor)
        raise AidtWorktreeFailure("content_invalid")
    return descriptor, before


def _directory_open_flags() -> int:
    directory = getattr(os, "O_DIRECTORY", None)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if directory is None or no_follow is None:
        raise AidtWorktreeFailure("capability_unsupported")
    return os.O_RDONLY | directory | no_follow


def _scan_ignored_directory(
    descriptor: int, remaining: int
) -> list[tuple[str, bool]]:
    entries: list[tuple[str, bool]] = []
    try:
        with os.scandir(descriptor) as stream:
            for entry in stream:
                if len(entries) >= remaining:
                    raise AidtWorktreeFailure("cap_exceeded")
                entries.append(
                    (entry.name, entry.is_dir(follow_symlinks=False))
                )
    except AidtWorktreeFailure:
        raise
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None
    return sorted(entries, key=lambda item: os.fsencode(item[0]))


def _hash_content_node(root: Path, relative: str, source_kind: str) -> _ContentItem:
    path = root / relative
    _validate_parent_chain(root, relative)
    try:
        before = path.lstat()
    except FileNotFoundError:
        if source_kind in {"tracked", "renamed", "unmerged"}:
            return _ContentItem(relative, "missing", _digest_bytes("aidt-missing-v1", b""), 0)
        raise AidtWorktreeFailure("content_invalid") from None
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None
    if stat.S_ISREG(before.st_mode):
        return _hash_regular(path, relative, before)
    if stat.S_ISLNK(before.st_mode):
        return _hash_symlink(path, relative, before)
    if stat.S_ISDIR(before.st_mode):
        return _ContentItem(relative, "directory", _digest_bytes("aidt-directory-v1", b""), 0)
    raise AidtWorktreeFailure("content_invalid")


def _hash_regular(path: Path, relative: str, before: os.stat_result) -> _ContentItem:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256(b"aidt-regular-v1\0")
    total = 0
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _stat_key(opened) != _stat_key(before) or not stat.S_ISREG(opened.st_mode):
                raise AidtWorktreeFailure("content_invalid")
            while chunk := stream.read(65_536):
                total += len(chunk)
                if total > 536_870_912:
                    raise AidtWorktreeFailure("cap_exceeded")
                digest.update(chunk)
        after = path.lstat()
    except AidtWorktreeFailure:
        raise
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None
    if _stat_key(after) != _stat_key(before):
        raise AidtWorktreeFailure("content_invalid")
    return _ContentItem(relative, "regular", digest.hexdigest(), total)


def _hash_symlink(path: Path, relative: str, before: os.stat_result) -> _ContentItem:
    try:
        target = os.readlink(path).encode("utf-8", errors="strict")
        after = path.lstat()
    except (OSError, UnicodeEncodeError):
        raise AidtWorktreeFailure("content_invalid") from None
    if _stat_key(after) != _stat_key(before) or len(target) > 4_096:
        raise AidtWorktreeFailure("content_invalid")
    return _ContentItem(relative, "symlink", _digest_bytes("aidt-symlink-v1", target), len(target))


def _validate_parent_chain(root: Path, relative: str) -> None:
    current = root
    try:
        for part in relative.split("/")[:-1]:
            current /= part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise AidtWorktreeFailure("content_invalid")
    except AidtWorktreeFailure:
        raise
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None


def _is_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None


def _hash_index(path: Path) -> str:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return _digest_bytes("aidt-index-missing-v1", b"")
    except OSError:
        raise AidtWorktreeFailure("content_invalid") from None
    item = _hash_regular(path, "index", before)
    return _digest("aidt-index-v1", {"digest": item.digest, "size": item.size})


def _stat_key(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns


def _target_artifacts(
    refs: tuple[RefRecord, ...],
    registrations: tuple[WorktreeRegistration, ...],
    branch: str,
    path: Path,
) -> tuple[RefRecord | None, WorktreeRegistration | None]:
    if _has_remote_target_ref(refs, branch):
        raise AidtWorktreeFailure("collision")
    name = f"refs/heads/{branch}"
    ref = next((item for item in refs if item.name == name), None)
    candidates = [item for item in registrations if item.path == path or item.branch == name]
    if len(candidates) > 1:
        raise AidtWorktreeFailure("collision")
    registration = candidates[0] if candidates else None
    if registration is not None and (registration.path != path or registration.branch != name):
        raise AidtWorktreeFailure("collision")
    return ref, registration


def _has_remote_target_ref(refs: tuple[RefRecord, ...], branch: str) -> bool:
    prefix = "refs/remotes/"
    expected_suffix = f"/{branch}"
    return any(
        item.name.startswith(prefix)
        and len(item.name) > len(prefix) + len(expected_suffix)
        and item.name.endswith(expected_suffix)
        for item in refs
    )


def _required_ref(refs: tuple[RefRecord, ...], name: str) -> RefRecord:
    values = [item for item in refs if item.name == name]
    if len(values) != 1:
        raise AidtWorktreeFailure("base_invalid")
    return values[0]


def _protected_registrations(
    registrations: tuple[WorktreeRegistration, ...]
) -> tuple[WorktreeRegistration, ...]:
    return tuple(item for item in registrations if item.branch is not None and _protected_ref(item.branch))


def _protected_ref(ref: str) -> bool:
    branch = ref.removeprefix("refs/heads/")
    if ref.startswith("refs/remotes/"):
        parts = ref.split("/", 3)
        branch = parts[3] if len(parts) == 4 else ""
    return branch in {"aidt-dev", "aidt-stg", "aidt-prd"} or branch.startswith(("release/", "merge/"))


def _refs_digest(refs: tuple[RefRecord, ...]) -> str:
    payload = [item.__dict__ for item in sorted(refs, key=lambda value: value.name)]
    return _digest("aidt-refs-v1", payload)


def _registry_digest(registrations: tuple[WorktreeRegistration, ...]) -> str:
    payload = [_registration_payload(item) for item in sorted(registrations, key=lambda value: str(value.path))]
    return _digest("aidt-worktree-registry-v1", payload)


def _protected_digest(registrations: tuple[WorktreeRegistration, ...]) -> str:
    payload = [_registration_payload(item) for item in _protected_registrations(registrations)]
    return _digest("aidt-protected-occupancy-v1", payload)


def _registration_digest(registration: WorktreeRegistration) -> str:
    return _digest("aidt-target-registration-v1", _registration_payload(registration))


def _registration_payload(item: WorktreeRegistration) -> dict[str, object]:
    return {
        "path": str(item.path), "head": item.head, "branch": item.branch,
        "detached": item.detached, "locked": item.locked, "prunable": item.prunable,
    }


def _digest(domain: str, value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _digest_bytes(domain, payload)


def _digest_bytes(domain: str, value: bytes) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + value).hexdigest()


def _repository_identity(
    ref: str,
    root: Path,
    git_dir: Path,
    common: Path,
    objects: Path,
    object_format: str,
    origin: str,
) -> RepositoryIdentity:
    top_identity = _path_identity("aidt-top-level-v1", root)
    common_identity = _path_identity("aidt-common-git-v1", common)
    object_identity = _path_identity("aidt-object-directory-v1", objects)
    return RepositoryIdentity(
        ref,
        root,
        git_dir,
        common,
        objects,
        object_format,
        canonical_origin_digest(origin),
        top_identity,
        common_identity,
        object_identity,
    )


def _read_git_path(
    root: Path, command: tuple[str, ...], runner: BinaryRunner
) -> Path:
    value = _read_git_scalar(root, command, runner)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return _canonical_directory(path)


def _read_git_scalar(
    root: Path, command: tuple[str, ...], runner: BinaryRunner
) -> str:
    output = _git_output(root, command, runner, 4_096)
    return _decode_scalar(output)


def _decode_scalar(output: bytes) -> str:
    raw = output[:-1] if output.endswith(b"\n") else output
    if not raw or b"\n" in raw or b"\r" in raw or b"\0" in raw:
        raise AidtWorktreeFailure("protocol_invalid")
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        raise AidtWorktreeFailure("protocol_invalid") from None
    if not _bounded_visible_ascii(value, 4_096):
        raise AidtWorktreeFailure("protocol_invalid")
    return value


def _git_output(
    root: Path,
    command: tuple[str, ...],
    runner: BinaryRunner,
    stdout_cap: int = GIT_STDOUT_CAP,
) -> bytes:
    result = _run_git(root, command, runner, stdout_cap)
    return _checked_output(result, stdout_cap, allow_stderr=False)


def _run_git(
    root: Path,
    command: tuple[str, ...],
    runner: BinaryRunner,
    stdout_cap: int,
) -> GitCommandResult:
    argv = ("git", *GIT_GLOBAL_OPTIONS, *command)
    try:
        return runner(
            argv,
            root,
            git_environment(),
            GIT_LOCAL_TIMEOUT_SECONDS,
            stdout_cap,
            GIT_STDERR_CAP,
        )
    except AidtWorktreeFailure:
        raise
    except Exception:
        raise AidtWorktreeFailure("internal_error") from None


def _checked_output(
    result: object, stdout_cap: int, *, allow_stderr: bool
) -> bytes:
    if not _valid_result(result):
        raise AidtWorktreeFailure("protocol_invalid")
    assert isinstance(result, GitCommandResult)
    if result.timed_out:
        raise AidtWorktreeFailure("protocol_invalid")
    if result.stdout_overflow or result.stderr_overflow:
        raise AidtWorktreeFailure("cap_exceeded")
    if len(result.stdout) > stdout_cap or len(result.stderr) > GIT_STDERR_CAP:
        raise AidtWorktreeFailure("cap_exceeded")
    if result.returncode != 0 or (result.stderr and not allow_stderr):
        raise AidtWorktreeFailure("protocol_invalid")
    return result.stdout


def _valid_result(result: object) -> bool:
    return (
        isinstance(result, GitCommandResult)
        and type(result.returncode) is int
        and isinstance(result.stdout, bytes)
        and isinstance(result.stderr, bytes)
        and type(result.timed_out) is bool
        and type(result.stdout_overflow) is bool
        and type(result.stderr_overflow) is bool
    )


def _canonical_directory(path: Path) -> Path:
    if not isinstance(path, Path):
        raise AidtWorktreeFailure("path_invalid")
    canonical = Path(os.path.abspath(path))
    try:
        value = canonical.lstat()
    except OSError:
        raise AidtWorktreeFailure("path_invalid") from None
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
        raise AidtWorktreeFailure("path_invalid")
    return canonical


def _path_identity(domain: str, path: Path) -> str:
    try:
        value = path.lstat()
    except OSError:
        raise AidtWorktreeFailure("identity_invalid") from None
    payload = {
        "path": str(path),
        "device": value.st_dev if type(value.st_dev) is int else None,
        "inode": value.st_ino if type(value.st_ino) is int else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(domain.encode() + b"\0" + encoded).hexdigest()


def _service_ref(service_id: object) -> str:
    if type(service_id) is not str or _SERVICE_ID.fullmatch(service_id) is None:
        raise AidtWorktreeFailure("identifier_invalid")
    if len(service_id.encode("ascii")) > 48:
        raise AidtWorktreeFailure("identifier_invalid")
    return f"service:{service_id}"


def canonical_origin_digest(origin: object) -> str:
    """Validate and hash the sole HTTPS/SSH origin identity."""
    normalized = _normalize_origin(origin)
    return hashlib.sha256(
        b"aidt-origin-v1\0" + normalized.encode("ascii")
    ).hexdigest()


def parse_status_porcelain_v2(output: object) -> tuple[StatusEntry, ...]:
    """Parse the exact bounded porcelain-v2 NUL status grammar."""
    records = _nul_records(output, "content_invalid")
    entries: list[StatusEntry] = []
    seen: set[str] = set()
    index = 0
    while index < len(records):
        entry, index = _parse_status_record(records, index)
        if entry.path in seen:
            raise AidtWorktreeFailure("content_invalid")
        seen.add(entry.path)
        entries.append(entry)
        index += 1
    if len(entries) > 10_000:
        raise AidtWorktreeFailure("cap_exceeded")
    return tuple(entries)


def _parse_status_record(
    records: list[bytes], index: int
) -> tuple[StatusEntry, int]:
    record = records[index]
    match record[:2]:
        case b"1 ":
            return _ordinary_status(record), index
        case b"2 ":
            if index + 1 >= len(records):
                raise AidtWorktreeFailure("content_invalid")
            return _rename_status(record, records[index + 1]), index + 1
        case b"u ":
            return _unmerged_status(record), index
        case b"? ":
            return StatusEntry("untracked", _status_path(record[2:])), index
        case b"! ":
            return StatusEntry("ignored", _status_path(record[2:])), index
        case _:
            raise AidtWorktreeFailure("content_invalid")


def _nul_records(output: object, category: str) -> list[bytes]:
    if not isinstance(output, bytes) or len(output) > GIT_STDOUT_CAP:
        raise AidtWorktreeFailure(category)
    if not output:
        return []
    if not output.endswith(b"\0"):
        raise AidtWorktreeFailure(category)
    records = output[:-1].split(b"\0")
    if any(not item for item in records):
        raise AidtWorktreeFailure(category)
    return records


def _ordinary_status(record: bytes) -> StatusEntry:
    fields = record.split(b" ", 8)
    if len(fields) != 9 or not _valid_common_status(fields[1:8], renamed=False):
        raise AidtWorktreeFailure("content_invalid")
    return StatusEntry("tracked", _status_path(fields[8]))


def _rename_status(record: bytes, original: bytes) -> StatusEntry:
    fields = record.split(b" ", 9)
    valid_score = len(fields) == 10 and re.fullmatch(
        rb"[RC](?:100|[1-9]?[0-9])", fields[8]
    )
    kind = _rename_kind(fields[1]) if len(fields) == 10 else None
    matching_kind = valid_score and kind == fields[8][:1]
    if not matching_kind or not _valid_common_status(fields[1:8], renamed=True):
        raise AidtWorktreeFailure("content_invalid")
    path = _status_path(fields[9])
    old_path = _status_path(original)
    if path == old_path:
        raise AidtWorktreeFailure("content_invalid")
    return StatusEntry("renamed", path, old_path)


def _unmerged_status(record: bytes) -> StatusEntry:
    fields = record.split(b" ", 10)
    modes = len(fields) == 11 and all(item in _GIT_MODES for item in fields[3:7])
    oids = len(fields) == 11 and all(re.fullmatch(rb"[0-9a-f]{40}", item) for item in fields[7:10])
    submodule = len(fields) == 11 and _valid_submodule(fields[2], fields[3:7])
    if not modes or not oids or fields[1] not in _UNMERGED_XY or not submodule:
        raise AidtWorktreeFailure("content_invalid")
    return StatusEntry("unmerged", _status_path(fields[10]))


def _valid_common_status(fields: list[bytes], *, renamed: bool) -> bool:
    if len(fields) != 7:
        return False
    xy = fields[0]
    modes = fields[2:5]
    valid_xy = _valid_rename_xy(xy) if renamed else xy in _ORDINARY_XY
    return (
        valid_xy
        and all(item in _GIT_MODES for item in modes)
        and _valid_submodule(fields[1], modes)
        and all(re.fullmatch(rb"[0-9a-f]{40}", item) for item in fields[5:7])
    )


def _valid_rename_xy(xy: bytes) -> bool:
    return _rename_kind(xy) is not None


def _rename_kind(xy: bytes) -> bytes | None:
    if len(xy) != 2:
        return None
    if xy[0] in b"RC" and xy[1] in b".MTD":
        return xy[:1]
    if xy[:1] == b"." and xy[1] in b"RC":
        return xy[1:]
    return None


def _valid_submodule(sub: bytes, modes: list[bytes]) -> bool:
    if sub == b"N...":
        return b"160000" not in modes
    valid = (
        len(sub) == 4
        and sub[:1] == b"S"
        and sub[1:2] in {b".", b"C"}
        and sub[2:3] in {b".", b"M"}
        and sub[3:4] in {b".", b"U"}
    )
    return valid and b"160000" in modes and all(
        mode in {b"000000", b"160000"} for mode in modes
    )


def _status_path(raw: bytes) -> str:
    try:
        path = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AidtWorktreeFailure("content_invalid") from None
    path = path[:-1] if path.endswith("/") else path
    encoded = path.encode("utf-8")
    parts = path.split("/")
    invalid = (
        not path
        or len(encoded) > 4_096
        or path.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or parts[0].casefold() == ".git"
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    )
    if invalid:
        raise AidtWorktreeFailure("content_invalid")
    return path


def parse_ref_listing(output: object) -> tuple[RefRecord, ...]:
    """Parse the fixed three-column for-each-ref representation."""
    if not isinstance(output, bytes) or len(output) > GIT_STDOUT_CAP:
        raise AidtWorktreeFailure("protocol_invalid")
    if output and not output.endswith(b"\n"):
        raise AidtWorktreeFailure("protocol_invalid")
    records: list[RefRecord] = []
    seen: set[str] = set()
    for line in output.splitlines():
        fields = line.split(b"\t")
        if len(fields) != 3:
            raise AidtWorktreeFailure("protocol_invalid")
        name, sha, upstream = (_ascii_field(item) for item in fields)
        if name in seen or not _valid_ref(name) or _SHA1.fullmatch(sha) is None:
            raise AidtWorktreeFailure("protocol_invalid")
        if upstream and not _valid_ref(upstream):
            raise AidtWorktreeFailure("protocol_invalid")
        seen.add(name)
        records.append(RefRecord(name, sha, upstream or None))
    if len(records) > 2_500:
        raise AidtWorktreeFailure("cap_exceeded")
    return tuple(records)


def _ascii_field(raw: bytes) -> str:
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        raise AidtWorktreeFailure("protocol_invalid") from None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise AidtWorktreeFailure("protocol_invalid")
    return value


def _valid_ref(value: str) -> bool:
    forbidden = ("..", "@{", "\\", " ", "~", "^", ":", "?", "*", "[")
    parts = value.split("/")
    return (
        len(parts) >= 2
        and parts[0] == "refs"
        and len(value.encode("ascii")) <= 4_096
        and all(
            part
            and not part.startswith(".")
            and not part.endswith((".", ".lock"))
            for part in parts
        )
        and not any(item in value for item in forbidden)
    )


def parse_worktree_porcelain(output: object) -> tuple[WorktreeRegistration, ...]:
    """Parse exact NUL worktree-list records without accepting unknown fields."""
    if not isinstance(output, bytes) or len(output) > GIT_STDOUT_CAP:
        raise AidtWorktreeFailure("protocol_invalid")
    if not output:
        return ()
    if not output.endswith(b"\0\0"):
        raise AidtWorktreeFailure("protocol_invalid")
    groups = output[:-2].split(b"\0\0")
    records = tuple(_worktree_record(group.split(b"\0")) for group in groups)
    if len(records) > 2_500 or len({item.path for item in records}) != len(records):
        raise AidtWorktreeFailure("cap_exceeded" if len(records) > 2_500 else "protocol_invalid")
    branches = [item.branch for item in records if item.branch is not None]
    if len(set(branches)) != len(branches):
        raise AidtWorktreeFailure("protocol_invalid")
    return records


def _worktree_record(fields: list[bytes]) -> WorktreeRegistration:
    if len(fields) < 3 or not fields[0].startswith(b"worktree "):
        raise AidtWorktreeFailure("protocol_invalid")
    path = _absolute_record_path(fields[0][9:])
    values: dict[str, object] = {"branch": None, "detached": False, "locked": False, "prunable": False}
    if not fields[1].startswith(b"HEAD "):
        raise AidtWorktreeFailure("protocol_invalid")
    head = _ascii_field(fields[1][5:])
    if _SHA1.fullmatch(head) is None:
        raise AidtWorktreeFailure("protocol_invalid")
    for field_value in fields[2:]:
        _apply_worktree_field(field_value, values)
    branch = values["branch"]
    detached = values["detached"]
    if branch is not None and type(branch) is not str:
        raise AidtWorktreeFailure("protocol_invalid")
    assert branch is None or isinstance(branch, str)
    if (branch is None) == (detached is False):
        raise AidtWorktreeFailure("protocol_invalid")
    return WorktreeRegistration(path, head, branch, bool(detached), bool(values["locked"]), bool(values["prunable"]))


def _apply_worktree_field(field_value: bytes, values: dict[str, object]) -> None:
    if field_value.startswith(b"branch ") and values["branch"] is None:
        branch = _ascii_field(field_value[7:])
        if not _valid_ref(branch):
            raise AidtWorktreeFailure("protocol_invalid")
        values["branch"] = branch
        return
    if field_value == b"detached" and values["detached"] is False:
        values["detached"] = True
        return
    for key in ("locked", "prunable"):
        if (field_value == key.encode() or field_value.startswith(key.encode() + b" ")) and values[key] is False:
            _bounded_reason(field_value[len(key) :])
            values[key] = True
            return
    raise AidtWorktreeFailure("protocol_invalid")


def _bounded_reason(raw: bytes) -> None:
    if len(raw) > 4_096 or b"\0" in raw or b"\n" in raw or b"\r" in raw:
        raise AidtWorktreeFailure("protocol_invalid")
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AidtWorktreeFailure("protocol_invalid") from None


def _absolute_record_path(raw: bytes) -> Path:
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AidtWorktreeFailure("protocol_invalid") from None
    path = Path(value)
    if not path.is_absolute() or Path(os.path.abspath(path)) != path or len(raw) > 4_096:
        raise AidtWorktreeFailure("protocol_invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise AidtWorktreeFailure("protocol_invalid")
    return path


def _normalize_origin(origin: object) -> str:
    if type(origin) is not str or not _bounded_visible_ascii(origin, 4_096):
        raise AidtWorktreeFailure("protocol_invalid")
    if "\\" in origin or re.search(r"%2f|%5c", origin, re.IGNORECASE):
        raise AidtWorktreeFailure("protocol_invalid")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        raise AidtWorktreeFailure("protocol_invalid") from None
    if not _valid_origin_parts(parsed):
        raise AidtWorktreeFailure("protocol_invalid")
    host = str(parsed.hostname).lower()
    host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme.lower() == "https" else 22
    suffix = "" if port is None or port == default_port else f":{port}"
    user = f"{parsed.username}@" if parsed.username is not None else ""
    return f"{parsed.scheme.lower()}://{user}{host}{suffix}{parsed.path}"


def _valid_origin_parts(parsed: object) -> bool:
    scheme = getattr(parsed, "scheme", "").lower()
    hostname = getattr(parsed, "hostname", None)
    username = getattr(parsed, "username", None)
    if scheme not in {"https", "ssh"} or hostname is None:
        return False
    if getattr(parsed, "password", None) is not None:
        return False
    if getattr(parsed, "query", "") or getattr(parsed, "fragment", ""):
        return False
    if scheme == "https" and username is not None:
        return False
    if username is not None and not _valid_origin_component(username, 256):
        return False
    if not _bounded_visible_ascii(hostname, 253):
        return False
    return _valid_origin_path(getattr(parsed, "path", ""))


def _valid_origin_path(path: str) -> bool:
    if not path.startswith("/") or path == "/" or not _bounded_visible_ascii(path, 4_096):
        return False
    decoded = _decoded_origin_component(path)
    if decoded is None:
        return False
    decoded_bytes, decoded_text = decoded
    if b"\\" in decoded_bytes or _has_control_bytes(decoded_bytes):
        return False
    return all(segment not in {".", ".."} for segment in decoded_text.split("/"))


def _valid_origin_component(value: str, cap: int) -> bool:
    if not _bounded_visible_ascii(value, cap):
        return False
    decoded = _decoded_origin_component(value)
    return decoded is not None and not _has_control_bytes(decoded[0])


def _decoded_origin_component(value: str) -> tuple[bytes, str] | None:
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return None
    try:
        decoded = unquote_to_bytes(value)
        return decoded, decoded.decode("ascii")
    except (UnicodeDecodeError, ValueError):
        return None


def _has_control_bytes(value: bytes) -> bool:
    return any(byte < 32 or byte == 127 for byte in value)


def _bounded_visible_ascii(value: object, cap: int) -> bool:
    if type(value) is not str or _VISIBLE_ASCII.fullmatch(value) is None:
        return False
    return 0 < len(value.encode("ascii")) <= cap
