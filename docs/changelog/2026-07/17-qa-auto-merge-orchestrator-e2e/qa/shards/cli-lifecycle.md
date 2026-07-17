# CLI lifecycle tester shard

## Scope

- Functional Symphony CLI, HTTP API, real Codex worker, Git worktree, and local bare-origin lifecycle.
- Disposable target only; original checkout and board observed read-only.
- Browser actions: `0`.
- Database: skipped because this path has no database dependency.

## Scenario observations

| ID | Tester status | Observation | Evidence |
| --- | --- | --- | --- |
| M1 | PASS | Corrected disposable fixture passed `symphony doctor`; original checkout stayed untouched. Three pre-product harness failures are separated from product behavior. | `qa/harness-and-preflight.md` |
| M2 | PASS | A real Codex worker completed the required In Progress, Verify, Learn, and Done contracts and created the tracked evidence. | `qa/failure-gate.md` |
| M3 | PASS | Origin rejection produced `push_failed`, Blocked the card, preserved the workspace immediately, left the remote stale, and created exactly one local merge. | `qa/failure-gate.md` |
| M4 | FAIL | Retry recovery itself was idempotent, but service restart removed the Blocked ticket's supposedly preserved workspace before operator recovery and lost its ignored lifecycle artifact. | `qa/restart-probe.md`, `qa/recovery.md` |
| M5 | PASS | Final refs matched exactly, the merge delta remained one, API was idle, cleanup completed, Git integrity passed, service stopped, and the original checkout remained unchanged. | `qa/recovery.md` |
| S1 | PASS | The configured-empty capture replay reached the staged-empty path, reported `SKIP: nothing staged after merge`, and verified the exact upstream SHA. | `qa/recovery.md` |
| N1 | NOT COVERED | A local bare remote cannot reproduce hosted authentication or branch-protection policy. | Boundary from `brief.md` |
| N2 | NOT COVERED | The isolated run did not exercise concurrent Done tickets. | Boundary from `brief.md` |

## Tester observations

- The rejected-push transaction and the subsequent no-duplicate-merge synchronization behaved as intended.
- The restart cleanup contradicts durable workspace preservation after a merge-gate failure. Tracked branch state was sufficient for this ticket's retry, but ignored or otherwise workspace-only diagnostics were destroyed.
- Harness-only failures: sandbox access to the nested Codex state directory, an unsupported fixture model, and an initially unexcluded board symlink. Each was corrected only inside the disposable target before the product path was assessed.
- `bash templates/qa-gate.sh <vault> cli`: PASS (`QA GATE PASS`).
- Independent auditor verdict remains pending; this shard records observations and scenario outcomes only.
