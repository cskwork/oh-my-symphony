# Frontier 002 - Symphony routing integration

## Decision

Add a default-off, fail-closed AIDT routing pass after Jira intake and before local candidate fetch. Routing reads
only the managed Jira source snapshot, validates a configured service catalog against the configured AIDT root, and
stores an owned `routing` frontmatter block. Ambiguous or invalid cards move to Human Review. A global routing
configuration/runtime failure stops that tick before dispatch.

Frontier 002 must also enrich the Jira snapshot with `components`, `status`, `priority`, `updated`, and `url`.
Components and revision are required by routing; status, priority, and URL share the same bounded DTO/search/render
surface and close the root card-completeness gap without another Jira schema rewrite. Status and priority are display
context only and cannot increase a service score.

## Current seams

- `JiraInboxIssue` and `_INTAKE_SEARCH_FIELDS` currently omit component/status/priority/updated data
  (`trackers/jira.py:53,68-78`).
- `ExternalSourceUpdate` owns a safe, atomic batch boundary, but its `source` frontmatter currently stores only
  `kind/key` (`trackers/file.py:111-126,669-838`).
- Unknown frontmatter is serialization-stable, while `Issue` intentionally exposes only generic dispatch fields
  (`trackers/file.py:354-385,456-485`; `issue.py:17-54`). Routing need not widen `Issue`.
- The tick already runs Jira intake after dispatch validation and before candidate fetch
  (`orchestrator/core.py:2098-2108`). The route hook belongs immediately after intake.
- Generic `create` and `update_fields` are whole-card operations; route-owned coordinator/child refresh needs a
  stricter batch API rather than those methods (`trackers/file.py:994-1137`).

## Default-off configuration

Parse `ServiceConfig.raw["aidt_routing"]` feature-locally:

- `enabled`, `aidt_root`, `minimum_confidence` (must equal 90 in this frontier), and explicit
  `ready_state`/`review_state`/`coordinator_state`.
- A bounded `services` list. Each entry has canonical `id`, relative `checkout`, explicit `kind`
  (`backend/frontend`), build/package `markers`, component aliases, context paths, optional verified route anchors,
  domain anchors, and branch templates.
- Cards and Jira text never supply checkout paths, commands, regexes, or arbitrary catalog IDs.

Enabled validation resolves the root once; rejects symlink/path escapes, duplicate/case-colliding IDs or aliases,
absolute/traversal checkout paths, missing/disabled checkout, invalid marker/anchor paths, unknown kinds, unbounded
entries, and dirty/non-40-hex Git revision output. Git reads use argv, no shell, a timeout, bounded output, and an
injectable revision reader for tests.

## Source snapshot

Extend the intake DTO and GET field allowlist with bounded, type-checked:

- issue `components`, `status.name`, `priority.name` or null, `updated`, issue URL, and existing issue type/parent;
- optional parent components as supporting parent evidence.

Compute a deterministic SHA-256 `source.revision` from the validated normalized DTO, not from Jira `updated` alone.
Persist an allowlisted source map (`kind/key/revision/url/status/priority/issue_type/components/updated/parent_key`)
and render the same human context inside the inert marker block. Refresh changes only source-owned metadata/block,
preserves routing/local evidence, and makes a prior route stale by revision comparison.

## Routing decision

For each Jira-managed coordinator without a current route fingerprint:

1. Read only its exact managed source block and allowlisted source map.
2. Validate the catalog and checkout revision before scoring.
3. Extract exact component aliases, context paths/endpoints, configured route anchors, parent contract, explicit
   backend/frontend scope, and bounded supporting terms. Verify route/domain anchor files and configured literal
   symbols in the current checkout.
4. Score each service independently using the grounded AIDT table: component 45, context 30, code anchor 35,
   service-owned domain 15, parent contract 10, kind agreement 5, supporting terms 5 total; cap at 100 and deduplicate
   an evidence source.
5. Require at least 90, at least two independent authoritative categories, and component or code-symbol authority.
   Any direct conflict, unknown/absent checkout, missing revision, or score tie fails closed.

Keyword/domain/supporting terms never route alone. Runtime dependencies/consumers are recorded as supporting services
and do not create children without an independent change anchor.

