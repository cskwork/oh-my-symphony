# Scenario ledger - terminal auto-merge rejection and recovery

## Impact Matrix

| ID | Priority | Surface | Scenario | Expected | Status | Evidence or reason |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | Must | Harness | Patch fixture, bare origin, board, workflow, free ports, and `doctor` are valid | Preflight passes without touching the original checkout | PASS | `qa/harness-and-preflight.md` |
| M2 | Must | Worker lifecycle | Real Codex advances a contract-valid ticket through In Progress, Verify, Learn, and Done | Worker exits normally and all required evidence exists | PASS | `qa/failure-gate.md` |
| M3 | Must | Failure gate | Bare origin rejects the target push at Done | Ticket becomes Blocked with `push_failed`; workspace remains; remote is stale; one local merge exists | PASS | `qa/failure-gate.md` |
| M4 | Must | Restart and retry | Stop service, observe Blocked-workspace startup behavior, remove rejection, restart, move Blocked -> Learn, and let a real worker return to Done | Existing merge is pushed and verified; no duplicate merge; completed workspace cleaned; restart-durability is recorded exactly | FAIL | Restart removed the promised Blocked workspace and lost its ignored lifecycle artifact; recovery itself passed. See `qa/restart-probe.md` and `qa/recovery.md`. |
| M5 | Must | Final consistency | Inspect API, refs, worktrees, logs, and original checkout | No active/retrying worker or unintended mutation remains | PASS | `qa/recovery.md` |
| S1 | Should | Capture branch | Configure an empty capture directory on the already-merged retry | Retry reaches staged-empty synchronization and still recovers idempotently | PASS | `qa/recovery.md` |
| N1 | Not covered | Hosted Git | Hosted authentication and branch-protection behavior | Explicit residual risk only | NOT COVERED | Reason: local bare remote cannot reproduce provider policy |
| N2 | Not covered | Concurrency | Multiple tickets reach Done concurrently | Explicit residual risk only | NOT COVERED | Reason: this run isolates one failure transaction |

## Shards

- M1/M2/M3/M5/S1 PASS qa/shards/cli-lifecycle.md - tester evidence is complete.
- M4 FAIL qa/restart-probe.md - the independent auditor confirmed restart workspace loss.
- Independent verdict: FAIL; see `report.md`.

## Budget

- Browser actions: 0/100 used. CLI commands and API reads do not consume browser actions.
