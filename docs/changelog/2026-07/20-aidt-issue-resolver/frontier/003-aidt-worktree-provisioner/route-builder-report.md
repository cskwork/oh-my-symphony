# Frontier 003 route builder report

Date: 2026-07-21
Branch: `run/symphony-aidt-orchestrator-20260720`
Scope: route-side foundation only; no commit

## Outcome

PASS. The route layer now nominates only validated pending children, re-attests the coordinator and child as one
stable locked pair, derives issue/change/branch identity independently, and exposes one shared repository-binding-v1
observer for catalog routing and worker-time service rechecks.

## Theory and implementation

- `AidtRoutingResult.provisionable_child_identifiers` is the final/defaulted field. It is an exact bounded
  `frozenset`, contains only canonical child IDs, is a subset of the complete managed block set, and atomically
  normalizes invalid/disabled/denied/failure combinations to the existing canonical failure.
- Successful runtime composition nominates only routed `RouteCardProjection` children with exact
  `aidt-route-object-v2` / `child` / `pending_fresh_base_equality` identity. Review, retained, stale, coordinator, and
  malformed projections remain blocked.
- `filter_routing_candidates` keeps its old two-argument behavior. The optional third set releases nominated children
  in tracker order while continuing to retain unmanaged issues and exclude every other managed ID.
- `load_route_dispatch_contract` uses the same `FileBoardTracker._ticket_lock_path` files as
  `apply_route_resolutions`, acquired in canonical case-folded coordinator/child order. Under both locks it performs
  coordinator/child read-hash-lstat, rereads both, verifies the two complete observations plus final identities, and
  rejects exact-name/case collisions before parsing the second stable bytes.
- The frozen DTO binds coordinator/child/service/catalog/ref/SHA/fingerprint identity plus `route_pair_digest`,
  normalized `issue_type`, `change_kind`, and independently derived branch. Normalization is strip + Unicode
  case-fold; accepted types are bug/story/task/sub-task/improvement/new feature; bug maps to fix and all other accepted
  types map to feat.
- The durable pair digest hashes canonical route-owned `source` + `routing` projections and the sorted complete child
  set. Raw file hashes are used only for read stability, so body/operator-note edits do not steal ownership.
- The public facade lazily exports the DTO/loader and repository observer without loading runtime, worktree, or Git
  modules on an ordinary package import. Loading the dispatch DTO alone also does not load the Git observer.
- `aidt-repository-binding-v1` includes service ID/kind/catalog checkout, fixed ref/full commit/object format,
  domain-separated normalized HTTPS/SSH origin digest, canonical top-level/common-Git/object-directory path +
  device/inode identities, and sorted required immutable blob object IDs. Raw origin URLs are not retained or
  rendered.
- `observe_catalog` and `observe_service_binding(settings, service_id)` share the same service observer and serializer.
  Recheck recomputes the binding, so same-HEAD origin/repository identity drift fails closed.

## TDD evidence

Red first:

- 2 collection failures: missing `AidtRouteDispatchContract`/loader and missing repository-binding-v1 exports.

Green isolated suites:

- `tests/test_aidt_route_dispatch_contract.py`: 14 passed.
- `tests/test_aidt_routing_contract.py`: 99 passed.
- `tests/test_aidt_routing_runtime.py`: 20 passed.
- `tests/test_aidt_routing_git_objects.py`: 52 passed.
- `tests/test_aidt_routing_decision.py`: 8 passed.
- `tests/test_aidt_routing_storage.py`: 11 passed.
- Total: 204 passed, 0 failed.

Focused gates:

- Fresh-process import/lazy facade permutations: 5 passed, 0 failed (included in the total above).
- Ruff on the exact product/test scope: pass.
- Pyright on the changed product and new/changed focused tests: 0 errors, 0 warnings.
- AST across all five owned product modules: 157 functions, 0 over 50 lines, 0 nesting over four.
- Tracked diff check plus explicit owned-file trailing-whitespace/EOF scan: pass, 0 errors.

## Scope and safety

Changed product files only:

- `src/symphony/aidt_routing/__init__.py`
- `src/symphony/aidt_routing/contract.py`
- `src/symphony/aidt_routing/dispatch.py`
- `src/symphony/aidt_routing/runtime.py`
- `src/symphony/aidt_routing/git_objects.py`

Changed tests only:

- `tests/test_aidt_route_dispatch_contract.py`
- `tests/test_aidt_routing_git_objects.py`
- `tests/test_aidt_routing_runtime.py`

No network, live Git remote, live Jira, AIDT checkout, worktree package, workspace/core integration, or destructive Git
operation was used. Git tests used temporary local repositories; product parsing saw only canonical fixture HTTPS
origin strings and executed no fetch.

## Integration handoff

The consumer must pass `result.provisionable_child_identifiers` as the third argument to
`filter_routing_candidates`, then treat tick nomination only as entry to the specialized worker barrier. Create,
resume, and pre-backend checks must call `load_route_dispatch_contract` again and compare the returned
`route_pair_digest` plus `observe_service_binding(...).repository_binding_digest`; no generic workspace fallback is
authorized by this route-side foundation.
