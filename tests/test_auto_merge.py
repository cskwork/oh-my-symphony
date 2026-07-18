"""Tests for the builtin auto-merge-on-done feature."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from symphony._shell import resolve_bash
from symphony.utils.auto_merge import (
    AutoMergeResult,
    _build_script,
    auto_merge_on_done_best_effort,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )


def _git_output(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_bytes(cwd: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    ).stdout


def _capture_repo_state(repo: Path, paths: tuple[Path, ...]) -> dict[str, Any]:
    merge_head = repo / ".git" / "MERGE_HEAD"
    return {
        "head": _git_bytes(repo, "rev-parse", "HEAD"),
        "merge_head": merge_head.read_bytes() if merge_head.exists() else None,
        "status": _git_bytes(
            repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ),
        "cached": _git_bytes(repo, "diff", "--cached", "--binary", "--no-ext-diff"),
        "worktree": _git_bytes(repo, "diff", "--binary", "--no-ext-diff"),
        "files": {str(path): path.read_bytes() if path.exists() else None for path in paths},
    }


def _prepare_capture_repo(tmp_path: Path, ident: str) -> tuple[Path, Path, Path]:
    repo = _make_repo(tmp_path)
    capture = repo / "capture"
    capture.mkdir()
    tracked = capture / "tracked.txt"
    tracked.write_bytes(b"tracked baseline\n")
    (repo / ".gitignore").write_text("capture/*.ignored\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "capture/tracked.txt")
    _git(repo, "commit", "-q", "-m", "capture baseline")
    _make_symphony_branch(repo, ident, with_symlinks=False)
    tracked.write_bytes(b"operator dirty bytes\n")
    return repo, capture, tracked


def _prepend_git_wrapper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> None:
    real_git = shutil.which("git")
    assert real_git is not None
    wrapper_dir = tmp_path / "git-wrapper"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "git"
    wrapper.write_text(
        "#!/bin/sh\n" + body + f"\nexec {shlex.quote(real_git)} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{wrapper_dir}:{os.environ.get('PATH', '')}")
    bash_env = wrapper_dir / "bash-env"
    bash_env.write_text(
        f"export PATH={shlex.quote(str(wrapper_dir))}:\"$PATH\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BASH_ENV", str(bash_env))


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _add_bare_origin(repo: Path, tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return origin


def _make_symphony_branch(
    repo: Path, ident: str, *, with_symlinks: bool = True
) -> None:
    """Create a symphony/<ident> branch that mirrors what after_create produces:
    a real code change plus optional leaked workspace roots at kanban/docs."""
    _git(repo, "checkout", "-q", "-b", f"symphony/{ident}")
    (repo / "feature.py").write_text("print('hi')\n")
    _git(repo, "add", "feature.py")
    if with_symlinks:
        # Workspace symlink stand-ins — just regular files in the branch
        # for test purposes (we only need them to appear in the diff).
        (repo / "kanban").write_text("symlink-stand-in\n")
        (repo / "docs").write_text("symlink-stand-in\n")
        _git(repo, "add", "kanban", "docs")
    _git(repo, "commit", "-q", "-m", f"{ident}: feature + workspace")
    _git(repo, "checkout", "-q", "main")


def test_auto_merge_creates_no_ff_merge_commit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_symphony_branch(repo, "T-1", with_symlinks=False)

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-1",
            identifier="T-1",
            title="test feature",
            target_branch="main",
            exclude_paths=("kanban",),
        )
    )

    assert (repo / "feature.py").exists()
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "merge: T-1 from symphony/T-1" in log
    parents = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert len(parents) == 3, "auto merge must create an explicit merge commit"


def test_auto_merge_pushes_terminal_merge_to_target_upstream(tmp_path: Path) -> None:
    """Done must not leave the target branch ahead of its configured upstream.

    Reproduces a Learn-stage push followed by a late fallback commit: the
    terminal auto-merge creates a newer target commit after the worker's push.
    """
    repo = _make_repo(tmp_path)
    origin = _add_bare_origin(repo, tmp_path)
    _make_symphony_branch(repo, "T-LATE", with_symlinks=False)

    result = asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-LATE",
            identifier="T-LATE",
            title="late terminal evidence",
            target_branch="main",
            exclude_paths=(),
        )
    )

    local_sha = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    remote_sha = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=str(origin),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert result.ok is True
    assert remote_sha == local_sha, (
        "Done must prove the terminal merge reached upstream"
    )


def test_auto_merge_reports_target_push_failure(tmp_path: Path) -> None:
    """A rejected terminal push must fail the Done merge gate."""
    repo = _make_repo(tmp_path)
    origin = _add_bare_origin(repo, tmp_path)
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _make_symphony_branch(repo, "T-REJECT", with_symlinks=False)

    result = asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-REJECT",
            identifier="T-REJECT",
            title="rejected terminal push",
            target_branch="main",
            exclude_paths=(),
        )
    )

    assert result.ok is False
    assert result.status == "push_failed"


@pytest.mark.parametrize(
    "capture_dirname",
    [None, "empty-capture"],
    ids=["no-capture-preflight-noop", "empty-capture-staged-noop"],
)
def test_retry_after_push_failure_retries_rejected_push_until_upstream_matches(
    tmp_path: Path,
    capture_dirname: str | None,
) -> None:
    repo = _make_repo(tmp_path)
    origin = _add_bare_origin(repo, tmp_path)
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _make_symphony_branch(repo, "T-RETRY", with_symlinks=False)
    capture_untracked: tuple[str, ...] = ()
    expected_retry_message = "SKIP: nothing differs"
    if capture_dirname is not None:
        (repo / capture_dirname).mkdir()
        capture_untracked = (capture_dirname,)
        expected_retry_message = "SKIP: nothing staged after merge"

    def run_merge() -> AutoMergeResult:
        return asyncio.run(
            auto_merge_on_done_best_effort(
                workflow_dir=repo,
                branch="symphony/T-RETRY",
                identifier="T-RETRY",
                title="retry rejected terminal push",
                target_branch="main",
                exclude_paths=(),
                capture_untracked=capture_untracked,
            )
        )

    first = run_merge()
    local_merge_sha = _git_output(repo, "rev-parse", "main")
    remote_sha = _git_output(origin, "rev-parse", "main")
    merge_count = _git_output(repo, "rev-list", "--merges", "--count", "main")
    assert first.ok is False
    assert first.status == "push_failed"
    assert remote_sha != local_merge_sha
    assert merge_count == "1"

    second = run_merge()
    assert second.ok is False
    assert second.status == "push_failed"
    assert _git_output(repo, "rev-parse", "main") == local_merge_sha
    assert _git_output(origin, "rev-parse", "main") != local_merge_sha
    assert (
        _git_output(repo, "rev-list", "--merges", "--count", "main")
        == merge_count
    )

    hook.unlink()
    third = run_merge()
    local_sha = _git_output(repo, "rev-parse", "main")
    remote_sha = _git_output(origin, "rev-parse", "main")
    assert third.ok is True
    assert third.status == "nothing_to_apply"
    assert expected_retry_message in third.detail
    assert local_sha == local_merge_sha
    assert remote_sha == local_sha
    assert (
        _git_output(repo, "rev-list", "--merges", "--count", "main")
        == merge_count
    )


def test_auto_merge_reports_remote_verification_mismatch(tmp_path: Path) -> None:
    """A successful push is insufficient when the remote ref does not stick."""
    repo = _make_repo(tmp_path)
    origin = _add_bare_origin(repo, tmp_path)
    hook = origin / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read old new ref; do\n"
        '  git update-ref "$ref" "$old" "$new"\n'
        "done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _make_symphony_branch(repo, "T-REWIND", with_symlinks=False)

    result = asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-REWIND",
            identifier="T-REWIND",
            title="rewound terminal push",
            target_branch="main",
            exclude_paths=(),
        )
    )

    assert result.ok is False
    assert result.status == "remote_verify_failed"


def test_auto_merge_skips_when_host_dirty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "symphony/T-2")
    (repo / "README.md").write_text("branch change\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "T-2: modify readme")
    _git(repo, "checkout", "-q", "main")
    # make host dirty on the same path the branch changes
    (repo / "README.md").write_text("modified\n")

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-2",
            identifier="T-2",
            title="should skip",
            target_branch="main",
            exclude_paths=(),
        )
    )

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_before == head_after, "skip on dirty host must not create commit"


def test_auto_merge_reports_real_conflict_before_dirty_overlap(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "symphony/T-CONFLICT")
    (repo / "README.md").write_text("branch change\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "T-CONFLICT: modify readme")
    _git(repo, "checkout", "-q", "main")
    (repo / "README.md").write_text("target change\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "main: modify readme")

    # Operator also has a dirty local edit on the same path. The merge gate
    # must still report the committed target/branch conflict first; otherwise
    # agents block on "dirty worktree" and miss the real integration work.
    (repo / "README.md").write_text("operator scratch\n")

    result = subprocess.run(
        [
            resolve_bash(),
            "-lc",
            _build_script(
                branch="symphony/T-CONFLICT",
                target="main",
                identifier="T-CONFLICT",
                title="conflict should surface",
                excludes=(),
            ),
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 50
    assert "CONFLICT" in output
    assert "SKIP: host tracked changes overlap branch merge" not in output


def test_auto_merge_allows_non_overlapping_host_dirty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_symphony_branch(repo, "T-2B", with_symlinks=False)
    (repo / "local-note.txt").write_text("operator scratch\n")

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-2B",
            identifier="T-2B",
            title="should merge",
            target_branch="main",
            exclude_paths=(),
        )
    )

    assert (repo / "feature.py").exists()
    assert (repo / "local-note.txt").read_text() == "operator scratch\n"
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "?? local-note.txt" in status


def test_auto_merge_skips_missing_branch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/does-not-exist",
            identifier="T-X",
            title="missing",
            target_branch="main",
            exclude_paths=(),
        )
    )

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_before == head_after


def test_auto_merge_uses_current_branch_when_target_empty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "dev")
    _make_symphony_branch(repo, "T-3", with_symlinks=False)
    _git(repo, "checkout", "-q", "dev")

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-3",
            identifier="T-3",
            title="auto-pick branch",
            target_branch="",  # empty -> current
            exclude_paths=(),
        )
    )

    # commit landed on dev, not main
    dev_head = subprocess.run(
        ["git", "log", "--oneline", "-1", "dev"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    main_head = subprocess.run(
        ["git", "log", "--oneline", "-1", "main"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "merge: T-3" in dev_head
    assert "merge: T-3" not in main_head


def test_auto_merge_blocks_when_excluded_root_changed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    # Branch only adds a path that we then exclude entirely.
    _git(repo, "checkout", "-q", "-b", "symphony/T-4")
    (repo / "kanban").write_text("only-this\n")
    _git(repo, "add", "kanban")
    _git(repo, "commit", "-q", "-m", "T-4: only workspace")
    _git(repo, "checkout", "-q", "main")

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-4",
            identifier="T-4",
            title="all excluded",
            target_branch="main",
            exclude_paths=("kanban",),
        )
    )

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_before == head_after
    assert not (repo / "kanban").exists()


def test_auto_merge_captures_untracked_paths(tmp_path: Path) -> None:
    """Opt-in capture: a host-side untracked file under `docs-host/` should
    land in the same merge commit, even when the branch-side `docs` blob
    is excluded. This closes the after_create-symlink gap where docs are
    written via symlink into the host repo and never appear in the
    symphony/<ID> branch diff."""
    repo = _make_repo(tmp_path)
    _make_symphony_branch(repo, "T-5", with_symlinks=False)

    # Simulate what an agent does when writing through an after_create
    # symlink: a real file lands in the host repo's docs-host/ directory
    # as untracked content, never staged on the symphony/<ID> branch.
    docs_dir = repo / "docs-host"
    docs_dir.mkdir()
    (docs_dir / "note.md").write_text("agent wrote this via symlink\n")

    asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-5",
            identifier="T-5",
            title="capture host untracked",
            target_branch="main",
            exclude_paths=("kanban",),
            capture_untracked=("docs-host",),
        )
    )

    # feature.py was applied as before
    assert (repo / "feature.py").exists()
    # host-side untracked note got captured into the same commit
    assert (repo / "docs-host" / "note.md").exists()
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert "docs-host/note.md" in tree
    assert "feature.py" in tree
    # And the commit is the auto-merge commit, not a stray prior one.
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "merge: T-5 from symphony/T-5" in log


@pytest.mark.parametrize(
    ("identifier", "excluded_root", "changed_path", "blocked"),
    [
        ("T-ROOT", "kanban", "kanban", True),
        ("T-DESC", "kanban", "kanban/T-DESC.md", True),
        ("T-META", "work[1]", "work[1]/ticket.md", True),
        ("T-ODD", "odd\tline\nroot", "odd\tline\nroot/ticket.md", True),
        ("T-PREFIX", "kanban", "kanban-copy/T-PREFIX.md", False),
    ],
    ids=["root", "descendant", "regex-metachar", "tab-newline", "prefix-near-miss"],
)
def test_auto_merge_exclusions_use_literal_pathspec_boundaries(
    tmp_path: Path,
    identifier: str,
    excluded_root: str,
    changed_path: str,
    blocked: bool,
) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", f"symphony/{identifier}")
    changed = repo / changed_path
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_bytes(b"branch bytes\n")
    _git(repo, "add", "--", changed_path)
    _git(repo, "commit", "-q", "-m", f"{identifier}: path boundary")
    _git(repo, "checkout", "-q", "main")
    head_before = _git_output(repo, "rev-parse", "HEAD")

    result = asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch=f"symphony/{identifier}",
            identifier=identifier,
            title="literal exclusion boundary",
            target_branch="main",
            exclude_paths=(excluded_root,),
        )
    )

    if blocked:
        assert result.status == "excluded_paths"
        assert "BLOCK: branch changed excluded workspace roots:" in result.detail
        assert _git_output(repo, "rev-parse", "HEAD") == head_before
        assert not changed.exists()
    else:
        assert result.ok is True
        assert changed.read_bytes() == b"branch bytes\n"


def test_auto_merge_capture_stages_only_untracked_literal_paths(tmp_path: Path) -> None:
    repo, capture, tracked = _prepare_capture_repo(tmp_path, "T-CAPTURE")
    unusual = (
        capture / "space name.txt",
        capture / "tab\tname.txt",
        capture / "line\nname.txt",
    )
    for index, path in enumerate(unusual):
        path.write_bytes(f"artifact-{index}\n".encode())
    ignored = capture / "secret.ignored"
    ignored.write_bytes(b"ignored operator bytes\n")

    result = asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-CAPTURE",
            identifier="T-CAPTURE",
            title="capture untracked only",
            target_branch="main",
            exclude_paths=(),
            capture_untracked=("capture",),
        )
    )

    tree_paths = set(_git_bytes(repo, "ls-tree", "-rz", "--name-only", "HEAD").split(b"\0"))
    assert result.ok is True
    assert {os.fsencode(str(path.relative_to(repo))) for path in unusual} <= tree_paths
    assert b"capture/secret.ignored" not in tree_paths
    assert ignored.read_bytes() == b"ignored operator bytes\n"
    assert _git_output(repo, "show", "HEAD:capture/tracked.txt") == "tracked baseline"
    assert tracked.read_bytes() == b"operator dirty bytes\n"
    assert _git_bytes(repo, "diff", "--cached") == b""


def test_auto_merge_partial_capture_add_failure_restores_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, capture, tracked = _prepare_capture_repo(tmp_path, "T-PARTIAL")
    first = capture / "a.txt"
    second = capture / "b.txt"
    first.write_bytes(b"first bytes\n")
    second.write_bytes(b"second bytes\n")
    before = _capture_repo_state(repo, (tracked, first, second))
    marker = tmp_path / "partial-add-fired"
    real_git = shutil.which("git")
    assert real_git is not None
    wrapper_body = (
        'case " $* " in\n'
        '  *" add "*)\n'
        f"    if [ ! -e {shlex.quote(str(marker))} ]; then\n"
        f"      : > {shlex.quote(str(marker))}\n"
        f"      {shlex.quote(real_git)} --literal-pathspecs add -- "
        f"{shlex.quote(str(first.relative_to(repo)))}\n"
        "      exit 86\n"
        "    fi\n"
        "    ;;\n"
        "esac"
    )
    _prepend_git_wrapper(monkeypatch, tmp_path, wrapper_body)

    result = asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-PARTIAL",
            identifier="T-PARTIAL",
            title="partial capture failure",
            target_branch="main",
            exclude_paths=(),
            capture_untracked=("capture",),
        )
    )

    assert result.ok is False
    assert result.status == "git_failed"
    assert marker.exists()
    assert _capture_repo_state(repo, (tracked, first, second)) == before


def test_auto_merge_commit_hook_failure_restores_captured_files(tmp_path: Path) -> None:
    repo, capture, tracked = _prepare_capture_repo(tmp_path, "T-HOOK")
    first = capture / "space name.txt"
    second = capture / "line\nname.txt"
    first.write_bytes(b"first bytes\n")
    second.write_bytes(b"second bytes\n")
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    before = _capture_repo_state(repo, (tracked, first, second))

    result = asyncio.run(
        auto_merge_on_done_best_effort(
            workflow_dir=repo,
            branch="symphony/T-HOOK",
            identifier="T-HOOK",
            title="commit hook failure",
            target_branch="main",
            exclude_paths=(),
            capture_untracked=("capture",),
        )
    )

    assert result.ok is False
    assert result.status == "commit_failed"
    assert _capture_repo_state(repo, (tracked, first, second)) == before
