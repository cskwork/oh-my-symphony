# Frontier 003 provisioner product repair map

Date: 2026-07-21

## Decision

Repair only `src/symphony/aidt_worktree/provisioner.py` and the lazy facade
`src/symphony/aidt_worktree/__init__.py`. The landed manifest schemas, manifest public APIs, Git-state public APIs,
and recovery-proof DTOs/functions are sufficient; do not change their wire shape or reach into private Git helpers.

The current executable baseline is exactly `30 failed, 32 passed in 474.72s` across 62 collected cases. The failures
are product/facade failures, not fixture construction failures. The recovery foundation is already independently green:
the recovery recheck reports 153 focused recovery cases and 209 Git-state/manifest cases passing, with zero MUST or
SHOULD findings (`persisted-recovery-proof-recheck-report.md:5-18`, `:100-118`).

This is an implementation map only. No source, test, external service, live repository, network, branch, or commit was
mutated.

## Frozen boundaries

- Keep the seven provisioner exports exactly: `ActiveCompletionLease`, `AidtProvisioningAdmission`, `AidtRunGuard`,
  `AidtWorktreeProvisioner`, `CompletionAuthority`, `DenyAllCompletionAuthority`, and `PreparedAidtWorktree`
  (`tests/test_aidt_worktree_provisioner.py:154-163`).
- Keep DTO field order unchanged (`tests/test_aidt_worktree_provisioner.py:1052-1079`).
- Keep the manifest four-state sequence and revisions: `prepared(1) -> ready(2) -> removing(3) -> removed(4)`
  (`src/symphony/aidt_worktree/manifest.py:908-945`, `:1242-1259`).
- Keep `RepositorySnapshot` at 18 fields and use the public recovery API. The recovery functions return genuinely
  observed `RepositoryState`; the provisioner may carry the state opaquely but must not synthesize or inspect its raw
  collections (`persisted-recovery-proof-api.md:5-21`, `:44-72`).
- Keep manifest/ownership/attempt canonical CAS persistence unchanged
  (`src/symphony/aidt_worktree/manifest.py:458-530`). Partial-write convergence belongs in the provisioner under the
  existing common-Git-then-manifest lock order (`src/symphony/aidt_worktree/manifest.py:636-643`).

## Exact function-to-failure map

