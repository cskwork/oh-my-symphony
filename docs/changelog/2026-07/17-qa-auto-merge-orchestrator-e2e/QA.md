# QA - terminal auto-merge rejection and recovery

## Before

- The verified patch worktree is the source under test; the original checkout and its real board are observation-only.
- Stage contracts were read from the code knowledge graph before constructing the fixture.
- `symphony doctor` must pass against the disposable workflow before the service starts.

## QA

- Integration smoke: completed by `qa-tester` against the disposable Symphony/Codex/Git lifecycle.
- Scenario outcomes: M1 PASS, M2 PASS, M3 PASS, M4 FAIL, M5 PASS, S1 PASS; N1 and N2 not covered by design.
- Product finding: restart removed the Blocked ticket's failed workspace before operator recovery, losing the ignored lifecycle artifact despite the rejection path preserving the workspace immediately.
- Recovery evidence: the existing local merge was pushed and verified at `571950551e1dccb56f702e1221ab63383f8c7a38`; merge delta remained exactly one; the ticket reached Done and the completed workspace was cleaned.
- Harness-only failures: sandboxed nested-Codex state initialization, unsupported fixture model, and initially unexcluded board symlink; corrected only in the disposable target and not counted as product defects.
- Evidence shard: `qa/shards/cli-lifecycle.md` with supporting files under `qa/`.
- Tool: CLI and HTTP state API; no browser surface.
- Database: skipped; no database participates in this path.

## Results

Verdict: FAIL

- Patch-specific result: PASS. The rejected-push retry now re-pushes and verifies the existing target commit on both the no-capture preflight no-op and configured-empty-capture staged no-op paths; no duplicate merge is created.
- Lifecycle result: FAIL. M4 is a Must scenario, and restart removes the merge-gate-blocked workspace before recovery, losing workspace-only evidence even though tracked branch state later allows a successful retry.
- Independent checks: `tests/test_auto_merge.py` 13 passed; targeted merge-gate/startup-cleanup tests 3 passed; Ruff passed; explicit-venv Pyright passed with 0 diagnostics; `symphony doctor ./WORKFLOW.md` passed; `git diff --check` passed.
- Final evidence gate: `QA-ONLY GATE PASS` with 0/100 browser actions and no database use.
- Root cause: the failure gate moves Done to terminal `Blocked`, while startup cleanup preserves/reconciles only literal `Done` workspaces and removes other terminal workspaces.
- Coverage: every changed helper is exercised through its owning module and the real rejection/recovery evidence. Hosted Git policy, concurrent Done tickets, and rare pre-existing script error arms remain uncovered; S1 is library-level rather than a second worker E2E.
- Harness-only setup failures remain separate from the product verdict: nested-Codex sandbox state, unsupported fixture model, initial board-symlink exclusion, and an initial Pyright interpreter-selection miss.
- Gate status: the shared CLI `qa-gate.sh` passed. The QA-ONLY wrapper gate rejected the frozen populated scenario ledger at its outcome-regex check; the auditor did not edit the conductor-owned ledger.
- Independent report: `report.md`.

## Residual Risk

- A local bare remote reproduces Git rejection and exact ref synchronization, but not hosted-provider authentication or branch-protection messages.
