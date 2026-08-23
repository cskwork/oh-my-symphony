# Windows service-stop delivery proof

## Eval intent

- Goal: make plain Windows managed-service stop safe and reliable, then commit
  and push the complete refactor only if every required gate and adversarial
  review passes.
- Constraints: exact workflow and per-launch instance proof before automatic
  forced escalation; force only the proved serving PID; no new tree killer;
  no POSIX/CLI change; backward-compatible private record addition; no
  force-push.
- Rejected: Toolhelp fallback, blind force, automatic elevation, public
  shutdown endpoint.

## Before state

- Normal-host process-tree test: PASS (`1 passed in 4.12s`) outside sandbox.
- Normal-host explicit forced service stop: PASS.
- Normal-host plain service stop: FAIL; exit 1, PID remains, record retained.
- Sandboxed process/service termination: taskkill access denied; environment
  limitation, not a reason to broaden the kill primitive.

## After result

- Plain Windows stop escalates after timeout only when the exact recorded
  workflow API reports the exact random per-launch instance ID and a strict
  positive serving PID.
- Automatic force targets only that serving PID, never the reusable recorded
  launcher PID, and confirms both serving and launcher PIDs exit.
- Unverified, malformed, stale, legacy, or unreachable identity proofs perform
  no automatic force and retain their record.
- Explicit `--force` backend sweep and every POSIX path remain unchanged.

## Command manifest

| Check | Source | Status |
|---|---|---|
| New red/green service tests | evaluator-owned | PASS — 79 passed final |
| Service/process regression suites | evaluator-owned | PASS |
| Full pytest | `CONTRIBUTING.md` | PASS — 2311 passed, 80 declared skips |
| Ruff | `CONTRIBUTING.md` | PASS |
| Pyright | `CONTRIBUTING.md` | PASS — 0 errors |
| Scratch doctor | `AGENTS.md` | PASS |
| Native browser/orchestrator E2E | repository tests | PASS — 5; 77 + 18 skips |
| Live plain service stop | evaluator-owned | PASS — twice on real wrapper shape |
| Independent reviews | repository workflow | PASS — no Critical/Important findings |
| Commit/push | user-authorized, green-only | ready after final staged-diff gate |

## Commit gate

- Status: verification green; commit remains gated on final staged-diff review
  and fresh remote ancestry verification.

## Change-set cohesion

The phase-transition extraction is the primary requested refactor. Its required
live E2E exposed the Windows teardown defect, and the user conditioned publish
on a wholly green E2E/adversarial gate. The refactor, lifecycle correction, and
curated proof therefore form one atomic delivery: the repository should not
publish the refactor at an intermediate commit with a known failing required
teardown gate.

## Residual risk

- A narrow serving-PID reuse race remains between the proved health response
  and Windows termination. Removing it requires a process handle or creation-
  time identity and is intentionally outside this scoped change.
- Declared platform/capability skips are documented in the E2E report; they are
  not product failures.
