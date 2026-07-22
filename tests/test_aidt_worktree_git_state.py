"""Bounded Git-state protocol for AIDT worktree ownership."""

from __future__ import annotations

import ast
import hashlib
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from symphony.aidt_worktree import (
    FETCH_ARGV,
    FetchResult,
    GIT_GLOBAL_OPTIONS,
    GIT_LOCAL_TIMEOUT_SECONDS,
    GitCommandResult,
    RefRecord,
    RepositoryIdentity,
    RepositoryState,
    TargetArtifactDisposition,
    TicketWorktreeState,
    StatusEntry,
    WorktreeRegistration,
    advisory_lock,
    add_worktree,
    base_is_ancestor,
    canonical_origin_digest,
    classify_target_artifacts,
    common_git_lock_path,
    default_binary_runner,
    fetch_production_base,
    git_environment,
    observe_repository_identity,
    observe_repository_state,
    observe_ticket_worktree,
    parse_ref_listing,
    parse_status_porcelain_v2,
    parse_worktree_porcelain,
    remove_worktree,
    stable_worktree_paths,
    validate_create_delta,
    validate_fetch_delta,
    validate_remove_delta,
    verify_service_binding,
)
from symphony.aidt_worktree.contract import AidtWorktreeFailure


def _hold_process_lock(
    lock_path: str,
    entered: object,
    release: object,
    crash: bool,
) -> None:
    from symphony.aidt_worktree import advisory_lock

    with advisory_lock(Path(lock_path), timeout_seconds=3.0):
        entered.set()  # type: ignore[attr-defined]
        if crash:
            os._exit(23)
        release.wait(3.0)  # type: ignore[attr-defined]


class _RecordingRunner:
    def __init__(self) -> None:
        self.argv: list[tuple[str, ...]] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        environment: object,
        timeout: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> GitCommandResult:
        self.argv.append(argv)
        return default_binary_runner(
            argv, cwd, environment, timeout, stdout_cap, stderr_cap  # type: ignore[arg-type]
        )


def _assert_no_forbidden_commands(requests: list[tuple[str, ...]]) -> None:
    forbidden = {"reset", "rebase", "switch", "checkout", "prune", "--force", "-D"}
    assert all(not forbidden.intersection(argv) for argv in requests)


def _control_depth(node: ast.AST, depth: int = 0) -> int:
    controls = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)
    current = depth + int(isinstance(node, controls))
    children = [
        child
        for child in ast.iter_child_nodes(node)
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    ]
    return max([current, *(_control_depth(child, current) for child in children)])


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=check,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_process_exit(pid: int, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.01)
    return False


