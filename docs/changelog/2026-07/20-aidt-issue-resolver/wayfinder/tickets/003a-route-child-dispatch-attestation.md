# 003a - Route-child dispatch attestation

Route: RESEARCH / HISTORICAL UMBRELLA

Status: historical umbrella; aggregate verification passed

Blocked by: 002

Unblocks: 003b, 003e, 003g

## Goal and interface contract

Own the routing-to-provisioning seam: nominate only fresh eligible route children and turn one stable coordinator/
child pair into one bounded `AidtRouteDispatchContract`. Callers learn only the nominated identifier set and the
side-effect-free loader; card parsing, pair locking, digesting, and rejection stay inside the module.

## Historical file ownership

- `src/symphony/aidt_routing/dispatch.py`
- `src/symphony/aidt_routing/contract.py`
- `src/symphony/aidt_routing/runtime.py`
- `src/symphony/aidt_routing/__init__.py`
- `tests/test_aidt_route_dispatch_contract.py`
- `tests/test_aidt_routing_runtime.py` nomination/filter cases

This six-file, well-over-500-line executed slice is not a Build ticket. It records one deep-module interface for
review and commit attribution; further implementation must be separately sliced.

## Acceptance and proof

- Only exact pending fresh-base children are provisionable; coordinators, retained, stale, review, and malformed
  cards remain blocked in tracker order.
- The loader reads a stable locked pair, validates exact route/source/catalog identity, and returns bounded failures
  without Git or workspace mutation.
- Proof surfaces: `tests/test_aidt_route_dispatch_contract.py`, the named routing-runtime nomination cases, and
  `R-LOOP.md` R1/R4 plus the preserved affected matrices.

## Scope boundary

Does not own repository mutation, durable worktree records, provisioning, Core lifecycle, or operator activation.
