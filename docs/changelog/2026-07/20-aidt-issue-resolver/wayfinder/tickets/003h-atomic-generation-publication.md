# 003h - Atomic generation publication

Route: CORRECTION / BUILD

Status: closed - verified

Blocked by: 003f, 003g

Unblocks: 003v

## Goal and interface contract

Make runtime generation/provisioner and WorkspaceManager publication one atomic Core operation: a caller observes
either the exact prior tuple or the complete new tuple, never a split publication.

## Bounded file ownership

- `src/symphony/orchestrator/core.py`
- `tests/test_aidt_worktree_core_integration.py`

The completed correction is bounded to these two files and remains below 500 net lines. Runtime's existing internal
publication stayed unchanged; any further expansion requires a new ticket.

## Acceptance criteria

- A valid changed generation plus injected manager-stage failure preserves the exact prior runtime generation,
  provisioner, manager, and Core generation and denies admission for the unpublished tuple.
- The failure emits one bounded rejection and returns before heartbeat, reconcile, fetch, or dispatch.
- Publication uses a prepare/commit seam or equivalent atomic ordering; it does not roll back by republishing, bypass
  a failure, weaken fail-closed behavior, or expose mutable partial state.
- Startup, valid reload, disabled profile, and captured prior-attempt ownership retain existing behavior.

## Proof

- Recorded build proof is in `/private/tmp/f003-atomic-publication-result.md`; it does not close this ticket.
- Focused red/green manager-stage failure regression in `tests/test_aidt_worktree_core_integration.py`.
- Exact 41-case Core/workspace barrier, 459-case affected controls, 326-case orchestrator matrix, Ruff, Pyright, and
  whitespace checks. Aggregate/full proof remains 003v-owned.

Fresh aggregate verification passed the 3-case focused publication set, 42-case expanded barrier, affected and
orchestrator matrices, and full repository baseline without a new failure.

## Scope boundary

Does not alter Git timeouts, routing, records, provisioner semantics, operator examples, GOAL/run-state, or live state.
