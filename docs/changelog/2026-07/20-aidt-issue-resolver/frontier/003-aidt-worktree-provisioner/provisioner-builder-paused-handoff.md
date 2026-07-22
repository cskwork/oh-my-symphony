# Frontier 003 provisioner builder paused handoff

Date: 2026-07-21

Status: paused at the upstream Git-state verification gate; no green claim

## Preserved edits

- Added red-first skeletons at `tests/test_aidt_worktree_provisioner.py` and
  `tests/test_aidt_worktree_runtime.py`. They contain every frozen test name, but most are still import/existence
  tracers and are not acceptance evidence.
- Added only the brief-authorized manifest helpers in `src/symphony/aidt_worktree/manifest.py`:
  `read_optional_manifest`, `read_optional_ownership`, `read_optional_attempt`, `initial_attempt_record`,
  `advance_attempt_phase`, and `ready_attempt_record`.
- Added an unverified draft at `src/symphony/aidt_worktree/provisioner.py`. It sketches the frozen DTOs, create/
  prepared/ready/removing transitions, injected authority, route/binding rechecks, and fault seams. It landed
  immediately before the pause instruction and is intentionally not exported from the facade.
- `src/symphony/aidt_worktree/runtime.py` was not created. No workspace/core/entry integration was touched.

## Red evidence

Command:

```text
PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_provisioner.py tests/test_aidt_worktree_runtime.py
```

Observed: collection failed with two expected import errors: missing `ActiveCompletionLease` and missing
`AidtWorktreeGeneration`. No product implementation existed at that boundary.

## Draft incompleteness

- The named tests must be replaced with executable temporary-Git, durable-attempt, recovery, authority, reload,
  ownership, and command/fallback-spy assertions before they can turn green.
- The provisioner draft has not been run through pytest, Ruff, Pyright, AST limits, or prose verification.
- Persisted-snapshot reconstruction, failure lock identity, branch-retained post-remove proof, attempt timestamp/
  phase transitions, and exact authorization revision equality require review against the repaired Git-state API.
- Lazy facade exports, runtime generation/admission/fatal-circuit behavior, counters, health, and compatibility proof
  remain unimplemented.

## Resume action

After the independent Git-state repair is verified, first reconcile the draft exclusively against the repaired
public API and PLAN amendments O/P. Then replace all skeletons with behavioral red tests, make the provisioner green,
implement `runtime.py`, and run the complete Frontier 003 route/worktree compatibility and static gates. Do not
touch workspace/core/entries until this slice passes an independent verifier.
