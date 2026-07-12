<!--
QA-ONLY human report. Fill from the qa-auditor summary. Keep it readable by a non-engineer.
Prose in the docs language (SKILL.md). Keep the `##` anchor headings in English (the gate greps them)
and file paths/commands verbatim.
No credentials, no raw DB rows, no PII.
-->

# QA report - <objective>

- Date: <YYYY-MM-DD>   Target: <url/env>   Driver: <playwright-cli | CLI smoke>
- Comparison: <functional | data-integrity | before-after | ab | env>
- Verdict: <PASS | FAIL | PARTIAL>   Actions used: <action_count>/<action_cap>

## Impact coverage

<what the Impact Matrix covered: selected feature-specific scenario family, direct feature path,
before/during/after actions, adjacent screens/APIs, displayed data consistency/state propagation paths,
roles, data checks, viewport/device checks, failure/retry paths. Keep it short but specific.>
- Covered: <must scenario group> -> <evidence path>
- Covered: <adjacent/shared-state or displayed-data propagation group> -> <evidence path>

## What worked

<plain-language list of scenarios/checks that passed, each with its evidence path>
- <scenario> -> PASS (`qa/to-be-<view>.png`)

## What didn't

<scenarios/checks that failed, each with observed vs expected and evidence. Each failure must have a
matching item under `## Reproduction notes`.>
- <scenario> -> FAIL: expected <x>, saw <y> (`qa/as-is-<view>.png`)

## What I discovered

<concrete findings worth the user's attention: a UI value that disagreed with the DB source of truth,
a broken edge case, an A/B / before-after difference, a data-integrity gap. One finding per bullet.>
- <finding> (evidence: `qa/<file>`)

## Reproduction notes

<for each issue, make it easy for a human to reproduce without reading the transcript. If there are no
issues, write "No issues to reproduce.">
- <issue id/title>
  Reproduce:
  1. Target: <url/env>; role/account type: <role only, no credentials>.
  2. Starting state: <data/settings/navigation state>.
  3. Steps: <click/type/navigate sequence>.
  4. Expected: <expected result>.
  5. Actual: <actual result>.
  Evidence: `qa/<file>`

## Not covered

<scenario groups from the Impact Matrix that were not exercised, plus why and the residual risk. Use
"None" only if every planned must/should scenario was covered within the action cap.>
- <scenario group> -> not covered because <blocker>; risk: <what could still regress>
- DB evidence: Not covered -> reason: <missing/skipped/unsafe access>; risk: <what data mismatch could still hide>

## How to re-run

<the exact way to reproduce this check next time, so it is repeatable>
- Suite: `.domain-agent/qa/<suite>.md`
- Command / spec: `<re-run command or qa/<suite>/<flow>.spec.ts>`
- DB checks: <named checks; read-only; connection from <env|config path>>

<!-- If the action cap was hit: list what remains so a follow-up run can continue. -->
