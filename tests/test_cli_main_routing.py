"""`symphony.cli.main:main` — argv routing to subcommand mains.

The dispatcher trims the first argv token and forwards to the right
sub-CLI: board, doctor, service, wiki-sweep, or the orchestrator default.
We assert each subcommand router by monkeypatching the target main, so
the tests don't actually start orchestrators / TUIs / wiki sweeps.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# `symphony/cli/__init__.py` re-exports `main` as a function on the package,
# which shadows the submodule attribute lookup. Pull the actual module
# through importlib so we can monkeypatch its internals (`_run` etc.).
cli_main_mod = importlib.import_module("symphony.cli.main")


def test_board_token_dispatches_to_board_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {"called": False, "argv": None}

    def fake_board_main(argv: list[str]) -> int:
        captured["called"] = True
        captured["argv"] = argv
        return 7

    monkeypatch.setattr("symphony.cli.board.main", fake_board_main)
    rc = cli_main_mod.main(["board", "ls", "--state", "Todo"])
    assert rc == 7
    assert captured["called"] is True
    # The "board" token is stripped before forwarding.
    assert captured["argv"] == ["ls", "--state", "Todo"]


def test_doctor_token_dispatches_to_doctor_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {"argv": None}

    def fake_doctor_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 3

    monkeypatch.setattr("symphony.cli.doctor.main", fake_doctor_main)
    rc = cli_main_mod.main(["doctor", "--workflow", "WORKFLOW.md"])
    assert rc == 3
    assert captured["argv"] == ["--workflow", "WORKFLOW.md"]


def test_service_token_dispatches_to_service_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {"argv": None}

    def fake_service_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 5

    monkeypatch.setattr("symphony.service.main", fake_service_main)
    rc = cli_main_mod.main(["service", "status"])
    assert rc == 5
    assert captured["argv"] == ["status"]


def test_wiki_sweep_token_runs_inline_main_with_root_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Build a wiki root so sweep doesn't fail on missing dir.
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "INDEX.md").write_text("# Index\n", encoding="utf-8")

    rc = cli_main_mod.main(["wiki-sweep", "--root", str(wiki), "--dry-run"])
    # Sweep on an empty wiki is clean: rc == 0 with summary lines printed.
    assert rc == 0
    assert capsys.readouterr().out  # summary lines printed


def test_tui_token_is_rewritten_to_tui_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`symphony tui` is sugar for `symphony --tui`. The rewriter prepends
    `--tui` and then the orchestrator main path takes over. We don't want
    the orchestrator to actually start, so we intercept asyncio.run."""

    seen_argv: dict = {}

    async def fake_run(args) -> int:  # noqa: ANN001
        seen_argv["tui"] = bool(getattr(args, "tui", False))
        seen_argv["host"] = getattr(args, "host", None)
        return 0

    monkeypatch.setattr(cli_main_mod, "_run", fake_run)
    rc = cli_main_mod.main(["tui", "--host", "127.0.0.1"])
    assert rc == 0
    assert seen_argv["tui"] is True
    assert seen_argv["host"] == "127.0.0.1"


def test_keyboard_interrupt_during_run_returns_130(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGINT during the orchestrator path exits with the POSIX 130."""

    async def fake_run(args) -> int:  # noqa: ANN001
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_main_mod, "_run", fake_run)
    rc = cli_main_mod.main([])
    assert rc == 130


def test_empty_argv_falls_through_to_default_orchestrator_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No subcommand token => parse args + call _run (orchestrator path)."""

    async def fake_run(args) -> int:  # noqa: ANN001
        # The default parser succeeds with no args — workflow defaults are
        # applied downstream.
        return 0

    monkeypatch.setattr(cli_main_mod, "_run", fake_run)
    rc = cli_main_mod.main([])
    assert rc == 0


def test_unknown_subcommand_is_treated_as_workflow_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-routed first token is *not* a subcommand error; the parser
    accepts it as the positional workflow argument and `_run` decides."""

    captured: dict = {}

    async def fake_run(args) -> int:  # noqa: ANN001
        captured["workflow"] = getattr(args, "workflow", None)
        return 0

    monkeypatch.setattr(cli_main_mod, "_run", fake_run)
    rc = cli_main_mod.main(["my-workflow.md"])
    assert rc == 0
    # The first token reaches argparse as the positional workflow arg.
    assert str(captured["workflow"]).endswith("my-workflow.md")


def test_version_flag_prints_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`symphony --version` prints `symphony <version>` and exits 0.

    argparse's version action prints to stdout and raises SystemExit(0) inside
    `parse_args` — before the orchestrator path — so no event loop ever starts.
    """
    from symphony import __version__

    with pytest.raises(SystemExit) as exc:
        cli_main_mod.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"symphony {__version__}"
