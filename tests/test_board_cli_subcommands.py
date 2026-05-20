"""`symphony board` subcommand coverage beyond the new --root override.

`test_board_cli.py` pinned the cross-agent `new --root` override. This
file walks the rest of the surface:

  * init       seeds a sample ticket; idempotent on rerun.
  * ls         filters by --state (case-insensitive).
  * new        --workflow path picks board_root from WORKFLOW.md.
  * new        rejects unsupported agent-kind via argparse.
  * mv         transitions a ticket between states.
  * mv         non-zero exit when the ticket is missing.
  * show       prints front-matter + body.
  * show       non-zero exit when the ticket is missing.

Each test isolates to a tmp_path so they're hermetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.cli import board as board_cli


def _make_workflow(tmp_path: Path, board_dir: str = "board") -> Path:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "\n".join(
            [
                "---",
                "tracker:",
                "  kind: file",
                f"  board_root: ./{board_dir}",
                "---",
                "prompt",
            ]
        ),
        encoding="utf-8",
    )
    return workflow


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_board_dir_and_sample_ticket(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    board = tmp_path / "fresh-board"
    rc = board_cli.main(["init", str(board)])
    assert rc == 0
    assert (board / "DEMO-001.md").exists()
    captured = capsys.readouterr()
    assert "initialized board at" in captured.out


def test_init_is_idempotent_when_sample_already_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    board = tmp_path / "fresh-board"
    board_cli.main(["init", str(board)])
    capsys.readouterr()  # discard first
    rc = board_cli.main(["init", str(board)])
    assert rc == 0
    assert "already initialized" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# new + ls + mv + show happy path against a workflow-rooted board
# ---------------------------------------------------------------------------


def test_new_uses_workflow_board_root_when_no_root_override(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path, "my-board")
    rc = board_cli.main(
        [
            "new",
            "--workflow",
            str(workflow),
            "TKT-1",
            "first ticket",
            "--priority",
            "1",
            "--labels",
            "alpha,beta",
        ]
    )
    assert rc == 0
    ticket = tmp_path / "my-board" / "TKT-1.md"
    assert ticket.exists()
    content = ticket.read_text(encoding="utf-8")
    assert "TKT-1" in content
    assert "priority: 1" in content
    # comma-split labels propagated.
    assert "alpha" in content and "beta" in content


def test_new_rejects_unknown_agent_kind_via_argparse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    with pytest.raises(SystemExit):
        board_cli.main(
            [
                "new",
                "--workflow",
                str(workflow),
                "TKT-2",
                "x",
                "--agent-kind",
                "totally-not-an-agent",
            ]
        )
    # argparse error went to stderr.
    assert "invalid choice" in capsys.readouterr().err


def test_ls_filters_by_state_case_insensitively(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(["new", "--workflow", str(workflow), "TKT-A", "a"])
    board_cli.main(
        ["new", "--workflow", str(workflow), "TKT-B", "b", "--state", "In Progress"]
    )
    capsys.readouterr()  # discard new prints

    rc = board_cli.main(["ls", "--workflow", str(workflow), "--state", "in progress"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TKT-B" in out
    assert "TKT-A" not in out


def test_ls_prints_empty_marker_when_no_tickets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path, "empty-board")
    # Create the board dir but no tickets.
    (tmp_path / "empty-board").mkdir()
    rc = board_cli.main(["ls", "--workflow", str(workflow)])
    assert rc == 0
    assert "no tickets" in capsys.readouterr().out


def test_mv_transitions_ticket_and_prints_arrow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(["new", "--workflow", str(workflow), "TKT-3", "x"])
    capsys.readouterr()
    rc = board_cli.main(["mv", "--workflow", str(workflow), "TKT-3", "In Progress"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TKT-3 -> In Progress" in out
    ticket = (tmp_path / "board" / "TKT-3.md").read_text(encoding="utf-8")
    assert "state: In Progress" in ticket


def test_mv_returns_nonzero_when_ticket_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    (tmp_path / "board").mkdir()
    rc = board_cli.main(["mv", "--workflow", str(workflow), "DOES-NOT-EXIST", "Done"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err.lower()


def test_show_prints_front_matter_and_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(
        [
            "new",
            "--workflow",
            str(workflow),
            "TKT-4",
            "show me",
            "--priority",
            "2",
            "--labels",
            "x,y",
            "--description",
            "body line one",
        ]
    )
    capsys.readouterr()
    rc = board_cli.main(["show", "--workflow", str(workflow), "TKT-4"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "# TKT-4" in out
    assert "title: show me" in out
    assert "priority: 2" in out
    # Labels list normalized into a comma-joined display.
    assert "x" in out and "y" in out
    # Body printed after a blank line separator.
    assert "body line one" in out


def test_show_returns_nonzero_when_ticket_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    (tmp_path / "board").mkdir()
    rc = board_cli.main(["show", "--workflow", str(workflow), "MISSING"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
