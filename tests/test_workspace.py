"""SPEC §17.2 — workspace manager and safety invariants."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from symphony._shell import resolve_bash
from symphony.errors import InvalidWorkspaceCwd, SymphonyError
from symphony.workflow import HooksConfig
from symphony.workflow import build_service_config, load_workflow
from symphony.workspace import (
    WorkspaceManager,
    commit_workspace_on_done,
    validate_agent_cwd,
)


_HAS_GIT = shutil.which("git") is not None
_BASH = resolve_bash()


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        env={
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": os.environ.get("PATH", ""),
        },
    )


def _hooks(**overrides) -> HooksConfig:
    base = dict(
        after_create=None,
        before_run=None,
        after_run=None,
        before_remove=None,
        # Generous default — Git Bash on Windows takes 1–4 s for a cold
        # `bash -lc` startup; 2 s caused false-positive timeouts in CI.
        timeout_ms=30_000,
    )
    base.update(overrides)
    return HooksConfig(**base)


@pytest.mark.asyncio
async def test_create_and_reuse(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks())
    ws1 = await mgr.create_or_reuse("MT-1")
    assert ws1.created_now is True
    assert ws1.path.exists()
    ws2 = await mgr.create_or_reuse("MT-1")
    assert ws2.created_now is False
    assert ws2.path == ws1.path


@pytest.mark.asyncio
async def test_sanitization(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks())
    ws = await mgr.create_or_reuse("../escape")
    expected = (tmp_path / "ws" / ".._escape").resolve()
    assert ws.path == expected


@pytest.mark.asyncio
async def test_after_create_hook_runs_only_on_creation(tmp_path):
    # Hook writes into its own cwd (the workspace) using a relative path so
    # the assertion is independent of how bash on the host parses absolute
    # paths — MSYS bash on Windows mishandles drive-letter prefixes when
    # they're embedded in the script string.
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(after_create="echo created > marker"),
    )
    ws1 = await mgr.create_or_reuse("MT-2")
    marker = ws1.path / "marker"
    assert marker.exists()
    marker.unlink()
    await mgr.create_or_reuse("MT-2")
    assert not marker.exists()  # not re-run on reuse
    assert ws1.path.exists()


@pytest.mark.asyncio
async def test_after_create_hook_reruns_on_reuse_when_refresh_policy(tmp_path):
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(after_create="n=$(cat marker 2>/dev/null || echo 0); echo $((n + 1)) > marker"),
        reuse_policy="refresh",
    )
    ws1 = await mgr.create_or_reuse("MT-2")
    marker = ws1.path / "marker"
    assert marker.read_text(encoding="utf-8").strip() == "1"

    ws2 = await mgr.create_or_reuse("MT-2")

    assert ws2.created_now is False
    assert ws2.path == ws1.path
    assert marker.read_text(encoding="utf-8").strip() == "2"


@pytest.mark.asyncio
async def test_workspace_collision_blocks_before_after_create(tmp_path):
    root = tmp_path / "ws"
    workspace = root / "MT-COLLISION"
    workspace.mkdir(parents=True)
    owners = root / ".symphony-workspace-owners"
    owners.mkdir()
    (owners / "MT-COLLISION.json").write_text(
        json.dumps(
            {
                "version": 1,
                "workspace_key": "MT-COLLISION",
                "identity": {
                    "workflow_dir": str(tmp_path / "foreign-workflow"),
                    "repo_root": str(tmp_path / "foreign-repo"),
                },
            }
        ),
        encoding="utf-8",
    )
    current_workflow = tmp_path / "current-workflow"
    current_workflow.mkdir()
    mgr = WorkspaceManager(
        root,
        _hooks(after_create="echo should-not-run > hook-ran"),
        workflow_dir=current_workflow,
        reuse_policy="refresh",
    )

    with pytest.raises(SymphonyError) as exc_info:
        await mgr.create_or_reuse("MT-COLLISION")

    assert "workspace owner mismatch" in str(exc_info.value)
    assert not (workspace / "hook-ran").exists()


@pytest.mark.asyncio
async def test_workspace_board_root_collision_blocks_before_after_create(tmp_path):
    root = tmp_path / "ws"
    workspace = root / "MT-BOARD"
    workspace.mkdir(parents=True)
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    current_board = tmp_path / "current-board"
    current_board.mkdir()
    foreign_board = tmp_path / "foreign-board"
    foreign_board.mkdir()
    owners = root / ".symphony-workspace-owners"
    owners.mkdir()
    (owners / "MT-BOARD.json").write_text(
        json.dumps(
            {
                "version": 1,
                "workspace_key": "MT-BOARD",
                "identity": {
                    "workflow_dir": str(workflow.resolve()),
                    "board_root": str(foreign_board.resolve()),
                },
            }
        ),
        encoding="utf-8",
    )
    mgr = WorkspaceManager(
        root,
        _hooks(after_create="echo should-not-run > hook-ran"),
        workflow_dir=workflow,
        board_root=current_board,
        reuse_policy="refresh",
    )

    with pytest.raises(SymphonyError) as exc_info:
        await mgr.create_or_reuse("MT-BOARD")

    assert "workspace owner mismatch" in str(exc_info.value)
    assert "board_root" in str(exc_info.value)
    assert not (workspace / "hook-ran").exists()


@pytest.mark.asyncio
async def test_hook_failure_preserves_full_output_artifacts(tmp_path):
    root = tmp_path / "ws"
    full_stdout = "stdout-" + ("x" * 700)
    full_stderr = "stderr-" + ("y" * 700)
    mgr = WorkspaceManager(
        root,
        _hooks(
            after_create=(
                f"printf '{full_stdout}'; "
                f"printf '{full_stderr}' >&2; "
                "exit 7"
            )
        ),
    )

    with pytest.raises(SymphonyError) as exc_info:
        await mgr.create_or_reuse("MT-HOOK")

    meta_dir = root / ".symphony-workspace-hook-output" / "MT-HOOK"
    meta = next(meta_dir.glob("*.json"))
    payload = json.loads(meta.read_text(encoding="utf-8"))
    stdout_path = Path(payload["stdout"])
    stderr_path = Path(payload["stderr"])
    assert stdout_path.read_text(encoding="utf-8") == full_stdout
    assert stderr_path.read_text(encoding="utf-8") == full_stderr
    assert str(meta) in str(exc_info.value)
    assert not (root / "MT-HOOK").exists()


@pytest.mark.asyncio
async def test_hook_output_artifact_records_setup_failure_patterns(tmp_path):
    root = tmp_path / "ws"
    mgr = WorkspaceManager(
        root,
        _hooks(after_create="printf 'PrismaConfigEnvError: missing DATABASE_URL'"),
    )

    await mgr.create_or_reuse("MT-WARN")

    meta_dir = root / ".symphony-workspace-hook-output" / "MT-WARN"
    meta = next(meta_dir.glob("*.json"))
    payload = json.loads(meta.read_text(encoding="utf-8"))
    stdout_path = Path(payload["stdout"])
    assert stdout_path.read_text(encoding="utf-8") == (
        "PrismaConfigEnvError: missing DATABASE_URL"
    )
    assert payload["warning_patterns"] == ["PrismaConfigEnvError"]


@pytest.mark.asyncio
async def test_hook_timeout_writes_output_artifact(tmp_path):
    root = tmp_path / "ws"
    mgr = WorkspaceManager(
        root,
        _hooks(after_create="sleep 1", timeout_ms=100),
    )

    with pytest.raises(SymphonyError) as exc_info:
        await mgr.create_or_reuse("MT-TIMEOUT")

    meta_dir = root / ".symphony-workspace-hook-output" / "MT-TIMEOUT"
    meta = next(meta_dir.glob("*.json"))
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["returncode"] == -1
    assert str(meta) in str(exc_info.value)


@pytest.mark.asyncio
async def test_after_create_failure_aborts(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks(after_create="exit 7"))
    with pytest.raises(SymphonyError):
        await mgr.create_or_reuse("MT-3")
    # Partial directory cleaned up.
    assert not (tmp_path / "ws" / "MT-3").exists()


@pytest.mark.asyncio
async def test_after_create_failure_surfaces_stderr(tmp_path):
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(after_create="echo 'requires Python >=3.12,<3.13' >&2; exit 7"),
    )

    with pytest.raises(SymphonyError) as exc_info:
        await mgr.create_or_reuse("MT-3")

    message = str(exc_info.value)
    assert "hook after_create exited 7" in message
    assert "requires Python >=3.12,<3.13" in message


# Regression guard for the cross-platform symlink helper embedded in
# WORKFLOW.md / WORKFLOW.file.example.md / examples/WORKFLOW.smoke.md. On Windows
# Git Bash without admin/Developer Mode, `ln -s` silently copies the
# source; the agent's edits then never propagate to the host board and
# the tracker re-dispatches forever. The helper falls back to a Windows
# directory junction (mklink /J) which all programs treat as a real dir.
_LINK_DIR_HELPER = r"""
set -euo pipefail
_symphony_link_dir() {
  local target="$1" source="$2"
  rm -rf "$target"
  if [ "${OS:-}" = "Windows_NT" ] && command -v cmd.exe >/dev/null 2>&1; then
    # MSYS bash mangles backslashes inside `cmd.exe //c "..."` argument
    # strings (e.g. `\U` in `\Users` becomes garbled), so route through a
    # tiny .bat that takes %1/%2 — bat files receive properly quoted args
    # untouched. Also handles paths containing spaces.
    local target_win source_win bat bat_win
    target_win="$(cygpath -w "$(realpath -m "$target")")"
    source_win="$(cygpath -w "$source")"
    bat="${TEMP:-/tmp}/symphony-link-$$-$RANDOM.bat"
    printf '@echo off\r\nmklink /J %%1 %%2\r\n' > "$bat"
    bat_win="$(cygpath -w "$bat")"
    cmd.exe //c "$bat_win" "$target_win" "$source_win" >/dev/null
    rm -f "$bat"
  else
    ln -s "$source" "$target"
  fi
}
_symphony_link_dir "$TARGET_NAME" "$SOURCE_PATH"
"""


def test_symphony_link_dir_propagates_writes_back_to_source(tmp_path):
    """The after_create symlink helper must make agent writes inside the
    workspace appear in the host's board directory. Regression guard for
    the Windows-only silent-copy defect where `ln -s` left the workspace
    with an isolated real directory."""
    host = tmp_path / "host_repo"
    host.mkdir()
    board = host / "kanban_smoke"
    board.mkdir()
    (board / "DEMO-1.md").write_text("state: Todo\n", encoding="utf-8")

    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Pre-create the target as an empty directory (Symphony does this
    # before the hook runs, so the helper has to delete it first).
    (workspace / "kanban_smoke").mkdir()

    subprocess.run(
        [_BASH, "-lc", _LINK_DIR_HELPER],
        cwd=str(workspace),
        check=True,
        env={
            **os.environ,
            "TARGET_NAME": "kanban_smoke",
            "SOURCE_PATH": str(board),
        },
    )

    linked = workspace / "kanban_smoke"
    # Existing host file is visible through the link.
    assert (linked / "DEMO-1.md").read_text(encoding="utf-8") == "state: Todo\n"

    # Writes through the link reach the host board — this is the property
    # the silent-copy bug breaks.
    (linked / "DEMO-1.md").write_text("state: Done\n", encoding="utf-8")
    assert (board / "DEMO-1.md").read_text(encoding="utf-8") == "state: Done\n"

    # New file created through the link must also appear at the host.
    (linked / "DEMO-2.md").write_text("state: Todo\n", encoding="utf-8")
    assert (board / "DEMO-2.md").exists()


def test_setup_worktree_script_uses_pyproject_compatible_browser_env():
    script = (
        Path(__file__).parents[1]
        / "scripts"
        / "symphony-setup-worktree.sh"
    ).read_text(encoding="utf-8")

    assert "for candidate in python3.12 python3.13 python3.11 python3 python" in script
    assert "-e '.[dev,browser]'" in script


_SETUP_SCRIPT_POSIX_COMMANDS = (
    "bash",
    "basename",
    "chmod",
    "date",
    "dirname",
    "git",
    "grep",
    "ln",
    "mkdir",
    "rm",
    "sleep",
    "stat",
    "xargs",
)


def _setup_script_test_path(tmp_path: Path, *, include_flock: bool) -> str:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    commands = _SETUP_SCRIPT_POSIX_COMMANDS + (("flock",) if include_flock else ())
    for command in commands:
        source = shutil.which(command)
        if source is None and command == "flock":
            pytest.skip("flock CLI not available")
        assert source is not None
        (fakebin / command).symlink_to(source)

    fake_python = fakebin / "python3.12"
    fake_python.write_text(
        """#!/bin/sh
