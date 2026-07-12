# QA - black-box exercise of the running app

QA drives the real app like a user and records observable evidence. Applies to GREENFIELD/LEGACY and
web-bug DEBUG checks.

UI changes are browser app verification: if product code changes a user-facing browser surface, the run
must drive that surface with `playwright-cli` and pass `bash templates/qa-gate.sh <vault> browser`.
Lint/typecheck/build/unit/static screenshots can support but not replace browser evidence.

QA-ONLY is the broad regression lane. For "QA only", broad verification, or post-change impact sweep, use
`reference/qa-only.md`'s Impact Matrix. GREENFIELD/LEGACY/DEBUG browser QA stays lean by default, but may
borrow the matrix for high-blast-radius changes.

## Characterization baseline (non-UI code changes)

Use when GREENFIELD / DEBUG / LEGACY work touches shared code/state: function, module, global state,
DB row/schema, config, cache, integration contract.

1. Pick reachable neighbor behavior the change could break.
2. Before editing, put that behavior under a check and save current output to
   `<vault>/qa/baseline/<neighbor>.txt`.
3. After editing, re-run the same check and diff against the snapshot.

Unnamed drift is red. Intentional drift must be named in `QA.md`. Characterization is a
regression signal, not a correctness oracle; update snapshots only for intentional bug fixes.

## Scenario stencil (code changes)

Lean default for DEBUG/LEGACY/GREENFIELD code verification:

- Equivalence partitions: normal inputs that should follow the same rule.
- Boundary values: null/undefined/empty/min/max/ordering edges.
- Negative/error paths: rejected inputs, failed dependencies, recovery.
- Regression: previous passing neighbor scenarios the change could break; link to captured baselines.
- Metamorphic relation: when no exact oracle exists, transform equivalent inputs and require equivalent
  meaning across outputs. Candidate execution is required; otherwise record named residual risk.

For prod issues that do not reproduce exactly in dev, derive synthetic or similar data from prod evidence
(logs, stack, read-only schema/rows via `reference/db-access.md`) while preserving failure-triggering
properties such as null/boundary/scale/ordering/timing/concurrency. Scrub PII. Do not call one fabricated
green case conclusive; record reproduction fidelity and post-deploy confirmation in the proof.

## Always use the `qa-tester` subagent

Browser dumps/screenshots/logs stay in `qa-tester`. Conductor receives summary + evidence paths only.

## Browser context capture - the single driver stage

One stage, one driver: QA black-box, DEBUG observe-first (`screen -> API`), LEGACY preserve-baseline.
They differ in captured evidence, not driver. `playwright-cli` is the ONLY driver
(`reference/playwright-cli.md`).

1. **Serve.** Start the app from the working tree (or the Verify worktree when one is used), poll
   until ready, record URL, tear down at end. Static/single HTML opens via `file://` from that tree.
2. **Get the driver.** `command -v playwright-cli`; if absent `npm install -g @playwright/cli@0.1.14`,
   then `playwright-cli install --skills`. Browser: `--browser=chrome` for system Chrome or
   `npx playwright install chromium`. If install is genuinely blocked, STOP and ask the user to install
   it - never substitute a headless render or any other tool (`reference/playwright-cli.md`).
3. **Drive flows.** `open`/`goto`, `snapshot` (get `ref`s), `click`/`type`/`fill`, `screenshot`. Golden
   path, edge cases, and a11y must pass. UI/UX jobs also run `reference/ui-ux.md` pre-flight, record a
   `UI-tier: Expressive|Functional` line in `## QA`, and enumerate the
   text/background pairs to `qa/contrast-pairs.json` — `qa-gate.sh` runs the contrast gate and blocks on FAIL.
4. **Capture the network.** `playwright-cli requests` lists every call; `request <index>` shows
   method + path + status + payload for one. This is the shared evidence for DEBUG `screen -> API` and
   LEGACY baseline (below). The Verify step still re-runs the project's REAL tests with no browser.
5. **Capture as-is/to-be.** Same route and viewport:
   `qa/as-is-<view>.png` before, `qa/to-be-<view>.png` after. For DEBUG, as-is is the failure and to-be
   is fixed behavior.

