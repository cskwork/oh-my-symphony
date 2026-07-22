"""Contract coverage for the shipped default-off AIDT operator example."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import symphony.jira_intake as jira_intake
from symphony.cli import doctor as doctor_module
from symphony.cli.doctor import CheckResult
from symphony.aidt_routing import load_routing_settings
from symphony.aidt_worktree import (
    AIDT_WORKTREE_BASE_REF,
    load_aidt_worktree_settings,
)
from symphony.workflow import build_service_config, load_workflow


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "WORKFLOW.aidt.example.md"


def test_aidt_operator_example_is_default_off_and_loader_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIDT_SYMPHONY_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.setenv("JIRA_INTAKE_EMAIL", "operator@example.invalid")
    monkeypatch.setenv("JIRA_INTAKE_TOKEN", "test-token")

    config = build_service_config(load_workflow(EXAMPLE))

    assert config.server.port == 9918
    assert config.prompts.base_path is not None
    assert config.prompts.base_path.is_file()
    for state in config.tracker.active_states:
        prompt_path = config.prompts.stage_paths[state.casefold()]
        assert prompt_path.is_file()
        assert config.prompt_template_for_state(state)
    assert jira_intake._settings(config) is None
    assert load_routing_settings(config) is None
    assert load_aidt_worktree_settings(config) is None

    config.raw["jira_intake"]["enabled"] = True
    config.raw["aidt_routing"]["enabled"] = True
    config.raw["aidt_worktree"]["enabled"] = True

    intake = jira_intake._settings(config)
    routing = load_routing_settings(config)
    worktree = load_aidt_worktree_settings(config)

    assert intake is not None
    assert intake.statuses == ("백로그",)
    assert routing is not None
    assert [service.id for service in routing.services] == ["lms-api"]
    assert worktree is not None
    assert worktree.workspace_root == (tmp_path / "workspaces").resolve()
    assert AIDT_WORKTREE_BASE_REF == "refs/remotes/origin/aidt-prd"


def test_aidt_operator_example_passes_real_doctor_without_external_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture_root = tmp_path / "fixture"
    example_root = fixture_root / "examples"
    example_root.mkdir(parents=True)
    workflow = example_root / EXAMPLE.name
    shutil.copyfile(EXAMPLE, workflow)
    shutil.copytree(
        REPO_ROOT / "docs" / "symphony-prompts" / "file",
        fixture_root / "docs" / "symphony-prompts" / "file",
    )
    (example_root / "kanban-aidt").mkdir()
    (example_root / "workspaces-aidt").mkdir()

    monkeypatch.setenv("AIDT_SYMPHONY_WORKSPACES", "./workspaces-aidt")
    monkeypatch.setattr(
        doctor_module,
        "check_port",
        lambda _cfg, host="127.0.0.1": CheckResult(
            "server.port=9918", "pass", f"{host}:9918 controlled test probe"
        ),
    )
    monkeypatch.setattr(
        doctor_module,
        "check_shell",
        lambda: CheckResult("shell.bash", "pass", "controlled test shell"),
    )
    monkeypatch.setattr(
        doctor_module,
        "check_agent_cli",
        lambda _cfg: CheckResult(
            "agent.kind=codex", "pass", "controlled test executable"
        ),
    )

    result = doctor_module.main([str(workflow), "--no-color"])

    output = capsys.readouterr()
    assert result == 0
    assert "FAIL" not in output.out
    assert "PASS  prompts.files" in output.out
    assert "PASS  tracker.board_root" in output.out
    assert "PASS  workspace.root=" in output.out
