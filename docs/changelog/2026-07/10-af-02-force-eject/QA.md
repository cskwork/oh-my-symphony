# QA - AF-02 force-eject kills all backend process groups

All testing results are recorded as succinct evidence, not transcripts.

- Verdict: PASS

## Before

- [x] Existing force-eject, event pid recording, and lease persistence behavior captured before product-code changes: 3 focused neighbor tests and 7 event/contract tests passed on `bf4ba63` - evidence: `qa/baseline/neighbor-tests.txt`
- [x] New non-Codex backend-neutral force-eject acceptance test observed RED before source migration - evidence: `qa/baseline/red-force-eject.txt`
- [x] New shared two-turn spawn contract observed RED in all six per-turn adapters before spawn-time emission: no `turn_started` event existed while the fake child was live and blocked on output - evidence: `qa/baseline/red-turn-spawn-pid.txt`
- [x] New same-phase regression observed RED before the pre-turn refresh: the second turn inherited pid `11111` while the backend reported no live child - evidence: `qa/red-stale-per-turn-agent-pid.txt`
- [x] New lifecycle regressions observed RED before final synchronization: a completed per-turn pid remained during a blocked after-run hook, and persistent startup initialization observed `[None, 11111]` instead of `[11111, 22222]` - evidence: `qa/red-lifecycle-pid-sync.txt`
- [x] Iteration-4 ownership regressions observed RED: no explicit persisted clear, stale persisted pids after success/failure, swallowed old-stop failure, and an uncaptured late-start pid - evidence: `qa/red-persisted-owner-lifecycle.txt`
- [x] Iteration-5 stop-confirmation regressions observed RED: an old backend's idempotent final stop erased pid `11111` after its first stop marked closed and raised, and a replacement backend's failed cleanup lost pid `22222` when the outer client still referenced the old backend - evidence: `qa/red-finalizer-stop-confirmation.txt`, `qa/red-replacement-cleanup-confirmation.txt`
- [x] Iteration-7 unsafe-pid regressions observed RED: boolean, zero, and negative process-group values reached the final kill boundary; malformed normalized events were recorded or fell back to a legacy pid - evidence: `qa/red-bool-agent-pid.txt`

## Results

- [x] Targeted AF-02 tests pass: focused force-eject 2 passed; shared backend lifecycle/event pid contract 7 passed.
- [x] Full-spec backend coverage includes the persistent Codex lifecycle as well as all six per-turn adapters; combined acceptance selector passed 10 tests.
- [x] Every per-turn family now emits normalized `turn_started` immediately after publishing each child; six shared contract cases observed distinct pids `11111` then `22222` before any turn outcome.
- [x] Neighbor baseline re-runs without unnamed drift: 4 passed after adding the named missing-pid case (baseline was 3 passed).
- [x] Before every turn, process ownership is refreshed from the backend's current pid; the focused stale-pid regression and same-phase neighbor passed, and the combined AF-02 selector passed 17 tests.
- [x] After every turn, ownership is refreshed again before after-run work; persistent initial and phase-rebuilt starts publish and heartbeat their pids before initialization. Both focused lifecycle regressions, all 34 phase-transition tests, and the 19-test combined AF-02 selector passed.
- [x] In-memory and persisted ownership now move together: explicit clear preserves normal heartbeat semantics; successful/failed per-turn paths clear stale rows; late start failure records its pid before cleanup; unconfirmed cleanup retains ownership; failed old-phase stop aborts replacement. Focused 7, RunRegistry 13, phase-transition 38, and combined AF-02 26 tests passed.
- [x] Old and replacement cleanup failures remain explicitly unconfirmed across later idempotent final-stop calls, retaining their last recorded process groups in memory and in the run registry. The three focused stop-confirmation regressions, all 40 phase-transition tests, and the 28-test combined AF-02 selector passed.
- [x] PID/PGID inputs are safe at ingestion and use: only positive, non-boolean integers can be persisted or sent to `kill_process_group`; a present invalid normalized key cannot fall back to the legacy key. Focused controls passed 11, the combined AF-02 selector passed 34, and the affected six-module set passed 266.
- [x] Lint, type check, and full coverage suite pass. The fresh exact-verifier run reported Ruff clean, Pyright 0 errors, and 1,312 passed / 2 skipped at 83.26% coverage. The earlier 14-test compatibility regression was resolved in iteration 6 and re-proved by the later compatibility selector and full suite.

