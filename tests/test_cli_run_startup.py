"""A2/U3 — direct run path: preflight failures and actionable sentences."""

from __future__ import annotations

import importlib
import socket
import sys
from pathlib import Path

import pytest

cli_main_mod = importlib.import_module("symphony.cli.main")


def _workflow_text(
    *,
    codex_command: str = "codex app-server",
    after_create: str | None = None,
    workspace_root: str = "./tmp_workspaces",
) -> str:
    hook_block = ""
    if after_create is not None:
        hook_block = f"hooks:\n  after_create: '{after_create}'\n\n"
    return (
        "---\n"
        "tracker:\n"
        "  kind: file\n"
        "  board_root: ./kanban\n"
        '  active_states: [Todo, "In Progress"]\n'
        "  terminal_states: [Done]\n"
        "\n"
        "workspace:\n"
        f"  root: {workspace_root}\n"
        "\n"
        f"{hook_block}"
        "agent:\n"
        "  kind: codex\n"
        "\n"
        "codex:\n"
        f"  command: '{codex_command}'\n"
        "---\n"
        "\n"
        "Prompt for {{ issue.identifier }}.\n"
    )


def _board(tmp_path: Path, text: str) -> Path:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(text, encoding="utf-8")
    (tmp_path / "kanban").mkdir(exist_ok=True)
    return workflow


def test_missing_workflow_prints_actionable_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_main_mod.main([str(tmp_path / "nope" / "WORKFLOW.md")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "symphony:" in err
    assert "WORKFLOW.md not found" in err
    assert "symphony board init" in err


def test_broken_workflow_prints_actionable_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("---\ntracker: [broken\n---\n", encoding="utf-8")
    rc = cli_main_mod.main([str(workflow)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "symphony:" in err
    assert "could not be loaded" in err
    assert "symphony doctor" in err


def test_preflight_blocks_missing_agent_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _board(
        tmp_path,
        _workflow_text(codex_command="definitely-not-a-real-cli-xyz app-server"),
    )
    rc = cli_main_mod.main([str(workflow)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "symphony:" in err
    assert "definitely-not-a-real-cli-xyz" in err
    assert "$PATH" in err


def test_preflight_blocks_placeholder_after_create(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _board(
        tmp_path,
        _workflow_text(
            codex_command=f"{sys.executable} -m symphony.mock_codex app-server",
            after_create="git clone git@github.com:my-org/my-repo.git .",
        ),
    )
    rc = cli_main_mod.main([str(workflow)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "symphony:" in err
    assert "my-org/my-repo" in err


def test_port_conflict_prints_actionable_sentence_not_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _board(
        tmp_path,
        _workflow_text(
            codex_command=f"{sys.executable} -m symphony.mock_codex app-server",
            workspace_root=str(tmp_path / "ws"),
        ),
    )
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        rc = cli_main_mod.main([str(workflow), "--port", str(port)])
    finally:
        blocker.close()
    assert rc == 1
    err = capsys.readouterr().err
    assert "symphony:" in err
    assert str(port) in err
    assert "already in use" in err
