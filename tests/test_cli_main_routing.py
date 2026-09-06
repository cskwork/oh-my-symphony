"""`symphony.cli.main:main` — argv routing to subcommand mains.

The dispatcher trims the first argv token and forwards to the right
sub-CLI: board, doctor, service, wiki-sweep, or the orchestrator default.
We assert each subcommand router by monkeypatching the target main, so
the tests don't actually start orchestrators / TUIs / wiki sweeps.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from symphony.issue import Issue
from symphony.orchestrator.run_registry import RunRegistry

# `symphony/cli/__init__.py` re-exports `main` as a function on the package,
# which shadows the submodule attribute lookup. Pull the actual module
# through importlib so we can monkeypatch its internals (`_run` etc.).
cli_main_mod = importlib.import_module("symphony.cli.main")


def _workflow(tmp_path: Path) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text("---\ntracker: {kind: file}\n---\nbody\n", encoding="utf-8")
    return path


def _issue(identifier: str = "MT-1") -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="",
        priority=None,
        state="In Progress",
        created_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )


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


def test_release_token_dispatches_to_release_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {"argv": None}

    def fake_release_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 4

    monkeypatch.setattr("symphony.cli.release.main", fake_release_main)
    rc = cli_main_mod.main(
        ["release", "check", "WORKFLOW.md", "--ticket", "VERIFY-1", "--workspace", "."]
    )
    assert rc == 4
    assert captured["argv"] == [
        "check",
        "WORKFLOW.md",
        "--ticket",
        "VERIFY-1",
        "--workspace",
        ".",
    ]


def test_hub_token_dispatches_to_hub_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {"argv": None}

    def fake_hub_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 6

    monkeypatch.setattr("symphony.hub.main", fake_hub_main)
    rc = cli_main_mod.main(["hub", "--host", "localhost", "--port", "8123"])
    assert rc == 6
    assert captured["argv"] == ["--host", "localhost", "--port", "8123"]


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


def test_runs_token_prints_recent_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _workflow(tmp_path)
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    issue = _issue()
    now = datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc)
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="force_ejected_zombie",
        now=now + timedelta(seconds=1),
    )

    rc = cli_main_mod.main(["runs", str(workflow), "--issue", issue.id, "--limit", "5"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "MT-1" in out
    assert "retry" in out
    assert "codex" in out
    assert "force_ejected_zombie" in out


def test_runs_token_empty_history_is_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _workflow(tmp_path)

    rc = cli_main_mod.main(["runs", str(workflow)])

    assert rc == 0
    assert "no runs recorded" in capsys.readouterr().out


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


def test_project_token_dispatches_to_project_main(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_project_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 6

    monkeypatch.setattr("symphony.cli.project.main", fake_project_main)
    assert cli_main_mod.main(["project", "list"]) == 6
    assert captured["argv"] == ["list"]


def test_help_lists_every_routed_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """`symphony --help` must name each first-token subcommand; before this
    the top-level help showed only the orchestrator flags."""
    with pytest.raises(SystemExit) as exc:
        cli_main_mod.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name, _ in cli_main_mod.SUBCOMMANDS:
        assert f"\n  {name} " in out, f"{name} missing from --help"


def test_mistyped_subcommand_is_rejected_with_command_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare word that is not a command and not a path fails fast, instead
    of being resolved as `./servce` and reported as a missing WORKFLOW.md."""
    called = False

    async def fake_run(args) -> int:  # noqa: ANN001
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli_main_mod, "_run", fake_run)
    rc = cli_main_mod.main(["servce", "start"])
    assert rc == 2
    assert called is False
    err = capsys.readouterr().err
    assert "unknown command 'servce'" in err
    assert "service" in err


def test_existing_directory_token_is_still_a_workflow_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mistyped-command guard must not swallow real paths without a dot."""
    captured: dict = {}

    async def fake_run(args) -> int:  # noqa: ANN001
        captured["workflow"] = getattr(args, "workflow", None)
        return 0

    monkeypatch.setattr(cli_main_mod, "_run", fake_run)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "myproj").mkdir()
    assert cli_main_mod.main(["myproj"]) == 0
    assert str(captured["workflow"]).endswith("myproj")
