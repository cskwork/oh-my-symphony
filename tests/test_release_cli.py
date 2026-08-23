"""Command-line surface for machine release validation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from symphony.cli import release

from tests.test_release_contracts import _git, _write_valid_release
from tests._win_skips import requires_symlink_privilege


def _repo(
    tmp_path: Path,
    *,
    board_name: str = "kanban",
    board_root_value: str | None = None,
) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.test")
    # Pin the repo to the line-ending behavior CI runs with: a host-wide
    # `core.autocrlf=true` (common on Windows) rewrites blobs/worktrees and
    # breaks the byte-exact contract comparisons these tests assert on.
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "branch", "symphony/APP-1")
    board = repo / board_name
    board.mkdir()
    board_root_config = board_root_value or f"./{board_name}"
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        """---
tracker:
  kind: file
  board_root: __BOARD_ROOT__
  active_states: [Build, Verify, Document]
  terminal_states: [Done, Blocked]
workspace: { root: ./workspaces }
agent:
  kind: codex
  auto_merge_target_branch: main
codex: { command: codex app-server }
---
Release workflow.
""".replace("__BOARD_ROOT__", board_root_config),
        encoding="utf-8",
    )
    _git(repo, "add", "WORKFLOW.md")
    _git(repo, "commit", "-m", "release workflow")
    _write_valid_release(repo)
    workspace = tmp_path / "workspace"
    _git(
        repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "symphony/RELEASE-CLI-WORKSPACE",
        str(workspace),
        "main",
    )
    shutil.copytree(repo / "docs", workspace / "docs", dirs_exist_ok=True)
    (workspace / board_name).symlink_to(board, target_is_directory=True)
    return repo, workflow, workspace


@requires_symlink_privilege
def test_release_check_json_passes_current_target(
    tmp_path: Path, capsys
) -> None:
    repo, workflow, workspace = _repo(tmp_path)

    rc = release.main(
        [
            "check",
            str(workflow),
            "--ticket",
            "VERIFY-1",
            "--workspace",
            str(workspace),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["passed"] is True
    assert payload["target_sha"] == _git(repo, "rev-parse", "main")


@requires_symlink_privilege
def test_release_check_accepts_copied_workflow_with_host_board_mount(
    tmp_path: Path, capsys
) -> None:
    repo, workflow, workspace = _repo(tmp_path)
    shutil.copy2(workflow, workspace / "WORKFLOW.md")

    rc = release.main(
        [
            "check",
            str(workspace / "WORKFLOW.md"),
            "--ticket",
            "VERIFY-1",
            "--workspace",
            str(workspace),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["passed"] is True


@requires_symlink_privilege
def test_release_check_copied_workflow_preserves_file_board_default(
    tmp_path: Path, capsys
) -> None:
    for case_name, raw_board_root in (("empty", "''"), ("non-string", "17")):
        case_root = tmp_path / case_name
        case_root.mkdir()
        repo, workflow, workspace = _repo(
            case_root,
            board_name="board",
            board_root_value=raw_board_root,
        )
        shutil.copy2(workflow, workspace / "WORKFLOW.md")

        rc = release.main(
            [
                "check",
                str(workspace / "WORKFLOW.md"),
                "--ticket",
                "VERIFY-1",
                "--workspace",
                str(workspace),
                "--json",
            ]
        )

        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["passed"] is True


@requires_symlink_privilege
def test_release_check_rejects_copied_workflow_wrong_target_board_mount(
    tmp_path: Path, capsys
) -> None:
    _repo_root, workflow, workspace = _repo(tmp_path)
    shutil.copy2(workflow, workspace / "WORKFLOW.md")
    wrong_board = tmp_path / "wrong-board"
    wrong_board.mkdir()
    (workspace / "kanban").unlink()
    (workspace / "kanban").symlink_to(wrong_board, target_is_directory=True)

    rc = release.main(
        [
            "check",
            str(workspace / "WORKFLOW.md"),
            "--ticket",
            "VERIFY-1",
            "--workspace",
            str(workspace),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert any("workspace board mount" in item for item in payload["evidence_errors"])


@requires_symlink_privilege
def test_release_check_rejects_copied_workflow_board_traversal(
    tmp_path: Path, capsys
) -> None:
    _repo_root, workflow, workspace = _repo(tmp_path)
    escape = tmp_path / "escape"
    escape.mkdir()
    copied = workflow.read_text(encoding="utf-8").replace(
        "board_root: ./kanban", "board_root: ../escape"
    )
    (workspace / "WORKFLOW.md").write_text(copied, encoding="utf-8")

    rc = release.main(
        [
            "check",
            str(workspace / "WORKFLOW.md"),
            "--ticket",
            "VERIFY-1",
            "--workspace",
            str(workspace),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert any("workspace board mount is unsafe" in item for item in payload["evidence_errors"])


@requires_symlink_privilege
def test_release_check_is_nonzero_for_stale_evidence(
    tmp_path: Path, capsys
) -> None:
    repo, workflow, workspace = _repo(tmp_path)
    evidence_path = workspace / "docs" / "VERIFY-1" / "qa" / "release-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["target_sha"] = "0" * 40
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    rc = release.main(
        [
            "check",
            str(workflow),
            "--ticket",
            "VERIFY-1",
            "--workspace",
            str(workspace),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["passed"] is False
    assert any("target_sha" in item for item in payload["evidence_errors"])
