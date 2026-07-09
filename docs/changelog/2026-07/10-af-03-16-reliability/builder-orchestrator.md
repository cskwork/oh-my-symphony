# Builder evidence - orchestrator reliability

## Scope

Frozen PLAN steps 1, 4, 5, and 6: AF-03, AF-07, AF-08, AF-10, AF-11,
AF-12's running-refresh seam, AF-13, AF-14, AF-15, and AF-16. The backend
builder supplied AF-05's canonical `message`; this builder owns its
orchestrator G2 integration assertion.

## Root causes and decisions

- AF-03: pause time was charged against the old progress clock. A separate
  `resumed_at` floor preserves the last real progress timestamp while granting
  one fresh stall window.
- AF-07: pause was checked before cancellation escalation, and force-eject
  cleanup was not exception-safe. Cancellation now has priority; per-entry
  reconcile and nested cleanup isolate failures while still finishing the
  lease and scheduling retry.
- AF-08: `stop()` awaited each cancelled task without a bound. Worker drain now
  waits one force-eject grace window, records survivors, and kills a recorded
  process group when available.
- AF-10: dead-owner lease reclaim returned recorded agent pids but startup did
  not reap them. Killing stays outside the SQLite transaction and preserves
  nullable legacy pids.
- AF-11: the checked-in contract is a lifetime CI turn cap. A one-shot latch
  warning, full-interval lease postponement, terminal-persist idleness, and
  CI/worker mutual exclusion make that behavior observable and safe.
- AF-12: a successful tracker response that omits a running id is degraded
  state, not success. Reconcile records a visible tracker error and clears it
  when the id returns.
- AF-13: configured active-state order, normalized case-insensitively, defines
  backward movement. Direct callers retain the default-pipeline fallback.
- AF-14: Codex 0.144 and checked-in 0.130 schemas both require `last` and
  `total`; production accounting remains unchanged.
- AF-15: `_completed` had no reader and grew forever, so it was removed.
  `stop()` now also clears issue diagnostics.
- AF-16: first, continuation, and phase-rebuild prompts now share the ticket
  lifetime turn position and `max_total_turns` denominator. `max_turns`
  remains the per-attempt execution cap.

## RED and GREEN evidence

### AF-03

- RED: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_resume_after_long_pause_gets_fresh_stall_window`
  -> exit 1; `1 failed` because resume inherited the stale clock.
- GREEN: the new test plus paused-worker and first-stall regressions -> exit 0;
  `3 passed in 1.62s`.

### AF-07

- RED: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_reconcile_force_ejects_cancelled_worker_even_when_paused tests/test_orchestrator_dispatch.py::test_reconcile_isolates_force_eject_cleanup_and_still_schedules_retry`
  -> exit 1; `2 failed`.
- GREEN: those tests plus existing force-eject, first-stall, and AF-03 cases ->
  exit 0; `5 passed in 1.14s`.

### AF-08

- RED: `.venv/bin/python -m pytest -q tests/test_dispatch_state.py::test_stop_bounds_cancellation_resistant_worker_drain`
  -> exit 1; the stop task remained pending past the bound.
- GREEN: the new test plus existing background-drain coverage -> exit 0;
  `2 passed in 0.21s`.

### AF-12 orchestrator seam

- RED: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_reconcile_marks_running_issue_missing_from_tracker_refresh`
  -> exit 1; no attention signal was recorded.
- GREEN: the missing, present, and refresh-failure cases -> exit 0;
  `3 passed in 0.38s`.
- Harness correction: the first green attempt asserted a nonexistent `detail`
  key instead of the public `message` key (`1 failed, 2 passed`); only that
  assertion changed before the final green run.

### AF-15

- RED: `.venv/bin/python -m pytest -q tests/test_dispatch_state.py::test_dispatch_state_does_not_retain_completed_ids tests/test_dispatch_state.py::test_stop_clears_issue_debug_state`
  -> exit 1; `2 failed`.
- GREEN: those tests plus stale-worker identity coverage -> exit 0;
  `3 passed in 0.10s`.
- Harness correction: one intermediate command named a nonexistent test node
  and exited 4 with no tests collected; the corrected node is
  `test_worker_exit_rechecks_identity_after_finally_gate`.

### AF-10

- RED: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_startup_reclaim_kills_recorded_orphan_agent_before_return`
  -> exit 1; the recorded pid was not killed.
- GREEN: startup integration plus dead-owner and legacy-null registry tests ->
  exit 0; `3 passed in 0.16s`.

### AF-11

- RED: `.venv/bin/python -m pytest -q tests/test_orchestrator_continuous_improvement.py::test_require_idle_board_counts_terminal_persist_pending tests/test_orchestrator_continuous_improvement.py::test_require_idle_board_blocks_dispatch_while_ci_active tests/test_orchestrator_continuous_improvement.py::test_max_turns_latch_warns_once_until_manual_reset tests/test_orchestrator_continuous_improvement.py::test_lease_held_retries_after_full_interval`
  -> exit 1; `4 failed`.
- GREEN: the same four tests -> exit 0; `4 passed in 0.12s`.

### AF-13

