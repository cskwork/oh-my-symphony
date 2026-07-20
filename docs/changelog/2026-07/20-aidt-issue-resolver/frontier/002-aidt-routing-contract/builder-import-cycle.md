# Frontier 002 iteration 3 - import-cycle correction

## Outcome

Removed the facade/runtime/storage import cycle without changing the frozen public API. Importing the routing package,
its contract submodule, or `symphony.trackers.aidt_routes` no longer loads `aidt_routing.runtime`. Accessing either
runtime-owned public name loads the runtime on demand and returns the exact function object defined there.

The real-world boundary is now explicit: contract and persistence consumers can initialize independently, while the
orchestrator still receives the complete routing composition functions through the approved package facade.

## Root cause and correction

The eager facade statement importing `.runtime` changed the intended dependency direction into this cycle:

```text
trackers.aidt_routes -> aidt_routing package init -> runtime -> trackers.aidt_routes
```

The smallest correction is a typed module `__getattr__` facade:

- contract-owned names remain eager and safe;
- `TYPE_CHECKING` preserves static types for `filter_routing_candidates` and `run_aidt_routing`;
- runtime-owned names import only on first attribute access and are cached in the package namespace;
- returned values are the runtime functions themselves, not wrappers, so identity and signatures are unchanged;
- the approved 11-name `__all__` remains byte-for-byte equivalent.

No runtime, storage, contract, orchestrator, tracker, workflow, or activation behavior changed.

## TDD evidence

Trusted verifier red reproduced before the correction:

```text
standalone import symphony.trackers.aidt_routes:
ImportError: cannot import name 'apply_route_resolutions' from partially initialized module

isolated tests/test_aidt_routing_storage.py:
1 error during collection
```

A new public-behavior regression runs three import permutations in fresh bounded Python subprocesses. Before the
product change its exact result was:

```text
2 failed, 1 passed, 15 deselected
```

The storage-first case reproduced the circular import. The package-first case proved the facade eagerly loaded
`symphony.aidt_routing.runtime`. The already-working public-runtime-first case passed.

After the lazy facade change:

```text
3 passed, 15 deselected
```

The storage-first and package-first cases both assert that `.runtime` is absent from `sys.modules` until a public
runtime export is requested. All three cases assert facade/runtime function identity.

## Verification

Standalone fresh-process imports:

```text
storage-first: ok
package-first: ok
public-runtime-first: ok
core-public-facade: ok
```

Five suites as independent collections:

```text
contract: 25 passed
Git objects: 39 passed
decision: 8 passed
storage: 11 passed
runtime: 18 passed
```

Combined and affected matrices:

```text
five routing suites: 101 passed in 44.03s
plan-listed routing/Jira/tracker/health/service/web regressions: 331 passed in 56.16s
```

Static and structural gates:

```text
Ruff over the complete approved product/test scope: All checks passed
Pyright over the complete approved product scope: 0 errors, 0 warnings, 0 informations
git diff --check: passed
git diff --no-index --check on both untracked owned files: no whitespace findings
__getattr__: 12 lines, control-flow nesting 1
fresh-process regression: 25 lines, control-flow nesting 0
```

Repository-wide pytest parity and fresh semantic verification remain assigned to the fresh Frontier verifier. No
network, live Jira/AIDT mutation, workflow activation, commit, worktree operation, deployment, or out-of-scope edit
was performed.
