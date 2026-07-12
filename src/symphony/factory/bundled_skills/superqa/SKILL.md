---
name: superqa
description: Browser QA for any website: turns a plain prompt into test scenarios, drives a real browser, and flags side effects (console/JS errors, failed requests, unexpected dialogs/popups/tabs) in plain-language reports. Also click-to-record, visual baselines, a clickable web admin, and CI/JUnit output. Use when the user says QA, browser test, regression check, record/schedule a scenario, or gives a URL to verify.
---

# SuperQA - browser QA on anything, for anyone

Contract: simple prompt -> concrete scenarios -> real browser evidence -> report in the
user's language. Never claim a check passed without a run directory + report to show.

## Mode (classify the request, state it in one line)

| Signal in request | Mode | Route |
|---|---|---|
| "QA this <url>", vague target, no scenarios yet | EXPLORE-QA | explore live site, generate scenario cases, run them (`reference/agent-qa.md`) |
| scenarios exist / "run the cases" / feature finished, verify | REGRESSION | `superqa run --all --site <site>`; diff vs last run (`reference/agent-qa.md` step 5) |
| "quick check / smoke / is it up" | AUTO | `superqa auto <url> --site <site>` |
| non-dev wants to create a test by clicking | RECORD | `superqa record <url>` or TUI `n` key (`reference/tui.md`) |
| "every N minutes / daily / automate" | SCHEDULE | `superqa schedule add <scenario> --every <min>` + daemon (`reference/tui.md`) |
| "open the QA app / dashboard" | TUI | `bash scripts/superqa.sh` |

## Hard rules

1. **Site knowledge is local, never committed.** Entry URLs, accounts, login quirks,
   popup behaviors live in `~/.superqa/sites/<site>/rules.md` and the SQLite var store -
   never in this repo, never in scenario files pushed anywhere (`reference/site-rules.md`).
2. **Credentials via the var store only.** `superqa vars set <site> username <v>` /
   `password <v>`; scenarios reference `{{username}}` / `{{password}}`. Password-like keys
   are auto-masked in every report. Never hardcode credentials in YAML or reports.
3. **Evidence or it did not happen.** Every run produces
   `~/.superqa/reports/<stamp>-<name>/report.html` + per-step screenshots. Quote the
   report path and the pass/fail counts in your summary.
4. **Report in the user's language.** Scenario `language:` drives report labels; your
   summary to the user follows the conversation language (`reference/report.md`).
5. **Side effects are findings, not noise.** Console errors, JS exceptions, failed
   requests, HTTP 4xx/5xx, unexpected dialogs/popups/tabs are collected on every run,
   deduped with counts, and diffed against the previous run (new types = regression
   signal). Declare known noise in `~/.superqa/sites/<site>/ignore.yaml` instead of
   ignoring findings by hand (`reference/side-effects.md`).
6. **Popups and dialogs never block a run.** Engine policy auto-accepts dialogs and
   follows new tabs by default; scenario `policy:` overrides (`reference/scenario-format.md`).

## EXPLORE-QA loop (default when only a URL/prompt is given)

1. **Ground.** Read `~/.superqa/sites/<site>/rules.md` if present; ask for credentials
   only if login is required and vars are missing.
2. **Explore.** Drive the live site with `playwright-cli` (snapshot -> click -> snapshot),
   mapping entry flow, login, menus, popups/new tabs (`reference/agent-qa.md`).
3. **Generate cases.** Write scenario YAMLs to `~/.superqa/scenarios/<site>/` covering:
   happy path, validation, error paths, edge cases, and every popup/tab transition you
   found (`reference/scenario-gen.md`).
4. **Run.** `python3 -m superqa_tui run --all --site <site> --headless` (from this skill's
   root, or the installed `superqa` command).
5. **Report.** Read the report, triage side effects, summarize for the user in their
   language with the report path. Update the local site rules file with what you learned.

## Non-dev lane (what you tell users)

- **Web admin (most clickable): `superqa serve`** opens a browser dashboard listing every
  scenario - recorded and agent-authored alike - with a Run button each, live progress,
  run history, and inline reports. Same data as the TUI/CLI.
- Terminal TUI: `bash scripts/superqa.sh` - `n` record by clicking in a real browser,
  `r` run, `a` run all, `u` auto QA, `s` schedule, `v` accounts/vars, `o` open report.
- While recording, a SuperQA panel floats in the browser (pause / add-assertion /
  save-and-finish; it re-mounts itself if the site re-renders). Typed passwords are
  stored as `{{password}}`, never as plain text.

## Reference map

| File | When |
|---|---|
| `reference/agent-qa.md` | EXPLORE-QA / REGRESSION procedure for the agent |
| `reference/scenario-gen.md` | prompt -> scenario case design method |
| `reference/scenario-format.md` | YAML schema: actions, selectors, `{{vars}}`, policy |
| `reference/side-effects.md` | what is captured; triage rules |
| `reference/site-rules.md` | local per-site knowledge protocol (never commit) |
| `reference/report.md` | report structure + language rules |
| `reference/tui.md` | TUI / record / schedule usage for humans |

**Done =** mode stated; scenarios exist as YAML under `~/.superqa/scenarios/<site>/`;
run executed with report path quoted; side effects triaged; site rules updated;
no site-specific data staged for commit.
