# Side effects - what SuperQA watches while your scenario runs

Every run attaches collectors to the whole browser context (including popups):

| type | severity | source |
|---|---|---|
| console_error | error | `console.error` on any page |
| console_warning | warning | `console.warn` |
| page_error | error | uncaught JS exception |
| request_failed | error | network request failed (DNS, refused, aborted by server) |
| http_error | warning/error | response status 4xx (warning) / 5xx (error), deduped per URL |
| dialog | info | alert/confirm/prompt/beforeunload text + auto-action taken |
| popup | info | a new tab/window opened, with its URL |
| download | info | file download started |

Each effect stores the step index it happened during, so the report ties "500 on
/api/orders" to "step 4: 주문 버튼 클릭".

## Dedupe and noise rules

- Identical effects (same type + message) within a run are ONE row with a count
  ("횟수" column). A warning spammed 20 times reads as one line, not twenty.
- Known noise is declared once and split out of the headline count, two ways:
  - per scenario: `policy.ignore_effects: ["substring", ...]`
  - per site (applies to every scenario + auto QA of that site):
    `~/.superqa/sites/<site>/ignore.yaml` - a plain YAML list of substrings.
  Ignored effects still appear in the report under "무시된 부작용" - nothing is
  deleted, only demoted. Never put a bug candidate in an ignore list.

## Visual regression (screenshot baseline)

Accept a known-good run's screenshots as the baseline, and every later run
pixel-compares each step against it:

```bash
superqa run 로그인-정상 --headless      # a run you trust
superqa baseline 로그인-정상            # accept its screenshots as baseline
# from now on, changes above policy.visual_threshold (default 1.0%)
# appear as visual_change effects with a red-overlay step-NN-diff.png
```

Practical rules:

- Capture the baseline in the SAME mode you run in (headless baseline for
  headless runs) - renderer differences otherwise inflate percentages.
- Dynamic pages (animations, dates, dashboards) produce small persistent diffs;
  raise `policy.visual_threshold` (e.g. 5.0) for those scenarios, or
  re-baseline after intentional UI changes.
- The diff image shows exactly WHERE it changed - attach it when reporting
  layout regressions.
- Trace on failure: any failing run also saves `trace.zip`
  (`npx playwright show-trace trace.zip` replays the failure).

## Run-to-run comparison (automatic)

Every run stores a summary; the next run of the same scenario prints and embeds a
diff: new failures, resolved failures, newly appeared / disappeared side-effect
types (messages are digit-normalized so changing ids do not churn). "지난 실행과
동일합니다 - 회귀 없음" is the clean verdict a developer wants after shipping.

## Triage rules (agent duty, in the user's report summary)

1. **Bug candidate** - page_error, request_failed, http_error 5xx, console_error that
   appears when a step interacts with the feature under test. Lead with these.
2. **Known behavior** - dialogs/popups the scenario expected (`expect_popup`, dialog
   steps) or that site rules list as normal. Mention only in the detail table.
3. **Environment noise** - third-party analytics failures, ad blockers, dev-only
   warnings. Group into one line; do not bury real findings under them.

Never delete effects from a report. Triage happens in the summary text, the raw table
stays complete - that is the audit trail.

## Making effects fail a run

Default: effects are recorded, steps decide pass/fail. For strict gates set in the
scenario:

```yaml
policy:
  fail_on_console_error: true   # console_error/page_error fail the run
  fail_on_http_error: true      # http_error/request_failed fail the run
```

Use strict mode for smoke scenarios on staging; keep it off on noisy prod pages.
