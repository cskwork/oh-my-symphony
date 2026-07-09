# Correction iteration 3 - confirmed adversarial reliability findings

Date: 2026-07-10
Scope: AF-06, AF-08, AF-09, AF-10 only

## Decision

Four confirmed lifecycle gaps were corrected without changing the frozen
`GOAL.md`, `PLAN.md`, `QA.md`, or `run-state.json`:

- AF-06 cleanup now owns only marker-named current temps and parseable legacy
  Symphony ticket temps; arbitrary operator `.tmp-*` files survive startup.
- AF-10 recovery uses a persisted `reclaiming` fence, performs process-group
  cleanup outside SQLite, and finalizes `orphaned` only after cleanup returns.
- AF-08 bounded shutdown finalizes every cancellation-resistant worker lease
  before clearing live ownership and closing the run registry.
- AF-09 a failed corrupt-stream reap remains retryable by later `stop()`.

## RED evidence

Initial regressions:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py::test_stale_temp_sweep_preserves_operator_owned_tmp_files tests/test_run_registry.py::test_run_registry_reclaims_dead_owner_lease_before_ttl tests/test_run_registry.py::test_run_registry_retries_interrupted_reclaim_after_reopen tests/test_orchestrator_dispatch.py::test_startup_reclaim_kills_recorded_orphan_agent_before_return tests/test_dispatch_state.py::test_stop_bounds_cancellation_resistant_worker_drain tests/test_backends.py::test_codex_corrupt_stream_failed_reap_is_retried_by_stop
```

Result: `5 failed, 1 passed in 1.48s`.

- AF-06 deleted `.tmp-operator-notes`.
- AF-10 became `orphaned` and redispatchable before external cleanup, and had
  no retry-safe finalize phase.
- AF-08 left the resistant worker's row `active` after `stop()` returned.
- AF-09 later `stop()` did not make a second process-reap attempt.
- The first AF-10 order probe passed only because the pre-fix best-effort
  boundary caught its in-callback assertion. The final regression records
  in-callback state and contender results, then asserts them after recovery.

Legacy selective-sweep refinement:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py::test_stale_tracker_temp_is_swept_on_startup_with_warning tests/test_tracker_file.py::test_stale_temp_sweep_preserves_operator_owned_tmp_files
```

Result before the content-validation fix: `1 failed, 1 passed in 0.15s`; the
parseable legacy `.tmp-legacy-ticket.md` remained on disk.

## GREEN evidence

Focused AF-06 selective cleanup:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py::test_stale_tracker_temp_is_swept_on_startup_with_warning tests/test_tracker_file.py::test_stale_temp_sweep_preserves_operator_owned_tmp_files tests/test_tracker_file.py::test_legacy_temp_files_are_ignored_by_every_board_read tests/test_tracker_file.py::test_atomic_write_temp_does_not_match_board_glob
```

Result: `4 passed in 0.08s`.

Full changed-module regressions:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_tracker_file.py tests/test_run_registry.py tests/test_dispatch_state.py tests/test_backends.py tests/test_orchestrator_dispatch.py
```

Result: `360 passed in 18.17s`.

Neighbor lifecycle/API regressions:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pytest -q tests/test_backends_lifecycle.py tests/test_service.py tests/test_webapi.py
```

Result: `62 passed in 1.55s`.

Static checks:

```console
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev ruff check src tests
env UV_CACHE_DIR=/private/tmp/symphony-af-03-16-uv-cache uv run --extra dev pyright src
```

Results: `All checks passed!`; `0 errors, 0 warnings, 0 informations`.

`git diff --check` is recorded after the final documentation edit.

## Rejected alternatives

- A broad or random-looking `.tmp-*` name heuristic: operator filenames can
  collide with tempfile output, so only the new ownership marker or validated
  legacy ticket content is deletable.
- Marking a dead-owner row `orphaned` before signalling: that opens a same-
  worktree redispatch window while the old process group can still run.
- Killing while holding `BEGIN IMMEDIATE`: OS teardown can block or fail and
  must not extend the SQLite write transaction.
- Clearing resistant workers without finishing their rows: clean shutdown
  would leave stale active leases despite having discarded in-memory owners.
- Treating backend closure as completed process cleanup: a first reaper failure
  must not make the process unreachable to later shutdown.
