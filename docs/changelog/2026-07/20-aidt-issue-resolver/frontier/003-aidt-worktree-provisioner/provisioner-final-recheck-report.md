# Frontier 003 Provisioner Final Recheck

Date: 2026-07-21

## Decision

PASS. The final local product has zero required corrections and zero recommended corrections.

All original twelve MUST corrections, all three SHOULD corrections, and the final three verifier-gap regressions are
closed on the reviewed bytes. The implementation provisions, resumes, attests, recovers, and removes only through the
frozen route, manifest, repository-binding, Git-state, authority, lease, and durable-attempt contracts. No synthetic
repository state, direct recovery path inference, private Git API, destructive fallback, or generic workspace fallback
was found.

## Review scope

The recheck read the applicable repository instructions; the root and Frontier 003 plans and amendments; ticket,
integration, and provisioner briefs; the draft attack, product repair, product verification, verifier-gap RED, and
verifier-gap repair reports; the current provisioner and lazy facade; manifest, Git-state, routing Git-object, route
dispatch, and route-writer public contracts; the disposable support fixture; and every one of the 65 collected
provisioner cases.

Reviewed product/test byte identities:

| File | SHA-256 |
| --- | --- |
| `src/symphony/aidt_worktree/provisioner.py` | `ebafcb6055ab2a77bb861d96f826ab6579edcba4cf9f31eb90d6e8ce3bf51aed` |
| `src/symphony/aidt_worktree/__init__.py` | `13f3dce7cb177164c70d2fb0730c63455f79253bc87a8e4c13480e989e49db81` |
| `tests/aidt_provisioner_support.py` | `68ab9f46d9ec3de7a1385b850b8e719eaa4e43cf02285409d507d90f23ca1034` |
| `tests/test_aidt_worktree_provisioner.py` | `f15aad66ab4ffd6da6ef13897e6308d5bcf6a678eb28881b605c68597de18e0f` |

## Original correction closure

| Finding | Status | Final evidence |
| --- | --- | --- |
| MUST-1 lazy public provisioner surface | Closed | The seven frozen exports are type-only at facade import and are resolved only by the closed lazy branch (`__init__.py:115-123`, `:213-223`, `:336-347`). Cold-process proof stayed free of provisioner, Git-state, and manifest modules until symbol access. |
| MUST-2 genuine persisted Git proof | Closed | Creation carries real S0/S1/S2 `RepositoryState`; removal carries real cleanup-pre/post state. Restart paths call only public `prove_prepared_recovery`, `prove_ready_recovery`, and `prove_removed_recovery` (`provisioner.py:33-57`, `:376-412`, `:569-590`, `:701-761`). No synthetic-state helper remains. |
| MUST-3 branch-retained removal recovery | Closed | `prove_removed_recovery` is the sole proof-only finalizer after classification (`provisioner.py:701-743`, `:829-861`). It preserves mixed shapes and performs no second remove. |
| MUST-4 exact ready/cleanup target and unrelated state | Closed | Ready resume and cleanup-pre use the public recovery proof, preserving dirty descendant ticket work while rejecting root, protected, ref, registration, target, upstream, and ancestry drift (`provisioner.py:569-590`, `:655-699`, `git_state.py:537-628`). |
| MUST-5 locked route/binding barriers | Closed | Before-run and every cleanup/recovery path re-read the exact route and freshly verify the repository binding while common-Git then manifest locks are held; destructive paths repeat both immediately before mutation (`provisioner.py:261-295`, `:611-743`). |
| MUST-6 exact ready authorization revision | Closed | Authorization requires ready manifest revision exactly `2`, the current generation/pair, byte-equal run/lease token, matching active non-competing lease, and injected authority verification (`provisioner.py:863-886`). |
| MUST-7 exact failure ownership and real lock lineage | Closed | A frozen per-call `_FailureContext` captures the real common-Git lock after identity resolution; persistence reacquires that exact ordered pair and writes only the same identifier/pair/generation/action/revision/manifest lineage (`provisioner.py:90-95`, `:246-259`, `:1183-1222`). Pre-identity failure alone may use the manifest lock. |
| MUST-8 recoverable multi-file transitions | Closed | Prepared, ready, removing, and removed paths accept and converge only their legal suffix predecessors under the lifecycle locks (`provisioner.py:515-557`, `:811-861`). Manifest-derived ownership reconstruction compares all identity, scope, path, revision, tombstone, and timestamp fields before ready/removing/removed acceptance or advancement (`provisioner.py:1012-1118`). All eleven individual partial-write cases pass. |
| MUST-9 sealed admissions and guards | Closed | DTO construction validates bounded exact fields/types; prepare and before-run compare identifier, generation, pair, attempt revision/action, manifest revision, and workspace path against current durable state (`provisioner.py:97-190`, `:261-295`, `:953-966`). Manual, non-due, swapped, or forged capabilities do not reach Git/backend proof. |
| MUST-10 six crash seams | Closed | All four create and two cleanup seams remain at the bound mutation boundaries (`provisioner.py:363`, `:371`, `:431`, `:473`, `:696`, `:758`). Process-loss `BaseException` fixtures restart through public methods without an ordinary failure write; ordinary exceptions retain bounded failure persistence. |
| MUST-11 executable behavioral evidence | Closed | The suite contains real disposable SHA-1 repositories, canonical HTTPS fixture origin, exact production fetch-vector double, real worktree add/remove and state observations, route/binding drift, authority/lease, crash, durable-byte, and forbidden-command assertions. Fresh result: `65 passed`. |
| MUST-12 typed public boundary | Closed | Every transition path uses `StableWorktreePaths` and public Git names; Pyright reports zero errors, warnings, and information. No dynamic path-narrowing workaround remains. |
| SHOULD-1 consume exact public returns | Closed | Exact `FetchResult` SHA/binding values are checked before prepared persistence and a missing registration digest is an explicit protocol failure (`provisioner.py:354-360`, `:478-500`). |
| SHOULD-2 redact representations | Closed | Guard, prepared result, and active lease use bounded explicit reprs that omit workspace paths, workflow/pair digests, and run/lease tokens (`provisioner.py:117-190`). |
| SHOULD-3 keyword durable construction | Closed | Manifest, ownership, post-proof, and removal-proof records are keyword-constructed; canonical durable-byte tests remain exact (`provisioner.py:478-513`, `:655-692`, `:968-1038`). |

