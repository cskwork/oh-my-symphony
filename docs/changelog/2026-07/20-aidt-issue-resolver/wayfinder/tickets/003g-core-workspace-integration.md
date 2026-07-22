# 003g - Core and workspace integration

Route: RESEARCH / HISTORICAL UMBRELLA

Status: historical umbrella; aggregate verification passed

Blocked by: 003a, 003f

Unblocks: 003h, 003v

## Goal and interface contract

Own the orchestrator integration seam: `WorkspaceManager` asks the runtime before generic lifecycle work, while Core
captures one manager/generation/admission/guard per attempt and re-attests before each backend boundary. The generic
workspace interface remains compatible for truly unmanaged tickets.

## Historical file ownership

- `src/symphony/workspace.py`
- `src/symphony/orchestrator/entries.py`
- `src/symphony/orchestrator/core.py`
- `tests/test_workspace.py`
- `tests/test_aidt_worktree_core_integration.py`

This executed five-file slice is well above 500 net lines and is therefore a historical umbrella, not a Build
ticket. Routing-runtime, health, dispatch, archive, and lifecycle tests are compatibility proof surfaces, not added
implementation ownership.

## Acceptance and proof

- Candidate order is filter, admission, capacity/eligibility/conflict, path, lease, entry, create, guard, backend.
- Initial/rebuilt/retry workers use the captured manager and exact guard; owned failures never reach generic hooks,
  commit/merge, tracker mutation, recursive removal, or a second manager.
- Terminal/startup/inactive paths run the non-destructive ownership guard before any generic mutation; health copies
  only the runtime snapshot.
- Proof surfaces: the two owned test files, the 41-case barrier, 326-case orchestrator matrix, and preserved
  compatibility matrices in `R-LOOP.md`.

## Scope boundary

Does not grant completion authority, activate live AIDT worktrees, or close the atomic publication correction.