set -eu
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  mkdir -p "$3/bin"
  printf '#!/bin/sh\nexit 0\n' > "$3/bin/python"
  chmod +x "$3/bin/python"
fi
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    return str(fakebin)


@pytest.mark.skipif(os.name != "posix", reason="POSIX lock integration test")
@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.parametrize("lock_backend", ["mkdir", "flock"])
def test_setup_worktree_script_supports_linked_workflow_dir(tmp_path, lock_backend):
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    (primary / "kanban").mkdir()
    (primary / "kanban" / "DEMO-1.md").write_text("---\nstate: Todo\n---\n")
    _git(primary, "add", "-A")
    _git(primary, "commit", "-q", "-m", "seed")

    workflow_dir = tmp_path / "workflow"
    _git(primary, "worktree", "add", "-q", "-b", "operator", str(workflow_dir))
    assert (workflow_dir / ".git").is_file()

    workspace = tmp_path / "workspaces" / f"LINKED-{lock_backend.upper()}"
    workspace.mkdir(parents=True)
    repo_root = Path(__file__).parents[1]
    env = {
        **os.environ,
        "SYMPHONY_WORKFLOW_DIR": str(workflow_dir),
        "SYMPHONY_FEATURE_BASE_BRANCH": "operator",
        "PATH": _setup_script_test_path(
            tmp_path, include_flock=lock_backend == "flock"
        ),
    }

    subprocess.run(
        [_BASH, str(repo_root / "scripts" / "symphony-setup-worktree.sh")],
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )

    common_git_dir = Path(
        _git(workflow_dir, "rev-parse", "--git-common-dir").stdout.strip()
    )
    if not common_git_dir.is_absolute():
        common_git_dir = workflow_dir / common_git_dir
    common_git_dir = common_git_dir.resolve()
    assert (workspace / ".git").is_file()
    assert (workspace / "kanban").is_symlink()
    assert not (common_git_dir / "symphony-worktree.lock.d").exists()


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_file_workflow_after_create_hides_host_symlink_roots_from_git(tmp_path):
    """The file-tracker example links host kanban into the workspace.

    That link is workflow plumbing, not ticket output. docs/ stays as a real
    branch-local tree so review/QA artefacts merge back with the feature.
    """
    host = tmp_path / "host"
    host.mkdir()
    _git(host, "init", "-q", "-b", "main")
    (host / "kanban").mkdir()
    (host / "docs").mkdir()
    (host / "kanban" / "DEMO-1.md").write_text("---\nstate: Review\n---\n")
    (host / "docs" / "seed.md").write_text("seed\n")
    # C4 — the after_create hook now delegates to scripts/symphony-setup-worktree.sh
    # under SYMPHONY_WORKFLOW_DIR. Copy the canonical script into the synthetic
    # host so the hook can find it (real users have it next to WORKFLOW.md).
    import shutil as _shutil
    repo_root = Path(__file__).parents[1]
    (host / "scripts").mkdir()
    _shutil.copy2(
        repo_root / "scripts" / "symphony-setup-worktree.sh",
        host / "scripts" / "symphony-setup-worktree.sh",
    )
    (host / "scripts" / "symphony-setup-worktree.sh").chmod(0o755)
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", "seed")

    workflow = load_workflow(Path(__file__).parents[1] / "WORKFLOW.file.example.md")
    cfg = build_service_config(workflow)
    mgr = WorkspaceManager(
        tmp_path / "ws",
        cfg.hooks,
        workflow_dir=host,
        reuse_policy=cfg.workspace_reuse_policy,
    )

    ws = await mgr.create_or_reuse("DEMO-1")

    assert (ws.path / "kanban").is_symlink()
    assert not (ws.path / "docs").is_symlink()
    assert (ws.path / "docs").is_dir()
    assert _git(ws.path, "status", "--short").stdout == ""


