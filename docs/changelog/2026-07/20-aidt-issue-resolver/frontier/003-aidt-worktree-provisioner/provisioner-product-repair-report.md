# Frontier 003 provisioner product repair report

Date: 2026-07-21

## Decision

The accepted local provisioner contract is GREEN on the final product bytes: all 62 cases pass. The repair changes
only the provisioner, its lazy facade, and this report. The existing manifest and Git-state public helpers were
sufficient; no schema, manifest helper, recovery API, runtime, test, or live system was changed.

The provisioner now treats the route pair, repository binding, manifest bundle, attempt admission, and Git proof as
one fail-closed capability. Fresh creation carries genuine S0/S1/S2 states. Restart paths consume the three public
recovery proofs. Cleanup remains deny-all by default and can remove only through exact revision-2 authorization plus
the same active lease.

## Product repair

- Added exactly seven lazy facade exports without loading provisioner, Git-state, or manifest modules during a cold
  facade import.
- Replaced every widened path parameter with `StableWorktreePaths`; removed the dynamic path narrowing helper.
- Deleted `_state_from_snapshot` and manual removal-state inference. Uninterrupted create/remove pass genuine before
  and after `RepositoryState` values to the existing delta validators.
- Consumed exact `FetchResult` values and rejected absent target-registration proof rather than substituting a value.
- Used `prove_prepared_recovery`, `prove_ready_recovery`, and `prove_removed_recovery` for prepared, ready,
  before-run, cleanup, removing, and removed restart barriers.
- Re-read and compared the exact route inside common-Git then manifest locks; freshly verified the binding before
  add/remove and before backend admission.
- Enforced exact admission, guard, attempt, workspace, manifest, revision-2 authorization, and active non-competing
  lease fields.
- Repaired only the documented prepared/ready/removing/removed multi-file persistence suffixes. Unknown or conflicting
  durable shapes remain fail-closed; removed re-entry re-proves the recorded removal before repairing the tombstone.
- Scoped failure persistence to the same identifier, pair, generation, active disposition, mutation phase, and owned
  revision lineage. It cannot downgrade a manual/ready record or overwrite a later due attempt, and it no longer
  invents an all-zero common-Git lock.
- Kept all six process-interruption hook sites. Test-local `BaseException` process-loss signals pass through public
  methods, while ordinary `Exception` failures retain durable failure handling.
- Constructed `AidtWorktreeManifest`, `OwnershipRecord`, `PostProof`, and `RemovalProof` by keyword and added bounded
  DTO representations that omit workspace paths and lease tokens.

## RED to GREEN evidence

Initial accepted suite:

```text
tests/test_aidt_worktree_provisioner.py
30 failed, 32 passed in 432.54s
```

Vertical slices:

| Slice | Result |
|---|---|
| Lazy facade, exact path types, sealed admission, redacted representations | `5 passed, 57 deselected in 4.32s` |
| Create/prepared/ready/before-run/fetch plus partial transitions | `23 passed, 6 failed, 33 deselected in 142.97s`; remaining failures identified one added-phase predecessor and the not-yet-migrated cleanup rows |
| Cleanup, authorization, removing/removed recovery, all partial writes | `33 passed, 29 deselected in 498.18s` |
| Failure disposition and revision-lineage tightening | `7 passed, 55 deselected in 21.75s` |
| Final accepted suite on final bytes | `62 passed in 326.85s` |

The earlier complete GREEN before the final lineage tightening was also `62 passed in 411.91s`; the final rerun above
is the acceptance result.

## Regression and quality evidence

| Gate | Final result |
|---|---|
| Worktree contract, manifest, and Git-state foundation | `241 passed in 28.72s` |
| Persisted recovery proofs | `153 passed in 272.46s` |
| Route dispatch and routing Git objects | `66 passed in 27.81s` |
| Ruff `--no-cache` over product, facade, support, and accepted tests | `All checks passed!` |
| Pyright over the same product/test slice | `0 errors, 0 warnings, 0 informations` |
| Lazy/static public-boundary subset | `3 passed, 59 deselected in 2.33s` |
| Provisioner AST | `functions=66 max_lines=45 max_nesting=3` |
| Product/facade no-index whitespace | expected content-difference exit 1, zero diagnostics for both files |
| Worktree tracked whitespace | `git diff --check` exit 0, zero diagnostics |

The executable command/fallback spy is part of the 62-case suite. It observed only the exact forced fetch,
`worktree add --no-track -b`, and plain `worktree remove` mutation vectors and rejected reset, rebase, checkout,
switch, prune, force, branch deletion, filesystem deletion, and generic fallback.

## Frozen-contract interpretations

- The PLAN scalar summary says run/lease tokens are 64 hex, but the frozen `CompletionAuthorization` validator and
  accepted fixtures require a 32-hex `run_id` with byte-equal `owning_lease_token`. The repair preserves the executable
  32-hex public contract and does not broaden the schema.
- The draft attack requested a product crash sentinel, while the accepted suite deliberately models process loss with
  test-local `BaseException` types and the frozen facade has no crash-sentinel export. No product exception was added.
- A restart after `after_forced_fetch_before_prepared` performs another exact fetch from a new genuine S0. A no-op
  fixed-ref delta remains valid while all unrelated state is still prohibited.

## Changed files

- `src/symphony/aidt_worktree/provisioner.py`
- `src/symphony/aidt_worktree/__init__.py`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/provisioner-product-repair-report.md`

No commit, network fetch, Jira operation, external service, live AIDT checkout, force cleanup, branch deletion, reset,
rebase, schema change, or test edit was performed.
