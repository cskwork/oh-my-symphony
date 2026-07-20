# Frontier 001 exploration — safe Jira inbox

## Decision

Keep `tracker.kind: file` as the delivery board and add a secondary, read-only Jira intake before the
normal file-board candidate fetch. One Jira key owns one local card; Jira owns only a delimited source
block, while the local state and all delivery evidence remain local-owned.

Proposed optional config is a top-level `jira_intake` map (`enabled`, `endpoint`, `project_key`,
`actionable_states`, `assignee: currentUser()`, `initial_state`, `email`, `api_key`). Parse it into a small
feature-local frozen config from `ServiceConfig.raw`; resolve credentials from environment indirection.
Do not enable the block in `WORKFLOW.md` until the real A20 status allowlist and unattended credentials
are known.

## Current call paths and symbols

- Poll: `Orchestrator.start` -> `_tick_loop` -> `_on_tick` -> `validate_for_dispatch` ->
  `_fetch_candidates` -> `_tracker_call_candidates` -> `build_tracker_client`. The factory selects exactly
  one adapter from `cfg.tracker.kind`; the current workflow selects `FileBoardTracker`.
- Jira read: `JiraClient.fetch_candidate_issues` -> `_jql_for_states` -> `_search_paginated` ->
  `_normalize_issue`. JQL currently scopes only project/status. `_SEARCH_FIELDS` omits `assignee` and
  `parent`; `_normalize_issue` has no assignee/parent output. `update_state` and `append_note` are mutating
  methods and must never be reachable from intake.
- File board: `FileBoardTracker.create` rejects an existing identifier; `update_fields` replaces the entire
  body. `_mutate_ticket`, `_exclusive_lock`, `_write_ticket_with_updated_at_cas`, and
  `write_ticket_atomic` already provide the lock/CAS/atomic-write primitives needed for an upsert.
- Failure surface: `Orchestrator.health` exposes primary tracker fetch counters at `/api/v1/health`;
  `_health_summary` is included in `/api/v1/state`. `/api/v1/board` and the SPA do not currently expose a
  global health error.
- Config: `build_service_config` supplies `$JIRA_EMAIL`/`$JIRA_API_TOKEN` defaults only when Jira is the
  primary tracker. `ServiceConfig.raw` preserves an optional secondary source without widening the primary
  `TrackerConfig` contract.

## Exact gaps

1. No secondary-source hook exists; changing the primary tracker to Jira loses the local delivery lanes and
   lets worker state/note calls write Jira.
2. The current JQL has no assignee clause, and the response is not independently checked. A faulty search
   response can therefore admit another user's issue.
3. Subtask parent data is neither requested nor normalized. `issuetype` is requested but unused.
4. There is no source-owned partial-body upsert. `create` is create-only and `update_fields(description=...)`
   would erase plans, QA, reviews, and other local evidence.
5. Jira failure currently either affects the primary tracker or is invisible. Secondary intake must fail
   independently so existing local cards still dispatch, while health records a retryable failure.
6. The checked-out worktree has no `kanban/` despite `board_root: ./kanban`; no real card format sample was
   available. Existing parser/serializer tests are the format evidence.
7. Literal HTML dashboard failure display is outside the five-file slice below: the SPA never fetches
   `/api/v1/health`. Either interpret this ticket's dashboard criterion as the health API, move the banner to
   frontier 005, or allow a sixth product file (`src/symphony/web/static/app.js`) plus browser/static proof.

## Reusable utilities

- Jira: `_flatten_adf`, `_normalize_issue`, `_request`, `_json_or_raise`, pagination constants, and
  `send_with_retry`. Add a dedicated read-only result DTO rather than widening shared `Issue` and every
  reconstruction site.
- File board: `parse_ticket_file`, `serialize_ticket`, `find_path`, ticket locks, CAS mutation, and atomic
  replace. Expose one public `upsert_external_source` method over these existing internals.
- Runtime: the tick's `asyncio.to_thread` pattern, `request_refresh` coalescing, health counters, and existing
  `/api/v1/health` route.
- Ordering/model: Jira key as the canonical local identifier (`A20-1188`), existing priority/label/ADF
  normalization, and registration-order sorting.

## Recommended minimal slice (5 files)

1. `src/symphony/jira_intake.py` (new): parse/validate the optional secondary config, render the managed
   source block, coordinate fetch-then-upsert, and return a non-secret sync result.
2. `src/symphony/trackers/jira.py`: add an intake-only DTO and `fetch_assigned_inbox`; JQL must be
   `project = "A20" AND status in (...) AND assignee = currentUser()`. Fetch `/myself`, compare its exact
   `accountId` to every returned `fields.assignee.accountId`, and fetch parent summary/description only when
   the subtask body is empty. This path issues GETs only.
