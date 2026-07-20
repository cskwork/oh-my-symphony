# PLAN - Frontier 002 AIDT Routing Contract

## Approval

- Status: auto-approved
- Record: 2026-07-20; the user explicitly authorized the generated infrastructure plan and autonomous supergoal loop.
- Blast radius: cross-module but default-off. No active profile contains `aidt_routing`, so current runtime behavior
  remains unchanged until a later reviewed activation.
- Build gate: a conditional plan attack for checkout trust, hostile evidence, multi-card ownership, confidence
  manipulation, source drift, concurrency, and fail-closed dispatch must pass before product changes.

## Binding Plan-Attack Amendments

These clauses resolve every `MUST before Build` in `plan-attack.md` and override earlier ambiguous wording:

1. **Closed schema and limits.** Only `enabled` defaults false; absent/exact false ignores siblings and constructs
   nothing. Enabled routing requires `source_mode` (`same_tick_jira` or `static_snapshot`), absolute `aidt_root`,
   `minimum_confidence: 90`, three `states`, and `services`; recursively reject unknown keys and non-exact bool/int
   types. IDs are lowercase ASCII `[a-z0-9]+(?:-[a-z0-9]+)*`, 1-48 bytes. NFC+casefold IDs, aliases, checkout names,
   or resolved checkout collisions fail. Caps: 64 services; 32 aliases/service; 16 marker/context/route/domain
   anchors per category; 256 bytes per path/literal; 32 evidence records/candidate. Checkout is one relative segment.
   Disabled service IDs remain known but never score or pass.
2. **Checked-in anchors.** Context anchor is exactly `{id,file,literal}`; route anchor is
   `{id,file,method,endpoint,symbols}`; domain anchor is `{id,file,terms}`. Matches are literal. Markers prove
   repository shape and never score. Jira values never become a path, regex, symbol, service ID, ref, or command
   argument.
3. **Filesystem observation.** The root, checkout, `.git` entry, resolved Git directory, and common Git directory
   are the filesystem trust boundary. They must be absolute/existing and have no symlink component; capture and
   recheck their identity after observation and in route batch preflight. Marker/anchor working-tree paths are never
   opened. Their trusted shape and bytes come only from immutable Git objects. Any repository identity swap is global
   `path_changed` before commit. Frontier 003 must reverify the stored commit before creating a worktree because later
   external mutation remains a residual boundary.
4. **Git observation.** Use fixed argv only: `git --no-optional-locks -C <checkout>` with fixed subcommands to
   (a) resolve exactly `refs/remotes/origin/aidt-prd^{commit}`, (b) assert `--show-toplevel` equals the trusted
   checkout, (c) assert object format `sha1`, (d) query each marker/scoring path with exact
   `ls-tree -z --full-tree <sha> -- <path>`, and (e) read scoring bytes with `cat-file blob <blob-id>`. Accept only
   exact regular modes `100644`/`100755`, type `blob`, exact requested paths, lowercase 40-hex IDs, bounded strict
   UTF-8 scoring blobs, and one unchanging base-ref commit. Reject symlink/tree/submodule/extra/truncated/malformed
   entries. Sanitize environment, disable replace/object-directory overrides, bound binary output/time, and never log
   output. Working-tree staged/unstaged/untracked/ignored state is permitted and ignored; absent/moved refs,
   non-SHA-1/moved repos, and object/ref drift fail globally.
5. **Scoring map.** Award each service/category once: component 45, context 30, code 35, domain 15, parent 10,
   kind 5, supporting 5 total. Only component/context/code are authoritative. Sort evidence by
   service/category/normalized-source/anchor-ID. Components come only from structured components. Endpoints come only
   from bounded structured summary/description/parent text using ASCII method/path tokens without decode/normalize.
   Component A versus unique context/code B is a conflict. Multi-route requires a disjoint explicit change anchor
   (component or exact affected code contract) for every passer and no reused evidence; otherwise it is a tie/review.