@pytest.mark.skipif(os.name != "posix", reason="POSIX shell concurrency test")
@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
def test_setup_worktree_script_serializes_concurrent_git_admin_writes(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    _git(host, "init", "-q", "-b", "main")
    (host / "kanban").mkdir()
    (host / "kanban" / "DEMO-1.md").write_text("---\nstate: Todo\n---\n")

    import shutil as _shutil

    repo_root = Path(__file__).parents[1]
    (host / "scripts").mkdir()
    _shutil.copy2(
        repo_root / "scripts" / "symphony-setup-worktree.sh",
        host / "scripts" / "symphony-setup-worktree.sh",
    )
    script = host / "scripts" / "symphony-setup-worktree.sh"
    script.chmod(0o755)
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", "seed")

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake_python = fakebin / "python3.11"
    fake_python.write_text(
        """#!/bin/sh
set -eu
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  mkdir -p "$3/bin"
  cat > "$3/bin/python" <<'PY'
#!/bin/sh
exit 0
PY
  chmod +x "$3/bin/python"
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    env = {
        **os.environ,
        "SYMPHONY_WORKFLOW_DIR": str(host),
        "SYMPHONY_FEATURE_BASE_BRANCH": "main",
        "PATH": f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    processes = []
    for index in range(4):
        workspace = workspace_root / f"CONC-{index}"
        workspace.mkdir()
        processes.append(
            (
                workspace,
                subprocess.Popen(
                    [_BASH, str(script)],
                    cwd=str(workspace),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ),
            )
        )

    failures = []
    for workspace, proc in processes:
        stdout, stderr = proc.communicate(timeout=120)
        if proc.returncode != 0:
            failures.append((workspace.name, proc.returncode, stdout, stderr))
        assert "could not lock config file" not in stderr

    assert failures == []
    for workspace, _proc in processes:
        assert (workspace / ".git").exists()
        assert (workspace / "kanban").is_symlink()


@pytest.mark.asyncio
async def test_before_run_aborts_attempt(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks(before_run="exit 9"))
    ws = await mgr.create_or_reuse("MT-4")
    with pytest.raises(SymphonyError):
        await mgr.before_run(ws.path)


@pytest.mark.asyncio
async def test_after_run_failure_is_logged_and_ignored(tmp_path):
    mgr = WorkspaceManager(tmp_path / "ws", _hooks(after_run="exit 11"))
    ws = await mgr.create_or_reuse("MT-5")
    # Should not raise.
    await mgr.after_run_best_effort(ws.path)


@pytest.mark.asyncio
async def test_after_run_skipped_when_cwd_missing(tmp_path):
    """If the agent (or anything else) deletes the workspace before exit,
    after_run_best_effort must skip the hook silently rather than spawn
    bash with a missing cwd (which raises a noisy FileNotFoundError that
    the user cannot act on)."""
    mgr = WorkspaceManager(
        tmp_path / "ws", _hooks(after_run="echo should-not-run > marker")
    )
    ws = await mgr.create_or_reuse("MT-6")
    # Simulate post-agent deletion.
    import shutil as _shutil
    _shutil.rmtree(ws.path)
    # Should not raise; hook is skipped, no marker created elsewhere.
    await mgr.after_run_best_effort(ws.path)
    assert not ws.path.exists()


def test_validate_agent_cwd_rejects_outside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(InvalidWorkspaceCwd):
        validate_agent_cwd(outside, root)


def test_validate_agent_cwd_accepts_inside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "MT-1"
    inside.mkdir()
    validate_agent_cwd(inside, root)


@pytest.mark.asyncio
async def test_workflow_dir_env_exported(tmp_path):
    wf_dir = tmp_path / "host"
    wf_dir.mkdir()
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(after_create='echo "$SYMPHONY_WORKFLOW_DIR" > wfdir'),
        workflow_dir=wf_dir,
    )
    ws = await mgr.create_or_reuse("MT-ENV")
    content = (ws.path / "wfdir").read_text().strip()
    assert content == str(wf_dir)


@pytest.mark.asyncio
async def test_branch_policy_env_exported_to_after_create(tmp_path):
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(
            after_create=(
                'printf "%s\\n%s" "$SYMPHONY_FEATURE_BASE_BRANCH" '
                '"$SYMPHONY_MERGE_TARGET_BRANCH" > branch-env'
            )
        ),
        hook_env={
            "SYMPHONY_FEATURE_BASE_BRANCH": "dev",
            "SYMPHONY_MERGE_TARGET_BRANCH": "release",
        },
    )

    ws = await mgr.create_or_reuse("MT-BRANCH")

    assert (ws.path / "branch-env").read_text() == "dev\nrelease"


@pytest.mark.asyncio
async def test_branch_policy_env_is_scoped_to_after_create(tmp_path):
    mgr = WorkspaceManager(
        tmp_path / "ws",
        _hooks(before_run='printf "%s" "${SYMPHONY_FEATURE_BASE_BRANCH:-}" > before-env'),
        hook_env={"SYMPHONY_FEATURE_BASE_BRANCH": "dev"},
    )
    ws = await mgr.create_or_reuse("MT-BRANCH-SCOPE")

    await mgr.before_run(ws.path)

    assert (ws.path / "before-env").read_text() == ""


# ---------------------------------------------------------------------------
# auto-commit on Done — commit_workspace_on_done
# ---------------------------------------------------------------------------


def _git_id_env(monkeypatch, home):
    """Set per-test git author/committer + isolated HOME so commits don't
    pick up the developer's global ~/.gitconfig (sigstore signing, etc.)."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_initialises_fresh_repo(
    tmp_path, monkeypatch
):
    """Workspace with no .git ancestor: init + commit creates first revision."""
    _git_id_env(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "out.txt").write_text("hello")

    await commit_workspace_on_done(ws, identifier="OLV-1", title="setup db")

    assert (ws / ".git").is_dir()
    log = _git(ws, "log", "--oneline")
    assert "OLV-1: setup db" in log.stdout


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_reuses_parent_repo(
    tmp_path, monkeypatch
):
    """Workspace nested in an existing repo: commit lands there, no nested .git."""
    _git_id_env(monkeypatch, tmp_path)
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init", "-q", "-b", "main")
    (parent / "seed.txt").write_text("seed")
    _git(parent, "add", "-A")
    _git(parent, "commit", "-q", "-m", "seed")

    nested = parent / "ws"
    nested.mkdir()
    (nested / "out.txt").write_text("nested work")

    await commit_workspace_on_done(nested, identifier="OLV-2", title="nested")

    assert not (nested / ".git").exists()
    log = _git(parent, "log", "--oneline")
    assert "OLV-2: nested" in log.stdout


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_ignores_host_untracked(
    tmp_path, monkeypatch
):
    """When the workspace is nested in a host repo with unrelated untracked
    files, auto-commit must only snapshot the workspace tree. A prior
    smoke run on Windows discovered this surface: the file-tracker
    workspace at `tmp_workspaces/iso2/ISO-1/` lived inside the symphony
    repo, and `git add -A` (no pathspec) swept in every untracked file
    at the repo root — including unrelated drafts and config — bundling
    them into the ticket commit. Pin the scope to the workspace path."""
    _git_id_env(monkeypatch, tmp_path)
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init", "-q", "-b", "main")
    (parent / "seed.txt").write_text("seed")
    _git(parent, "add", "-A")
    _git(parent, "commit", "-q", "-m", "seed")

    # Unrelated untracked files at the host repo root — these MUST NOT
    # land in the auto-commit.
    (parent / "draft.md").write_text("operator draft, not a ticket artefact")
    (parent / "secret-notes.md").write_text("private")

    nested = parent / "tmp_workspaces" / "ISO-A"
    nested.mkdir(parents=True)
    (nested / "out.txt").write_text("the only thing this ticket produced")

    await commit_workspace_on_done(nested, identifier="ISO-A", title="scoped")

    tree = _git(parent, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    assert "tmp_workspaces/ISO-A/out.txt" in tree
    for leaked in ("draft.md", "secret-notes.md"):
        assert leaked not in tree, (
            f"{leaked!r} leaked into the auto-commit — `git add -A` must use "
            "the workspace pathspec, not the repo-wide default"
        )


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_skips_when_nothing_to_commit(
    tmp_path, monkeypatch
):
    """Empty workspace with init: helper logs and returns, no commit created."""
    _git_id_env(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()  # empty — no files to commit

    await commit_workspace_on_done(ws, identifier="OLV-3", title="empty")

    assert (ws / ".git").is_dir()
    # `git log` errors with exit 128 on a zero-commit repo (no HEAD yet),
    # so count revs instead — empty workspace must produce zero commits.
    count = _git(ws, "rev-list", "--all", "--count")
    assert count.stdout.strip() == "0"


@pytest.mark.asyncio
async def test_commit_workspace_on_done_missing_path_is_silent_noop(tmp_path):
    """Workspace already removed by hook/agent: helper must not raise."""
    missing = tmp_path / "gone"
    # Don't create it.
    await commit_workspace_on_done(missing, identifier="OLV-4", title="x")
    # No exception = pass.


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_tags_non_done_state(tmp_path, monkeypatch):
    """Non-Done state must appear in the commit subject so a quick `git log`
    makes obvious that the agent didn't reach Done before the snapshot."""
    _git_id_env(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "leftover.txt").write_text("agent left this behind")

    await commit_workspace_on_done(
        ws, identifier="OLV-5", title="cancelled mid-flight", state="Cancelled"
    )

    log = _git(ws, "log", "--oneline")
    assert "OLV-5: cancelled mid-flight [state: Cancelled]" in log.stdout


_AFTER_RUN_HOOK = r"""
set -uo pipefail
git add -A -- . ':(exclude).symphony' 2>/dev/null || true
if git diff --cached --quiet 2>/dev/null; then
  exit 0
fi
MSG="$(sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q;}' .symphony/commit-message.txt 2>/dev/null || true)"
[ -n "$MSG" ] || MSG="turn $(date -u +%FT%TZ)"
case "$MSG" in wip:*) COMMIT_MSG="$MSG" ;; *) COMMIT_MSG="wip: $MSG" ;; esac
LAST="$(git log -1 --format=%s 2>/dev/null || echo "")"
if [ "${LAST#wip:}" != "$LAST" ]; then
  git commit --amend -m "$COMMIT_MSG" >/dev/null 2>&1 || true
else
  git commit -m "$COMMIT_MSG" >/dev/null 2>&1 || true
fi
"""


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
def test_after_run_amend_keeps_branch_at_one_wip_commit(tmp_path, monkeypatch):
    """after_run runs after every turn: first turn creates a `wip:` commit,
    subsequent turns must amend it so the branch stays at exactly one
    commit-since-base. This is what makes the per-turn safety net
    compatible with the one-commit-per-ticket guarantee."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    import subprocess

    def _run_hook() -> None:
        subprocess.run(
            [_BASH, "-lc", _AFTER_RUN_HOOK],
            cwd=str(repo),
            check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        )

    # Turn 1 — first commit on branch.
    (repo / ".symphony").mkdir()
    (repo / ".symphony" / "commit-message.txt").write_text("feat: first slice")
    (repo / "t1.txt").write_text("turn1")
    _run_hook()
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "wip: feat: first slice"
    last1 = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Turn 2 — amend, no new commit. SHA changes but count stays.
    (repo / ".symphony" / "commit-message.txt").write_text("fix: second slice")
    (repo / "t2.txt").write_text("turn2")
    _run_hook()
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2", (
        "after_run must amend the prior wip commit, not stack new ones"
    )
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "wip: fix: second slice"
    last2 = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert last1 != last2, "amend should produce a new SHA"

    # Turn 3 — still amends.
    (repo / "t3.txt").write_text("turn3")
    _run_hook()
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2"

    # All three turn files captured in the single wip commit.
    files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    for fname in ("seed.txt", "t1.txt", "t2.txt", "t3.txt"):
        assert fname in files

    # And commit_workspace_on_done collapses that wip into a single named ticket commit.
    _git(repo, "config", "symphony.basesha", base_sha)

    import asyncio
    asyncio.run(commit_workspace_on_done(repo, identifier="OLV-AM", title="amend flow"))

    log = _git(repo, "log", "--oneline", "--format=%s").stdout.strip().splitlines()
    assert log == ["OLV-AM: amend flow", "base commit"], (
        f"expected base + 1 ticket commit, got {log!r}"
    )


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
def test_after_run_does_not_amend_agent_authored_commit(tmp_path, monkeypatch):
    """If the agent itself committed (subject doesn't start with `wip:`),
    after_run must NOT clobber the agent's message via --amend; it stacks
    a new `wip:` commit on top so the agent's intent stays in the squash."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")

    # Agent makes a deliberate commit mid-run.
    (repo / "feature.txt").write_text("agent's deliberate work")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: add feature X")

    # after_run picks up further uncommitted changes.
    (repo / "more.txt").write_text("more work")
    import subprocess
    subprocess.run(
        [_BASH, "-lc", _AFTER_RUN_HOOK],
        cwd=str(repo), check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )

    log = _git(repo, "log", "--oneline", "--format=%s").stdout.strip().splitlines()
    # Newest first: wip on top, then agent's feat, then base.
    assert log[0].startswith("wip:")
    assert log[1] == "feat: add feature X"
    assert log[2] == "base commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_squashes_to_recorded_base(
    tmp_path, monkeypatch
):
    """When `git config symphony.basesha` is set (the worktree-default
    after_create hook records this), commit_workspace_on_done must soft-
    reset to that fork point so all per-turn commits + uncommitted changes
    collapse into ONE ticket commit. Anything else breaks the
    'one-commit-per-ticket' guarantee operators rely on for clean merges."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")

    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Simulate after_create's record-the-fork-point step.
    _git(repo, "config", "symphony.basesha", base_sha)

    # Simulate per-turn agent activity: three commits accumulating on the
    # branch, plus uncommitted leftover changes at the end.
    (repo / "turn1.txt").write_text("t1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: turn 1")

    (repo / "turn2.txt").write_text("t2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: turn 2")

    (repo / "turn3.txt").write_text("t3")  # uncommitted leftover

    # Pre-state: HEAD is two commits past base + dirty working tree.
    pre_count = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert pre_count == "3"

    await commit_workspace_on_done(repo, identifier="OLV-SQ", title="squash demo")

    # After: branch has exactly base + 1 ticket commit, all turn files
    # captured in that single commit.
    post_count = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert post_count == "2", (
        f"expected base + 1 ticket commit, got {post_count} commits"
    )
    log = _git(repo, "log", "--oneline", "--format=%s").stdout.strip().splitlines()
    assert log[0] == "OLV-SQ: squash demo"
    assert log[1] == "base commit"
    # All three turn files must be present in the squashed commit.
    files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    for fname in ("seed.txt", "turn1.txt", "turn2.txt", "turn3.txt"):
        assert fname in files, f"{fname} missing from squashed commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_squashes_onto_merged_lineage(
    tmp_path, monkeypatch
):
    """When the ticket branch was already merged into the recorded merge
    target (Verify stage's `--no-ff` merge), the squash must land ON the
    merged tip, not reset all the way back to the original fork point.
    Resetting to the fork point rewrites the branch onto an orphan
    lineage: the post-Done fallback merge then computes a merge base at
    the fork point and hits guaranteed add/add conflicts on any file both
    sides touched after the merge (observed live: docs/changelog conflict
    demoted a healthy Done ticket to Blocked)."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "switch", "-c", "symphony/T-1")
    (repo / "app.txt").write_text("app work")
    (repo / "changelog.md").write_text("- did app work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: app work")
    c1_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "switch", "main")
    _git(repo, "merge", "--no-ff", "-m", "Merge symphony/T-1", "symphony/T-1")
    _git(repo, "switch", "symphony/T-1")

    (repo / "changelog.md").write_text("- did app work\n- learn notes\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: learn notes")
    (repo / "leftover.txt").write_text("uncommitted leftover")

    _git(repo, "config", "symphony.basesha", base_sha)
    _git(repo, "config", "symphony.mergetargetbranch", "main")

    await commit_workspace_on_done(repo, identifier="T-1", title="merged lineage")

    head_parent = _git(repo, "rev-parse", "HEAD^").stdout.strip()
    assert head_parent == c1_sha, (
        "squash must sit on the merged tip (C1), not reset past the merge "
        "back to the original fork point"
    )
    merge_base = _git(repo, "merge-base", "main", "HEAD").stdout.strip()
    assert merge_base == c1_sha
    subject = _git(repo, "log", "-1", "--format=%s").stdout.strip()
    assert subject == "T-1: merged lineage"
    files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    for fname in ("changelog.md", "leftover.txt"):
        assert fname in files, f"{fname} missing from squashed commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_noops_when_fully_merged_and_clean(
    tmp_path, monkeypatch
):
    """Once the ticket branch is fully merged into the recorded target and
    the worktree is clean, there is nothing left to snapshot — the helper
    must no-op rather than resetting past the merge and minting an orphan
    commit that duplicates work already on the target branch."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "switch", "-c", "symphony/T-2")
    (repo / "app.txt").write_text("app work")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: app work")
    c1_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "switch", "main")
    _git(repo, "merge", "--no-ff", "-m", "Merge symphony/T-2", "symphony/T-2")
    _git(repo, "switch", "symphony/T-2")

    _git(repo, "config", "symphony.basesha", base_sha)
    _git(repo, "config", "symphony.mergetargetbranch", "main")

    pre_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    pre_log = _git(repo, "log", "--format=%s").stdout.strip()
    assert pre_head == c1_sha

    await commit_workspace_on_done(repo, identifier="T-2", title="already merged")

    post_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    post_log = _git(repo, "log", "--format=%s").stdout.strip()
    assert post_head == pre_head, "must no-op, not mint an orphan commit"
    assert post_log == pre_log


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_keeps_base_when_target_recorded_but_never_merged(
    tmp_path, monkeypatch
):
    """A recorded merge target that hasn't actually received the branch yet
    (Verify never ran, or ran and failed) must not change base selection —
    today's plain fork-point squash stays exactly as before."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "switch", "-c", "symphony/T-3")
    (repo / "turn1.txt").write_text("t1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: turn 1")
    (repo / "turn2.txt").write_text("t2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: turn 2")
    (repo / "leftover.txt").write_text("uncommitted leftover")

    _git(repo, "config", "symphony.basesha", base_sha)
    _git(repo, "config", "symphony.mergetargetbranch", "main")

    await commit_workspace_on_done(repo, identifier="T-3", title="never merged")

    post_count = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert post_count == "2", f"expected base + 1 ticket commit, got {post_count}"
    log = _git(repo, "log", "--format=%s").stdout.strip().splitlines()
    assert log[0] == "T-3: never merged"
    assert log[1] == "base commit"
    files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    for fname in ("seed.txt", "turn1.txt", "turn2.txt", "leftover.txt"):
        assert fname in files, f"{fname} missing from squashed commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_refuses_protected_root_deletion(
    tmp_path, monkeypatch
):
    """A bad in-turn commit must not be squashed into a ticket commit when it
    deletes root files that define the repo's runtime contract."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (repo / "WORKFLOW.md").write_text("states: []\n")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "config", "symphony.basesha", base_sha)

    _git(repo, "rm", "-q", "pyproject.toml")
    (repo / "feature.txt").write_text("useful worker output")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: bad deletion")

    await commit_workspace_on_done(
        repo, identifier="OLV-GUARD", title="protect root files"
    )

    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == base_sha
    log = _git(repo, "log", "--format=%s").stdout.strip().splitlines()
    assert log == ["base commit"]
    status = _git(repo, "status", "--short").stdout
    assert "D  pyproject.toml" in status
    assert "A  feature.txt" in status


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_refuses_high_volume_deletion(
    tmp_path, monkeypatch
):
    """Mass deletion is treated as a corrupt worker snapshot, even when no
    single protected root file is involved."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    docs = repo / "docs"
    docs.mkdir()
    for i in range(26):
        (docs / f"old-{i}.md").write_text(f"old {i}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "config", "symphony.basesha", base_sha)

    for i in range(26):
        (docs / f"old-{i}.md").unlink()
    (repo / "feature.txt").write_text("useful worker output")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "wip: destructive delete")

    await commit_workspace_on_done(
        repo, identifier="OLV-GUARD2", title="refuse mass delete"
    )

    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == base_sha
    log = _git(repo, "log", "--format=%s").stdout.strip().splitlines()
    assert log == ["base commit"]


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_no_base_falls_back_to_plain_commit(
    tmp_path, monkeypatch
):
    """Legacy / non-worktree workspaces have no `symphony.basesha` recorded;
    helper must still commit (no squash) so existing setups don't regress."""
    _git_id_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")

    # No symphony.basesha config — simulate legacy workspace.
    (repo / "new.txt").write_text("uncommitted")

    await commit_workspace_on_done(repo, identifier="OLV-LEG", title="legacy")

    log = _git(repo, "log", "--oneline", "--format=%s").stdout.strip().splitlines()
    assert log[0] == "OLV-LEG: legacy"
    assert log[1] == "base commit"


@pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")
@pytest.mark.asyncio
async def test_commit_workspace_on_done_tags_abnormal_exit(tmp_path, monkeypatch):
    """When the worker died (reason != normal) and the state is still active,
    surface the exit reason in the subject."""
    _git_id_env(monkeypatch, tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "wip.txt").write_text("partial")

    await commit_workspace_on_done(
        ws,
        identifier="OLV-6",
        title="recovered work",
        exit_reason="reconcile_terminate_terminal",
        state="Done",  # Done state suppresses the suffix even when reason is non-normal
    )
    log = _git(ws, "log", "--oneline")
    assert "OLV-6: recovered work" in log.stdout
    assert "[state:" not in log.stdout
    assert "[exit:" not in log.stdout

    # Now without the Done state — exit reason should surface.
    (ws / "wip.txt").write_text("partial v2")
    await commit_workspace_on_done(
        ws,
        identifier="OLV-7",
        title="leftover",
        exit_reason="reconcile_terminate_terminal",
        state="In Progress",
    )
    log = _git(ws, "log", "--oneline")
    assert "OLV-7: leftover [state: In Progress]" in log.stdout
