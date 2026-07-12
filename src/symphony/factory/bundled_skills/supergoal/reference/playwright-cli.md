# playwright-cli - the single browser driver

`playwright-cli` (`@playwright/cli`, microsoft/playwright-cli) is the ONLY sanctioned browser driver
for supergoal QA, DEBUG observe-first, and LEGACY baseline. No agent-browser, no Playwright MCP, no
Computer Use, no ad-hoc Chrome, no headless-render fallback. Token-efficient by design: it does not
force page data into the model. Run it only inside `qa-tester`/`qa-auditor`, never the conductor.

## Get the driver (inside the subagent)

1. `command -v playwright-cli` - if absent, `npm install -g @playwright/cli@0.1.14` (Node 18+).
   This repo records the package version tested by the gates; update this reference and rerun
   `bash tests/run-all.sh` before moving the pin.
2. `playwright-cli install --skills` - installs the upstream skill locally so the driver self-documents;
   skill-less operation is still fine via `playwright-cli --help`.
3. Browser binary: headless by default; `--headed` to watch. Use system Chrome with
   `playwright-cli open <url> --browser=chrome`, or fetch one with `npx playwright install chromium`.
4. If install is genuinely blocked (offline, no npm), STOP and ask the user to install. Never substitute
   a headless-Chrome render or any other tool - the gate requires `Tool: playwright-cli`.

## Drive a page

- `playwright-cli open <url>` / `goto <url>` - open / navigate (`file://` path for a static single HTML).
- `playwright-cli snapshot` - capture the page and get element `ref`s; re-snapshot after navigation.
- `playwright-cli click <ref>` / `type <text>` / `fill <ref> <text> [--submit]` / `hover|select|check` -
  interact. Target by `ref`, CSS selector, or locator (`getByRole(...)`, `getByTestId(...)`).
- `playwright-cli screenshot [ref] --filename=<path>` - the as-is/to-be evidence.
- `playwright-cli press <key>`, `go-back|go-forward|reload`, `resize <w> <h>`, `close`.

## Network capture (DEBUG screen->API, LEGACY baseline)

- `playwright-cli requests` - list every network request since page load.
- `playwright-cli request <index>` - method + path + status + headers/body for one call.
- Filter the list by path with `grep`; promote a confirmed `screen -> API` row into `qa/nav-map.md`.
- `playwright-cli console [min-level]` - console errors/warnings at the symptom boundary.

## Tabs / popups

`playwright-cli tab-list`, `tab-new [url]`, `tab-select <index>`, `tab-close [index]`. When a click opens
a new tab/popup, `tab-select` it and re-`snapshot` before interacting; record `trigger -> target` in nav-map.

## Authenticated / logged-in sessions (native, no separate tool)

Named session keeps cookies/storage across calls: `playwright-cli -s=<name> <cmd>` (or
`PLAYWRIGHT_CLI_SESSION`). `--persistent` saves the profile to disk. To reuse a real login:

- `playwright-cli state-save <file>` once authenticated, then `state-load <file>` on later runs; or
- attach to the user's existing browser over CDP via `open`'s attach flags (`playwright-cli open --help`).

This replaces the old separate attach-to-browser skill - it is all playwright-cli now. The driver line
stays `Tool: playwright-cli`; no `Fallback:` is needed because there is one driver.

## Repeatable spec

First run may be hand-driven. On re-check, stop hand-driving: save a Playwright CLI spec under
`qa/<flow>.spec.ts` (`run-code` / locators) and record its path in `## QA`.

Full command list: `playwright-cli --help` or the upstream skill
(`playwright-cli install --skills`; https://github.com/microsoft/playwright-cli/tree/main/skills/playwright-cli).
