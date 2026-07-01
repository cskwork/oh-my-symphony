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
# Critic contract (In Progress → Critic → Review, with Critic → In Progress
# rewind). Either a clean `## Critic` OR the rewind pair
# `## Surfaced Requirements` + `## Critic Tests` — mirrors _REVIEW_OUTCOMES.
# ---------------------------------------------------------------------------


def test_critic_contract_passes_with_clean_critic_section() -> None:
    body = """
## Critic
no surfaced requirements — existing tests cover the spec
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is True
    assert result.missing == []


def test_critic_contract_passes_with_rewind_pair() -> None:
    body = """
## Surfaced Requirements
### 2026-06-26
- spec implies empty input returns []; no test covers it; test_edge_empty; open

## Critic Tests
- tests/test_critic_gap.py::test_edge_empty
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is True
    assert result.missing == []


def test_critic_contract_fails_when_critic_tests_missing_on_rewind() -> None:
    # Surfaced Requirements without the matching failing-test list is an
    # incomplete rewind — the fixer has nothing red to clear.
    body = """
## Surfaced Requirements
- spec implies idempotency; not covered; open
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Critic Tests" in result.missing
    assert "## Contract Failure" in result.note


def test_critic_contract_fails_when_no_outcome_present() -> None:
    # Neither a clean `## Critic` nor the rewind pair -> the Critic agent
    # produced nothing actionable; both rewind sections are reported.
    body = """
## Implementation
shipped the feature
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Surfaced Requirements" in result.missing
    assert "## Critic Tests" in result.missing


def test_critic_contract_empty_critic_falls_through_to_rewind_pair() -> None:
    # An empty `## Critic` body is not a valid clean pass; the validator
    # then requires the rewind pair and reports both as missing.
    body = """
## Critic

## Implementation
nothing here
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Surfaced Requirements" in result.missing
    assert "## Critic Tests" in result.missing


def test_critic_clean_section_not_confused_with_critic_tests() -> None:
    # `## Critic Tests` must not satisfy the clean `## Critic` outcome — the
    # heading regex anchors on end-of-line, so a rewind that lists tests but
    # omits Surfaced Requirements still fails.
    body = """
## Critic Tests
- tests/test_critic_gap.py::test_edge_empty
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert "## Surfaced Requirements" in result.missing
    assert "## Critic Tests" not in result.missing


def test_critic_rewind_without_ledger_file_rewinds(tmp_path: Path) -> None:
    # H5: a rewind turn (`## Surfaced Requirements` + `## Critic Tests`)
    # must persist the ledger on disk; an absent file is a hard rewind.
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    body = """
## Surfaced Requirements
### 2026-06-26
- spec implies empty input returns []; no test covers it; test_edge_empty; open

## Critic Tests
- tests/test_critic_gap.py::test_edge_empty
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is False
    assert any("surfaced-requirements" in m for m in result.missing)


def test_critic_rewind_with_ledger_file_passes(tmp_path: Path) -> None:
    # H5: same rewind pair, but the durable ledger exists on disk.
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "critic").mkdir(parents=True)
    (docs_root / "SMA-1" / "critic" / "surfaced-requirements.md").write_text(
        "### 2026-06-26\n- empty input gap; test_edge_empty; open\n"
    )
    body = """
## Surfaced Requirements
### 2026-06-26
- spec implies empty input returns []; no test covers it; test_edge_empty; open

## Critic Tests
- tests/test_critic_gap.py::test_edge_empty
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is True
    assert result.missing == []


def test_critic_clean_pass_unaffected_by_ledger_gate(tmp_path: Path) -> None:
    # H5: a clean `## Critic` took no rewind path -> the ledger gate is a
    # no-op even when docs_root is supplied with no ledger present.
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    body = """
## Critic
no surfaced requirements — existing tests cover the spec
"""
    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is True
    assert result.missing == []


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
# S2 content-checking gates: external-fact checks layered on presence.
# ---------------------------------------------------------------------------


_AUDIT_ALL_PASS = """## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| secrets | pass | n/a |
| input-validation | pass | n/a |
| sql-injection | pass | n/a |
| xss | pass | n/a |
| csrf | pass | n/a |
| authz | pass | n/a |
| rate-limit | pass | n/a |
"""

_AUDIT_WITH_FAIL = """## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| secrets | pass | n/a |
| input-validation | pass | n/a |
| sql-injection | fail | src/foo.py:42 |
| xss | pass | n/a |
| csrf | pass | n/a |
| authz | pass | n/a |
| rate-limit | pass | n/a |
"""


