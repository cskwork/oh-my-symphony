# Conditional plan attack — Frontier 002 AIDT routing contract

## Decision

The intent is sound, but Build is not safe yet. The plan still has ambiguous wire formats, unpinned filesystem reads,
an impossible zero-write claim across independent renames, and no dispatch barrier for preserved stale children. These
are contract defects, not builder choices.

Grounding was limited to `JiraInboxIssue`, `_INTAKE_SEARCH_FIELDS`, `_normalize_inbox_node`, and
`_load_intake_parent` in `trackers/jira.py`; `render_jira_source`, `_updates`, and `run_jira_intake` in
`jira_intake.py`; `ExternalSourceUpdate`, `_validate_external_updates`, `_plan_external_update`,
`_external_source_matches`, and `_commit_external_plan` in `trackers/file.py`; and `_poll_jira_intake`, `_on_tick`,
and `health` in `orchestrator/core.py`.

## MUST resolve before Build

### 1. Freeze one catalog schema and reject typo/injection space

`PLAN.md` specifies `states.ready/review/coordinator` and derived branch prefixes; `routing-symphony.md` instead names
`ready_state/review_state/coordinator_state` and permits branch templates. Bind the former and delete the latter.
Only `enabled` defaults (`false`). If absent or exactly `false`, ignore every sibling key and construct no path/Git
reader. When enabled, require every other key, reject unknown keys recursively, require `type(x) is bool/int`, and
require `minimum_confidence == 90`.

Bind IDs to lowercase ASCII `[a-z0-9]+(?:-[a-z0-9]+)*`, 1–48 bytes; check IDs, aliases, and resolved checkout names
for NFC+casefold collisions. Allow at most 64 services, 32 aliases/service, 16 anchors/category, 256 bytes/path or
literal, and 32 evidence records/candidate. A checkout is exactly one relative path segment. Reject two enabled
services resolving to the same checkout. This closes YAML/config ambiguity without adding a shared workflow model.

Required test: `test_enabled_catalog_rejects_unknown_keys_schema_variants_casefold_alias_and_checkout_collisions`.

### 2. Replace claimed context strings with checked-in context anchors

Bare `context_paths` cannot satisfy “unique checked-in HTTP context/base path.” Define a context anchor as
`{file, literal}`; a route anchor as `{file, method, endpoint, symbols}`; and a domain anchor as `{file, terms}`.
All matches are literal and contribute at most once per category/service. Marker presence proves repository shape only
and never scores. Catalog paths and literals are the only filesystem selectors; Jira text never becomes a path,
symbol, service ID, regex, ref, or command argument.

Required test: `test_unverified_catalog_context_string_and_marker_never_score`.

### 3. Bind root, checkout, anchor, and revision to the same trusted observation

`Path.resolve()` followed by `read_text()` or `git -C` is TOCTOU-vulnerable. Require an absolute, existing,
non-symlink `aidt_root`; reject a symlink in every root/checkout/anchor component. Open regular anchors with
`O_NOFOLLOW` where available, compare `lstat`/`fstat` device+inode, and recheck the root, checkout, and anchor identity
after Git/anchor reads and immediately in route batch preflight. Any change is global `path_changed` and no commit
starts. Document the residual boundary: another process can mutate AIDT after preflight; Frontier 003 must reverify
the stored commit before creating a worktree.

Required tests: `test_root_checkout_and_anchor_swap_during_read_fails_before_route_write` and
`test_symlinked_root_ancestor_checkout_marker_and_anchor_are_rejected`.

### 4. Freeze Git semantics, not just “40 hex”

Use fixed argv only: `git --no-optional-locks -C <checkout> rev-parse --verify HEAD^{commit}` plus fixed commands to
assert `--show-toplevel` equals the trusted checkout, object format is `sha1`, every scoring anchor/marker is tracked,
and `git status --porcelain=v1 --untracked-files=all` is empty. Accept exactly one ASCII lowercase 40-hex line,
bounded stdout/stderr, zero exit, and timeout; no ref/config/Jira input is accepted. A dirty tree must fail because
HEAD otherwise does not pin the bytes used to score. Sanitize Git environment and never log output.

