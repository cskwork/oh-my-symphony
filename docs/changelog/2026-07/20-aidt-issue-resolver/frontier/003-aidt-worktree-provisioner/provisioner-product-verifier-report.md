# Frontier 003 provisioner product verification

Date: 2026-07-21

## Verdict

FAIL.

Fresh verification reproduced two MUST-8 durable-transition defects and one MUST-7 lock-lineage defect that the
original 62-case suite did not cover. The original suite independently remained green, so these are product coverage
gaps rather than fixture regressions. No SHOULD or NIT finding was identified.

## MUST findings

### MUST-8 - ready sidecars are repaired before the ready Git proof

`_prepare` called `_reconcile_ready_sidecars` before `_resume_ready`. A genuine ready-manifest partial predecessor plus
an unrelated ref change therefore failed the later proof only after ownership and attempt had been advanced to ready.
Fresh output was `READY_REPAIR_BEFORE_FAILED_PROOF identity_invalid True True ready`.

Required correction: prove the ready Git state first, then repair only the exact ownership/attempt suffix under the
same locks. A failing proof must preserve both sidecar byte strings.

### MUST-8 - ownership reconciliation does not bind exact identity/scope

Ready/removing/removed reconciliation checked revision and tombstone shape without comparing every ownership identity
field to the manifest. A structurally valid CAS-persisted ownership record with a foreign service and route-pair digest
was accepted by ready resume. Fresh output was `OWNER_CONFLICT_ACCEPTED False True`.

Required correction: construct the exact ownership value expected for the manifest and accept/advance only a
byte-equal predecessor. Apply the same exact identity comparison to ready, removing, and removed reconciliation.

### MUST-7 - post-identity failure persistence omits the real common-Git lock

Provisioning resolved and used the real common-Git lock, but `_persist_failure` later acquired only
`advisory_lock(paths.manifest_lock)`. Fresh output recorded the lifecycle common+manifest pair followed by only a
manifest-lock entry during failure persistence.

Required correction: carry an exact per-call failure context after repository identity resolution and reacquire
`ordered_worktree_locks(real_common_git_lock, manifest_lock)` before the lineage reread/CAS. Pre-identity failures may
use the manifest lock alone. Never store mutable context on the shared provisioner instance.

## Executable evidence

The fresh verifier reran the unmodified accepted suite: `62 passed in 287.08s`. The three repository regressions in
`provisioner-verifier-gap-red-tests.md` then reproduced every finding: `3 failed in 14.57s`; the original cases remained
`62 passed, 3 deselected in 304.09s`. Test Ruff passed and test Pyright reported zero errors/warnings/information.

The verifier also inspected the full provisioner, lazy facade, fixture, accepted tests, recovery proofs, manifest/route
contracts, and all 12 MUST/3 SHOULD requirements. The reported three defects were the only remaining findings.

## Scope

Verification used disposable local SHA-1 repositories and observation-only/local mutation fixtures. No network,
external service, live AIDT checkout, runtime/schema edit, branch, commit, Jira action, or deployment occurred. The
automated reviewer was interrupted while writing documentation; this file records its exact messages and independently
landed executable reproductions.