3. `src/symphony/trackers/file.py`: add atomic `upsert_external_source`. Create by Jira key; on refresh,
   require matching `source.kind/key`, replace only
   `<!-- symphony:jira-source:start -->...<!-- symphony:jira-source:end -->`, preserve local state and text
   outside the markers, and perform no write when content is unchanged.
4. `src/symphony/orchestrator/core.py`: call intake after dispatch validation and before file candidates;
   swallow intake transport/auth failure only after recording `enabled/status/last_success/last_error/
   consecutive_failures` in health. Continue the local candidate fetch on failure so existing cards remain
   usable and newly imported cards are eligible in the same successful tick.
5. `tests/test_jira_intake.py` (new): HTTP mock, file-board integration, two-poll idempotence, concurrency,
   and orchestrator health cases in one bounded test file.

Activation in `WORKFLOW.md` is intentionally not in this slice: exact statuses and credentials are unresolved.
Once supplied, activation is a separate config-only operation followed by `symphony doctor ./WORKFLOW.md`.

## Red/green proof

Write these failing tests first:

- `test_jql_requires_project_status_and_current_user`
- `test_missing_or_foreign_assignee_is_rejected`
- `test_empty_subtask_hydrates_parent_summary_and_description`
- `test_two_polls_create_one_card_and_second_poll_is_byte_stable`
- `test_source_refresh_preserves_local_state_and_delivery_evidence`
- `test_unmanaged_identifier_collision_fails_closed`
- `test_unauthorized_intake_preserves_cards_and_degrades_health`
- `test_intake_http_methods_are_get_only`

Commands:

```bash
PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py
PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider \
  tests/test_jira_intake.py tests/test_tracker_jira.py tests/test_tracker_jira_edges.py \
  tests/test_tracker_file.py tests/test_orchestrator_health.py tests/test_service.py tests/test_webapi.py
PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider
symphony doctor ./WORKFLOW.md
```

Baseline evidence: the existing Jira and file tracker suites pass (`105 passed in 5.01s`). Health/web API
collection is currently blocked because the shared `../../.venv` lacks `ruamel.yaml`; the system Python also
lacks project dependencies. Install the repo's dev extras before claiming those regression commands green.

## Edge and security cases

- Accept only the enumerated `currentUser()` assignee mode in this slice; do not accept arbitrary raw JQL.
  Require a non-empty status allowlist and validate/escape the project key and every status literal.
- Fail closed on missing/inactive `/myself`, missing assignee/account ID, foreign assignee, malformed key,
  parent fetch denial, or empty required parent context. Fetch/validate a page set before writing any card.
- A max-page cap or missing continuation token is degraded/incomplete, not success. Never delete local cards
  because a Jira result disappeared, was reassigned, or the server failed.
- Reject an existing same-key card without matching Jira source metadata. Never change its local state.
- Bound rendered source size, neutralize marker text arriving from Jira, reject symlink/path escapes, and do
  not log tokens, auth headers, email, account IDs, or unsanitized response bodies.
- Preserve local labels/routing and all text outside the managed block. A no-change poll must preserve bytes,
  mtime, and `updated_at`; a changed Jira source updates only source-owned fields/block.
- Tests must record every HTTP method and prove no POST/PUT/PATCH/DELETE and no call to
  `JiraClient.update_state`/`append_note`.

## Alternatives rejected

- Jira as primary tracker: incompatible stage model and exposes Jira mutation methods to workers.
- Broad search plus post-filter only: over-fetches other assignees and does not satisfy defense in depth.
- Full-body `update_fields`: destroys local delivery evidence; append-on-poll grows duplicates forever.
- Delete cards absent from a later search: an outage/reassignment could erase in-flight work.
- Raw configurable JQL: makes scope widening/injection easy and weakens the assigned-only proof.
- Expanding shared `Issue` with intake-only identity/parent fields: forces unrelated adapters, snapshots, and
  many `Issue(...)` reconstruction sites to change.
- External cron/webhook: duplicates service lifecycle/health/locking; webhook also adds public infrastructure
  without removing the authentication requirement.

## Blockers

- No unattended Atlassian authentication is configured (`JIRA_EMAIL` and `JIRA_API_TOKEN` remain external).
- The exact actionable A20 status allowlist is not known; it must not be guessed or hardcoded.
- Clarify whether “dashboard surfaces failure” means `/api/v1/health` for frontier 001 or a visible SPA banner.
  The latter exceeds the requested five-file slice and should be paired with frontier 005/browser proof.
