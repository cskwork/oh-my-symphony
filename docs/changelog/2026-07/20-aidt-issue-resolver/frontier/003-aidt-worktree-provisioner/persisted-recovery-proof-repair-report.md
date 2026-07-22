# Persisted recovery-proof repair report

Date: 2026-07-21

Scope: bounded persisted Git recovery proofs only; no provisioner, runtime, schema, live repository, network, or commit

## Decision

The acceptance-review defects are closed without changing the frozen public signatures or snapshot schema.

Every proof now starts from an identity/ref/registration observation and closes it with the same bounded observation.
The closing bracket checks the no-follow target-path shape after the closing collection commands; complete targets then
repeat ticket HEAD, symbolic branch, porcelain status digest, cleanliness, and upstream absence, followed by one final
path witness. Collection change fails as `identity_invalid`, path/target change as `collision`, and ticket status change
as `content_invalid`. Returned states use the initial raw collections only after the closing collections prove exact
equality; returned tickets use the equal closing ticket observation. Cleanup therefore cannot return a stale clean
witness across any tested observation seam.

Public proof DTO construction now validates before traversing nested values. Validation covers exact tuple and DTO
member types, bounded/unique refs and registrations, branch/ref/SHA/bool grammar, canonical absolute paths, snapshot
constructor validity, raw collection digests and counts, protected occupancy, fixed-base ref, target ref/registration/
upstream consistency, every ticket field, lowercase 64-hex status digests, and the equivalence between `clean=True`
and the canonical empty-status digest. All malformed constructor inputs totalize to
`AidtWorktreeFailure("protocol_invalid")`; no raw `TypeError` or `AttributeError` escapes.

## Vertical TDD evidence

| Slice | RED | GREEN |
|---|---|---|
| Initial collection/path and ticket races | `5 failed, 92 deselected in 14.84s`; every proof returned instead of raising | `5 passed, 92 deselected in 12.84s` after the first closing bracket |
| Nested DTO totalization | raw `TypeError: 'NoneType' object is not iterable`; `1 failed, 97 deselected in 1.50s` | `5 passed, 93 deselected in 2.18s` after validating nested shapes before traversal |
| Path mutation during closing collections | prepared/removed returned; ready/cleanup surfaced `path_invalid`; `4 failed, 143 deselected in 5.70s` | closing collections then final path witness preserves `collision`; `4 passed, 143 deselected in 7.02s` |
| All prepared/ready/cleanup/removed race seams | collection, path, and ticket mutations exercised across every applicable phase | `15 passed, 132 deselected in 28.88s` |

The loops were vertical: each executable failing slice was run before its cohesive product correction. The later
matrix additions retained those tests and added real temporary-Git rejection rows rather than replacing the RED
evidence with implementation-coupled assertions.

## Acceptance matrix closure

The focused suite now has 153 cases. Additions cover:

- inter-observation collection, path, and ticket changes for prepared absence/completed-add, ready resume,
  cleanup-pre, and removed recovery, including mutation during the closing collection commands;
- ready/cleanup root HEAD and symbolic drift, same-status ignored root-content mutation, protected and unrelated
  registration drift, moved path, detached and prunable target registrations, and the prior branch/upstream/ref rows;
- removed mismatched registration, protected/unrelated registration, root HEAD/symbolic drift, and an already-complete
  persisted phase, in addition to retained-ref/path/fixed/root/remote cases;
- persisted snapshot digest/count/base/target tampering plus 10,000/10,001, 512 MiB/plus-one, and
  2,500/2,501 count boundaries where the schema permits a bounded forged witness;
- field-by-field public DTO malformed containers, members, state scalars, ref/registration fields, snapshot-to-raw
  inconsistencies, ticket path/head/branch/status/clean/upstream values, uppercase/non-hex digests, and
  clean/status-digest disagreement.

All fixtures are disposable local SHA-1 repositories with the canonical HTTPS fixture origin. The command spy still
rejects mutation/network commands; the proofs issue only the existing bounded local observation commands and
no-follow filesystem witnesses.

## Verification evidence

| Gate | Exact result |
|---|---|
| Focused recovery proofs, final bytes | `153 passed in 217.72s (0:03:37)` |
| Existing Git-state + manifest foundation | `209 passed in 24.53s` |
| Ten-file compatibility superset | `592 passed in 308.18s (0:05:08)` |
| Final closing-collection seam after test-only Ruff refactor | `4 passed, 143 deselected in 7.02s` |
| Explicit ref/registration/ticket HEAD disagreement rows | `6 passed, 147 deselected in 6.81s` |
| Ruff `--no-cache` over product, facade, and recovery tests | `All checks passed!` |
| Product + recovery-test Pyright | `0 errors, 0 warnings, 0 informations` |
| Executable AST/lazy gates | `2 passed, 116 deselected in 0.23s` |
| Independent Git-state AST scan | `functions=149 max_lines=47 max_nesting=4` |
| Tracked whitespace | `git diff --check`: exit 0, zero output |
| Owned untracked whitespace | three `git diff --no-index --check /dev/null <file>` checks: expected content-difference exit, zero whitespace output |

Edits after the 592-test superset were test-only: the Ruff-required conversion of assigned lambdas into one equivalent
nested test function and six explicit target ref/registration/ticket HEAD disagreement rows. The complete final
153-case recovery file, the six new rows, Ruff, Pyright, AST/lazy, and whitespace checks were rerun on the final bytes.

## Changed files

- `src/symphony/aidt_worktree/git_state.py`
- `tests/test_aidt_worktree_recovery_proofs.py`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/persisted-recovery-proof-repair-report.md`

The lazy facade already exposed the frozen DTOs and operations correctly, so it was intentionally unchanged. No
provisioner/runtime/schema file, durable fixture, live AIDT checkout, user data, remote service, Jira state, branch,
tag, or commit was changed.
