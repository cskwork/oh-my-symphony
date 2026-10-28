"""Eject is explicit, preserves comments, and refuses partial overwrites."""

from pathlib import Path

import pytest

from symphony.cli.eject import eject, main
from symphony.cli.main import main as cli_main
from symphony.workflow import build_service_config, load_workflow
from symphony.workflow.mutate import WorkflowMutationError


def workflow(tmp_path: Path) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(
        "---\n# keep this comment\ndefaults: standard\ntracker: {kind: file}\n---\nBody stays exactly.\n"
    )
    return path


@pytest.mark.parametrize("selection,count", [("prompts", 6), ("hooks", 1), ("all", 7)])
def test_eject_preserves_config_and_comments(tmp_path, selection, count):
    path = workflow(tmp_path)
    before = build_service_config(load_workflow(path))
    written = eject(path, selection)
    assert len(written) == count + 1
    assert all(p.is_file() for p in written)
    text = path.read_text()
    assert "# keep this comment" in text
    assert text.endswith("---\nBody stays exactly.\n")
    after = build_service_config(load_workflow(path))
    assert before.prompts.base_template == after.prompts.base_template
    assert before.prompts.stage_templates == after.prompts.stage_templates
    assert before.hooks.before_run == after.hooks.before_run
    assert before.hooks.after_run == after.hooks.after_run
    if selection in {"all", "prompts"}:
        assert (
            after.prompts.base_path == tmp_path / "docs/symphony-prompts/file/base.md"
        )
    if selection in {"all", "hooks"}:
        assert (
            "$SYMPHONY_WORKFLOW_DIR/scripts/symphony-setup-worktree.sh"
            in after.hooks.after_create
        )
    with pytest.raises(WorkflowMutationError, match="refusing to overwrite"):
        eject(path, selection)
    assert path.read_text() == text


def test_conflict_is_detected_before_any_write(tmp_path):
    path = workflow(tmp_path)
    script = tmp_path / "scripts/symphony-setup-worktree.sh"
    script.parent.mkdir()
    script.write_text("custom")
    original = path.read_bytes()
    assert main(["all", str(path)]) == 1
    assert not (tmp_path / "docs").exists()
    assert script.read_text() == "custom"
    assert path.read_bytes() == original


def test_eject_preserves_explicit_overrides(tmp_path):
    path = workflow(tmp_path)
    path.write_text(
        path.read_text().replace(
            "tracker:", "hooks: {after_create: 'echo custom'}\ntracker:"
        )
    )
    eject(path)
    assert build_service_config(load_workflow(path)).hooks.after_create == "echo custom"


def test_symlink_parent_is_rejected(tmp_path):
    path = workflow(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "docs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkflowMutationError, match="requires a directory"):
        eject(path)
    assert not list(outside.iterdir())


def test_cli_routes_eject_and_lists_help(tmp_path, capsys):
    path = workflow(tmp_path)
    assert cli_main(["eject", "all", str(path)]) == 0
    assert "wrote " in capsys.readouterr().out
    from symphony.cli.main import SUBCOMMANDS

    assert "eject" in dict(SUBCOMMANDS)


def test_cli_accepts_workflow_without_selection(tmp_path):
    path = workflow(tmp_path)
    assert main([str(path)]) == 0
    assert (tmp_path / "scripts/symphony-setup-worktree.sh").is_file()
