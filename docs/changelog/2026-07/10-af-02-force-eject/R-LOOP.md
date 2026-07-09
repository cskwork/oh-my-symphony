# R-LOOP - verifier -> implementer loop channel

Never edit older sections; never delete this file.

## 2026-07-09T19:35 iteration 1

- [ ] GOAL criterion 5 (new): expected each per-turn child pid to replace the entry at spawn; actual backends emit `agent_pid` only on a later event, so a second turn that hangs before output leaves the first turn's pid recorded - evidence: edge-case review of `PerTurnCliBackend.run_turn`, `ClaudeCodeBackend.run_turn`, and `PiBackend.run_turn` versus their `_emit` call sites.
- [ ] Test/docs consistency: `tests/test_backend_contract.py` now contains a persistent Codex live-event contract but its module docstring says Codex only appears in the protocol check.
Regression: none; previous focused GREENs remain valid but do not exercise immediate per-turn replacement.
Next: add one normalized pid-bearing turn-spawn event emitted immediately after `_active_proc` publication in the three per-turn implementation families; prove two successive turns publish distinct pids and the orchestrator replaces `entry.agent_pgid`; update the contract docstring. Keep Codex persistent lifecycle, AF-01, AF-10, and reaping unchanged.

## 2026-07-09T19:42 iteration 2

- [x] Stale pre-spawn ownership: before a second same-phase turn, a prior per-turn pid (`11111`) remained on `RunningEntry` until the new child emitted its spawn event. RED evidence: `qa/red-stale-per-turn-agent-pid.txt` (`[None, 11111]` instead of `[None, None]`).
- [x] Fix: immediately before each `run_turn`, refresh `entry.agent_pgid` from `client.pid`. Per-turn clients expose `None` between children; persistent Codex retains its live process-group leader.
- [x] Regression: focused two-turn test and same-phase neighbor passed (2 passed); combined AF-02 selector passed 17 tests; focused Ruff and `git diff --check` passed.
Next: mandatory no-edit adversarial review, then exact verification. Preserve the turn-spawn event: it replaces the cleared value as soon as a per-turn child actually exists.

## 2026-07-09T19:52 iteration 3

- [x] Post-turn stale ownership: a per-turn backend could publish pid `11111`, finish and clear its child, yet leave `RunningEntry.agent_pgid` set while `after_run_best_effort` or state refresh blocked. RED evidence: `qa/red-lifecycle-pid-sync.txt` observed `11111` during the blocked after-run hook.
- [x] Persistent startup gap: initial and phase-rebuilt persistent backends did not copy their pid after `start()`, so force-eject during `initialize()` or `start_session()` lacked the live process group. RED evidence: `qa/red-lifecycle-pid-sync.txt` observed `[None, 11111]` instead of `[11111, 22222]`, with no explicit pid heartbeats.
- [x] Fix: synchronize `agent_pgid` from `client.pid` in the `run_turn` finally path; copy and heartbeat a non-null persistent pid immediately after every successful initial/rebuild `start()`; clear old phase ownership before the replacement spawn.
- [x] Regression: both lifecycle regressions passed; the full phase-transition module passed 34 tests; the combined AF-02 selector passed 19 tests; focused/full Ruff and `git diff --check` passed.
Next: repeat the mandatory no-edit adversarial review on the latest diff, then exact verification. Do not expand into reaping, exit identity, registry schema, or startup reclaim.

## 2026-07-09T20:10 iteration 4

- [x] Persisted stale ownership: `heartbeat(None)` intentionally preserved `backend_agent_pid`, so clearing only `RunningEntry.agent_pgid` left service-force and restart-visible ownership stale. RED evidence: `qa/red-persisted-owner-lifecycle.txt` retained `11111` after success and `22222` after a failed turn.
- [x] Stop-confirmation boundary: a failed old phase `stop()` was logged and ignored, then its PGID was erased and a replacement backend started. RED evidence: the focused phase-stop test did not raise and constructed a replacement.
- [x] Late-start boundary: a persistent backend could expose pid `33333` and fail before `start()` returned; the pid was neither recorded in memory nor persisted before cleanup. RED evidence: the late-start test observed `None` after unconfirmed cleanup.
- [x] Fix: add explicit `RunRegistry.clear_backend_agent_pid` while preserving `heartbeat(None)`; route live/non-live ownership through one orchestrator helper at start, turn, successful phase teardown, and confirmed cleanup boundaries. A failed old stop now logs and re-raises; failed cleanup retains the last confirmed live pid.
- [x] Compatibility: focused legacy-only `codex_app_server_pid` ingestion and normalized `agent_pid` precedence tests pass.
- [x] Regression: focused GREEN 7 passed; RunRegistry 13 passed; phase-transition 38 passed; combined AF-02 selector 26 passed; full Ruff passed; changed-source Pyright reported 0 errors; `git diff --check` passed.
Residual: if a backend's normal `stop()` raises, termination remains unconfirmed and ownership is deliberately retained. AF-02 does not change `safe_proc_wait`, process reaping, AF-10 reclaim/kill, or AF-01 exit identity.
Next: mandatory no-edit adversarial review on the iteration-4 diff, then fresh exact verification.

