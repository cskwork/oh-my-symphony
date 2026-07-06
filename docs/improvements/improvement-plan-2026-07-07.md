# Improvement plan — 2026-07-07

Status update and continuation of
[`architecture-improvement-plan-2026-07-05.md`](./architecture-improvement-plan-2026-07-05.md)
(the umbrella). That document holds the rationale and literature; this one
records what landed in the two days since, what the landing itself changed,
and the prioritized list of what to do next. Scope: `src/symphony/` at
`v0.12.0`, `main` == `dev`.

## Verified baseline (2026-07-07)

Every claim below was re-measured today, not carried over:

- Full suite: **1279 passed, 0 failed, 2 skipped** (`python -m pytest -q`).
- **ruff: clean. pyright: 0 errors** (`ruff check src tests`; `pyright`).
- CI now gates lint + types + coverage: `.github/workflows/tests.yml` runs
  `ruff check`, `pyright`, and `pytest --cov=src/symphony --cov-fail-under=80`.
- `git rev-list --left-right --count main...dev` → `0 0`.

## Scoreboard — 2026-07-05 initiatives

| Initiative | Status | Evidence |
|---|---|---|
| **E — CI gates** | **Done, incl. ratchet** | `tests.yml` (ruff/pyright/cov≥80), `pyproject.toml` `[tool.ruff]`/`[tool.pyright]`; both clean today. Commit `24aa571`. |
| **C — backend contract** | **~80% done** | `tests/test_backend_contract.py` (Testcase Superclass; claude/gemini/agy/kiro/opencode/pi subclasses); `backends/per_turn.py` Template Method; gemini/opencode/plain_cli migrated (`e1c676d`…`1e7a3d2`). Remaining below. |
| **A — god-class split** | **Step 1 done** | `orchestrator/dispatch_state.py` (121 lines) owns the slot maps (`78bd505`). Steps 2–4 remain — and the target moved, see "New friction". |
| **B — structured concurrency** | **Partial** | Fire-and-forget tasks supervised (`67eb6ba`); worker fan-out is still `create_task` + `add_done_callback` (`core.py:3237`). |
| **D — DI over monkeypatch** | **Done** | `_pkg`/`_tui_pkg` indirection retired in 3 steps (`45c4a01`, `8711925`, `51cb5c3`); 1 residual `_pkg.` reference in core/tui. |

Also closed since the umbrella was written (previously tracked as open gaps):

- **Heartbeat feature shipped end-to-end** — `continuous_improvement.py`
  (899 lines, real runner), web API PUT/reset endpoints, settings card.
  `docs/plans/2026-07-05-heartbeat-handoff.md` ("paused after Task 4") is
  **stale**.
- **Raw `proc.wait()` in backends: zero** — the PerTurnCliBackend migration
  removed the codex/gemini/pi reap gap.
- **opencode token accounting** — `opencode.py:199` now parses usage with
  multi-key fallback; the `input_tokens=0` tell is gone.
- **`feat/reliability-hardening` fully merged** — its 5 known-red tests are
  resolved history; `docs/plans/2026-07-02-reliability-handoff.md` is stale.

## New friction created by the landings

1. **`orchestrator/core.py` grew 4574 → 5736 lines, 105 → 133 methods.**
   Initiative A extracted 121 lines while the heartbeat scheduler and
   blocked-board recovery added several hundred *into the same class*: 71
   mentions of `improvement` now live in core (`_maybe_schedule_continuous_improvement`,
   `_improvement_task`, `_on_improvement_task_done`, turn-budget state).
   The god-class is winning the race against its own decomposition plan.
2. **`webapi.py` grew 775 → 905** (heartbeat endpoints) — past the 800-line
   file rule; it was already on the umbrella's watch-list.
3. **Branch graveyard.** 12+ fully-merged local branches, 6 merged
   `symphony/*` ticket branches, 12 remote `cursor/*` branches; plus two
   unmerged branches holding possibly-wanted work (see P0-3).

## Work items

### P0 — hygiene (hours, no behavior change)

- **P0-1 Mark superseded docs.** Prepend a `> Superseded — shipped in vX`
  banner to `docs/plans/2026-07-05-heartbeat-handoff.md` and
  `docs/plans/2026-07-02-reliability-handoff.md` so a future session doesn't
  resume finished work. Update the scoreboard section of the umbrella plan
  (or link here).