Backward-trace: clean

### Exact Verify

Iteration 7 exact verification used `/opt/anaconda3/bin/python` with `PYTHONPATH=src` for every test command, exercising the isolated worktree source with the dependency-complete interpreter. The earlier `.venv` Ruff/Pyright/pytest-cov mismatch was resolved by this interpreter choice. The 14-test legacy-backend compatibility regression found in the first dependency-complete full run was fixed in iteration 6 and is green in the later compatibility selector and this final full suite.

| Command | Result | Proves |
|---|---|---|
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_orchestrator_dispatch.py -k force_eject` | PASS - 5 passed, 168 deselected in 0.18s | Non-Codex kill, backend-kind logging, missing/unsafe-pid preservation |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py -k 'agent_pid or full_lifecycle or unsafe_normalized_pid'` | PASS - 12 passed, 193 deselected in 0.24s | Backend pid envelope, lifecycle ownership, normalized-pid safety |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_backend_contract.py -k 'turn_spawn or agent_pid'` | PASS - 7 passed, 25 deselected in 0.15s | Per-turn spawn-time pid publication and persistent Codex pid event |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'backend_agent_pid or force_eject or unsafe_normalized_pid'` | PASS - 11 passed, 175 deselected in 0.22s | Registry ownership, force-eject neighbors, unsafe input rejection |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_orchestrator_phase_transition.py -k 'phase_stop_failure_stays_unconfirmed_after_idempotent_final_stop or phase_transition_stop_failure or replacement_stop_failure_stays_unconfirmed_after_old_final_stop'` | PASS - 3 passed, 37 deselected in 0.15s | Stop-confirmation ownership boundaries |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_agent_lifecycle_e2e.py tests/test_orchestrator_contract_integration.py tests/test_orchestrator_dispatch.py -k 'full_todo_to_done_pipeline_rebuilds_backend_per_phase or lifecycle_stops_each_intermediate_backend_exactly_once or lifecycle_renders_verify_template_after_in_progress or file_board_e2e_auto_triage_dispatches_and_reaches_done or contract_passes_when_disk_has_required_sections or contract_fails_when_disk_missing_sections or qa_scorecard_fail_warns_without_rewind or stale_zombie_finally_does_not_eject_fresh_replacement_entry or worker_loop_stops_before_starting_past_total_turn_budget or worker_loop_no_stage_change or verify_state_turn_cap_blocks_with_budget_artifact or g2_empty_response_loop_does_not_block_phase_transitions'` | PASS - 14 passed, 167 deselected in 0.30s | The prior 14-test compatibility set and lifecycle neighbors remain green |
| `/opt/anaconda3/bin/python -m ruff check src tests` | PASS - `All checks passed!` | Repository lint gate |
| `/opt/anaconda3/bin/python -m pyright --pythonpath /opt/anaconda3/bin/python` | PASS - 0 errors, 0 warnings, 0 informations | Repository type gate with dependency-complete interpreter |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` | PASS - 1,312 passed, 2 skipped, 2 warnings in 99.04s; coverage 83.26% | Full CI-equivalent behavior and coverage gate |
| `git diff --check` | PASS | Patch is whitespace-clean |
| `git diff --name-status dev`; `git status --short` | PASS - 12 tracked AF-02 files plus the untracked run vault inspected | Diff remains scoped to AF-02 source/tests, the required daily changelog, and verifier artifacts |

### Iteration 6 - legacy backend compatibility repair

The lifecycle now normalizes optional backend pid reads through one helper. Missing and non-integer
values become `None`; real backend integer pids retain their existing ownership behavior. This
preserves older backend doubles without weakening the `AgentBackend` protocol.

| Command | Result | Proves |
|---|---|---|
| `env PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/test_agent_lifecycle_e2e.py::test_full_todo_to_done_pipeline_rebuilds_backend_per_phase` | RED - 1 failed in 0.12s with `AttributeError: '_FakeBackend' object has no attribute 'pid'` | Existing lifecycle fixture reproduces the exact full-suite compatibility failure; evidence: `qa/red-legacy-backend-without-pid.txt` |
| `env PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q --lf` | PASS - 14 passed in 0.28s | Every exact-verifier failure is cleared |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_agent_lifecycle_e2e.py tests/test_orchestrator_contract_integration.py tests/test_backend_contract.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py tests/test_run_registry.py` | PASS - 260 passed in 15.42s | Lifecycle, contract, backend, dispatch, phase, and registry neighbors remain green |
| combined AF-02 selector from iteration 5 | PASS - 28 passed, 224 deselected in 0.27s | PID spawn, persistence, cleanup confirmation, and force-eject behavior remain green |
| `/opt/anaconda3/bin/python -m ruff check src tests`; `/opt/anaconda3/bin/python -m pyright --pythonpath /opt/anaconda3/bin/python`; `git diff --check` | PASS - Ruff clean; Pyright 0 errors; diff clean | Iteration-6 source and tests satisfy static and whitespace gates |

