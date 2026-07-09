# AF-01 — Identity-safe worker exit path

Route: DEBUG | Severity: P0 | Confidence: CONFIRMED | Blocked by: none
Status: DONE 2026-07-10 — shipped on dev (branch debug/af-01-identity-safe-worker-exit); RED-first race tests + full suite 1286 passed / 2 skipped (baseline 1279 + 7 new)
Unblocks: cleaner test scaffolding for AF-02, AF-07, AF-08

This is the root cause of the residual `finished_without_cleanup` /
`worker_running_entry_vanished` incidents (07-07 plan P2-2; comment at the
orphan path names OLV-002 as the observed victim).

## Defect

A force-ejected zombie worker's `finally` block operates on whatever entry
currently sits under its issue id — including a **fresh replacement entry**
installed by the backoff retry — because neither the `finally` nor the
`_running` pop verifies task identity:

- `src/symphony/orchestrator/core.py:3915-3920` — worker `finally`:
  `self._running.get(running_issue_id)` → sets `exit_started_at` on that
  entry → `await asyncio.shield(self._on_worker_exit(...))`. Keyed by id only.
- `src/symphony/orchestrator/core.py:4654` — `_on_worker_exit_impl`:
  `self._running.pop(issue_id, None)`. Keyed by id only.
- `src/symphony/orchestrator/core.py:4990-4993` — `_force_eject_zombie`
  docstring asserts "`_on_worker_exit` is a no-op on a missing entry, so this
  is race-safe" — false once a retry re-installs a fresh entry under the key.
- `src/symphony/orchestrator/dispatch_state.py:79` — `entry_owned_by` exists
  for exactly this, but is applied only in the done-callback
  (`core.py:3281`), and even there the actual pop runs later in a different
  task (TOCTOU).

## Failure interleaving

1. Worker A (issue X) stalls → reconcile cancels, stamps `cancelled_at`.
2. A is wedged in an await that ignores cancellation; after the 30s grace,
   `_force_eject_zombie` pops A's entry and schedules a retry. For non-codex
   backends nothing is killed (see AF-02), so A stays alive.
3. Retry fires → `_dispatch(X)` installs fresh entry B; worker B runs.
4. A finally unblocks → its `finally` reads entry **B**, stamps
   `exit_started_at` on B, and `_on_worker_exit_impl` pops **B** — ejecting
   the live worker, finishing B's lease, possibly queueing another retry.
5. B hits `self._running.get(X)` → `None` → orphan path
   (`core.py:3354-3368`), slot mis-accounted, cascade stops.

Secondary defect in the same seam: `_on_worker_task_done` early-returns on
`entry is None` **before** retrieving `task.exception()`
(`core.py:3281-3282` vs `:3297-3298`). If `_on_worker_exit_impl` raises after
its pop, the worker task ends errored and the exception is never retrieved —
"Task exception was never retrieved" with no structured log.

## Fix direction

Make the pop the single identity-checked mutation point:

1. In the worker `finally` (`core.py:3915`), gate on
   `entry_owned_by(running_issue_id, asyncio.current_task())` before touching
   `exit_started_at` or calling `_on_worker_exit`; pass the owning task down.
2. In `_on_worker_exit_impl` (`core.py:4654`), pop only when
   `_running.get(issue_id).worker_task is owning_task` (reuse
   `entry_owned_by`); otherwise log `worker_exit_stale_task` and return.
3. In `_on_worker_task_done`, retrieve and log `task.exception()` (guarding
   `CancelledError`) before the `entry is None` early return.
4. Correct the `_force_eject_zombie` docstring.

## Acceptance checks

- [x] RED first: new test in `tests/test_orchestrator_dispatch.py` — a real
  awaitable zombie task in `_running` is force-ejected, the retry installs a
  fresh entry under the same id, then the zombie's await resolves; assert the
  fresh entry is untouched, still in `_running`, and its worker uninterrupted.
  This test MUST fail on current `main`.
- [x] WHEN a stale worker's `finally` runs after re-dispatch THEN the live
  entry's `exit_started_at` stays `None` and no `_on_worker_exit` side effects
  (lease finish, retry schedule, `_completed` add) apply to the live run.
- [x] WHEN `_on_worker_exit_impl` raises after the pop THEN the done-callback
  logs the exception (no "Task exception was never retrieved" warning).
- [x] Existing suite green: `python -m pytest -q` (1279 baseline), especially
  `test_orchestrator_dispatch.py`, `test_orchestrator_reconcile.py`,
  `test_agent_lifecycle_e2e.py`.

## Non-goals

Killing the zombie's OS process (AF-02); reconcile Part A isolation (AF-07);
any change to the two-stage eject timing.
