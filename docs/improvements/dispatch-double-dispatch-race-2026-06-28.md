# Double-dispatch race on `max_turns` exhaustion (2026-06-28)

Status: **FIXED** (approach A, see below) — verified by the live run-path smoke
(exactly one dispatch, one worker exit, zero `index.lock` collisions) plus a
regression test in `test_orchestrator_dispatch.py`.
Severity: non-fatal but real — a ticket that exhausts its per-attempt
`max_turns` ceiling can be dispatched a second time while its terminal-state
transition is still being persisted. The ticket still settles correctly in the
blocked state; the cost is a wasted worker, a noisy `git index.lock` collision,
and asyncio child-watcher reap noise.

## How it was found

A live run-path smoke (not a unit test): a real `symphony` service process
booted against a temp file-board with the `python -m symphony.mock_codex`
backend, `max_turns: 2`, `polling.interval_ms: 1000`, `max_retries: 0`. One
Todo ticket. The orchestrator dispatched it, ran two mock turns, hit the
per-attempt ceiling, and moved it to `Blocked` — but the structured log showed
the ticket dispatched **twice**.

Reproduction config lives in scratch only (mock backend + the WORKFLOW above);
the trigger is any workflow where a worker exhausts `max_turns` per attempt
without a terminal transition while the poll interval is short relative to the
tracker-write latency.

### Live trace (abridged, single ticket `TASK-1`)

```
worker_max_turns_exhausted turns=2 max_turns=2
worker_exit_pop reason=normal popped=true running_keys_after_pop=[]   # (1) left _running
stale_claimed_pruned ids=["TASK-1"]                                   # (2) _claimed lock pruned
dispatch issue_id=TASK-1 attempt=null agent_kind=codex                # (3) RE-DISPATCH (the bug)
budget_exhausted_persisted target_state=Blocked budget_kind=max_turns # (4) Blocked persisted — too late
...
auto_commit_failed ... '.git/index.lock': File exists. Another git process ...  # 2 commits collide
Unknown child process pid 19738, will report returncode 255          # asyncio watcher reap race
```

## Root cause

`Orchestrator._on_worker_exit` handles per-attempt `max_turns` exhaustion in the
`elif entry.hit_max_turns:` branch (`orchestrator/core.py` ~2689):

```python
elif entry.hit_max_turns:
    self._claimed.add(issue_id)                       # in-tick lock
    target_state = _max_turns_exhausted_target_state(cfg)
    if cfg is not None and target_state:
        persisted = await self._persist_budget_exhausted_state(...)  # <-- awaits two to_thread tracker writes
```

Two in-memory guards exist, and **both are pruned every tick** by
`_on_tick` (~774) against `in_flight_ids = set(self._running) | set(self._retry)`:

```python
stale_claimed       = self._claimed            - in_flight_ids   # ~759
stale_turn_budget   = self._turn_budget_exhausted - in_flight_ids  # ~774
```

The design intent (the comment at ~748) is that these sets are only *in-tick*
locks; the durable guard against re-dispatch is the ticket's **tracker state**
(`_eligible` returns False when the state is terminal / not active, ~986).

The race is a classic check-then-act across an `await`:

1. The worker is popped from `_running` upstream in the exit path **(1)** —
   so the ticket is no longer "in flight".
2. The `hit_max_turns` branch adds it to `_claimed`, then `await`s the async
   persist of `budget_exhausted_state` (two `to_thread` tracker calls). The
   `await` yields the event loop.
3. A concurrent poll tick runs during that yield. It prunes `_claimed`
   **(2)** because the ticket is not in `in_flight_ids`, then fetches
   candidates — and the tracker still reports the ticket **active** (the
   persist from step 2 has not completed). `_eligible` says yes →
   **re-dispatch (3)**.
4. The persist finishes **(4)**, moving the ticket to `Blocked` — but a second
   worker is already running on the same workspace, so the two auto-commits
   collide on `.git/index.lock`.

Note the asymmetry: the token-budget and total-turn branches
(`_turn_budget_exhausted.add(...)` at ~2558/2587/2596) populate the budget set;
the per-attempt `max_turns` branch does not. But because `_turn_budget_exhausted`
is *also* pruned by `- in_flight_ids`, simply mirroring that `.add()` here does
**not** by itself close the window — the prune removes it on the same tick. The
real fix has to keep the ticket protected until the terminal state is durably
written.

The `Unknown child process pid …` line is asyncio's own child watcher (not
Symphony code) observing a PID that `safe_proc_wait` already reaped via
`os.waitpid` — the reap-then-notify ordering documented in CPython #127049. It
is benign here but is the same family of race.

## Fix options (pick one before implementing — race-critical code)

**A. Persist before releasing the slot (preferred, most correct).**
Reorder the exit path so the terminal-state write completes *before* the worker
is popped from `_running` (or hold a placeholder in `_running`/`_retry` until
the persist returns). The ticket stays "in flight" across the `await`, so the
prune can't strip its lock and `_eligible` never sees it as a fresh candidate.
Risk: touches the pop/cleanup ordering, which is the most regression-prone part
of the exit path; needs the lifecycle E2E plus a new race test.

**B. Dedicated "terminal-pending" guard exempt from the in-flight prune.**
Add the id to a set that `_eligible` checks and that is cleared only after the
persist completes (success or failure), and exclude that set from the
`- in_flight_ids` prune. Smaller blast radius than A, but adds a fourth piece of
dispatch state to keep coherent.

**C. Make exhaustion → terminal-state synchronous-enough.**
Set `entry.issue.state` to the target locally and add to `_claimed`/budget set
*before* the `await`, and have the prune skip ids whose local state is already
terminal. Cheapest, but relies on the local state copy staying authoritative
until the tracker catches up.

A regression test should drive `_on_tick`/`_on_worker_exit` with a tracker whose
`update_state` blocks on an event, assert that a tick fired during that block
does **not** re-dispatch the ticket, then release and assert a single terminal
transition.

## Fix applied (approach A)

Implemented test-first. `_on_worker_exit` was split into a thin wrapper plus
`_on_worker_exit_impl`: the wrapper adds the issue id to a new
`_terminal_persist_pending` set on entry and clears it in a `finally`. That set
is unioned into `in_flight_ids` (so the G1 prune no longer strips the in-tick
`_claimed` lock mid-exit) and is checked in `_eligible`. The ticket therefore
stays ineligible for the whole exit handler — across both the auto-commit
`await` and the async budget persist — until its terminal state is durable.

Note the false start, kept here as a warning: a first attempt guarded only
inside `_persist_budget_exhausted_state`. The unit test passed, but the live
run-path smoke still showed the double dispatch — the auto-commit `await`
(earlier than the persist) was still an open window. Only moving the guard to
span the entire `_on_worker_exit` closed it. This is exactly why the run-path
smoke, not just the unit test, is the acceptance gate for dispatch-path fixes.

Verified: the live smoke now logs exactly one dispatch / one worker exit / zero
`index.lock` collisions; the regression test in `test_orchestrator_dispatch.py`
passes; the full suite stays green.
