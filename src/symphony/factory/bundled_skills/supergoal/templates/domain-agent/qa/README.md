# qa/ - reusable browser QA assets

Repo-local, gitignored. Two kinds of file live here:

- `nav-map.md` - the single navigation map for this repo (entry/auth, routes, popups/new tabs,
  stable selectors, `screen -> API`). Loaded before any browser QA/observe run; self-healed on drift.
- `<suite>.md` - reusable QA-ONLY suites (Impact Matrix, scenario list, `Comparison:` type, named DB
  checks, saved baseline values, reproduction notes, coverage/uncovered risk, re-run command /
  Playwright spec path), indexed in `index.md` under `## QA Suites`.
- Per-run vaults also carry `qa/scenario-ledger.md` and `qa/shards/<shard-id>.md`; persist their durable
  scenario and risk content into the suite, not transient screenshots or raw dumps.

No secrets, tokens, raw rows, or PII. See `reference/qa.md` and `reference/qa-only.md`.