Required test: `test_git_reader_binds_head_toplevel_sha1_clean_tracked_anchor_and_exact_output`.

### 5. Make scoring a category map and define conflict versus multi-service

The current “deduplicated by evidence category/source” permits double counting and leaves domain docs ambiguously
authoritative. Freeze the authoritative set to `component`, `context`, `code`; domain is corroborating only. Each
weight is awarded at most once per service/category, regardless of repetitions or number of anchors. Evidence is
sorted by `(service, category, normalized source field, anchor ID)` before hashing/rendering.

Structured components match aliases only; component-looking prose never does. Endpoint tokens come only from
bounded structured summary/description/parent fields, use ASCII method/path grammar, and are never percent-decoded,
path-normalized, or read from components. A direct component for A plus unique context/code ownership by B is a
conflict even if both exceed 90.

Multiple passing services are a multi-route only when each has an explicit, disjoint change anchor (its component or
exact affected code contract) and no evidence item is reused across services. Otherwise multiple passers are a tie
and review. Consumer/dependency/domain/parent/kind/keyword evidence cannot establish that anchor.

Required tests: `test_repeated_endpoint_alias_and_anchor_mentions_score_each_category_once` and
`test_component_endpoint_conflict_and_shared_consumer_evidence_cannot_become_multi_route`.

### 6. Route from structured source only; the marker is display-only

`render_jira_source` HTML-escapes and rewrites text, while the proposed source map omits the descriptions needed for
endpoint/parent evidence. Do not parse the body marker. Extend the allowlisted `source` map with normalized
`summary`, `description`, `components`, `status`, nullable `priority`, `issue_type`, `updated`, computed browse URL,
and complete nullable parent `{key,summary,description,components}`. The marker remains inert human display only.
Reject controls, invalid nesting/types, oversize values, duplicate casefolded components, and malformed timestamps
as a whole Jira batch. Load parent data whenever a parent is recorded, not only the current empty-description subtask
case in `_intake_parent_key`.

Required test: `test_hostile_marker_component_and_endpoint_text_cannot_change_structured_route_evidence`.

### 7. Specify stable source, catalog, and decision fingerprints

Use version-tagged UTF-8 canonical JSON (`sort_keys=True`, compact separators, no NaN) over normalized values.
`source.revision` includes every structured source field above. `catalog.revision` includes the complete normalized
enabled/disabled service definitions and schema version, but not list order or absolute root spelling.
`route.fingerprint` includes source revision, catalog revision, sorted service-to-HEAD revisions, and the semantic
decision; it excludes timestamps and rendered evidence order. Preserve the existing decision timestamp on an equal
fingerprint and do not serialize/write. Checkout identity is separately recorded so two roots with equal commits are
not silently conflated operationally.

Required test: `test_reordered_catalog_and_source_maps_have_stable_fingerprints_but_semantic_changes_do_not`.

### 8. Freeze coordinator/child identity, ownership, and collision rules

Child file, `id`, and `identifier` are exactly `<JIRA-KEY>--<service-id>`; source is exactly
`{kind: aidt-route-child, key: <JIRA-KEY>::<service-id>, coordinator: <JIRA-KEY>, service: <service-id>}`. Reject a
casefold match at another path, any ID/path/source disagreement, reparenting, duplicate resolved checkout, or child
listed by two coordinators. Lock both current route children and desired children, including IDs retained from an old
catalog. Catalog removal/rename cannot make an old child undiscoverable.

Required test: `test_cross_coordinator_reparent_case_path_and_removed_catalog_child_collisions_fail_preflight`.

### 9. Correct the whole-batch atomicity claim

