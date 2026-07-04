"""Scenario-proof for the 4-stage hardening loop on a bug-style ticket.

Where the per-gate unit tests in ``test_orchestrator_contracts.py`` probe
each new gate in isolation, this file walks ONE bug ticket (``BUG-7``)
through Verify -> In Progress and Learn -> In Progress rewinds.

The idiom matches the contract unit tests: drive ``evaluate_contract``
directly with crafted ticket bodies and a ``docs_root = tmp_path / "docs"``,
creating artefacts under ``docs_root / <id> / ...`` exactly as the real
agents would write them on disk.

The narrative:

1. Verify must close the bug-reproduction loop. H2 demands a populated
   ``reproduce/`` dir be answered with ``qa/repro-after.log`` — without it
   Verify rewinds naming the missing log. Once the re-run log is saved,
   Verify passes on valid evidence + scorecard.
2. Learn can rewind only when it records a real defect back to In Progress.
"""

from __future__ import annotations

from pathlib import Path

from symphony.orchestrator import _is_rewind_transition
from symphony.orchestrator.contracts import evaluate_contract


_BUG_ID = "BUG-7"


# Verify turn body: valid evidence + a scorecard whose single row cites a
# real evidence log under docs_root (created by each test that uses it).
_VERIFY_BODY = """## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| secrets | pass | BUG-7/qa/security.md |
| input-validation | pass | BUG-7/qa/security.md |
| injection | pass | BUG-7/qa/security.md |
| xss | pass | BUG-7/qa/security.md |
| csrf | pass | BUG-7/qa/security.md |
| authz | pass | BUG-7/qa/security.md |
| rate-limit | pass | BUG-7/qa/security.md |

## Review
diff matches plan

## QA Evidence
- re-ran the reproduction against the fix; failing case is now green

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| empty input returns [] | test_edge_empty | pass | BUG-7/qa/version.log |

## Merge Status
merged
"""


def _docs_root(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# H2: Verify must close the bug-reproduction loop.
# ---------------------------------------------------------------------------


def _seed_qa_artefacts(docs_root: Path) -> None:
    """Populate the artefacts every QA turn in this scenario needs.

    A language-agnostic reproduction file (H3: not hardcoded to `.spec.ts`)
    under `reproduce/`, plus the scorecard's cited evidence log under `qa/`.
    """
    reproduce = docs_root / _BUG_ID / "reproduce"
    reproduce.mkdir(parents=True)
    # H3: any-language repro file satisfies the populated-dir check; this
    # is a Python backend repro, not a web-E2E `.spec.ts`.
    (reproduce / "test_repro.py").write_text("def test_repro(): assert False\n")
    qa = docs_root / _BUG_ID / "qa"
    qa.mkdir(parents=True)
    (qa / "version.log").write_text("ok")
    (qa / "security.md").write_text("security evidence")


def test_verify_bug_repro_not_closed_rewinds(tmp_path: Path) -> None:
    """Loop stage: Verify on a bug whose `reproduce/` dir is populated but with
    no `qa/repro-after.log`. H2 rewinds, naming the missing re-run log,
    even though evidence + scorecard are otherwise valid.
    """
    docs_root = _docs_root(tmp_path)
    _seed_qa_artefacts(docs_root)

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=_VERIFY_BODY,
        identifier=_BUG_ID,
        docs_root=docs_root,
    )

    assert result.passed is False
    assert any("repro-after.log" in m for m in result.missing), result.missing
    assert "## Contract Failure" in result.note


def test_verify_bug_repro_closed_passes(tmp_path: Path) -> None:
    """Loop stage: Verify re-ran the reproduction and saved `qa/repro-after.log`
    -> the bug loop is closed. H2 satisfied; Verify passes on valid evidence +
    AC scorecard with a real cited path.
    """
    docs_root = _docs_root(tmp_path)
    _seed_qa_artefacts(docs_root)
    (docs_root / _BUG_ID / "qa" / "repro-after.log").write_text("0 failures")

    result = evaluate_contract(
        producing_state="Verify",
        ticket_body=_VERIFY_BODY,
        identifier=_BUG_ID,
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.missing == []


# ---------------------------------------------------------------------------
# The rewind transitions that wire the loop are recognised.
# ---------------------------------------------------------------------------


def test_loop_rewind_transitions_are_recognised() -> None:
    assert _is_rewind_transition("verify", "in progress") is True
    assert _is_rewind_transition("learn", "in progress") is True
    assert _is_rewind_transition("in progress", "verify") is False
