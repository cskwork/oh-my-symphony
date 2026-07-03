"""Coverage for the shipped 4-stage prompt templates."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.issue import BlockerRef, Issue
from symphony.prompt import build_prompt_env, render
from symphony.workflow import build_service_config, load_workflow


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_FILES = ("WORKFLOW.file.example.md", "WORKFLOW.example.md")

STAGE_HEADINGS_BY_STATE = {
    "Todo": "### TRIAGE",
    "In Progress": "### IMPLEMENT",
    "Verify": "### VERIFY",
    "Learn": "### LEARN",
    "Done": "### DONE",
}

IN_PROGRESS_RULES = (
    "## Plan",
    "## Acceptance Tests",
    "## Done Signals",
    "## Implementation",
    "## Self-Critique",
    "## Pipeline Route",
    "state to `Verify`",
)

VERIFY_RULES = (
    "Verify has three jobs: review, QA, and merge preflight/merge",
    "## Security Audit",
    "## Review Findings",
    "Browser UI work must drive Playwright/headless Chromium",
    "DOM shims are smoke only, never final Verify authority",
    "## Environment Block",
    "## QA Evidence",
    "## AC Scorecard",
    "Evidence cells must cite files under `docs/DEMO-1/` as `qa/...` or `work/...`",
    "## QA Failure",
    "git merge-tree --write-tree",
    "## Merge Status",
    "Set state to `Learn`",
)

LEARN_RULES = (
    "llm-wiki",
    "INDEX.md",
    "## Wiki Updates",
    "## Human Review",
    "Set state to `Human Review`",
    "Operator skip",
)

DONE_REPORT_SHAPE = (
    "## As-Is -> To-Be Report",
    "### As-Is",
    "### To-Be",
    "### Reasoning",
    "### Evidence",
)

HUMAN_REVIEW_HANDOFF_SHAPE = (
    "### What Changed",
    "### Why It Matters",
    "### Evidence",
    "### Risks",
    "### Human Checklist",
    "### Decision Needed",
)


def _load(name: str):
    return build_service_config(load_workflow(REPO_ROOT / name))


def _issue(state: str, **overrides) -> Issue:
    base = dict(
        id="DEMO-1",
        identifier="DEMO-1",
        title="t",
        description="d",
        priority=2,
        state=state,
        labels=(),
    )
    base.update(overrides)
    return Issue(**base)


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_active_states_cover_four_stage_pipeline(workflow: str) -> None:
    cfg = _load(workflow)

    assert tuple(cfg.tracker.active_states) == (
        "Todo",
        "In Progress",
        "Verify",
        "Learn",
    )
    for required in ("Todo", "In Progress", "Verify", "Learn"):
        assert required.lower() in cfg.prompts.stage_templates
    assert "done" in cfg.prompts.stage_templates
    assert "Human Review" in cfg.tracker.terminal_states
    assert "Human Review" not in cfg.tracker.active_states


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
@pytest.mark.parametrize("state", list(STAGE_HEADINGS_BY_STATE))
def test_prompt_renders_only_current_stage(workflow: str, state: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state(state),
        build_prompt_env(_issue(state), attempt=None),
    )

    assert f"Current state: {state}." in rendered
    current_heading = STAGE_HEADINGS_BY_STATE[state]
    assert current_heading in rendered
    for other_state, heading in STAGE_HEADINGS_BY_STATE.items():
        if other_state != state:
            assert heading not in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_todo_routes_actionable_work_to_in_progress(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Todo"),
        build_prompt_env(_issue("Todo"), attempt=None),
    )

    assert "routing to In Progress" in rendered
    assert "set state to `In Progress`" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_in_progress_stage_combines_plan_build_and_self_critique(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("In Progress"),
        build_prompt_env(_issue("In Progress"), attempt=None),
    )

    for phrase in IN_PROGRESS_RULES:
        assert phrase in rendered
    assert "docs/{{ issue.identifier }}/work/" not in rendered
    assert "docs/DEMO-1/work/" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_verify_stage_demands_review_qa_and_merge_evidence(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(_issue("Verify"), attempt=None),
    )

    for phrase in VERIFY_RULES:
        assert phrase in rendered
    assert "Do not use `git status -uno --porcelain` as merge proof" in rendered
    assert "set state to `In Progress`" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_verify_stage_respects_disabled_auto_merge(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(
            _issue("Verify"),
            attempt=None,
            auto_merge_on_done=False,
        ),
    )

    assert "Merge Gate is disabled" in rendered
    assert "leaves branch integration to the operator" in rendered
    assert "Set state to `Learn`" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_learn_stage_writes_wiki_and_human_review_handoff(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Learn"),
        build_prompt_env(_issue("Learn"), attempt=None),
    )

    for phrase in LEARN_RULES:
        assert phrase in rendered
    for heading in HUMAN_REVIEW_HANDOFF_SHAPE:
        assert heading in rendered
    assert "Do NOT edit source or run the Merge Gate here" in rendered


@pytest.mark.parametrize("flavor", ("file", "linear"))
def test_base_prompt_declares_four_stage_pipeline_and_skip_learn(flavor: str) -> None:
    text = (REPO_ROOT / "docs" / "symphony-prompts" / flavor / "base.md").read_text(
        encoding="utf-8"
    )

    assert "Production pipeline (4 active stages)" in text
    assert "Todo  ->  In Progress  ->  Verify  ->  Learn" in text
    assert "operator may skip Learn to Human Review" in text
    assert "Never skip Verify" in text


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_retry_branch_renders(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("In Progress"),
        build_prompt_env(_issue("In Progress"), attempt=2),
    )
    assert "retry attempt 2" in rendered.lower()


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_blocked_by_branch_renders(workflow: str) -> None:
    cfg = _load(workflow)
    issue = _issue(
        "Todo",
        blocked_by=(BlockerRef(id="b1", identifier="B-1", state="Todo"),),
    )
    rendered = render(
        cfg.prompt_template_for_state("Todo"), build_prompt_env(issue, attempt=None)
    )
    assert "B-1 (Todo)" in rendered


def test_pipeline_demo_ticket_is_a_complete_worked_example() -> None:
    body = (REPO_ROOT / "docs" / "PIPELINE-DEMO.md").read_text(encoding="utf-8")
    for required in (
        "## Plan",
        "## Acceptance Tests",
        "## Done Signals",
        "## Implementation",
        "## Self-Critique",
        "## Security Audit",
        "## Review",
        "## QA Evidence",
        "## AC Scorecard",
        "## Merge Status",
        "## Wiki Updates",
        "## Human Review",
        *DONE_REPORT_SHAPE,
    ):
        assert required in body


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_base_prompt_renders_token_budget_directive_when_set(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(
            _issue("Verify"),
            attempt=None,
            token_ema=1200,
            token_budget=8000,
        ),
    )
    assert "under 8000 completion tokens" in rendered
    assert "1200" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_base_prompt_omits_token_budget_directive_by_default(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(_issue("Verify"), attempt=None),
    )
    assert "Token budget" not in rendered