- **P0-2 Branch cleanup** *(destructive — operator consent per branch list)*.
  Delete local branches merged into `main` (`git branch --merged main`),
  merged `symphony/*` ticket branches, and the 12 remote `cursor/*`
  audit branches. Keep: `dev`, unmerged `symphony/SMA-*` until triaged.
- **P0-3 Salvage-or-drop the two live unmerged branches.**
  - `improve/observability-and-doctor` (1 commit `0043e28`: stderr capture +
    positive-int validation). Stderr capture addresses a real operational gap
    (launcher logs go to the terminal today; diagnosing a dead orchestrator
    requires an ad-hoc `2>>` wrapper). Rebase onto `main`, re-review, land or
    close with a note.
  - `supergoal-post-e2e-hardening-20260702` (1 commit `766fcd8`: web board
    E2E coverage). Same treatment.

### P1 — structural (the umbrella's remaining sequence, re-ordered)

- **P1-1 Extract `ImprovementScheduler` out of core** *(new item — not in
  the umbrella)*. The heartbeat state cluster (`_improvement_task`, turn
  budget, postpone-vs-skip decisions, status dict) is the newest and most
  self-contained resident of the god-class; it was born with an injectable
  seam (`improvement_runner` / `improvement_lease`) so it is the cheapest
  extraction available and stops core.py's growth trend immediately.
- **P1-2 A steps 2–3: extract the four field clusters** per the umbrella:
  `TokenAccountant` (`_token_ema`, totals, `_apply_token_totals`),
  `PauseController` (`_pause_events` `core.py:542`, pause/resume),
  `HealthReporter` (`_health_summary` `core.py:1203`, `_consecutive_*`
  counters), `StallReconciler` (`_reconcile_running` family). One extraction
  = one commit, characterization tests first, green between steps.
- **P1-3 A step 4: Split-Phase the worker-turn continuation.** The old
  600-line `_on_worker_task_done` was reshaped (the callback at
  `core.py:3266` is now thin), but the decide/apply split (`TurnOutcome`
  value object) still does not exist — continuation decisions remain
  interleaved with mutation inside `_run_agent_attempt` and its helpers.
- **P1-4 B remainder: TaskGroup for worker fan-out.** Workers are still bare
  tasks tracked by dict (`core.py:3237`). Adopt `asyncio.TaskGroup` (or at
  minimum move the identity check fully into `DispatchState.free_slot`),
  plus `asyncio.timeout()` for external awaits. Gate behind
  `test_orchestrator_reconcile.py` + `test_agent_lifecycle_e2e.py`.
- **P1-5 C remainder.**
  - Migrate `claude_code.py` (444 lines, per-turn CLI, still on
    `BaseAgentBackend`) onto `PerTurnCliBackend`.
  - Decide `pi.py`'s family (506 lines, on `BaseAgentBackend`): per-turn →
    migrate; persistent → document it beside codex as family #2.
  - Optional: the umbrella's out-of-pipeline daily contract job against the
    real CLIs (schema drift is the incident class that has actually bitten).

### P2 — opportunistic

- **P2-1 File-size breaches** (split when next touched, not proactively):
  `tui/app.py` 1115, `backends/codex.py` 1021, `service.py` 986,
  `trackers/file.py` 919, `webapi.py` 905.
- **P2-2 Residual `finished_without_cleanup` race.** The orphan-path guard
  and diagnostic marker are in place (`core.py:3316`, `:3903`) but the race
  that pops `_running` mid-flight was never root-caused. When it next fires,
  the marker pairing identifies the window; consider a targeted
  characterization test around exit-vs-callback ordering instead of hunting
  cold.
- **P2-3 Public-repo doc audit.** The repo is heading public: re-verify
  README / llm-wiki / launcher claims against `v0.12.0` behavior (every
  example must run from a fresh clone).
- **P2-4 Coverage ratchet.** Only after P1 extractions settle: consider
  raising `--cov-fail-under` from 80 once the new modules carry their own
  focused suites.

## Sequencing

1. P0-1 → P0-3 (same session; P0-2 needs operator consent for deletions).
2. P1-1 `ImprovementScheduler` (newest, cheapest, stops the growth trend).
3. P1-2 extractions one at a time, interleaved with P1-5 backend migrations
   (independent — can run as parallel tickets).
4. P1-3 Split-Phase, then P1-4 TaskGroup on top of the slimmer core.
5. P2 items ride along with whatever touches their files.

Global rules unchanged from the umbrella: characterization tests before every
extraction; one small step, green tests, commit on `dev`, merge to `main`;
Strangler, never big-bang; full `pytest` + ruff + pyright before publishing.
