# QA - AF-03 through AF-16 reliability

- Verdict: PASS

## Before

- [x] Ticket audit confirms fourteen open AF-03..AF-16 requirements on `dev@4de380f`; exact defect locations and current behavior are named in the source tickets - evidence: `docs/improvements/tickets/2026-07-09/AF-03-*.md` through `AF-16-*.md`.
- [x] Structural preserve-baseline captured before source edits for lifecycle, tracker, backend, registry, scheduler, rewind, and prompt entry points - evidence: codebase-memory snippets recorded during Frame.
- [x] Clean repository baseline recorded before Build - evidence: `1286 passed, 2 skipped` in 83.97s; Ruff `All checks passed!`; current project Pyright `0 errors, 0 warnings, 0 informations`.

## Results

- [x] Focused red-green regressions prove every reachable AF ticket - evidence: all 36 GOAL-named regressions exist in `tests/` and pass inside the full suite; the six correction-iteration-3 regressions were re-run in isolation on `dev@ee64bad` (`6 passed in 0.77s`).
- [x] Full repository tests, lint, and type checks pass - evidence on `dev@ee64bad` (2026-07-10): `python -m pytest -q` `1363 passed, 5 skipped in 89.45s`; `python -m ruff check src tests` `All checks passed!`; `python -m pyright src` `0 errors, 0 warnings, 0 informations`.
- [x] Mandatory full-spec, edge-case, and adversarial reviews have no open grounded finding - evidence: `review-full-spec.md` traces all 16 criteria as covered; `review-edge-cases.md` fixed its two gaps red-green; the four `review-adversarial.md` findings (AF-06/08/09/10) were closed red-green in `correction-iteration-3.md`, and the fixes were spot-verified present at `dev@ee64bad` (`reclaiming` fence, `shutdown_abandoned` lease finalize, marker-scoped temp sweep, closed-state-independent `stop()` reap).
- [x] Every diff hunk maps to AF-03..AF-16 or the required run evidence - evidence: the `ee64bad` 52-file diff contains only AF-scoped source modules traced in the full-spec criterion table, their tests, ticket/changelog/run-vault docs, and the AF-16 `max_total_turns` workflow-example documentation.

Backward-trace: clean

## Commands

| Command | Source | Proves |
|---|---|---|
| `uv run --extra dev pytest -q` | frozen_repo | full Python behavior |
| `uv run --extra dev ruff check src tests` | frozen_repo | lint |
| `uv run --extra dev pyright src` | frozen_repo | type safety |
| `codex app-server generate-json-schema --experimental --out /private/tmp/codex-schema-af14-20260710` | evaluator_owned | AF-14 current protocol shape |

## QA

Tool: not applicable (library/service tests; no browser UI change)
UI-tier: not applicable
DB: SQLite run-registry tests use isolated temporary files; no production data.

## Reproduction Fidelity

- Fidelity level: synthetic-representative
- Residual risk from data gap: timing/process tests use controlled tasks and child processes rather than a long-lived production board; full suite cannot prove every OS scheduling interleaving.
- Post-deploy confirmation plan: run a disposable file-board smoke with pause/resume, state PATCH refusal, corrupt-temp scan, bounded stop, and dead-owner lease recovery before release.
- Environment note: `symphony doctor ./WORKFLOW.md` passed configuration checks but could not validate this disposable worktree's host-owned board/root paths inside the managed sandbox; this is not product verification evidence.

## Residual Risk

- AF-04: the running-state guard and the tracker mutation are separate operations; no regression covers a dispatch that begins between the guard check and `update_fields`. Recorded by the adversarial review as an unpromoted risk pending an explicit concurrency decision.
- AF-10: recovery persists only a numeric pid/process-group id with no process birth-identity check, so PID reuse could signal an unrelated process group. No deterministic reproduction was established.
- Follow-up: the post-deploy file-board smoke listed under Reproduction Fidelity remains the release-time confirmation plan.

## Closure

- Exact verification completed 2026-07-10 on `dev@ee64bad` with `/opt/anaconda3/bin/python -m pytest -q`, `-m ruff check src tests`, and `-m pyright src`; all `GOAL.md` criteria ticked.
