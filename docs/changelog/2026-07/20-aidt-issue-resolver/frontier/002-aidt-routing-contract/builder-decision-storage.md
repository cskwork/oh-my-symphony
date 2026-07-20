# Builder C - pure decision and AIDT storage extraction

## Outcome

Implemented the pure structured-source decision engine and extracted AIDT route-card persistence from the generic
file tracker. The decision layer maps immutable Git-object observations plus normalized Jira source into versioned
coordinator/child/stale projections. The storage adapter applies every coordinator in one whole-poll lock and rename
boundary without importing runtime policy into the tracker.

Owned product paths:

- `src/symphony/aidt_routing/decision.py`
- `src/symphony/trackers/aidt_routes.py`
- `src/symphony/trackers/file.py` (AIDT extraction only)

Owned tests:

- `tests/test_aidt_routing_decision.py`
- `tests/test_aidt_routing_storage.py`

The remaining `trackers/file.py` diff is exactly the already-owned Jira source refresh plus the opaque canonical
`routing` frontmatter key. It contains no AIDT constants, types, marker parser, planner, commit method, or pass-through
adapter.

## Public integration seam

```python
resolve_card(frontmatter, settings, catalog, *, now) -> RouteResolution

apply_route_resolutions(
    board,
    resolutions,
    *,
    precommit_hook=None,
    rename_fault_hook=None,
) -> AidtRouteBatchResult
```

`RouteResolution` exposes `coordinator`, `children`, `retained`, `projections`, `routed`, and
`blocked_identifiers`. Runtime should resolve the complete eligible coordinator set, then call
`apply_route_resolutions` once. Its `precommit_hook` should close over `recheck_catalog`; the adapter calls it only
after every projection lock is held and before the final board/source/ownership preflight.

Storage raises sanitized `AidtRoutingFailure` categories. A failure after any successful rename becomes
`AidtPartialApplyError(category="partial_apply")`; runtime must block the tick and let the next whole poll repair the
owned artifacts.

## TDD evidence

First decision RED:

```text
tests/test_aidt_routing_decision.py -x
ModuleNotFoundError: No module named 'symphony.aidt_routing.decision'
```

First storage RED:

```text
tests/test_aidt_routing_storage.py -x
ModuleNotFoundError: No module named 'symphony.trackers.aidt_routes'
```

Subsequent tracer loops captured and corrected supporting-score, explicit review-reason, stale-schema timestamp, and
retained-child review behavior before the final green run.

Final focused GREEN:

```text
tests/test_aidt_routing_decision.py
tests/test_aidt_routing_storage.py
tests/test_tracker_file.py
73 passed in 2.31s
```

Cross-slice contract/Git/decision/storage GREEN:

```text
tests/test_aidt_routing_contract.py
tests/test_aidt_routing_git_objects.py
tests/test_aidt_routing_decision.py
tests/test_aidt_routing_storage.py
82 passed in 33.33s
```

## Behavior proved

- A20-1188 selects only `viewer-api` at 95; no LMS child is projected.
- Evidence categories score once; component/context/code authority, supporting kind/keyword caps, hostile body
  isolation, component conflicts, shared-anchor ties, and disjoint multi-service decisions are deterministic.
- Coordinator routes use `aidt-route-object-v2`, the all-enabled commit/ref/binding maps, and a semantic fingerprint;
  old schemas are stale. Binding changes force a write without conflating semantic fingerprints.
- Selected children contain only their service/ref/commit/binding/evidence slice and remain
  `pending_fresh_base_equality`.
- Injected UTC clock values are stable across equal fingerprint plus binding polls. Source, catalog/object, binding,
  review, and retained-child changes recompute the owned projection.
- Every desired/current/retained card lock is acquired in sorted order. The final order is all children sorted, then
  all coordinators sorted.
- Pre-first-rename collision, source drift, count cap, and serialized-byte cap failures write no route cards.
- Failures after child rename one or two report `partial_apply`; the next poll repairs missing artifacts without
  deleting or rewriting already-correct child bytes/mtime.
- Existing local frontmatter, local `updated_at`, active/manual state, and body outside the route marker survive
  refresh. Retained children become stale, force coordinator review, and are never deleted or reset.
- Equal polls preserve serialized bytes and mtimes.

## Static and diff checks

```text
Ruff (owned product/tests plus trackers/file.py): All checks passed.
Pyright (decision, storage, trackers/file.py): 0 errors, 0 warnings, 0 informations.
Function-length AST check: [] (no new function exceeds 50 lines).
git diff --check: pass.
```

## Residual integration scope

No owned blocker remains. Runtime/core still must compose one complete coordinator list and supply the catalog
recheck closure; facade/runtime/full-suite verification belongs to the integration builder. Actual process death
between filesystem renames cannot be caught in-process, so durability remains the planned next-poll repair boundary,
not cross-file atomicity.
