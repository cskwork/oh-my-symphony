"""Contract validator for stage transitions.

The Symphony stage prompts encode contracts (Plan must have Acceptance
Tests, Review must produce a Security Audit table, QA must produce an AC
Scorecard, Done must list evidence paths). Strong models obey; weak models
skip. The validator parses the ticket body for those required sections
and lets the orchestrator rewind to the producing stage when a section is
missing or empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.orchestrator.contracts import (
    ContractResult,
    evaluate_contract,
)


# ---------------------------------------------------------------------------
# Plan contract (Plan → In Progress)
# ---------------------------------------------------------------------------


def test_plan_contract_passes_with_all_required_sections() -> None:
    body = """
## Plan
- step 1
- step 2

## Acceptance Tests
- `pytest tests/test_foo.py::test_bar`

## Done Signals
- `grep '^version' pyproject.toml` shows 0.6.7
"""
    result = evaluate_contract(
        producing_state="Plan",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is True
    assert result.missing == []


def test_plan_contract_fails_when_acceptance_tests_missing() -> None:
    body = """
## Plan
- step 1

## Done Signals
- some signal
"""
    result = evaluate_contract(
        producing_state="Plan",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Acceptance Tests" in result.missing
    assert "## Contract Failure" in result.note


def test_plan_contract_fails_when_acceptance_tests_empty() -> None:
    body = """
## Plan
- step 1

## Acceptance Tests

## Done Signals
- some signal
"""
    result = evaluate_contract(
        producing_state="Plan",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Acceptance Tests" in result.missing


def test_plan_contract_fails_when_done_signals_missing() -> None:
    body = """
## Plan
- step 1

## Acceptance Tests
- `pytest -q`
"""
    result = evaluate_contract(
        producing_state="Plan",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Done Signals" in result.missing


def test_plan_contract_skips_for_chore_label() -> None:
    body = """
## Plan
- bump version

## Acceptance Tests
- none — chore

## Done Signals
- `grep '^version' pyproject.toml` shows 0.6.7
"""
    result = evaluate_contract(
        producing_state="Plan",
        ticket_body=body,
        identifier="SMA-1",
    )
    # Chore short-circuits accept `none — chore` as a valid signal —
    # the validator accepts any non-empty body under each section, so
    # `- none — chore` passes without needing chore-specific logic.
    assert result.passed is True


# ---------------------------------------------------------------------------
# Review contract (Review → QA)
# ---------------------------------------------------------------------------


def test_review_contract_passes_with_review_section_only() -> None:
    body = """
## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| secrets | pass | n/a |
| input-validation | pass | n/a |
| sql-injection | pass | n/a |
| xss | pass | n/a |
| csrf | pass | n/a |
| authz | pass | n/a |
| rate-limit | pass | n/a |

## Review
chore — diff matches plan, no findings
"""
    result = evaluate_contract(
        producing_state="Review",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is True


def test_review_contract_fails_without_security_audit() -> None:
    body = """
## Review
all good
"""
    result = evaluate_contract(
        producing_state="Review",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Security Audit" in result.missing


def test_review_contract_passes_with_review_findings_present() -> None:
    body = """
## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| secrets | pass | n/a |
| input-validation | pass | n/a |
| sql-injection | pass | n/a |
| xss | pass | n/a |
| csrf | pass | n/a |
| authz | pass | n/a |
| rate-limit | pass | n/a |

## Review Findings
| severity | file:line | fix |
| --- | --- | --- |
| HIGH | src/foo.py:42 | add error handling |
"""
    # Review Findings → rewind to In Progress; validator should not flag
    # "## Review" missing in this case because findings ARE the outcome.
    result = evaluate_contract(
        producing_state="Review",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# QA contract (QA → Learn)
# ---------------------------------------------------------------------------


def test_qa_contract_passes_with_evidence_and_scorecard() -> None:
    body = """
## QA Evidence
- ran pytest -q, exit 0
- payloads under docs/SMA-1/qa/

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| version bumped | pyproject.toml | pass | docs/SMA-1/qa/version.log |
"""
    result = evaluate_contract(
        producing_state="QA",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is True


def test_qa_contract_fails_without_ac_scorecard() -> None:
    body = """
## QA Evidence
- ran tests, all pass
"""
    result = evaluate_contract(
        producing_state="QA",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## AC Scorecard" in result.missing


# ---------------------------------------------------------------------------
# Done contract (Learn → Done): both sections + artefact paths
# ---------------------------------------------------------------------------


def test_done_contract_passes_with_report_and_artefacts(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "evidence.log").write_text("ok")
    (docs_root / "SMA-1" / "work").mkdir(parents=True)
    (docs_root / "SMA-1" / "work" / "feature.md").write_text("# feature")

    body = """
## As-Is -> To-Be Report
### As-Is
- old behaviour
### To-Be
- new behaviour
### Reasoning
- chose option A
### Evidence
- docs/SMA-1/qa/

## Merge Status
merged to main via PR #99
"""
    result = evaluate_contract(
        producing_state="Done",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is True


def test_done_contract_fails_without_artefacts(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()

    body = """
## As-Is -> To-Be Report
### As-Is
- old
### To-Be
- new
### Reasoning
- because
### Evidence
- docs/SMA-1/qa/

## Merge Status
merged
"""
    result = evaluate_contract(
        producing_state="Done",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is False
    assert any("artefact" in m.lower() for m in result.missing)


def test_done_contract_fails_without_merge_status(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "e.log").write_text("ok")
    (docs_root / "SMA-1" / "work").mkdir(parents=True)
    (docs_root / "SMA-1" / "work" / "f.md").write_text("ok")

    body = """
## As-Is -> To-Be Report
### As-Is
- old
### To-Be
- new
### Reasoning
- because
### Evidence
- docs/SMA-1/qa/
"""
    result = evaluate_contract(
        producing_state="Done",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is False
    assert "## Merge Status" in result.missing


# ---------------------------------------------------------------------------
# Unknown state → no-op
# ---------------------------------------------------------------------------


def test_unknown_state_passes_through() -> None:
    result = evaluate_contract(
        producing_state="Explore",
        ticket_body="anything",
        identifier="SMA-1",
    )
    # Explore is not enforced by the v0.6.7 contract validator.
    assert result.passed is True


# ---------------------------------------------------------------------------
# Note formatting
# ---------------------------------------------------------------------------


def test_contract_failure_note_lists_missing_sections() -> None:
    body = "## Plan\n- step 1"
    result = evaluate_contract(
        producing_state="Plan",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Contract Failure" in result.note
    assert "## Acceptance Tests" in result.note
    assert "## Done Signals" in result.note
    # Note should mention the producing state for operator clarity
    assert "Plan" in result.note


def test_contract_result_is_immutable() -> None:
    result = ContractResult(passed=True)
    with pytest.raises(Exception):
        result.passed = False  # type: ignore[misc]


def test_contract_result_note_property_combines_heading_and_body() -> None:
    result = ContractResult(
        passed=False,
        missing=["## Done Signals"],
        note_heading="Contract Failure",
        note_body="Stage Plan missing.\n- ## Done Signals",
    )
    assert result.note.startswith("## Contract Failure\n")
    assert "## Done Signals" in result.note
