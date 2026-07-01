# Supergoal-hardening loop — scenario proof

This is the end-to-end proof that the hardened bug loop works: each stage's
discipline is enforced by a concrete gate, and a focused test drives that
gate on a real bug ticket (`BUG-7`). Plain language; one line per stage.

The loop being proven, in order:

Critic finds gap -> rewind to In Progress -> fixer clears reds + writes
ledger -> Critic clean -> Review -> QA repro closure -> Learn.

## Stage-by-stage: which gate enforces it, which test proves it

| # | Loop stage | Gate that enforces it | Test that proves it |
|---|------------|-----------------------|---------------------|
| 1 | Critic finds a gap and rewinds to In Progress | `_is_rewind_transition` recognises `Critic -> In Progress` as a backward step, so the next worker run gets the rewind cue | `test_loop_rewind_transitions_are_recognised` |
| 2 | Rewind must leave a durable trail — appending the markdown sections without writing the ledger file is a skipped step | H5: the Critic contract names the missing `critic/surfaced-requirements.md` and keeps the ticket red | `test_critic_rewind_without_ledger_rewinds` |
| 3 | Fixer clears the reds and writes the durable ledger -> Critic outcome is clean | H5: with the ledger on disk the Critic rewind contract passes | `test_critic_rewind_with_ledger_passes` |
| 4 | Review hands a closed-out diff to QA | Existing Review contract (Security Audit table + clean Review / Review Findings) — unchanged by this work, already covered in `test_orchestrator_contracts.py` | (regression-covered; not re-proven here) |
| 5 | QA must close the bug-reproduction loop; a populated `reproduce/` dir with no re-run log is an open bug | H2: the QA contract names the missing `qa/repro-after.log` and rewinds (QA -> In Progress, also a recognised rewind per row 1) | `test_qa_bug_repro_not_closed_rewinds` |
| 6 | QA re-runs the reproduction, saves `repro-after.log` (repro closed) -> QA passes on valid evidence + AC scorecard | H2: with the re-run log present the QA contract passes | `test_qa_bug_repro_closed_passes` |
| 7 | Learn / forward exit | Not a new gate — once QA passes the ticket advances; pass-through state | (no new gate) |

Notes on the proof artefacts (mirroring how the real agents write to disk):

- The reproduction file seeded under `reproduce/` is a Python repro
  (`test_repro.py`), not a TypeScript `.spec.ts`. This also exercises H3:
  the repro gate is language-agnostic — it checks the dir is populated, not
  that it holds a web-E2E spec.
- All artefacts live under `docs_root = tmp_path / "docs"`, with files at
  `docs_root/BUG-7/...`, the same convention as `test_orchestrator_contracts.py`.

## Evidence

New test file: `tests/test_supergoal_hardening_loop.py` (5 tests).

Final full-suite result:

```
862 passed, 1 skipped in 122.85s (0:02:02)
```

(Pre-Harden baseline was 851 passed / 1 skipped; H1-H5 unit tests took it to
857; the 5 scenario-proof tests in this file take it to 862 — count only
went up.)
