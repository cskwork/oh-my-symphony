# OpenCode false-stall loop — fix plan (handoff)

Date: 2026-07-03. Branch: dev @ 5146b2a. Version at time of writing: 0.9.0.
Status: PLANNED — no code changed yet. This document is self-sufficient for a
fresh session to implement, review, and verify the fix.

## TL;DR

The opencode backend emits **zero events while a turn is running** (one
subprocess per turn, stdout read in one shot at exit). The orchestrator's
stall detector is event-driven, so any opencode turn longer than
`stall_timeout_ms` (default 300 000 ms) is indistinguishable from a wedged
worker and gets cancelled mid-work. The cancelled worker exits with
`reason=normal` (by design — see "Do not change"), which resets retry
accounting and schedules an immediate continuation, producing a tight
dispatch → turn → stall-kill → re-dispatch loop every ~5.4 minutes.

Fix: the opencode backend emits a periodic liveness heartbeat event while its
per-turn subprocess is alive, and its `is_progress_event` accepts exactly
that heartbeat. A live subprocess IS progress for a per-turn CLI; a genuinely
hung process is still bounded by `turn_timeout_ms` (default 3 600 000 ms) →
`TurnTimeout` → the existing backoff-retry path.

## Symptom and evidence

Observed on ticket SMA-32 (agent_kind=opencode), `log/symphony.log` lines
773–820 — three identical cycles:

| ts (UTC) | log line | event |
|---|---|---|
| 03:15:1x | 773 | `dispatch` attempt=null |
| 03:15:1x | 779 | `worker_turn_started` turn=1 |
| 03:20:40 | 780 | `stalled_session` elapsed_ms=322865 |
| 03:20:41 | 783 | `worker_finally_entered` outcome=normal error=null |
| 03:20:42 | 787–788 | `auto_commit_completed` (real work: 17 files, 1705 insertions), `worker_exit` reason=normal |
| 03:20:43 | 789 | `dispatch` attempt=1 — loop repeats |

Cycle 2 (795–804): `stalled_session` elapsed_ms=320265, auto-commit 3 files.
Cycle 3 (805–820) was cut short by service shutdown. Each cycle's
`after_create` hook resets the worktree to the previous cycle's auto-commit
(`HEAD is now at eec86ea` → `1c43801` → `8d9a...`), so work accumulates but
the agent restarts with fresh context every ~5 minutes and any phase that
needs one uninterrupted turn > 5 min can never finish. Every turn in the
5 min–60 min band is falsely killed.

## Root cause

Two mechanisms combine; only the first is a defect.

### A. opencode has no mid-turn progress signal (the defect)

- `src/symphony/backends/opencode.py:128-208` — `run_turn` spawns one
  subprocess per turn and awaits
  `asyncio.gather(stdout.read(), stderr.read(), safe_proc_wait(proc))` under
  `asyncio.wait_for(..., timeout=turn_timeout_ms)`. Events are emitted only
  at boundaries: `EVENT_SESSION_STARTED` (dispatch), `EVENT_TURN_FAILED` /
  `EVENT_TURN_COMPLETED` (exit). Nothing during the turn.
- `src/symphony/orchestrator/core.py:3792-3844` — `_reconcile_running`
  Part A computes `seen = last_progress_timestamp or last_codex_timestamp or
  started_at` and cancels the worker when `now - seen > stall_timeout_ms`.
  Both timestamps only advance inside `_on_codex_event` (core.py:2847),
  i.e. only when the backend emits an event.
- `src/symphony/backends/opencode.py:67-69` — `is_progress_event` returns
  `False` unconditionally (moot today: no OTHER_MESSAGE frames are emitted,
  but it matters once heartbeats exist).

So after `session_started`, an opencode entry's stall clock never resets
until the turn ends. elapsed_ms ≈ 322 865 ≈ stall_timeout (300 000) + poll
tick, exactly matching the log.

### B. stall-cancel exits as `reason=normal` (by design — do not change)

- `core.py:2075` — worker `outcome: str = "normal"` initial value.
- `core.py:2574-2578` — the worker's except ladder catches `SymphonyError`
  and `Exception` only. `asyncio.CancelledError` is a `BaseException`, so a
  stall-cancel (`worker_task.cancel()`, core.py:3842-3844) bypasses both
  handlers; the `finally` (core.py:2595) reports the initial
  `outcome="normal"` and shields `_on_worker_exit(id, "normal", None)`.
- `core.py:3266` — the `reason == "normal"` branch clears persisted retry
  attempts, runs the auto-commit snapshot, and (non-terminal state, no
  max-turns hit) schedules a continuation via
  `_schedule_retry(attempt=1, delay_ms=CONTINUATION_RETRY_DELAY_MS (=1s), kind="continuation")`
  (core.py:3409-3416). Hence "attempt=1, 1 s later" forever, with no backoff
  escalation.

