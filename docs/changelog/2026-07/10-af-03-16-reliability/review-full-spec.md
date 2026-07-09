# Full-spec review - AF-03 through AF-16

Role: fresh Full-Spec Improver. Scope: frozen `GOAL.md` and `PLAN.md`, all
AF-03..AF-16 tickets, builder evidence, source/tests/docs, and the complete
working diff. This pass did not edit production source, `GOAL.md`, `QA.md`, or
`run-state.json`.

## Criterion trace

| Ticket | Required behavior | Source/doc implementation | Direct proof | Non-goal trace | Result |
|---|---|---|---|---|---|
| AF-03 | Resume grants a fresh stall window; a later real stall still cancels. | `RunningEntry.resumed_at`, `Orchestrator.resume_worker`, `_reconcile_stall_state`. | `test_resume_after_long_pause_gets_fresh_stall_window`, `test_resumed_worker_stalls_after_fresh_window_expires`, and the existing paused/force-eject regressions. | Stall-timeout configuration is unchanged; cancelled-pause ordering is isolated under AF-07. | covered |
| AF-04 | A running state delta returns 409 before any mixed PATCH field is written; running metadata/same-state and idle state PATCHes remain allowed. | `webapi._register_issue_routes` guards only a case-insensitive state delta through the existing running lookup. | `test_patch_rejects_running_state_change_without_mutating_file`, `test_patch_allows_running_non_state_and_same_state_edits`, `test_patch_moves_state_and_updates_fields`. | No TUI guard or terminal-reconcile change; tracker locking changes are independently required by AF-12. | covered |
| AF-05 | Productive Plain/Gemini/Claude turns produce an orchestrator preview; exit-0 empty stdout fails; only three genuinely empty turns trip G2. | Canonical `message` fields in `plain_cli.py`, `gemini.py`, `claude_code.py`; empty-output rejection in `per_turn.py`; existing `_preview_from_payload`/G2 logic retained. | `PerTurnBackendContract.test_productive_completion_exposes_canonical_message` now calls `_preview_from_payload`; `test_zero_exit_whitespace_stdout_is_a_failed_turn`; `test_g2_opencode_shaped_payload_resets_only_with_message_key`. | No StreamingCli extraction, G2 threshold, or token-accounting change. | covered |
| AF-06 | Legacy `.tmp-*.md` files never enter reads; new atomic temps cannot match board scans; stale startup orphans are swept; id allocation is unchanged. | `FileBoardTracker._ticket_paths`, `_sweep_stale_temps`, non-`.md` `write_ticket_atomic` suffix. | `test_legacy_temp_files_are_ignored_by_every_board_read` (including `next_identifier`), `test_stale_legacy_temp_is_swept_on_startup_with_warning`, `test_atomic_write_temp_does_not_match_board_glob`. | Real-file duplicate handling belongs to AF-12; CAS behavior is unchanged. | covered |
| AF-07 | Paused cancelled zombies eject; one Part-A failure cannot suppress retry or the next issue's stall check; timing stays two-stage. | `_reconcile_stall_state`, per-entry Part-A guard, exception-safe `_force_eject_zombie`. | `test_reconcile_force_ejects_cancelled_worker_even_when_paused`, `test_reconcile_isolates_force_eject_cleanup_and_still_schedules_retry`, existing `test_reconcile_force_ejects_zombie_after_grace`. | No AF-01 exit-identity or AF-02 backend kill expansion; AF-03 resume stamping remains separate. | covered |
| AF-08 | `stop()` bounds cancellation-resistant worker drain, logs/reaps recorded survivors, and wakes paused prompts before cancellation. | `_drain_worker_tasks` plus existing `stop()` pause-event/cancel order. | `test_stop_bounds_cancellation_resistant_worker_drain` now also observes the pause event set before worker cancellation; background-drain tests remain green. | No launcher/signal or `_drain_background_tasks` bound change. | covered |
| AF-09 | A corrupt persistent Codex stream closes, fails waiters/future turns promptly, logs, and reaps; valid lines reset the malformed streak. | `CodexAppServerBackend._stdout_reader` closes and calls the shared process-tree reaper without invoking `stop()` from its own reader. | `test_codex_corrupt_stream_closes_reaps_and_later_turn_fails_fast`, `test_codex_valid_json_resets_malformed_line_streak`. | No limit tuning or restart/reconnect behavior. | covered |
| AF-10 | Dead-owner recovery kills a recorded live agent group before startup returns; null-pid legacy rows still reclaim. | `Orchestrator._ensure_run_registry` consumes existing nullable `RunRecord.backend_agent_pid` outside the SQLite transaction. | Fake-boundary test `test_startup_reclaim_kills_recorded_orphan_agent_before_return`; real POSIX process proof `test_startup_reclaim_terminates_live_recorded_orphan_agent_group`; existing null-pid registry reclaim tests. | No AF-02 writer expansion or worktree-per-attempt redesign. | covered |
| AF-11 | Lifetime cap warns once per latch/reset, lease contention waits an interval, terminal persistence counts as busy, and CI/worker dispatch cannot overlap. | `_improvement_cap_warned`, due-time update, expanded idle predicate, `_eligible` CI gate. | Four focused scheduler regressions: `test_max_turns_latch_warns_once_until_manual_reset`, `test_lease_held_retries_after_full_interval`, `test_require_idle_board_counts_terminal_persist_pending`, `test_require_idle_board_blocks_dispatch_while_ci_active`. | No scheduler extraction, default interval/timeout, or runner-internal change. | covered |
| AF-12 | Parse drops warn; duplicate ids collapse/reject; delete serializes with mutation; an omitted running id becomes visible degraded state. | `FileBoardTracker._scan_all`/`find_path`/`create`/`delete`; `_reconcile_running` missing-id signal and returned-id clear. | `test_parse_failures_warn_in_scan_and_find_path`, `test_duplicate_frontmatter_ids_collapse_deterministically_with_warning`, `test_create_rejects_identifier_found_under_noncanonical_filename`, both `test_delete_serializes_with_ticket_mutations` cases, `test_reconcile_marks_running_issue_missing_from_tracker_refresh`. | No CAS tuning or Jira/Linear parity change; temp exclusion is separately AF-06. | covered |
| AF-13 | Configured later-to-earlier transitions, including Korean states, consume the rewind budget; default behavior stays intact. | `_is_rewind_transition` uses normalized configured order and retains its default compatibility path; core passes active states. | `test_is_rewind_transition_uses_configured_active_state_order`, parametrized `test_custom_pipeline_rewinds_increment_budget_and_block_at_cap`, existing default helper/rewind tests. | Cap value and contract-forced rewind mechanics are unchanged. | covered |
| AF-14 | Close the last-only branch only if current and historical protocol schemas make it unreachable. | No production accounting change; research note and ticket resolution record both schemas. | `codex app-server generate-json-schema --experimental ...`; both notification schemas require `last` and `total`, compare byte-identical with recorded SHA-256. | Other backends and budget thresholds are untouched. | covered by research |
| AF-15 | Reader-less completed ids do not accumulate; stop clears issue diagnostics; persisted retry state stays intact. | `DispatchState.completed` and its unused core property/write are removed; `stop()` clears `_issue_debug`. | `test_dispatch_state_does_not_retain_completed_ids`, `test_stop_clears_issue_debug_state`; code-graph/source search found no completed-set consumer. | Persisted retry accounting is unchanged; no dispatch behavior was added. | covered |
| AF-16 | Initial, continuation, and phase-rebuild prompts share the lifetime numerator/denominator; anchor/template semantics remain deliberate. | `build_first_turn_prompt(turn_number=...)`; core passes completed lifetime turns and `max_total_turns`; workflow examples document the distinction from per-attempt `max_turns`. | `test_first_turn_prompt_accepts_lifetime_turn_budget`, `test_prompt_turn_budget_continues_across_attempts`, `test_phase_rebuild_prompt_keeps_lifetime_turn_budget`; prompt-anchor file has no diff. | No cap-value or prompt-template content redesign. | covered |