Sorted locks plus preflight can guarantee zero writes for validation errors and cooperative concurrent writers; it
cannot guarantee zero writes for a crash or non-cooperative mutation between separate renames. Amend GOAL/PLAN to
say this explicitly. Re-read every target and source revision under all locks before commit. Commit children in
sorted order and coordinator last as the completion sentinel, with one desired fingerprint on every owned artifact.
Any compare mismatch before the first rename is zero-write; a mismatch/crash after one rename is `partial_apply`,
stops candidate fetch, and is repaired idempotently next poll. Never roll back by deleting or by restoring stale
bytes.

Required tests: `test_precommit_late_collision_writes_nothing` and
`test_failure_after_each_child_rename_is_reported_and_next_poll_repairs_without_delete`.

### 10. Define no-delete and local ownership for every lifecycle transition

Routing may write only top-level `routing`, its bounded route body marker, and an initial route-owned state
transition. Intake may write only `source` and the Jira marker. Neither writes local priority, labels, title, URL,
notes, plans, QA, evidence, created time, or body outside its marker. Top-level status/priority/URL remain local;
Jira names live only under `source`.

On refresh, change state only while it is one of the configured route-owned states; preserve any other/manual or
in-flight state. A removed service remains in `routing.retained_children`, forces coordinator review, and is never
deleted or reset. A changed source/catalog/checkout revision marks an existing child stale without rewriting its
local state/body.

Required test: `test_intake_and_reroute_preserve_local_status_priority_url_timestamps_and_unowned_frontmatter`.

### 11. Add a real dispatch barrier for stale and partial cards

Preserving an active child state means moving only the coordinator to Human Review does not stop
`fetch_candidate_issues`. `_poll_aidt_routing` therefore cannot return only a boolean. Return a bounded result with
`global_allow_dispatch` and `blocked_identifiers`; `_on_tick` stops before `_fetch_candidates` on global failure and
filters route-managed stale/review/partial/retained IDs after fetch. Until Frontier 003 consumes the pinned checkout,
all enabled AIDT-routed coordinator/child IDs must be blocked from worker dispatch; default-off behavior is unchanged
and `Issue` need not widen.

Routing-enabled ticks also require Jira intake to be disabled by explicit reviewed mode or to have succeeded on that
same tick. The current `_poll_jira_intake` swallows failure and `_on_tick` continues; that must not permit stale Jira
cards to route or dispatch.

Required tests: `test_preserved_active_stale_child_is_filtered_while_unrelated_local_candidate_remains` and
`test_enabled_routing_blocks_fetch_after_same_tick_jira_intake_failure`.

### 12. Freeze hot-reload and health state transitions

Initialize health as `{enabled:false,status:disabled,last_success:null,last_error:null,counts:0}`. Disabled polls clear
error/consecutive failure/counts without constructing readers; success/review clears error and sets last success;
global failure retains last success, increments failure count, exposes only an allowlisted category and validated
canonical card/service ID, and skips fetch. Re-enable always rebuilds/revalidates the catalog and revisions.

If the last-good config has routing enabled, any workflow reload error must stop that tick rather than dispatch under
stale routing config. A malformed first activation while the last-good config is disabled remains outside this
feature-local parser; record that limitation rather than claiming universal hot-reload fail-closed behavior.

Required test: `test_enabled_routing_reload_error_uses_last_good_health_but_stops_candidate_fetch`.

## SHOULD bind before implementation review

- Make all caps named constants and test boundary and boundary+1 for services, source fields, anchors, Git output,
  evidence, children, and total batch bytes.
- Inject clock, Git runner, anchor opener, and rename fault point; otherwise mtime, TOCTOU, and partial-apply tests will
  be nondeterministic.
- Record A20-1188's A20-1186 mismatch as bounded evidence, not as a score or child trigger.
- Add explicit regression assertions that Jira status/priority enrichment does not change `Issue.state`,
  `Issue.priority`, registration order, file candidate order, or web/TUI payloads.

## Accepted constraints

