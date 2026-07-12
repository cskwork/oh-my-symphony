# Navigation map

The single browser navigation map for this repo. Loaded before any browser QA/observe run so the
driver reaches screens cheaply, and used by DEBUG observe-first to map `screen -> exact API`.
Self-healed on drift. Repo-local and gitignored. No secrets, tokens, or PII.
See `reference/qa.md` "Navigation map".

## Entry / auth flow

- Start URL: `<url>`
- How to get in: `<login steps / token / SSO / postMessage / nothing>`
- Driver: `playwright-cli` (auth via named session / state-load / CDP attach if needed)

## Tab / popup handling

- `<trigger, e.g. click "Enter">` opens a `<new tab | popup>` -> `<resulting URL/target>`; switch the
  driver to that target, then re-`snapshot` before interacting.

## Screens

| Screen | URL | Reach (clicks / popup / tab) | Key selector | API calls (method + path) |
|---|---|---|---|---|
| `<name>` | `<route>` | `<how to get there>` | `<@ref or selector>` | `<GET /api/...>` |

## Notes

- Last verified: `<date>` against `<env>`.
- On drift (selector miss, route 404, popup target moved, API path/method changed), correct only the
  touched row in place; do not re-crawl the whole app. Bump `config.json.lastUpdated`.
