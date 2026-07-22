"""Immutable, fixed-ref Git object observation for AIDT routing."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import threading
from urllib.parse import unquote, urlsplit
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import IO, Any

from .contract import (
    MAX_GIT_PATHS_PER_SERVICE,
    MAX_OBSERVATION_BYTES,
    MAX_SERVICE_OBJECT_BYTES,
    AidtRoutingFailure,
    RoutingService,
    RoutingSettings,
    canonical_fingerprint,
)


_AIDT_BASE_REF = "refs/remotes/origin/aidt-prd"
GIT_OBJECT_TRUST_SCHEMA = "aidt-git-object-v1"
AIDT_REPOSITORY_BINDING_SCHEMA = "aidt-repository-binding-v1"
GIT_TOKEN_STDOUT_CAP = 128
GIT_PATH_STDOUT_CAP = 4_096
GIT_TREE_RECORD_CAP = 1_024
GIT_BLOB_STDOUT_CAP = 1_048_576
GIT_STDERR_CAP = 8_192
GIT_METADATA_FILE_CAP = 1_048_576
GIT_METADATA_ENTRY_CAP = 4_096
GIT_TIMEOUT_SECONDS = 5.0

_OID = re.compile(r"^[0-9a-f]{40}$")
_GIT_GLOBAL_OPTIONS = (
    "--no-optional-locks",
    "--no-replace-objects",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.file.allow=never",
    "-c",
    "extensions.partialClone=",
    "-c",
    "remote.origin.promisor=false",
)


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    stdout_overflow: bool = False
    stderr_overflow: bool = False


GitRunner = Callable[
    [tuple[str, ...], Mapping[str, str], float, int, int],
    GitCommandResult,
]
IdentityProbe = Callable[[Path], str]


@dataclass(frozen=True, repr=False)
class ObservedService:
    service: RoutingService
    revision_ref: str
    checkout_revision: str
    repository_binding_digest: str
    contents: Mapping[str, str]
    _object_ids: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )
    _repository: _RepositoryBinding | None = field(default=None, repr=False)
    _origin_digest: str = field(default="", repr=False)

    @property
    def revision(self) -> str:
        """Compatibility spelling for the pure decision layer."""
        return self.checkout_revision

    def __repr__(self) -> str:
        return (
            "ObservedService("
            f"service={self.service.id!r}, "
            f"revision_ref={self.revision_ref!r}, "
            f"checkout_revision={self.checkout_revision!r}, "
            f"repository_binding_digest={self.repository_binding_digest!r}, "
            f"content_count={len(self.contents)!r})"
        )


@dataclass(frozen=True, repr=False)
class CatalogObservation:
    services: tuple[ObservedService, ...]
    trust_schema: str = GIT_OBJECT_TRUST_SCHEMA
    total_object_bytes: int = 0

    def __repr__(self) -> str:
        service_ids = tuple(item.service.id for item in self.services)
        return (
            "CatalogObservation("
            f"services={service_ids!r}, "
            f"trust_schema={self.trust_schema!r}, "
            f"total_object_bytes={self.total_object_bytes!r})"
        )


@dataclass(frozen=True)
class _IdentityRecord:
    label: str
    path: Path = field(repr=False)
    kind: str
    token: str
    device: int | None
    inode: int | None


@dataclass(frozen=True, repr=False)
class _RepositoryBinding:
    service_id: str
    checkout: Path
    git_entry: Path
    git_dir: Path
    common_dir: Path
    objects_dir: Path
    identities: tuple[_IdentityRecord, ...]


@dataclass
class _StreamCapture:
    cap: int
    data: bytearray = field(default_factory=bytearray)
    overflow: bool = False


def observe_catalog(
    settings: RoutingSettings,
    *,
    git_runner: GitRunner | None = None,
    identity_probe: IdentityProbe | None = None,
) -> CatalogObservation:
    """Observe every enabled service from one immutable production-base commit."""
    runner = git_runner or _default_git_runner
    probe = identity_probe or _default_identity_probe
    observed: list[ObservedService] = []
    total_bytes = 0
    for service in settings.services:
        if not service.enabled:
            continue
        item, object_bytes = _observe_service(settings, service, runner, probe)
        total_bytes += object_bytes
        if total_bytes > MAX_OBSERVATION_BYTES:
            raise _failure("git_output_limit", service.id)
        observed.append(item)
    _reject_checkout_collisions(observed)
    return CatalogObservation(tuple(observed), total_object_bytes=total_bytes)


def observe_service_binding(
    settings: RoutingSettings,
    service_id: str,
    *,
    git_runner: GitRunner | None = None,
    identity_probe: IdentityProbe | None = None,
) -> ObservedService:
    """Observe one enabled catalog service through the shared binding serializer."""
    matches = [
        service
        for service in settings.services
        if service.enabled and service.id == service_id
    ]
    if len(matches) != 1:
        raise AidtRoutingFailure("catalog_invalid")
    observed, _object_bytes = _observe_service(
        settings,
        matches[0],
        git_runner or _default_git_runner,
        identity_probe or _default_identity_probe,
    )
    return observed


def recheck_catalog(
    observation: CatalogObservation,
    *,
    git_runner: GitRunner | None = None,
    identity_probe: IdentityProbe | None = None,
) -> None:
    """Revalidate repository identity, fixed ref, commit, and required blobs."""
    runner = git_runner or _default_git_runner
    probe = identity_probe or _default_identity_probe
    for service in observation.services:
        _recheck_service(service, runner, probe)


def _default_git_runner(
    argv: tuple[str, ...],
    environment: Mapping[str, str],
    timeout: float,
    stdout_cap: int,
    stderr_cap: int,
) -> GitCommandResult:
    """Stream both channels with hard caps, killing and reaping on overflow."""
    process = _spawn(argv, environment)
    stdout_capture = _StreamCapture(stdout_cap)
    stderr_capture = _StreamCapture(stderr_cap)
    threads = (
        _reader_thread(process.stdout, stdout_capture, process),
        _reader_thread(process.stderr, stderr_capture, process),
    )
    timed_out = _wait_for_process(process, timeout)
    for thread in threads:
        thread.join()
    return GitCommandResult(
        returncode=process.returncode,
        stdout=bytes(stdout_capture.data),
        stderr=bytes(stderr_capture.data),
        timed_out=timed_out,
        stdout_overflow=stdout_capture.overflow,
        stderr_overflow=stderr_capture.overflow,
    )


def _spawn(
    argv: tuple[str, ...], environment: Mapping[str, str]
) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(environment),
        )
    except OSError:
        raise AidtRoutingFailure("git_command_failed") from None


def _reader_thread(
    stream: IO[Any] | None,
    capture: _StreamCapture,
    process: subprocess.Popen[bytes],
) -> threading.Thread:
    if stream is None:
        raise AidtRoutingFailure("internal_error")
    thread = threading.Thread(
        target=_capture_stream,
        args=(stream, capture, process),
        daemon=True,
    )
    thread.start()
    return thread


def _capture_stream(
    stream: IO[Any],
    capture: _StreamCapture,
    process: subprocess.Popen[bytes],
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
        _kill_process(process)
        return


def _wait_for_process(process: subprocess.Popen[bytes], timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
        return False
    except subprocess.TimeoutExpired:
        _kill_process(process)
        process.wait()
        return True


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.kill()
    except OSError:
        pass


def _git_environment() -> Mapping[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_ALLOW_PROTOCOL": "",
    }
    system_root = os.environ.get("SYSTEMROOT")
    if system_root is not None:
        environment["SYSTEMROOT"] = system_root
    return MappingProxyType(environment)


def _git_argv(checkout: Path, command: tuple[str, ...]) -> tuple[str, ...]:
    return ("git", *_GIT_GLOBAL_OPTIONS, "-C", str(checkout), *command)


def _git_output(
    repository: _RepositoryBinding,
    command: tuple[str, ...],
    stdout_cap: int,
    runner: GitRunner,
) -> bytes:
    argv = _git_argv(repository.checkout, command)
    try:
        result = runner(
            argv,
            _git_environment(),
            GIT_TIMEOUT_SECONDS,
            stdout_cap,
            GIT_STDERR_CAP,
        )
    except AidtRoutingFailure as exc:
        raise _failure(exc.category, repository.service_id) from None
    except Exception:
        raise AidtRoutingFailure("internal_error") from None
    return _checked_result(result, stdout_cap, repository.service_id)


def _checked_result(result: object, stdout_cap: int, service_id: str) -> bytes:
    if not _valid_result(result):
        raise _failure("git_protocol_invalid", service_id)
    assert isinstance(result, GitCommandResult)
    if result.timed_out:
        raise _failure("git_timeout", service_id)
    if result.stdout_overflow or result.stderr_overflow:
        raise _failure("git_output_limit", service_id)
    if len(result.stdout) > stdout_cap or len(result.stderr) > GIT_STDERR_CAP:
        raise _failure("git_output_limit", service_id)
    if result.returncode != 0:
        raise _failure("git_command_failed", service_id)
    if result.stderr:
        raise _failure("git_protocol_invalid", service_id)
    return result.stdout


def _valid_result(result: object) -> bool:
    if not isinstance(result, GitCommandResult):
        return False
    return (
        type(result.returncode) is int
        and isinstance(result.stdout, bytes)
        and isinstance(result.stderr, bytes)
        and type(result.timed_out) is bool
        and type(result.stdout_overflow) is bool
        and type(result.stderr_overflow) is bool
    )


def _decode_scalar(output: bytes, service_id: str | None = None) -> str:
    raw = output[:-1] if output.endswith(b"\n") else output
    invalid = not raw or b"\n" in raw or b"\r" in raw or b"\0" in raw
    if invalid or any(byte < 32 or byte == 127 for byte in raw):
        raise _failure("git_protocol_invalid", service_id)
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        raise _failure("git_protocol_invalid", service_id) from None


def _decode_oid(output: bytes, service_id: str) -> str:
    value = _decode_scalar(output, service_id)
    if _OID.fullmatch(value) is None:
        raise _failure("git_protocol_invalid", service_id)
    return value


def _parse_tree_record(
    output: bytes, expected_path: str, service_id: str | None = None
) -> str:
    if len(output) > GIT_TREE_RECORD_CAP:
        raise _failure("git_output_limit", service_id)
    if not output.endswith(b"\0") or b"\0" in output[:-1]:
        raise _failure("git_protocol_invalid", service_id)
    record = output[:-1]
    if record.count(b"\t") != 1:
        raise _failure("git_protocol_invalid", service_id)
    metadata, returned_path = record.split(b"\t")
    fields = metadata.split(b" ")
    if len(fields) != 3:
        raise _failure("git_protocol_invalid", service_id)
    _validate_tree_fields(fields, service_id)
    if returned_path != expected_path.encode("ascii"):
        raise _failure("git_protocol_invalid", service_id)
    return fields[2].decode("ascii")


def _validate_tree_fields(fields: list[bytes], service_id: str | None) -> None:
    mode, object_type, object_id = fields
    if mode not in {b"100644", b"100755"} or object_type != b"blob":
        raise _failure("git_object_invalid", service_id)
    try:
        decoded = object_id.decode("ascii")
    except UnicodeDecodeError:
        raise _failure("git_object_invalid", service_id) from None
    if _OID.fullmatch(decoded) is None:
        raise _failure("git_object_invalid", service_id)


def _observe_service(
    settings: RoutingSettings,
    service: RoutingService,
    runner: GitRunner,
    probe: IdentityProbe,
) -> tuple[ObservedService, int]:
    repository = _capture_repository(settings.aidt_root, service, probe)
    revision = _read_repository_revision(repository, runner)
    paths = _service_paths(service)
    object_ids = _read_tree_entries(repository, revision, paths, runner)
    contents, object_bytes = _read_scoring_blobs(
        repository, service, object_ids, runner
    )
    origin_digest = _read_origin_digest(repository, runner)
    digest = _repository_digest(
        service, repository, revision, origin_digest, object_ids
    )
    observed = ObservedService(
        service=service,
        revision_ref=_AIDT_BASE_REF,
        checkout_revision=revision,
        repository_binding_digest=digest,
        contents=MappingProxyType(contents),
        _object_ids=MappingProxyType(object_ids),
        _repository=repository,
        _origin_digest=origin_digest,
    )
    _recheck_service(observed, runner, probe)
    return observed, object_bytes


def _read_repository_revision(
    repository: _RepositoryBinding, runner: GitRunner
) -> str:
    top_level = _read_scalar(
        repository,
        ("rev-parse", "--show-toplevel"),
        GIT_PATH_STDOUT_CAP,
        runner,
    )
    if top_level != str(repository.checkout):
        raise _failure("repository_invalid", repository.service_id)
    object_format = _read_scalar(
        repository,
        ("rev-parse", "--show-object-format"),
        GIT_TOKEN_STDOUT_CAP,
        runner,
    )
    if object_format != "sha1":
        raise _failure("repository_invalid", repository.service_id)
    revision = _read_oid(repository, _AIDT_BASE_REF + "^{commit}", runner)
    _assert_commit(repository, revision, runner)
    return revision


def _read_scalar(
    repository: _RepositoryBinding,
    command: tuple[str, ...],
    cap: int,
    runner: GitRunner,
) -> str:
    output = _git_output(repository, command, cap, runner)
    return _decode_scalar(output, repository.service_id)


def _read_oid(
    repository: _RepositoryBinding, revision: str, runner: GitRunner
) -> str:
    output = _git_output(
        repository,
        ("rev-parse", "--verify", revision),
        GIT_TOKEN_STDOUT_CAP,
        runner,
    )
    return _decode_oid(output, repository.service_id)


def _assert_commit(
    repository: _RepositoryBinding, revision: str, runner: GitRunner
) -> None:
    output = _git_output(
        repository,
        ("cat-file", "-e", revision + "^{commit}"),
        GIT_TOKEN_STDOUT_CAP,
        runner,
    )
    if output:
        raise _failure("git_protocol_invalid", repository.service_id)


def _service_paths(service: RoutingService) -> tuple[str, ...]:
    paths = (
        *service.markers,
        *(item.file for item in service.context_anchors),
        *(item.file for item in service.route_anchors),
        *(item.file for item in service.domain_anchors),
    )
    unique = tuple(dict.fromkeys(paths))
    if len(paths) > MAX_GIT_PATHS_PER_SERVICE:
        raise _failure("catalog_invalid", None)
    return unique


def _scoring_paths(service: RoutingService) -> frozenset[str]:
    return frozenset(
        (
            *(item.file for item in service.context_anchors),
            *(item.file for item in service.route_anchors),
            *(item.file for item in service.domain_anchors),
        )
    )


def _read_tree_entries(
    repository: _RepositoryBinding,
    revision: str,
    paths: tuple[str, ...],
    runner: GitRunner,
) -> dict[str, str]:
    object_ids: dict[str, str] = {}
    for path in paths:
        output = _git_output(
            repository,
            ("ls-tree", "-z", "--full-tree", revision, "--", path),
            GIT_TREE_RECORD_CAP,
            runner,
        )
        object_ids[path] = _parse_tree_record(
            output, path, repository.service_id
        )
    return object_ids


def _read_scoring_blobs(
    repository: _RepositoryBinding,
    service: RoutingService,
    object_ids: Mapping[str, str],
    runner: GitRunner,
) -> tuple[dict[str, str], int]:
    contents: dict[str, str] = {}
    total_bytes = 0
    for path in sorted(_scoring_paths(service)):
        output = _git_output(
            repository,
            ("cat-file", "blob", object_ids[path]),
            GIT_BLOB_STDOUT_CAP,
            runner,
        )
        total_bytes += len(output)
        if total_bytes > MAX_SERVICE_OBJECT_BYTES:
            raise _failure("git_output_limit", service.id)
        contents[path] = _decode_blob(output, service.id)
    return contents, total_bytes


def _decode_blob(output: bytes, service_id: str | None = None) -> str:
    if len(output) > GIT_BLOB_STDOUT_CAP:
        raise _failure("git_output_limit", service_id)
    try:
        return output.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _failure("git_object_invalid", service_id) from None


def _capture_repository(
    root: Path, service: RoutingService, probe: IdentityProbe
) -> _RepositoryBinding:
    service_id = service.id
    trusted_root = _trusted_path(root, "directory", service_id)
    checkout = _trusted_path(trusted_root / service.checkout, "directory", service_id)
    git_entry, git_dir = _resolve_git_entry(checkout, service_id)
    common_dir = _resolve_common_dir(git_dir, service_id)
    objects_dir = _trusted_path(common_dir / "objects", "directory", service_id)
    _reject_object_indirection(common_dir, objects_dir, service_id)
    records = _identity_records(
        trusted_root,
        checkout,
        git_entry,
        git_dir,
        common_dir,
        objects_dir,
        probe,
        service_id,
    )
    return _RepositoryBinding(
        service_id,
        checkout,
        git_entry,
        git_dir,
        common_dir,
        objects_dir,
        records,
    )


def _resolve_git_entry(checkout: Path, service_id: str) -> tuple[Path, Path]:
    git_entry = _absolute(checkout / ".git")
    try:
        mode = git_entry.lstat().st_mode
    except OSError:
        raise _failure("repository_invalid", service_id) from None
    if stat.S_ISLNK(mode):
        raise _failure("repository_invalid", service_id)
    if stat.S_ISDIR(mode):
        return git_entry, _trusted_path(git_entry, "directory", service_id)
    if not stat.S_ISREG(mode):
        raise _failure("repository_invalid", service_id)
    data = _read_metadata_file(git_entry, GIT_PATH_STDOUT_CAP, service_id)
    git_dir = _metadata_target(data, b"gitdir: ", checkout, service_id)
    return git_entry, _trusted_path(git_dir, "directory", service_id)


def _resolve_common_dir(git_dir: Path, service_id: str) -> Path:
    entry = git_dir / "commondir"
    try:
        mode = entry.lstat().st_mode
    except FileNotFoundError:
        return git_dir
    except OSError:
        raise _failure("repository_invalid", service_id) from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise _failure("repository_invalid", service_id)
    data = _read_metadata_file(entry, GIT_PATH_STDOUT_CAP, service_id)
    target = _metadata_target(data, b"", git_dir, service_id)
    return _trusted_path(target, "directory", service_id)


def _metadata_target(
    data: bytes, prefix: bytes, base: Path, service_id: str
) -> Path:
    raw = data[:-1] if data.endswith(b"\n") else data
    if not raw.startswith(prefix) or not raw[len(prefix) :]:
        raise _failure("repository_invalid", service_id)
    value = raw[len(prefix) :]
    if b"\n" in value or b"\r" in value or b"\0" in value:
        raise _failure("repository_invalid", service_id)
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _failure("repository_invalid", service_id) from None
    path = Path(text)
    return _absolute(path if path.is_absolute() else base / path)


def _read_metadata_file(path: Path, cap: int, service_id: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise _failure("repository_invalid", service_id)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise _failure("repository_invalid", service_id)
            if _stat_identity(before) != _stat_identity(opened):
                raise _failure("repository_invalid", service_id)
            data = stream.read(cap + 1)
        after = path.lstat()
    except AidtRoutingFailure:
        raise
    except OSError:
        raise _failure("repository_invalid", service_id) from None
    if _stat_identity(after) != _stat_identity(before):
        raise _failure("repository_invalid", service_id)
    if len(data) > cap:
        raise _failure("repository_invalid", service_id)
    return data


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns


def _trusted_path(path: Path, kind: str, service_id: str) -> Path:
    absolute = _absolute(path)
    chain = tuple(reversed(absolute.parents)) + (absolute,)
    for index, component in enumerate(chain):
        expected = kind if index == len(chain) - 1 else "directory"
        _validate_path_shape(component, expected, service_id)
    return absolute


def _validate_path_shape(path: Path, kind: str, service_id: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        raise _failure("repository_invalid", service_id) from None
    valid = stat.S_ISDIR(mode) if kind == "directory" else stat.S_ISREG(mode)
    if stat.S_ISLNK(mode) or not valid:
        raise _failure("repository_invalid", service_id)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _identity_records(
    root: Path,
    checkout: Path,
    git_entry: Path,
    git_dir: Path,
    common_dir: Path,
    objects_dir: Path,
    probe: IdentityProbe,
    service_id: str,
) -> tuple[_IdentityRecord, ...]:
    values = (
        ("root", root, "directory"),
        ("checkout", checkout, "directory"),
        ("git-entry", git_entry, _path_kind(git_entry)),
        ("git-directory", git_dir, "directory"),
        ("common-directory", common_dir, "directory"),
        ("object-directory", objects_dir, "directory"),
    )
    return tuple(
        _identity_record(label, path, kind, probe, service_id)
        for label, path, kind in values
    )


def _identity_record(
    label: str,
    path: Path,
    kind: str,
    probe: IdentityProbe,
    service_id: str,
) -> _IdentityRecord:
    try:
        token = probe(path)
        value = path.lstat()
    except Exception:
        raise _failure("repository_invalid", service_id) from None
    if not isinstance(token, str) or not token:
        raise _failure("repository_invalid", service_id)
    opaque = canonical_fingerprint("aidt-identity-probe-v1", token)
    device = value.st_dev if type(value.st_dev) is int else None
    inode = value.st_ino if type(value.st_ino) is int else None
    return _IdentityRecord(label, path, kind, opaque, device, inode)


def _default_identity_probe(path: Path) -> str:
    value = path.lstat()
    return canonical_fingerprint(
        "aidt-stat-identity-v1",
        [value.st_dev, value.st_ino, value.st_mode],
    )


def _path_kind(path: Path) -> str:
    try:
        return "directory" if path.is_dir() else "regular"
    except OSError:
        raise AidtRoutingFailure("repository_invalid") from None


def _reject_object_indirection(
    common_dir: Path, objects_dir: Path, service_id: str
) -> None:
    alternates = objects_dir / "info" / "alternates"
    if _lexists(alternates):
        raise _failure("repository_invalid", service_id)
    pack_dir = objects_dir / "pack"
    if _lexists(pack_dir) and _directory_has_suffix(pack_dir, ".promisor", service_id):
        raise _failure("repository_invalid", service_id)
    replace_dir = common_dir / "refs" / "replace"
    if _lexists(replace_dir) and _directory_has_entries(replace_dir, service_id):
        raise _failure("repository_invalid", service_id)
    packed_refs = common_dir / "packed-refs"
    if not _lexists(packed_refs):
        return
    data = _read_metadata_file(packed_refs, GIT_METADATA_FILE_CAP, service_id)
    if any(b" refs/replace/" in line for line in data.splitlines()):
        raise _failure("repository_invalid", service_id)


def _directory_has_suffix(path: Path, suffix: str, service_id: str) -> bool:
    trusted = _trusted_path(path, "directory", service_id)
    try:
        with os.scandir(trusted) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > GIT_METADATA_ENTRY_CAP:
                    raise _failure("repository_invalid", service_id)
                if entry.name.endswith(suffix):
                    return True
    except AidtRoutingFailure:
        raise
    except OSError:
        raise _failure("repository_invalid", service_id) from None
    return False


def _directory_has_entries(path: Path, service_id: str) -> bool:
    trusted = _trusted_path(path, "directory", service_id)
    try:
        with os.scandir(trusted) as entries:
            return next(entries, None) is not None
    except OSError:
        raise _failure("repository_invalid", service_id) from None


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _read_origin_digest(
    repository: _RepositoryBinding, runner: GitRunner
) -> str:
    output = _git_output(
        repository,
        ("remote", "get-url", "--all", "origin"),
        GIT_PATH_STDOUT_CAP,
        runner,
    )
    value = _decode_scalar(output, repository.service_id)
    normalized = _normalize_origin(value, repository.service_id)
    payload = b"aidt-origin-v1\0" + normalized.encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _normalize_origin(value: str, service_id: str) -> str:
    if "\\" in value or re.search(r"%2f|%5c", value, re.IGNORECASE):
        raise _failure("repository_invalid", service_id)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise _failure("repository_invalid", service_id) from None
    if not _valid_origin_parts(parsed, service_id):
        raise _failure("repository_invalid", service_id)
    host = str(parsed.hostname).lower()
    host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme.lower() == "https" else 22
    port_text = "" if port is None or port == default_port else f":{port}"
    user = f"{parsed.username}@" if parsed.username is not None else ""
    return f"{parsed.scheme.lower()}://{user}{host}{port_text}{parsed.path}"


def _valid_origin_parts(value: Any, service_id: str) -> bool:
    scheme = value.scheme.lower()
    if scheme not in {"https", "ssh"} or value.hostname is None:
        return False
    if value.password is not None or value.query or value.fragment:
        return False
    if scheme == "https" and value.username is not None:
        return False
    if value.username is not None and not _bounded_ascii(value.username, 256):
        return False
    if not _bounded_ascii(value.hostname, 253):
        return False
    if not value.path.startswith("/") or value.path == "/":
        return False
    try:
        decoded = unquote(value.path, errors="strict")
    except UnicodeDecodeError:
        raise _failure("repository_invalid", service_id) from None
    segments = decoded.split("/")
    return "\\" not in decoded and all(item not in {".", ".."} for item in segments)


def _bounded_ascii(value: str, cap: int) -> bool:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return 0 < len(encoded) <= cap and not any(byte < 33 or byte == 127 for byte in encoded)


def _repository_digest(
    service: RoutingService,
    repository: _RepositoryBinding,
    revision: str,
    origin_digest: str,
    object_ids: Mapping[str, str],
) -> str:
    binding = {
        "service": service.id,
        "kind": service.kind,
        "catalog_checkout": service.checkout,
        "revision_ref": _AIDT_BASE_REF,
        "checkout_revision": revision,
        "object_format": "sha1",
        "origin_digest": origin_digest,
        "identities": [
            {
                "label": item.label,
                "path": str(item.path),
                "device": item.device,
                "inode": item.inode,
            }
            for item in repository.identities
            if item.label in {"checkout", "common-directory", "object-directory"}
        ],
        "required_objects": [
            {"path": path, "object_id": object_id}
            for path, object_id in sorted(object_ids.items())
        ],
    }
    return canonical_fingerprint(AIDT_REPOSITORY_BINDING_SCHEMA, binding)


def _recheck_service(
    observed: ObservedService, runner: GitRunner, probe: IdentityProbe
) -> None:
    repository = observed._repository
    if repository is None:
        raise AidtRoutingFailure("internal_error")
    current = _recapture_repository(repository, observed.service, probe)
    if current != repository:
        raise _failure("repository_changed", observed.service.id)
    _recheck_git_identity(observed, repository, runner)
    for object_id in observed._object_ids.values():
        _recheck_object(repository, object_id, runner)


def _recapture_repository(
    repository: _RepositoryBinding,
    service: RoutingService,
    probe: IdentityProbe,
) -> _RepositoryBinding:
    root = next(
        item.path for item in repository.identities if item.label == "root"
    )
    try:
        return _capture_repository(root, service, probe)
    except AidtRoutingFailure:
        raise _failure("repository_changed", service.id) from None


def _recheck_git_identity(
    observed: ObservedService,
    repository: _RepositoryBinding,
    runner: GitRunner,
) -> None:
    try:
        revision = _read_repository_revision(repository, runner)
    except AidtRoutingFailure as failure:
        if failure.category in {"repository_invalid", "repository_changed"}:
            raise _failure("repository_changed", observed.service.id) from None
        raise _failure("revision_changed", observed.service.id) from None
    if revision != observed.checkout_revision:
        raise _failure("revision_changed", observed.service.id)
    origin_digest = _read_origin_digest(repository, runner)
    digest = _repository_digest(
        observed.service,
        repository,
        revision,
        origin_digest,
        observed._object_ids,
    )
    if digest != observed.repository_binding_digest:
        raise _failure("repository_changed", observed.service.id)


def _recheck_object(
    repository: _RepositoryBinding, object_id: str, runner: GitRunner
) -> None:
    try:
        output = _git_output(
            repository,
            ("cat-file", "-e", object_id + "^{blob}"),
            GIT_TOKEN_STDOUT_CAP,
            runner,
        )
    except AidtRoutingFailure:
        raise _failure("revision_changed", repository.service_id) from None
    if output:
        raise _failure("revision_changed", repository.service_id)


def _reject_checkout_collisions(observed: list[ObservedService]) -> None:
    seen_paths: set[Path] = set()
    seen_tokens: set[str] = set()
    for item in observed:
        repository = item._repository
        if repository is None:
            raise AidtRoutingFailure("internal_error")
        token = next(
            record.token
            for record in repository.identities
            if record.label == "checkout"
        )
        if repository.checkout in seen_paths or token in seen_tokens:
            raise AidtRoutingFailure("catalog_invalid")
        seen_paths.add(repository.checkout)
        seen_tokens.add(token)


def _failure(category: str, service_id: str | None) -> AidtRoutingFailure:
    identifier = f"service:{service_id}" if service_id is not None else None
    return AidtRoutingFailure(category, identifier)