- Separate atomic renames are an honest recoverable boundary, not cross-file atomicity.
- Literal checked-in anchors prove configured ownership, not Java/TypeScript semantic reachability.
- No UI/TUI route rendering, worktree creation, prompt/stage consumption, live Jira mutation, or service base-ref
  validation belongs here.
- The six-file scope remains feasible only with the `core.py` identifier denylist above and backward-compatible
  defaults on new `JiraInboxIssue` fields; changing shared `Issue`, workflow schema, existing test files, or UI is not
  required. If the builder cannot preserve current DTO fixture construction without weakening normalized live input,
  return to Plan Approval rather than widening scope silently.

Build gate: FAIL

## Recheck

The amended `GOAL.md` and the overriding `Binding Plan-Attack Amendments` in `PLAN.md` resolve all twelve MUSTs:

1. Closed schema, exact defaults/types, collision normalization, caps, one-segment checkouts, and duplicate-checkout
   rejection are frozen.
2. Context/route/domain evidence now uses checked-in literal anchors; markers cannot score.
3. Root/checkouts/anchors are identity-checked across symlink and TOCTOU boundaries, with Frontier 003 revalidation
   explicitly retained.
4. Git argv, HEAD commit semantics, SHA-1, top-level, clean/tracked requirements, output bounds, and sanitization are
   exact.
5. Category-once scoring, authority classes, hostile component/endpoint separation, conflicts, ties, and disjoint
   multi-service anchors are deterministic.
6. The structured source map is the sole routing input; complete parent hydration and whole-batch hostile-input
   rejection are binding.
7. Versioned canonical source/catalog/route fingerprints, semantic exclusions, equal-write stability, and checkout
   identity are defined.
8. Child names, source ownership, reparent/cross-coordinator/case/path collisions, and retained-child locking are
   exact.
9. The zero-write claim is limited to precommit validation/cooperative races; children-first partial apply, dispatch
   stop, no rollback/delete, and next-poll repair are honest.
10. Intake/routing/local ownership, manual/in-flight state preservation, stale marking, retained children, and
    no-delete behavior are explicit.
11. Same-tick/static source modes, global stop, per-ID filtering, unrelated-candidate preservation, and the
    pre-Frontier-003 dispatch barrier are bound without widening `Issue`.
12. Disabled/success/review/failure/re-enable health transitions and last-good-enabled reload failure behavior are
    frozen, including the malformed-first-activation limitation.

SHOULD recheck: named cap boundary tests, injected clock/Git/anchor/rename seams, and the A20-1188/A20-1186
non-scoring evidence rule are resolved. One non-blocking verification detail remains: the plan requires local-field
preservation, disabled candidate-order parity, affected `test_service.py`/`test_webapi.py`, and full-suite parity, but
does not individually name assertions for unchanged registration order and web/TUI payloads. The verifier should
require those assertions in `tests/test_aidt_routing.py` if the enrichment can reach those surfaces; this does not
require or authorize shared `Issue`, UI/TUI, or existing-test edits.

The accepted cross-file atomicity, literal-anchor, Frontier 003/009, and six-file scope boundaries remain coherent.

Build gate after amendments: PASS

## Iteration 2 Immutable-Object Recheck

### Decision

Build gate: **FAIL**. Reading evidence from a fixed local production-base commit is the correct root-cause fix, but
the amended plan still leaves the executable Git wire protocol, repository-object trust boundary, precommit ref
binding, durable handoff version, and live Jira compatibility ambiguous. The frozen six-file boundary is also no
longer cohesive enough to implement the contract under the repository's coding rules.

The partial product/test diff was inspected only as feasibility evidence. It makes the gaps concrete:
`src/symphony/aidt_routing.py` is already 894 lines and combines catalog/schema parsing, filesystem and Git trust,
source validation, scoring, decisions, card rendering, and polling; `trackers/file.py` has grown by 276 lines to
1,442 lines; and `tests/test_aidt_routing.py` is already 693 lines. Its current Git runner returns text, reads
working-tree anchors, resolves `HEAD`, calls `status`/`ls-files`, rechecks no Git metadata/ref under the card locks,
and writes `aidt-route-v1` without the fixed ref/trust tag. Those are obsolete implementation seams, not evidence
that the immutable-object amendment is complete.

