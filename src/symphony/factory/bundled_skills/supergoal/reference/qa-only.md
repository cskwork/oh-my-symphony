# QA-ONLY mode - exercise + verify a running app, write no code

Use when the user wants QA, data verification, or a data comparison and explicitly NOT a code change:
"QA만", "qa only", "검증만 해줘", "데이터 정합성 확인", "API 수정 전후 확인", "A/B 테스트", "데이터 비교".

QA-ONLY drives an already-running app plus optional read-only DB, builds an Impact Matrix, reports what
worked / didn't / was discovered, and can persist a reusable QA suite. It writes NO production code, runs
NO build, creates NO worktree, and skips default-loop phases. It is read-only except the run folder and
the `.domain-agent/qa/` suite.

Fix/feature request -> route to DEBUG/LEGACY; QA-ONLY reports findings and stops.

## Pipeline

`Intake -> Target & Access -> Impact Matrix -> Scenario checkpoint -> Exercise -> Cross-check -> Report -> Persist`

| Phase | Goal | Writes | Exit gate |
|---|---|---|---|
| Intake | Capture QA goal, comparison type, acceptance, supplied change/release context | `brief.md`, `state.json` | goal + comparison type stated |
| Target & Access | Resolve running-app target (URL/env, NOT built from repo), browser driver, read-only DB access, action budget | `brief.md` | target reachable; driver + DB access named/skipped; `action_cap` set (default 100) |
| Impact Matrix | Infer the feature's direct, adjacent, before/during/after, data, role, viewport, and failure-risk surface | `brief.md` | coverage plan has priority tiers and explicit exclusions |
| Scenario checkpoint | Show the detailed scenario list + budget + comparison type; let the user narrow | run note | user confirms or narrows; proceed unless told to wait |
| Exercise | Drive the app through scenarios via `qa-auditor`, capped | `QA.md` `## QA`, `qa/`, `state.json` | each scenario pass/fail recorded; combined `action_count` written to `state.json`, `<= action_cap` |
| Cross-check | Read-only DB checks via `db-reader`: auth, source-of-truth expected values, dataset/env diff | `QA.md` `## QA`, `qa/` | each check pass/fail + small diff recorded; no raw dumps |
| Report | Human-friendly result with coverage, reproduction steps, and remaining risk | `report.md` | required report headings present |
| Persist | Save reusable suite, index it | `.domain-agent/qa/<suite>.md`, `index.md` | suite + re-run steps saved; no secrets/PII; path gitignored |

## Target & Access

- **Target.** Test an existing running app. Ask for URL/env. Start a local server only if asked; never
  build product code to get one. Static single HTML opens via `file://`.
- **Browser driver.** `playwright-cli` is the only driver (`reference/playwright-cli.md`); install if
  absent; record `Tool: playwright-cli` in `## QA`. Auth uses native session/state/CDP-attach paths
  (`reference/qa.md` "Authenticated sessions").
- **Navigation map.** Load `.domain-agent/qa/nav-map.md` first to reach gated/popup-heavy screens;
  build it on first entry and correct any drifted rows in place (`reference/qa.md` "Navigation map").
- **DB access.** Read-only, DB-independent (`reference/db-access.md`): fetch test auth, verify UI values
  against source of truth, or run dataset/env diffs.

## Impact Matrix (detailed QA model)

Before scenarios, build an **Impact Matrix** from the request, release note/diff summary, nav-map,
domain pack, screen/API calls, and observed behavior, so "changed button works" does not hide adjacent
regressions.

Include:

- **Feature under test.** The user/business outcome, not just the route or button label.
- **before/during/after actions.** Before: auth, role, permissions, settings, seed/existing data, entry
  route. During: happy path, edges, validation failure, retry, cancel, duplicate submit, repeated run.
  After: refresh, back/forward, reopen, logout/login, revisit elsewhere, and expected next-state checks.
- **Adjacent surfaces.** Screens, tabs, API calls, DB tables, cache/storage, notifications, exports/files,
  search/list views, counters, totals, and permissions that share the same state.
- **Displayed data.** Check displayed data accuracy and consistency across state propagation paths:
  detail, list/search, counters/totals, dashboards, notifications, exports, related records, and
  persisted source-of-truth where they exist.
- **Feature-specific scenario families.** Pick the family that matches the web feature instead of forcing
  a generic CRUD path:
  - CRUD/content publishing: create -> list/search -> detail -> edit -> refresh/reopen -> delete/archive.
  - Workflow/status transitions: start -> approve/reject/cancel -> downstream queue/list/badge update.
  - Commerce/booking: select -> price/availability -> cart/reservation -> payment/confirmation -> history.
  - Auth/account/settings: login/session -> permission change -> protected routes -> logout/relogin.
  - Search/filter/sort/pagination: query -> filter/sort/page change -> item open -> back preserves state.
  - Upload/download/export: upload -> preview/metadata -> persisted listing -> download/export content.
  - Realtime/notification: trigger event -> toast/badge/feed -> refresh/reconnect consistency.
- **Complexity probes.** Include complex multi-step scenarios such as create -> edit -> save -> reopen,
  partial failure -> retry, role switch, concurrent/repeated action, stale-cache refresh, empty data,
  high/low boundary values, mobile/desktop viewport, and keyboard/a11y navigation when relevant.
- **Risk tier.** Mark scenarios `must`/`should`/`could`; run `must` first, then highest-risk `should`
  until the action cap. Never mark an unrun item as covered.

If access/data/fixture/role/session/DB/budget blocks a scenario, list it as uncovered with reason and
residual risk. Do not collapse a detailed QA request into a happy-path smoke.

## Comparison types

State one in `brief.md` `Comparison:`; it picks what the Cross-check phase compares.

