# 003b - Worktree profile and identity contract

Route: RESEARCH / HISTORICAL UMBRELLA

Status: historical umbrella; aggregate verification passed

Blocked by: 003a

Unblocks: 003c, 003d, 003i

## Goal and interface contract

Own the configuration-and-identity seam exposed by `load_aidt_worktree_settings`: exact default-off profile
validation, canonical branch/path derivation, stable metadata paths, bounded result DTOs, and completion authority.
Callers receive canonical values or one bounded failure; they do not learn validation internals.

## Historical file ownership

- `src/symphony/aidt_worktree/contract.py`
- `tests/test_aidt_worktree_contract.py`

The executed source-and-test slice exceeds 500 net lines and is therefore a historical umbrella, not a Build ticket.

## Acceptance and proof

- Missing/false configuration is inert; enabled mode rejects unsafe hooks, merge/commit, reuse, tracker, routing,
  workflow, board, workspace, and path shapes before mutation.
- Branch, metadata, workspace, authorization, and public result identities are canonical and fail closed.
- Proof surfaces: `tests/test_aidt_worktree_contract.py` and the Frontier 003 contract/runtime matrices in
  `R-LOOP.md`.

## Scope boundary

Does not read cards, run Git, persist records, provision worktrees, publish generations, or integrate Core.