### MUST resolve before Build

1. **Freeze a genuinely binary, hard-bounded Git protocol.** Binding Amendment 4 says “binary output,” “exact
   `ls-tree -z`,” and “bounded strict UTF-8,” while the “All caps” paragraph requires an injectable binary-safe
   runner, but neither freezes the runner result type nor the byte grammar/capture boundary. Require the injected
   runner to return a structured `(returncode, stdout: bytes, stderr: bytes)` result and require production capture
   to stop and kill the process when either stream crosses its command-specific cap; checking length only after
   `subprocess.run(..., capture_output=True)` has buffered the output is not a bound. Scalar results must have exact
   raw-byte forms; an `ls-tree` result must be exactly one
   `mode SP type SP 40-lower-hex TAB requested-path NUL` record with no prefix/suffix/second record; and blob output
   must be capped before strict UTF-8 decode. Reject nonzero exit, timeout, nonempty unexpected stderr, missing/final
   NUL, extra records, wrong path/mode/type/OID, boundary+1 output, and invalid UTF-8 without logging either stream.
   Catalog Git paths should reject NUL/control/backslash ambiguity and compare the returned Git path bytes exactly,
   not through newline splitting or C-quoted text. This is required by PLAN Binding Amendment 4, Priority Rules 3
   and 10, and the named
   `test_git_object_reader_binds_fixed_aidt_prd_commit_regular_blobs_and_exact_output`; the test must also prove the
   runner never inserts a synthetic `--` before a subcommand and never invokes `status`, `diff`, `ls-files`,
   `hash-object`, or a working-tree opener.

2. **Close the real repository/object boundary, not only inherited object-directory variables.** Binding
   Amendments 3 and 4 name the `.git` entry, Git directory, common Git directory, replace objects, and object
   overrides, but do not define supported `.git` shapes or repository-controlled object indirection. Freeze whether
   both a directory and a bounded regular `gitdir:` file are supported; parse a gitfile without following symlinks;
   resolve Git/common directories to absolute paths; reject symlinks in every ancestor; and capture/recheck entry,
   target, and common-directory identities. Scrub all inherited `GIT_*` override/config/namespace variables, disable
   replacement objects explicitly (`--no-replace-objects` or its exact equivalent), disable optional locks and
   partial-clone lazy fetch, and prevent user/system config from reintroducing behavior. Repository-local
   `objects/info/alternates` must either be rejected or added as an explicitly identity-bound trust root; silently
   traversing an unobserved alternate contradicts Amendment 3's stated boundary. This frontier performs no network,
   so a missing promisor object must fail rather than lazy-fetch. Add cases for a symlinked gitfile target/common
   dir, replace ref, every object/config environment override, repository alternates, promisor/missing object, and
   unchanged Git metadata identity.

3. **Make Git/ref revalidation part of the locked precommit contract.** Binding Amendment 9 currently says to
   recheck “path identities,” while Amendments 3 and 4 separately require an unchanging base ref. Under all route
   locks, immediately before the first rename, recheck for every enabled observation: root/checkout/`.git`/Git-dir/
   common-dir identities, exact top-level, SHA-1 object format, and
   `refs/remotes/origin/aidt-prd^{commit} == captured SHA`; resolving the commit again must also prove the object still
   exists. The plan must choose one whole-poll batch containing all coordinators/current/retained/desired children,
   or define a separate recoverable `partial_tick` state when a global ref changes after an earlier coordinator was
   committed. The current per-coordinator wording and partial loop can otherwise write card A, observe ref drift for
   card B, and report an ordinary global failure that neither Amendment 9's `partial_apply` repair nor its zero-write
   boundary describes. Prefer one bounded whole-poll batch, one ref recheck before its first rename, children sorted
   before coordinators, and the existing post-first-rename `partial_apply` repair rule. A move after that final
   preflight remains the documented Frontier 003 equality boundary.