| `Comparison:` | Compares | How |
|---|---|---|
| `functional` | scenario outcome vs expected behavior | drive the Impact Matrix must-paths, edge paths, and adjacent checks; assert each step |
| `data-integrity` | UI value vs DB source of truth | read the value on screen, query the DB, diff |
| `before-after` | a saved baseline suite vs the app now (regression after an API/code change) | re-run a persisted `.domain-agent/qa/<suite>.md`; diff against its baseline |
| `ab` | variant A vs variant B (A/B test arms) | run the same scenario on each arm; diff outcome/data |
| `env` | environment A vs environment B | run the same scenario/query on each; diff outcome/data |

## Action cap (anti-context-overload)

Each browser interaction (`open/click/type/fill/snapshot/screenshot`) and DB query counts as one QA
action. Default `action_cap` is **100**. `functional`: all budget to `qa-auditor`; DB comparisons reserve
a small `db-reader` sub-budget. Agents report counts; conductor writes `state.json.action_count`. At cap,
stop, report done/remaining, and ask before a fresh budget. Gate fails if `action_count > action_cap`.

## Vault (reduced run folder)

`docs/changelog/<YYYY-MM>/<DD-qa-topic>/`: `brief.md`, `QA.md` (`## QA` evidence),
`report.md`, `qa/scenario-ledger.md`, `qa/` evidence, `state.json`. No `PLAN.md`. Per-run report stays
here; reusable suite goes to the domain pack.

## Report (the one human-facing deliverable)

Write `report.md` from `templates/qa-report.md` in the docs language (SKILL.md) under English anchor
headings. Include verdict, coverage, per-scenario pass/fail + evidence path, findings, reproduction
steps, not-covered risk, and re-run command/suite. For issues, include URL/env, role/account type (not
credentials), starting state, exact clicks/inputs, expected vs actual, evidence path, and retry count if
intermittent.

## Persist (repeatable + indexed)

Save reusable suites under the domain pack (`reference/domain-context.md` saving loop):

- `.domain-agent/qa/<suite>.md`: Impact Matrix, scenario list, steps, `Comparison:` type, named DB
  checks (no raw secrets/data), saved `before-after` baseline values, reproduction notes, coverage,
  uncovered areas, and residual risks, re-run command / Playwright spec path.
- Index it in `.domain-agent/index.md` under `## QA Suites` so "re-run the <feature> QA" routes
  instantly.
- `.domain-agent/qa/nav-map.md`: one shared navigation map per repo (entry/auth, routes, popups/new
  tabs, selectors, `screen -> API`). Load it before driving; correct touched drifted rows.
- Same domain-pack rules: read-only repo write, gitignored path, no secrets/tokens/PII/raw rows.

Repeatable Playwright spec path: `.domain-agent/qa/<suite>/<flow>.spec.ts` (first run may be hand-driven).

`before-after` re-run: confirm saved baseline date and schema/contract still fit; otherwise re-derive
before diffing.

## Gate

`bash templates/qa-only-gate.sh <vault> <browser|cli>` exits 0 only when: `report.md` carries the
required anchor headings; the underlying `qa-gate.sh` browser/CLI evidence passes; `action_count <=
action_cap` in `state.json`; and, if a DB was used, a `DB:` line marks it read-only and no write verb
(INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE) appears in the recorded DB commands. Never edit the gate to
pass.

## Dispatch

Two separate read-only subagents keep concerns/PII isolated. Broad QA may use multiple `qa-auditor`
instances, but types stay separate:

- `qa-auditor` (`agents/qa-auditor.md`): drives the running app; screenshots/dumps stay in its context.
- `db-reader` (`agents/db-reader.md`): reads the DB read-only; raw rows/secrets stay in its context.

For broad QA, split the Impact Matrix into **Scenario shards** only when independent: direct flow,
adjacent surface, role/permission, displayed-data propagation, viewport/a11y, error/retry, env/A-B arms.
Do not shard ordered flows, shared mutable state, or scarce test data.

Use the vault as the common communication surface:

- `qa/scenario-ledger.md`: the shared ledger owned by the conductor. It contains the Impact Matrix,
  shard list, owner, status, action count, evidence path, covered/uncovered result, and residual risk.
- `qa/shards/<shard-id>.md`: one file per QA subagent. The subagent writes only its own shard file and
  evidence under `qa/`; it never edits another shard or the shared ledger directly.

Conductor merges shards into `qa/scenario-ledger.md`, `QA.md`, and `report.md`.

Handoff is conductor-mediated through the vault, never agent-to-agent. For `data-integrity`, `db-reader`
writes sanitized `qa/expected.md` (`field -> expected value`, no raw rows/secrets/PII); the conductor
passes those small values to `qa-auditor` for UI diffing. Auth/credentials are the exception: hand off
transiently in the `qa-auditor` prompt, never written to any file. For `ab`/`env`/dataset diffs with no
UI, `db-reader` returns the diff directly. The conductor receives compressed summaries, assembles
`report.md`, and must never drive the browser or DB from the conductor.

`qa/expected.md` also becomes the saved **baseline** for a later `before-after` re-run.

## Stop conditions

- Target not reachable: report it; do not build product code to conjure one.
- A finding needs a code fix: report it as a finding and recommend switching to DEBUG/LEGACY; do not
  edit product code.
- DB only reachable with a write-capable account: still issue read-only statements only; never write.
- DB truth is load-bearing but access is missing/skipped/unsafe: record `DB evidence: Not covered` under
  `## Not covered`, with the blocker and residual risk.
- Action cap hit: stop, report done + remaining, ask before spending a fresh budget.
- DB credentials or PII would land in a saved file: redact; never persist secrets or raw rows.
- Complex or adjacent scenario cannot be exercised safely: record it under `## Not covered` with the
  concrete blocker and residual risk.
