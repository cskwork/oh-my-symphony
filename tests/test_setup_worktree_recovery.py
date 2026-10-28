"""Canonical setup must preserve shared data when refreshing retained worktrees."""
from __future__ import annotations

import os
import json
import shutil
from pathlib import Path
import subprocess
import sys

import pytest

from symphony._shell import resolve_bash
from symphony import _worktree_cleanup as cleanup_module
from tests.test_worktree_cleanup import _git, _link, _prepare

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src/symphony/templates/project/scripts/symphony-setup-worktree.sh"


def _setup_env(tmp_path, monkeypatch, workspace):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake = fakebin / "python3.12"
    fake.write_text(
        '#!/bin/sh\nset -eu\n'
        'test "$1 $2" = "-m venv"\n'
        'mkdir -p "$3/bin"\n'
        "printf '#!/bin/sh\\nexit 0\\n' > \"$3/bin/python\"\n"
        'chmod +x "$3/bin/python"\n', encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fakebin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    monkeypatch.setenv("SYMPHONY_PYTHON", sys.executable)
    monkeypatch.setenv("SYMPHONY_WORKSPACE_ROOT", str(workspace.parent))


def _trace_git(tmp_path, monkeypatch):
    trace = tmp_path / "git-events.jsonl"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    return trace


def _git_mutations(trace):
    calls = [json.loads(line).get("argv", []) for line in trace.read_text().splitlines()]
    return [args for args in calls if any(command in args for command in (
        "remove", "prune", "update-index", "update-ref", "add",
    ))]


def _run_setup(workspace):
    # Keep a real outer Bash in the workspace, matching the managed hook cwd.
    return subprocess.run(
        [resolve_bash(), "-c", 'bash "$1"', "setup-test", str(SCRIPT)],
        cwd=workspace, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("boards", [("kanban",), ("custom-board", "boards/release")])
def test_refresh_twice_preserves_shared_boards_and_external_target(tmp_path, monkeypatch, boards):
    host, _, workspace, external, expected = _prepare(tmp_path, monkeypatch, boards)
    _setup_env(tmp_path, monkeypatch, workspace)
    branch = _git(host, "rev-parse", "refs/heads/symphony/TASK-1").stdout
    for _ in range(2):
        result = _run_setup(workspace)
        assert result.returncode == 0, result.stdout + result.stderr
        assert expected == {name: (host / name).read_bytes() for name in expected}
        assert (external / "keep.bin").read_bytes() == b"preserve external bytes\x00\xff"
        assert _git(workspace, "symbolic-ref", "HEAD").stdout.strip() == "refs/heads/symphony/TASK-1"
        assert _git(host, "rev-parse", "refs/heads/symphony/TASK-1").stdout == branch
        assert cleanup_module._registered(host, workspace)
        assert (workspace / ".venv/bin/python").is_file()
        for name in boards:
            assert (workspace / name).resolve() == (host / name).resolve()
            assert _git(host, "ls-files", "-v", "--", f"{name}/TASK-1.md").stdout.startswith("S ")


def test_setup_refuses_wrong_second_board_before_first_link_or_git_mutation(tmp_path, monkeypatch):
    host, _, workspace, external, expected = _prepare(tmp_path, monkeypatch, ("kanban", "other"))
    _setup_env(tmp_path, monkeypatch, workspace)
    board = workspace / "other"
    board.rmdir() if board.is_junction() else board.unlink()
    _link(board, external)
    trace = _trace_git(tmp_path, monkeypatch)
    result = _run_setup(workspace)
    assert result.returncode != 0
    assert not _git_mutations(trace)
    assert (workspace / ".git").is_file()
    assert (workspace / "kanban").resolve() == (host / "kanban").resolve()
    assert board.resolve() == external.resolve()
    assert expected == {name: (host / name).read_bytes() for name in expected}


def _watch_preparation(monkeypatch):
    mutations = []
    run = subprocess.run

    def watch(args, **kwargs):
        if any(value in args for value in ("remove", "prune", "update-index", "update-ref", "add")):
            mutations.append(tuple(args))
        return run(args, **kwargs)

    monkeypatch.setattr(cleanup_module.subprocess, "run", watch)
    for method in ("unlink", "rmdir", "symlink_to"):
        original = getattr(Path, method)

        def watch_path(path, *args, _method=method, _original=original, **kwargs):
            mutations.append((_method, str(path)))
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, method, watch_path)
    return mutations


@pytest.mark.parametrize("exists", [False, True])
def test_fresh_preparation_leaves_empty_or_absent_target_untouched(tmp_path, monkeypatch, exists):
    host, _, workspace, _, expected = _prepare(tmp_path, monkeypatch)
    cleanup_module.cleanup(workspace)
    if exists:
        workspace.mkdir()
        identity = workspace.stat()
    mutations = _watch_preparation(monkeypatch)
    cleanup_module.prepare_setup(workspace, "symphony/TASK-1")
    assert not mutations
    assert workspace.exists() is exists
    if exists:
        assert workspace.stat().st_ino == identity.st_ino
        result = _run_setup_with_env(tmp_path, monkeypatch, workspace)
        assert result.returncode == 0, result.stderr
        assert cleanup_module._registered(host, workspace)
        assert expected == {name: (host / name).read_bytes() for name in expected}


def _run_setup_with_env(tmp_path, monkeypatch, workspace):
    _setup_env(tmp_path, monkeypatch, workspace)
    return _run_setup(workspace)


@pytest.mark.parametrize("empty", [False, True])
def test_stale_preparation_preserves_moved_bytes_and_reattaches(tmp_path, monkeypatch, empty):
    host, root, workspace, external, expected = _prepare(tmp_path, monkeypatch)
    moved = root / "moved retained data"
    assert moved.parent == workspace.parent == root
    workspace.rename(moved)
    (moved / "private.txt").write_bytes(b"retain moved workspace")
    if empty:
        workspace.mkdir()
        # Exact canonical hook under a real outer cwd: no rmdir of this empty target.
        result = _run_setup_with_env(tmp_path, monkeypatch, workspace)
        assert result.returncode == 0, result.stderr
    else:
        cleanup_module.prepare_setup(workspace, "symphony/TASK-1")
        assert not cleanup_module._registered(host, workspace)
        assert not workspace.exists()
        workspace.mkdir()
        result = _run_setup_with_env(tmp_path, monkeypatch, workspace)
        assert result.returncode == 0, result.stderr
    assert cleanup_module._registered(host, workspace)
    assert (moved / "private.txt").read_bytes() == b"retain moved workspace"
    assert (moved / ".git").is_file()
    assert expected == {name: (host / name).read_bytes() for name in expected}
    assert (external / "keep.bin").read_bytes() == b"preserve external bytes\x00\xff"


def test_stale_empty_default_prune_keeps_valid_locked_and_moved_worktree_bytes(tmp_path, monkeypatch):
    host, root, workspace, _, expected = _prepare(tmp_path, monkeypatch)
    others = {}
    for name in ("VALID", "LOCKED", "STALE"):
        path = root / name
        _git(host, "worktree", "add", "-b", name, str(path))
        (path / "keep.txt").write_bytes(name.encode())
        if name == "LOCKED":
            _git(host, "worktree", "lock", "--reason", "retain lock", str(path))
        if name != "VALID":
            moved = root / f"moved-{name}"
            assert moved.parent == path.parent == root
            path.rename(moved)
            others[name] = moved
        else:
            others[name] = path
    before = {record["branch"]: record for record in cleanup_module._worktrees(host)}
    moved = root / "moved-source"
    assert moved.parent == workspace.parent == root
    workspace.rename(moved)
    workspace.mkdir()
    result = _run_setup_with_env(tmp_path, monkeypatch, workspace)
    assert result.returncode == 0, result.stderr
    after = {record["branch"]: record for record in cleanup_module._worktrees(host)}
    for branch in ("refs/heads/main", "refs/heads/VALID", "refs/heads/LOCKED"):
        assert after[branch] == before[branch]
    assert "refs/heads/STALE" not in after
    assert "refs/heads/symphony/TASK-1" in after
    for name, path in others.items():
        assert (path / "keep.txt").read_bytes() == name.encode()
    assert (moved / ".git").is_file()
    assert expected == {name: (host / name).read_bytes() for name in expected}


@pytest.mark.parametrize("state", ["locked", "locked-stale", "wrong-branch", "nonempty-stale", "unregistered", "foreign", "wrong-common-dir", "root", "host", "outside", "linked-root"])
def test_preparation_refuses_unsafe_target_without_mutation(tmp_path, monkeypatch, state):
    host, root, workspace, _, expected = _prepare(tmp_path, monkeypatch)
    candidate = workspace
    if state in {"locked", "locked-stale"}:
        _git(host, "worktree", "lock", str(workspace))
    if state in {"locked-stale", "nonempty-stale"}:
        moved = root / "moved"
        assert moved.parent == workspace.parent == root
        workspace.rename(moved)
        workspace.mkdir()
        if state == "nonempty-stale":
            (workspace / "keep").write_bytes(b"not disposable")
    elif state == "wrong-branch":
        _git(workspace, "switch", "-c", "unexpected")
    elif state == "unregistered":
        candidate = root / "UNREGISTERED"
        candidate.mkdir()
        (candidate / "keep").write_bytes(b"not disposable")
    elif state in {"foreign", "wrong-common-dir"}:
        other = tmp_path / "foreign repo"
        other.mkdir()
        _git(other, "init", "-b", "main")
        (other / "keep").write_text("foreign")
        _git(other, "add", ".")
        _git(other, "commit", "-m", "foreign seed")
        candidate = root / "FOREIGN"
        _git(other, "worktree", "add", "-b", "symphony/FOREIGN", str(candidate))
        if state == "wrong-common-dir":
            (workspace / ".git").unlink()
            (workspace / ".git").write_bytes((candidate / ".git").read_bytes())
            candidate = workspace
    elif state == "root":
        candidate = root
    elif state == "host":
        candidate = host
    elif state == "outside":
        candidate = tmp_path / "outside"
        candidate.mkdir()
    elif state == "linked-root":
        candidate = root / "LINK"
        _link(candidate, workspace)
    before = _git(host, "worktree", "list", "--porcelain", "-z").stdout
    mutations = _watch_preparation(monkeypatch)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        cleanup_module.prepare_setup(candidate, "symphony/" + candidate.name)
    assert not mutations
    assert _git(host, "worktree", "list", "--porcelain", "-z").stdout == before
    assert expected == {name: (host / name).read_bytes() for name in expected}
    if state == "nonempty-stale":
        assert (workspace / "keep").read_bytes() == b"not disposable"


@pytest.mark.parametrize("shape", ["real", "wrong", "dangling", "absolute", "parent", "escaping-parent"])
def test_setup_validates_all_board_paths_before_mutation(tmp_path, monkeypatch, shape):
    host, _, workspace, external, expected = _prepare(tmp_path, monkeypatch, ("kanban", "other"))
    board = workspace / "other"
    board.rmdir() if board.is_junction() else board.unlink()
    if shape == "real":
        board.mkdir()
    elif shape in {"wrong", "dangling"}:
        _link(board, external)
        if shape == "dangling":
            (external / "keep.bin").unlink()
            external.rmdir()
    elif shape == "escaping-parent":
        _link(workspace / "escaped", external)
        monkeypatch.setenv("SYMPHONY_BOARD_ROOT_NAME", "kanban escaped/board")
    else:
        monkeypatch.setenv("SYMPHONY_BOARD_ROOT_NAME", "kanban " + (str(external) if shape == "absolute" else "../outside"))
    mutations = _watch_preparation(monkeypatch)
    with pytest.raises(RuntimeError):
        cleanup_module.prepare_setup(workspace, "symphony/TASK-1")
    assert not mutations
    assert (workspace / "kanban").resolve() == (host / "kanban").resolve()
    assert expected == {name: (host / name).read_bytes() for name in expected}


@pytest.mark.parametrize("failure", ["listing", "prune", "still-present", "post-prune-listing"])
def test_stale_preparation_failure_is_explicit_before_ref_or_add(tmp_path, monkeypatch, failure):
    host, root, workspace, _, _ = _prepare(tmp_path, monkeypatch)
    moved = root / "moved"
    assert moved.parent == workspace.parent == root
    workspace.rename(moved)
    workspace.mkdir()
    run = subprocess.run
    calls = []
    pruned = False

    def fail(args, **kwargs):
        nonlocal pruned
        calls.append(args)
        listing = "list" in args and "worktree" in args
        if listing and (failure == "listing" or (pruned and failure == "post-prune-listing")):
            raise subprocess.CalledProcessError(73, args)
        if "prune" in args:
            pruned = True
            if failure == "prune":
                raise subprocess.CalledProcessError(74, args)
            if failure == "still-present":
                return subprocess.CompletedProcess(args, 0)
        return run(args, **kwargs)

    monkeypatch.setattr(cleanup_module.subprocess, "run", fail)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        cleanup_module.prepare_setup(workspace, "symphony/TASK-1")
    assert not any(value in args for args in calls for value in ("remove", "update-index", "update-ref", "add"))
    assert workspace.is_dir() and not any(workspace.iterdir())
    assert (moved / ".git").is_file()


def test_other_checkout_ref_index_registration_and_files_survive_refusal(tmp_path, monkeypatch):
    host, root, workspace, _, _ = _prepare(tmp_path, monkeypatch)
    other = root / "OTHER"
    _git(host, "worktree", "move", str(workspace), str(other))
    # The feature tip is contained by an advanced main, so old ordering would CAS it.
    (host / "next.txt").write_text("target advanced")
    _git(host, "add", "next.txt")
    _git(host, "commit", "-m", "advance main")
    (other / "private.txt").write_bytes(b"unrelated untracked data")
    (other / "README.md").write_bytes(b"unrelated local edit")
    _git(other, "add", "README.md")
    sha = _git(host, "rev-parse", "refs/heads/symphony/TASK-1").stdout
    registration = _git(host, "worktree", "list", "--porcelain", "-z").stdout
    index_path = Path(_git(other, "rev-parse", "--git-path", "index").stdout.strip())
    if not index_path.is_absolute():
        index_path = other / index_path
    index_bytes = index_path.read_bytes()
    workspace.mkdir()
    trace = _trace_git(tmp_path, monkeypatch)
    result = _run_setup_with_env(tmp_path, monkeypatch, workspace)
    assert result.returncode != 0
    assert not _git_mutations(trace)
    assert "another worktree" in result.stderr
    assert _git(host, "rev-parse", "refs/heads/symphony/TASK-1").stdout == sha
    assert _git(host, "worktree", "list", "--porcelain", "-z").stdout == registration
    assert index_path.read_bytes() == index_bytes
    assert (other / "private.txt").read_bytes() == b"unrelated untracked data"
    assert (other / "README.md").read_bytes() == b"unrelated local edit"
    assert workspace.is_dir() and not any(workspace.iterdir())


def test_linear_no_board_setup_and_cleanup_are_compatible(tmp_path, monkeypatch):
    host, _, workspace, external, expected = _prepare(tmp_path, monkeypatch, ())
    monkeypatch.delenv("SYMPHONY_BOARD_ROOT_NAME", raising=False)
    _setup_env(tmp_path, monkeypatch, workspace)
    result = _run_setup(workspace)
    assert result.returncode == 0, result.stderr
    assert not (workspace / "kanban").exists()
    cleanup_module.cleanup(workspace)
    assert not cleanup_module._registered(host, workspace)
    assert expected == {name: (host / name).read_bytes() for name in expected}
    assert (external / "keep.bin").read_bytes() == b"preserve external bytes\x00\xff"


@pytest.mark.parametrize("failure", ["scan", "detach"])
def test_setup_scan_or_detach_failure_never_reaches_remove_ref_or_add(tmp_path, monkeypatch, failure):
    host, _, workspace, _, expected = _prepare(tmp_path, monkeypatch)
    mutations = _watch_preparation(monkeypatch)
    if failure == "scan":
        def deny_scan(*args, **kwargs):
            kwargs["onerror"](PermissionError("denied scan"))
            return iter(())
        monkeypatch.setattr(cleanup_module.os, "walk", deny_scan)
    else:
        board = workspace / "kanban"
        method = "rmdir" if board.is_junction() else "unlink"
        original = getattr(Path, method)
        def deny_detach(path, *args, **kwargs):
            if path == board:
                raise PermissionError("denied detach")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, method, deny_detach)
    with pytest.raises(PermissionError):
        cleanup_module.prepare_setup(workspace, "symphony/TASK-1")
    assert not any(value in call for call in mutations for value in ("remove", "prune", "update-ref", "add"))
    assert (workspace / "kanban").exists()
    assert (workspace / ".git").is_file()
    assert expected == {name: (host / name).read_bytes() for name in expected}


