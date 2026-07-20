# PLAN - Frontier 001 Safe Jira Inbox

## Approval

- Status: auto-approved
- Record: 2026-07-20; the user explicitly directed the setup to run autonomously and to accept the generated plan.
- Blast radius: cross-module but default-off. The hard gate is cleared only for this infrastructure slice because
  disabled configuration follows the existing path and the new transport is GET-only. Future Jira issue plans are
  not pre-approved.
- Build gate: a conditional plan attack for external-source ownership, concurrency, and identity safety must pass
  before product code changes.

## Binding Plan-Attack Amendments

These constraints resolve every `MUST before Build` in `plan-attack.md`:

1. The intake path owns a dedicated JQL encoder; it does not reuse or change primary-tracker pagination semantics.
   Project and status values must be non-empty, bounded, free of control characters, and encode `\` and `"`.
   The status list is non-empty and bounded. No raw JQL is accepted. Every result key must match the configured
   project exactly (`^A20-[1-9][0-9]*$` when configured for A20).
2. Intake requires an explicit boolean `isLast`. A non-final page requires a new, non-empty, non-repeated
   `nextPageToken`. Missing/invalid flags, repeated tokens, duplicate issue keys, page cap, issue cap, response-byte
   cap, or any incomplete condition raises before writes.
3. `/myself` is fetched even for an empty search and must return `active is True` and a non-empty string
   `accountId`. The entire issue/parent batch is shape-, identity-, project-, issue-type-, and content-validated
   before a DTO batch is returned. A late invalid row or parent yields zero writes.
4. Fixed ADF depth/node, per-field, per-card, response-byte, and total-batch limits apply before unbounded flattening
   or rendering. Upstream source lines are rendered as inert quoted lines; `<` is encoded, including marker-like
   input, so source text cannot create Markdown headings, fences, dependencies, touched-file claims, contracts,
   acceptance criteria, RCA, or evidence sections. Required context is rejected, never silently truncated.
5. File intake uses a batch `upsert_external_sources` contract. It validates strict Jira-key filenames with `lstat`,
   rejects live/dangling symlinks, resolved escapes, non-regular files, duplicate/case-colliding IDs, unmanaged cards,
   mismatched `source.kind`/`source.key`, and anything other than one ordered marker pair.
6. Batch preflight acquires per-key locks in sorted identifier order, then re-reads and validates all targets before
   the first write. It computes every mutation from the latest bytes. Exhausted CAS retries raise without an
   unconditional write. Config, fetch, identity, content, hydration, pagination, and preflight failures guarantee
   zero writes. Each committed rename is atomic; a process/filesystem failure between separate card renames is
   explicitly not a cross-file transaction.
7. Intake failures are mapped to a small stable allowlist plus optional HTTP status. Neither `str(exc)` nor response
   bodies, query URLs, config values, email, account ID, token, or authorization text may enter logs or health.
   `jira_intake_failure` participates in overall degraded reasons while local candidate fetch still runs.
8. Enabled credentials must be `$ENV_NAME` indirections resolved feature-locally. Disabled/absent intake constructs
   no client and makes no request. Disabling clears intake degradation; success resets failures; failure retains
   `last_success` and increments failures.
9. A changed source update may normalize serialized frontmatter/body and change `updated_at`, but must preserve the
   semantic values of every local-owned field and all local-owned body text. An unchanged source must preserve exact
   bytes, mtime, and `updated_at`.

## Intent

Keep `tracker.kind: file` as Symphony's delivery board. Add an optional secondary Jira inbox at the start of a poll,
then run the existing local candidate fetch regardless of a retryable Jira intake failure. One Jira key maps to one
card. Jira may update only a delimited, bounded source block; all local workflow state and evidence remain untouched.

Rejected: Jira as primary tracker, raw configurable JQL, broad search followed only by client filtering, whole-body
updates, delete-on-absence, external cron, shared `Issue` expansion, and UI changes in this slice.

## Priority Rules

1. Fail closed unless project, status allowlist, current identity, result assignee, issue key, and required parent
   context are verified before any write.
2. Jira is read-only external context; the file board owns state, routing, and delivery evidence.
3. A failed or incomplete fetch never deletes cards and never blocks dispatch of existing local candidates.
4. The same source payload is byte- and mtime-stable; changed payload replaces only the managed source block.
5. No credential, identity, auth header, or unsanitized upstream body enters logs, exceptions, board content, or health.
6. Reuse the existing Jira request/pagination utilities and file-board lock, CAS, parser, serializer, and atomic writer.
7. The feature remains absent/default-off until a later config-only activation has exact A20 statuses and credentials.

## Frozen Product Scope

Exactly five product/test files may change:

1. `src/symphony/jira_intake.py` (new): parse and validate optional `ServiceConfig.raw["jira_intake"]`, render
   a neutralized bounded marker block, coordinate complete fetch-then-upsert, return a non-secret result.