- RED: `.venv/bin/python -m pytest -q tests/test_orchestrator_phase_transition.py::test_is_rewind_transition_uses_configured_active_state_order tests/test_orchestrator_phase_transition.py::test_custom_pipeline_rewinds_increment_budget_and_block_at_cap`
  -> exit 1; `3 failed`.
- GREEN: those cases plus default helper and fourth-rewind regression -> exit 0;
  `5 passed in 0.12s`.

### AF-16

- RED: `.venv/bin/python -m pytest -q tests/test_prompt.py::test_first_turn_prompt_accepts_lifetime_turn_budget tests/test_orchestrator_phase_transition.py::test_prompt_turn_budget_continues_across_attempts tests/test_orchestrator_phase_transition.py::test_phase_rebuild_prompt_keeps_lifetime_turn_budget`
  -> exit 1; `3 failed in 0.21s` (`turn_number` rejected and prompts rendered
  `1/5`).
- GREEN: those cases plus existing first-prompt phase/rewind tests -> exit 0;
  `5 passed in 0.13s` (`7/60`, then `8/60`).

### AF-05 G2 integration

The backend builder's canonical `message` field already met the orchestrator
preview contract, so this was a characterization rather than a source RED.
The OpenCode-shaped productive-message reset plus the existing three-empty-turn
guard both pass: `2 passed in 0.37s`. After reset, three truly empty payloads
persist `empty_response_loop` and cancel the worker.

### AF-14 protocol research

- `codex --version` -> `codex-cli 0.144.0`.
- `codex app-server generate-json-schema --experimental --out /private/tmp/codex-schema-af14-20260710`
  -> exit 0.
- Current and checked-in 0.130
  `v2/ThreadTokenUsageUpdatedNotification.json` files both require `last` and
  `total`; `cmp` returns 0 and both hash to
  `fe70a73653ae9e3fffb0db84d1312f47ac47d92526c2d44461492cd864ada3ad`.

## Final verification

- Exact ticket-regression selection across AF-03/05/07/08/10/11/12/13/15/16:
  `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_resume_after_long_pause_gets_fresh_stall_window tests/test_orchestrator_dispatch.py::test_reconcile_force_ejects_cancelled_worker_even_when_paused tests/test_orchestrator_dispatch.py::test_reconcile_isolates_force_eject_cleanup_and_still_schedules_retry tests/test_dispatch_state.py::test_stop_bounds_cancellation_resistant_worker_drain tests/test_orchestrator_dispatch.py::test_reconcile_marks_running_issue_missing_from_tracker_refresh tests/test_dispatch_state.py::test_dispatch_state_does_not_retain_completed_ids tests/test_dispatch_state.py::test_stop_clears_issue_debug_state tests/test_orchestrator_dispatch.py::test_startup_reclaim_kills_recorded_orphan_agent_before_return tests/test_orchestrator_continuous_improvement.py::test_require_idle_board_counts_terminal_persist_pending tests/test_orchestrator_continuous_improvement.py::test_require_idle_board_blocks_dispatch_while_ci_active tests/test_orchestrator_continuous_improvement.py::test_max_turns_latch_warns_once_until_manual_reset tests/test_orchestrator_continuous_improvement.py::test_lease_held_retries_after_full_interval tests/test_orchestrator_phase_transition.py::test_is_rewind_transition_uses_configured_active_state_order tests/test_orchestrator_phase_transition.py::test_custom_pipeline_rewinds_increment_budget_and_block_at_cap tests/test_prompt.py::test_first_turn_prompt_accepts_lifetime_turn_budget tests/test_orchestrator_phase_transition.py::test_prompt_turn_budget_continues_across_attempts tests/test_orchestrator_phase_transition.py::test_phase_rebuild_prompt_keeps_lifetime_turn_budget tests/test_orchestrator_dispatch.py::test_g2_opencode_shaped_payload_resets_only_with_message_key`
  -> `19 passed in 0.74s`.
- Owned regression set (nine modules): `328 passed in 19.42s`.
- `env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev ruff check src tests`
  -> `All checks passed!`.
- `env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pyright src`
  -> `0 errors, 0 warnings, 0 informations`.
- `git diff --check` -> exit 0, no output.

The initial bare `uv run` attempt could not initialize the default
`/Users/danny/.cache/uv` in the restricted sandbox. Setting `UV_CACHE_DIR` to
the writable temporary directory supplied the authoritative full static-check
environment. Before that correction, direct `.venv/bin/pyright src` reported
only unresolved optional third-party imports; the ownership-only direct check
was independently green.

## Rejected alternatives

- Rewriting progress time on resume: loses the distinction between real model
  progress and operator control state.
- Letting pause suppress system cancellation: leaks cancelled workers.
- Unbounded or sequential shutdown waits: violates the stop deadline.
- Killing inside the registry transaction or making the pid non-nullable:
  couples OS side effects to SQLite and breaks old rows.
- Resetting AF-11 automatically per interval: contradicts the lifetime-cap
  contract and can silently re-enable spending.
- Static English rewind pairs: fails custom and non-English pipelines.
- Defensive AF-14 accounting for an invalid wire shape: creates an unsupported
  alternate protocol without evidence.
- Bounding reader-less `_completed`: retains state with no consumer.
- Showing per-attempt values in first prompts: disagrees with continuation and
  the lifetime safety boundary.
