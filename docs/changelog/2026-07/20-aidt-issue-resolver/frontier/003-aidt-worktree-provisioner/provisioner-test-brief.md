# Frontier 003 provisioner/runtime test brief

## Decision

Implement one synchronous, fail-closed state machine in `aidt_worktree/provisioner.py` and one process-lifetime
ownership/admission facade in `aidt_worktree/runtime.py`. They compose the landed route DTO/observer, manifest
foundation, and pending `git_state.py`; they do not yet alter `WorkspaceManager`, Core, `RunningEntry`, or health
serialization. No live repository, network, Jira, or AIDT checkout is a fixture.

## Existing contracts that are inputs, not redesign targets

- Route authority is a fresh `load_route_dispatch_contract(config, identifier)` result. Managed malformed/stale/
  retained/coordinator cards raise; only a truly unmanaged identifier returns `None`.
- `AidtRouteDispatchContract` supplies the exact service/kind/catalog/ref/SHA, route and coordinator fingerprints,
  source/catalog revisions, repository-binding digest, route-pair digest, issue/change kind, and derived branch.
- Repository equality is recomputed through `observe_service_binding(settings, service_id)`; copied card digests are
  never evidence.
- Durable ownership uses `AidtWorktreeSettings`, `StableWorktreePaths`, `AidtWorktreeManifest`, `OwnershipRecord`,
  `AttemptRecord`, strict reads/CAS writes, `ordered_worktree_locks`, and the four-way `DelegateResult`.
- Git work uses only the public `git_state.py` API: `observe_repository_identity`, `observe_repository_state`,
  `fetch_production_base`, `verify_service_binding`, `add_worktree`, `remove_worktree`,
  `classify_target_artifacts`, `observe_ticket_worktree`, `base_is_ancestor`, and the three delta validators.
  `FetchResult` binds `base_sha` and `repository_binding_digest`; `RepositoryState.snapshot` is the persisted proof.

## Frozen `provisioner.py` surface

```python
@dataclass(frozen=True)
class AidtProvisioningAdmission:
    identifier: str
    workflow_generation: str
    route_pair_digest: str
    attempt_record_revision: int
    action: Literal["provision", "resume"]

@dataclass(frozen=True)
class AidtRunGuard:
    identifier: str
    workflow_generation: str
    route_pair_digest: str
    attempt_record_revision: int
    manifest_revision: int
    workspace_path: Path

@dataclass(frozen=True)
class PreparedAidtWorktree:
    result: AidtWorktreeResult
    guard: AidtRunGuard

@dataclass(frozen=True)
class ActiveCompletionLease:
    identifier: str
    issue_id: str
    run_id: str
    attempt_kind: str
    active: bool
    competing_owner: bool

class CompletionAuthority(Protocol):
    def verify(
        self,
        authorization: CompletionAuthorization,
        lease: ActiveCompletionLease,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
    ) -> bool: ...

class DenyAllCompletionAuthority:
    def verify(...) -> bool: return False

class AidtWorktreeProvisioner:
    def __init__(
        self,
        config: ServiceConfig,
        settings: AidtWorktreeSettings,
        *,
        runner: BinaryRunner = default_binary_runner,
        clock: Callable[[], datetime],
        route_loader: RouteLoader = load_route_dispatch_contract,
        binding_observer: BindingObserver = observe_service_binding,
        completion_authority: CompletionAuthority = DenyAllCompletionAuthority(),
        fault_hook: Callable[[str], None] = noop_fault_hook,
    ) -> None: ...

    def prepare(self, admission: AidtProvisioningAdmission) -> PreparedAidtWorktree: ...
    def attest_before_run(self, guard: AidtRunGuard) -> None: ...
    def cleanup(
        self,
        identifier: str,
        workspace_path: Path,
        *,
        authorization: CompletionAuthorization | None = None,
        lease: ActiveCompletionLease | None = None,
    ) -> DelegateResult[None]: ...
```

`config` and `settings` are one immutable runtime generation. The runner is the sole Git process seam. Route and
binding callables are injected only for deterministic drift/fetch fixtures. The clock returns whole-second UTC.
`fault_hook` accepts only the six names below and defaults to a no-op. `cleanup` never returns `UNMANAGED`; denied
authority is `OWNED_PRESERVED("authorization_invalid")`, a completed removal is `HANDLED(None)`, and a recognized
invalid shape raises `AidtWorktreeFailure` for runtime conversion to `OWNED_ERROR`.

## Provisioner transitions