2. `src/symphony/trackers/jira.py`: add an intake-only DTO and `fetch_assigned_inbox`; use the escaped fixed-form JQL
   `project = "A20" AND status in (...) AND assignee = currentUser()`, fetch `/myself`, validate exact active
   `accountId` for every result, and hydrate an empty child body from its parent. This path issues GET only.
3. `src/symphony/trackers/file.py`: add atomic batch `upsert_external_sources`; require matching source metadata, replace only
   `<!-- symphony:jira-source:start -->...<!-- symphony:jira-source:end -->`, preserve all local-owned content and
   state, reject unmanaged collisions, and skip writes for equal content.
4. `src/symphony/orchestrator/core.py`: after dispatch validation and before candidate fetch, run enabled intake;
   expose `enabled`, `status`, `last_success`, sanitized `last_error`, and `consecutive_failures` in health. Continue
   normal local dispatch when intake fails.
5. `tests/test_jira_intake.py` (new): bounded red-green coverage of HTTP, identity, parent hydration, file ownership,
   idempotence, concurrency, and health behavior.

Run-vault documentation under this directory may change as evidence is recorded. No other product file may change
without returning to Plan Approval.

## Steps

1. Write the eight named tests below and prove they fail for the missing behavior.
2. Implement the smallest Jira inbox DTO/fetch path and the binding strict JQL, identity, hydration, byte/shape limit,
   and complete-pagination contracts. Do not alter the existing primary Jira search behavior.
3. Implement the source-owned atomic batch file-board preflight/upsert using sorted existing locks, strict path/source
   ownership, CAS, and atomic serialization. Preflight the whole batch before the first write.
4. Add the default-off poll hook and sanitized health state. Intake exceptions degrade only intake health; existing
   local candidate dispatch continues.
5. Run targeted and relevant regression suites, then a fresh verifier inspects diff, behavior, and negative cases.

## Required Red Tests

- `test_jql_requires_project_status_and_current_user`
- `test_missing_or_foreign_assignee_is_rejected`
- `test_empty_subtask_hydrates_parent_summary_and_description`
- `test_two_polls_create_one_card_and_second_poll_is_byte_stable`
- `test_source_refresh_preserves_local_state_and_delivery_evidence`
- `test_unmanaged_identifier_collision_fails_closed`
- `test_unauthorized_intake_preserves_cards_and_degrades_health`
- `test_intake_http_methods_are_get_only`

Additional required edges: disabled config parity, inactive `/myself`, malformed key, denied/empty parent, page-cap
failure with zero writes, marker injection, oversize source, concurrent equal upserts, no mutation-method calls, and
sanitized errors.

Plan-attack additions: hostile project/status literals, missing/repeated pagination tokens, missing `isLast`,
duplicate/cross-project issue keys, valid-then-foreign rows, two-card late collision, canonical/noncanonical and
case-colliding IDs, duplicate markers, dangling/outward symlinks, concurrent local edits, exhausted CAS, forged
Symphony headings/fences, mixed marker variants, deep ADF, response/batch limits, and secret-bearing 401/500/transport
failures captured in both logs and health.

## Acceptance Checklist

- [ ] Every `GOAL.md` criterion is linked to an exact test or inspection artifact.
- [ ] The disabled path does not construct or call Jira intake.
- [ ] No partial fetch can produce a partial set of writes.
- [ ] No refresh overwrites local-owned fields or delivery evidence.
- [ ] No Jira method other than GET is observed by the HTTP fake.
- [ ] Existing local candidates are fetched after a simulated 401 intake failure.
- [ ] Relevant regressions pass; full `pytest` has no failure beyond the documented pre-change continuous-improvement
  E2E baseline failure, unless that unrelated baseline is repaired separately.

## Verification Commands

```bash
PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py
PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py tests/test_tracker_jira.py tests/test_tracker_jira_edges.py tests/test_tracker_file.py tests/test_orchestrator_health.py tests/test_service.py tests/test_webapi.py
PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider
../../.venv/bin/symphony doctor ./WORKFLOW.md
```

## Grounding Ledger

- Config preservation: `src/symphony/workflow/config.py:435-459` retains optional raw config.
- Poll insertion: `src/symphony/orchestrator/core.py:1987` validates dispatch before candidate fetch.
- Jira gaps/reuse: `src/symphony/trackers/jira.py:46,122,172,220,296,359,378`.
- File-board locks/upsert base: `src/symphony/trackers/file.py:508,576,673,852,909`.
- Health surface: `src/symphony/orchestrator/core.py:1283`; `/api/v1/health` is the frontier failure surface.
- Atlassian current-user JQL:
  `https://support.atlassian.com/jira-software-cloud/docs/jql-functions/`.
- Jira enhanced search and pagination:
  `https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/`.
- Authenticated user identity:
  `https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-myself/`.
- Issue/parent representation:
  `https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/`.

## Loop Contract

`max_iterations: 3`. Fresh builder and fresh verifier roles are mandatory. Failed verification writes only exact
failures and smallest fixes to `R-LOOP.md`; unresolved design expansion returns to Plan Approval.
