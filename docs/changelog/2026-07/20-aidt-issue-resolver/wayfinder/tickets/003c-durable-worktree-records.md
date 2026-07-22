# 003c - Durable worktree records

Route: RESEARCH / HISTORICAL UMBRELLA

Status: historical umbrella; aggregate verification passed

Blocked by: 003b

Unblocks: 003d, 003e

## Goal and interface contract

Own the durable-record seam: canonical manifest, activation, ownership, and attempt records with exact schemas,
compare-and-swap transitions, stable registry discovery, advisory locking, and persisted admission/backoff. Callers
use typed read/persist/admit operations; filesystem encoding and crash-safe replacement remain local.

## Historical file ownership

- `src/symphony/aidt_worktree/manifest.py`
- `tests/test_aidt_worktree_manifest.py`

The executed single-module slice is far above 500 net lines and is therefore a historical umbrella, not a Build
ticket.

## Acceptance and proof

- Unknown keys, symlinks, collisions, malformed JSON/types, path escape, invalid transitions, stale CAS, and
  unsupported locking fail closed without leaking unbounded content.
- State and sidecar revisions preserve absent/prepared/ready/removing/removed recovery intent and exact ownership.
- Proof surfaces: `tests/test_aidt_worktree_manifest.py` and `R-LOOP.md` R1/R2 persistence evidence.

## Scope boundary

Does not execute Git, decide repository state, create/remove worktrees, publish process generations, or mutate Core.