6. **Structured source.** Routing never parses the body marker. `source` stores normalized
   summary/description/components/status/nullable priority/issue_type/updated/url and nullable complete parent
   `{key,summary,description,components}`. Fetch parent whenever Jira records it, including a non-empty child. Reject
   controls, malformed timestamp/nesting, duplicate casefold components, and size/count violations as one zero-write
   Jira batch. The marker is inert display only.
7. **Canonical fingerprints.** Source/catalog/route fingerprints use version-tagged UTF-8 canonical JSON
   (`sort_keys=True`, compact separators, no NaN). Source revision includes every source field. Catalog revision
   includes full normalized enabled/disabled definitions, excluding list order and absolute root spelling. Route
   fingerprint includes source/catalog revisions, an immutable-object trust schema tag, the fixed base-ref name,
   sorted service-to-base-ref commits, and semantic decision, excluding timestamps/evidence order. Equal fingerprints
   preserve decision timestamp and bytes. Record checkout/Git identity separately from commit.
8. **Child identity.** Child file/id/identifier is `<JIRA-KEY>--<service-id>`; source is exactly
   `{kind: aidt-route-child,key: <KEY>::<service>,coordinator: <KEY>,service: <service>}`. Reject case/path/source
   disagreement, reparenting, child listed by two coordinators, and duplicate resolved checkout. Lock current,
   retained, and desired child IDs so catalog removal cannot hide an old child.
9. **Honest batch boundary.** Under all sorted locks, re-read every card/source revision and recheck path identities
   before commit. Commit children sorted and coordinator last; every artifact carries the desired fingerprint.
   Validation/cooperative precommit mismatch is zero-write. Failure/noncooperative mismatch after a rename becomes
   `partial_apply`, stops dispatch, never rolls back/deletes, and is repaired idempotently on the next poll.
10. **Local ownership.** Intake writes only `source` plus its marker. Routing writes only `routing` plus its marker and
    an initial state transition while state remains route-owned. Neither overwrites local title/priority/URL/labels/
    timestamps/unknown frontmatter/body outside markers. Manual/in-flight states persist. Removed children remain in
    `routing.retained_children`, become stale, force review, and are never reset/deleted.
11. **Dispatch barrier and intake coupling.** Routing returns `global_allow_dispatch` and bounded
    `blocked_identifiers`. Global failure stops before fetch. After successful fetch, filter every route-managed
    coordinator/ready/stale/review/partial/retained child; until Frontier 003, all enabled routed IDs are blocked while
    unrelated local candidates remain. `same_tick_jira` requires Jira intake success on that tick; `static_snapshot`
    requires Jira intake explicitly disabled. Jira intake polling returns bounded disabled/success/failure status.
12. **Hot reload/health.** Initial state is disabled/null/zero. Disable clears error/failure/counts without readers.
    Success/review resets failure and updates success. Global failure retains success, increments failure, exposes an
    allowlisted category plus validated canonical card/service ID only, and skips fetch. Re-enable rebuilds all
    observations. A workflow reload error with last-good routing enabled stops the tick and records sanitized
    `workflow_reload_error`; last-good disabled retains generic behavior.

## Iteration 2 Binding Amendments

These clauses resolve the immutable-object recheck and override any conflicting six-file, text-runner, per-card
commit, live-Jira-default, or flat-module wording above.

1. **Hard-bounded binary Git protocol.** `GitRunner` accepts fixed argv, sanitized environment, timeout,
   `stdout_cap`, and `stderr_cap`, and returns a structured result containing integer return code, raw stdout/stderr
   bytes, timeout, and overflow flags. The production runner streams both channels, kills/reaps the process when a
   channel crosses its command-specific cap, and never buffers unbounded output. Scalar output is one exact ASCII
   token plus one optional LF; tree output is exactly one
   `mode SP type SP 40-lower-hex TAB requested-path NUL` record with no prefix/suffix/second record; blob bytes are
   capped before strict UTF-8 decode. Nonzero exit, timeout, overflow, unexpected stderr, missing/final NUL, wrong
   path/mode/type/OID, boundary+1, or invalid UTF-8 fails closed. Catalog Git paths reject NUL, controls, backslash,
   traversal, and byte ambiguity. Fixed Git global options precede `-C`; no synthetic `--` precedes a subcommand.
   Routing never invokes `status`, `diff`, `ls-files`, `hash-object`, or a working-tree opener.
