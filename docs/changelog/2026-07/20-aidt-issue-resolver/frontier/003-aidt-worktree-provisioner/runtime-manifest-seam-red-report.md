# Runtime manifest seam RED report

Date: 2026-07-21
Scope: PLAN Binding Amendment 5, manifest attempt validation, and the public attempt persistence seam
Verdict: **INTENTIONAL RED / READY FOR THE PRODUCT VALIDATOR FIX**

## Decision

The manifest suite now has the smallest executable regression for Amendment 5. A valid public ready
`AttemptRecord` at `mutation_phase="added"` and `manifest_revision=2` is passed to
`next_failure_record(..., "registry_invalid", "added", 2, monotonic_now)`. The expected manual failure preserves
the attempt, identifier, route-pair digest, workflow generation, and creation time; clears `retry_at`; truthfully
retains `added/2`; advances the record revision; and persists and reads back through the public revision-CAS API.

The new test is intentionally RED against the current product validator. No product, runtime, or facade code was
changed in this slice.

## Exact RED boundary

`tests/test_aidt_worktree_manifest.py:485-519` constructs and persists the valid ready sidecar, then exercises only
public manifest APIs. `next_failure_record` builds the expected `manual/registry_invalid/added/2` value, but
`AttemptRecord.__post_init__` raises `AidtWorktreeFailure("registry_invalid")` before the CAS assertion can run.

The rejection is the current `_valid_manual_attempt` path:

1. `_valid_attempt_state` dispatches manual records to `_valid_manual_attempt`
   (`src/symphony/aidt_worktree/manifest.py:1059-1065`).
2. `_valid_manual_attempt` delegates its phase/revision decision to `_manual_phase_revision`
   (`src/symphony/aidt_worktree/manifest.py:1081-1089`).
3. `_manual_phase_revision` accepts active `none`, `prepared/1`, `added/1`, or `removing/3`, but not the truthful
   ready-evidence failure shape `added/2` (`src/symphony/aidt_worktree/manifest.py:1104-1115`).

That validator seam is the intended product repair point. The test does not prescribe a private helper or broaden
accepted arbitrary revisions.

## Compatibility protection

Existing exact-shape coverage remains authoritative rather than being copied into the new regression:

- `test_attempt_constructors_enforce_exact_source_phase_and_manifest_revisions` verifies the valid public phase
  transitions for attempt counts 1-3.
- `test_attempt_constructors_reject_manual_attempt_zero_ready_and_arbitrary_revisions` continues to reject invalid
  source phases and revisions, including `prepared/99`, `ready/removing/2`, and `added/99`.

Those focused controls pass as four cases, and all 91 pre-existing manifest cases pass when the new intentional RED
case is deselected.

## Verification evidence

| Gate | Exit | Result |
|---|---:|---|
| Pre-change manifest baseline | 0 | `91 passed in 0.60s` |
| Focused Amendment 5 seam | 1 | `1 failed in 0.21s`; exact rejection while constructing the manual `added/2` record |
| Final complete manifest suite | 1 | `1 failed, 91 passed in 0.67s`; only the new intentional RED fails |
| Existing manifest baseline after test addition | 0 | `91 passed, 1 deselected in 0.81s` |
| Existing exact/arbitrary revision controls | 0 | `4 passed in 0.16s` |
| Ruff, no cache | 0 | `All checks passed!` |
| Pyright | 0 | `0 errors, 0 warnings, 0 informations` |
| Test AST | 0 | 49 functions; max 45 lines/nesting 3; new test 37 lines/nesting 0; no violations |
| No-index whitespace: manifest test | 1 | Expected content-difference exit; no output, so no whitespace finding |
| No-index whitespace: RED report | 1 | Expected content-difference exit; no output, so no whitespace finding |

The first Ruff invocation could not initialize a cache in the isolated worktree; the equivalent `--no-cache` gate
passed.

## Scope and coordination

Changed paths are limited to:

- `tests/test_aidt_worktree_manifest.py`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/runtime-manifest-seam-red-report.md`

No network, live repository, product source, runtime, facade, Git state, or commit operation was used. The platform
agent-thread cap prevented creation of another fresh child, so the parent assigned this independent test-only
follow-up to the existing review thread. This report keeps the test result and product repair boundary explicit for
the separate product builder.