### Iteration 7 - safe PID/PGID normalization

The ownership pipeline now rejects booleans and non-positive integers before they can become a
process-group target. Normalized event-key presence still wins over the legacy key, so malformed
normalized input is ignored instead of silently selecting another value.

| Command | Result | Proves |
|---|---|---|
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_orchestrator_dispatch.py -k 'unsafe_normalized_pid or without_safe_pgid'` before source change | RED - 6 failed, 1 passed, 166 deselected in 0.82s | Boolean, zero, and negative event/stored values reached ownership or kill paths; evidence: `qa/red-bool-agent-pid.txt` |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_orchestrator_dispatch.py -k 'unsafe_normalized_pid or without_safe_pgid or records_backend_agent_pid or accepts_legacy_codex_app_server_pid or prefers_normalized_agent_pid or force_eject'` | PASS - 11 passed, 162 deselected in 0.24s | Unsafe values are ignored while valid, legacy-only, normalized-precedence, positive kill, and missing-pid retry controls remain green |
| combined AF-02 selector including `unsafe_normalized_pid` | PASS - 34 passed, 224 deselected in 0.29s | Spawn, ingestion, persistence, cleanup confirmation, and force-eject safety remain green together |
| `env PYTHONPATH=src /opt/anaconda3/bin/python -m pytest -q tests/test_agent_lifecycle_e2e.py tests/test_orchestrator_contract_integration.py tests/test_backend_contract.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py tests/test_run_registry.py` | PASS - 266 passed in 16.19s | Re-runs iteration 6's affected module set, including the modules containing all 14 prior compatibility failures |
| `/opt/anaconda3/bin/python -m ruff check src tests`; `/opt/anaconda3/bin/python -m pyright --pythonpath /opt/anaconda3/bin/python`; `git diff --check` | PASS - Ruff clean; Pyright 0 errors, 0 warnings, 0 informations; diff clean | Iteration-7 source, tests, and docs satisfy static and whitespace gates |

## Commands

| Command | Source | Proves |
|---|---|---|
| `python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'backend_agent_pid or force_eject'` | evaluator_owned | Shared-state neighbor baseline and force-eject behavior |
| `python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py -k 'agent_pid or full_lifecycle'` | evaluator_owned | Backend event pid contract and orchestrator recording |
| `python -m ruff check src tests` | frozen_repo | Repository lint gate from CI |
| `python -m pyright` | frozen_repo | Repository type gate from CI |
| `python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` | frozen_repo | Full CI-equivalent behavior and coverage gate |

