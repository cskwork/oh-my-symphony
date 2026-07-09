# AF-11 — Heartbeat scheduler: `max_turns` silent latch, lease busy-spin, idle-guard duration

Route: LEGACY | Severity: P2 | Confidence: CONFIRMED (latch) / PLAUSIBLE (others)
Blocked by: 07-07 P1-1 (soft — prefer implementing inside the extracted
`ImprovementScheduler`; do not grow core.py for this)

Three scheduler-semantics defects in the continuous-improvement cluster.

## Defect A — `max_turns` is a lifetime kill-switch that latches silently

`_improvement_turns_used` only ever increments (`core.py:2780`) and the due
check short-circuits forever once `>= max_turns` (`core.py:2691`). No reset
on interval rollover or config reload — only the manual web-API reset
(`core.py:2793-2795`). An operator who reads `max_turns` as a rate limit
gets a heartbeat that dies quietly after N runs, with only a status field
as evidence.

DECISION NEEDED (map: "Not yet specified"): per-window rate with auto-reset,
or documented lifetime cap. Either way, `log.warning` once when the cap
latches.

## Defect B — lease-held path re-arms to `now`, busy-spinning per tick

On lease-held, due-time resets to `time.monotonic()` (`core.py:2724-2731`) →
re-attempt (fs probe + supervised task spawn) every poll tick for as long as
a peer holds the lease. Back off by one interval instead.

## Defect C — `require_idle_board` is kickoff-only and ignores exit-in-flight

The idle guard (`core.py:2696`) snapshots `_running`/`_retry` at kickoff:
later ticks dispatch workers that overlap the (minutes-long) CI run, and a
ticket in `_terminal_persist_pending` (`core.py:4635`) doing exit-time git
work is invisible to the guard. CI's `git worktree add`/`remove --force` can
then race worker worktree ops on the shared repo. Re-assert the idle
predicate (including `_terminal_persist_pending`) inside the run and/or hold
a CI-active flag that dispatch honors while `require_idle_board` is set.

## Acceptance checks

- [ ] RED first (A): drive `_improvement_turns_used` to the cap; assert a
  warning is emitted at latch and behavior matches the decided semantics
  (auto-reset test if per-window is chosen).
- [ ] RED first (B): lease-held branch leaves next-due one full interval
  away (fails on current `main`).
- [ ] RED first (C): with `require_idle_board=True`, dispatch a worker while
  a CI run is in flight; assert they do not overlap (or CI aborts/postpones).
- [ ] Idle guard counts `_terminal_persist_pending`. Full suite green.

## Non-goals

The ImprovementScheduler extraction itself (07-07 P1-1); changing default
interval/timeout values; the improvement runner's internal logic.
