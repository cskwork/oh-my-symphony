# AF-03 — Resume after long pause instantly stall-cancels the healthy worker

Route: DEBUG | Severity: P1 | Confidence: CONFIRMED | Blocked by: none

## Defect

Reconcile skips stall detection while paused (`core.py:5336-5337`) but the
stall clock (`entry.last_progress_timestamp`, read at `:5360-5371`) is never
re-stamped on resume (`resume_worker`, `core.py:1498-1524`). A worker pauses
at the turn boundary (`core.py:3447-3475`) with its clock frozen at T0; any
pause longer than `stall_timeout` (default 300s,
`workflow/constants.py:92`) guarantees the first reconcile tick after resume
computes `elapsed > stall_timeout` and cancels the just-resumed healthy
worker before it can emit its first event — burning a retry attempt. Resume
is broken for exactly its main use case (long investigative holds).

## Fix direction

On `resume_worker`, give the worker a fresh stall window: either stamp
`entry.last_progress_timestamp = now(utc)`, or add `resumed_at` and make the
stall predicate use `max(last_progress_timestamp, resumed_at, started_at)`.
Prefer the explicit `resumed_at` field — it keeps the progress timestamp
semantically pure (only real progress events advance it).

## Acceptance checks

- [ ] RED first: pause a running entry, advance the clock past
  `stall_timeout`, resume, run `_reconcile_running` before any progress
  event; assert the worker is NOT cancelled — fails on current `main`.
- [ ] WHEN a worker is resumed THEN it has a full stall window before the
  stall predicate can fire.
- [ ] WHEN a worker genuinely stalls after resume THEN stall detection still
  fires once the fresh window elapses.
- [ ] Existing `test_reconcile_skips_stall_detection_for_paused_worker`
  (`tests/test_orchestrator_dispatch.py:2014`) stays green; full suite green.

## Non-goals

Pause interaction with already-cancelled zombies (AF-07); changing the
stall-timeout default.