2. **Repository/object trust.** Support only a non-symlink `.git` directory or a bounded regular `gitdir:` file read
   without following symlinks. Resolve Git/common directories absolutely; reject symlinks in all ancestors; capture
   and recheck the `.git` entry, gitfile target, Git directory, common directory, and checkout identities. Start Git
   from an allowlisted environment after removing inherited `GIT_*`; set `GIT_CONFIG_NOSYSTEM=1`, point global
   config to `os.devnull`, disable prompts, replace objects, optional locks, and lazy promisor fetch, and pass exact
   config overrides preventing protocol/partial-clone behavior. Reject repository `objects/info/alternates`, replace
   refs as evidence, missing promisor objects, unsupported object formats, or an identity outside the captured trust
   roots. This frontier never fetches or uses network.
3. **One locked whole-poll batch.** Bound and scan the complete eligible Jira coordinator set, observe all enabled
   services once, resolve every card, and construct one route batch covering all coordinators plus current/retained/
   desired children. Under all sorted card locks, re-read every source/ownership record and recheck every root,
   checkout, `.git`, Git/common-directory identity, top-level, SHA-1 format, fixed-ref SHA, and commit existence
   immediately before the first rename. Any pre-first-rename drift is zero-write. Commit every child sorted, then
   every coordinator sorted. Any failure after the first rename is `partial_apply`, blocks the whole tick, preserves
   artifacts, and is repaired by the next whole-poll plan. A ref move after final preflight remains Frontier 003's
   fresh-fetch equality boundary.
4. **Durable object-backed handoff.** Exact schema tags are `aidt-git-object-v1`, `aidt-catalog-object-v2`, and
   `aidt-route-object-v2`; old/unknown/`aidt-route-v1` routes are stale and recomputed. Equal-write skipping requires
   both semantic fingerprint and `repository_binding_digest`, where the digest covers canonical service/checkout,
   fixed ref, commit, and opaque checkout/Git/common-directory identity tokens but never absolute paths or raw stat
   values. Coordinator routing stores the sorted revision/binding map for all enabled observed services. Each selected
   child stores only its exact service, fixed ref, commit, binding digest, coordinator fingerprint, and service route
   slice. Every successful decision is `pending_fresh_base_equality` and remains dispatch-blocked. Frontier 003 may
   release only an exact current child after a fresh `aidt-prd` fetch matches its schema/ref/service/commit/binding;
   mismatch is bounded `route_base_mismatch`, preserves state, and requests rerouting.
5. **Dirty-state non-interference proof.** Routing permits staged, unstaged, untracked, ignored, and working-tree
   symlink changes because it never reads or writes them. Tests preserve and compare `.git/index` bytes/mtime,
   symbolic HEAD/ref, tracked anchor bytes/mtime, staged content, untracked/ignored files, and working-tree-only
   marker/anchor symlinks before/after routing; decision/evidence/SHA/fingerprint must equal the clean immutable-base
   observation. A committed mode `120000`, tree, or `160000` at a configured marker/anchor fails globally.
6. **Strict Jira wire, compatible DTO.** Live requested `components`, `status`, `priority`, and `updated` fields are
   mandatory (`priority` value may be null); hydrated parent `components` is mandatory. Require expected container
   shapes and bounded allowlisted `name` values while ignoring unrelated nested Jira transport keys. Missing,
   wrong-type, control, oversize, or duplicate casefold data rejects the complete intake batch with zero writes.
   Backward-compatible defaults exist only on direct `JiraInboxIssue` construction and never normalize missing live
   fields to empty/Unknown/epoch. Existing Jira HTTP fixtures are updated within the approved test scope.