**Why B stays as-is:** the `reason == "normal"` branch is load-bearing for
cancellation paths. The token-budget cancel (core.py:2911) relies on the
cancelled worker exiting "normal" so that the `entry.hit_token_budget`
handling inside that branch runs (`_persist_budget_exhausted_state`). The
auto-commit snapshot — which saved 17 files of real work here — also only
runs in that branch. Reclassifying CancelledError as its own outcome would
break both, plus pause/shutdown/phase-transition cancels. Loop escalation is
already bounded by `max_total_turns` accounting (`debug.completed_turn_count`
accumulates across runs → `worker_total_turn_budget_exhausted`). Fixing A
removes the false trigger; genuine stalls keep the existing
cancel-then-fresh-continuation recovery model (phase restarts are Symphony's
intended recovery: markdown is the source of truth).

## Fix design: subprocess-liveness heartbeat

All changes in `src/symphony/backends/opencode.py`; no orchestrator changes.

1. Import `EVENT_OTHER_MESSAGE` from `. ` (backends `__init__`).
2. Module constant next to `MAX_LINE_BYTES`:

   ```python
   HEARTBEAT_INTERVAL_S = 30.0
   ```

   30 s gives ~10 beats per default stall window; no new config knob
   (avoid speculative configurability).
3. New private coroutine on `OpenCodeBackend`:

   ```python
   async def _emit_heartbeats(self, proc: asyncio.subprocess.Process) -> None:
       # Liveness beacon: opencode surfaces no JSON until the per-turn
       # subprocess exits, so without this the orchestrator's stall
       # detector sees silence for the whole turn and cancels healthy
       # long turns (SMA-32, 2026-07-03). An alive subprocess IS
       # progress for a per-turn CLI; a hung process is still bounded
       # by turn_timeout_ms in run_turn.
       while proc.returncode is None:
           await asyncio.sleep(HEARTBEAT_INTERVAL_S)
           if proc.returncode is not None:
               return
           await self._emit(
               EVENT_OTHER_MESSAGE,
               {"type": "opencode_heartbeat", "pid": proc.pid},
           )
   ```

   Note `_emit` (opencode.py:305-318) already swallows callback exceptions,
   so the heartbeat task cannot die on observer errors. Read
   `HEARTBEAT_INTERVAL_S` via the module global (not captured at def time)
   so tests can monkeypatch it.
4. In `run_turn`, right after `self._active_proc = proc` (opencode.py:152):

   ```python
   heartbeat_task = asyncio.create_task(self._emit_heartbeats(proc))
   ```

   and in the existing `finally` (opencode.py:207-208), before
   `self._active_proc = None`:

   ```python
   heartbeat_task.cancel()
   ```

   Plain `.cancel()` without await is sufficient: the coroutine only sleeps
   and emits; a cancelled task's CancelledError is not an "unretrieved
   exception". Do NOT await it inside `finally` — `run_turn` itself is
   cancelled by stall/shutdown paths and an `await` in `finally` during task
   cancellation is an avoidable edge.
5. Replace the always-False `is_progress_event` (opencode.py:67-69):

   ```python
   def is_progress_event(self, event: dict[str, Any]) -> bool:
       # Subprocess liveness (the run_turn heartbeat) is the only
       # mid-turn progress signal opencode has. Anything else stays
       # conservative-False (IB-006 lesson: never let echo/meta frames
       # reset the stall clock).
       return event.get("type") == "opencode_heartbeat"
   ```

### Side-effect audit (verified against current code)

`_on_codex_event` (core.py:2847) receives each heartbeat and:

- updates `entry.last_codex_timestamp` (intended) and, because
  `is_progress_event` returns True at the delegation point
  (core.py:2944-2948), `entry.last_progress_timestamp` + run-lease heartbeat
  (core.py:2951-2957) — this is the fix.
- `entry.last_codex_event` becomes `"other_message"` during long turns —
  cosmetic, arguably informative.
- `_preview_from_payload` returns `""` for
  `{"type": "opencode_heartbeat", "pid": N}` (no text/message/item keys), so
  the TUI preview (`last_codex_message`) is NOT overwritten.
- `usage` attached by `_emit` is `_latest_usage`, unchanged mid-turn →
  `delta_out == 0` → no token accounting effects.
- `agent_pid` in the envelope sets `entry.codex_app_server_pid` to the live
  opencode child pid — a bonus: `_force_eject_zombie`'s
  `kill_process_group` gains a real target for opencode.

### Rejected alternatives

