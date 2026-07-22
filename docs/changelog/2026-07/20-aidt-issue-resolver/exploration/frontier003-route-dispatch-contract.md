# Frontier 003 route-to-dispatch contract

Date: 2026-07-21
Kind: Feature exploration
Scope: landed AIDT route child/facade + orchestrator dispatch/reload seam only
Commit inspected: `e9794e86228b3ab032a9db8116839a1a8cdd0a39`

## Executive summary

Frontier 003 should not let the current tick filter treat a routed child as backend-ready. The landed route child is intentionally `pending_fresh_base_equality`; it is only a trusted request to provision one named service checkout. The smallest safe extension is a public, frozen child-dispatch DTO, a result field that nominates only validated route children, and a worker-time barrier that reloads and revalidates the card before replacing the generic Symphony workspace path with the AIDT provisioner. Any mismatch must stop before backend construction, with no fallback to generic hooks or workspaces.

## Current evidence

- Stable facade exports only config/result plus `run_aidt_routing` and `filter_routing_candidates`; no child dispatch attestation API exists: `src/symphony/aidt_routing/__init__.py:7-50`.
- `AidtRoutingResult` carries only global allow/deny and `blocked_identifiers`; malformed output canonicalizes to global deny: `src/symphony/aidt_routing/contract.py:164-212`, `src/symphony/aidt_routing/contract.py:251-261`.
- Runtime scans every Jira coordinator, includes coordinator/children/retained children in the managed set, then filters every identifier in that set: `src/symphony/aidt_routing/runtime.py:121-174`, `src/symphony/aidt_routing/runtime.py:44-53`.
- Successful routing returns all managed IDs in `blocked_identifiers`; it exposes no safe child subset: `src/symphony/aidt_routing/runtime.py:177-206`, `src/symphony/aidt_routing/runtime.py:227-247`.
- Route projection identity is explicit: `identifier`, `coordinator`, optional `service`, `role`, routing payload, source, desired state, owned states, expected source revision: `src/symphony/aidt_routing/decision.py:56-83`.
- Coordinator/child IDs are deterministic; child ID is `<jira-key>--<service-id>`: `src/symphony/aidt_routing/decision.py:643-663`.
- Child routing is not ready yet: schema/role/status are `aidt-route-object-v2`/`child`/`pending_fresh_base_equality`; payload binds coordinator, service, checkout, exact ref/SHA, source/catalog revisions, repository digest, branch prefix, confidence, evidence, and `recheck_requirements=[fresh_base_equality]`: `src/symphony/aidt_routing/decision.py:666-693`.
- Child source ownership is exact: `kind=aidt-route-child`, key `<coordinator>::<service>`, coordinator, service: `src/symphony/aidt_routing/decision.py:732-738`.
- Storage rejects noncanonical IDs/source ownership, preserves non-route frontmatter/body, and changes state only while the current state is route-owned: `src/symphony/trackers/aidt_routes.py:218-256`, `src/symphony/trackers/aidt_routes.py:279-305`.
- Tick order is reload -> validation -> Jira intake -> routing -> global route gate -> candidate fetch -> managed-ID filter -> generic eligibility/dispatch: `src/symphony/orchestrator/core.py:2159-2175`, `src/symphony/orchestrator/core.py:2229-2288`.
- Last-known-good reload is already fail-closed when routing is enabled: `workflow_reload_error` stops the tick: `src/symphony/orchestrator/core.py:2160-2173`.
- Actual worker setup currently uses captured `cfg`, generic `WorkspaceManager.create_or_reuse`, generic `before_run`, then constructs the backend: `src/symphony/orchestrator/core.py:3760-3808`.
- Generic workspace creation may execute `hooks.after_create`; `before_run` executes the generic hook: `src/symphony/workspace.py:154-188`. Those are the wrong fallback for a route-managed AIDT child.

## Narrow consumed contract