7. **Cohesive package and tests.** Replace the flat prototype with a package facade plus `contract.py`,
   `git_objects.py`, `decision.py`, and `runtime.py`; extract AIDT persistence to
   `trackers/aidt_routes.py`. The facade preserves only the frozen public names. Dependency direction is
   `contract <- git_objects/decision <- runtime -> trackers.aidt_routes -> trackers.file`; core consumes only the
   facade. Split tests into support/contract/Git-object/decision/storage/runtime modules. No changed/new function
   exceeds 50 lines or nesting four. No compressed semicolon/inline suites remain. This is feature separation, not a
   generic transaction framework or unrelated refactor.
8. **Sanitized public errors.** The exact public categories are `config_invalid`, `source_mode_invalid`,
   `intake_unavailable`, `catalog_invalid`, `repository_invalid`, `repository_changed`, `git_timeout`,
   `git_output_limit`, `git_command_failed`, `git_protocol_invalid`, `git_object_invalid`, `revision_changed`,
   `source_invalid`, `source_drift`, `route_collision`, `batch_limit`, `preflight_changed`, `partial_apply`,
   `workflow_reload_error`, and `internal_error`. Only repository/Git/revision categories may carry a validated
   `service:<canonical-id>` ref; only source/collision/preflight/partial categories may carry a validated
   `card:<canonical-key>` ref; every other category has no ref. Result repr, health, and structured logs contain only
   event/category/allowed ref/counts. They never contain causes, argv, paths, object bytes, stdout/stderr, source or
   payload text, environment values, or exception strings.

Named caps and boundary/boundary+1 tests are binding: Git token stdout 128 bytes, Git path stdout 4,096 bytes,
one tree record 1,024 bytes, one blob stdout 1,048,576 bytes, stderr 8,192 bytes, 64 configured Git paths per service,
4,194,304 decoded object bytes per service, 16,777,216 bytes for the whole observation, 500 coordinators, 2,000
children, and 10,485,760 serialized bytes for the whole route batch. A real local temporary Git fixture covers
unrelated `HEAD`, dirty state, stable then moved production ref, reroute, and the future exact-equality handoff
without network.

All caps are named constants with boundary/boundary+1 tests. Clock, binary-safe Git runner, repository identity probe,
and per-rename fault hook are injectable. The A20-1188/A20-1186 mismatch is evidence only and never scores.

## Theory

A Jira issue names business work, while a worker edits one concrete repository revision. The routing contract bridges
those models. It proves service ownership from component/context/code/domain evidence stored in the immutable local
`refs/remotes/origin/aidt-prd` commit, records that exact checkout revision, and represents multi-repository scope as
a coordinator plus one child per repository. The user's current branch, index, and working-tree files are not routing
evidence. A routing score is not authorization when identity, path, revision, or ownership is uncertain.

For A20-1188, current code grounds `GET /v-api/ailearning/{aiLrnNo}` in
`aidt-viewer-api/.../MathAILearningCenterController.getMathAILearningCenter` and its service/DAO chain. LMS tables and
consumers are dependencies, not change ownership. The route is `viewer-api` at 95; frontend navigation, if separately
in scope, is a future `viewer-web` child.

## Priority Rules

1. Explicit, current code ownership outranks prose, keywords, dependencies, and consumers.
2. A card can reference only a canonical catalog service; paths and commands never come from Jira/card text.
3. Checkout/Git identity, regular marker/anchor blobs in fixed `refs/remotes/origin/aidt-prd`, in-root resolution,
   and a full Git revision are hard gates before scoring; working-tree dirtiness is preserved and ignored.
4. Require >=90 plus two independent authoritative categories and component or verified code authority.
5. Any direct conflict/tie/unknown/disabled service fails closed to Human Review.
6. One independently required repository maps to one child lifecycle; consumers do not create children by themselves.
7. Route/source ownership is partial and idempotent; local state/evidence is never replaced by a refresh.
8. Validation/preflight failure guarantees zero route-owned writes; separate atomic renames are not a cross-file
   filesystem transaction.
