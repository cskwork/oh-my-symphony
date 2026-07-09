# QA - AF-03 through AF-16 reliability

- Verdict: PARTIAL

## Before

- [x] Ticket audit confirms fourteen open AF-03..AF-16 requirements on `dev@4de380f`; exact defect locations and current behavior are named in the source tickets - evidence: `docs/improvements/tickets/2026-07-09/AF-03-*.md` through `AF-16-*.md`.
- [x] Structural preserve-baseline captured before source edits for lifecycle, tracker, backend, registry, scheduler, rewind, and prompt entry points - evidence: codebase-memory snippets recorded during Frame.
- [x] Clean repository baseline recorded before Build - evidence: `1286 passed, 2 skipped` in 83.97s; Ruff `All checks passed!`; current project Pyright `0 errors, 0 warnings, 0 informations`.

## Results

- [ ] Focused red-green regressions prove every reachable AF ticket.
- [ ] Full repository tests, lint, and type checks pass.
- [ ] Mandatory full-spec, edge-case, and adversarial reviews have no open grounded finding.
- [ ] Every diff hunk maps to AF-03..AF-16 or the required run evidence.

Backward-trace: pending

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

- Not proven: implementation and exact verification are pending.
- Follow-up: none until the role loop completes.