Add a frozen public DTO, exported through `symphony.aidt_routing`:

```text
AidtRouteDispatchContract
  identifier                 # exact <coordinator>--<service>
  coordinator                # exact Jira key
  service                    # enabled catalog service id
  kind                       # backend/frontend
  checkout                   # catalog-relative checkout
  checkout_ref               # refs/remotes/origin/aidt-prd
  checkout_revision          # frozen commit OID
  repository_binding_digest  # routed repository identity
  route_fingerprint
  coordinator_fingerprint
  source_revision
  catalog_revision
  branch_prefix
  confidence
```

Add `load_route_dispatch_contract(config, identifier) -> AidtRouteDispatchContract | None`:

- `None` only for a genuinely unmanaged card.
- Route coordinator, stale/review child, disabled-routing managed child, malformed source/routing, wrong state, low confidence, mismatched catalog service/checkout/ref/revision/digest/fingerprint, or unexpected recheck requirements -> sanitized `AidtRoutingFailure`; never `None`.
- Accepted child: canonical file/path + exact child source tuple + schema `aidt-route-object-v2` + role `child` + status `pending_fresh_base_equality` + configured ready state + `recheck_requirements == [fresh_base_equality]` + confidence threshold met.
- No Git mutation or network in the facade; it attests durable card/config input only.

Extend `AidtRoutingResult` with `provisionable_child_identifiers: frozenset[str]` (name intentionally avoids claiming backend readiness). Populate it only from routed, non-retained child projections that pass the frozen child shape. Preserve `blocked_identifiers` as the complete managed-ID set. Candidate filtering becomes:

```text
unmanaged candidate -> preserve
managed coordinator/review/stale/retained child -> exclude
managed ID in provisionable_child_identifiers -> retain for worker-time barrier
```

Invalid DTO/result combinations canonicalize to global deny, as the current result contract already does.

## Dispatch barrier recommendation

Add one route-aware workspace preparation seam at the start of `_run_agent_attempt`, before `WorkspaceManager.create_or_reuse`:

1. For a tick-nominated route child, reload current workflow config; reload error or routing disabled -> fail.
2. Call `load_route_dispatch_contract` from the public facade against the current card bytes.
3. Pass only the frozen DTO and intended workspace path to Frontier 003 provisioner.
4. Immediately before `git worktree add`/resume, re-observe checkout identity and require current `refs/remotes/origin/aidt-prd` commit equals `checkout_revision` and repository binding digest equals the DTO.
5. Create/resume only that service worktree; persist Frontier 003-owned identity outside the route-owned `routing` map.
6. Re-read the card and require the same route/coordinator fingerprints and source/catalog revisions before `_build_agent_backend`.
7. Route child path skips generic `after_create` and generic `before_run`; unmanaged tickets retain the exact current path.

This worker-time barrier covers normal poll dispatch and retry dispatch because both enter `_run_agent_attempt`. Tick filtering alone is insufficient: the captured `Issue`/`cfg` can become stale after `_on_tick`, and retry candidate lookup has a separate path.

## Options

1. Tick-time provisioning: reject. Holds the poll on Git I/O, provisions cards that may lose the slot, and still has a tick-to-worker race.
2. Generic `after_create` hook reads route frontmatter: reject. Hook input is shell-parsed, cannot consume the typed facade safely, and would retain unsafe fallback behavior.
3. Worker-time route-aware provisioner before generic workspace/backend setup: recommend. One shared barrier covers initial/retry runs and permits a final card/config/base equality check.

## Fail-closed behavior

- Config/card/fingerprint/source/catalog mismatch: no workspace mutation; child remains undispatched; sanitized routing/worktree health reason.
- Base SHA or repository binding mismatch: no worktree creation; require reroute; never silently rebase/update the frozen revision.
- Existing path/branch/worktree identity mismatch: Blocked; preserve path and unrelated worktrees; no reset/removal/fallback.
- Failure after worktree creation but before final card recheck/backend: preserve the ticket-owned worktree for inspection; do not construct backend or auto-delete.
- Coordinator/review/stale cards: never reach generic workspace construction.
- Routing disabled after nomination: managed child still fails closed; it never becomes an unmanaged Symphony ticket.
- Error surfaces use allowlisted category/reference only; no absolute paths, command output, remote URLs, card body, or secrets in health/logs.

