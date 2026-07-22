# Frontier 003 manifest-helper repair report

Date: 2026-07-21

Status: PASS

## Decision

The bounded manifest-helper surface now enforces the same collision, clock, and state-transition rules as the durable
registry. No schema was weakened and no persistence path was added; writes still go only through the existing CAS,
fsync, and atomic-replace functions.

## Changes

- Optional manifest, ownership, and attempt reads perform one capped parent-directory scan before returning a value.
  Entry names compare by Unicode NFC plus case-fold. An alias, duplicate, symlink, wrong mode/type, malformed record,
  or scan overflow fails; `None` means only a collision-free exact `ENOENT`.
- Due admission normalizes its consumed backoff timestamp to the current whole UTC second. Active prepared/added phase
  transitions do the same, so elapsed time through 600 seconds cannot self-invalidate a valid record.
- Every durable attempt write rejects a clock earlier than its source record. Backward time leaves non-due backoff
  blocked and cannot regress `updated_at` through phase, failure, scope-reset, or attempt-update paths.
- Constructor transitions are closed:
  - exact initial record: revision 1, due backoff, attempt 0, phase `none`, null manifest revision;
  - consumed backoff attempt 1-3: `none -> prepared` at manifest revision 1;
  - exact prepared attempt: `prepared -> added` at manifest revision 1;
  - eligible prepared/added success: `ready` at manifest revision 2;
  - exact ready/added revision-2 record: `removing` at manifest revision 3.
- Manual, attempt-zero, already-ready, wrong-phase, and arbitrary manifest-revision promotion fails closed.
- The package facade lazily exports all six authorized helpers without loading `manifest` during the default import.

Rejected alternatives: relaxing `_bounded_retry_time`, preserving a consumed retry timestamp older than
`updated_at`, clamping a backward clock, scanning the entire registry through `discover_registry`, or allowing the
provisioner to construct records directly.

## TDD evidence

Red first:

- `tests/test_aidt_worktree_manifest.py`: 9 failed, 39 passed.
- Failures reproduced all three missed case aliases, elapsed admission/phase invalidation at 1 and 600 seconds,
  backward timestamp regression, and impossible manual/attempt-zero/arbitrary-revision promotions.

Green:

| Gate | Exact result |
| --- | --- |
| Focused manifest suite | 51 passed in 0.62s; repeat 51 passed in 0.48s |
| Worktree contract + manifest | 83 passed in 0.46s |
| Worktree contract + manifest + repaired Git-state | 145 passed in 29.22s |
| Ruff `--no-cache`, exact product/test scope | all checks passed |
| Pyright, exact product/test scope | 0 errors, 0 warnings, 0 informations |
| Product AST | 94 functions; maximum 34 lines; maximum nesting 3; no violations |
| Fresh-process lazy facade | passed |
| Tracked diff and owned-file whitespace/EOF | clean |

## Scope and safety

Owned product/test files only:

- `src/symphony/aidt_worktree/manifest.py`
- lazy helper exports in `src/symphony/aidt_worktree/__init__.py`
- `tests/test_aidt_worktree_manifest.py`

No provisioner draft, Git-state implementation, runtime, workspace, orchestrator, live repository, network, Jira,
Jenkins, branch/ref, or commit operation was changed or executed by this repair.