9. Routing failure blocks candidate fetch; ambiguity is recorded as a successful review decision.
10. Logs/health expose allowlisted categories and canonical IDs only, never source text, checkout paths, Git output,
    environment values, or exception strings.

## Frozen Configuration Contract

Parse only optional `ServiceConfig.raw["aidt_routing"]`:

- `enabled` boolean; disabled/absent returns immediately.
- `source_mode`, absolute `aidt_root`, and `minimum_confidence` exactly integer 90.
- `states.ready`, `states.review`, `states.coordinator` non-empty bounded strings.
- bounded `services` list with canonical `id`, one relative top-level `checkout`, `kind` backend/frontend,
  `enabled`, regular-blob marker paths, component aliases, context anchors, route anchors, and domain anchors.
- Context/route/domain anchors use only the exact closed shapes frozen above.
- Domain anchor: relative regular file and bounded literal terms used only when source context also matches.

No raw regex, shell command, bare context string, absolute checkout, branch template, or arbitrary environment
expansion is accepted.
Branch prefixes are derived: Bug -> fix, otherwise feat; backend `{feat|fix}/{KEY}`, frontend
`csk-{feat|fix}/{KEY}`.

## Confidence Contract

Score per canonical service, deduplicated by evidence category/source, cap 100:

| Evidence | Weight | Authority |
|---|---:|---|
| exact Jira component/catalog alias | 45 | authoritative |
| unique verified context/base path | 30 | authoritative |
| exact verified route/code symbol | 35 | authoritative |
| service-owned domain anchor matching source | 15 | corroborating |
| parent exact endpoint/contract | 10 | corroborating |
| explicit backend/frontend scope agrees | 5 | supporting |
| keywords/dependency/consumer text | 5 total | supporting |

A frozen route needs >=90, two authoritative categories, and component or code-symbol evidence. A component conflicting
with code/context blocks. Multiple >=90 services create children only when each has an independent change anchor.

## Source Snapshot Contract

Extend `JiraInboxIssue` and the intake field allowlist/normalizer with:

- issue components, status name, nullable priority name, Jira `updated`, browse URL, and existing issue type;
- parent key/summary/description and optional parent components.

All values are type/size/count bounded and batch-validated before any file write. Always hydrate a recorded parent.
Compute `source.revision` as SHA-256 over canonical normalized structured fields including descriptions.
`ExternalSourceUpdate` carries the allowlisted source map. File refresh
updates only that map and the existing inert Jira marker block; local priority/state/labels/routing/text remain owned
locally. Equal snapshots remain byte/mtime stable.

## Routing/Card Contract

New route coordinator reads only regular Jira-managed cards and structured `source`; the body marker is display-only.
It validates the full catalog/current checkout observations, computes canonical fingerprints, skips an equal route
fingerprint, and emits a complete batch of routing mutations.

Coordinator `routing` frontmatter owns: schema, role/status, source/catalog/checkout revisions, canonical service or
candidate list, kind, relative checkout, derived branch prefix, confidence, bounded evidence, supporting services,
children, recheck requirements, and decision timestamp.

`checkout_revision` is the exact commit whose immutable tree supplied every marker/anchor. `checkout_ref` is the
fixed audit value `refs/remotes/origin/aidt-prd`; neither workflow nor Jira may configure it. Frontier 002 performs no
fetch. Frontier 003 freshly fetches `aidt-prd` and requires exact SHA equality before any provisioning mutation.

Child ID is deterministic `<JIRA-KEY>--<service-id>`. Child source is `aidt-route-child` keyed by
`<JIRA-KEY>::<service-id>` and records coordinator/service/source/catalog/checkout revisions. A bounded route-owned
body marker carries human route context; Jira source context is copied inertly for the worker. Existing children keep
their local state/body outside the marker. Removed/stale children are retained and force coordinator review.

The file tracker locks coordinator/current/retained/desired child IDs in sorted order, re-reads and preflights every
target before the first write, then uses bounded CAS and atomic rename per card, children first/coordinator last. It
rejects unmanaged/case/symlink/path/marker/source collisions and source drift. Failure between distinct renames records
`partial_apply`; a later poll repairs owned incomplete application without rollback/delete.

