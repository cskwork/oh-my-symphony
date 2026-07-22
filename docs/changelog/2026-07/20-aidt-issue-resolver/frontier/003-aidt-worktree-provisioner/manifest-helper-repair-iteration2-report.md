# Frontier 003 manifest foundation repair iteration 2

Date: 2026-07-21

## Verdict

PASS for the manifest-owned slice.

The durable attempt validator now treats category, disposition, attempt count, mutation phase, manifest revision,
and retry clock as one canonical state. Permanent/manual-only failures cannot persist as backoff or reach prepared or
ready. Optional manifest, ownership, and attempt readers stop on the 2,501st directory entry, retain only collision
matches, and close the scan without requesting a 2,502nd entry.

## State theory

An attempt record models one of four operational states:

| State | Canonical shape |
| --- | --- |
| Initial or scope reset | `attempt_backoff|scope_changed`, backoff, attempt 0, phase `none`, null manifest revision, `retry_at == updated_at` |
| Waiting or consumed provision | exact active category allowlist, backoff, attempts 1..3; `none` has null revision, `prepared|added` has revision 1 |
| Manual preservation | every failure category except `ready|attempt_backoff|scope_changed`, manual, attempts 1..3, null retry; phase/revision is `none/null`, `prepared|added/1`, or `removing/3` |
| Ready or removing | category/disposition `ready`, attempts 1..3, null retry; phase/revision is `added/2` or `removing/3` |

The active category allowlist is exactly `attempt_backoff`, `scope_changed`, `lock_timeout`, `fetch_timeout`, and
`fetch_command_failed`. A waiting retry remains durable but cannot advance mutation phase until admission consumes it.
Consumed and post-intent backoff records require `retry_at == updated_at`. `attempt_exhausted` is manual at attempt 3
only. These constraints preserve the existing whole-second UTC, bounded retry, revision CAS, and scope-reset rules.

## Repairs

### Canonical attempt coupling

- Split validation into disposition-specific state checks and exact phase/revision pairs.
- Replaced the negative `category != ready` provision guard with the exact positive active-category allowlist.
- Kept true transient retries valid before intent and manual after intent or exhaustion.
- Prevented a future waiting retry from calling phase helpers before due admission.
- Added regressions for every permanent/manual-only category, all five valid active categories, both valid attempt-zero
  categories, waiting/consumed retry clocks, and canonical optional attempt reads.

### Bounded optional collision scan

- Replaced whole-directory list materialization with cap-plus-one iteration.
- Stop and fail closed immediately on entry 2,501.
- Retain only NFC/case-fold names matching the requested canonical path.
- Close the scan iterator on success and failure.
- Exercise the same no-overread sentinel through all three public optional readers.

## TDD evidence

Initial red:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_manifest.py
```

Result: `33 failed, 56 passed in 1.69s`. The failures reproduced permanent-category backoff acceptance, invalid
attempt-zero retry categories, and overread by all three optional readers.

Retry-clock refinement red:

```text
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_worktree_manifest.py -k 'waiting_retry or active_mutation_phases'
```

Result: `2 failed, 89 deselected in 0.15s`. A future waiting retry advanced to prepared and an active prepared record
accepted an unconsumed future retry clock.

Green:

| Gate | Exact result |
| --- | --- |
| Focused manifest | `91 passed in 0.55s` |
| Worktree contract + manifest | `123 passed in 0.64s` |
| Git-state + manifest | `173 passed in 32.77s` |
| Nine-suite pre-provisioner superset | `409 passed in 35.48s` |
| Ruff `--no-cache`, Git-state/manifest/facade/focused tests | `All checks passed!` |
| Pyright, Git-state/manifest/facade product scope | `0 errors, 0 warnings, 0 informations` |
| Pyright, manifest product + focused test | `0 errors, 0 warnings, 0 informations` |
| Product AST | manifest 101 functions, maximum 34 lines, maximum nesting 3; Git-state gate passed |
| Fresh-process lazy facade | cold `False/False`; Git access `True/False`; manifest access `False/True` |
| Tracked whitespace | `git diff --check` exit 0, no output |

The pre-provisioner command covered routing contract, storage, decision, Git objects, runtime, route dispatch,
worktree contract, manifest, and Git-state. It expands the prior 349-test baseline and excludes the paused
provisioner/runtime drafts.

## Scope and safety

Changed only:

- `src/symphony/aidt_worktree/manifest.py`
- `tests/test_aidt_worktree_manifest.py`
- this report

No facade, provisioner, Git-state, runtime, workspace, orchestrator, live repository, network, Jira, AIDT, Jenkins,
branch/ref, commit, or deployment operation was changed or executed by this manifest repair.
