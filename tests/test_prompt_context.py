"""Prompt-context compaction rules for repeated ticket histories."""

from __future__ import annotations

from symphony.issue import BlockerRef, Issue
from symphony.prompt_context import build_issue_prompt_context


def _body_with_history() -> str:
    return """\
TASK-004: duplicated title scaffold
Current state: Verify.
Labels: backend, reliability
Blocked by: TASK-003
User goal: reduce repeated prompt input while keeping audit evidence.
Scope: change only the prompt render path.

## Acceptance Criteria

- Verify prompts retain implementation and evidence.
- Rewind prompts retain the newest failure only.

## Plan

Old plan with unresolved work already superseded.

## Implementation

old implementation v1

## Review Findings

old finding v1 should be stale

## QA Failure

old failure v1 should be stale

```text
huge historical log line
```

## Implementation

new implementation v2

## Evidence Manifest

- docs/TASK-004/qa/evidence.md

## Changed Files

- src/symphony/prompt.py
- src/symphony/prompt_context.py

## Stage Contract Checklist

- stage contract row latest

## QA Evidence

- worked: pytest tests/test_prompt_context.py
- not covered: live jira rerun

## AC Scorecard

| AC | Result | Evidence |
|---|---|---|
| prompt shrinks | Pass | docs/TASK-004/qa/evidence.md |

## QA Failure

new failure v2
Evidence: docs/TASK-004/qa/latest-failure.md

## Wiki Updates

- docs/llm-wiki/INDEX.md updated

## Merge Status

- merge proof in docs/TASK-004/qa/merge.log
"""


def _issue(state: str) -> Issue:
    return Issue(
        id="TASK-004",
        identifier="TASK-004",
        title="Prompt compaction",
        description=_body_with_history(),
        priority=1,
        state=state,
        labels=("backend", "reliability"),
        blocked_by=(BlockerRef(id="TASK-003", identifier="TASK-003", state="Done"),),
    )


def test_verify_context_keeps_latest_evidence_and_drops_stale_history() -> None:
    context = build_issue_prompt_context(_issue("Verify"), state="Verify")

    assert "User goal: reduce repeated prompt input" in context
    assert "## Acceptance Criteria" in context
    assert "new implementation v2" in context
    assert "docs/TASK-004/qa/evidence.md" in context
    assert "## Changed Files" in context
    assert "src/symphony/prompt_context.py" in context
    assert "stage contract row latest" in context
    assert "## QA Evidence" in context
    assert "## AC Scorecard" in context

    assert "old implementation v1" not in context
    assert "old finding v1 should be stale" not in context
    assert "old failure v1 should be stale" not in context
    assert "huge historical log line" not in context


def test_rewind_context_keeps_newest_failure_and_scope() -> None:
    context = build_issue_prompt_context(
        _issue("In Progress"),
        state="In Progress",
        is_rewind=True,
    )

    assert "User goal: reduce repeated prompt input" in context
    assert "## Acceptance Criteria" in context
    assert "new failure v2" in context
    assert "docs/TASK-004/qa/latest-failure.md" in context

    assert "old failure v1 should be stale" not in context
    assert "old finding v1 should be stale" not in context
    assert "huge historical log line" not in context


def test_in_progress_fresh_dispatch_keeps_latest_failure_after_restart() -> None:
    context = build_issue_prompt_context(_issue("In Progress"), state="In Progress")

    assert "new failure v2" in context
    assert "docs/TASK-004/qa/latest-failure.md" in context
    assert "old failure v1 should be stale" not in context


def test_learn_context_keeps_latest_delivery_notes() -> None:
    context = build_issue_prompt_context(_issue("Learn"), state="Learn")

    assert "User goal: reduce repeated prompt input" in context
    assert "new implementation v2" in context
    assert "## QA Evidence" in context
    assert "## AC Scorecard" in context
    assert "## Merge Status" in context
    assert "## Wiki Updates" in context

    assert "old implementation v1" not in context
    assert "old failure v1 should be stale" not in context


def test_compact_description_does_not_duplicate_base_scaffolding() -> None:
    context = build_issue_prompt_context(_issue("Verify"), state="Verify")

    assert "TASK-004:" not in context
    assert "Current state:" not in context
    assert "Labels:" not in context
    assert "Blocked by:" not in context
    assert "User goal: reduce repeated prompt input" in context


def test_headed_description_scope_survives_compaction() -> None:
    issue = Issue(
        id="TASK-005",
        identifier="TASK-005",
        title="Headed scope",
        description="""\
## Description

Original user request under a heading must stay.

## Acceptance Criteria

- Keep the request.

## Implementation

old implementation should be dropped

## Implementation

new implementation should stay
""",
        priority=1,
        state="Verify",
        labels=(),
    )

    context = build_issue_prompt_context(issue, state="Verify")

    assert "## Description" in context
    assert "Original user request under a heading must stay." in context
    assert "new implementation should stay" in context
    assert "old implementation should be dropped" not in context


def test_leading_goal_and_background_scope_survive_compaction() -> None:
    issue = Issue(
        id="TASK-006",
        identifier="TASK-006",
        title="Goal scope",
        description="""\
## Goal

Keep the operator's real objective.

## Background

This explains why the work matters.

## Implementation

old implementation should be dropped

## Implementation

new implementation should stay
""",
        priority=1,
        state="Verify",
        labels=(),
    )

    context = build_issue_prompt_context(issue, state="Verify")

    assert "## Goal" in context
    assert "Keep the operator's real objective." in context
    assert "## Background" in context
    assert "This explains why the work matters." in context
    assert "new implementation should stay" in context
    assert "old implementation should be dropped" not in context
