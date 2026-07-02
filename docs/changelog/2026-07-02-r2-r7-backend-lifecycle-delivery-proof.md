# Delivery Proof

## Eval Intent

- Goal: finish the R2/R7 backend lifecycle WIP from `70dbc75`.
- Constraints: preserve the process-group shell invariant, keep base `is_progress_event=True`, update old-contract tests instead of weakening backend behavior, and leave later reliability backlog items untouched.
- Tradeoffs: process-group termination covers stuck backend subprocesses; Gemini bulk-read memory limits stay deferred as a separate LOW-MED item.
- Rejected approaches: restoring raw per-backend `safe_proc_wait` imports for test convenience; changing the base progress predicate to match the original audit text; rebasing local history before the WIP is proven.

## Before State

- Mode: LEGACY
- Proof: handoff lists five known-red tests after `70dbc75` and one missing Codex `TurnFailed` event path; targeted test run captures current failures before edits.
- Command or artifact: `docs/plans/2026-07-02-reliability-handoff.md`; targeted `pytest` output before changes.
- What this proves: the current branch intentionally contains old-contract tests and incomplete lifecycle coverage.
- What this does not prove: the full suite result after later R4+ backlog items, browser behavior, or real external agent CLI auth.

## After Target

- Expected behavior: backend spawn/stop/reap/process-group semantics are covered for all four backends, Codex EOF/corrupt-stream failures emit `turn_failed`, and orchestrator force-eject kills the recorded child process group before retry.
- Compatibility to preserve: no raw `proc.wait()` usage, existing backend API events, current scheduler retry/force-eject semantics, and documented bootstrap environment failure handling.
- Intentional drift: Pi and Gemini progress tests now encode their concrete backend predicates instead of the old base-default contract.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| focused-before | `.venv/bin/python -m pytest -q tests/test_backends.py tests/test_orchestrator_dispatch.py -k 'stop_reaps_with_safe_proc_wait or backend_is_progress_event_defaults_to_true or force_eject'` | frozen_repo | Captures the known red/old-contract surface before edits | before |
| lifecycle-targeted | `.venv/bin/python -m pytest -q tests/test_backends.py tests/test_backends_lifecycle.py tests/test_orchestrator_dispatch.py -k 'stop_reaps_with_safe_proc_wait or backend_is_progress_event or start_new_session or terminate_process_tree or completion_waiter or malformed or post_stream or force_eject or records_backend_agent_pid'` | evaluator_owned | Backend lifecycle and force-eject behavior | after |
| backend-orchestrator | `.venv/bin/python -m pytest -q tests/test_backends.py tests/test_backends_lifecycle.py tests/test_orchestrator_dispatch.py` | frozen_repo | Backend and scheduler regressions around this slice | after |
| bootstrap-targeted | `.venv/bin/python -m pytest -q tests/skills/test_symphony_oneshot_bootstrap.py` | frozen_repo | Generated OneShot workflow bootstraps even when the default port is occupied | after |
| full-tests | `.venv/bin/python -m pytest -q` | frozen_repo | Broad Python regression check | after |
| diff-check | `git diff --check` | frozen_repo | No whitespace errors in edited files | after |
| workflow-doctor | `.venv/bin/symphony doctor ./WORKFLOW.md` | frozen_repo | Current workflow still validates | after |
| browser-e2e | `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` | frozen_repo | Browser UI flow can execute when Playwright Chromium is installed | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | auto-fix | resolved | Codex completion waiter `TurnFailed` currently bypasses `EVENT_TURN_FAILED` emission. | Added the narrow exception handler in `_send_turn_and_resolve`. | `test_codex_completion_turn_failed_emits_failure_event` |
| d2 | auto-fix | resolved | Existing backend tests patch old per-module `safe_proc_wait` imports and old progress defaults. | Rewrote tests around `symphony._shell` and concrete backend predicates. | Backend targeted tests |
| d3 | auto-fix | resolved | Force-eject currently records only the Codex app server pid and may leave the backend process group alive. | Captured `agent_pid` and called `kill_process_group` during force-eject. | `test_reconcile_force_ejects_zombie_after_grace` |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| focused-before | pass | `5 failed, 1 passed, 164 deselected` before edits. | The known-red tests matched the handoff old-contract surface. | New lifecycle coverage |
| lifecycle-targeted | pass | `18 passed, 166 deselected in 0.35s`. | Process-group stop, spawn kwargs, shell escalation, Codex completion waiter, malformed stream guards, bounded Claude post-stream reap, and force-eject PID kill. | Full repository regression |
| backend-orchestrator | pass | `184 passed in 7.17s`. | Backend and orchestrator regressions around R2/R7. | Other repo areas |
| bootstrap-targeted | pass | `3 passed, 1 skipped in 1.55s`. | Bootstrap writes a numeric generated port, substitutes all placeholders, and rejects an explicitly occupied requested port. | Real launch after another process takes the chosen free port |
| full-tests | pass | `909 passed, 2 skipped, 1 warning in 56.96s`. | Broad Python regression status, including the former bootstrap failure. | Browser E2E with a real Chromium binary |
| diff-check | pass | No output, exit 0. | Edited files have no whitespace errors. | Runtime behavior |
| workflow-doctor | blocked | Sandbox run failed on port 9999 in use and `/Users/danny/symphony_workspaces` outside writable roots; escalated rerun was rejected by policy. | Current environment blocks the final doctor gate. | Real unsandboxed doctor status |
| browser-e2e | blocked | `1 skipped`; Playwright Chromium executable missing from `/Users/danny/Library/Caches/ms-playwright/...`; escalated install was rejected by policy. | The opt-in E2E test is wired and skips with the documented missing dependency. | Actual browser interaction |

## Residual Risk

- Not proven: real OS process-group behavior on Windows; Gemini full-output memory cap; real unsandboxed doctor status while port 9999 is occupied; browser E2E without an installed Chromium binary; later R4/R5/R6/A/U backlog.
- Follow-up: finish remaining reliability plan items and final 0.10.0 gate after R2/R7 lands.
