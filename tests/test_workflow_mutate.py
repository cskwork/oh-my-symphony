"""workflow.mutate — comment-preserving WORKFLOW.md edits for the web UI."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.workflow.mutate import (
    StateSpec,
    WorkflowMutationError,
    apply_states_update,
    read_prompt,
    resolve_prompt_path,
    set_branch_policy,
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
