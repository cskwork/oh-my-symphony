"""Scenario-proof for the supergoal-hardening loop on a bug-style ticket.

Where the per-gate unit tests in ``test_orchestrator_contracts.py`` probe
each new gate in isolation, this file walks ONE bug ticket (``BUG-7``)
through the full critic -> fix -> review -> QA discipline and asserts the
NEW H2/H5 gates fire at the right moment — and clear once the agent has
done the work the gate demands.

The idiom matches the contract unit tests: drive ``evaluate_contract``
directly with crafted ticket bodies and a ``docs_root = tmp_path / "docs"``,
creating artefacts under ``docs_root / <id> / ...`` exactly as the real
agents would write them on disk.

The narrative:

1. Critic surfaces a hidden requirement and rewinds to In Progress
   (``## Surfaced Requirements`` + ``## Critic Tests``). H5 demands the
   rewind also persist a durable ledger — without it the rewind is a
   skipped step and the contract rewinds again (step a). Once the fixer
   writes the ledger the Critic outcome clears (step b).
2. QA must close the bug-reproduction loop. H2 demands a populated
   ``reproduce/`` dir be answered with ``qa/repro-after.log`` — without it
   QA rewinds naming the missing log (step c). Once the re-run log is
   saved (repro closed), QA passes on valid evidence + scorecard (step d).
3. The backward transitions that wire this loop — Critic -> In Progress
   and QA -> In Progress — are recognised rewinds (step e).
"""

from __future__ import annotations

from pathlib import Path

from symphony.issue import normalize_state
from symphony.orchestrator import _is_rewind_transition
from symphony.orchestrator.contracts import evaluate_contract


_BUG_ID = "BUG-7"


# Critic rewind turn: the agent surfaced a spec gap and authored the
# matching failing test. Both rewind sections are present in the body.
_CRITIC_REWIND_BODY = """## Surfaced Requirements
### 2026-06-26
- spec implies empty input returns []; no test covers it; test_edge_empty; open

## Critic Tests
- tests/test_bug7_gap.py::test_empty_input_returns_empty_list
"""


# QA turn body: valid evidence + a scorecard whose single row cites a
# real evidence log under docs_root (created by each test that uses it).
_QA_BODY = """## QA Evidence
- re-ran the reproduction against the fix; failing case is now green

## AC Scorecard
| signal | source | result | evidence |
| --- | --- | --- | --- |
| empty input returns [] | test_edge_empty | pass | BUG-7/qa/version.log |
"""


def _docs_root(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# (a) + (b) — H5: Critic rewind must persist the durable ledger.
# ---------------------------------------------------------------------------


def test_critic_rewind_without_ledger_rewinds(tmp_path: Path) -> None:
    """Loop stage: Critic finds a gap -> rewind, but the fixer has not yet
    written the ledger. H5 keeps the ticket red and names the missing file.
    """
    docs_root = _docs_root(tmp_path)

    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=_CRITIC_REWIND_BODY,
        identifier=_BUG_ID,
        docs_root=docs_root,
    )

    assert result.passed is False
    assert any("surfaced-requirements" in m for m in result.missing), result.missing
    # The named path is the durable ledger the fixer must author.
    expected = docs_root / _BUG_ID / "critic" / "surfaced-requirements.md"
    assert any(str(expected) in m for m in result.missing), result.missing
    assert "## Contract Failure" in result.note


def test_critic_rewind_with_ledger_passes(tmp_path: Path) -> None:
    """Loop stage: the fixer wrote the durable ledger -> the Critic rewind
    contract clears. H5 satisfied.
    """
    docs_root = _docs_root(tmp_path)
    ledger = docs_root / _BUG_ID / "critic" / "surfaced-requirements.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "### 2026-06-26\n- empty input gap; test_edge_empty; open\n"
    )

    result = evaluate_contract(
        producing_state="Critic",
        ticket_body=_CRITIC_REWIND_BODY,
        identifier=_BUG_ID,
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.missing == []


# ---------------------------------------------------------------------------
# (c) + (d) — H2: QA must close the bug-reproduction loop.
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


def test_qa_bug_repro_not_closed_rewinds(tmp_path: Path) -> None:
    """Loop stage: QA on a bug whose `reproduce/` dir is populated but with
    no `qa/repro-after.log`. H2 rewinds, naming the missing re-run log,
    even though evidence + scorecard are otherwise valid.
    """
    docs_root = _docs_root(tmp_path)
    _seed_qa_artefacts(docs_root)

    result = evaluate_contract(
        producing_state="QA",
        ticket_body=_QA_BODY,
        identifier=_BUG_ID,
        docs_root=docs_root,
    )

    assert result.passed is False
    assert any("repro-after.log" in m for m in result.missing), result.missing
    assert "## Contract Failure" in result.note


def test_qa_bug_repro_closed_passes(tmp_path: Path) -> None:
    """Loop stage: QA re-ran the reproduction and saved `qa/repro-after.log`
    -> the bug loop is closed. H2 satisfied; QA passes on valid evidence +
    AC scorecard with a real cited path.
    """
    docs_root = _docs_root(tmp_path)
    _seed_qa_artefacts(docs_root)
    (docs_root / _BUG_ID / "qa" / "repro-after.log").write_text("0 failures")

    result = evaluate_contract(
        producing_state="QA",
        ticket_body=_QA_BODY,
        identifier=_BUG_ID,
        docs_root=docs_root,
    )

    assert result.passed is True
    assert result.missing == []


# ---------------------------------------------------------------------------
# (e) — the rewind transitions that wire the loop are recognised.
# ---------------------------------------------------------------------------


def test_loop_rewind_transitions_are_recognised() -> None:
    """The Critic and QA rewinds that drive this loop are recognised
    backward transitions.

    `_is_rewind_transition` compares against normalised (lower-cased)
    states — the orchestrator runs every state through `normalize_state`
    before the check (see `_REWIND_TRANSITIONS` in constants.py). Assert
    via the same normalisation the production call site uses.
    """
    assert (
        _is_rewind_transition(
            normalize_state("Critic"), normalize_state("In Progress")
        )
        is True
    )
    assert (
        _is_rewind_transition(
            normalize_state("QA"), normalize_state("In Progress")
        )
        is True
    )
    # A forward step is not a rewind — guards against an over-broad match.
    assert (
        _is_rewind_transition(
            normalize_state("In Progress"), normalize_state("Critic")
        )
        is False
    )