## Card ownership and atomicity

Add a file-tracker batch API that locks the coordinator plus deterministic child IDs in sorted order, re-reads every
card, and preflights all paths/ownership/revisions before the first write.

- Single route: preserve coordinator body/local fields; write only `routing` with role `single`, catalog fingerprint,
  source revision, service ID/relative checkout/kind, 40-hex checkout revision, branch prefix, confidence, bounded
  evidence, supporting services, and decision timestamp. Move to configured ready state.
- Ambiguous/invalid: write role `coordinator`, status/reason/candidate scores/recheck requirements; move to Human
  Review. Never delete or guess.
- Multi-service: coordinator records deterministic children and moves to coordinator state. Create one child
  `<JIRA-KEY>--<service-id>` per independently authoritative service. Child source is
  `aidt-route-child` keyed by coordinator/service, owns one bounded route-slice marker/frontmatter block, records its
  coordinator, source/catalog/checkout revisions and branch prefix, and starts in ready state.
- Equal decisions are byte/mtime stable. Refresh may change only route-owned fields/marker text; it preserves child
  local state, notes, plans, QA, and evidence. Unmanaged/case/symlink/duplicate child collisions, source drift, or
  exhausted CAS fail before writes. A removed service never deletes an in-flight child; it blocks for review.

## Hook and health

`_poll_aidt_routing` runs immediately after `_poll_jira_intake`. Absent/disabled routing constructs nothing and
preserves current behavior. Success records routed/review/child counts and last success. Failure stores only a stable
allowlisted category and optional service/card ID, adds `aidt_routing_failure` to health, and returns from the tick
before candidate fetch. Per-card ambiguity is a successful fail-closed decision, not a global error.

## Minimal product/test scope

1. New `src/symphony/aidt_routing.py`: config/catalog validation, evidence extraction/scoring, route fingerprints,
   checkout revision reader, decision/child specifications, coordinator.
2. `src/symphony/trackers/jira.py`: bounded source-snapshot fields and normalization.
3. `src/symphony/jira_intake.py`: source revision, inert rendering, and structured source update.
4. `src/symphony/trackers/file.py`: source metadata refresh plus atomic/idempotent route coordinator-child apply.
5. `src/symphony/orchestrator/core.py`: default-off hook and sanitized health/fail-closed tick behavior.
6. New `tests/test_aidt_routing.py`: evaluator-style routing/file/orchestrator integration fixtures.

No `Issue`, shared workflow model, WORKFLOW profile, UI/TUI, worktree, prompt/stage, merge, Jenkins, or live Jira
change belongs to this frontier.

## Required red tests

- `test_a20_1188_fixture_routes_viewer_api_at_95_without_lms_children`
- `test_component_context_and_code_anchor_produce_deduplicated_authoritative_score`
- `test_keywords_parent_only_conflict_tie_and_below_90_fail_to_human_review`
- `test_missing_checkout_symlink_escape_and_revision_failure_stop_dispatch`
- `test_source_snapshot_includes_status_priority_components_url_and_stable_revision`
- `test_source_revision_change_recomputes_route_without_overwriting_local_evidence`
- `test_multi_service_route_creates_one_idempotent_owned_child_per_checkout`
- `test_unmanaged_child_late_collision_produces_zero_batch_writes`
- `test_reroute_never_deletes_or_resets_an_in_flight_child`
- `test_disabled_routing_constructs_nothing_and_preserves_candidate_order`
- `test_routing_failure_degrades_health_and_skips_candidate_fetch`
- `test_route_errors_and_git_failures_do_not_leak_paths_output_or_source_text`

Additional edges: catalog/alias/path case collisions, absolute/traversal paths, marker/symbol size limits, malicious
Jira components/endpoints, duplicate evidence, consumer-only service, Bug/frontend branch templates, stale catalog
fingerprint, concurrent local note, child source mismatch, old timestamps, and byte-stable repeat polls.

## Blockers and deferrals

Fixture proof is unblocked. Live activation still needs Jira component/status discovery and an approved catalog/profile.
Frontier 003 owns actual service worktrees and base refs. Frontier 004 owns plan/stage/dispatch evidence gates.
Status/priority are stored for completeness but do not authorize routing or delivery.
