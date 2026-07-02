# Delivery Proof

## Eval Intent

- Goal: implement R6 persisted safety valves from the reliability handoff.
- Constraints: preserve existing dispatch semantics, reuse the SQLite run
  registry, wrap registry access with the orchestrator's guard, and avoid
  adding a new operator workflow in this slice.
- Tradeoffs: budget exhaustion remains an operator stop condition; this change
  makes the stop survive restart instead of adding an automatic reset path.

## Before State

- Mode: LEGACY
- Proof: retry attempts lived only in `_retry`, budget exhaustion only in
  `_turn_budget_exhausted`, and pause state only in `_paused_issue_ids`.
- Command or artifact: `docs/plans/2026-07-02-reliability-handoff.md`.
- What this proves: a service crash or restart could forget retry backoff,
  re-dispatch a budget-exhausted issue, or lose an operator pause.

## After Target

- Expected behavior: one registry instance can persist issue flags, a fresh
  orchestrator rehydrates them before dispatch, paused and budget-exhausted
  issues remain ineligible, and persisted retry attempts continue from the
  stored count until continuation, success, or retry-cap escalation clears
  them.
- Compatibility to preserve: existing lease rows, existing pause/resume API,
  retry-cap behavior, and existing in-process budget attention behavior.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| registry-r6 | `.venv/bin/python -m pytest -q tests/test_run_registry.py::test_run_registry_persists_issue_flags_across_reopen tests/test_run_registry.py::test_run_registry_clears_issue_flags_independently` | evaluator_owned | Issue flag rows persist and clear independently | after |
| orchestrator-r6 | `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_persisted_issue_flags_block_dispatch_after_restart tests/test_orchestrator_dispatch.py::test_persisted_retry_attempt_drives_next_dispatch_and_cap tests/test_orchestrator_dispatch.py::test_pause_resume_write_through_issue_flags tests/test_orchestrator_dispatch.py::test_retry_schedule_write_through_and_continuation_clears_issue_flag tests/test_orchestrator_dispatch.py::test_total_turn_budget_exhaustion_write_through_issue_flags` | evaluator_owned | Startup rehydrate plus pause, retry, and budget write-through | after |
| r6-focused | `.venv/bin/python -m pytest -q tests/test_run_registry.py::test_run_registry_persists_issue_flags_across_reopen tests/test_run_registry.py::test_run_registry_clears_issue_flags_independently tests/test_orchestrator_dispatch.py::test_persisted_issue_flags_block_dispatch_after_restart tests/test_orchestrator_dispatch.py::test_persisted_retry_attempt_drives_next_dispatch_and_cap tests/test_orchestrator_dispatch.py::test_pause_resume_write_through_issue_flags tests/test_orchestrator_dispatch.py::test_retry_schedule_write_through_and_continuation_clears_issue_flag tests/test_orchestrator_dispatch.py::test_total_turn_budget_exhaustion_write_through_issue_flags` | frozen_repo | Complete R6 focused contract | after |
| dispatch-regression | `.venv/bin/python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py` | frozen_repo | Run registry and dispatch regression around R6 | after |
| full-tests | `.venv/bin/python -m pytest -q` | frozen_repo | Broad Python regression check | after |
| diff-check | `git diff --check` | frozen_repo | No whitespace errors in edited files | after |
| browser-e2e | `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` | frozen_repo | Browser UI flow executes with Playwright Chromium | after |
| workflow-doctor | `.venv/bin/symphony doctor ./WORKFLOW.md` | frozen_repo | Current workflow validates in the real operator environment | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | auto-fix | resolved | Retry, budget, and pause guards were in-memory only. | Added `issue_flags` to `RunRegistry` with CRUD and startup rehydrate. | registry-r6, r6-focused |
| d2 | auto-fix | resolved | Retry attempts needed to survive restart but not linger after success. | Persist retry attempts on failure retries; clear on continuation, clean exit, and retry-cap escalation. | orchestrator-r6 |
| d3 | auto-fix | resolved | Pause/resume and budget exhaustion used direct set mutation. | Route runtime writes through guarded registry helpers while preserving the in-memory sets. | orchestrator-r6 |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| r6-focused | pass | `7 passed in 0.25s`. | Flag CRUD, restart rehydrate, persisted retry cap, pause/resume write-through, budget write-through. | Full repository behavior |
| dispatch-regression | pass | `105 passed in 7.38s`. | Existing dispatch, lease, pause, retry, budget, and registry behavior around the changed paths. | Live service restart with a real board |
| full-tests | pass | `933 passed, 2 skipped, 1 warning in 59.04s`. | Broad repository regression status after R6. | Real browser runtime |
| diff-check | pass | No output, exit 0. | Edited files have no whitespace errors. | Runtime behavior |
| browser-e2e | pass | `1 passed in 5.79s`. | Browser E2E runs after Playwright Chromium install and outside the sandbox. | Manual operator inspection |
| workflow-doctor | pass | All checks PASS. | Port 9999 free, workspace root writable, board root present, and viewer script available in the real environment. | Running launcher smoke |

## Residual Risk

- Not proven: manual operator clearing of a persisted budget guard,
  cross-process registry contention beyond the existing SQLite busy-timeout
  behavior, and full launcher smoke against olive-clone.
