# Frontier 002a iteration 1 builder report

## Verdict

PASS for fresh verification.

The public result constructor is now the single total trust boundary. Every valid exact value remains unchanged;
any malformed field atomically becomes the canonical enabled `internal_error` failure before repr, structured logs,
health, or dispatch can consume it. Product/test edits stayed within the approved three paths. Full repository parity
and final baseline judgment remain for the fresh verifier.

## Theory and root cause

`AidtRoutingResult` is copied directly into orchestrator health, failure logging, candidate filtering, and the global
dispatch gate. Its old `__post_init__` independently repaired only status/category/ref. Runtime annotations did not
prevent hostile booleans, counts, containers, identifier text, or unhashable subclasses from surviving or raising.
The root fix is therefore one closed constructor invariant, not repeated sanitation in each consumer.

## Red proof

Before the product change, this focused command failed both new regressions:

```bash
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider \
  tests/test_aidt_routing_contract.py::test_public_result_combined_verifier_payload_is_atomically_sanitized \
  tests/test_aidt_routing_runtime.py::test_core_normalizes_combined_hostile_result_before_every_surface
```

Exact result: `2 failed in 0.42s`.

- The contract case raised `TypeError` on membership of an unhashable `str` subclass; pytest's failing result repr
  also rendered the injected count, mapping, list, and hostile object payload.
- The real `_on_tick` case reached logging/health with malformed values and invoked the hostile object's repr three
  times. Routed/review/child/failure payloads survived in result repr, captured `aidt_routing_failure`, and health.

After the constructor correction, the identical command reported `2 passed in 0.40s`.

## Implementation

`src/symphony/aidt_routing/contract.py` now enforces:

- exact booleans and exact non-boolean integers at their named coordinator/child caps;
- an exact `frozenset`, the combined 2,500-value cap, canonical coordinator/child grammar, 256-byte complete/key
  bounds, and the existing 48-byte service bound;
- exact-string status/category/ref types and only valid category/ref pairs;
- atomic canonical failure replacement for any invalid field;
- total `AidtRoutingFailure` category/identifier sanitation with type checks before membership or string operations.

The valid path performs no coercion or field replacement. The invalid path sets all ten public fields directly on
the frozen instance without recursively constructing another result. No consumer, runtime producer, facade, storage,
or import seam changed.

`tests/test_aidt_routing_runtime.py` also corrects only the synthetic helper defect by making `child_count`
non-negative. It adds core-first import coverage and one real-tick hostile-result regression. The regression proves
canonical repr/log/health values, global dispatch false, no legacy normalization, no candidate fetch, and no hostile
object repr invocation.

## Boundary coverage

The contract matrix proves:

- exact zero/max counts, both booleans, all four statuses, every allowed category with null ref, and canonical card/
  service refs remain unchanged;
- exact 256-byte coordinator/full child values, 48-byte services, and 2,500 blocked values pass; 257-byte complete/
  key values, 49-byte services, malformed separators/case/path/traversal/control/Unicode, and cap-plus-one fail closed;
- bool-as-int, integer/string/frozenset subclasses, negative/overflow counts, sets/lists, unknown/orphaned/forbidden
  error pairs, and list/dict/set/unhashable values never raise and normalize the whole result;
- frozen assignment fails, `dataclasses.replace` revalidates, normalized hash/repr are safe, and valid boundary field
  objects retain identity;
- `AidtRoutingFailure` preserves exact valid service/card refs and maps malformed categories to `internal_error` and
  malformed identifiers to null.

## Green verification

| Gate | Exact result |
|---|---|
| Focused red regressions after fix | 2 passed in 0.40s |
| Isolated contract suite | 99 passed in 0.28s |
| Isolated Git-object suite | exit 0; 39 collected/passed |
| Isolated decision suite | 8 passed; 8 collected |
| Isolated storage suite | 11 passed in 0.26s |
| Isolated runtime suite | 20 passed in 1.10s |
| Five isolated routing suites | 177 passed total |
| Four fresh import permutations | 4 passed in 1.63s |
| Routing/Jira/tracker/health/service/web matrix | exit 0; 407 collected/passed |
| Broader Jira/tracker/orchestrator/service/web matrix | exit 0; 545 collected/passed |
| Ruff over complete approved Frontier 002 product/test scope | `All checks passed!` |
| Pyright over complete approved Frontier 002 product scope | 0 errors, 0 warnings, 0 informations |
| AST over the three owned paths | `[]`; no function over 50 lines or nesting over four |
| Tracked `git diff --check` | exit 0, no output |
| No-index whitespace checks for the three untracked owned files | empty output; expected exit 1 for content difference |

The plan's wildcard broad-suite spelling referenced nonexistent `tests/test_service_*.py` and zsh stopped before
pytest with `no matches found`. The rerun enumerated the current repository's actual orchestrator/service/web files;
all 545 collected tests passed. This was a command-glob mismatch, not a test failure.

Four fresh processes cover storage-first, package-first, public-runtime-first, and core-first. Facade/runtime callable
identity holds, and storage/package-first keep `symphony.aidt_routing.runtime` lazy until public runtime access.

## Scope and handoff

Changed only:

- `src/symphony/aidt_routing/contract.py`
- `tests/test_aidt_routing_contract.py`
- `tests/test_aidt_routing_runtime.py`
- this builder report

The three product/test files were already untracked Frontier 002 paths in the shared worktree; no unrelated existing
change was modified. No GOAL, PLAN, QA, state, R-LOOP, facade, runtime, core, storage, or finalization artifact changed.
No commit, activation, network access, external mutation, or full repository pytest run was performed. Full parity
and the accepted doctor baseline are intentionally left to the fresh verifier.