@pytest.mark.skipif(os.name != "posix", reason="controlled POSIX Git CAS wrapper")
@pytest.mark.parametrize("mode", ["retry", "exhausted", "target-drift"])
def test_setup_preserves_expected_old_cas_retry_and_captured_target(tmp_path, monkeypatch, mode):
    from tests.test_workspace import _setup_script_test_path

    host, _, workspace, _, _ = _prepare(tmp_path, monkeypatch, ())
    cleanup_module.cleanup(workspace)
    workspace.mkdir()
    original = _git(host, "rev-parse", "refs/heads/symphony/TASK-1").stdout.strip()
    _git(host, "commit", "--allow-empty", "-m", "target advanced")
    captured = _git(host, "rev-parse", "main").stdout.strip()
    _git(host, "commit", "--allow-empty", "-m", "later target")
    drift = _git(host, "rev-parse", "main").stdout.strip()
    _git(host, "update-ref", "refs/heads/main", captured, drift)
    real_git = shutil.which("git")
    fakebin = Path(_setup_script_test_path(tmp_path, include_flock=False))
    (fakebin / "git").unlink()
    (fakebin / "git").write_text(
        '#!/bin/sh\nset -eu\n'
        'if [ "$1" = "update-ref" ]; then\n'
        '  printf "%s\\n" "$*" >> "$SYMPHONY_CAS_LOG"\n'
        '  count=0; while IFS= read -r line; do count=$((count + 1)); done < "$SYMPHONY_CAS_LOG"\n'
        '  if [ "$SYMPHONY_CAS_MODE" = "exhausted" ] || { [ "$SYMPHONY_CAS_MODE" = "retry" ] && [ "$count" -lt 3 ]; }; then exit 1; fi\n'
        '  "$SYMPHONY_REAL_GIT" "$@"\n'
        '  if [ "$SYMPHONY_CAS_MODE" = "target-drift" ]; then\n'
        '    "$SYMPHONY_REAL_GIT" update-ref refs/heads/main "$SYMPHONY_DRIFT_SHA" "$SYMPHONY_CAPTURED_SHA"\n'
        '  fi\n'
        '  exit 0\n'
        'fi\nexec "$SYMPHONY_REAL_GIT" "$@"\n', encoding="utf-8",
    )
    (fakebin / "git").chmod(0o755)
    log = tmp_path / "cas-attempts"
    for key, value in {
        "PATH": str(fakebin), "PYTHONPATH": str(ROOT / "src"),
        "SYMPHONY_REAL_GIT": str(real_git), "SYMPHONY_CAS_LOG": str(log),
        "SYMPHONY_CAS_MODE": mode, "SYMPHONY_DRIFT_SHA": drift,
        "SYMPHONY_CAPTURED_SHA": captured,
    }.items():
        monkeypatch.setenv(key, value)
    trace = _trace_git(tmp_path, monkeypatch)
    result = _run_setup(workspace)
    attempts = log.read_text().splitlines()
    assert len(attempts) == (1 if mode == "target-drift" else 3)
    assert all(line == f"update-ref --no-deref refs/heads/symphony/TASK-1 {captured} {original}" for line in attempts)
    actual = _git(host, "rev-parse", "refs/heads/symphony/TASK-1").stdout.strip()
    if mode == "exhausted":
        assert result.returncode != 0
        assert actual == original
        assert not cleanup_module._registered(host, workspace)
        assert not any("add" in args for args in _git_mutations(trace))
    else:
        assert result.returncode == 0, result.stderr
        assert actual == captured
        assert _git(workspace, "rev-parse", "HEAD").stdout.strip() == captured
        assert _git(workspace, "config", "--worktree", "--get", "symphony.basesha").stdout.strip() == captured
    assert _git(host, "rev-parse", "main").stdout.strip() == (drift if mode == "target-drift" else captured)