## 2026-07-09T20:25 iteration 5

- [x] Old-cleanup confirmation gap: an old backend could mark itself closed and raise on its first phase-transition `stop()`, then return from the finalizer's idempotent retry without proving termination; the finalizer erased pid `11111`. RED evidence: `qa/red-finalizer-stop-confirmation.txt`.
- [x] Replacement-cleanup confirmation gap: a replacement could publish pid `22222`, fail during initialization, mark itself closed and raise during cleanup, then lose ownership when the caller's still-old `client` returned from its final stop. RED evidence: `qa/red-replacement-cleanup-confirmation.txt`.
- [x] Fix: retain an explicit unconfirmed-cleanup marker for either old or replacement stop failure. A later idempotent stop cannot clear in-memory or persisted ownership without a confirmed teardown.
- [x] Regression: the focused replacement test passed 1; both old-stop controls passed 2; the phase-transition module passed 40; the combined AF-02 selector passed 28; focused/full Ruff and `git diff --check` passed.
Next: mandatory no-edit adversarial review on the iteration-5 diff, then fresh exact verification. Keep reaping, AF-10 startup reclaim, and AF-01 exit identity outside AF-02.

## 2026-07-10T05:28:43+09:00 exact verification blocker

- [x] Focused behavior gates are green: force-eject 2 passed; agent-pid/full-lifecycle 9 passed; turn-spawn/agent-pid 7 passed; registry/force-eject neighbors 5 passed; stop-confirmation boundaries 3 passed.
- [ ] Repository lint/type/full-coverage gates could not execute in the prescribed dependency environment. `/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/python -m ruff check src tests` failed with `No module named ruff`; the equivalent Pyright command failed with `No module named pyright`; the coverage command failed because pytest did not recognize `--cov`, `--cov-report`, or `--cov-fail-under` (pytest-cov is absent).
- [x] `git diff --check` passed and the diff name set backward-traces cleanly to AF-02 implementation, tests, and the required daily changelog; no ticket or unrelated product files changed.
Next: supply or select one Python/tool environment containing Ruff, Pyright, and pytest-cov, then rerun the complete Exact Verify command set from this frozen worktree. Do not tick `GOAL.md`, mark QA PASS, or create `Z-2026-07-10.md` until those fresh gates pass.

## 2026-07-10T05:42 iteration 6

- [x] Full-suite compatibility regression: lifecycle PID synchronization dereferenced `client.pid` directly at six boundaries, so legacy backend doubles without that optional runtime attribute exited before their intended behavior. RED evidence: `qa/red-legacy-backend-without-pid.txt`; the prior exact verifier also reported the exact 14 affected tests.
- [x] Fix: one private normalizer maps an absent or non-integer backend pid to `None`; all six start/turn/rebuild reads use it. The `AgentBackend` protocol remains strict, while real integer pids are unchanged.
- [x] Regression: all 14 prior failures passed; 260 lifecycle/contract/backend/dispatch/phase/registry tests passed; the combined AF-02 selector passed 28; full Ruff, full Pyright, and `git diff --check` passed.
Next: fresh Exact Verify must rerun the complete CI-equivalent coverage suite, then alone may tick `GOAL.md`, mark QA PASS, and create `Z-2026-07-10.md`.

## 2026-07-10T05:45 iteration 7

- [x] Unsafe process-group input: Python treats `bool` as `int`, so normalized event `agent_pid=True` became PGID 1; zero and negative values were also accepted. At the final boundary, a stale/manually constructed entry could call `kill_process_group(True)`, `kill_process_group(0)`, or `kill_process_group(-1)`. RED evidence: `qa/red-bool-agent-pid.txt` (6 failed, 1 missing-pid control passed).
- [x] Fix: one value normalizer accepts only positive, non-boolean integers. All six backend-property reads, event ingestion, and the final force-eject boundary use it. Event-key presence preserves normalized-key precedence: an invalid present `agent_pid` is ignored rather than falling back to a valid legacy value.
- [x] Regression: focused unsafe/valid/legacy/precedence/force-eject controls passed 11; the combined AF-02 selector passed 34; the affected six-module set containing the prior 14 compatibility failures passed 266; full Ruff and Pyright passed; `git diff --check` passed.
Next: mandatory no-edit adversarial review, then fresh Exact Verify. Leave `GOAL.md` unticked, QA BLOCKED, and the completion marker absent until that independent gate passes.