## Hook and Health Contract

`_poll_aidt_routing` runs after a status-returning `_poll_jira_intake` and before legacy normalization/candidate fetch.
It returns global dispatch permission plus blocked IDs. Disabled/success/review/failure transitions expose
enabled/status/last_success/last_error and routed/review/child/failure counts. Any global
config/catalog/path/Git/source-drift/preflight/partial-apply failure adds `aidt_routing_failure` and returns before
candidate fetch. Success filters every route-managed ID until Frontier 003. Health/logs map failures to bounded
categories plus optional canonical service/card ID and never use `str(exc)`.

## Frozen Product/Test Scope

Iteration 2 is re-approved for exactly these product/test paths:

1. `src/symphony/aidt_routing/__init__.py`;
2. `src/symphony/aidt_routing/contract.py`;
3. `src/symphony/aidt_routing/git_objects.py`;
4. `src/symphony/aidt_routing/decision.py`;
5. `src/symphony/aidt_routing/runtime.py`;
6. `src/symphony/trackers/aidt_routes.py`;
7. `src/symphony/trackers/jira.py`;
8. `src/symphony/jira_intake.py`;
9. `src/symphony/trackers/file.py`;
10. `src/symphony/orchestrator/core.py`;
11. `tests/aidt_routing_support.py`;
12. `tests/test_aidt_routing_contract.py`;
13. `tests/test_aidt_routing_git_objects.py`;
14. `tests/test_aidt_routing_decision.py`;
15. `tests/test_aidt_routing_storage.py`;
16. `tests/test_aidt_routing_runtime.py`;
17. `tests/test_jira_intake.py`.

The untracked iteration-1 prototypes `src/symphony/aidt_routing.py` and `tests/test_aidt_routing.py` are migration
inputs and must be replaced, not retained. Run-vault evidence may change. No other product/test path may change
without returning to Plan Approval.

## Steps

1. Preserve the import/behavior red proof, replace the flat prototypes with the approved package/test layout, and
   make contract/import tests green first.
2. Implement the immutable fixed-ref binary Git reader and hostile trust fixtures directly; do not migrate the
   rejected `HEAD`/status/working-tree reader.
3. Move pure structured-source scoring/decisions and AIDT batch storage into their cohesive modules; prove relocation
   before integrating runtime.
4. Enforce strict live Jira wire fields while updating compatible HTTP fixtures; preserve direct DTO defaults.
5. Compose one whole-poll batch, repository/source precommit recheck, partial repair, sanitized result, and default-off
   core health/dispatch barrier.
6. Run focused suites in contract/Git/decision/storage/runtime order, affected regressions, static checks, full-suite
   parity, and fresh adversarial verification.

## Required Red Tests

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

## Plan-Attack Required Tests

- `test_enabled_catalog_rejects_unknown_keys_schema_variants_casefold_alias_and_checkout_collisions`
- `test_unverified_catalog_context_string_and_marker_never_score`
- `test_root_checkout_git_identity_and_base_ref_drift_fail_before_route_write`
- `test_symlinked_root_checkout_git_metadata_and_committed_non_blob_anchor_are_rejected`
- `test_git_object_reader_binds_fixed_aidt_prd_commit_regular_blobs_and_exact_output`
- `test_dirty_canonical_checkout_is_not_routing_input`
- `test_route_revision_uses_aidt_prd_object_when_head_is_on_another_branch`
- `test_base_ref_move_recomputes_or_blocks_before_write`
- `test_repeated_endpoint_alias_and_anchor_mentions_score_each_category_once`
- `test_component_endpoint_conflict_and_shared_consumer_evidence_cannot_become_multi_route`
- `test_hostile_marker_component_and_endpoint_text_cannot_change_structured_route_evidence`
- `test_reordered_catalog_and_source_maps_have_stable_fingerprints_but_semantic_changes_do_not`
- `test_cross_coordinator_reparent_case_path_and_removed_catalog_child_collisions_fail_preflight`
- `test_precommit_late_collision_writes_nothing`
- `test_failure_after_each_child_rename_is_reported_and_next_poll_repairs_without_delete`
- `test_intake_and_reroute_preserve_local_status_priority_url_timestamps_and_unowned_frontmatter`
- `test_preserved_active_stale_child_is_filtered_while_unrelated_local_candidate_remains`
- `test_enabled_routing_blocks_fetch_after_same_tick_jira_intake_failure`
- `test_enabled_routing_reload_error_uses_last_good_health_but_stops_candidate_fetch`