4. **Freeze durable fingerprint, repository identity, multi-service, and stale-ref handoff semantics.** Binding
   Amendment 7 requires an unspecified “immutable-object trust schema tag” and records repository identity outside
   the fingerprint, while also saying equal fingerprints preserve bytes. A checkout/Git repository can be replaced
   between polls by a different stable repository containing the same commit; the fingerprint then compares equal
   and preserves the old identity, silently conflating the two observations. Freeze a new exact route/trust schema
   literal that cannot equal the prototype `aidt-route-v1`, and define equal-write skipping as equality of both the
   semantic fingerprint and a sanitized repository-observation binding (or include its digest in the fingerprint).
   Old/unknown/HEAD-based schemas are stale inputs and must be recomputed, never accepted as object-backed routes.

   Also define the exact revision map as all enabled services observed for that decision. A coordinator stores that
   sorted map plus the fixed ref; each selected child stores its own exact `(service, checkout_ref,
   checkout_revision)`, the coordinator fingerprint, and its service-specific route slice. A multi-service child
   must never inherit another service's SHA or a null coordinator scalar. Because the local remote-tracking ref may
   be stale, every successful object-backed route remains explicitly `pending_fresh_base_equality` and blocked by
   Binding Amendment 11. Frontier 003 may release only the exact child after a fresh `aidt-prd` fetch resolves the
   same SHA and the route schema/ref/service/identity binding is still current; mismatch is bounded
   `route_base_mismatch`, preserves all state, and requests rerouting. This closes Binding Amendment 7, the
   Routing/Card Contract's `checkout_ref` handoff, and the stated Frontier 003 equality rule.

5. **Preserve dirty checkout and index state with byte-level proof.** Binding Amendment 4 and Priority Rule 3 say
   staged/unstaged/untracked/ignored state is permitted and ignored. Make the named
   `test_dirty_canonical_checkout_is_not_routing_input` binding on bytes and metadata: freeze the base ref, then
   change a tracked anchor in the worktree, stage a different file, add untracked and ignored files, and assert
   identical decision/evidence/SHA/fingerprint plus unchanged `.git/index`, HEAD/symbolic ref, tracked files,
   untracked files, ignored files, and their mtimes after routing. A working-tree-only marker/anchor symlink must be
   ignored and unopened, while a committed `120000`, tree, or `160000` entry fails. This is a success criterion, not
   merely a helpful fixture.

6. **Separate strict normalized Jira source from compatible live Jira transport.** Binding Amendment 6 and the
   Source Snapshot Contract correctly require a closed, complete stored `source`, but they do not say how to handle
   Jira's richer nested component/status/priority objects. Require the requested live fields to be present and
   correctly typed, extract only allowlisted `name` values, and ignore non-authoritative Jira transport keys rather
   than requiring each nested object to contain only `name`; missing/wrong-type/name/control/size/duplicate data
   still fails the whole intake batch. Backward-compatible defaults may remain on direct `JiraInboxIssue`
   construction, but must not turn a missing live `components`, `status`, `updated`, or hydrated parent component
   field into `[]`, `Unknown`, or an epoch timestamp.

   This requires returning to Plan Approval for test scope. Existing `tests/test_jira_intake.py` HTTP fixtures omit
   the newly required fields (including parent components), while the Frozen Product/Test Scope forbids editing
   that test file and still requires it to pass. The current partial normalizer demonstrates the impossible choice:
   strict wire input fails the existing fixtures, while defaults weaken the live contract. Add the affected Jira
   intake test file(s) to the authorized scope and cover realistic extra Jira keys, missing required fields, nullable
   priority, parent hydration for a non-empty child, and whole-batch zero-write behavior.

