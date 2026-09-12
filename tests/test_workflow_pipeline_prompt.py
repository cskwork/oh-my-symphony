"""Coverage for the shipped 4-stage prompt templates."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.issue import BlockerRef, Issue
from symphony.prompt import build_first_turn_prompt, build_prompt_env, render
from symphony.workflow import build_service_config, load_workflow


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_FILES = ("WORKFLOW.file.example.md", "WORKFLOW.example.md")

STAGE_HEADINGS_BY_STATE = {
    "Todo": "### TRIAGE",
    "In Progress": "### IMPLEMENT",
    "Verify": "### VERIFY",
    "Document": "### DOCUMENT",
    "Done": "### DONE",
}

IN_PROGRESS_RULES = (
    "proof map",
    "before state",
    "after target",
    "what would still be `Not proven`",
    "## Plan",
    "## Acceptance Tests",
    "## Done Signals",
    "## Implementation",
    "## Self-Critique",
    "Static browser apps that claim direct `file://` support must boot from `file://`",
    "## Pipeline Route",
    "state to `Verify`",
)

VERIFY_RULES = (
    "Verify has three jobs: review, QA, and merge preflight",
    # F-10: exactly one merge per ticket, created by the orchestrator at Done.
    "Verify proves, Document documents, the orchestrator merges",
    "what worked",
    "what failed",
    "what is not covered",
    "How to re-run",
    "## Security Audit",
    "## Review Findings",
    "Browser UI work must drive Playwright/headless Chromium",
    "fail on module-script/CORS boot errors",
    "DOM shims are smoke only, never final Verify authority",
    "## Environment Block",
    "Full integration gate",
    "committed target branch",
    "register new Kanban/board bug tickets",
    # F-09: the lane names the validated write verb instead of telling the
    # agent to hand-edit `blocked_by` frontmatter.
    "board update {{ issue.identifier }} --add-blocked-by <ID>".replace(
        "{{ issue.identifier }}", "DEMO-1"
    ),
    "never hand-edit frontmatter",
    "rerun from scratch",
    "## QA Evidence",
    "## AC Scorecard",
    "Evidence cells must cite files under `docs/DEMO-1/` as `qa/...` or `work/...`",
    "## QA Failure",
    "git merge-tree --write-tree",
    "Do not merge the target branch into the ticket workspace",
    "## Merge Status",
    "Set state to `Document`",
)

APP_RELEASE_VERIFY_RULES = (
    "Tickets labeled `app-release`",
    "release-contract.yaml",
    "docs/DEMO-1/qa/release-evidence.json",
    "exact target commit",
    "desktop, tablet, and mobile",
    "symphony release check",
    "Historical Markdown or ledger GREEN text is never current approval",
    '"$SYMPHONY_WORKFLOW_DIR/WORKFLOW.md"',
    "structurally valid repairable RED",
    "forward historical transition",
    "Evidence, schema, or environment errors stay in `Verify`",
    "rebase this evidence-only verifier branch",
    "never merges it into the target",
)

DOCUMENT_RULES = (
    "llm-wiki",
    "INDEX.md",
    "## Wiki Updates",
    "## As-Is -> To-Be Report",
    "Final History Gate",
    # The gate moved to the host: the agent is told to stay out of git, and
    # told not to self-block when its sandbox refuses a local git write.
    "Symphony runs it, not you",
    "Do not stage, commit, or publish the delivery record yourself",
    "git ls-remote",
    "Never set `Blocked` because a local git command failed",
    "## Human Review",
    "Set state to `Done`",
    "Set state to `Human Review` only",
    "Operator skip",
)

DONE_REPORT_SHAPE = (
    "## As-Is -> To-Be Report",
    "### Goal",
    "### As-Is",
    "### To-Be",
    "### Reasoning",
    "### Evidence",
    "### Not Covered",
    "### How To Re-run",
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


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_shipped_workflows_allow_registry_access_without_full_sandbox(
    workflow: str,
) -> None:
    cfg = _load(workflow)

    assert cfg.codex.thread_sandbox == "workspace-write"
    assert cfg.codex.turn_sandbox_policy == {
        "type": "workspaceWrite",
        "networkAccess": True,
    }


def test_workflow_docs_explain_registry_network_policy() -> None:
    docs = (
        REPO_ROOT / "skills" / "symphony-skill" / "reference" / "workflow-config.md"
    ).read_text(encoding="utf-8")
    normalized_docs = " ".join(docs.split())

    assert "turn_sandbox_policy: {type: workspaceWrite, networkAccess: true}" in docs
    assert "package registries" in docs
    assert "does not require `danger-full-access`" in normalized_docs


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
        "Document",
    )
    for required in ("Todo", "In Progress", "Verify", "Document"):
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
    # F-10: the Verify lane must not hand-merge — that produced two merge
    # commits per ticket and landed code before Document wrote the wiki.
    assert "orchestrator will merge at Done" in rendered
    assert "Do NOT create the merge commit yourself" in rendered
    assert "create the explicit `--no-ff` merge commit" not in rendered


def test_file_verify_stage_carries_the_app_release_machine_gate() -> None:
    cfg = _load("WORKFLOW.file.example.md")
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(_issue("Verify"), attempt=None),
    )

    for phrase in APP_RELEASE_VERIFY_RULES:
        assert phrase in rendered
    assert "$SYMPHONY_WORKFLOW_PATH" not in rendered


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
    assert "Set state to `Document`" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_document_stage_writes_wiki_and_done_or_intervention_handoff(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Document"),
        build_prompt_env(_issue("Document"), attempt=None),
    )

    for phrase in DOCUMENT_RULES:
        assert phrase in rendered
    for heading in HUMAN_REVIEW_HANDOFF_SHAPE:
        assert heading in rendered
    # F-07: read-only history inspection is the lane's job, not a cage.
    assert "Read-only inspection of history is expected" in rendered
    assert "do NOT run git history commands" not in rendered
    # F-08: the lane's write scope matches its charter (docs, not just wiki).
    assert "the user-facing docs this change touched" in rendered
    assert "Update every user-facing doc the change touched" in rendered
    assert "no source or test edits" in rendered
    # The ownership boundaries that ARE legitimate stay.
    assert "Do not create commits, tags, branches, or pushes" in rendered
    # F-27: `## Document Defect` was consumed as a rewind section that no
    # prompt ever told an agent to write.
    assert "## Document Defect" in rendered
    assert "set state to `In Progress`" in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_document_stage_receives_board_health_only_when_present(workflow: str) -> None:
    cfg = _load(workflow)
    template = cfg.prompt_template_for_state("Document")
    quiet = render(template, build_prompt_env(_issue("Document"), attempt=None))
    assert "Board health" not in quiet
    noisy = render(
        template,
        build_prompt_env(
            _issue("Document"),
            attempt=None,
            board_health="- lane `verify`: rewound 3x, contract failed 2x",
        ),
    )
    assert "Board health (orchestrator stats, last 30 days)" in noisy
    assert "rewound 3x" in noisy
    assert "## Learnings" in noisy


@pytest.mark.parametrize("flavor", ("file", "linear"))
def test_base_prompt_declares_four_stage_pipeline_and_skip_document(flavor: str) -> None:
    text = (REPO_ROOT / "docs" / "symphony-prompts" / flavor / "base.md").read_text(
        encoding="utf-8"
    )

    assert "Production pipeline (4 active stages)" in text
    assert "Board card mental model" in text
    assert "Each lane answers one human question" in text
    assert "Todo  ->  In Progress  ->  Verify  ->  Document" in text
    assert "critical/manual intervention -> Human Review" in text
    assert "Use `Human Review` only for real critical/manual intervention" in text
    assert "Use `Not proven` when evidence is missing" in text
    assert "Never skip Verify" in text


def test_operator_skill_routes_ticket_registration_by_work_type() -> None:
    skill = (REPO_ROOT / "skills" / "symphony-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    delegation = (
        REPO_ROOT
        / "skills"
        / "symphony-skill"
        / "reference"
        / "delegation.md"
    ).read_text(encoding="utf-8")
    decomposition = (
        REPO_ROOT
        / "skills"
        / "symphony-skill"
        / "oneshot"
        / "reference"
        / "decomposition.md"
    ).read_text(encoding="utf-8")

    assert "production-ready app delivery planning" in skill
    assert "Work-type route: classify the request before ticket creation" in skill
    assert "Do not force every task through" in skill
    assert "product-delivery shape" in skill
    assert "App-delivery work starts with discovery" in skill
    assert "Human Review history gate" in skill
    assert "committed and pushed" in skill
    assert "Final integration loop" in skill
    assert "Work type | First ticket owns | Final proof owns" in delegation
    assert "Bugfix | Reproduction, suspected area, failing test/log" in delegation
    assert "Do not register product-discovery tickets for a narrow bugfix" in delegation
    assert "target customer and the job they need done" in delegation
    assert "Reproduce failure with failing test/log" in delegation
    assert "Behavior contract + acceptance matrix" in delegation
    assert "Product readiness brief + release matrix" in delegation
    assert "Release verification on merged target" in delegation
    assert "register new Kanban bug tickets" in delegation
    assert "loop until the merged target passes" in delegation
    assert "curl 000" in delegation
    assert "Route before slicing" in decomposition
    assert "Bugfix**: reproduction -> minimal fix -> regression verification" in decomposition
    assert "Feature/enhancement**: behavior contract" in decomposition
    assert "Do not use the app-delivery pattern for every prompt" in decomposition
    assert "If this is app delivery, is the product defined?" in decomposition
    assert "Merged-target release verification" in decomposition
    assert "defect-registration loop" in decomposition


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


def test_file_base_prompt_renders_full_ticket_path_outside_description() -> None:
    cfg = _load("WORKFLOW.file.example.md")
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(
            _issue("Verify", description="ticket body marker"),
            attempt=None,
            full_ticket_path="release-kanban/DEMO-1.md",
        ),
    )

    full_ticket_index = rendered.index("Full ticket: release-kanban/DEMO-1.md")
    description_index = rendered.index("## Description")
    description_block = rendered.split("## Description", 1)[1].split(
        "## Production pipeline",
        1,
    )[0]

    assert full_ticket_index < description_index
    assert "ticket body marker" in description_block
    assert "Full ticket: release-kanban/DEMO-1.md" not in description_block
    assert "Ticket file: `release-kanban/DEMO-1.md`." in rendered
    assert "Ticket file: `kanban/DEMO-1.md`." not in rendered


def test_file_base_prompt_does_not_direct_workers_to_kanban_without_path() -> None:
    cfg = _load("WORKFLOW.file.example.md")
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(_issue("Verify"), attempt=None),
    )

    assert "use the configured tracker board root" in rendered
    assert "kanban/{{ issue.identifier }}.md" not in rendered


def test_contract_rewind_prompt_uses_failing_rows_not_full_history() -> None:
    body = """\
