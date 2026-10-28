"""Project bootstrap uses installed resources and keeps minimal projects small."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from symphony import projects
from symphony.cli import project as project_cli
from symphony.projects import ProjectError, ProjectRegistry, create_or_adopt_project
from symphony.templates.project import resource_path
from symphony.workflow import build_service_config, load_workflow
from symphony.workflow.presets import DEEP_PRESET


def tracked(repo: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True, text=True, check=True,
    )
    return set(result.stdout.splitlines())


def test_create_uses_packaged_minimal_bundle_without_source(tmp_path: Path) -> None:
    target = tmp_path / "app"
    record = create_or_adopt_project(
        target, project_id="app", port=10201,
        registry=ProjectRegistry(tmp_path / "projects.json"),
    )

    assert tracked(target) == {"WORKFLOW.md", "AGENTS.md", "kanban/.gitkeep"}
    assert (target / "AGENTS.md").read_bytes() == resource_path("AGENTS.md").read_bytes()
    config = build_service_config(load_workflow(record.workflow))
    assert config.tracker.board_root == target / "kanban"
    assert config.workspace_root == Path.home() / "symphony_workspaces/app"
    assert config.agent.kind == "codex"
    assert config.codex.approval_policy == "never"
    assert config.codex.thread_sandbox == "workspace-write"
    assert config.codex.turn_sandbox_policy == {"type": "workspaceWrite", "networkAccess": True}
    assert config.codex.model == ""
    assert config.server.port == 10201
    assert "__PROJECT_ID__" not in (target / "WORKFLOW.md").read_text()
    assert not any((target / name).exists() for name in ("docs", "scripts", "skills", ".claude"))


def test_source_argument_only_guards_identity_and_need_not_be_a_bundle(tmp_path: Path) -> None:
    target = tmp_path / "app"
    source = tmp_path / "not-a-git-checkout"
    source.mkdir()
    (source / "WORKFLOW.md").write_text("malicious source workflow")
    create_or_adopt_project(
        target, source=source, project_id="app",
        registry=ProjectRegistry(tmp_path / "projects.json"),
    )
    assert "defaults: standard" in (target / "WORKFLOW.md").read_text()
    assert "malicious" not in (target / "WORKFLOW.md").read_text()


def test_wheel_style_create_defaults_to_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(project_cli, "source_checkout", lambda: None)
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "registry.json"))
    assert project_cli.main(["create", "Installed App", "--id", "installed"]) == 0
    assert ProjectRegistry().get("installed").git_repo == str(tmp_path / "installed")
    assert len(tracked(tmp_path / "installed")) <= 10


def test_source_checkout_ignores_unrelated_git_parents_for_wheel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("symphony.runtime_safety.protected_source_common_dir", lambda: None)

    def unexpected_lookup(_path: Path) -> Path:
        raise AssertionError("wheel installs must not adopt an enclosing unrelated repo")

    monkeypatch.setattr(projects, "_git_toplevel", unexpected_lookup)
    assert projects.source_checkout() is None


def test_source_guard_applies_even_without_source_argument(tmp_path: Path) -> None:
    source = projects.source_checkout()
    assert source is not None
    with pytest.raises(ProjectError, match="protected Symphony source checkout"):
        create_or_adopt_project(
            source / "new-project", project_id="unsafe",
            registry=ProjectRegistry(tmp_path / "registry.json"),
        )
    assert not (source / "new-project").exists()


def test_with_skill_copies_packaged_skills_and_discovery_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "registry.json"))
    target = tmp_path / "skilled"
    assert project_cli.main(["create", "Skilled", "--path", str(target), "--with-skill"]) == 0
    canonical = target / "skills/symphony-skill/SKILL.md"
    assert canonical.read_bytes() == resource_path("skills/symphony-skill/SKILL.md").read_bytes()
    assert (target / ".claude/skills/symphony-skill/SKILL.md").read_bytes() == canonical.read_bytes()
    assert not (target / "docs").exists()
    assert not (target / "scripts").exists()
    assert "skills/symphony-skill/SKILL.md" in tracked(target)


def test_minimal_adoption_preserves_workflow_and_unselected_path_shapes(tmp_path: Path) -> None:
    target = tmp_path / "adopt"
    target.mkdir()
    custom = "---\ntracker: {kind: file, board_root: ./kanban}\n---\nCustom workflow\n"
    (target / "WORKFLOW.md").write_text(custom)
    for name in ("docs", "scripts", "skills", ".claude"):
        (target / name).write_text("existing product file")
    create_or_adopt_project(
        target, project_id="adopt", registry=ProjectRegistry(tmp_path / "registry.json"),
    )
    assert (target / "WORKFLOW.md").read_text() == custom
    assert tracked(target) == {"AGENTS.md", "kanban/.gitkeep"}
    for name in ("docs", "scripts", "skills", ".claude"):
        assert (target / name).read_text() == "existing product file"


def test_deep_preset_materializes_only_its_packaged_prompt_files(tmp_path: Path) -> None:
    target = tmp_path / "deep"
    create_or_adopt_project(
        target, project_id="deep", preset="deep",
        registry=ProjectRegistry(tmp_path / "registry.json"),
    )
    config = build_service_config(load_workflow(target / "WORKFLOW.md"))
    assert config.tracker.active_states == DEEP_PRESET.active_states
    assert set(config.prompts.stage_templates) == {state.lower() for state in DEEP_PRESET.stage_prompts}
    expected = {relative.removeprefix("./") for relative in (
        DEEP_PRESET.base_prompt, *DEEP_PRESET.stage_prompts.values(),
    )}
    assert tracked(target) == {"WORKFLOW.md", "AGENTS.md", "kanban/.gitkeep"} | expected
    for relative in expected:
        assert (target / relative).read_bytes() == resource_path(relative).read_bytes()
    assert not (target / "skills").exists()
    assert not (target / "docs/symphony-prompts/linear").exists()


def test_with_skill_validates_nested_conflicts_before_mutation(tmp_path: Path) -> None:
    target = tmp_path / "conflict"
    (target / "skills/symphony-skill").mkdir(parents=True)
    (target / "skills/symphony-skill/reference").write_text("product file")
    with pytest.raises(ProjectError, match="requires a directory"):
        create_or_adopt_project(
            target, project_id="conflict", with_skill=True,
            registry=ProjectRegistry(tmp_path / "registry.json"),
        )
    assert not (target / ".git").exists()
    assert not (target / "WORKFLOW.md").exists()
    assert (target / "skills/symphony-skill/reference").read_text() == "product file"


def test_preset_refuses_to_rewrite_an_adopted_workflow(tmp_path: Path) -> None:
    target = tmp_path / "custom"
    target.mkdir()
    custom = "---\ntracker: {kind: file, board_root: ./kanban}\n---\nKeep custom workflow\n"
    (target / "WORKFLOW.md").write_text(custom)
    registry = ProjectRegistry(tmp_path / "registry.json")
    with pytest.raises(ProjectError, match="applies only to a workflow this setup creates"):
        create_or_adopt_project(target, project_id="custom", preset="deep", registry=registry)
    assert (target / "WORKFLOW.md").read_text() == custom
    assert {path.name for path in target.iterdir()} == {"WORKFLOW.md"}
    assert registry.load() == []