| Failing case(s), exact count | Current owner | Smallest repair |
| --- | --- | --- |
| `test_static_recovery_boundary_has_no_synthetic_state_or_path_inference` (1) | `_verify_created`, `_remove_and_finalize`, `_finalize_already_removed`, `_state_from_snapshot` (`provisioner.py:349-372`, `:524-560`, `:899-910`) | Import and call the three public `prove_*_recovery` functions; carry real uninterrupted before-states; delete `_state_from_snapshot` and manual target/path inference. |
| `test_static_boundary_uses_only_public_git_names_and_stable_path_types` (1) | every `paths: object`, `_identity_and_paths`, `_stable_paths` (`provisioner.py:267-405`, `:463-582`, `:609-616`, `:941-947`) | Import `StableWorktreePaths`; type every `paths` parameter/return exactly; remove `_stable_paths`. |
| `test_public_facade_exports_exact_provisioner_surface_lazily_in_all_orders` (1) | facade `TYPE_CHECKING`, export sets, `__all__`, `__getattr__` (`__init__.py:32-114`, `:116-202`, `:204-317`) | Add a closed `_PROVISIONER_EXPORTS`, the seven type-only imports and `__all__` names, and one lazy `provisioner` branch. Cold facade import must still load neither `provisioner`, `git_state`, nor `manifest`. |
| `test_ready_resume_rejects_unrelated_state_drift_before_backend[unrelated_ref|unrelated_registration]` (2) | `_resume_ready`, `_require_ready_root` (`provisioner.py:407-425`, `:884-896`) | Replace the incomplete snapshot subset check with `prove_ready_recovery(..., phase="resume")`. |
| `test_before_run_rechecks_pair_binding_attempt_and_git_identity` (1) | `attest_before_run` (`provisioner.py:216-230`) | Compare every guard field, including exact workspace path, to manifest/attempt/settings; then run the locked ready proof. |
| `test_locked_before_run_barrier_rereads_route_and_binding[route]` (1) | `attest_before_run` pre-lock route (`provisioner.py:220-230`) | Reload route inside both locks, require byte/equality-identical DTO to the pre-lock route, then freshly observe binding. The `[binding]` row already fails closed and must remain green. |
| `test_admission_and_guard_fields_are_exact_sealed_capabilities` (1) | `PreparedAidtWorktree`, `_attempt_for_admission` (`provisioner.py:125-128`, `:662-674`) | Add exact-type `PreparedAidtWorktree.__post_init__`; require attempt identifier plus action-specific disposition/phase/revision. Manual is never provisionable. |
| `test_non_due_backoff_and_swapped_same_revision_attempt_never_reach_git` (1) | `_attempt_for_admission` (`provisioner.py:662-674`) | Require exact identifier and an actually consumed/due active provision shape; do not derive every non-ready disposition as `provision`. |
| `test_authorized_cleanup_writes_removing_then_plain_remove_then_removed` (1) | `_begin_removal`, `_remove_and_finalize` (`provisioner.py:463-493`, `:524-539`) | Use locked `prove_ready_recovery(..., cleanup_pre)`; carry its real state through plain remove; pair with genuine cleanup-post in `validate_remove_delta`. |
| `test_removing_recovery_rejects_every_wrong_authority_or_lease_field[authorization_change1-lease_change1]` (1; `ready_manifest_revision=3`) | `_authorized` (`provisioner.py:584-607`; test input `tests/test_aidt_worktree_provisioner.py:623-668`) | Require `ready_manifest_revision == 2`, never `{2, current removing revision}`. |
| `test_removing_recovery_requires_fresh_authority_only_for_destructive_retry` (1) | `_recover_removing`, `_remove_and_finalize` (`provisioner.py:495-539`) | For exact target, require fresh exact authority/lease and recorded authority digest, obtain a genuine current cleanup-pre proof, compare it to the recorded removal intent, then carry it through one plain remove. |
| `test_branch_retained_proof_only_finalization_needs_no_fresh_authority` (1) | `_recover_removing`, `_finalize_already_removed` (`provisioner.py:495-522`, `:541-560`) | Replace ambiguous/manual reclassification with `prove_removed_recovery`; on success persist its genuine cleanup-post state/delta with no authority and no Git mutation. |
| `test_cleanup_rechecks_route_and_binding_inside_locked_barrier[route|binding]` (2) | `_cleanup` (`provisioner.py:427-461`) | After both locks, re-read manifest/sidecars and route, compare exact route to pre-lock DTO, then freshly attest binding before authorization/proof. Repeat route/binding immediately before destructive remove. |
| `test_all_cleanup_crash_seams_restart_through_public_cleanup[after_removing_fsync_before_remove|after_physical_remove_before_removed_fsync]` (2) | `_recover_removing`, remove proof functions (`provisioner.py:495-582`) | Exact target uses fresh authority + genuine cleanup-pre/remove pair; branch-retained absence uses proof-only `prove_removed_recovery`. No duplicate remove. |
| `test_every_individual_multi_file_partial_write_restart_is_recoverable[prepared-manifest|prepared-ownership]` (2) | `_create_new`, `_recover_prepared` (`provisioner.py:267-316`) | Recognize only the exact prepared sidecar predecessors and finish missing ownership/attempt before Git. |
| `...partial_write...[ready-manifest|ready-ownership]` (2) | `_persist_ready`, `_prepare` (`provisioner.py:248-265`, `:374-405`) | Prove the ready Git state, then finish only the missing owner/ready-attempt CAS suffix before applying action checks. |
| `...partial_write...[removing-manifest|removing-ownership|removing-attempt]` (3) | `_begin_removal`, `_recover_removing` (`provisioner.py:463-522`) | Finish the exact missing owner/attempt suffix first. Metadata repair is non-destructive; a subsequent remove still requires fresh authority. |
| `...partial_write...[removed-manifest|removed-ownership]` (2) | `_finish_removed`, removed early return (`provisioner.py:435-441`, `:562-582`) | Do not return on manifest `removed` before locking. Re-prove recorded removal and finish/validate the tombstoned ownership suffix. |
| `test_fetch_result_and_registration_proof_are_consumed_exactly` (1) | `_fetch`, `_persist_ready` (`provisioner.py:618-626`, `:374-388`) | Return and compare exact `FetchResult(base_sha, repository_binding_digest)`; reject missing S2 registration proof explicitly, removing the `or ""` fallback. |
| `test_durable_dtos_are_keyword_constructed_and_golden_bytes_stay_exact` (1) | `_prepared_manifest`, `_new_ownership`, `PostProof`, `RemovalProof` calls (`provisioner.py:386-388`, `:479-481`, `:676-714`) | Use field keywords for all four durable DTO constructors; do not change canonical bytes/schema. |
| `test_repr_never_exposes_workspace_paths_or_lease_tokens` (1) | provisioner DTO default reprs (`provisioner.py:83-151`) | Use `repr=False` and bounded allowlisted reprs. `AidtRunGuard`/`PreparedAidtWorktree` omit paths; `ActiveCompletionLease` omits run ID. |
| `test_stale_failure_cannot_overwrite_ready_or_open_fabricated_common_lock` (1) | `prepare`, `_persist_failure` (`provisioner.py:207-214`, `:815-832`) | Carry exact failure ownership context, use the real common-Git lock when known, and CAS only the owned lineage. A stale failure preserves newer bytes and re-raises the original `scope_changed`. |