## Final verifier-gap closure

1. Proof-before-sidecar ordering is closed: ready handling calls `_prove_ready` before
   `_reconcile_ready_sidecars`; a failed Git proof preserves both sidecar byte strings (`provisioner.py:329-333`).
2. Ownership alignment is closed: `_owner_record`, `_require_owner_revision`, and `_advance_owner` derive and compare
   the complete record for ready, removing, and removed states before accepting or advancing it
   (`provisioner.py:1012-1118`). Foreign service/pair ownership is rejected without durable mutation.
3. Ordered failure-lock reacquisition is closed: the local failure context captures the observed repository's real
   common-Git lock and `_persist_failure` reacquires that exact common-Git/manifest pair on each post-identity failure
   (`provisioner.py:246-259`, `:1183-1198`). No shared mutable context or fabricated lock is present.

Fresh focused result: `3 passed in 14.96s`.

## Additional contract conclusions

- Mutation order is exact: production-vector forced fetch, `worktree add --no-track -b`, and plain
  `worktree remove`. The provisioner contains no reset, rebase, checkout, switch, prune, force remove, branch delete,
  raw filesystem delete, shell, subprocess, synthetic state, or private Git helper.
- Route pair and repository binding are re-attested at every authority boundary. The route loader shares the writer's
  per-card locks and pair-wide reread; the binding observer retains the exact HTTPS/SSH origin digest and fixed
  `origin/aidt-prd` commit contract.
- Ready resume performs no fetch/add/remove and permits descendant commits plus dirty ticket work. Prepared and
  removing recovery preserve every ambiguous shape. Removed re-entry is proof-only and tombstone-idempotent.
- Production cleanup remains deny-all. Physical removal requires an injected verifier plus the exact active owner;
  proof-only post-remove finalization requires the recorded authority digest but performs no Git mutation.
- Canonical manifest/ownership/attempt CAS, revision shapes, failure disposition, partial transitions, lazy imports,
  exact DTO fields, type boundaries, and redacted reprs remain unchanged.

## Fresh executable evidence

| Gate | Result |
| --- | --- |
| Three final verifier-gap cases | `3 passed in 14.96s` |
| Complete provisioner acceptance file | `65 passed in 331.42s (0:05:31)` |
| Contract, manifest, Git-state, persisted recovery, route dispatch, and routing Git-object suites | `460 passed in 276.07s (0:04:36)` |
| Ruff `--no-cache` over product, facade, support, and all provisioner tests | `All checks passed!` |
| Pyright over the same slice | `0 errors, 0 warnings, 0 informations` |
| Provisioner AST | `functions=72 max_lines=45 max_nesting=3` |
| Fresh-process lazy facade | `cold=clean provisioner=lazy`; unknown-name behavior also passed in the 65-case suite |
| Tracked whitespace | `git diff --check` exit `0`, zero diagnostics |
| Product/facade/support/test no-index whitespace | expected content-difference exit `1` for each, zero diagnostics |

The 65-case fixture and the independent 460-case compatibility gate use only disposable local repositories and
local observation/mutation. No network, external service, live repository, AIDT checkout, Jira, branch promotion,
push, deployment, source/test/runtime/schema edit, or commit was performed during this recheck.