Global trace: every production hunk maps to one row above. The only new
full-spec-pass hunks are direct tests, ticket resolution sections, this review,
and missing decision rationale in the dated changelog. AF-01/AF-02, dependency
versions, migrations, public APIs outside the listed guards, and shipped prompt
content are untouched.

## Findings and fixes

1. Closure documentation was inconsistent: AF-04, AF-05, AF-06, and AF-09
   had no resolution/evidence section, and AF-12 recorded only its orchestrator
   half. Added concise ticket-local closure sections without rewriting defect
   history.
2. The dated changelog omitted the decisions and rejected alternatives for
   AF-04/05/06/09 and the tracker half of AF-12, despite the frozen plan's
   all-ticket documentation requirement. Added the missing rationale.
3. AF-03's fresh-window test proved only the immediate post-resume case. Added
   a separate resume-then-expire assertion proving genuine stall cancellation.
4. AF-05's contract test asserted the canonical key but not the promised
   orchestrator preview result. It now calls `_preview_from_payload` directly.
5. AF-06 did not name identifier allocation in a temp-file regression. Added a
   direct `next_identifier` assertion to the every-board-read test.
6. AF-08 bounded shutdown did not directly prove pause/prompt wake ordering.
   The cancellation-resistant worker now records that its pause event was set
   before it observed cancellation.