Count: `3 + 4 + 2 + 8 + 9 + 1 + 1 + 1 + 1 = 30`, matching the fresh run and the RED-builder matrix
(`provisioner-red-test-builder-report.md:51-76`).

## Smallest safe vertical repair order

1. **Facade/type/repr static boundary.** Add lazy exports; exact `StableWorktreePaths`; proof imports; exact-type DTO
   validation; redacted reprs; keyword durable constructors. This makes the public and Pyright boundary trustworthy
   before lifecycle edits.
2. **Locked bundle and failure ownership primitives.** Under manifest lock, read manifest + ownership + attempt as one
   bundle, classify it against the durable predecessor table below, and create/update one exact failure context. Do
   not perform Git in this step.
3. **Fresh create vertical.** Keep genuine `S0`; consume exact `FetchResult`; keep genuine `S1`; persist the exact
   prepared bundle; carry `S1` through add; observe genuine `S2`; validate `S1 -> S2`; persist/repair ready bundle.
4. **Prepared recovery vertical.** Use `prove_prepared_recovery`. Absent result carries its genuine `S1` through one
   add; exact result carries its genuine `S2`, ticket, and create digest directly to ready; all failures preserve.
5. **Ready/resume and before-run vertical.** Use one locked route/binding/sidecar/guard barrier plus
   `prove_ready_recovery(..., resume)` for both prepare-resume and every backend attestation.
6. **Initial cleanup vertical.** Exact ready authority + lease, locked `cleanup_pre` proof, durable removing bundle,
   immediate route/binding recheck, one plain remove, genuine cleanup-post, exact delta, removed bundle.
7. **Removing/removed recovery vertical.** Exact target: repair sidecars, require fresh authority, prove and remove.
   Branch retained and path/registration absent: `prove_removed_recovery`, no authority/mutation. Removed manifest:
   lock, re-prove, finish/validate tombstone.
8. **Close negative/static gates.** Exact FetchResult mismatch, every forbidden command/fallback spy, canonical bytes,
   lazy imports, Pyright/Ruff/AST, then the 62-case and foundation regression supersets.

Do not make cleanup green by weakening `validate_remove_delta`; do not make recovery green by reconstructing historical
raw tuples. The landed proof API exists specifically to avoid both shortcuts.

## Durable transition predecessor and repair table

Notation: `M` = manifest, `O` = ownership, `A` = attempt. `O1..O4` are successive ownership revisions aligned to the
manifest revision; attempt record revisions remain monotonic but are not globally fixed. Every row is handled only
after exact route/manifest identity and the listed sidecar fields match. Any skipped, contradictory, foreign-scope,
manual, or newer revision is owned-preserved/error with no Git action.

