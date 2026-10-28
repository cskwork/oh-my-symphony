"""The standard bundle is opt-in and preserves explicit workflow choices."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from symphony.errors import ConfigValidationError
from symphony.workflow import build_service_config, load_workflow, parse_workflow_text

ROOT = Path(__file__).resolve().parents[1]


def test_standard_defaults_match_shipped_four_lane_board(tmp_path):
    legacy = load_workflow(ROOT / "examples/advanced/WORKFLOW.file.example.md")
    cfg = build_service_config(
        parse_workflow_text(
            "---\ndefaults: standard\ntracker: {kind: file}\n---\n",
            tmp_path / "WORKFLOW.md",
        )
    )
    expected = build_service_config(
        replace(legacy, source_path=ROOT / "src/symphony/templates/project" / "WORKFLOW.md")
    )
    assert cfg.tracker.active_states == expected.tracker.active_states
    assert cfg.tracker.terminal_states == expected.tracker.terminal_states
    assert cfg.tracker.state_descriptions == expected.tracker.state_descriptions
    assert cfg.tracker.archive_state == expected.tracker.archive_state
    assert cfg.tracker.archive_after_days == expected.tracker.archive_after_days
    assert cfg.prompts.base_template == expected.prompts.base_template
    assert cfg.prompts.stage_templates == expected.prompts.stage_templates
    assert cfg.hooks.before_run == expected.hooks.before_run
    assert cfg.hooks.after_run == expected.hooks.after_run
    assert cfg.hooks.before_remove == expected.hooks.before_remove
    assert cfg.hooks.after_create
    import shlex

    script = Path(shlex.split(cfg.hooks.after_create)[1])
    assert script.is_absolute() and script.is_file()
    assert (
        script.read_bytes()
        == (ROOT / "src/symphony/templates/project/scripts/symphony-setup-worktree.sh").read_bytes()
    )


def test_explicit_keys_win_and_definition_is_not_mutated(tmp_path):
    (tmp_path / "base.md").write_text("local base")
    workflow = parse_workflow_text(
        """---
defaults: standard
tracker:
  kind: file
  active_states: [Custom]
  terminal_states: [Finished]
  archive_after_days: 0
  state_descriptions: {Todo: Local}
prompts:
  base: base.md
  stages: {Todo: base.md}
hooks:
  after_create: ''
  timeout_ms: 123
---
Body
""",
        tmp_path / "WORKFLOW.md",
    )
    original = deepcopy(workflow.config)
    cfg = build_service_config(workflow)
    assert cfg.tracker.active_states == ("Custom",)
    assert cfg.tracker.terminal_states == ("Finished",)
    assert cfg.tracker.archive_after_days == 0
    assert cfg.tracker.state_descriptions["todo"] == "Local"
    assert cfg.prompts.base_template == "local base"
    assert cfg.prompts.stage_templates["todo"] == "local base"
    assert cfg.prompts.stage_templates == {"todo": "local base"}
    assert cfg.hooks.after_create == ""
    assert cfg.hooks.timeout_ms == 123
    assert workflow.config == original


def test_without_defaults_uses_exact_legacy_config(tmp_path, monkeypatch):
    # Poison the opt-in loader: legacy workflows must never consult it.
    import symphony.templates.project as bundle

    monkeypatch.setattr(
        bundle, "standard_defaults", lambda **kw: pytest.fail("legacy read bundle")
    )
    workflow = parse_workflow_text(
        """---
tracker: {kind: file}
hooks: {before_run: 'echo custom'}
---
Legacy body
""",
        tmp_path / "WORKFLOW.md",
    )
    cfg = build_service_config(workflow)
    assert cfg.hooks.after_create is None
    assert cfg.hooks.before_run == "echo custom"
    assert cfg.prompts.base_template == ""
    assert cfg.prompts.stage_templates == {}
    assert cfg.prompt_template == "Legacy body"
    # The previously supported spelling remains a plain ignored unknown key.
    alternate = replace(workflow, config={**workflow.config, "unrelated": "standard"})
    assert replace(build_service_config(alternate), raw=cfg.raw) == cfg


@pytest.mark.parametrize("value", ["unknown", True, {"standard": True}])
def test_invalid_defaults_are_explicit_errors(tmp_path, value):
    workflow = parse_workflow_text(
        "---\ntracker: {kind: file}\n---\n", tmp_path / "W.md"
    )
    workflow.config["defaults"] = value
    with pytest.raises(ConfigValidationError, match="defaults"):
        build_service_config(workflow)


def test_generated_bundle_is_equal_to_repository_sources():
    from symphony.templates.project import resource_path
    from symphony.templates.project._sync import source_files

    from symphony.templates.project._sync import AGENT_BLOCKS

    for source, relative in source_files(ROOT):
        assert resource_path(relative).read_bytes() == source.read_bytes(), relative
    advanced = load_workflow(ROOT / "examples/advanced/WORKFLOW.file.example.md")
    bundled = yaml.safe_load(resource_path("defaults.yaml").read_text())
    assert bundled == {
        key: advanced.config[key]
        for key in ("tracker", "prompts", "hooks", *AGENT_BLOCKS)
    }


def test_standard_defaults_grant_claude_the_linked_board(tmp_path):
    """A minimal board must let Claude workers write the host ticket files."""
    cfg = build_service_config(
        parse_workflow_text(
            "---\ndefaults: standard\ntracker: {kind: file}\nagent: {kind: claude}\n---\n",
            tmp_path / "WORKFLOW.md",
        )
    )
    assert '--add-dir "$SYMPHONY_WORKFLOW_DIR/kanban"' in cfg.claude.command
    assert "--permission-mode acceptEdits" in cfg.claude.command


def test_without_defaults_matches_baseline_service_config(tmp_path):
    """Snapshot from c6a3947; only the intentional Codex model change differs."""
    import json
    from dataclasses import asdict

    workflow = parse_workflow_text(
        """---
tracker: {kind: file}
workspace: {root: ./workspaces}
hooks: {before_run: echo custom}
---
Legacy body
""",
        tmp_path / "WORKFLOW.md",
    )

    def serialize(value):
        if isinstance(value, Path):
            return value.as_posix().replace(tmp_path.as_posix(), "<project>")
        raise TypeError(type(value).__name__)

    actual = json.loads(
        json.dumps(asdict(build_service_config(workflow)), default=serialize)
    )
    expected = json.loads(
        (ROOT / "tests/fixtures/legacy_service_config.json").read_text()
    )
    assert actual == expected