7. AF-10 used an injected killer only, while the ticket explicitly requested a
   live dummy process. Added a POSIX process-group integration test with bounded
   cleanup; Windows keeps the existing boundary/compatibility proof.

No grounded production-source omission remained after these fixes, so this
pass made no source behavior change and did not manufacture a RED.

## Exact verification

- Direct added assertions:
  `.venv/bin/python -m pytest -q tests/test_backend_contract.py -k productive_completion_exposes_canonical_message`
  plus the five exact test nodes for AF-03/06/08/10 -> `4 passed, 2 skipped`
  for the backend matrix and `5 passed` for the exact nodes. The two skips are
  OpenCode/Pi, which are outside AF-05's affected preview payloads.
- Affected module suites:
  `.venv/bin/python -m pytest -q tests/test_backend_contract.py tests/test_dispatch_state.py tests/test_orchestrator_dispatch.py tests/test_tracker_file.py`
  -> `269 passed, 2 skipped in 17.52s`.
- Remaining ticket-owned modules:
  `.venv/bin/python -m pytest -q tests/test_backends.py tests/test_orchestrator_continuous_improvement.py tests/test_orchestrator_phase_transition.py tests/test_prompt.py tests/test_webapi.py`
  -> `217 passed in 1.05s`.
- Static check:
  `.venv/bin/ruff check tests/test_backend_contract.py tests/test_dispatch_state.py tests/test_orchestrator_dispatch.py tests/test_tracker_file.py`
  -> `All checks passed!`.
- Whitespace/patch check: `git diff --check` -> exit 0, no output.

## Residuals and handoff

- This role did not promote the run to verified/done and did not tick
  `GOAL.md`. The fresh exact verifier must rerun the frozen full commands:
  `uv run --extra dev pytest -q`, `uv run --extra dev ruff check src tests`,
  and `uv run --extra dev pyright src`, plus the AF-14 schema proof.
- The real process-group test is POSIX-only because the repository's
  `kill_process_group` contract is POSIX-only; Windows retains the fake-boundary
  and null-pid compatibility proofs.
- Timing/process tests remain controlled local evidence, not a long-lived live
  board run. The run vault's existing post-deploy smoke residual remains valid.
- Full-spec verdict: no open grounded requirement gap; ready for the separate
  edge-case improver and mandatory adversarial review.
