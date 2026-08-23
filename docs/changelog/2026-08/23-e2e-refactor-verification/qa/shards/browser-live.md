# Live browser QA shard

## Verdict

**PASS with one non-breaking console observation.** The live board loaded, E2E-3 was visible in the terminal Done lane, its issue detail/history and run diagnostic surface were coherent, the state/issue/run APIs returned 200, and a safe Run history refresh preserved the result. The only console error was a missing favicon (`/favicon.ico` 404); no application API or JavaScript errors were observed.

## Target and driver

- URL: `http://127.0.0.1:9998/#/board`
- Exact state URL checked: `http://127.0.0.1:9998/api/v1/state`
- Tool: installed `playwright-cli` only; no fallback driver and no install/update.
- Named session: `sym-live-qa-20260823`
- Action count: **25 / 25**, conservatively including failed open/click attempts.
- Managed service: left running for the separately recorded teardown gate.

## Findings

- Initial board snapshot: project `sym-e2e`; E2E-3 appeared in the terminal `Done` lane. E2E-4 was initially visible in `In Progress`, which was allowed by the brief.
- E2E-3 detail: State `Done`, agent `opencode`, one `normal` run, and description history containing Triage plus notes for `In Progress`, `Verify`, and `Document`.
- Run detail: status `normal`, state `Done`, duration `5.5m`, failure class `—`, and recovery checkpoint `Done · turn 2`. The timeline exposed run acquisition, worker/session/turn activity, `In Progress -> Verify`, and terminal `Run completed` with state Done.
- Diagnostic surface: `Download diagnostic JSON` was visibly available on the E2E-3 run detail. It was not downloaded because surface availability was sufficient and the bounded task did not require retaining a second artifact.
- Refresh: clicking the Run history `Refresh` control preserved E2E-3 as `normal` / Done and its diagnostic timeline. The refreshed UI showed `0 running · 0 retrying`; E2E-4 had completed as `normal` during observation.
- Exact `/api/v1/state` response: `200 OK`; `running=0`, `retrying=0`, health `ok`, no degraded reasons, and `tick_alive=true`.

## Network summary

- `GET /api/v1/projects` -> `200 OK`
- `GET /api/v1/board` -> `200 OK` (polling state surface); response listed E2E-3 as Done and later E2E-4 as Done.
- `GET /api/v1/issues/E2E-3` -> `200 OK` in `9ms`; response state was Done and matched the detail drawer/history.
- `GET /api/v1/runs?issue=E2E-3&limit=10` -> `200 OK`
- `GET /api/v1/runs?limit=200` -> `200 OK`
- `GET /api/v1/runs/<run-id>` -> `200 OK`
- `GET /api/v1/state` -> `200 OK`

## Console summary

- Errors: **1** — `Failed to load resource` for `http://127.0.0.1:9998/favicon.ico` (`404 Not Found`).
- Warnings: **0**.
- Breaking JavaScript/application errors: **0 observed**.

## Evidence retention

The browser state was visually inspected during the run. Generated screenshots
were removed before commit under the repository policy that excludes generated
browser artifacts; the durable evidence here is the curated action/API ledger.

## Action ledger

All session commands used `playwright-cli -s=sym-live-qa-20260823`.

| # | Command suffix | Result |
|---:|---|---|
| 1 | `open http://127.0.0.1:9998/#/board` | Sandbox blocked daemon log creation (`EPERM`); no page opened. |
| 2 | `open http://127.0.0.1:9998/#/board` | Opened after allowing the installed CLI to use its existing daemon directory. |
| 3 | `snapshot` | Board/project snapshot; E2E-3 Done and E2E-4 In Progress. |
| 4 | `screenshot` | Visual checkpoint captured during QA; generated file not retained in Git. |
| 5 | `click e243` | Auto-refresh invalidated the snapshot ref. |
| 6 | `snapshot` | Refreshed live refs. |
| 7 | `click e409` | Auto-refresh invalidated the snapshot ref again. |
| 8 | `click 'button:has-text("E2E-3")'` | Selector quoting rejected by the CLI. |
| 9 | `click 'text=E2E-3'` | Opened E2E-3 detail. |
| 10 | `snapshot` | Captured Done state, notes, and run history. |
| 11 | `click 'text=initial opencode'` | Opened the E2E-3 run detail. |
| 12 | `snapshot` | Captured run diagnostic/timeline surface. |
| 13 | `requests` | Captured board, issue, and run request list. |
| 14 | `request 30` | Inspected `GET /api/v1/issues/E2E-3` (`200`). |
| 15 | `request 54` | Inspected `GET /api/v1/board` (`200`). |
| 16 | `response-body 30` | Confirmed E2E-3 Done and history shape. |
| 17 | `response-body 54` | Confirmed board/API state consistency. |
| 18 | `console warning` | One favicon 404 error; zero warnings. |
| 19 | `click 'text=Refresh'` | Exercised safe Run history refresh. |
| 20 | `snapshot` | Confirmed E2E-3 remained normal/Done after refresh. |
| 21 | `goto http://127.0.0.1:9998/api/v1/state` | Opened exact state API read-only. |
| 22 | `snapshot` | Confirmed healthy state response and zero running/retrying. |
| 23 | `requests` | Dynamic-only list omitted the document request. |
| 24 | `requests --static` | Captured `GET /api/v1/state -> 200 OK`. |
| 25 | `close` | Named browser session closed successfully. |

Non-browser setup/usage checks (`Get-Command playwright-cli` and `playwright-cli click --help`) did not contact the page and are not counted as browser actions.

## Teardown

- Browser session: `Browser 'sym-live-qa-20260823' closed`.
- Managed Symphony service: deliberately left for the separate service-stop
  verification, which later passed after the lifecycle correction.
- Product source/tests: not edited.