### Builder run-to-prove

| Command | Source | Result | Proves |
|---|---|---|---|
| `python -m pytest -q tests/test_orchestrator_dispatch.py -k force_eject` | agent_detected | RED — 1 failed, 156 deselected in 0.70s; exit 1 | Before-source tracer bullet rejects missing `agent_pgid`; raw output in `qa/baseline/red-force-eject.txt` |
| `env PYTHONPATH=src python -m pytest -q tests/test_orchestrator_dispatch.py -k force_eject` | agent_detected | PASS — 2 passed, 156 deselected in 0.35s | Claude process group is killed and missing pid still releases/retries |
| `env PYTHONPATH=src python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py -k 'agent_pid or full_lifecycle'` | agent_detected | PASS — 7 passed, 176 deselected in 0.42s | Every shared backend publishes a live turn pid and the orchestrator records it as `agent_pgid` |
| `env PYTHONPATH=src python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'backend_agent_pid or force_eject'` | agent_detected | PASS — 4 passed, 166 deselected in 0.14s | Run-registry pid persistence and force-eject neighbors retain only the named AF-02 drift |
| `python -m ruff check src tests` | agent_detected | PASS — `All checks passed!` | Repository lint gate |
| `python -m pyright` | agent_detected | HISTORICAL ENVIRONMENT MISMATCH — 24 unresolved-import errors, 3 warnings; resolved by the later `/opt/anaconda3/bin/python -m pyright --pythonpath /opt/anaconda3/bin/python` exact gate | Documents why the first interpreter was not authoritative |
| `env PYTHONPATH=src python -m pytest -q tests/test_backend_contract.py::test_codex_live_event_exposes_agent_pid tests/test_backend_contract.py tests/test_orchestrator_dispatch.py -k 'agent_pid or full_lifecycle or force_eject'` | agent_detected | PASS — 10 passed, 174 deselected in 0.20s | Codex and all per-turn adapters publish `agent_pid`; entry recording, process-group kill, backend-kind logging, and missing-pid retry remain green |
| `env PYTHONPATH=src python -m pytest -q --tb=short tests/test_backend_contract.py -k turn_spawn_events_publish_distinct_pids_immediately` | agent_detected | RED — 6 failed, 26 deselected in 0.88s; exit 1 | Before-source contract found no spawn-time pid event in Claude, shared per-turn, or Pi; raw output in `qa/baseline/red-turn-spawn-pid.txt` |
| `env PYTHONPATH=src python -m pytest -q tests/test_backend_contract.py -k turn_spawn_events_publish_distinct_pids_immediately` | agent_detected | PASS — 6 passed, 26 deselected in 0.11s | Two successive blocked-output spawns publish distinct live pids immediately in all six per-turn adapters |
| `env PYTHONPATH=src python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py -k 'turn_spawn or agent_pid or full_lifecycle or force_eject'` | agent_detected | PASS — 16 passed, 174 deselected in 0.18s | Spawn replacement, backend pid envelopes, orchestrator recording, and force-eject behavior remain green together |
| `python -m ruff check src/symphony/backends tests/test_backend_contract.py` | agent_detected | PASS — `All checks passed!` | Spawn-event implementation and shared contract satisfy focused lint |
| `env PYTHONPATH=src python -m pytest -q tests/test_orchestrator_phase_transition.py -k next_turn_clears_stale_per_turn_agent_pid` | agent_detected | RED — 1 failed, 31 deselected in 0.19s; exit 1 | Before-source second turn retained prior pid `11111`; raw output in `qa/red-stale-per-turn-agent-pid.txt` |
| `env PYTHONPATH=src python -m pytest -q tests/test_orchestrator_phase_transition.py -k 'next_turn_clears_stale_per_turn_agent_pid or same_phase_does_not_restart_backend'` | agent_detected | PASS — 2 passed, 30 deselected in 0.15s | A new per-turn attempt clears stale ownership without rebuilding the same-phase backend |
| `env PYTHONPATH=src python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py -k 'turn_spawn or agent_pid or full_lifecycle or force_eject or next_turn_clears_stale_per_turn_agent_pid'` | agent_detected | PASS — 17 passed, 205 deselected in 0.19s | Spawn, record, clear, kill, missing-pid, and lifecycle behaviors remain green together |
| `python -m ruff check src/symphony/orchestrator/core.py tests/test_orchestrator_phase_transition.py` and `git diff --check` | agent_detected | PASS | R-loop iteration 2 is lint- and whitespace-clean |
| `env PYTHONPATH=src python -m pytest -q tests/test_orchestrator_phase_transition.py -k 'completed_per_turn_pid_is_cleared_before_after_run_blocks or persistent_backend_pid_is_registered_before_each_initialize'` | agent_detected | RED — 2 failed, 32 deselected in 0.30s; exit 1 | Completed per-turn ownership remained `11111`; initial/rebuilt persistent initialize observed `[None, 11111]`; raw output in `qa/red-lifecycle-pid-sync.txt` |
| `env PYTHONPATH=src python -m pytest -q tests/test_orchestrator_phase_transition.py -k 'completed_per_turn_pid_is_cleared_before_after_run_blocks or persistent_backend_pid_is_registered_before_each_initialize'` | agent_detected | PASS — 2 passed, 32 deselected in 0.14s | Post-turn cleanup clears stale ownership before after-run work, and persistent startup records/heartbeats both initial and replacement pids before initialize |
| `env PYTHONPATH=src python -m pytest -q tests/test_orchestrator_phase_transition.py` | agent_detected | PASS — 34 passed in 0.23s | Full phase-transition lifecycle suite remains green |
| `env PYTHONPATH=src python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py -k 'turn_spawn or agent_pid or full_lifecycle or force_eject or next_turn_clears_stale_per_turn_agent_pid or completed_per_turn_pid_is_cleared_before_after_run_blocks or persistent_backend_pid_is_registered_before_each_initialize'` | agent_detected | PASS — 19 passed, 205 deselected in 0.22s | Spawn, initial/rebuilt registration, post-turn cleanup, recording, kill, missing-pid, and backend lifecycle behaviors remain green together |
| `python -m ruff check src/symphony/orchestrator/core.py tests/test_orchestrator_phase_transition.py`; `python -m ruff check src tests`; `git diff --check` | agent_detected | PASS | R-loop iteration 3 is focused/full lint- and whitespace-clean |
| `env PYTHONPATH=src python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_phase_transition.py tests/test_orchestrator_dispatch.py -k 'clear_backend_agent_pid_is_explicit or stop_failure_retains_old_backend_ownership or completed_per_turn_pid_is_cleared_from_run_registry or failed_per_turn_clears_pid_before_failed_final_stop or start_failure_records_late_pid_when_cleanup_is_unconfirmed or legacy_codex_app_server_pid or prefers_normalized_agent_pid'` | agent_detected | RED — 5 failed, 2 passed, 211 deselected in 3.16s; exit 1 | Persisted-clear, stop-confirmation, late-start, and compatibility controls before iteration-4 source changes; raw output in `qa/red-persisted-owner-lifecycle.txt` |
| same focused iteration-4 selector | agent_detected | PASS — 7 passed, 211 deselected in 1.01s | Explicit clear, lifecycle ownership, old-stop abort, and event-key compatibility are green together |
| `env PYTHONPATH=src python -m pytest -q tests/test_run_registry.py` | agent_detected | PASS — 13 passed in 4.13s | Explicit clear works while ordinary `heartbeat(None)` still preserves pid ownership |
| `env PYTHONPATH=src python -m pytest -q tests/test_orchestrator_phase_transition.py` | agent_detected | PASS — 38 passed in 8.34s | Full phase/start/turn/cleanup lifecycle module is green with AF-01 preserved |
| `env PYTHONPATH=src python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py tests/test_run_registry.py -k 'turn_spawn or agent_pid or full_lifecycle or force_eject or backend_agent_pid or phase_transition_stop_failure or start_failure_records_late_pid or failed_per_turn_clears_pid or completed_per_turn_pid or persistent_backend_pid'` | agent_detected | PASS — 26 passed, 224 deselected in 6.10s | Combined AF-02 spawn, persistence, compatibility, stop, cleanup, and eject contract |
| `python -m ruff check src tests`; `python -m pyright src/symphony/orchestrator/run_registry.py src/symphony/orchestrator/core.py`; `git diff --check` | agent_detected | PASS — Ruff clean; changed-source Pyright 0 errors; diff clean | Iteration-4 source/tests are lint-, type-, and whitespace-clean; full-repository exact gates remain verifier-owned |
| `env PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/test_orchestrator_phase_transition.py::test_replacement_stop_failure_stays_unconfirmed_after_old_final_stop` | agent_detected | PASS — 1 passed in 0.14s | A failed replacement cleanup retains pid `22222` when tuple assignment leaves the outer client pointing at the old backend |
| `env PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/test_orchestrator_phase_transition.py::test_phase_transition_stop_failure_retains_old_backend_ownership tests/test_orchestrator_phase_transition.py::test_phase_stop_failure_stays_unconfirmed_after_idempotent_final_stop` | agent_detected | PASS — 2 passed in 0.10s | Existing old-stop confirmation boundaries remain green |
| `env PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/test_orchestrator_phase_transition.py` | agent_detected | PASS — 40 passed in 0.20s | Full phase-transition lifecycle module is green after both stop-confirmation fixes |
| `env PYTHONPATH=src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py tests/test_run_registry.py -k 'turn_spawn or agent_pid or full_lifecycle or force_eject or backend_agent_pid or phase_transition_stop_failure or phase_stop_failure or replacement_stop_failure or start_failure_records_late_pid or failed_per_turn_clears_pid or completed_per_turn_pid or persistent_backend_pid'` | agent_detected | PASS — 28 passed, 224 deselected in 0.23s | Combined AF-02 spawn, persistence, stop-confirmation, cleanup, and eject contract |
| `python -m ruff check src/symphony/orchestrator/core.py tests/test_orchestrator_phase_transition.py`; `python -m ruff check src tests`; `git diff --check` | agent_detected | PASS | Iteration-5 source/tests are lint- and whitespace-clean |

