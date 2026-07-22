# Frontier 003 provisioner verifier-gap repair

Date: 2026-07-21

## Decision

Repaired exactly the three accepted provisioner verification gaps in
`src/symphony/aidt_worktree/provisioner.py`. The lazy facade, tests, support fixture, manifest/schema helpers,
Git-state/recovery APIs, route modules, and runtime were not changed.

The final product proves ready Git state before any ready suffix repair, validates every ownership identity/scope/path
field against a manifest-derived record before accepting or advancing an owner revision, and carries a frozen local
failure context that reacquires the real common-Git/manifest lock pair after repository identity is known.

## Product repair

### Ready proof precedes suffix convergence

- `_prepare` now calls `_prove_ready` before `_reconcile_ready_sidecars`.
- `_prove_ready` contains only the manifest/route/binding/Git recovery proof; `_require_ready_attempt` is applied after
  the exact ownership/attempt suffix has converged.
- A ready proof failure therefore performs no owner or attempt suffix write. Failure persistence also skips a
  manifest/attempt revision mismatch, preserving the exact partial-ready sidecar bytes and original proof failure.

### Ownership is exact before revision or tombstone advancement

- `_owner_record` builds the canonical ownership value from the stable paths and manifest.
- `_require_owner_revision` compares the complete durable record, including schema, record/manifest revisions,
  identifier, service, workspace root/path, manifest path, route-pair digest, tombstone, creation time, and the allowed
  transition timestamp.
- Ready, cleanup-ready, removing, and removed paths validate the current or sole legal predecessor before accepting,
  advancing, or tombstoning it. `_advance_owner` writes a newly constructed canonical record rather than propagating
  fields from the predecessor.

### Failure persistence reacquires the real lock lineage

- `_FailureContext` is a frozen per-call value containing the admission, stable paths, and optional real common-Git
  lock. It is local to `prepare`; no mutable context is stored on the shared provisioner instance.
- Before identity resolution, failure persistence may acquire only the manifest lock. Immediately after identity
  resolution, `prepare` replaces the local context with the exact `common_git_lock_path`.
- `_persist_failure` then reacquires `ordered_worktree_locks(common_git_lock, manifest_lock)` before its scope,
  disposition, action, attempt-revision, manifest-revision, and CAS checks. Stale or partial lineages are preserved.

## RED to GREEN evidence

Focused verifier-gap command:

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_provisioner.py::test_ready_resume_rejects_conflicting_ownership_without_sidecar_mutation \
  tests/test_aidt_worktree_provisioner.py::test_ready_partial_predecessor_waits_for_git_proof_before_sidecar_repair \
  tests/test_aidt_worktree_provisioner.py::test_failure_after_identity_resolution_reacquires_ordered_locks
```

| Stage | Result |
| --- | --- |
| Accepted pre-change RED | `3 failed in 13.53s` |
| First product GREEN | `3 passed in 14.64s` |
| Final-byte GREEN after exact lineage tightening | `3 passed in 12.80s` |
| Final full provisioner suite | `65 passed in 340.36s (0:05:40)` |

The full 65-case result contains the original 62 accepted cases plus the three verifier-gap cases.

## Regression and quality evidence

| Gate | Final result |
| --- | --- |
| Contract, manifest, and Git-state foundation | `241 passed in 28.22s` |
| Persisted recovery proofs | `153 passed in 210.72s (0:03:30)` |
| Route dispatch and routing Git objects | `66 passed in 27.62s` |
| Ruff `--no-cache` over product, facade, support, and all provisioner tests | `All checks passed!` |
| Pyright over the same slice | `0 errors, 0 warnings, 0 informations` |
| Static recovery/public-boundary/lazy-facade subset | `3 passed in 1.62s` |
| Provisioner AST | `functions=72 max_lines=45 max_nesting=3` |
| Tracked whitespace | `git diff --check` exit 0, zero diagnostics |
| Product/facade no-index whitespace | expected content-difference exit 1, zero diagnostics for both files |

The executable full suite retained the exact fetch/add/plain-remove command and forbidden-fallback spy. No network,
external service, live repository, runtime/schema/manifest/Git/route edit, branch operation, commit, or deployment was
performed.

## Changed files

- `src/symphony/aidt_worktree/provisioner.py`
- this report
