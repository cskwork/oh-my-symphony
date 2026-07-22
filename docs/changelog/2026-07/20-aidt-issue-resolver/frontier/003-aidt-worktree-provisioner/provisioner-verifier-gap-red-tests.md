# Frontier 003 provisioner verifier-gap RED tests

Date: 2026-07-21

## Decision

Added exactly three ordinary provisioner regression cases. They use the existing disposable SHA-1 fixture, real route
cards and binding observation, public ownership CAS, public repository identity and lock-path APIs, genuine Git state
changes, and the existing runner/crash seams. All three are RED against the current product for the confirmed verifier
gaps; the pre-existing 62 cases remain GREEN.

No product, runtime, schema, fixture-support, external service, network, live repository, or project Git history was
changed.

## Executable gaps

1. `test_ready_resume_rejects_conflicting_ownership_without_sidecar_mutation`
   persists a structurally valid next-revision ownership record through `persist_ownership`, but changes its service and
   route-pair identity away from the ready manifest. Resume must reject it as `registry_invalid` and preserve manifest,
   ownership, and attempt bytes exactly.
2. `test_ready_partial_predecessor_waits_for_git_proof_before_sidecar_repair`
   delegates the real ready-manifest CAS and then models process loss. This naturally leaves the ready manifest with its
   ownership/attempt predecessors. After a real unrelated ref is added, resume must fail the ready Git proof before
   repairing either sidecar; both byte strings must remain exact.
3. `test_failure_after_identity_resolution_reacquires_ordered_locks`
   injects a fetch command failure only after provisioning has resolved and acquired the real common-Git/manifest lock
   pair. Recording wrappers delegate both real public lock context managers. Failure persistence must reacquire the same
   ordered pair, with no manifest-only acquisition.

## Exact RED evidence

Command:

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_provisioner.py::test_ready_resume_rejects_conflicting_ownership_without_sidecar_mutation \
  tests/test_aidt_worktree_provisioner.py::test_ready_partial_predecessor_waits_for_git_proof_before_sidecar_repair \
  tests/test_aidt_worktree_provisioner.py::test_failure_after_identity_resolution_reacquires_ordered_locks
```

Result:

```text
FFF                                                                      [100%]
___ test_ready_resume_rejects_conflicting_ownership_without_sidecar_mutation ___
E   Failed: DID NOT RAISE <class 'symphony.aidt_worktree.contract.AidtWorktreeFailure'>

___ test_ready_partial_predecessor_waits_for_git_proof_before_sidecar_repair ___
E   AssertionError: ready sidecars changed before the Git proof succeeded

_______ test_failure_after_identity_resolution_reacquires_ordered_locks ________
E   AssertionError: failure persistence did not reacquire ordered locks

3 failed in 14.57s
```

These are behavior failures, not fixture failures: each case reaches the asserted public lifecycle path. The first
returns success, the second reaches Git-proof rejection only after changing ownership bytes, and the third records the
existing `manifest-only:enter` event where the assertion expected a second `ordered:enter` with the captured real
common-Git and manifest paths.

## Preserved baseline and static gates

Collection:

```text
65 tests collected in 0.31s
```

Original 62 cases, excluding only the three names above:

```text
62 passed, 3 deselected in 304.09s (0:05:04)
```

Ruff:

```text
rtk ../../.venv/bin/ruff check --no-cache \
  tests/aidt_provisioner_support.py tests/test_aidt_worktree_provisioner.py
All checks passed!
```

Pyright:

```text
rtk ../../.venv/bin/pyright --pythonpath ../../.venv/bin/python \
  tests/aidt_provisioner_support.py tests/test_aidt_worktree_provisioner.py
0 errors, 0 warnings, 0 informations
```

## Scope

Changed only:

- `tests/test_aidt_worktree_provisioner.py`
- this report

Stopped at RED as required. No product-green repair was attempted.
