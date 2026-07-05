"""workflow.mutate — comment-preserving WORKFLOW.md edits for the web UI."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.workflow import build_service_config, load_workflow
from symphony.workflow.mutate import (
    StateSpec,
    WorkflowMutationError,
    apply_states_update,
    read_prompt,
    resolve_prompt_path,
    set_branch_policy,
    set_continuous_improvement_settings,
    validate_states,
    write_prompt,
)


WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  # operator note: keep Todo first
  active_states: [Todo, Doing]
  terminal_states: [Done, Archive]
  state_descriptions:
    Todo: "triage"
    Doing: "build"

agent:
  kind: claude
  max_concurrent_agents_by_state:
    Doing: 2
  max_total_tokens_by_state:
    Doing: 1000
  max_state_turns_by_state:
    Doing: 7

prompts:
  base: ./prompts/base.md
  stages:
    Todo: ./prompts/stages/todo.md
    Doing: ./prompts/stages/doing.md
---

Body text with {{ issue.identifier }} stays untouched.
"""


@pytest.fixture()
def workflow(tmp_path: Path) -> Path:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(WORKFLOW_TEXT, encoding="utf-8")
    stages = tmp_path / "prompts" / "stages"
    stages.mkdir(parents=True)
    (tmp_path / "prompts" / "base.md").write_text("base", encoding="utf-8")
    (stages / "todo.md").write_text("todo prompt", encoding="utf-8")
    (stages / "doing.md").write_text("doing prompt", encoding="utf-8")
    return path


def _specs(*rows: tuple[str, str, bool]) -> list[StateSpec]:
    return [StateSpec(name=n, description=d, terminal=t) for n, d, t in rows]


# ---------------------------------------------------------------------------
# validate_states
# ---------------------------------------------------------------------------


def test_validate_rejects_empty_and_duplicates() -> None:
    with pytest.raises(WorkflowMutationError):
        validate_states([])
    with pytest.raises(WorkflowMutationError):
        validate_states(_specs(("Todo", "", False), ("todo", "", True)))


def test_validate_requires_active_and_terminal() -> None:
    with pytest.raises(WorkflowMutationError):
        validate_states(_specs(("Todo", "", False)))
    with pytest.raises(WorkflowMutationError):
        validate_states(_specs(("Done", "", True)))


def test_validate_rejects_bad_names() -> None:
    with pytest.raises(WorkflowMutationError):
        validate_states(_specs(("Bad:Name", "", False), ("Done", "", True)))


# ---------------------------------------------------------------------------
# apply_states_update
# ---------------------------------------------------------------------------


def test_add_column_creates_prompt_and_keeps_comments(workflow: Path) -> None:
    plan = apply_states_update(
        workflow,
        _specs(
            ("Todo", "triage", False),
            ("Doing", "build", False),
            ("QA", "verify", False),
            ("Done", "", True),
            ("Archive", "", True),
        ),
    )
    assert plan.added == ["QA"]
    text = workflow.read_text(encoding="utf-8")
    assert "# operator note: keep Todo first" in text
    assert "active_states: [Todo, Doing, QA]" in text
    assert "QA: prompts/stages/qa.md" in text
    assert (workflow.parent / "prompts" / "stages" / "qa.md").exists()
    assert "Body text with {{ issue.identifier }} stays untouched." in text


def test_rename_column_updates_per_state_maps(workflow: Path) -> None:
    plan = apply_states_update(
        workflow,
        [
            StateSpec(name="Todo"),
            StateSpec(name="Building", description="build", previous_name="Doing"),
            StateSpec(name="Done", terminal=True),
            StateSpec(name="Archive", terminal=True),
        ],
    )
    assert plan.renamed == {"Doing": "Building"}
    text = workflow.read_text(encoding="utf-8")
    assert "active_states: [Todo, Building]" in text
    assert "Doing:" not in text.split("prompts:")[1]
    assert "Building: ./prompts/stages/doing.md" in text
    assert "Building: 2" in text
    assert "Building: 1000" in text
    assert "Building: 7" in text