Additional binding edges: component/alias/catalog case collisions; disabled service; absolute/traversal/symlink anchor;
oversize marker/symbol/source/evidence; malicious component/endpoint; duplicate evidence; consumer-only service;
Bug/backend/frontend branch prefixes; catalog/checkout/source drift; concurrent local note; duplicate/invalid route
markers; child ownership mismatch; old timestamps; partial prior owned apply repair; equal-poll bytes/mtime stability.

Plan-attack additions: unknown keys/wrong scalar types; NFC and duplicate-checkout collisions; unverified
context/marker no-score; root/checkout/Git-identity/base-ref swaps; dirty working-tree preservation; non-SHA-1/moved
Git repository; exact binary Git-object output and regular blobs; repeated score mentions; component-vs-code conflict;
shared consumer false multi-route; hostile
marker/component/endpoint separation; parent hydration on non-empty child; canonical fingerprint reordering;
cross-coordinator reparent/removal collision; failure after each child rename and repair; preservation of every local
top-level field; route-managed active-child filtering with unrelated candidate; same-tick Jira failure; enabled reload
error.

## Verification Commands

```bash
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py tests/test_aidt_routing_decision.py tests/test_aidt_routing_storage.py tests/test_aidt_routing_runtime.py
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py tests/test_aidt_routing_decision.py tests/test_aidt_routing_storage.py tests/test_aidt_routing_runtime.py tests/test_jira_intake.py tests/test_tracker_jira.py tests/test_tracker_jira_edges.py tests/test_tracker_file.py tests/test_orchestrator_health.py tests/test_service.py tests/test_webapi.py
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider
rtk ../../.venv/bin/ruff check --no-cache src/symphony/aidt_routing src/symphony/trackers/aidt_routes.py src/symphony/jira_intake.py src/symphony/trackers/jira.py src/symphony/trackers/file.py src/symphony/orchestrator/core.py tests/aidt_routing_support.py tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py tests/test_aidt_routing_decision.py tests/test_aidt_routing_storage.py tests/test_aidt_routing_runtime.py tests/test_jira_intake.py
rtk ../../.venv/bin/pyright --pythonpath ../../.venv/bin/python src/symphony/aidt_routing src/symphony/trackers/aidt_routes.py src/symphony/jira_intake.py src/symphony/trackers/jira.py src/symphony/trackers/file.py src/symphony/orchestrator/core.py
rtk git diff --check
rtk ../../.venv/bin/symphony doctor ./WORKFLOW.md
```

## Grounding Ledger

- A20 ownership and catalog: `../../exploration/routing-aidt-evidence.md`.
- Symphony seams and rejected alternatives: `../../exploration/routing-symphony.md`.
- Immutable base-object decision and dirty-checkout conflict: `../../exploration/routing-git-object-trust.md`.
- Provisioning handoff and fetched-base equality: `../../exploration/worktree-provisioning.md`.
- Cohesive package/storage/test split: `../../exploration/routing-cohesion.md`.
- Current safe source/file CAS boundary: Frontier 001 commit `f7b0585`.
- Base for this frontier: clean `run/symphony-aidt-orchestrator-20260720` at `f7b0585`.

## Loop Contract

`max_iterations: 3`. Fresh builder and fresh verifier per iteration. Failed verification records only exact defects
and trusted reruns in `R-LOOP.md`. Conditional plan attack must pass before Build.