## TDD seams

Add `tests/test_aidt_route_dispatch_contract.py`:

- `test_facade_attests_only_canonical_pending_child`
- `test_facade_returns_none_only_for_unmanaged_card`
- `test_facade_rejects_coordinator_review_stale_and_disabled_managed_cards`
- `test_facade_rejects_state_source_schema_fingerprint_catalog_and_confidence_drift`
- `test_result_rejects_provisionable_ids_outside_managed_children`
- `test_candidate_filter_releases_only_provisionable_children_in_original_order`

Add `tests/test_aidt_worktree_provisioner.py`:

- `test_route_child_reloads_and_attests_before_any_workspace_mutation`
- `test_fresh_base_equality_runs_immediately_before_worktree_add`
- `test_base_or_repository_binding_drift_creates_no_worktree`
- `test_route_child_skips_generic_after_create_and_before_run_hooks`
- `test_final_route_drift_after_create_stops_before_backend_and_preserves_worktree`
- `test_resume_requires_exact_service_branch_base_path_and_route_fingerprint`
- `test_initial_and_retry_dispatch_share_the_same_route_barrier`
- `test_coordinator_and_stale_child_never_construct_backend`
- `test_unmanaged_ticket_preserves_existing_generic_workspace_flow`

Regression focus: `tests/test_aidt_routing_runtime.py:428-452,590-630,631-698`; storage child ownership/concurrency tests at `tests/test_aidt_routing_storage.py:132-248,324-460`; generic workspace/backend ordering tests in `tests/test_workspace.py` and `tests/test_orchestrator_dispatch.py`.

## Exact implementation file boundary

- `src/symphony/aidt_routing/contract.py` — widen validated result; add frozen dispatch DTO.
- `src/symphony/aidt_routing/dispatch.py` — new side-effect-free card/config attestation.
- `src/symphony/aidt_routing/__init__.py` — lazy public facade export.
- `src/symphony/aidt_routing/runtime.py` — emit provisionable child subset; filter with complete managed set.
- `src/symphony/orchestrator/core.py` — worker-time reload/attest/provision barrier before backend construction.
- Frontier 003 provisioner module (new, exact name to freeze in its plan) — service-local create/resume and final equality proof.
- Tests: the two files named above; edit existing runtime assertions only for the result shape/filter contract.

## Deferred

- No merge, deploy, Jenkins, dev QA, plan approval, transition-gate, prompt, dashboard, or TUI change.
- No routing evidence/scoring/catalog redesign.
- No mutation of route-owned `routing` payload by the provisioner; store worktree identity in a separate Frontier 003-owned namespace.
- No fetch/rebase/reset/branch cleanup policy expansion; this slice consumes the already recorded local remote-tracking ref.

## Blockers and exploration record

- `[BLOCK]` Exact Frontier 003 worktree metadata namespace/error-to-state mapping must be frozen by its plan before implementation.
- `[INFO]` No DB/schema/shared external service is involved; durable state is Markdown frontmatter plus local Git worktree metadata.
- `[INFO]` Independent exploration subagent unavailable: `agent thread limit reached`; parent explicitly authorized bounded solo fallback. No subagent claim was incorporated.
- Search path: public facade -> result/filter -> child projection/source/storage -> tick reload/filter -> worker workspace/backend ordering.
- Kept hypothesis: worker-time barrier, because it closes tick/retry/card/config races before backend construction.
- Rejected hypotheses: tick-time provisioning and shell-hook routing, because neither provides the same typed final attestation boundary.