## Navigation map (load first; build on first entry; self-heal on drift)

Use `.domain-agent/qa/nav-map.md` to avoid rediscovering auth gates, popups, new tabs, and deep routes.
It also gives DEBUG `screen -> API` routing and LEGACY the exact screen/API to baseline. Repo-local and
gitignored; no app-specific selector, route, or credential lives in this skill.

1. **Load first.** Before driving, read `.domain-agent/qa/nav-map.md` if present and navigate by its
   entry/auth flow, route list, popup/new-tab triggers, tab-switch notes, and stable selectors.
2. **Build on first entry (when absent).** Within the action budget, do one light mapping pass and save
   it: the entry/auth flow; the `screen -> URL` route list; every click that opens a **new tab or
   popup** and the resulting target; how to switch the driver to that target; stable selectors for key
   controls; and each screen's real API calls from `playwright-cli requests` (method + path).
   Write `.domain-agent/qa/nav-map.md` and index it in `index.md`.
3. **Self-heal on drift.** When a saved entry no longer matches the live site - a selector ref is gone,
   a route 404s/redirects, a popup opens a different target, or an API path/method changed - re-map only
   that slice, correct that row, and bump `config.json.lastUpdated`. Never re-crawl the whole app. This
   is how a site change flows back into the map inside the workflow, so the next DEBUG/QA run navigates
   the current site.

**New tab / popup handling.** When an action opens a new tab or popup, `playwright-cli tab-select <index>`
to that target and re-`snapshot` before interacting, then record `trigger -> target` in nav-map
(`playwright-cli tab-list`; see `reference/playwright-cli.md`).

## API behavior baseline (LEGACY preserve)

Before refactoring or integrating against an existing API, capture its exact behavior so the refactor
preserves it. DEBUG's `screen -> endpoint` only localizes. Save to the run vault
`<vault>/qa/api-baseline-<endpoint>.md`: method + path + status + a representative request and the
response shape (the contract, not every value).

- **UI-reachable** - reach the screen via `qa/nav-map.md` and capture real calls with `playwright-cli
  requests` (grep the list by path; `request <index>` for one call's full contract), through `qa-tester`;
  promote a confirmed `screen -> API` row back into nav-map.
- **Backend-only (no UI)** - capture at the HTTP level: a recorded curl/HAR or an HTTP probe (e.g. the
  `verify` skill) against the running endpoint.

After refactor, re-capture the same call and diff; unintended drift is red (role-loop Verify). Baselines
are per-run vault evidence, never domain pack; strip secrets, tokens, PII.

## Authenticated sessions (native playwright-cli)

A real logged-in session is handled by playwright-cli itself - no separate tool. Three native paths
(`reference/playwright-cli.md`): a named session that keeps cookies/storage across calls
(`playwright-cli -s=<name> <cmd>`), a saved storage state (`state-save <file>` once authenticated,
`state-load <file>` later), or CDP attach to the user's existing browser (`playwright-cli open` attach
flags). The driver line stays `Tool: playwright-cli`; capture as-is/to-be the same way. There is one
driver, so there is no `Fallback:` line.

## CLI / library

Run an integration smoke: real invocation against a fixture, stdout/stderr/exit-code compared to
known-good output.

## Vault record

Put evidence under `<vault>/qa/` and summarize in `QA.md` `## QA`:

- `Tool: playwright-cli` (the only sanctioned driver).
- Commands run and pass/fail per check.
- Exact `qa/as-is-<view>.png` and `qa/to-be-<view>.png` paths.
- Served URL and teardown note.

## Exit gate

QA passes only when `bash templates/qa-gate.sh <vault> <browser|cli>` exits 0. Browser runs require
as-is/to-be evidence and a `Tool: playwright-cli` line. Never edit the gate to pass.

## Repeatable script

First QA may be hand-driven. On any re-check, stop hand-driving and propose a Playwright CLI script in
`qa/<flow>.spec.ts`; record its path in `## QA`.