## QA

Tool: CLI pytest/lint/type-check commands; no browser surface or database is involved.
DB: not used; AF-02 preserves the existing run-registry schema.

## Reproduction Fidelity

- Fidelity level: synthetic-representative
- Residual risk from data gap: process-group kill is spied in unit tests rather than sending SIGKILL to a real agent CLI process; backend contract tests exercise each adapter's live fake subprocess envelope.
- Post-deploy confirmation plan: verify a future real stalled non-Codex run emits `force_eject_killed_process_group` with the backend kind and that the recorded group no longer exists.

## Residual Risk

- Exact-verification status: complete. Iteration 6 cleared all 14 legacy-backend regressions; iteration 7 exact verification then passed the compatibility selector and the 1,312-test CI-equivalent coverage suite.
- Environment note: the earlier `.venv` tool mismatch is resolved. Exact verification used `/opt/anaconda3/bin/python` plus `PYTHONPATH=src`, so the isolated worktree source and all repository dependencies/tools were active.
- Stop-confirmation residual: a normal backend `stop()` exception keeps the last known pid and aborts a phase replacement because termination is unconfirmed. Process reaping and later reclaim/kill remain explicitly outside AF-02.
- Follow-up: AF-10 owns startup reclaim using the recorded pid/pgid; AF-01 owns late worker-exit identity checks.