def test_remove_column_reports_removed_and_fallback(workflow: Path) -> None:
    plan = apply_states_update(
        workflow,
        [
            StateSpec(name="Todo"),
            StateSpec(name="Done", terminal=True),
            StateSpec(name="Archive", terminal=True),
        ],
    )
    assert plan.removed == ["Doing"]
    assert plan.fallback_state == "Todo"
    text = workflow.read_text(encoding="utf-8")
    assert "active_states: [Todo]" in text
    # Doing dropped from stages and per-state maps, prompt file kept on disk.
    assert "./prompts/stages/doing.md" not in text
    assert (workflow.parent / "prompts" / "stages" / "doing.md").exists()


def test_missing_frontmatter_rejected(tmp_path: Path) -> None:
    path = tmp_path / "WORKFLOW.md"
    path.write_text("no frontmatter here", encoding="utf-8")
    with pytest.raises(WorkflowMutationError):
        apply_states_update(
            path, _specs(("Todo", "", False), ("Done", "", True))
        )


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


def test_read_and_write_prompt_roundtrip(workflow: Path) -> None:
    payload = read_prompt(workflow, "todo")
    assert payload is not None
    assert payload["content"] == "todo prompt"
    write_prompt(workflow, "Todo", "updated!")
    assert (workflow.parent / "prompts" / "stages" / "todo.md").read_text(
        encoding="utf-8"
    ) == "updated!"


def test_prompt_unknown_state_returns_none(workflow: Path) -> None:
    assert read_prompt(workflow, "Nope") is None
    with pytest.raises(WorkflowMutationError):
        write_prompt(workflow, "Nope", "x")


def test_prompt_path_escape_rejected(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8").replace(
        "./prompts/stages/todo.md", "../outside.md"
    )
    workflow.write_text(text, encoding="utf-8")
    with pytest.raises(WorkflowMutationError):
        resolve_prompt_path(workflow, "Todo")


# ---------------------------------------------------------------------------
# branch policy
# ---------------------------------------------------------------------------


def test_set_branch_policy_writes_agent_keys(workflow: Path) -> None:
    set_branch_policy(
        workflow, feature_base_branch="dev", auto_merge_target_branch="main"
    )
    text = workflow.read_text(encoding="utf-8")
    assert "feature_base_branch: dev" in text
    assert "auto_merge_target_branch: main" in text
    assert "# operator note: keep Todo first" in text


# ---------------------------------------------------------------------------
# review follow-ups (2026-07-02)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# continuous improvement settings
# ---------------------------------------------------------------------------


def test_set_continuous_improvement_settings_writes_keys(workflow: Path) -> None:
    set_continuous_improvement_settings(
        workflow, enabled=True, interval_ms=120_000, max_turns=10
    )
    text = workflow.read_text(encoding="utf-8")
    assert "continuous_improvement:" in text
    assert "enabled: true" in text
    assert "interval_ms: 120000" in text
    assert "max_turns: 10" in text


def test_set_continuous_improvement_settings_preserves_comments_and_order(
    workflow: Path,
) -> None:
    before = workflow.read_text(encoding="utf-8")
    set_continuous_improvement_settings(workflow, enabled=True)
    after = workflow.read_text(encoding="utf-8")
    # Existing sections/comments/order are untouched — only the new section
    # is appended, so every pre-existing line still appears verbatim.
    for line in before.splitlines():
        assert line in after
    assert "# operator note: keep Todo first" in after
    assert after.index("tracker:") < after.index("agent:") < after.index("prompts:")
    assert "Body text with {{ issue.identifier }} stays untouched." in after


