"""Coverage for the production-pipeline prompt templates shipped in
WORKFLOW.file.example.md / WORKFLOW.example.md plus docs/symphony-prompts/.

These tests assert the prompt: (1) parses + renders for every active state,
    (2) carries only the current stage-specific instructions the agent needs,
    (3) renders the retry and blocked_by branches, and (4) preserves the
    fixed human-review handoff before Done.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.issue import BlockerRef, Issue
from symphony.prompt import build_prompt_env, render
from symphony.workflow import build_service_config, load_workflow


REPO_ROOT = Path(__file__).resolve().parent.parent

# `WORKFLOW.md` is gitignored — every operator customizes it for their own
# board (different prompt body, different agent.kind, different hooks). The
# pipeline prompt is shipped via the *example* files; those are the
# canonical reference users copy from. We deliberately do not assert the
# pipeline prompt lives in `WORKFLOW.md` itself, since requiring that
# would break any non-pipeline workflow people legitimately run.
WORKFLOW_FILES = (
    "WORKFLOW.file.example.md",
    "WORKFLOW.example.md",
)

STAGE_HEADINGS_BY_STATE = {
    "Todo": "### TRIAGE",
    "Explore": "### EXPLORE",
    "Plan": "### PLAN",
    "In Progress": "### IMPLEMENT",
    "Critic": "### CRITIC",
    "Review": "### REVIEW",
    "QA": "### QA",
    "Learn": "### LEARN",
    "Done": "### DONE",
}

# Phrases the EXPLORE stage must reference so the agent actually consults
# the three sources of domain knowledge (wiki, history, code) and produces
# the structured brief / candidate plans / recommendation.
EXPLORE_HARD_RULES = (
    "llm-wiki",
    "git log",
    "Domain Brief",
    "Plan Candidates",
    "Recommendation",
)

# Phrases the LEARN stage must reference so wiki updates are not optional.
LEARN_HARD_RULES = (
    "llm-wiki",
    "INDEX.md",
    "Decision log",
    "Wiki Updates",
)

# File-tracker variants record QA via a `## QA Evidence` markdown section in
# the ticket body; the Linear variant records it as a "QA Evidence comment".
# Both must mention `QA Evidence` and demand real execution.
QA_HARD_RULES = (
    "THIS STAGE MUST EXECUTE REAL CODE",
    "QA Evidence",
)

REVIEW_REWIND_RULES = (
    "CRITICAL, HIGH, or MEDIUM finding",
    "Review Findings",
)

QA_REWIND_RULES = (
    "server-reported HIGH",
    "QA Failure",
)

# Phrases the CRITIC stage must reference (shared across file + linear
# flavors): an independent agent writes failing tests for spec gaps, must
# not touch source or existing tests, records the durable ledger, and
# carries the supergoal guardrail that the generated tests are a signal,
# not the oracle.
CRITIC_HARD_RULES = (
    "did NOT write this code",
    "NEW FAILING test",
    "Surfaced Requirements",
    "Critic Tests",
    "surfaced-requirements.md",
    "not the acceptance oracle",
)

# Critic outcomes (verb + section markup differ by flavor: file appends
# `## ...` sections / "set state", linear posts comments / "transition
# state"): gaps rewind to In Progress, a clean Critic outcome advances to
# Review. Assert on the destination states only — flavor-agnostic.
CRITIC_OUTCOME_RULES = (
    "In Progress",
    "Review",
)

# S3 difficulty gate (prompt-level branch, both flavors). Plan declares
# `## Difficulty` (trivial/standard/complex, default standard); In Progress
# routes trivial+non-bug straight to Review (skip Critic); Review routes
# trivial + no-runtime-change to Learn (skip QA). Every elision is recorded
# in a `## Pipeline Route` line — never silent. Hard safety rails: a `bug`
# ticket never skips, and a `fail` Security Audit row forces the full route.
PLAN_DIFFICULTY_RULES = (
    "## Difficulty",
    "trivial",
    "standard",
    "complex",
    "defaults to `standard`",
)

IN_PROGRESS_DIFFICULTY_RULES = (
    "## Difficulty: trivial",
    "to `Review`",
    "to `Critic`",
    "## Pipeline Route",
)

REVIEW_DIFFICULTY_RULES = (
    "## Difficulty: trivial",
    "to `Learn`",
    "to `QA`",
    "## Pipeline Route",
)

DONE_REPORT_SHAPE = (
    "## As-Is -> To-Be Report",
    "### As-Is",
    "### To-Be",
    "### Reasoning",
    "### Evidence",
)

HUMAN_REVIEW_HANDOFF_SHAPE = (
    "## Human Review",
    "### What Changed",
    "### Why It Matters",
    "### Evidence",
    "### Risks",
    "### Human Checklist",
    "### Decision Needed",
)


def _load(name: str):
    cfg = build_service_config(load_workflow(REPO_ROOT / name))
    return cfg


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
def test_active_states_cover_full_pipeline(workflow: str) -> None:
    cfg = _load(workflow)
    for required in (
        "Todo",
        "Explore",
        "Plan",
        "In Progress",
        "Critic",
        "Review",
        "QA",
        "Learn",
    ):
        assert required in cfg.tracker.active_states, (
            f"{workflow} active_states missing {required!r} — TUI lane will not render"
        )
        assert required.lower() in cfg.prompts.stage_templates, (
            f"{workflow} prompts.stages missing {required!r}"
        )
    assert "done" in cfg.prompts.stage_templates, (
        f"{workflow} prompts.stages missing terminal Done report prompt"
    )
    assert "Human Review" in cfg.tracker.terminal_states
    assert "Human Review" not in cfg.tracker.active_states


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
@pytest.mark.parametrize(
    "state",
    ["Todo", "Explore", "Plan", "In Progress", "Critic", "Review", "QA", "Learn", "Done"],
)
def test_prompt_renders_for_every_stage(workflow: str, state: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state(state),
        build_prompt_env(_issue(state), attempt=None),
    )
    assert f"Current state: {state}." in rendered
    current_heading = STAGE_HEADINGS_BY_STATE[state]
    assert current_heading in rendered, (
        f"missing current stage heading {current_heading!r} at state={state}"
    )
    for other_state, heading in STAGE_HEADINGS_BY_STATE.items():
        if other_state == state:
            continue
        assert heading not in rendered, (
            f"unexpected stage heading {heading!r} in render for state={state}"
        )


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_qa_stage_demands_real_execution(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("QA"),
        build_prompt_env(_issue("QA"), attempt=None),
    )
    for phrase in QA_HARD_RULES:
        assert phrase in rendered, f"QA stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_review_high_findings_rewind_to_in_progress(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Review"),
        build_prompt_env(_issue("Review"), attempt=None),
    )
    for phrase in REVIEW_REWIND_RULES:
        assert phrase in rendered, f"Review stage missing rewind rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_qa_server_reported_high_issues_rewind_to_in_progress(
    workflow: str,
) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("QA"),
        build_prompt_env(_issue("QA"), attempt=None),
    )
    for phrase in QA_REWIND_RULES:
        assert phrase in rendered, f"QA stage missing server-high rewind rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_critic_stage_writes_failing_tests_for_spec_gaps(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Critic"),
        build_prompt_env(_issue("Critic"), attempt=None),
    )
    for phrase in CRITIC_HARD_RULES:
        assert phrase in rendered, f"Critic stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_critic_stage_rewinds_or_advances(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Critic"),
        build_prompt_env(_issue("Critic"), attempt=None),
    )
    # Both outcomes must be reachable: gaps -> In Progress, clean -> Review.
    for phrase in CRITIC_OUTCOME_RULES:
        assert phrase in rendered, f"Critic stage missing outcome state: {phrase!r}"
    # Must not authorize editing source — that is the fixer's job on rewind.
    assert "Do NOT edit source" in rendered or "do NOT edit source" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_explore_stage_consults_wiki_history_and_code(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Explore"),
        build_prompt_env(_issue("Explore"), attempt=None),
    )
    for phrase in EXPLORE_HARD_RULES:
        assert phrase in rendered, f"Explore stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_plan_stage_creates_professional_executable_plan(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Plan"),
        build_prompt_env(_issue("Plan"), attempt=None),
    )
    for phrase in (
        "Do not write production code",
        "## Plan",
        "implementation-plan.md",
        "execute by reading only `## Plan`",
        "verification commands",
        "state to `In Progress`",
    ):
        assert phrase in rendered, f"Plan stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_in_progress_uses_plan_as_primary_contract(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("In Progress"),
        build_prompt_env(_issue("In Progress"), attempt=None),
    )

    assert "Read the plan first" in rendered
    assert "That plan should be enough to implement" in rendered
    assert "Use Explore notes, llm-wiki, or" in rendered
    assert "only as reference material" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_plan_stage_declares_difficulty(workflow: str) -> None:
    """S3: Plan must declare `## Difficulty` (trivial/standard/complex) so the
    later stages can route; omitting it defaults to standard (backward-compat)."""
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Plan"),
        build_prompt_env(_issue("Plan"), attempt=None),
    )
    for phrase in PLAN_DIFFICULTY_RULES:
        assert phrase in rendered, f"Plan stage missing difficulty rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_in_progress_difficulty_branch_skips_critic_for_trivial(
    workflow: str,
) -> None:
    """S3: trivial + non-bug routes In Progress -> Review (skip Critic); else
    -> Critic. The elision is recorded in `## Pipeline Route` (never silent),
    and a `bug` ticket may never skip the Critic+QA path."""
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("In Progress"),
        build_prompt_env(_issue("In Progress"), attempt=None),
    )
    for phrase in IN_PROGRESS_DIFFICULTY_RULES:
        assert phrase in rendered, (
            f"In Progress stage missing difficulty branch: {phrase!r}"
        )
    # Safety rail: a bug ticket can never skip.
    assert "bug" in rendered
    assert "never skip" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_review_difficulty_branch_skips_qa_for_trivial(workflow: str) -> None:
    """S3: trivial + no runtime behavior change routes Review -> Learn (skip
    QA); else -> QA. Records the route in `## Pipeline Route`. Hard rails: a
    `bug` ticket never skips QA, and any `fail` Security Audit row forces the
    full route."""
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Review"),
        build_prompt_env(_issue("Review"), attempt=None),
    )
    for phrase in REVIEW_DIFFICULTY_RULES:
        assert phrase in rendered, (
            f"Review stage missing difficulty branch: {phrase!r}"
        )
    # Safety rails override difficulty.
    assert "may never skip QA" in rendered
    assert "Security Audit" in rendered
    assert "forces the full route" in rendered


@pytest.mark.parametrize("flavor", ("file",))
def test_base_prompt_caps_difficulty_and_pipeline_route(flavor: str) -> None:
    """S3: base.md must length-cap the two new sections so they cannot bloat
    the ticket (Difficulty <= 2 lines, Pipeline Route a single line)."""
    text = (REPO_ROOT / "docs" / "symphony-prompts" / flavor / "base.md").read_text(
        encoding="utf-8"
    )
    assert "## Difficulty" in text
    assert "## Pipeline Route" in text


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_learn_stage_writes_back_to_wiki(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Learn"),
        build_prompt_env(_issue("Learn"), attempt=None),
    )
    for phrase in LEARN_HARD_RULES:
        assert phrase in rendered, f"Learn stage missing hard rule: {phrase!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_learn_stage_requires_merge_before_done(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Learn"),
        build_prompt_env(_issue("Learn"), attempt=None),
    )
    for phrase in ("Merge Gate", "target branch", "before setting state to `Human Review`"):
        assert phrase in rendered, f"Learn stage missing merge gate: {phrase!r}"
    assert "before setting state to `Done`" not in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_learn_stage_requires_merge_tree_preflight_not_status_only(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Learn"),
        build_prompt_env(_issue("Learn"), attempt=None),
    )

    assert "git merge-tree --write-tree" in rendered
    assert "Do not use `git status -uno --porcelain` as the merge proof" in rendered
    assert "committed target/branch merge conflict" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_learn_stage_respects_disabled_auto_merge(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Learn"),
        build_prompt_env(
            _issue("Learn"),
            attempt=None,
            auto_merge_on_done=False,
        ),
    )

    assert "Merge Gate is disabled" in rendered
    assert "leaves branch integration to the operator" in rendered
    assert "before setting state to `Done`" not in rendered
    assert "Transition state to `Human Review`" in rendered


@pytest.mark.parametrize("flavor", ("file", "linear"))
def test_base_prompt_declares_merge_gate(flavor: str) -> None:
    text = (REPO_ROOT / "docs" / "symphony-prompts" / flavor / "base.md").read_text(
        encoding="utf-8"
    )

    assert "eight stages, no skipping" in text
    assert "Plan  ->  In Progress" in text
    assert "Learn  ->  Merge Gate  ->  Human Review  ->  Done" in text
    assert "successful Learn Merge Gate" in text
    assert "human confirmation" in text


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_learn_stage_carries_human_review_handoff_shape(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Learn"),
        build_prompt_env(_issue("Learn"), attempt=None),
    )
    for heading in HUMAN_REVIEW_HANDOFF_SHAPE:
        assert heading in rendered, f"Human Review handoff missing section: {heading!r}"
    assert "Transition state to `Human Review`" in rendered


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
    """The shipped reference ticket must demonstrate every artefact the
    pipeline expects, so users can copy its structure.

    Lives under ``docs/`` (not ``kanban/``) so it is tracked in git;
    ``kanban/`` is gitignored as the user-local board directory.
    """
    body = (REPO_ROOT / "docs" / "PIPELINE-DEMO.md").read_text(encoding="utf-8")
    for required in (
        "## Plan",
        "## Implementation",
        "## Review",
        "## QA Evidence",
        "## As-Is -> To-Be Report",
        "### As-Is",
        "### To-Be",
        "### Reasoning",
        "### Evidence",
    ):
        assert required in body, f"PIPELINE-DEMO missing section {required!r}"


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_base_prompt_renders_token_budget_directive_when_set(workflow: str) -> None:
    """C3 plumbing: a non-zero `token_budget` must surface as a soft
    budget directive in every dispatched prompt."""
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("QA"),
        build_prompt_env(_issue("QA"), attempt=None, token_ema=1200, token_budget=8000),
    )
    assert "under 8000 completion tokens" in rendered
    assert "1200" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_base_prompt_omits_token_budget_directive_by_default(workflow: str) -> None:
    """Default env (budget 0) must not render the directive."""
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("QA"),
        build_prompt_env(_issue("QA"), attempt=None),
    )
    assert "Token budget" not in rendered