| Entry | Required sequence | Result |
|---|---|---|
| no manifest | re-attest pair; observe identity; lock common Git then manifest; re-read admission and absence; S0; exact forced fetch; S1 and fetch delta; re-attest pair; require fetched SHA and first recomputed binding; persist `prepared` plus ownership/attempt; recompute binding again immediately before add; exact add; S2/create delta/ticket proof; persist `ready`, ownership, and ready attempt | `created_now=True` |
| `prepared`, target absent | re-attest pair/binding and stored S1 invariants; no fetch; exact add; verify S2; persist `ready` and ready attempt | `created_now=True` |
| `prepared`, target exact | re-attest pair/binding; no fetch/add; verify exact clean base worktree, no upstream, S2 allowed delta; persist `ready` and ready attempt | `created_now=False` |
| `prepared`, ambiguous | preserve manifest/path/ref/registration and persist manual post-intent attempt | failure |
| `ready` | re-attest pair and binding under both locks; require exact manifest/scope/path/branch/registration, no upstream, base ancestor, unchanged root/protected/unrelated state; run no fetch/add/remove | `created_now=False` |
| `removing`, target exact | require a newly verified authorization and the same active, non-competing lease; retry one plain remove, prove removal delta, write `removed` and tombstone | handled |
| `removing`, target absent | require recorded authority digest and unchanged branch/fixed ref/root/protected/unrelated state; perform no Git mutation; finalize `removed` and tombstone | handled |
| `removing`, ambiguous; `removed` | preserve; removed is idempotently owned and never recreates | preserved/error |

`prepare` dispatches only absent/`prepared`/`ready`. Every path re-reads the attempt record revision carried by the
admission. `attest_before_run` accepts only the returned guard, repeats the ready-resume route/binding/Git proof, and
performs no durable or Git mutation. Non-route card notes/state may change; route-owned projections may not.

The attempt record transitions are exact:

1. First attested scope: persist revision 1 as due `backoff`, `attempt=0`, phase `none`, then durable admission consumes
   revision 2 with `attempt=1` before Git.
2. Manual and non-due backoff deny. Due backoff increments before Git. Ready admits `resume` without increment.
3. Pair/generation change persists one `scope_reset`, `attempt=0` record and denies this tick; the next due tick may
   consume it only after another pair attestation.
4. Durable phase changes are `none -> prepared -> added`; each has the matching manifest revision. Success writes
   disposition/category `ready`, null retry time, and ready manifest revision.
5. `next_failure_record` handles failures: only pre-intent lock/fetch timeout or fetch command failure backs off;
   every other or post-intent failure is manual. Failure persistence failure trips the runtime fatal circuit.

## Frozen `runtime.py` surface

```python
@dataclass(frozen=True)
class AidtWorktreeGeneration:
    revision: int
    config: ServiceConfig
    settings: AidtWorktreeSettings | None
    workflow_generation: str | None

@dataclass(frozen=True)
class AidtWorktreeHealth:
    enabled: bool
    status: Literal["disabled", "ready", "degraded", "fatal"]
    workflow_generation: str | None
    create_count: int
    resume_count: int
    failure_count: int
    consecutive_failures: int
    last_category: str | None
    last_ref: str | None
    last_success_at: str | None

class AidtWorktreeRuntime:
    def __init__(self, workflow_path: Path, *, clock: Callable[[], datetime],
                 provisioner_factory: ProvisionerFactory | None = None) -> None: ...
    def publish(self, config: ServiceConfig) -> AidtWorktreeGeneration: ...
    def reject_reload(self, category: str = "profile_invalid") -> None: ...
    def path_for(self, generation: AidtWorktreeGeneration, identifier: str) -> DelegateResult[Path]: ...
    def admit_candidate(self, generation: AidtWorktreeGeneration,
                        identifier: str) -> DelegateResult[AidtProvisioningAdmission]: ...
    def create_or_reuse(self, generation: AidtWorktreeGeneration,
                        admission: AidtProvisioningAdmission) -> DelegateResult[PreparedAidtWorktree]: ...
    def before_run(self, generation: AidtWorktreeGeneration,
                   guard: AidtRunGuard) -> DelegateResult[None]: ...
    def remove(self, generation: AidtWorktreeGeneration, path: Path, *, identifier: str | None = None,
               authorization: CompletionAuthorization | None = None,
               lease: ActiveCompletionLease | None = None) -> DelegateResult[None]: ...
    def health_snapshot(self) -> AidtWorktreeHealth: ...
```

`publish` validates completely before atomically publishing; enabled first publication activates the stable registry.
Disabled publication performs no activation but retains discovery of any prior registry. Failed reload publishes no
generation, closes new admission, and keeps all old guards owned. Any later publication revision makes an earlier
admission stale; ownership remains final but `create_or_reuse`/`before_run` deny it. A persistence failure is fatal
for the process. Counters are monotonic; absent or prepared entry increments create once on ready, ready entry
increments resume once, handled authorization preservation does not count as failure, and successful prepare/resume
resets consecutive failures. Category/ref are existing bounded values only.