7. **Split the feature by cohesive domain before continuing Build.** The Frozen Product/Test Scope's single new
   `aidt_routing.py` and single new test file now conflict with repository Commandment 3 (“one file = one purpose,”
   split unwieldy files by feature/domain, functions at most 50 lines). The 894-line partial module already has at
   least six independent reasons to change, and the immutable runner/metadata parser will enlarge it; the 693-line
   test file mirrors that coupling. Widen the approved boundary to a small `aidt_routing` package (at minimum
   config/catalog, Git-object observation, decision/scoring, and coordinator/card specifications), split tests along
   those same seams, and move route-card pure validation/planning out of the already 1,442-line `trackers/file.py`
   while leaving only the minimal lock/CAS/atomic-write adapter there. The sampled new helpers are generally short,
   so the present blocker is cohesion rather than a proven across-the-board function-length breach; the amended
   build/verifier must still enforce new functions at most 50 lines and nesting at most four. A builder may not obey
   the current exact-six-file clause and the repository architecture rule simultaneously.

8. **Freeze and test the sanitizer as an output contract.** Priority Rule 10, Binding Amendment 12, and the Hook and
   Health Contract require allowlisted categories but do not enumerate them. Bind the exact public error-category
   enum and which categories may carry a validated canonical card/service ID. No raw exception, cause, argv,
   checkout/Git path, object bytes, stdout/stderr, source text, environment value, or Jira payload may reach result
   repr, health, structured logs, or reload/partial-apply messages. Tests must capture logs as well as result/health
   for nonzero exit, timeout carrying output, malformed tree/blob bytes, identity/ref drift, injected runner/probe
   exceptions, and `internal_error`. This is necessary to verify Amendment 4's “never log output” and Priority Rule
   10; merely avoiding `str(exc)` in one health branch is insufficient.

### SHOULD bind before implementation review

- Mark `exploration/worktree-provisioning.md`'s remaining statements that Frontier 002 records `HEAD` as superseded
  by Binding Amendments 3/4/7. The PLAN is higher authority, so this wording alone is not another build blocker, but
  leaving it in the Frontier 003 grounding path invites an equality implementation against the wrong revision.
- Give scalar, tree-record, blob, stderr, per-service path, total observed-object, whole route-batch card/byte, and
  public evidence limits separate named constants with boundary/boundary+1 tests. One generic Git-output cap is not
  sufficient for both a one-line SHA and a scoring blob.
- Add a real temporary-repository handoff fixture with `HEAD` on another branch, a dirty index/worktree, a stable
  local production ref, then a simulated newer fetched production ref. It should prove Frontier 002 records only the
  old immutable object and blocks dispatch, the mismatch authorizes no provisioning, the next routing poll emits a
  new versioned fingerprint, and only a later exact fresh equality can release that service child.

Build gate after iteration-2 immutable-object recheck: **FAIL**.

### Iteration 2 Amendment Recheck

The latest `PLAN.md` resolves every iteration-2 blocker and overrides the obsolete prototype, per-card, text-runner,
live-Jira-default, six-file, and `HEAD` wording explicitly:

1. **MUST 1 — hard-bounded binary runner: PASS.** Iteration 2 Binding Amendment 1 freezes the `GitRunner` inputs and
   structured raw-byte result, streaming two-channel capture, kill/reap behavior, exact scalar/tree/blob grammars,
   fail-closed cases, argv ordering, and forbidden commands. The named-cap paragraph fixes separate token/path/tree/
   blob/stderr byte limits and requires boundary/boundary+1 tests.
2. **MUST 2 — repository/object trust: PASS.** Amendment 2 limits `.git` to a non-symlink directory or bounded
   no-follow regular gitfile, binds checkout/entry/target/Git/common-dir identities, scrubs inherited `GIT_*`, disables
   system/global config, prompts, replace objects, optional locks, protocols, and lazy promisor fetch, rejects
   alternates/replace evidence/missing promisor objects, and forbids network/fetch.