| Intended transition and write order | Exact durable predecessor after a crash | Allowed repair under the same locks | Git/authority rule |
| --- | --- | --- | --- |
| `prepared`: `M1 -> O1 -> A(prepared,m1)` | `M1, O absent, A(active none,null)` | create exact O1; advance A to prepared/m1 | no Git until repaired |
| same | `M1, O1, A(active none,null)` | advance A to prepared/m1 | no Git until repaired |
| same | `M1, O1, A(prepared,m1)` | already complete | call prepared recovery proof |
| `ready`: prerequisite `M1,O1,A(added,m1)`; writes `M2 -> O2 -> A(ready,added,m2)` | `M2, O1, A(added,m1)` | after ready proof, advance O2 then A ready/m2 | no add; proof only |
| same | `M2, O2, A(added,m1)` | after ready proof, advance A ready/m2 | no add; proof only |
| same | `M2, O2, A(ready,added,m2)` | already complete | resume proof before dispatch |
| `removing`: prerequisite `M2,O2,A(ready,added,m2)`; writes `M3 -> O3 -> A(ready,removing,m3)` | `M3, O2, A(ready,added,m2)` | advance O3 then A removing/m3 | metadata only; preserve unless fresh authority later permits remove |
| same | `M3, O3, A(ready,added,m2)` | advance A removing/m3 | same |
| same | `M3, O3, A(ready,removing,m3)` | already complete | exact target needs fresh authority; absent target uses proof-only recovery |
| `removed`: prerequisite `M3,O3,A(ready,removing,m3)`; writes `M4 -> O4(tombstone)` | `M4, O3, A(ready,removing,m3)` | verify persisted complete removal proof against fresh proof, then advance O4 tombstone | no remove, no fresh authority |
| same | `M4, O4(tombstone), A(ready,removing,m3)` | validate exact alignment; handled idempotently | no Git |

The existing manifest validator fixes state/revision and transition order (`manifest.py:908-945`, `:1242-1259`),
while public CAS writers enforce record revision increments (`manifest.py:501-530`, `:1262-1265`). The attempt helpers
already permit `none -> prepared -> added`, ready closure, and ready-added -> removing
(`manifest.py:760-811`, `:1482-1508`). No manifest helper or schema addition is required.

## Failure ownership context

Introduce one private, exact context carried and `replace`d after each successful durable write:

| Field | Why it is required |
| --- | --- |
| `identifier` | prevents cross-child failure writes |
| `workflow_generation` | prevents an old runtime generation poisoning a reload |
| `route_pair_digest` | prevents old card/catalog scope poisoning current scope |
| `admission_attempt_revision` | binds the call to the consumed admission capability |
| `owned_attempt_revision` | exact CAS revision currently owned after phase writes |
| `owned_disposition` | prevents manual/ready/newer disposition downgrade |
| `owned_mutation_phase` | selects pre-intent retry versus post-intent manual failure semantics |
| `owned_manifest_revision` | binds `none/prepared/added` failure to the exact manifest lineage |
| `manifest_lock` | always available from stable paths |
| `common_git_lock: Path | None` | exact real lock after repository identity attestation; never fabricate all-zero identity |

`_persist_failure` re-reads the bundle under the real common-Git + manifest locks when `common_git_lock` is known, or
the manifest lock alone before identity exists. It writes only if every context field still equals durable state. If a
newer owner advanced/reset/readied the bundle, it performs no write and preserves the original failure. A real storage
or CAS I/O failure remains `persistence_failed` for the future runtime fatal circuit. Current code instead reads the
latest attempt under an unrelated zero lock and can downgrade it (`provisioner.py:815-832`).

## Route, binding, and authorization checkpoints

