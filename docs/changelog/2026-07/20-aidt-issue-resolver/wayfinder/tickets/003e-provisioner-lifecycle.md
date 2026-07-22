# 003e - Provisioner lifecycle and cleanup

Route: RESEARCH / HISTORICAL UMBRELLA

Status: historical umbrella; aggregate verification passed

Blocked by: 003a, 003c, 003d

Unblocks: 003f

## Goal and interface contract

Own the ticket-worktree lifecycle seam exposed by `AidtWorktreeProvisioner`: admit and prepare one routed child,
re-attest before a run, and preserve or remove only through exact completion authority. Route, durable-record, and
Git complexity remain behind this interface.

## Historical file ownership

- `src/symphony/aidt_worktree/provisioner.py`
- `tests/aidt_provisioner_support.py`
- `tests/test_aidt_worktree_provisioner.py`

The executed three-file slice is several thousand lines and is therefore a historical umbrella, not a Build ticket.

## Acceptance and proof

- Create, exact resume, prepared recovery, ready re-attestation, failure persistence, collision/concurrency, and
  bounded rejection preserve the frozen route/base/branch/worktree identity.
- Cleanup requires exact authority and lease, proves clean registered ownership, removes without force/prune/branch
  deletion, and durably records the removal proof; every ambiguous case preserves evidence.
- Proof surfaces: `tests/test_aidt_worktree_provisioner.py`, shared temporary-Git fixtures, and `R-LOOP.md` R3 plus
  exclusive provisioner/recovery evidence.

## Scope boundary

Does not own process generations, generic workspace delegation, Core retry/terminal ordering, or operator docs.
