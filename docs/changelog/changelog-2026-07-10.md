# Changelog — 2026-07-10

## AF-01 — Identity-safe worker exit path (P0, DEBUG)

Ticket: docs/improvements/tickets/2026-07-09/AF-01-identity-safe-worker-exit.md
Branch: debug/af-01-identity-safe-worker-exit → dev

### What

A force-ejected zombie worker's cleanup path could eject the fresh replacement entry the backoff
retry installed under the same issue id, because neither the worker `finally` nor the `_running`
pop in `_on_worker_exit_impl` verified task identity. This was the root cause of the residual
`finished_without_cleanup` / `worker_running_entry_vanished` incidents (OLV-002).

Changes (src):

- `dispatch_state.py` — new `DispatchState.entry_foreign_to(issue_id, task)`: True only when a
  running entry exists AND its `worker_task` is populated AND disagrees with `task`. The single
  identity predicate both exit-path gates reuse.
- `core.py` worker `finally` — computes `owning_task = asyncio.current_task()`; a foreign or
  missing entry skips `exit_started_at` stamping and the `_on_worker_exit` call entirely
  (`worker_finally_stale_entry` warning on foreign); the owning task is passed down.
- `core.py` `_on_worker_exit` / `_on_worker_exit_impl` — keyword-only `owning_task` threaded
  through; when provided, a missing or foreign entry logs `worker_exit_stale_task` and returns
  BEFORE any mutation (including the `_claim_released_at` / `_pause_events` pops that previously
  ran ahead of the entry-None check). This closes the TOCTOU across the `asyncio.shield` yield
  between the finally's check and the pop.
- `core.py` `_on_worker_task_done` — retrieves `task.exception()` (CancelledError-guarded) BEFORE
  the entry-identity early return; a post-pop exception now logs
  `worker_task_errored_after_cleanup` instead of asyncio's unstructured "Task exception was never
  retrieved". Passes `owning_task=task` into the exit it spawns.
- `core.py` `_force_eject_zombie` docstring — the "no-op on a missing entry, so race-safe" claim
  was false once a retry re-installs a fresh entry; now documents the identity-gate contract.

Tests: 7 new in `tests/test_orchestrator_dispatch.py` (RED-first: the two core race tests failed
verbatim on unmodified source). Full suite 1286 passed / 2 skipped (baseline 1279 + 7 new).

### Why (decisions and rejected alternatives)

- **`entry.worker_task is None` counts as OWNED, not foreign.** Strict `entry_owned_by` semantics
  in the exit path (rejected) broke 6+ existing tests that drive `_run_agent_attempt` /
  `_on_worker_exit_impl` directly against hand-installed entries that never went through
  `_dispatch`. Only a populated `worker_task` that disagrees is a genuine identity conflict; in
  production, `_dispatch` always binds `worker_task` before the worker coroutine's first slice.
- **`owning_task=None` callers skip the check.** Legacy/internal call sites and tests keep
  pre-AF-01 behavior; only the two real exit paths (worker `finally`, done-callback) pass it.
- **Identity checks over locks/generation counters (rejected).** asyncio's single-thread
  cooperative scheduling means a re-check at each mutation point suffices; the only yield between
  the finally's gate and the pop is the `asyncio.shield` boundary, which the impl-side re-check
  covers.
- **Missing entry now skips `_on_worker_exit` from the finally (behavior change, deliberate).**
  Force-eject already finishes the lease, pops the pause event, and schedules the retry; letting
  the stale finally run the exit handler against an absent/replaced entry is exactly the defect.
  Matches the ticket's `entry_owned_by` gate direction.
- **No version bump here.** Restoring intended behavior would be a patch-level bump per repo
  convention; left for a separate `chore(release)` commit when the AF batch ships.

Non-goals honored: no OS-process kill (AF-02), no reconcile Part A isolation (AF-07), no change to
two-stage eject timing.

Process note: build by local subagent; full-spec/edge-case improve passes and the adversarial
review ran on Codex CLI (user-directed); exact verification (full suite in the worktree run path,
`PYTHONPATH=<worktree>/src`) by the conductor.