def test_review_fail_verdict_with_clean_review_rewinds() -> None:
    # A `fail` Security Audit row paired with a clean `## Review` (not
    # `## Review Findings`) is self-contradictory -> hard rewind.
    body = _AUDIT_WITH_FAIL + "\n## Review\nlooks good, shipping\n"
    result = evaluate_contract(
        producing_state="Review",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is False
    assert any("fail" in m.lower() and "review" in m.lower() for m in result.missing)


def test_review_fail_verdict_with_findings_passes() -> None:
    # Same `fail` row, but the reviewer correctly used `## Review Findings`.
    body = _AUDIT_WITH_FAIL + (
        "\n## Review Findings\n"
        "| severity | file:line | fix |\n"
        "| --- | --- | --- |\n"
        "| HIGH | src/foo.py:42 | sanitize input |\n"
    )
    result = evaluate_contract(
        producing_state="Review",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is True


def test_review_missing_evidence_file_rewinds(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    # Audit cites src/foo.py:42 but no such file exists under docs_root.
    body = _AUDIT_WITH_FAIL + (
        "\n## Review Findings\n"
        "| severity | file:line | fix |\n"
        "| --- | --- | --- |\n"
        "| HIGH | src/foo.py:42 | sanitize input |\n"
    )
    result = evaluate_contract(
        producing_state="Review",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is False
    assert any("src/foo.py" in m for m in result.missing)


def test_review_clean_pass_with_real_evidence_passes(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "src").mkdir(parents=True)
    (docs_root / "src" / "foo.py").write_text("# real file")
    # Findings cite a path:line that DOES exist -> no fabricated citation.
    body = _AUDIT_ALL_PASS + (
        "\n## Review Findings\n"
        "| severity | file:line | fix |\n"
        "| --- | --- | --- |\n"
        "| LOW | src/foo.py:1 | tidy import |\n"
    )
    result = evaluate_contract(
        producing_state="Review",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is True
    assert result.missing == []


def test_review_clean_pass_with_na_evidence_passes() -> None:
    # `n/a` evidence cells must never be treated as cited paths, even
    # with a docs_root absent — a clean pass stays clean.
    body = _AUDIT_ALL_PASS + "\n## Review\nchore — diff matches plan\n"
    result = evaluate_contract(
        producing_state="Review",
        ticket_body=body,
        identifier="SMA-1",
    )
    assert result.passed is True


def test_qa_missing_evidence_file_rewinds(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    # Scorecard cites an evidence log that does not exist under docs_root.
    body = """
## QA Evidence
- ran pytest -q, exit 0

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| version bumped | pyproject.toml | pass | SMA-1/qa/version.log |
"""
    result = evaluate_contract(
        producing_state="QA",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is False
    assert any("SMA-1/qa/version.log" in m for m in result.missing)


def test_qa_bug_repro_not_closed_rewinds(tmp_path: Path) -> None:
    # H2: a populated `reproduce/` dir with no `qa/repro-after.log` is an
    # unclosed bug loop -> hard rewind naming the missing log.
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "reproduce").mkdir(parents=True)
    (docs_root / "SMA-1" / "reproduce" / "repro.spec.ts").write_text("test")
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")
    body = """
## QA Evidence
- ran pytest -q, exit 0

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| version bumped | pyproject.toml | pass | SMA-1/qa/version.log |
"""
    result = evaluate_contract(
        producing_state="QA",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is False
    assert any("repro-after.log" in m for m in result.missing)


def test_qa_bug_repro_closed_passes(tmp_path: Path) -> None:
    # H2: reproduce dir populated AND repro-after.log saved -> loop closed.
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "reproduce").mkdir(parents=True)
    (docs_root / "SMA-1" / "reproduce" / "repro.spec.ts").write_text("test")
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "repro-after.log").write_text("0 failures")
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")
    body = """
## QA Evidence
- re-ran reproduction, now green

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| version bumped | pyproject.toml | pass | SMA-1/qa/version.log |
"""
    result = evaluate_contract(
        producing_state="QA",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is True
    assert result.missing == []


def test_qa_no_reproduce_dir_unaffected(tmp_path: Path) -> None:
    # H2: a non-bug ticket has no `reproduce/` dir -> the repro gate is a
    # no-op; QA still passes on evidence + a real scorecard path.
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")
    body = """
## QA Evidence
- ran pytest -q, exit 0

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| version bumped | pyproject.toml | pass | SMA-1/qa/version.log |
"""
    result = evaluate_contract(
        producing_state="QA",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is True
    assert result.missing == []


def test_qa_scorecard_fail_row_warns_without_rewind(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")
    (docs_root / "SMA-1" / "qa" / "ac2.log").write_text("ok")
    # One scorecard row is `fail`, but its evidence file is real. The fail
    # ships SOFT this release: a warning, not a rewind (passed stays True).
    body = """
## QA Evidence
- ran pytest -q

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| version bumped | pyproject.toml | pass | SMA-1/qa/version.log |
| handles empty input | test_edge | fail | SMA-1/qa/ac2.log |
"""
    result = evaluate_contract(
        producing_state="QA",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )
    assert result.passed is True
    assert result.missing == []
    assert result.warnings
    assert any("handles empty input" in w for w in result.warnings)
    assert "[contract-warn]" in result.warning_note


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