3. **MUST 3 — locked whole-poll precommit: PASS.** Amendment 3 requires one complete bounded coordinator/child plan,
   all sorted locks, source/ownership plus repository/top-level/SHA-1/ref/commit revalidation immediately before the
   first rename, zero writes before that boundary, globally ordered children then coordinators, and whole-tick
   `partial_apply` repair after it. The named caps bind 500 coordinators, 2,000 children, and 10,485,760 serialized
   batch bytes.
4. **MUST 4 — durable schema/binding/multi-service/Frontier 003 handoff: PASS.** Amendment 4 freezes
   `aidt-git-object-v1`, `aidt-catalog-object-v2`, and `aidt-route-object-v2`, invalidates old/unknown schemas, makes
   semantic fingerprint plus `repository_binding_digest` the equal-write key, stores the all-enabled revision/
   binding map on the coordinator and only the exact service slice on each child, and keeps every success
   `pending_fresh_base_equality`. Frontier 003 may release only an exact fresh schema/ref/service/commit/binding
   match; `route_base_mismatch` preserves state and reroutes.
5. **MUST 5 — byte-level dirty-state proof: PASS.** Amendment 5 requires unchanged index bytes/mtime, symbolic
   HEAD/ref, tracked anchor bytes/mtime, staged content, untracked/ignored files, and working-tree-only symlinks;
   clean and dirty observations must have identical decision/evidence/SHA/fingerprint. Committed symlink, tree, and
   submodule modes fail globally.
6. **MUST 6 — strict live Jira with compatible fixtures: PASS.** Amendment 6 makes requested live components/status/
   priority/updated and hydrated-parent components mandatory, validates the expected containers and allowlisted names
   while ignoring unrelated transport keys, and preserves defaults only for direct DTO construction. The Frozen
   Product/Test Scope now authorizes `tests/test_jira_intake.py`, and Step 4 requires its HTTP fixtures to be updated
   without weakening the live wire contract.
7. **MUST 7 — cohesive package and test scope: PASS.** Amendment 7 and the 17-path Frozen Product/Test Scope replace
   the flat prototypes with the facade plus contract/Git-object/decision/runtime modules, the AIDT tracker adapter,
   and support/contract/Git/decision/storage/runtime tests. They freeze the acyclic dependency direction, facade-only
   public surface, <=50-line/<=4-nesting gates, and removal of compressed suites without creating a generic
   transaction framework.
8. **MUST 8 — exact sanitizer: PASS.** Amendment 8 enumerates the complete public category set, restricts service
   refs to repository/Git/revision categories and card refs to source/collision/preflight/partial categories, denies
   refs everywhere else, and limits result repr, health, and structured logs to event/category/allowed ref/counts.
   Causes, argv, paths, bytes, streams, source/payload text, environment values, and exception strings are forbidden.

The three SHOULDs are also resolved: `exploration/worktree-provisioning.md` now marks every Frontier 002 `HEAD`
statement as superseded by fixed-ref object routing; the latest PLAN gives distinct numeric token/path/tree/blob/
stderr/path-count/per-service-object/whole-observation/coordinator/child/batch limits while retaining the 32-record
public-evidence cap and requires named boundary tests; and its real temporary-repository fixture binds unrelated
`HEAD`, dirty state, stable/moved production refs, rerouting, and the future no-network exact-equality handoff.

Read-only feasibility recheck: the current 894-line flat module, 693-line test, and AIDT-specific block in the
1,442-line generic file tracker still implement the rejected iteration-1 seams, but they are declared migration
inputs rather than constraints. The approved package/tracker/test paths provide a direct extraction target;
`tests/test_jira_intake.py` is now in scope for strict-wire fixture repair; and `orchestrator/core.py` is authorized
for the still-missing default-off barrier. No additional product/test path is required by the inspected partial diff.

Build gate after iteration-2 amendments: **PASS**.