def test_set_continuous_improvement_settings_partial_update_keeps_others(
    workflow: Path,
) -> None:
    set_continuous_improvement_settings(
        workflow, enabled=True, interval_ms=120_000, max_turns=10
    )
    set_continuous_improvement_settings(workflow, enabled=False)
    text = workflow.read_text(encoding="utf-8")
    assert "enabled: false" in text
    # interval_ms/max_turns from the first call are untouched.
    assert "interval_ms: 120000" in text
    assert "max_turns: 10" in text


def test_set_continuous_improvement_settings_accepts_lower_bound_interval(
    workflow: Path,
) -> None:
    set_continuous_improvement_settings(workflow, interval_ms=60_000)
    text = workflow.read_text(encoding="utf-8")
    assert "interval_ms: 60000" in text


def test_set_continuous_improvement_settings_rejects_invalid_interval(
    workflow: Path,
) -> None:
    with pytest.raises(WorkflowMutationError):
        set_continuous_improvement_settings(workflow, interval_ms=1000)


def test_set_continuous_improvement_settings_rejects_invalid_max_turns(
    workflow: Path,
) -> None:
    with pytest.raises(WorkflowMutationError):
        set_continuous_improvement_settings(workflow, max_turns=-1)


def test_set_continuous_improvement_settings_agent_kind_roundtrip(
    workflow: Path,
) -> None:
    set_continuous_improvement_settings(workflow, agent_kind="Claude")
    text = workflow.read_text(encoding="utf-8")
    assert "agent_kind: claude" in text
    cfg = build_service_config(load_workflow(workflow))
    assert cfg.continuous_improvement.agent_kind == "claude"

    # Explicit "" clears back to inherit-workflow-default.
    set_continuous_improvement_settings(workflow, agent_kind="")
    text = workflow.read_text(encoding="utf-8")
    assert "agent_kind: claude" not in text
    cfg = build_service_config(load_workflow(workflow))
    assert cfg.continuous_improvement.agent_kind == ""


def test_set_continuous_improvement_settings_rejects_unknown_agent_kind(
    workflow: Path,
) -> None:
    with pytest.raises(WorkflowMutationError):
        set_continuous_improvement_settings(workflow, agent_kind="bogus")


def test_malformed_yaml_raises_mutation_error(tmp_path: Path) -> None:
    path = tmp_path / "WORKFLOW.md"
    path.write_text("---\ntracker: [unclosed\n---\nbody\n", encoding="utf-8")
    with pytest.raises(WorkflowMutationError, match="YAML"):
        set_branch_policy(path, feature_base_branch="dev")


def test_mutation_error_message_has_no_code_prefix() -> None:
    err = WorkflowMutationError("title is required")
    assert err.message == "title is required"
    assert err.code == "workflow_mutation_error"


def test_omitted_description_preserved_and_empty_string_clears(workflow: Path) -> None:
    apply_states_update(
        workflow,
        [
            StateSpec(name="Todo"),  # description omitted -> keep "triage"
            StateSpec(name="Building", previous_name="Doing"),  # rename keeps "build"
            StateSpec(name="Done", terminal=True),
            StateSpec(name="Archive", terminal=True),
        ],
    )
    text = workflow.read_text(encoding="utf-8")
    assert "Todo: triage" in text
    assert "Building: build" in text
    # Explicit empty string clears.
    apply_states_update(
        workflow,
        [
            StateSpec(name="Todo", description=""),
            StateSpec(name="Building"),
            StateSpec(name="Done", terminal=True),
            StateSpec(name="Archive", terminal=True),
        ],
    )
    text = workflow.read_text(encoding="utf-8")
    assert "Todo: triage" not in text
    assert "Building: build" in text


def test_column_count_cap(workflow: Path) -> None:
    specs = [StateSpec(name=f"Col{i}") for i in range(101)] + [
        StateSpec(name="Done", terminal=True)
    ]
    with pytest.raises(WorkflowMutationError, match="too many columns"):
        apply_states_update(workflow, specs)
