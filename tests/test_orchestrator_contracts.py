"""Contract validator coverage for the 4-stage pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.orchestrator.contracts import ContractResult, evaluate_contract


def _complete_in_progress_body() -> str:
    return """
## Plan
- step 1

## Acceptance Tests
- `pytest tests/test_foo.py::test_bar`

## Done Signals
- feature is observable in the UI

## Implementation
- changed src/foo.py

## Self-Critique
- checked empty and error paths
"""


def _complete_verify_body() -> str:
    return """
## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| secrets | pass | n/a |
| input-validation | pass | n/a |
| injection | pass | n/a |
| xss | pass | n/a |
| csrf | pass | n/a |
| authz | pass | n/a |
| rate-limit | pass | n/a |

## Review
diff matches plan

## QA Evidence
- ran pytest -q, exit 0

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| version bumped | pytest | pass | qa/version.log |

## Merge Status
merged to main with --no-ff
"""


def test_in_progress_contract_passes_with_sections_and_work_file(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "work").mkdir(parents=True)
    (docs_root / "SMA-1" / "work" / "notes.md").write_text("ok")

    result = evaluate_contract(
        producing_state="In Progress",
        ticket_body=_complete_in_progress_body(),
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.missing == []


def test_in_progress_contract_fails_when_required_sections_missing() -> None:
    body = """
## Plan
- step 1

## Acceptance Tests
- `pytest -q`
"""

    result = evaluate_contract(
        producing_state="In Progress",
        ticket_body=body,
        identifier="SMA-1",
    )

    assert result.passed is False
    assert "## Done Signals" in result.missing
    assert "## Implementation" in result.missing
    assert "## Self-Critique" in result.missing
    assert "## Contract Failure" in result.note


def test_in_progress_contract_requires_work_artifact_when_docs_root_is_present(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()

    result = evaluate_contract(
        producing_state="In Progress",
        ticket_body=_complete_in_progress_body(),
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert any("work" in item for item in result.missing)


def test_verify_contract_passes_with_review_qa_scorecard_and_merge(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=_complete_verify_body(),
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.missing == []


def test_verify_contract_normalizes_docs_prefixed_evidence_path(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | docs/SMA-1/qa/version.log |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.missing == []


def test_verify_contract_fails_without_security_audit() -> None:
    body = """
## Review
all good

## QA Evidence
- ran tests

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| happy path | pytest | pass | n/a |

## Merge Status
merged
"""

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
    )

    assert result.passed is False
    assert "## Security Audit" in result.missing


def test_verify_contract_accepts_review_findings_as_verify_outcome() -> None:
    body = """
## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| secrets | pass | n/a |

## Review Findings
| severity | file:line | fix |
| --- | --- | --- |
| HIGH | src/foo.py:42 | add error handling |

## QA Evidence
- not run because review failed

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| happy path | pytest | pass | n/a |

## Merge Status
not merged because review failed
"""

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
    )

    assert result.passed is True


def test_verify_fail_verdict_with_clean_review_rewinds() -> None:
    body = """
## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| injection | fail | src/foo.py:42 |

## Review
looks good

## QA Evidence
- ran tests

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| happy path | pytest | pass | n/a |

## Merge Status
merged
"""

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
    )

    assert result.passed is False
    assert any("fail" in item.lower() and "review" in item.lower() for item in result.missing)


def test_verify_missing_evidence_file_rewinds(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=_complete_verify_body(),
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert any("qa/version.log" in item for item in result.missing)


def test_verify_rejects_source_anchor_prose_as_evidence_cell(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | No secrets in examples/foo.js:1 |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert any("source anchors/prose" in item for item in result.missing)


def test_verify_bug_repro_not_closed_rewinds(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "reproduce").mkdir(parents=True)
    (docs_root / "SMA-1" / "reproduce" / "repro.spec.ts").write_text("test")
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=_complete_verify_body(),
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert any("repro-after.log" in item for item in result.missing)


def test_verify_bug_repro_closed_passes(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "reproduce").mkdir(parents=True)
    (docs_root / "SMA-1" / "reproduce" / "repro.spec.ts").write_text("test")
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")
    (docs_root / "SMA-1" / "qa" / "repro-after.log").write_text("0 failures")

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=_complete_verify_body(),
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is True


def test_verify_scorecard_fail_row_warns_without_rewind(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "version.log").write_text("ok")
    (docs_root / "SMA-1" / "qa" / "ac2.log").write_text("ok")
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | qa/version.log |\n"
        "| handles empty input | pytest | fail | qa/ac2.log |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.warnings
    assert "[contract-warn]" in result.warning_note


def test_learn_contract_requires_human_review_and_wiki_updates() -> None:
    result = evaluate_contract(
        producing_state="Learn",
        ticket_body="## Wiki Updates\n- docs/llm-wiki/foo.md\n",
        identifier="SMA-1",
    )

    assert result.passed is False
    assert "## Human Review" in result.missing


def test_learn_contract_passes_with_required_sections() -> None:
    result = evaluate_contract(
        producing_state="Learn",
        ticket_body="""
## Wiki Updates
- docs/llm-wiki/foo.md

## Human Review
### What Changed
- shipped the fix
""",
        identifier="SMA-1",
    )

    assert result.passed is True


def test_done_contract_passes_with_report_merge_and_artifacts(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "evidence.log").write_text("ok")
    (docs_root / "SMA-1" / "work").mkdir(parents=True)
    (docs_root / "SMA-1" / "work" / "feature.md").write_text("ok")

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

    assert result.passed is True


def test_done_contract_fails_without_merge_status(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "qa").mkdir(parents=True)
    (docs_root / "SMA-1" / "qa" / "evidence.log").write_text("ok")
    (docs_root / "SMA-1" / "work").mkdir(parents=True)
    (docs_root / "SMA-1" / "work" / "feature.md").write_text("ok")

    result = evaluate_contract(
        producing_state="Done",
        ticket_body="## As-Is -> To-Be Report\n### As-Is\n- old\n",
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert "## Merge Status" in result.missing


def test_unknown_state_passes_through() -> None:
    result = evaluate_contract(
        producing_state="Explore",
        ticket_body="anything",
        identifier="SMA-1",
    )

    assert result.passed is True


def test_contract_failure_note_lists_missing_sections() -> None:
    result = evaluate_contract(
        producing_state="In Progress",
        ticket_body="## Plan\n- step 1",
        identifier="SMA-1",
    )

    assert result.passed is False
    assert "## Contract Failure" in result.note
    assert "## Acceptance Tests" in result.note
    assert "In Progress" in result.note


def test_contract_result_is_immutable() -> None:
    result = ContractResult(passed=True)
    with pytest.raises(Exception):
        result.passed = False  # type: ignore[misc]


def test_contract_result_note_property_combines_heading_and_body() -> None:
    result = ContractResult(
        passed=False,
        missing=["## Done Signals"],
        note_heading="Contract Failure",
        note_body="Stage In Progress missing.\n- ## Done Signals",
    )
    assert result.note.startswith("## Contract Failure\n")
    assert "## Done Signals" in result.note
