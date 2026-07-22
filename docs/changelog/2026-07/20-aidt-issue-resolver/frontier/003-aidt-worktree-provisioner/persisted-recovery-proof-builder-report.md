# Persisted recovery-proof builder report

Date: 2026-07-21

Scope: persisted Git recovery-proof API only; no schema, provisioner, live repository, network, or commit

## Decision

The frozen API is sound on the existing `aidt-worktree-v1` snapshot schema. The persisted snapshot already binds the
full ref and registration collection digests/counts and the target payload is fully determined by branch, absolute
path, SHA, upstream absence, and false registration flags. Recovery therefore replaces only that canonical target
record inside the internal digest preimage; it never reconstructs a historical `RepositoryState` or exposes a
caller-controlled digest API.

Implemented exactly these frozen public values:

- `PreparedRecoveryProof` and `prove_prepared_recovery`;
- `ReadyRecoveryProof` and `prove_ready_recovery`;
- `RemovedRecoveryProof` and `prove_removed_recovery`.

Every returned `RepositoryState` is built from a fresh bounded Git observation. Result DTOs validate their own phase,
target, ticket, cleanliness/upstream, and standard delta-digest relationships. Prepared recovery accepts only exact
S1 absence or the completed clean base add. Ready recovery accepts a complete base-created target at the base or a
descendant, with dirty state allowed only for `resume`. Removed recovery accepts only the exact retained branch with
the target path and registration absent.

## Vertical TDD evidence

The implementation proceeded through observable red/green slices:

| Slice | RED | GREEN |
|---|---|---|
| Prepared S1 absence | collection failed: `PreparedRecoveryProof` missing from facade | `1 passed` |
| Prepared completed add | exact completed add raised `collision` | `2 passed` |
| Ready resume | collection failed: `ReadyRecoveryProof` missing from facade | `3 passed` |
| Removed finalization | collection failed: `RemovedRecoveryProof` missing from facade | `4 passed` |
| DTO totalization | malformed state leaked `AttributeError` | `3 passed, 4 deselected` |
| Ready phase/base invariant | unhashable phase leaked `TypeError`; non-base S2 was accepted | `2 passed, 7 deselected` |
| S1 nullable shape | half-null target snapshot did not raise | `2 passed, 9 deselected` |
| Prepared rejection matrix | branch/path/dirty/collision/projection cases added | `20 passed, 54 deselected` |
| Final tamper/projection/DTO matrix | persisted and unrelated-record drift cases added | `17 passed, 75 deselected` |

The final 92-test temporary-Git suite covers S1 absence and completed-add recovery, ready resume and cleanup entry,
retained-branch removal finalization, regular/directory/symlink/broken-symlink artifacts, branch/registration/remote
collisions, wrong SHA/upstream/detached/locked/prunable shapes, tracked/untracked/ignored dirt, descendants and
non-descendants, root/fixed/protected/unrelated drift, deterministic unrelated ref and registration
add/delete/change/rename, persisted field tampering, malformed DTO cross-fields, runner cap propagation, lazy facade
exports, and a command spy rejecting fetch/add/remove/checkout/reset/rebase/prune.

## Verification

| Gate | Exact result |
|---|---|
| Hardened recovery proof file | `92 passed in 114.81s` |
| Existing Git-state + manifest foundation | `209 passed in 25.94s` |
| Ten-file superset: prior nine-suite baseline plus recovery proofs | `537 passed in 141.48s` |
| Ruff `--no-cache` over product, facade, and recovery tests | `All checks passed!` |
| Product Pyright over Git-state and facade | `0 errors, 0 warnings, 0 informations` |
| Executable AST/lazy tests | `2 passed in 0.31s` |
| Independent Git-state AST scan | `139 functions, max 42 lines, max nesting 4` |
| Fresh lazy facade processes | `cold False False`; `git True False`; `manifest False True` |
| Tracked whitespace | `git diff --check` exit 0, no output |
| Owned no-index whitespace checks | expected exit 1, zero output for product/facade/recovery test; final bytes all `0a` |

The only pytest warning was the repository's pre-existing unknown `asyncio_mode` configuration warning when using the
available interpreter environment.

## Scope boundary for the next slice

The paused provisioner still contains `_state_from_snapshot` and has not yet been migrated to these APIs. Its removal,
the prohibition on importing underscored Git helpers, and the direct target `lstat`/`exists`/`is_dir` probe regression
belong to the first provisioner-repair red/green slice. They were deliberately not hidden by an API-only test that
would make this bounded builder suite fail, and no provisioner edit was made here.

## Changed files

- `src/symphony/aidt_worktree/git_state.py`
- `src/symphony/aidt_worktree/__init__.py`
- `tests/test_aidt_worktree_recovery_proofs.py`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/persisted-recovery-proof-builder-report.md`

All edits used `apply_patch`. All Git fixtures were disposable local SHA-1 repositories with the canonical HTTPS
fixture origin. No live AIDT checkout, Jira operation, network request, schema golden, durable fixture, mutation of
user data, or commit occurred.
