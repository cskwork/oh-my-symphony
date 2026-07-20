# Plan Attack - Frontier 001 Safe Jira Inbox

## Summary

The plan has the right ownership model: Jira supplies bounded source context while the file card remains the sole
workflow/evidence authority. The five-file slice is feasible, but Build must wait until the plan makes the following
security and atomicity contracts explicit.

## MUST before Build

1. **Specify one strict, intake-only JQL and pagination contract.** Existing `_jql_for_states` interpolates project and
   status strings without escaping, and `_search_paginated` returns accumulated results when `MAX_PAGES` is reached or
   a continuation token is absent (`src/symphony/trackers/jira.py:172,296-345`). Amend Steps 1-2 to require a dedicated
   literal encoder that rejects controls/empty or overlong values and escapes `\\` and `"`; no raw JQL is accepted.
   Require a non-empty, bounded status list and an exact result-key match to the configured project (for activation,
   `^A20-[1-9][0-9]*$`). The intake path must require boolean `isLast`, a new non-empty/non-repeated token while not
   last, a page/issue cap, unique issue keys, and must raise on every incomplete condition. Preserve the primary Jira
   method's current semantics because `tests/test_tracker_jira.py:179-207` asserts its max-page behavior. Add hostile
   project/status literals, missing/repeated token, missing `isLast`, duplicate key, and cap-with-zero-writes cases.

2. **Make identity and hydration validation batch-wide before the first board mutation.** `/myself` must be fetched
   even for an empty search and must contain `active is True` plus a non-empty string `accountId`; every search row
   must contain the same non-empty assignee account ID. Validate each issue key, `fields`, assignee, issue type, parent
   key, parent response, and required parent summary/description before returning the DTO batch. A valid early row
   followed by a foreign/malformed row or denied/empty parent must produce zero writes. This is necessary because the
   current normalizer silently substitutes empty fields (`src/symphony/trackers/jira.py:122-170`) and the reusable
   search fields omit both `assignee` and `parent` (`src/symphony/trackers/jira.py:46-49`).

3. **Render Jira text inert to Symphony's Markdown machines, not merely marker-escaped.** Imported text can otherwise
   forge `## Dependencies`, `## Touched Files`, acceptance, contract, RCA, or evidence sections. Those headings are
   actively interpreted by `parse_body_dependency_ids` (`src/symphony/ticket_markdown.py:16-43`), conflict parsing
   (`src/symphony/orchestrator/parsing.py:33-68`), prompt section selection
   (`src/symphony/prompt_context.py:107-191`), and stage contracts
   (`src/symphony/orchestrator/contracts.py:119-132`). Amend the renderer contract to encode marker substrings and
   prefix every source line into a non-heading form (for example, an escaped blockquote), including blank lines and
   fence-like input. Apply fixed ADF depth/node, per-field, per-card, response-byte, and total-batch limits *before*
   unbounded flatten/render work; reject oversize input with zero writes rather than truncating required parent
   context. Test forged headings, both markers, mixed-case/whitespace marker variants, fences, deep ADF, and oversize
   search/parent payloads.

4. **Strengthen path ownership and the external-source CAS contract.** `find_path` follows a canonical path first and
   otherwise returns the first matching card (`src/symphony/trackers/file.py:658-671`), so it cannot prove unique
   ownership and does not reject symlinks. `_write_ticket_with_updated_at_cas` eventually performs an unconditional
   write after repeated conflicts (`src/symphony/trackers/file.py:703-721`), which can overwrite local evidence.
   Amend Step 3 so a strict Jira-key-to-filename mapping is established before path construction; use `lstat`, reject
   dangling/live symlinks and any resolved escape, scan for all same-ID/case-colliding files, and require exactly one
   regular in-root card with exact `source.kind: jira`, `source.key`, and exactly one ordered marker pair. For this new
   path, exhausted CAS retries must raise without writing. Under the per-key lock, re-resolve/revalidate ownership,
   recompute the block replacement from the latest bytes, and skip serialization/write when unchanged. Tests must
   cover canonical and noncanonical collisions, duplicate IDs/markers, dangling and outward symlinks, a concurrent
   local edit, concurrent equal creates/refreshes, and byte/mtime/`updated_at` stability.

