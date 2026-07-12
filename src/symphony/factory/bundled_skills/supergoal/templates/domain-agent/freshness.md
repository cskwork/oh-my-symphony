# Freshness Policy

Goal: keep domain context useful without making every run spend tokens on stale-document auditing.

## Policy

- Light refresh threshold: 5 days after `config.json.lastUpdated`.
- Full review threshold: 30 days after `config.json.lastUpdated`.
- Triggered refresh: selected knowledge conflicts with current code, terminology conflicts with the
  glossary, or a run proves a stable new fact that improves future routing.

## Every Run

1. Read `config.json` and `index.md`.
2. Select at most five domain files.
3. Verify load-bearing facts against current code/docs.
4. Write only a compact Domain Brief to the run vault, including terminology conflicts.

## Light Refresh

Use when the pack is older than 5 days or a selected flow feels suspicious.

1. Run CodeGraph status for only affected repos.
2. Check changed docs/source since `lastUpdated` when git history is usable.
3. Re-read only selected domain files and their cited source files.
4. Patch stale entries surgically.

## Full Review

Use monthly, after major repo changes, or after repeated stale-context misses. Review `index.md`,
`code-map.md`, `test-map.md`, `invariants.md`, and high-traffic flows. Split broad files instead of
appending more detail.
