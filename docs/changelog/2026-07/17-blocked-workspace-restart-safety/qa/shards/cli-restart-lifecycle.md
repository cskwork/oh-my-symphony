# CLI restart lifecycle tester shard

## Scope

- Fresh disposable Symphony CLI, HTTP state API, real Codex worker, Git worktree, and local bare-origin lifecycle.
- Fixture: `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh`; port: `19117`.
- Action count: n/a; no browser or database participates.

## Scenario observations

| ID | Tester status | Observation | Evidence |
| --- | --- | --- | --- |
| C1 | PASS | Corrected fixture passed doctor; the real worker traversed In Progress -> Verify -> Learn -> Done and created branch-local work plus tracked and ignored QA artefacts. | `qa/cli-preflight-and-worker.md` |
| C2 | PASS | The rejected target push returned `push_failed`, moved the card to Blocked, left the remote stale, created exactly one local merge, and preserved the workspace immediately. | `qa/rejected-push-and-restart.md` |
| C3 | PASS | A clean stop/restart while still Blocked kept the same worktree and ignored diagnostic; startup logged the preservation event and did not auto-commit or remove the ticket workspace. | `qa/rejected-push-and-restart.md` |
| C4 | PASS | Recovery from Learn pushed and verified the existing merge through `nothing_to_apply`; local/remote refs matched, merge delta stayed one, feature branch remained, and Done removed the workspace. | `qa/recovery-and-isolation.md` |
| C5 | PASS | API was idle, Git integrity was clean, shutdown completed, port stopped, and both product checkouts/boards remained unchanged outside this assigned vault evidence. | `qa/recovery-and-isolation.md` |

## Coverage boundary

- Not covered: hosted Git authentication and branch-protection behavior; the local bare remote proves
  rejection and exact ref readback but not provider-specific policy messages.
- Not covered: concurrent terminal-ticket startup cleanup; this fixture isolates one card.
- Harness-only: the first fixture tracked its ignored board card and failed the symlink assertion;
  the second sandboxed service start could not keep nested Codex alive. Both stopped before a real
  worker turn and are separated in `qa/cli-preflight-and-worker.md`.

This shard records evidence and per-scenario observations only. The independent auditor owns the final Verdict.
