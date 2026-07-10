"""Contract validator coverage for the 4-stage pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.orchestrator.parsing import _parse_findings_rows
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
| secrets | pass | qa/security.md |
| input-validation | pass | qa/security.md |
| injection | pass | qa/security.md |
| xss | pass | qa/security.md |
| csrf | pass | qa/security.md |
| authz | pass | qa/security.md |
| rate-limit | pass | qa/security.md |

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


def _write_verify_artifacts(docs_root: Path, identifier: str = "SMA-1") -> None:
    qa = docs_root / identifier / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "version.log").write_text("ok")
    (qa / "security.md").write_text("security audit evidence")


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
    _write_verify_artifacts(docs_root)

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
    _write_verify_artifacts(docs_root)
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
    _write_verify_artifacts(docs_root)
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


def test_contract_failure_reports_expected_evidence_path_shape(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    _write_verify_artifacts(docs_root)
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | validated in source |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert result.failures
    failure = result.failures[0]
    assert failure.section == "## AC Scorecard"
    assert failure.row == 1
    assert failure.found == "validated in source"
    assert "docs/SMA-1/qa/evidence.md" in failure.expected
    assert "work/verify.log" in failure.expected
    assert "## AC Scorecard row 1" in result.note
    assert "validated in source" in result.note
    assert "docs/SMA-1/qa/evidence.md" in result.note


def test_contract_failure_rejects_placeholder_evidence_cells(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    _write_verify_artifacts(docs_root)
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | - |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert result.failures
    assert result.failures[0].found == "-"
    assert "docs/SMA-1/qa/evidence.md" in result.note


def test_contract_failure_note_round_trips_backticked_evidence_scope(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    _write_verify_artifacts(docs_root)
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | `validated in source` |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    rows = _parse_findings_rows(result.note)
    assert rows == [
        {
            "severity": "CONTRACT",
            "file": "",
            "line": 1,
            "fix": (
                "## AC Scorecard row 1: found ``validated in source``; "
                "expected evidence must cite a durable artifact such as "
                "`docs/SMA-1/qa/evidence.md`, `qa/evidence.md`, "
                "`docs/SMA-1/work/verify.log`, or `work/verify.log`; "
                "put source anchors/prose inside that artifact"
            ),
            "section": "## AC Scorecard",
            "found": "`validated in source`",
            "expected": (
                "evidence must cite a durable artifact such as "
                "`docs/SMA-1/qa/evidence.md`, `qa/evidence.md`, "
                "`docs/SMA-1/work/verify.log`, or `work/verify.log`; "
                "put source anchors/prose inside that artifact"
            ),
        }
    ]


def test_verify_accepts_backticked_artifact_with_trailing_qualifier(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    _write_verify_artifacts(docs_root)
    (docs_root / "SMA-1" / "qa" / "manual-acceptance.log").write_text("ok")
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | "
        "`qa/manual-acceptance.log` (README grep block) |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.missing == []


def test_verify_accepts_multiple_cited_artifacts_when_all_exist(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    _write_verify_artifacts(docs_root)
    qa = docs_root / "SMA-1" / "qa"
    (qa / "manual-run.log").write_text("ok")
    (qa / "pytest.log").write_text("ok")
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | "
        "`qa/manual-run.log`, `qa/pytest.log` "
        "(`test_add_then_list_shows_item_with_index`) |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.missing == []


def test_verify_rejects_multi_citation_when_any_artifact_missing(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    _write_verify_artifacts(docs_root)
    (docs_root / "SMA-1" / "qa" / "manual-run.log").write_text("ok")
    # qa/pytest.log intentionally NOT created.
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | pass | "
        "`qa/manual-run.log`, `qa/pytest.log` "
        "(`test_add_then_list_shows_item_with_index`) |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert any("qa/pytest.log" in item for item in result.missing)


def test_verify_skips_security_audit_evidence_for_na_result_rows(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    _write_verify_artifacts(docs_root)
    body = _complete_verify_body()
    body = body.replace(
        "| rate-limit | pass | qa/security.md |",
        "| rate-limit | n/a | No network/API resource exposed. |",
    )
    body = body.replace(
        "| authz | pass | qa/security.md |",
        "| authz | pass | validated via manual review |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert not any(
        "No network/API resource exposed." in item for item in result.missing
    )
    assert any("validated via manual review" in item for item in result.missing)


def test_verify_ac_scorecard_na_result_still_requires_evidence(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    _write_verify_artifacts(docs_root)
    body = _complete_verify_body().replace(
        "| version bumped | pytest | pass | qa/version.log |",
        "| version bumped | pytest | n/a | - |",
    )

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=body,
        identifier="SMA-1",
        docs_root=docs_root,
    )

    assert result.passed is False
    assert result.failures
    assert result.failures[0].section == "## AC Scorecard"
    assert result.failures[0].found == "-"


def test_verify_bug_repro_not_closed_rewinds(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SMA-1" / "reproduce").mkdir(parents=True)
    (docs_root / "SMA-1" / "reproduce" / "repro.spec.ts").write_text("test")
    _write_verify_artifacts(docs_root)

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
    _write_verify_artifacts(docs_root)
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
    _write_verify_artifacts(docs_root)
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


def test_learn_contract_requires_completion_record_or_human_review() -> None:
    result = evaluate_contract(
        producing_state="Learn",
        ticket_body="## Wiki Updates\n- docs/llm-wiki/foo.md\n",
        identifier="SMA-1",
    )

    assert result.passed is False
    assert "one of `## As-Is -> To-Be Report` or `## Human Review`" in result.missing


def test_learn_contract_passes_with_completion_record() -> None:
    result = evaluate_contract(
        producing_state="Learn",
        ticket_body="""
## Wiki Updates
- docs/llm-wiki/foo.md

## As-Is -> To-Be Report
### Goal
- ship the fix
""",
        identifier="SMA-1",
    )

    assert result.passed is True


def test_learn_contract_passes_with_intervention_handoff() -> None:
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