| Path | Required checkpoint sequence while common-Git then manifest locks are held |
| --- | --- |
| fresh create | compare pre-lock route with locked reload -> S0 -> exact fetch and exact `FetchResult` -> S1/fetch delta -> locked route reload/equality -> persist prepared -> fresh binding immediately before add -> add |
| prepared absent | locked route equality -> fresh binding digest -> `prove_prepared_recovery` absent -> fresh binding immediately before add -> add |
| prepared exact | locked route equality -> fresh binding digest -> `prove_prepared_recovery` exact -> no mutation |
| ready prepare/resume | locked route equality -> exact manifest/ownership/attempt/admission -> fresh binding digest -> ready resume proof |
| before-run | pre-lock route identifies lock -> locked route equality -> exact guard/manifest/ownership/attempt -> fresh binding digest -> ready resume proof -> backend may start |
| initial cleanup | pre-lock manifest/path/route identifies owner/lock -> locked bundle + route equality -> fresh binding -> exact rev-2 authority/active lease -> cleanup-pre proof -> persist removing bundle -> second route/binding check -> plain remove |
| removing exact retry | locked bundle + route/binding -> exact recorded authority digest + fresh exact rev-2 authority/lease -> current cleanup-pre proof equal to recorded intent -> second route/binding -> plain remove |
| removing branch-retained absence | locked bundle + route/binding -> `prove_removed_recovery` against recorded cleanup-pre/retained SHA -> persist removed/tombstone; no fresh authority |
| removed re-entry | lock first -> bundle/route/binding -> repeat removed proof and compare persisted post proof/delta -> finish/validate tombstone; no mutation |

The current stale barriers are at `provisioner.py:216-230` and `:427-461`; the wrong authorization revision set is at
`:593-601`.

## Genuine RepositoryState/recovery proof carried by each path

| Path | Proof carried to the transition |
| --- | --- |
| fresh create | genuine `S0 RepositoryState`; exact fetch result; genuine `S1 RepositoryState`; after add genuine `S2 RepositoryState`; `validate_fetch_delta(S0,S1)` and `validate_create_delta(S1,S2)` |
| restart before prepared after fetch | new genuine S0; exact second fetch; new genuine S1. A no-op fixed-ref fetch is valid because `validate_fetch_delta` permits only that ref to move, but does not require movement (`git_state.py:1051-1063`). |
| prepared absent | `PreparedRecoveryProof(state=S1,ticket=None,digest=None)`; carry returned genuine S1 through add; genuine S2 after add; validate create delta |
| prepared exact | `PreparedRecoveryProof(state=S2,ticket=exact clean base,create_delta_digest=...)`; persist its S2/digest; no add |
| ready prepare/resume/before-run | `ReadyRecoveryProof(state=resume,ticket=exact current target)`; dirty ticket and descendant commits allowed, unrelated/root/protected/binding drift rejected |
| initial cleanup | `ReadyRecoveryProof(state=cleanup_pre,ticket=clean current target)`; persist its state snapshot and ticket HEAD; carry the genuine cleanup-pre state through remove; pair with genuine observed cleanup-post in `validate_remove_delta` |
| removing exact destructive retry | new `ReadyRecoveryProof(..., cleanup_pre)` against manifest S2, additionally equal to the recorded partial RemovalProof invariants; carry this genuine state through one remove |
| removing absent/proof-only | `RemovedRecoveryProof(state=cleanup_post,remove_delta_digest=...)` against recorded cleanup-pre and retained SHA; no mutation |
| removed sidecar repair | repeat the removed proof and require its snapshot/delta equal persisted completed RemovalProof except the allowed observed-time reobservation; then repair/validate tombstone |

The public operations and DTO invariants are at `git_state.py:182-246`, `:464-630`; uninterrupted delta validators are
at `:1051-1096`. The recovery API recheck confirms all proof phases close their identity/ref/registration/path bracket
and preserve unrelated collections (`persisted-recovery-proof-recheck-report.md:19-66`).

## Facade, type, repr, and keyword edits

- `__init__.py`: add seven `TYPE_CHECKING` imports, `_PROVISIONER_EXPORTS`, seven `__all__` entries, and a lazy
  `elif name in _PROVISIONER_EXPORTS: from . import provisioner as module`. Do not alter manifest/Git export sets.
- `provisioner.py`: import `StableWorktreePaths`, the three proof DTOs/functions, and use only public Git names.
  Remove `_state_from_snapshot`, `_target_ref_sha`, `_require_snapshot_equal`, `_require_ready_root`, and
  `_stable_paths` once all callers use proofs/typed paths.