5. **Define no-partial-write scope and preflight the complete batch.** Fetch-then-upsert alone prevents partial network
   pages but not `valid A20-1 -> unmanaged collision A20-2`, where a sequential coordinator could write A20-1 first.
   Amend Step 3 to preflight every target's path, ownership, markers, and prospective mutation before the first write,
   under locks acquired in sorted identifier order. State explicitly that the guarantee is zero writes for config,
   identity, content, pagination, hydration, and preflight collision failures; each committed card is atomic, while a
   process/filesystem failure between distinct card renames is not a cross-file transaction. Add the two-card late
   collision test. This is the smallest honest contract supported by the existing per-ticket locks and atomic rename
   (`src/symphony/trackers/file.py:100-112,474-490,852-879`).

6. **Make failure sanitization an allowlist, including logs.** `_json_or_raise` embeds the first 200 response characters
   in exceptions (`src/symphony/trackers/jira.py:378-393`), `SymphonyError.__str__` renders all context
   (`src/symphony/errors.py:8-19`), and logging only redacts fields whose *key* is sensitive; an `error=` value is not
   redacted (`src/symphony/logging.py:15-29`). Amend Step 4 so intake maps failures to bounded stable categories plus
   optional HTTP status; neither `str(exc)` nor response bodies, URLs with query data, email, account ID, or config
   values enter logs/health. Test a 401/500 body and transport exception containing token, email, account ID, auth
   text, and markers, asserting none appears in captured logs or `/api/v1/health`. Add `jira_intake_failure` to overall
   degraded reasons while continuing `_fetch_candidates` (`src/symphony/orchestrator/core.py:2058-2072`).

## SHOULD

- Require enabled intake credentials to be `$ENV_NAME` indirections, resolved feature-locally; `ServiceConfig.raw`
  preserves literal YAML (`src/symphony/workflow/builder.py:656-679`) while the normal Jira credential resolution is
  only applied to primary tracker fields (`src/symphony/workflow/builder.py:156-167`). Never include the raw credential
  value in config errors.
- Define hot-reload health transitions: absent/disabled => `enabled: false`, `status: disabled`, no degraded reason;
  enabled success => reset failures and set `last_success`; enabled failure => retain last success and increment;
  disabling after failure clears the degraded reason. The disabled test must prove no Jira client construction or
  HTTP call and unchanged local candidate order/dispatch.
- On changed source, preserve exact bytes outside the managed block except an explicitly allowed file-board
  `updated_at` change. `parse_ticket_file` strips body-edge whitespace and `serialize_ticket` rewrites YAML
  (`src/symphony/trackers/file.py:122-154,412-438`), so the test should settle whether semantic preservation is
  sufficient or raw prefix/suffix byte preservation is required.

## Accepted

- The implementation remains feasible in the specified five files: feature-local config/coordinator in
  `jira_intake.py`; strict read DTO/fetch mode in `trackers/jira.py`; ownership-aware batch/upsert logic in
  `trackers/file.py`; poll and health state in `orchestrator/core.py`; integrated coverage in
  `tests/test_jira_intake.py`. No new shared `Issue`, error type, server route, UI file, or workflow activation is
  required.
- The hook position after `validate_for_dispatch` and before local candidate fetch is compatible with the current
  `_on_tick` flow (`src/symphony/orchestrator/core.py:1987,2051-2072`), and `/api/v1/health` already serializes
  `Orchestrator.health` without a server change (`src/symphony/orchestrator/core.py:1283-1334`).
- No-delete-on-absence, GET-only transport, exact account-ID defense in depth, managed-source metadata, and
  unchanged-payload no-op are appropriate accepted constraints.

Build gate: FAIL

## Recheck

Unresolved items: none. `PLAN.md`'s binding amendments make all six MUSTs explicit: strict intake-only JQL and
complete pagination, batch-wide identity/hydration validation, parser-inert bounded Jira content, strict
path/source ownership with non-overwriting CAS, whole-batch preflight with an honest cross-file atomicity boundary,
and allowlisted failure reporting. The three SHOULDs are also settled through env-only credentials, defined
hot-reload health/default-off behavior, and the explicit semantic-preservation contract for changed sources while
retaining exact no-op byte stability. The amended `GOAL.md`, required edge tests, five-file scope, and verification
commands make these constraints binding and feasible without scope expansion.

Build gate after amendments: PASS