- **Orchestrator-side liveness probe** (skip stall check when
  `entry.client.pid` is alive): touches the shared stall path for all
  backends, risks the claude/codex invariants pinned by
  `tests/test_orchestrator_dispatch.py` (OLV-002 / IB-006 regressions), and
  loses the run-lease heartbeat refresh the event path gives for free.
- **Classify CancelledError in the worker** (`outcome="cancelled"`): breaks
  the token-budget and auto-commit-on-cancel behavior documented in Root
  cause B.

## TDD plan (write these first, watch them fail)

In `tests/test_backends.py`, reusing `_make_cfg` / `_FakeSubprocess` (line
189) / `_noop_event` (line 132) patterns:

1. `test_opencode_backend_is_progress_event_accepts_only_heartbeat` — mirror
   of the gemini test at line 1907: `{"type": "opencode_heartbeat"}` → True;
   `{"type": "assistant"}`, `{}` → False. (Fails now: always False.)
2. `test_opencode_emits_heartbeats_while_turn_subprocess_runs` — async test:
   - collector `async def record(ev): events.append(ev)` as `on_event`.
   - `proc = _FakeSubprocess(stdout_blob=b"plain answer")`;
     `proc.returncode = None` to simulate an in-flight turn.
   - inline doubles (do not reuse `_install_subprocess_double`, whose
     `safe_proc_wait` returns immediately): `fake_create_subprocess_exec`
     returns `proc`; `fake_safe_proc_wait` sleeps ~0.15 s, sets
     `proc.returncode = 0`, returns 0. Monkeypatch both on
     `opencode_module`, plus `HEARTBEAT_INTERVAL_S = 0.03`.
   - run `start_session` + `run_turn`; assert at least one event with
     `event == "other_message"` and `payload["type"] == "opencode_heartbeat"`,
     assert the turn still completes (`status == "turn_completed"`), and
     assert no heartbeat-only regression in existing fields
     (`agent_pid == 98765`).
3. Existing opencode tests (lines 613, 666) must stay green — their fakes
   have `returncode=0` from the start, so the heartbeat loop exits before
   the first sleep.

Optional (cheap, high value): one orchestrator-level test in
`tests/test_orchestrator_dispatch.py` next to
`test_reconcile_stalls_on_progress_timestamp_not_codex_timestamp` (line 828)
asserting that an `other_message` event whose entry's `client` stubs
`is_progress_event → True` advances `last_progress_timestamp` — the wiring
at core.py:2944-2948 is already covered for claude/codex; add only if not.
Mind the module-level-stub leak gotcha: always use `monkeypatch.setattr`,
never bare module attribute assignment.

## Verification checklist

1. `pytest tests/test_backends.py -x -q` then the full suite
   (`pytest -q`); `ruff check src tests` and `black --check` if configured.
2. Prompt-anchor contract untouched — this change must not touch any prompt
   template (`tests/test_workflow_pipeline_prompt.py` stays green
   trivially).
3. **Run-path verification (required, not optional):** launch via the real
   launcher against a file board with `agent: {kind: opencode}` and a
   temporarily low `stall_timeout_ms` (e.g. 60 000) plus a prompt that
   forces a long turn. Confirm in `log/symphony.log`: heartbeat-driven
   absence of `stalled_session`, exactly one `dispatch` per phase, and
   `worker_exit reason=normal` only after real completion. Tests passing in
   a different env ≠ launcher works.
4. Re-run SMA-32: move it out of Blocked, start the service, confirm the
   ticket progresses past the previous 5-minute ceiling.

## Versioning and commits

- Patch bump (fix restores intended behavior): 0.9.0 → 0.9.1 in
  `pyproject.toml` AND `src/symphony/__init__.py` in lockstep, as its own
  `chore` commit on top of the fix commit.
- Commit sequence on `dev` (never directly on main; merge dev → main after):
  1. `fix: emit opencode liveness heartbeats so long turns do not trip stall detection`
  2. `chore: bump version to 0.9.1`
- Add a `docs/changelog/changelog-2026-07-03.md` entry recording the
  decision and the rejected alternatives above.

## Out of scope / follow-ups

- **gemini backend has the same bug class**: batch CLI + always-False
  `is_progress_event` (src/symphony/backends/gemini.py:56-62, pinned by
  `test_gemini_backend_is_progress_event_is_always_false`) + no mid-turn
  events. Apply the same heartbeat recipe if/when gemini gets real use.
- codex/gemini/pi still contain raw `proc.wait()` calls (child-reaper hang
  risk under Textual + 3.12 on macOS); use `_shell.safe_proc_wait`.
- SMA-31 showed a transient `auto_commit_failed` on `index.lock` ("another
  git process") at 2026-07-03T00:29:11Z — worktree git contention between a
  cancelled worker's child and the auto-commit path; separate investigation.
- SMA-32 is currently parked in Blocked; unblock it to re-test after the
  fix lands.