Only `UNMANAGED` permits generic fallback. Current managed cards, registry/manifest/attempt/ownership names,
tombstones, deterministic recorded paths, and known registrations are owned even when disabled or corrupt. Once
recognition begins, all exceptions become `OWNED_ERROR`; missing authority and non-destructive removing preservation
become `OWNED_PRESERVED`.

Binding runtime clarification: resolve the default provisioner lazily only on first valid enable. `path_for` may
return an exact validated durable recorded path while disabled/rejected because it is non-mutating; every other
generation gate stays closed. Ready admission requires aligned manifest, ownership, and attempt evidence under the
manifest lock. Publication is all-or-nothing; actual successful prepare increments the action-based counter before a
stale-publication postcheck. The fatal/no-I/O/exception/immutable-key/concurrent-initializer rules are those in PLAN
Binding Amendment 4. Standalone registration-only recognition without route or durable record is explicitly deferred;
private Git-state parsing is forbidden.

## Exact red tests and seams

`tests/test_aidt_worktree_provisioner.py` starts with these red tests:

- `test_new_create_orders_pair_binding_prepared_add_verify_ready`
- `test_post_fetch_pair_or_binding_drift_blocks_before_prepared`
- `test_second_binding_recheck_drift_blocks_before_add`
- `test_prepared_absent_recovery_adds_once_without_fetch`
- `test_prepared_exact_recovery_finalizes_without_fetch_or_add`
- `test_prepared_mixed_artifacts_are_manual_and_preserved`
- `test_ready_resume_allows_ticket_commits_and_dirty_work_without_mutation`
- `test_before_run_rechecks_pair_binding_attempt_and_git_identity`
- `test_attempt_phase_ready_and_failure_records_follow_durable_order`
- `test_cleanup_is_deny_all_without_verified_authority_and_active_lease`
- `test_authorized_cleanup_writes_removing_then_plain_remove_then_removed`
- `test_removing_recovery_requires_fresh_authority_only_for_destructive_retry`
- `test_every_failure_uses_exact_command_and_no_generic_fallback_spy`

The fault hook names and restart assertions are exact:

- `after_forced_fetch_before_prepared`: absent manifest; restart begins new S0.
- `after_prepared_fsync_before_add`: prepared/absent; restart adds once.
- `after_add_before_verification`: prepared/exact; restart verifies then readies.
- `after_verification_before_ready_fsync`: prepared/exact; restart re-verifies then readies.
- `after_removing_fsync_before_remove`: removing/exact; fresh authority permits one retry.
- `after_physical_remove_before_removed_fsync`: removing/absent; restart finalizes without Git mutation.

`tests/test_aidt_worktree_runtime.py` starts with:

- `test_never_enabled_unmanaged_runtime_is_inert`
- `test_admission_handles_initial_manual_backoff_due_scope_reset_and_ready`
- `test_ready_restart_admits_resume_once_without_fetch_or_add`
- `test_stale_or_failed_reload_generation_cannot_reach_backend_barrier`
- `test_disabled_corrupt_missing_and_removed_ownership_never_falls_back`
- `test_delegate_converts_post_recognition_exceptions_to_owned_error`
- `test_persistence_failure_opens_fatal_circuit_for_process_lifetime`
- `test_health_counts_create_resume_failure_and_sanitizes_last_detail`

## Explicit bounded foundation additions

Do not bypass private helpers. Add only these public manifest helpers if the builder needs them:

- `read_optional_manifest`, `read_optional_ownership`, and `read_optional_attempt`: return `None` only for `ENOENT`;
  symlink, wrong mode/type, collision, and malformed bytes remain failures.
- `initial_attempt_record(...)`, `advance_attempt_phase(...)`, and `ready_attempt_record(...)`: construct the exact
  canonical revision/timestamp/disposition transitions above; persistence remains through `persist_attempt` CAS.
- Binding Amendment 5 permits `next_failure_record` to transform an exact ready `added`/2 attempt into a manual
  failure while preserving that truthful phase/revision; its manifest regression must reject every broader shape.

No broader contract/manifest redesign is authorized. `classify_target_artifacts` is the bounded Git-state recovery
classifier: only `ABSENT`, `EXACT`, or `AMBIGUOUS`; provisioner never infers recovery shape from filesystem probes.

## Deferred to the later core/workspace integration slice

- Add the delegate-provider hook and optional admission/guard keywords to `WorkspaceManager`; preserve every existing
  positional signature and unmanaged return/hook/marker/remove behavior.
- Capture generation, manager, admission, and returned guard in `RunningEntry`; use that manager for the whole attempt.
- Insert admission after routing filtering for initial and timer retry, suppress generic retry for specialized durable
  outcomes, and route both attempts through the same create/final barrier.
- Wire all startup/reconcile/Done/remove/hook/commit/merge seams to ownership results, expose health serialization, and
  keep production completion issuance absent until the later delivery-stage controller owns merge/deploy/dev-E2E.