Original task: make contract failures cheap and specific.

## Acceptance Criteria

- Contract rewind prompt includes failing rows and expected evidence shape.

## Contract Failure
Stage `Verify` did not produce the required outputs.
Missing:
- old vague failure

```text
stale historical log that must not be resent
```

## Implementation

large implementation history that should not be in a contract rewind

## Contract Failure
Stage `Verify` did not produce the required outputs.

Failing rows:
- `## AC Scorecard` row 1 evidence `validated in source`
  expected durable evidence such as `docs/DEMO-1/qa/evidence.md`,
  `qa/...`, or `work/...`.

Symphony rewound the ticket so the producing stage can complete the contract.
"""
    prompt, env = build_first_turn_prompt(
        prompt_template=(
            "Full ticket: {{ issue.full_ticket_path }}\n"
            "## Description\n\n{{ issue.description }}"
        ),
        issue=_issue("Verify", description=body),
        attempt=None,
        language="en",
        max_turns=3,
        is_rewind=True,
        compact_issue_context=True,
        full_ticket_path="kanban/DEMO-1.md",
    )

    compact_description = env["issue"]["description"]
    assert "Full ticket: kanban/DEMO-1.md" in prompt
    assert "Original task: make contract failures cheap and specific." in prompt
    assert "## Acceptance Criteria" in compact_description
    assert "`## AC Scorecard` row 1" in compact_description
    assert "validated in source" in compact_description
    assert "docs/DEMO-1/qa/evidence.md" in compact_description

    assert "old vague failure" not in compact_description
    assert "stale historical log" not in compact_description
    assert "large implementation history" not in compact_description


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_base_prompt_directs_deliverables_to_the_artifacts_dir(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(
            _issue("Verify"), attempt=None, artifacts_dir=".symphony-artifacts"
        ),
    )
    assert "`.symphony-artifacts/` at the workspace root" in rendered
    assert "manifest.json" in rendered
    # The docs/ evidence root stays authoritative for proof.
    assert "Every ticket artefact lives under `docs/DEMO-1/`." in rendered


@pytest.mark.parametrize("workflow", WORKFLOW_FILES)
def test_base_prompt_omits_artifacts_directive_when_disabled(workflow: str) -> None:
    cfg = _load(workflow)
    rendered = render(
        cfg.prompt_template_for_state("Verify"),
        build_prompt_env(_issue("Verify"), attempt=None),
    )
    assert "Board deliverables" not in rendered
    assert "manifest.json" not in rendered