def _repository(tmp_path: Path) -> tuple[Path, str]:
    checkout = tmp_path / "service"
    checkout.mkdir()
    _git(checkout, "init", "-b", "aidt-prd")
    _git(checkout, "config", "user.name", "Fixture")
    _git(checkout, "config", "user.email", "fixture@example.test")
    (checkout / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (checkout / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(checkout, "add", ".gitignore", "tracked.txt")
    _git(checkout, "commit", "-m", "base")
    revision = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    _git(
        checkout,
        "remote",
        "add",
        "origin",
        "https://fixture.invalid/repository.git",
    )
    _git(
        checkout,
        "update-ref",
        "refs/remotes/origin/aidt-prd",
        revision,
    )
    return checkout.resolve(), revision


def test_binary_runner_returns_early_exit_before_deadline(tmp_path: Path) -> None:
    before = set(threading.enumerate())
    started = time.monotonic()

    result = default_binary_runner(
        (sys.executable, "-c", "print('complete')"),
        tmp_path,
        git_environment(),
        GIT_LOCAL_TIMEOUT_SECONDS,
        1_048_576,
        65_536,
    )

    assert time.monotonic() - started < 2.0
    assert result == GitCommandResult(0, b"complete\n", b"")
    assert not any(thread.is_alive() for thread in set(threading.enumerate()) - before)


def test_binary_runner_kills_and_reaps_process_group_at_deadline(
    tmp_path: Path,
) -> None:
    script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "print(child.pid, flush=True); time.sleep(30)"
    )
    before = set(threading.enumerate())
    started = time.monotonic()

    result = default_binary_runner(
        (sys.executable, "-c", script),
        tmp_path,
        git_environment(),
        0.2,
        1_048_576,
        65_536,
    )

    child_pid = int(result.stdout.strip())
    assert 0.15 <= time.monotonic() - started < 2.0
    assert result.timed_out is True and result.returncode == -9
    assert _wait_for_process_exit(child_pid)
    assert not any(thread.is_alive() for thread in set(threading.enumerate()) - before)


def test_twenty_real_worktree_adds_keep_deadline_margin_and_release_resources(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    add_argv: list[tuple[str, ...]] = []
    add_elapsed: list[float] = []
    before = set(threading.enumerate())
    descriptors_before = len(os.listdir("/dev/fd"))
    children_before = set(multiprocessing.active_children())

    def timed_runner(argv: tuple[str, ...], *args: Any) -> GitCommandResult:
        started = time.monotonic()
        result = default_binary_runner(argv, *args)
        if "worktree" in argv and "add" in argv:
            add_argv.append(argv)
            add_elapsed.append(time.monotonic() - started)
        return result

    for offset in range(20):
        issue = 1200 + offset
        branch = f"fix/A20-{issue}"
        workspace = (tmp_path / "workspaces" / str(issue)).resolve()
        workspace.parent.mkdir(exist_ok=True)
        add_worktree(identity, branch, workspace, revision, runner=timed_runner)
        remove_worktree(identity, workspace, runner=timed_runner)
        _git(checkout, "update-ref", "-d", f"refs/heads/{branch}")
        assert not workspace.exists()

    expected_prefix = ("git", *GIT_GLOBAL_OPTIONS, "worktree", "add", "--no-track", "-b")
    registrations = _git(checkout, "worktree", "list", "--porcelain").stdout
    assert len(add_argv) == len(add_elapsed) == 20
    assert all(argv[: len(expected_prefix)] == expected_prefix for argv in add_argv)
    assert max(add_elapsed) < GIT_LOCAL_TIMEOUT_SECONDS * 0.8
    assert registrations.count(b"worktree ") == 1
    assert len(os.listdir("/dev/fd")) <= descriptors_before
    assert set(multiprocessing.active_children()) == children_before
    assert not any(thread.is_alive() for thread in set(threading.enumerate()) - before)


def _status_bytes(checkout: Path) -> bytes:
    return _git(
        checkout,
        "status",
        "--porcelain=v2",
        "-z",
        "--untracked-files=all",
    ).stdout


def _type2_wire_records(
    raw: bytes,
) -> tuple[tuple[bytes, bytes, bytes, bytes], ...]:
    records = raw.removesuffix(b"\0").split(b"\0") if raw else []
    result: list[tuple[bytes, bytes, bytes, bytes]] = []
    index = 0
    while index < len(records):
        record = records[index]
        if record.startswith(b"2 "):
            fields = record.split(b" ", 9)
            assert len(fields) == 10
            assert index + 1 < len(records)
            result.append((fields[1], fields[8], fields[9], records[index + 1]))
            index += 2
        else:
            index += 1
    return tuple(result)


def _type1_wire_records(raw: bytes) -> tuple[tuple[bytes, bytes], ...]:
    records = raw.removesuffix(b"\0").split(b"\0") if raw else []
    result: list[tuple[bytes, bytes]] = []
    for record in records:
        if record.startswith(b"1 "):
            fields = record.split(b" ", 8)
            assert len(fields) == 9
            result.append((fields[1], fields[8]))
    return tuple(result)


def _forged_type2_record(xy: bytes, score: bytes) -> bytes:
    oid = b"a" * 40
    return (
        b"2 "
        + xy
        + b" N... 100644 100644 100644 "
        + oid
        + b" "
        + oid
        + b" "
        + score
        + b" new.txt\0old.txt\0"
    )


def _type2_source_content() -> str:
    return "".join(
        f"shared-{index:03d}-" + "x" * 68 + "\n" for index in range(200)
    )


def _type2_copy_content() -> str:
    shared = _type2_source_content().splitlines(keepends=True)[:150]
    distinct = [
        f"target-{index:03d}-" + "y" * 68 + "\n" for index in range(150, 200)
    ]
    return "".join((*shared, *distinct))


def _commit_type2_source(checkout: Path, source: Path) -> None:
    source.write_text(_type2_source_content(), encoding="utf-8")
    _git(checkout, "add", source.name)
    _git(checkout, "commit", "-m", "type2 source")


def _prepare_type2_change(checkout: Path, source: Path, target: Path, xy: bytes) -> bytes:
    index_kind = xy[:1]
    kind = index_kind if index_kind in {b"R", b"C"} else xy[1:]
    if kind == b"C":
        _git(checkout, "config", "status.renames", "copies")
        source.write_text(
            _type2_source_content().replace("shared-199", "source-199"),
            encoding="utf-8",
        )
        target.write_text(_type2_copy_content(), encoding="utf-8")
    elif index_kind == b"R":
        _git(checkout, "mv", source.name, target.name)
    else:
        source.rename(target)
    if index_kind == b"C":
        _git(checkout, "add", source.name, target.name)
    elif index_kind not in {b"R", b"C"}:
        _git(checkout, "add", "--intent-to-add", target.name)
    return kind


def _apply_type2_worktree_suffix(target: Path, xy: bytes) -> None:
    match xy[1:]:
        case b"M":
            target.write_text(target.read_text(encoding="utf-8") + "modified\n", encoding="utf-8")
        case b"T":
            target.unlink()
            target.symlink_to("tracked.txt")
        case b"D":
            target.unlink()


def _produce_type2_status(tmp_path: Path, xy: bytes) -> tuple[bytes, str, str, bytes]:
    checkout, _revision = _repository(tmp_path)
    source = checkout / "type2-source.txt"
    target = checkout / "type2-target.txt"
    _commit_type2_source(checkout, source)
    kind = _prepare_type2_change(checkout, source, target, xy)
    if xy[:1] in {b"R", b"C"}:
        _apply_type2_worktree_suffix(target, xy)
    score = kind + (b"100" if kind == b"R" else b"75")
    return _status_bytes(checkout), target.name, source.name, score


def test_production_fetch_contract_and_origin_parser_are_exact() -> None:
    assert FETCH_ARGV == (
        "git",
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
        "fetch",
        "--no-tags",
        "--no-recurse-submodules",
        "--no-write-fetch-head",
        "origin",
        "+refs/heads/aidt-prd:refs/remotes/origin/aidt-prd",
    )
    expected = hashlib.sha256(
        b"aidt-origin-v1\0https://fixture.invalid/repository.git"
    ).hexdigest()
    assert canonical_origin_digest(
        "HTTPS://FIXTURE.INVALID:443/repository.git"
    ) == expected
    assert canonical_origin_digest("ssh://git@EXAMPLE.test:22/a/repo.git") == (
        hashlib.sha256(
            b"aidt-origin-v1\0ssh://git@example.test/a/repo.git"
        ).hexdigest()
    )
    assert git_environment()["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert type(GitCommandResult(0, b"", b"").returncode) is int


@pytest.mark.parametrize(
    "origin",
    [
        "file:///tmp/repo.git",
        "git@example.test:repo.git",
        "https://user@example.test/repo.git",
        "ssh://git:secret@example.test/repo.git",
        "https://example.test/repo.git?x=1",
        "https://example.test/a/../repo.git",
        "https://example.test/a/%2Frepo.git",
        "https://example.test/a\\repo.git",
    ],
)
def test_origin_parser_rejects_every_noncanonical_or_unsafe_transport(
    origin: str,
) -> None:
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        canonical_origin_digest(origin)


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.test/repo\x00evil.git",
        "https://example.test/repo\x1fevil.git",
        "https://example.test/repo\x7fevil.git",
        "https://example.test/repo%00evil.git",
        "https://example.test/repo%0Aevil.git",
        "https://example.test/repo%1fevil.git",
        "https://example.test/repo%7Fevil.git",
        "ssh://git\x00evil@example.test/repo.git",
        "ssh://git\x7fevil@example.test/repo.git",
        "ssh://git%00evil@example.test/repo.git",
        "ssh://git%0Aevil@example.test/repo.git",
        "ssh://git%7Fevil@example.test/repo.git",
    ],
)
def test_origin_parser_rejects_raw_and_decoded_control_text(origin: str) -> None:
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        canonical_origin_digest(origin)


def test_git_environment_is_the_exact_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/fixture/bin")
    monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
    monkeypatch.setenv("SECRET_SENTINEL", "must-not-leak")

    assert git_environment() == {
        "PATH": "/fixture/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "SYSTEMROOT": "C:\\Windows",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/usr/bin/false",
        "SSH_ASKPASS": "/usr/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    assert os.environ["SECRET_SENTINEL"] not in git_environment().values()


def test_repository_identity_binds_top_common_objects_format_and_origin(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)

    identity = observe_repository_identity(checkout, "viewer-api")

    assert type(identity) is RepositoryIdentity
    assert identity.service_ref == "service:viewer-api"
    assert identity.service_root == checkout
    assert identity.git_directory == checkout / ".git"
    assert identity.common_git_directory == checkout / ".git"
    assert identity.object_directory == checkout / ".git" / "objects"
    assert identity.object_format == "sha1"
    assert identity.origin_digest == canonical_origin_digest(
        "https://fixture.invalid/repository.git"
    )
    assert len(identity.top_level_identity) == 64
    assert len(identity.common_git_identity) == 64
    assert len(identity.object_identity) == 64


@pytest.mark.parametrize("field", ["returncode", "stdout", "stderr", "timed_out"])
def test_repository_identity_rejects_malformed_injected_runner_result(
    tmp_path: Path, field: str
) -> None:
    checkout, _revision = _repository(tmp_path)

    def malformed(*_args: Any) -> object:
        values: dict[str, object] = {
            "returncode": 0,
            "stdout": f"{checkout}\n".encode(),
            "stderr": b"",
            "timed_out": False,
        }
        values[field] = {"returncode": True, "stdout": "bad", "stderr": "bad", "timed_out": 1}[field]
        return GitCommandResult(**values)  # type: ignore[arg-type]

    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        observe_repository_identity(checkout, "viewer-api", runner=malformed)


def test_status_parser_accepts_real_git_non_padded_rename_score(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    source = checkout / "rename-source.txt"
    target = checkout / "rename-target.txt"
    source.write_text(
        "".join(f"line-{index:03d}-" + "x" * 72 + "\n" for index in range(200)),
        encoding="utf-8",
    )
    _git(checkout, "add", source.name)
    _git(checkout, "commit", "-m", "rename source")
    _git(checkout, "mv", source.name, target.name)
    content = target.read_text(encoding="utf-8")
    target.write_text(content.replace("line-000", "changed-0", 1), encoding="utf-8")
    _git(checkout, "add", target.name)
    raw = _git(
        checkout,
        "status",
        "--porcelain=v2",
        "-z",
        "--untracked-files=all",
    ).stdout
    rename = next(record for record in raw.split(b"\0") if record.startswith(b"2 "))

    assert rename.split(b" ", 9)[8] == b"R99"
    assert parse_status_porcelain_v2(raw) == (
        StatusEntry("renamed", target.name, source.name),
    )


@pytest.mark.parametrize(
    "xy",
    [b"R.", b"RM", b"RT", b"RD", b"C.", b"CM", b"CT", b"CD", b".R", b".C"],
)
def test_status_parser_accepts_all_real_git_type2_families(
    tmp_path: Path,
    xy: bytes,
) -> None:
    raw, target, source, score = _produce_type2_status(tmp_path, xy)
    assert _type2_wire_records(raw) == (
        (xy, score, target.encode(), source.encode()),
    )
    expected = (StatusEntry("renamed", target, source),)
    if score.startswith(b"C"):
        expected = (StatusEntry("tracked", source), *expected)
    assert parse_status_porcelain_v2(raw) == expected


def test_real_git_staged_modification_then_worktree_rename_uses_separate_rows(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    source = checkout / "compound-source.txt"
    target = checkout / "compound-target.txt"
    _commit_type2_source(checkout, source)
    source.write_text(
        _type2_source_content().replace("shared-000", "changed-000"),
        encoding="utf-8",
    )
    _git(checkout, "add", source.name)
    source.rename(target)
    _git(checkout, "add", "--intent-to-add", target.name)
    raw = _status_bytes(checkout)

    assert _type1_wire_records(raw) == ((b"M.", source.name.encode()),)
    assert _type2_wire_records(raw) == (
        (b".R", b"R100", target.name.encode(), source.name.encode()),
    )
    assert b"2 MR " not in raw
    assert parse_status_porcelain_v2(raw) == (
        StatusEntry("tracked", source.name),
        StatusEntry("renamed", target.name, source.name),
    )


def test_real_git_staged_rename_then_worktree_rename_uses_separate_rows(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    source = checkout / "compound-source.txt"
    middle = checkout / "compound-middle.txt"
    target = checkout / "compound-target.txt"
    _commit_type2_source(checkout, source)
    _git(checkout, "mv", source.name, middle.name)
    middle.rename(target)
    _git(checkout, "add", "--intent-to-add", target.name)
    raw = _status_bytes(checkout)

    assert _type2_wire_records(raw) == (
        (b"R.", b"R100", middle.name.encode(), source.name.encode()),
        (b".R", b"R100", target.name.encode(), middle.name.encode()),
    )
    assert b"2 RR " not in raw
    assert parse_status_porcelain_v2(raw) == (
        StatusEntry("renamed", middle.name, source.name),
        StatusEntry("renamed", target.name, middle.name),
    )


@pytest.mark.parametrize("score", [b"R0", b"R9", b"R99", b"R100", b"C75"])
def test_status_parser_accepts_canonical_rename_copy_score_range(score: bytes) -> None:
    oid = b"a" * 40
    raw = (
        b"2 "
        + score[:1]
        + b". N... 100644 100644 100644 "
        + oid
        + b" "
        + oid
        + b" "
        + score
        + b" new.txt\0old.txt\0"
    )

    assert parse_status_porcelain_v2(raw) == (
        StatusEntry("renamed", "new.txt", "old.txt"),
    )


@pytest.mark.parametrize(
    ("xy", "score"),
    [
        (b"R.", b"C75"),
        (b"RM", b"C75"),
        (b"RT", b"C75"),
        (b"RD", b"C75"),
        (b"C.", b"R75"),
        (b"CM", b"R75"),
        (b"CT", b"R75"),
        (b"CD", b"R75"),
        (b".R", b"C75"),
        (b".C", b"R75"),
    ],
)
def test_status_parser_rejects_symmetric_type2_marker_mismatches(
    xy: bytes,
    score: bytes,
) -> None:
    raw = _forged_type2_record(xy, score)

    with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
        parse_status_porcelain_v2(raw)


@pytest.mark.parametrize(
    "xy",
    [
        b"MR",
        b"TR",
        b"AR",
        b"DR",
        b"CR",
        b"MC",
        b"TC",
        b"AC",
        b"DC",
        b"RC",
        b"RR",
        b"CC",
        b"RA",
        b"CA",
    ],
)
def test_status_parser_rejects_forged_mixed_or_two_sided_type2_xy(xy: bytes) -> None:
    score = b"R100" if b"R" in xy else b"C75"

    with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
        parse_status_porcelain_v2(_forged_type2_record(xy, score))


@pytest.mark.parametrize(
    "score",
    [b"R00", b"R01", b"R000", b"R099", b"R101", b"C101", b"R-1", b"R", b"X75"],
)
def test_status_parser_rejects_noncanonical_or_out_of_range_scores(score: bytes) -> None:
    oid = b"a" * 40
    raw = (
        b"2 R. N... 100644 100644 100644 "
        + oid
        + b" "
        + oid
        + b" "
        + score
        + b" new.txt\0old.txt\0"
    )

    with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
        parse_status_porcelain_v2(raw)


def test_strict_status_parser_handles_tracked_rename_untracked_and_ignored() -> None:
    oid = b"a" * 40
    raw = (
        b"1 .M N... 100644 100644 100644 " + oid + b" " + oid + b" tracked.txt\0"
        b"2 R. N... 100644 100644 100644 "
        + oid
        + b" "
        + oid
        + b" R100 renamed.txt\0old.txt\0"
        b"? untracked.txt\0"
        b"! ignored/\0"
    )
    assert parse_status_porcelain_v2(raw) == (
        StatusEntry("tracked", "tracked.txt"),
        StatusEntry("renamed", "renamed.txt", "old.txt"),
        StatusEntry("untracked", "untracked.txt"),
        StatusEntry("ignored", "ignored"),
    )


def test_status_parser_rejects_impossible_command_specific_fields() -> None:
    oid = b"a" * 40
    ordinary = b" 100644 100644 100644 " + oid + b" " + oid + b" file.txt\0"
    rename = ordinary.removesuffix(b" file.txt\0") + b" R100 new.txt\0old.txt\0"
    unmerged = (
        b" 100644 100644 100644 100644 "
        + oid
        + b" "
        + oid
        + b" "
        + oid
        + b" file.txt\0"
    )
    malformed = (
        b"1 ?? N..." + ordinary,
        b"1 U. N..." + ordinary,
        b"1 R. N..." + ordinary,
        b"1 .M X..." + ordinary,
        b"1 .M N... 100664 100644 100644 " + oid + b" " + oid + b" file.txt\0",
        b"2 M. N..." + rename,
        b"2 MR N..." + rename,
        b"2 RR N..." + rename,
        b"2 R. N..." + rename.replace(b"R100", b"R101"),
        b"u M. N..." + unmerged,
    )

    for raw in malformed:
        with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
            parse_status_porcelain_v2(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"? ../escape\0",
        b"? /absolute\0",
        b"? .git/config\0",
        b"? duplicate\0? duplicate\0",
        b"? missing-terminator",
        b"x unknown\0",
        b"? \xff\0",
        b"? a\nname\0",
    ],
)
def test_status_parser_rejects_malformed_or_administrative_paths(raw: bytes) -> None:
    with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
        parse_status_porcelain_v2(raw)


def test_strict_ref_parser_binds_upstreams_and_rejects_duplicate_or_bad_oids() -> None:
    oid = "a" * 40
    raw = (
        f"refs/heads/aidt-prd\t{oid}\trefs/remotes/origin/aidt-prd\n"
        f"refs/remotes/origin/aidt-prd\t{oid}\t\n"
    ).encode()
    assert parse_ref_listing(raw) == (
        RefRecord("refs/heads/aidt-prd", oid, "refs/remotes/origin/aidt-prd"),
        RefRecord("refs/remotes/origin/aidt-prd", oid, None),
    )
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        parse_ref_listing(raw + raw.splitlines(keepends=True)[0])
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        parse_ref_listing(b"refs/heads/main\tBAD\t\n")
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        parse_ref_listing(f"refs/heads/main\t{oid}\t".encode())


def test_ref_parser_matches_representative_git_check_ref_format(
    tmp_path: Path,
) -> None:
    accepted = (
        "refs/heads/@",
        "refs/heads/main",
        "refs/heads/foo@bar",
        "refs/remotes/origin/fix/A20-1188",
        "refs/tags/release.v1",
    )
    rejected = (
        "refs/heads/.hidden",
        "refs/heads/foo.lock",
        "refs/heads/foo..bar",
        "refs/heads/foo@{bar",
        "refs/heads/foo bar",
        "refs/heads/foo~bar",
        "refs/heads/foo^bar",
        "refs/heads/foo:bar",
        "refs/heads/foo?bar",
        "refs/heads/foo*bar",
        "refs/heads/foo[bar",
        "refs/heads/foo\\bar",
        "/refs/heads/main",
        "refs/heads/main/",
        "refs//heads/main",
        "refs/heads/main.",
        "refs/heads/control\x7f",
    )
    oid = "a" * 40

    for ref in accepted:
        result = subprocess.run(("git", "check-ref-format", ref), cwd=tmp_path)
        assert result.returncode == 0, ref
        assert parse_ref_listing(f"{ref}\t{oid}\t\n".encode()) == (
            RefRecord(ref, oid, None),
        )
    for ref in rejected:
        result = subprocess.run(("git", "check-ref-format", ref), cwd=tmp_path)
        assert result.returncode != 0, ref
        with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
            parse_ref_listing(f"{ref}\t{oid}\t\n".encode())


def test_strict_worktree_parser_accepts_exact_records_and_rejects_mixed_shape(
    tmp_path: Path,
) -> None:
    first = (tmp_path / "service").resolve()
    second = (tmp_path / "ticket").resolve()
    oid = "a" * 40
    raw = (
        f"worktree {first}\0HEAD {oid}\0branch refs/heads/aidt-prd\0\0"
        f"worktree {second}\0HEAD {oid}\0branch refs/heads/fix/A20-1188\0locked\0\0"
    ).encode()
    assert parse_worktree_porcelain(raw) == (
        WorktreeRegistration(first, oid, "refs/heads/aidt-prd", False, False, False),
        WorktreeRegistration(second, oid, "refs/heads/fix/A20-1188", False, True, False),
    )
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        parse_worktree_porcelain(
            f"worktree {first}\0HEAD {oid}\0detached\0branch refs/heads/main\0\0".encode()
        )


def test_repository_snapshot_proves_dirty_index_tracked_untracked_and_ignored_content(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    (checkout / "tracked.txt").write_text("dirty-one\n", encoding="utf-8")
    (checkout / "untracked.txt").write_text("untracked-one\n", encoding="utf-8")
    ignored = checkout / "ignored"
    ignored.mkdir()
    (ignored / "data.txt").write_text("ignored-one\n", encoding="utf-8")
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "workspaces" / "A20-1188--viewer-api").resolve()

    before = observe_repository_state(
        identity,
        "s0",
        "b" * 64,
        "fix/A20-1188",
        workspace,
        "2026-07-21T01:02:03Z",
    )
    (checkout / "tracked.txt").write_text("dirty-two\n", encoding="utf-8")
    (ignored / "data.txt").write_text("ignored-two\n", encoding="utf-8")
    after = observe_repository_state(
        identity,
        "s0",
        "b" * 64,
        "fix/A20-1188",
        workspace,
        "2026-07-21T01:02:04Z",
    )

    assert type(before) is RepositoryState
    assert before.snapshot.root_status_digest == after.snapshot.root_status_digest
    assert before.snapshot.root_content_digest != after.snapshot.root_content_digest
    assert before.snapshot.root_content_count == 4
    assert before.snapshot.root_content_bytes > 0
    assert before.snapshot.base_ref_sha == _revision
    assert before.snapshot.target_ref_sha is None
    assert before.snapshot.target_registration_digest is None
    assert before.target_upstream is None


def test_ignored_content_proof_rejects_special_files_without_sampling(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    ignored = checkout / "ignored"
    ignored.mkdir()
    os.mkfifo(ignored / "unsafe.fifo")
    identity = observe_repository_identity(checkout, "viewer-api")

    with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
        observe_repository_state(
            identity,
            "s0",
            "b" * 64,
            "fix/A20-1188",
            (tmp_path / "ticket").resolve(),
            "2026-07-21T01:02:03Z",
        )


def test_ignored_directory_replacement_before_descriptor_open_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, _revision = _repository(tmp_path)
    ignored = checkout / "ignored"
    ignored.mkdir()
    (ignored / "state.txt").write_text("original\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "state.txt").write_text("replacement\n", encoding="utf-8")
    identity = observe_repository_identity(checkout, "viewer-api")
    original_open = os.open
    replaced = False

    def replace_before_open(path: object, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal replaced
        if Path(path) == ignored and flags & getattr(os, "O_DIRECTORY", 0) and not replaced:
            ignored.rename(tmp_path / "ignored-original")
            ignored.symlink_to(replacement, target_is_directory=True)
            replaced = True
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
        observe_repository_state(
            identity,
            "s0",
            "b" * 64,
            "fix/A20-1188",
            (tmp_path / "ticket").resolve(),
            "2026-07-21T01:02:03Z",
        )


def test_ignored_directory_replacement_during_fd_enumeration_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, _revision = _repository(tmp_path)
    ignored = checkout / "ignored"
    ignored.mkdir()
    (ignored / "state.txt").write_text("original\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    identity = observe_repository_identity(checkout, "viewer-api")
    original_scandir = os.scandir
    replaced = False

    def replace_during_scan(path: object) -> os.ScandirIterator[str]:
        nonlocal replaced
        if isinstance(path, int) and not replaced:
            ignored.rename(tmp_path / "ignored-original")
            ignored.symlink_to(replacement, target_is_directory=True)
            replaced = True
        return original_scandir(path)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(os, "scandir", replace_during_scan)
    with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
        observe_repository_state(
            identity,
            "s0",
            "b" * 64,
            "fix/A20-1188",
            (tmp_path / "ticket").resolve(),
            "2026-07-21T01:02:03Z",
        )


def test_ignored_directory_replacement_before_second_status_is_rejected(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    ignored = checkout / "ignored"
    ignored.mkdir()
    (ignored / "state.txt").write_text("same-size-a\n", encoding="utf-8")
    identity = observe_repository_identity(checkout, "viewer-api")
    status_calls = 0

    def replace_before_second_status(
        argv: tuple[str, ...],
        cwd: Path,
        environment: object,
        timeout: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> GitCommandResult:
        nonlocal status_calls
        if "status" in argv:
            status_calls += 1
            if status_calls == 2:
                ignored.rename(tmp_path / "ignored-original")
                ignored.mkdir()
                (ignored / "state.txt").write_text("same-size-b\n", encoding="utf-8")
        return default_binary_runner(
            argv,
            cwd,
            environment,  # type: ignore[arg-type]
            timeout,
            stdout_cap,
            stderr_cap,
        )

    with pytest.raises(AidtWorktreeFailure, match="content_invalid"):
        observe_repository_state(
            identity,
            "s0",
            "b" * 64,
            "fix/A20-1188",
            (tmp_path / "ticket").resolve(),
            "2026-07-21T01:02:03Z",
            runner=replace_before_second_status,
        )


def test_ignored_directory_cap_stops_at_cap_plus_one_before_materializing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, _revision = _repository(tmp_path)
    ignored = checkout / "ignored"
    ignored.mkdir()
    identity = observe_repository_identity(checkout, "viewer-api")
    original_scandir = os.scandir

    class FakeEntry:
        def __init__(self, index: int) -> None:
            self.name = f"entry-{index:05d}"

        def is_dir(self, *, follow_symlinks: bool) -> bool:
            assert follow_symlinks is False
            return False

    class CapPlusOneStream:
        index = 0

        def __enter__(self) -> CapPlusOneStream:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self) -> CapPlusOneStream:
            return self

        def __next__(self) -> FakeEntry:
            if self.index >= 10_000:
                raise AssertionError("scanner read beyond the cap-plus-one witness")
            entry = FakeEntry(self.index)
            self.index += 1
            return entry

    def bounded_scandir(path: object) -> object:
        if isinstance(path, int):
            return CapPlusOneStream()
        return original_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "scandir", bounded_scandir)
    with pytest.raises(AidtWorktreeFailure, match="cap_exceeded"):
        observe_repository_state(
            identity,
            "s0",
            "b" * 64,
            "fix/A20-1188",
            (tmp_path / "ticket").resolve(),
            "2026-07-21T01:02:03Z",
        )


class _ProductionFetchDouble:
    def __init__(self, revision: str, bare_fixture: Path) -> None:
        self.revision = revision
        self.bare_fixture = bare_fixture
        self.fetch_requests: list[tuple[tuple[str, ...], Path, dict[str, str], float, int, int]] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        environment: object,
        timeout: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> GitCommandResult:
        from symphony.aidt_worktree import default_binary_runner

        assert isinstance(environment, dict) or hasattr(environment, "items")
        if argv == FETCH_ARGV:
            fixture_revision = _git(
                self.bare_fixture, "rev-parse", "refs/heads/aidt-prd"
            ).stdout.decode().strip()
            assert fixture_revision == self.revision
            values = dict(environment)  # type: ignore[arg-type]
            self.fetch_requests.append((argv, cwd, values, timeout, stdout_cap, stderr_cap))
            _git(cwd, "update-ref", "refs/remotes/origin/aidt-prd", self.revision)
            return GitCommandResult(0, b"", b"")
        return default_binary_runner(
            argv, cwd, environment, timeout, stdout_cap, stderr_cap  # type: ignore[arg-type]
        )


def test_fetch_requests_only_production_vector_and_recomputes_binding_once(
    tmp_path: Path,
) -> None:
    checkout, old_revision = _repository(tmp_path)
    (checkout / "tracked.txt").write_text("next\n", encoding="utf-8")
    _git(checkout, "add", "tracked.txt")
    _git(checkout, "commit", "-m", "next")
    new_revision = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    bare_fixture = tmp_path / "fixture.git"
    _git(tmp_path, "clone", "--bare", str(checkout), str(bare_fixture))
    _git(checkout, "update-ref", "refs/remotes/origin/aidt-prd", old_revision)
    identity = observe_repository_identity(checkout, "viewer-api")
    expected_digest = "c" * 64
    calls: list[int] = []

    def observe_binding() -> SimpleNamespace:
        calls.append(1)
        return SimpleNamespace(
            checkout_revision=new_revision,
            repository_binding_digest=expected_digest,
        )

    runner = _ProductionFetchDouble(new_revision, bare_fixture)
    result = fetch_production_base(
        identity,
        identity.origin_digest,
        new_revision,
        expected_digest,
        observe_binding,
        runner=runner,
    )

    assert result == FetchResult(new_revision, expected_digest)
    assert calls == [1]
    assert len(runner.fetch_requests) == 1
    request = runner.fetch_requests[0]
    assert request[0] == FETCH_ARGV
    assert request[1] == checkout
    assert request[2] == dict(git_environment())
    assert request[3:] == (30.0, 1_048_576, 65_536)
    assert not any("file://" in item for item in request[0])
    verify_service_binding(new_revision, expected_digest, observe_binding)
    assert calls == [1, 1]


def test_real_add_and_plain_remove_have_exact_phase_deltas_and_preserve_root(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    (checkout / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (checkout / "untracked.txt").write_text("user state\n", encoding="utf-8")
    ignored = checkout / "ignored"
    ignored.mkdir()
    (ignored / "state.txt").write_text("ignored state\n", encoding="utf-8")
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "workspaces" / "A20-1188--viewer-api").resolve()
    workspace.parent.mkdir()
    binding = "d" * 64
    runner = _RecordingRunner()

    s0 = observe_repository_state(identity, "s0", binding, branch, workspace, "2026-07-21T01:02:03Z")
    s1 = observe_repository_state(identity, "s1", binding, branch, workspace, "2026-07-21T01:02:04Z")
    fetch_delta = validate_fetch_delta(s0, s1)
    assert len(fetch_delta) == 64
    assert classify_target_artifacts(identity, branch, workspace, revision) is TargetArtifactDisposition.ABSENT

    add_worktree(identity, branch, workspace, revision, runner=runner)
    s2 = observe_repository_state(identity, "s2", binding, branch, workspace, "2026-07-21T01:02:05Z")
    create_delta = validate_create_delta(s1, s2)
    ticket = observe_ticket_worktree(workspace, branch)

    assert len(create_delta) == 64
    assert type(ticket) is TicketWorktreeState
    assert ticket.head == revision
    assert ticket.branch == branch
    assert ticket.clean is True
    assert ticket.no_upstream is True
    assert base_is_ancestor(workspace, revision, ticket.head) is True
    assert classify_target_artifacts(identity, branch, workspace, revision) is TargetArtifactDisposition.EXACT

    cleanup_pre = observe_repository_state(
        identity, "cleanup_pre", binding, branch, workspace, "2026-07-21T01:02:06Z"
    )
    remove_worktree(identity, workspace, runner=runner)
    cleanup_post = observe_repository_state(
        identity, "cleanup_post", binding, branch, workspace, "2026-07-21T01:02:07Z"
    )
    remove_delta = validate_remove_delta(cleanup_pre, cleanup_post)

    assert len(remove_delta) == 64
    assert not workspace.exists()
    assert _git(checkout, "rev-parse", "--verify", f"refs/heads/{branch}").stdout.strip() == revision.encode()
    assert cleanup_post.snapshot.root_content_digest == s0.snapshot.root_content_digest
    mutations = [argv for argv in runner.argv if "worktree" in argv]
    assert mutations == [
        (
            "git",
            *FETCH_ARGV[1:-6],
            "worktree",
            "add",
            "--no-track",
            "-b",
            branch,
            str(workspace),
            revision,
        ),
        ("git", *FETCH_ARGV[1:-6], "worktree", "remove", str(workspace)),
    ]
    _assert_no_forbidden_commands(runner.argv)


@pytest.mark.parametrize("remote", ["origin", "backup"])
def test_remote_tracking_feature_ref_is_an_ambiguous_target(
    tmp_path: Path,
    remote: str,
) -> None:
    checkout, revision = _repository(tmp_path)
    branch = "fix/A20-1188"
    _git(checkout, "update-ref", f"refs/remotes/{remote}/{branch}", revision)
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()

    assert (
        classify_target_artifacts(identity, branch, workspace, revision)
        is TargetArtifactDisposition.AMBIGUOUS
    )


def test_nested_remote_feature_ref_blocks_classification_and_snapshot(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    branch = "fix/A20-1188"
    _git(
        checkout,
        "remote",
        "add",
        "team/origin",
        "https://fixture.invalid/team.git",
    )
    _git(
        checkout,
        "update-ref",
        f"refs/remotes/team/origin/{branch}",
        revision,
    )
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()

    assert (
        classify_target_artifacts(identity, branch, workspace, revision)
        is TargetArtifactDisposition.AMBIGUOUS
    )
    with pytest.raises(AidtWorktreeFailure, match="collision"):
        observe_repository_state(
            identity,
            "s0",
            "f" * 64,
            branch,
            workspace,
            "2026-07-21T01:02:03Z",
        )


@pytest.mark.parametrize(
    "unrelated",
    [
        "refs/remotes/team/origin/fix/A20-11880",
        "refs/remotes/team/origin/prefix-fix/A20-1188",
    ],
)
def test_nested_remote_unrelated_suffix_remains_noncolliding(
    tmp_path: Path,
    unrelated: str,
) -> None:
    checkout, revision = _repository(tmp_path)
    branch = "fix/A20-1188"
    _git(checkout, "update-ref", unrelated, revision)
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()

    assert (
        classify_target_artifacts(identity, branch, workspace, revision)
        is TargetArtifactDisposition.ABSENT
    )
    snapshot = observe_repository_state(
        identity,
        "s0",
        "f" * 64,
        branch,
        workspace,
        "2026-07-21T01:02:03Z",
    )
    assert snapshot.snapshot.target_ref_sha is None


def test_locked_target_is_rejected_by_create_and_remove_delta_proof(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    binding = "f" * 64
    s1 = observe_repository_state(
        identity, "s1", binding, branch, workspace, "2026-07-21T01:02:03Z"
    )
    add_worktree(identity, branch, workspace, revision)
    _git(checkout, "worktree", "lock", str(workspace))
    s2 = observe_repository_state(
        identity, "s2", binding, branch, workspace, "2026-07-21T01:02:04Z"
    )
    cleanup_pre = observe_repository_state(
        identity, "cleanup_pre", binding, branch, workspace, "2026-07-21T01:02:05Z"
    )

    with pytest.raises(AidtWorktreeFailure, match="collision"):
        validate_create_delta(s1, s2)

    _git(checkout, "worktree", "unlock", str(workspace))
    remove_worktree(identity, workspace)
    cleanup_post = observe_repository_state(
        identity, "cleanup_post", binding, branch, workspace, "2026-07-21T01:02:06Z"
    )
    with pytest.raises(AidtWorktreeFailure, match="identity_invalid"):
        validate_remove_delta(cleanup_pre, cleanup_post)


@pytest.mark.parametrize("unsafe", ["filter.evil.smudge", "core.sshCommand", "remote.origin.uploadpack"])
def test_add_rejects_executable_filter_or_transport_configuration_before_mutation(
    tmp_path: Path, unsafe: str
) -> None:
    checkout, revision = _repository(tmp_path)
    sentinel = tmp_path / "sentinel"
    _git(checkout, "config", unsafe, f"touch {sentinel}")
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()

    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        add_worktree(identity, "fix/A20-1188", workspace, revision)

    assert not workspace.exists()
    assert not sentinel.exists()


def test_add_rejects_executable_hook_before_mutation(tmp_path: Path) -> None:
    checkout, revision = _repository(tmp_path)
    hook = checkout / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\ntouch forbidden\n", encoding="utf-8")
    hook.chmod(0o755)
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()

    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        add_worktree(identity, "fix/A20-1188", workspace, revision)

    assert not workspace.exists()


def test_add_rejects_symlink_hooks_root_before_mutation(tmp_path: Path) -> None:
    checkout, revision = _repository(tmp_path)
    hooks = checkout / ".git" / "hooks"
    hooks.rename(checkout / ".git" / "hooks-original")
    empty_hooks = tmp_path / "empty-hooks"
    empty_hooks.mkdir()
    hooks.symlink_to(empty_hooks, target_is_directory=True)
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()

    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        add_worktree(identity, "fix/A20-1188", workspace, revision)

    assert not workspace.exists()


def test_hooks_root_replacement_before_descriptor_open_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, revision = _repository(tmp_path)
    hooks = checkout / ".git" / "hooks"
    replacement = tmp_path / "replacement-hooks"
    replacement.mkdir()
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()
    original_open = os.open
    replaced = False

    def replace_before_open(path: object, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal replaced
        if path == hooks and flags & getattr(os, "O_DIRECTORY", 0) and not replaced:
            hooks.rename(tmp_path / "hooks-original")
            hooks.symlink_to(replacement, target_is_directory=True)
            replaced = True
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        add_worktree(identity, "fix/A20-1188", workspace, revision)

    assert replaced is True
    assert not workspace.exists()


def test_hooks_root_replacement_during_descriptor_scan_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, revision = _repository(tmp_path)
    hooks = checkout / ".git" / "hooks"
    replacement = tmp_path / "replacement-hooks"
    replacement.mkdir()
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()
    original_scandir = os.scandir
    replaced = False

    def replace_during_scan(path: object) -> Any:
        nonlocal replaced
        scanning_hooks = isinstance(path, int) or path == hooks
        if scanning_hooks and not replaced:
            hooks.rename(tmp_path / "hooks-original")
            hooks.symlink_to(replacement, target_is_directory=True)
            replaced = True
        return original_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "scandir", replace_during_scan)
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        add_worktree(identity, "fix/A20-1188", workspace, revision)

    assert replaced is True
    assert not workspace.exists()


def test_hooks_cap_stops_at_cap_plus_one_before_materializing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, revision = _repository(tmp_path)
    hooks = checkout / ".git" / "hooks"
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()
    original_scandir = os.scandir

    class FakeHook:
        def __init__(self, index: int) -> None:
            self.name = f"hook-{index:04d}.sample"

        def stat(self, *, follow_symlinks: bool) -> SimpleNamespace:
            assert follow_symlinks is False
            return SimpleNamespace(st_mode=0o100644)

    class CapPlusOneHooks:
        index = 0

        def __enter__(self) -> CapPlusOneHooks:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self) -> CapPlusOneHooks:
            return self

        def __next__(self) -> FakeHook:
            if self.index >= 2_501:
                raise AssertionError("hooks scan read beyond cap-plus-one")
            entry = FakeHook(self.index)
            self.index += 1
            return entry

    def bounded_scandir(path: object) -> object:
        if isinstance(path, int) or path == hooks:
            return CapPlusOneHooks()
        return original_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "scandir", bounded_scandir)
    with pytest.raises(AidtWorktreeFailure, match="cap_exceeded"):
        add_worktree(identity, "fix/A20-1188", workspace, revision)

    assert not workspace.exists()


def test_phase_delta_rejects_same_status_content_mutation(tmp_path: Path) -> None:
    checkout, _revision = _repository(tmp_path)
    (checkout / "tracked.txt").write_text("dirty-one\n", encoding="utf-8")
    identity = observe_repository_identity(checkout, "viewer-api")
    workspace = (tmp_path / "ticket").resolve()
    before = observe_repository_state(
        identity, "s0", "e" * 64, "fix/A20-1188", workspace, "2026-07-21T01:02:03Z"
    )
    (checkout / "tracked.txt").write_text("dirty-two\n", encoding="utf-8")
    after = observe_repository_state(
        identity, "s1", "e" * 64, "fix/A20-1188", workspace, "2026-07-21T01:02:04Z"
    )

    assert before.snapshot.root_status_digest == after.snapshot.root_status_digest
    with pytest.raises(AidtWorktreeFailure, match="identity_invalid"):
        validate_fetch_delta(before, after)


def test_common_git_lock_serializes_two_processes(tmp_path: Path) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", "A20-1188--viewer-api")
    paths.locks.mkdir(parents=True)
    lock = common_git_lock_path(paths, "a" * 64)
    context = multiprocessing.get_context("spawn")
    first_entered = context.Event()
    first_release = context.Event()
    second_entered = context.Event()
    second_release = context.Event()
    first = context.Process(
        target=_hold_process_lock,
        args=(str(lock), first_entered, first_release, False),
    )
    second = context.Process(
        target=_hold_process_lock,
        args=(str(lock), second_entered, second_release, False),
    )

    first.start()
    assert first_entered.wait(3.0)
    second.start()
    time.sleep(0.2)
    assert not second_entered.is_set()
    first_release.set()
    assert second_entered.wait(3.0)
    second_release.set()
    first.join(3.0)
    second.join(3.0)
    assert first.exitcode == second.exitcode == 0


def test_common_git_lock_is_kernel_released_after_process_crash(tmp_path: Path) -> None:
    paths = stable_worktree_paths(tmp_path / "WORKFLOW.md", "A20-1188--viewer-api")
    paths.locks.mkdir(parents=True)
    lock = common_git_lock_path(paths, "b" * 64)
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_process_lock,
        args=(str(lock), entered, release, True),
    )

    process.start()
    assert entered.wait(3.0)
    process.join(3.0)
    assert process.exitcode == 23
    with advisory_lock(lock, timeout_seconds=0.5):
        assert lock.is_file()


def test_public_facade_keeps_git_state_lazy_until_requested() -> None:
    script = """
import sys
import symphony.aidt_worktree as package
assert 'symphony.aidt_worktree.git_state' not in sys.modules
assert package.FETCH_ARGV[-1].startswith('+refs/heads/aidt-prd:')
assert 'symphony.aidt_worktree.git_state' in sys.modules
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        (sys.executable, "-c", script),
        check=False,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_git_state_product_functions_stay_bounded_and_shallow() -> None:
    product = Path(__file__).parents[1] / "src/symphony/aidt_worktree/git_state.py"
    tree = ast.parse(product.read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert functions
    assert max(node.end_lineno - node.lineno + 1 for node in functions) <= 50
    assert max(_control_depth(node) for node in functions) <= 4
