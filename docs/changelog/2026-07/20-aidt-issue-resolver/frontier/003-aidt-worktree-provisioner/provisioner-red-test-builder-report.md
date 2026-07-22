# Frontier 003 provisioner RED-test builder report

Date: 2026-07-21

## Decision

The provisioner test artifact is collectible and statically clean. The 31 test functions remain intact and expand to
62 pytest cases. Normal baselines now come from a disposable two-revision SHA-1 repository, real coordinator/child
route cards, two fresh `load_route_dispatch_contract` attestations, and a retained real
`observe_service_binding` observation. No product, runtime, schema, other test module, live checkout, external service,
or project Git history was changed.

## Test-only correction

- Reused `frozen_git_repository` to create `old_base_sha`, then committed `base_sha` on local `aidt-prd`.
- Added the inert HTTPS origin and a local bare evidence repository whose `refs/heads/aidt-prd` equals `base_sha`.
- Moved the root checkout to unrelated history and retained tracked, untracked, and ignored dirt.
- Observed the production binding at `base_sha`, built real coordinator/child cards from its committed route/domain
  contents, loaded the public dispatch contract twice, and asserted card fingerprints, checkout SHA, repository
  binding, route-pair stability, and `fix/A20-1188`.
- Restored only `refs/remotes/origin/aidt-prd` to `old_base_sha`; the fetch double verifies the bare ref before applying
  the genuine old-to-current fixed-ref delta. It never performs a network fetch.
- Replaced a leaking class-wide fetch monkeypatch with per-fixture result overrides. Corrected binding drift to the
  production observer's first post-fetch call and second immediate pre-add call.
- Replaced direct attempt-file bytes with public CAS persistence. The mismatched-fetch negative now delegates the real
  public fetch/binding proof before returning a forged exact-type `FetchResult`.

Rejected alternatives: fabricated route/binding digests, a one-SHA no-op fetch baseline, a live origin, direct durable
file replacement, and a global runner monkeypatch. Each would make failures depend on test machinery rather than the
public contract.

## Coverage

The suite covers lazy facade exports; public Git/recovery boundaries; fresh create ordering; post-fetch and pre-add
route/binding drift; prepared absent/exact/ambiguous restart; ready resume and before-run barriers; admission and
attempt ordering; deny-by-default and verified cleanup authority; all six process-crash seams; individual durable
partial writes; exact fetch/result consumption; canonical DTO bytes; repr redaction; stale-failure ordering; and
forbidden command/fallback safety.

## Verification

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q \
  tests/test_aidt_worktree_provisioner.py
```

Result: `62 tests collected in 0.24s` from 31 unchanged test functions.

```text
rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_provisioner.py
```

Result: expected RED, `30 failed, 32 passed in 234.88s`.

```text
rtk ../../.venv/bin/ruff check --no-cache \
  tests/aidt_provisioner_support.py tests/test_aidt_worktree_provisioner.py
```

Result: `All checks passed!`

```text
rtk ../../.venv/bin/pyright --pythonpath ../../.venv/bin/python \
  tests/aidt_provisioner_support.py tests/test_aidt_worktree_provisioner.py
```

Result: `0 errors, 0 warnings, 0 informations`.

No-index `git diff --check` whitespace diagnostics emitted no errors for both test files and this report.

## Current expected RED matrix

| Failure family | Cases | Current evidence |
|---|---:|---|
| Public recovery/type/facade boundary | 3 | Private `_state_from_snapshot`, non-`StableWorktreePaths` path parameters, and missing lazy facade exports |
| Ready/before-run re-attestation | 4 | Unrelated ref/registration changes, a forged guard path, and locked route drift are accepted |
| Admission/sealed-capability ordering | 2 | Invalid `PreparedAidtWorktree` values are accepted; non-due backoff reaches fetch |
| Cleanup/removal proof and barriers | 8 | Removal delta reconstruction fails; proof-only recovery and locked route/binding barriers do not preserve correctly |
| Per-write restart convergence | 9 | Prepared/ready manifest or ownership crashes and removing/removed sidecar crashes do not converge through public restart |
| Exact fetch-result consumption | 1 | A real fetch followed by a mismatched returned `FetchResult` is ignored |
| Canonical construction | 1 | Some durable DTOs are still positionally constructed |
| Repr sealing | 1 | Workspace path/lease capability values remain visible |
| Stale failure ordering | 1 | A stale failure becomes `persistence_failed` instead of preserving newer ready bytes as `scope_changed` |
| **Total** | **30** | All failures are in the current product/facade behavior; fixture construction and static gates are green |

## Scope

Changed only:

- `tests/aidt_provisioner_support.py`
- `tests/test_aidt_worktree_provisioner.py`
- this report

No commit, network access, external service, live AIDT repository, source-module edit, runtime edit, or schema edit was
performed.