@pytest.mark.parametrize("state", ["locked", "wrong-branch", "nonempty-stale", "real-default-board"])
def test_canonical_setup_invalid_target_has_no_git_or_link_mutations(tmp_path, monkeypatch, state):
    host, root, workspace, _, expected = _prepare(tmp_path, monkeypatch)
    _setup_env(tmp_path, monkeypatch, workspace)
    monkeypatch.delenv("SYMPHONY_BOARD_ROOT_NAME", raising=False)
    if state == "locked":
        _git(host, "worktree", "lock", str(workspace))
    elif state == "wrong-branch":
        _git(workspace, "switch", "-c", "unexpected")
    elif state == "nonempty-stale":
        moved = root / "moved"
        assert moved.parent == workspace.parent == root
        workspace.rename(moved)
        workspace.mkdir()
        (workspace / "keep").write_bytes(b"retain this data")
    else:
        board = workspace / "kanban"
        board.rmdir() if board.is_junction() else board.unlink()
        board.mkdir()
        (board / "keep").write_bytes(b"ambiguous local board")
    registration = _git(host, "worktree", "list", "--porcelain", "-z").stdout
    sha = _git(host, "rev-parse", "refs/heads/symphony/TASK-1").stdout
    index = (host / ".git/index").read_bytes()
    trace = _trace_git(tmp_path, monkeypatch)
    result = _run_setup(workspace)
    assert result.returncode != 0
    assert not _git_mutations(trace)
    assert _git(host, "worktree", "list", "--porcelain", "-z").stdout == registration
    assert _git(host, "rev-parse", "refs/heads/symphony/TASK-1").stdout == sha
    assert (host / ".git/index").read_bytes() == index
    assert expected == {name: (host / name).read_bytes() for name in expected}
    if state == "nonempty-stale":
        assert (workspace / "keep").read_bytes() == b"retain this data"
    elif state == "real-default-board":
        assert (workspace / "kanban/keep").read_bytes() == b"ambiguous local board"
    else:
        assert (workspace / "kanban").resolve() == (host / "kanban").resolve()