- Mark `AidtRunGuard`, `PreparedAidtWorktree`, and `ActiveCompletionLease` `repr=False`; provide bounded reprs with
  identifier/action/revision/active booleans only. Avoid route/workflow digests as well as path/token values.
- Add `PreparedAidtWorktree.__post_init__` requiring exact `AidtWorktreeResult` and exact `AidtRunGuard`.
- Construct `AidtWorktreeManifest`, `OwnershipRecord`, `PostProof`, and `RemovalProof` with keywords. Preserve field
  order and canonical bytes (`tests/test_aidt_worktree_provisioner.py:964-990`).
- Make `_fetch` return the exact `FetchResult`; compare both fields before prepared persistence. Require non-null S2
  `target_registration_digest` before constructing `PostProof`.

## Contradictory expectations and corrections

### 1. No fetch-restart contradiction

The crash case expects another exact fetch only for `after_forced_fetch_before_prepared`
(`tests/test_aidt_worktree_provisioner.py:774-792`). This is compatible with the Git contract: the new process starts
from a new genuine S0, and `validate_fetch_delta` permits the fixed production ref to remain at the same SHA while
forbidding every other change (`git_state.py:1051-1063`). Do not weaken or special-case this test.

### 2. Correct PLAN token width to 32 hex; do not change product/tests

One PLAN scalar-summary row says every run/lease token is 64 hex, but the executable frozen authorization validator
requires `run_id` to be 32 lowercase hex and `owning_lease_token == run_id`
(`src/symphony/aidt_worktree/contract.py:380-394`). The fixtures and wrong-lease test also use 32 hex
(`tests/aidt_provisioner_support.py:504-519`; `tests/test_aidt_worktree_provisioner.py:623-635`). Treat the PLAN row as
stale prose; 32 hex is authoritative.

### 3. Crash-sentinel requirement conflicts with the executable tests

Draft-attack MUST-10 asks the product to define a bounded test-only crash sentinel, but the executable suite defines
its own `CrashAtSeam(BaseException)`/`PartialWriteCrash(BaseException)` and requires those objects to propagate through
the public methods (`tests/test_aidt_worktree_provisioner.py:54-70`, `:774-827`, `:846-904`). The fresh run shows all
four create seam cases already pass; the two cleanup seam failures occur later in synthetic removal proof, not because
the hook was swallowed. The frozen public surface has no crash-sentinel type.

Resolution: do not add a new public/product exception solely for tests. Keep ordinary failures in the existing
`except Exception` path and let process-death `BaseException` escape without failure persistence. If a bounded sentinel
is still required, the test contract and frozen facade must first be explicitly amended together.

## Regression gates

Run in this order with no network and disposable repositories only:

1. Collection: exactly 62 provisioner cases from 31 functions.
2. Focused product: `pytest -p no:cacheprovider -q tests/test_aidt_worktree_provisioner.py` -> `62 passed`.
3. Recovery API: `tests/test_aidt_worktree_recovery_proofs.py` -> preserve the independently reported 153-case green
   matrix.
4. Foundation: `tests/test_aidt_worktree_git_state.py tests/test_aidt_worktree_manifest.py` -> preserve the reported
   209-case Git-state/manifest gate; also run contract and route-dispatch suites.
5. Ruff `--no-cache` over `provisioner.py`, facade, support fixture, and provisioner tests.
6. Pyright with the repository venv over the same product/test slice -> zero errors/warnings/information.
7. Fresh-process facade permutations: cold import loads none of provisioner/Git-state/manifest; accessing a frozen
   provisioner export loads lazily; unknown names remain `AttributeError`.
8. AST: every product function <=50 physical lines and control nesting <=4.
9. Command/fallback spy: only exact fetch/add/plain-remove mutation commands; zero reset/rebase/checkout/switch/prune,
   force, branch deletion, recursive deletion, network fallback, or generic workspace fallback.
10. Canonical durable bytes and no-index/tracked whitespace checks unchanged; inspect `git diff --check`.

Acceptance is all gates green together. A 62-case green obtained by weakening a recovery proof, skipping sidecar
alignment, accepting stale route/binding state, or catching arbitrary `BaseException` is not acceptable.
