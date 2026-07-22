# Frontier 003 AIDT Worktree Runtime Implementation Map

Date: 2026-07-21
Scope: `src/symphony/aidt_worktree/runtime.py` and its lazy facade/tests only
Classification: Feature, read-only implementation map

## Decision

Implement one process-lifetime, synchronous `AidtWorktreeRuntime` that owns generation publication, durable attempt
admission, final delegate disposition, the fatal circuit, and bounded in-memory health. It composes the accepted
provisioner and manifest public APIs; it does not absorb their Git, route-proof, manifest-transition, or cleanup
algorithms. The later integration slice alone changes `WorkspaceManager`, `RunningEntry`, Core dispatch/retry/terminal
paths, and health serialization.

The smallest safe runtime has two distinct kinds of state:

- published-generation state: replaceable only after complete validation and activation;
- process state: stable workflow-relative ownership paths, fatal latch, reload gate, counters, and last bounded health
  detail, never replaced by reload.

This split is required because the plan keeps prior AIDT paths owned after disable/error while making every old
admission and guard stale before backend construction
(`docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/PLAN.md:333-353`).

## Evidence ledger

| Evidence | Consequence for this slice |
|---|---|
| `provisioner-test-brief.md:5-8` | Runtime is the process-lifetime ownership/admission facade; workspace/core integration is deferred. |
| `provisioner-test-brief.md:132-184` | DTOs, public method surface, stale-generation behavior, ownership finality, counters, and health are frozen. |
| `src/symphony/aidt_worktree/provisioner.py:211-237` | Provisioner construction requires exact `config`, `settings`, and keyword-only `clock`; runtime must not reconstruct Git seams. |
| `src/symphony/aidt_worktree/provisioner.py:239-296` | Runtime delegates only to `prepare`, `attest_before_run`, and `cleanup`; provisioner already bounds ordinary exceptions. |
| `src/symphony/aidt_worktree/provisioner.py:931-944` | `prepare` accepts only an already consumed, exact attempt revision/action/scope. Runtime therefore owns admission CAS. |
| `src/symphony/aidt_worktree/provisioner.py:1100-1114` | Returned guard is the only accepted pre-backend capability and includes the ready attempt/manifest revisions/path. |
| `src/symphony/aidt_worktree/provisioner.py:1116-1144` | Provisioner owns failure-record persistence; failure-persistence loss surfaces as `persistence_failed`. |
| `src/symphony/aidt_worktree/contract.py:81-88` | `AidtWorktreeFailure` already sanitizes category and ref. Runtime must reuse it rather than expose nested text. |
| `src/symphony/aidt_worktree/contract.py:142-176` | The four `DelegateResult` states are sealed; only exact `UNMANAGED` can permit generic fallback. |
| `src/symphony/aidt_worktree/contract.py:200-214` | `load_aidt_worktree_settings` is side-effect-free and returns `None` for missing/false; enabled profile validation is closed. |
| `src/symphony/aidt_worktree/contract.py:255-308` | Stable metadata and deterministic workspace paths come only from workflow/config plus canonical child ID. |
| `src/symphony/aidt_worktree/manifest.py:462-498` | Optional readers return `None` only for absence; malformed/symlink/wrong-type data remains failure. |
| `src/symphony/aidt_worktree/manifest.py:523-556` | Attempt writes and first activation are existing public durable primitives. |
| `src/symphony/aidt_worktree/manifest.py:559-610` | Registry discovery/identifier/path recognition preserves manifests, attempts, ownership records, and tombstones. |
| `src/symphony/aidt_worktree/manifest.py:646-695` | Existing admission helper performs exact revision/scope/clock evaluation and persists a consumed/reset revision under lock. |
| `src/symphony/aidt_worktree/manifest.py:734-757` | A new attested scope starts as due revision 1, attempt 0, phase `none`. |
| `src/symphony/aidt_worktree/manifest.py:1463-1479` | Manual/non-due deny, ready admits resume without increment, and due backoff consumes the next attempt. |
| `src/symphony/aidt_worktree/manifest.py:1520-1545` | Pair/generation drift writes one `scope_reset` record and denies that tick. |
| `tests/test_aidt_worktree_runtime.py:1-41` | The present runtime file is only an eight-name smoke skeleton, not behavioral RED evidence. |
| `src/symphony/aidt_worktree/__init__.py:213-223` | The accepted lazy facade currently exports provisioner names only; runtime exports are still absent. |
| `src/symphony/workspace.py:150-188,236-252` | Generic path/mkdir/owner/hook/rmtree behavior exists today and must remain outside this slice. |
| `src/symphony/orchestrator/core.py:3592-3643` | Current dispatch resolves path/lease/entry/task in that order; runtime integration must not be smuggled into this slice. |
| `src/symphony/orchestrator/core.py:3760-3809` | Current worker creates and runs hooks through the mutable manager before backend construction; later integration will capture a manager/guard. |
| `src/symphony/orchestrator/core.py:5835-5857` | Timer retry currently bypasses routing/admission; later integration owns the repair. |
| `core-integration-test-brief.md:28-38` | Integration touch points explicitly exclude runtime algorithm redesign. |

## Frozen public contracts

The public field order and method signatures must be exact so later `WorkspaceManager` and Core changes can be purely
adapters (`provisioner-test-brief.md:132-171`).

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
    def __init__(
        self,
        workflow_path: Path,
        *,
        clock: Callable[[], datetime],
        provisioner_factory: ProvisionerFactory | None = None,
    ) -> None: ...

    def publish(self, config: ServiceConfig) -> AidtWorktreeGeneration: ...
    def reject_reload(self, category: str = "profile_invalid") -> None: ...
    def path_for(
        self, generation: AidtWorktreeGeneration, identifier: str
    ) -> DelegateResult[Path]: ...
    def admit_candidate(
        self, generation: AidtWorktreeGeneration, identifier: str
    ) -> DelegateResult[AidtProvisioningAdmission]: ...
    def create_or_reuse(
        self, generation: AidtWorktreeGeneration, admission: AidtProvisioningAdmission
    ) -> DelegateResult[PreparedAidtWorktree]: ...
    def before_run(
        self, generation: AidtWorktreeGeneration, guard: AidtRunGuard
    ) -> DelegateResult[None]: ...
    def remove(
        self,
        generation: AidtWorktreeGeneration,
        path: Path,
        *,
        identifier: str | None = None,
        authorization: CompletionAuthorization | None = None,
        lease: ActiveCompletionLease | None = None,
    ) -> DelegateResult[None]: ...
    def health_snapshot(self) -> AidtWorktreeHealth: ...
```

Exact DTO invariants:

1. Generation revision is exact non-boolean integer `1..2_147_483_647`; `config` is exact `ServiceConfig`;
   `settings is None` iff `workflow_generation is None`; enabled values must be exact and equal
   `settings.workflow_generation`.
2. Constructor `workflow_path` is canonicalized once without I/O; every publication requires
   `config.workflow_path == workflow_path` and, when enabled, `settings.workflow_path == workflow_path`.
3. Generation representation must not include `config`, raw workflow values, paths, credentials, or environment.
4. Health is an immutable in-memory projection only. It performs no filesystem, route, Git, registry, tracker, or clock
   read (`core-integration-test-brief.md:217-239`).
5. `last_success_at`, when present, is a whole-second UTC `YYYY-MM-DDTHH:MM:SSZ`; category/ref have already passed
   through `AidtWorktreeFailure` sanitization (`contract.py:81-88,475-499`).
6. Add exactly `AidtWorktreeGeneration`, `AidtWorktreeHealth`, and `AidtWorktreeRuntime` to the facade's
   `TYPE_CHECKING` imports, closed lazy export set, `__all__`, and `__getattr__`; cold facade import still must not load
   runtime, provisioner, manifest, or Git-state.

`ProvisionerFactory` should remain an internal runtime protocol/type alias. Resolve `None` by importing the default
provisioner only inside the first valid enabled publication; disabled runtime import/construction/publication must
not load provisioner, manifest, or Git-state. Its invocation is
`factory(config, settings, clock=clock)`, matching the accepted constructor at `provisioner.py:214-225`. Do not add a
reverse import from provisioner to runtime.

## Process-lifetime state machine

Recommended internal state under one small `threading.RLock`:

```text
workflow_path + stable_metadata_paths   immutable for process
current_generation                     None | last successfully published DTO
current_provisioner                    None | provisioner for current enabled generation
admission_open                         false after failed reload/disabled/fatal
fatal                                  one-way false -> true
revision                               last successful material publication revision
health counters/details                process-lifetime, monotonic except consecutive/detail reset
```

Do not hold the runtime lock across board, filesystem, provisioner, or Git work. Snapshot the publication token under
lock, perform the operation, then recheck the same token before returning a handled guard. This preserves atomic
publication and health snapshots without serializing independent service Git operations. A generation change during
prepare may leave a safely durable ready worktree, but the postcheck and later `before_run` barrier must return owned
failure and prevent backend construction.

### Publication transitions

| Entry/event | Required transition | Admission/health |
|---|---|---|
| no current + disabled config | validate `load_aidt_worktree_settings -> None`; publish revision 1, no activation/provisioner | closed; `disabled` |
| no current + enabled config | validate completely; validate clock; `activate_registry`; construct provisioner; only then publish revision 1 | open; `ready` |
| current + byte/equality-equivalent validated generation | return the current DTO, do not increment revision | preserve in-flight guards |
| current + materially changed valid enabled config | activate/validate and construct off to the side; atomically publish revision + 1 | open; old admission/guards stale |
| current + valid disabled config | atomically publish revision + 1 with settings/generation `None`; retain stable recognition state | closed; `disabled`; old guards stale |
| validation/activation/factory failure | publish nothing; caller invokes/receives bounded reload rejection | closed; `degraded`, or `fatal` for permanent categories |
| `reject_reload(category)` | keep current generation/provisioner only for ownership recognition; close gate | increment failure/consecutive; `degraded` |
| durability/persistence/invalid-clock failure | latch fatal for process lifetime; never reopen on later publish | closed; `fatal` |

Idempotent equivalent publication is mandatory even though the brief does not spell it out: Core is directed to call
`publish(cfg)` on every tick (`core-integration-test-brief.md:107-126`). Incrementing on every unchanged reload would
make every running admission/guard stale before a later turn. Equality should use the validated generation inputs,
freezing exact `ServiceConfig` equality together with exact settings/workflow generation; do not compare only workspace
root or only the worktree safety digest.

`publish` must validate before mutation. If activation or provisioner construction fails, no half-published DTO or
manager-facing state is visible. Fatal is one-way. A later call may validate for bounded diagnostics but cannot publish
an admission-capable generation.

### Current/stale checks

Every delegate method first establishes ownership, then applies the generation gate:

```text
unrecognized identifier/path -> UNMANAGED
recognized + fatal -> OWNED_ERROR(fatal bounded category)
recognized + rejected reload/disabled -> OWNED_PRESERVED(profile/category)
recognized + generation is not current material revision -> OWNED_ERROR(scope_changed)
recognized + exact current enabled generation -> continue
```

`path_for` is the sole non-mutating exception: after exact durable manifest and ownership/tombstone validation it may
return `HANDLED(recorded_path)` under a disabled or rejected generation. Every admission, create/reuse, before-run,
and removal gate retains the table above. A canonical child may be unmanaged only after the bounded route loader
returns `None`; zero-loader unmanaged controls use a non-child identifier.

Recognition must precede stale/disabled handling so old/corrupt/removed AIDT paths never regain generic behavior.
`create_or_reuse` additionally requires every admission field to match the current generation and the fresh attempt
scope. `before_run` additionally requires every guard generation/pair/revision/path field; the provisioner repeats the
locked route/binding/Git proof (`provisioner.py:248-280`).

## Ownership recognition and final DelegateResult mapping

Ownership sources, in safety order:

1. Exact stable metadata root or per-child manifest/ownership/attempt name, including malformed, symlinked, missing
   counterpart, case collision, and tombstone (`manifest.py:559-610`).
2. Exact parsed ownership/manifest workspace path, preserving the originally recorded root after manager root change.
3. A freshly loaded current route-managed child; `None` alone means truly unmanaged, while loader failure is already an
   owned card failure (`provisioner-test-brief.md:10-17`).
4. A known registration at the deterministic path, when reachable through the current route or durable ownership.

Registration-only recognition with no current route and no durable record is deferred: the authorized public APIs do
not expose a standalone observer, and runtime must not import private Git-state parsing to fabricate one.

| Runtime outcome | DelegateResult | Side effects/fallback |
|---|---|---|
| No current route and no durable identifier/path/registration evidence | `UNMANAGED` | Generic code may run later. |
| Deterministic owned path resolved | `HANDLED(path)` | No generic path sanitizer/mkdir/owner marker. |
| Attempt admitted provision/resume | `HANDLED(admission)` | Durable attempt CAS is complete before return. |
| Prepare succeeds | `HANDLED(prepared)` | No generic create/hook/marker. |
| Before-run attestation succeeds | `HANDLED(None)` | Backend may be constructed by later integration. |
| Verified cleanup succeeds | `HANDLED(None)` | Provisioner alone performed exact removal. |
| Manual/non-due/scope-reset admission | `OWNED_PRESERVED(bounded category)` | No dispatch, generic retry, or tracker mutation. |
| Missing/invalid authority or safe removing preservation | preserve provisioner's `OWNED_PRESERVED` | Not a health failure. |
| Recognized stale/disabled/rejected generation | `OWNED_PRESERVED` or `OWNED_ERROR` per gate table | Never generic fallback. |
| Recognized `AidtWorktreeFailure` | `OWNED_ERROR(failure.category)` | Record bounded failure; no nested message. |
| Any other exception after recognition | `OWNED_ERROR("internal_error")` | Record bounded failure; no nested message. |

`remove` is the only method that may return `UNMANAGED` after path/identifier probing. Once owned, it either returns the
provisioner's final result or a runtime preservation/error. It never downgrades denied cleanup to unmanaged. This keeps
production deny-all cleanup intact (`provisioner-test-brief.md:97-101`) and defers every Core terminal mutation guard to
the later integration slice (`core-integration-test-brief.md:196-215`).

## Durable admission algorithm

`admit_candidate` is the runtime/provisioner boundary:

1. Establish current route ownership with `load_route_dispatch_contract(generation.config, identifier)`.
2. Require current enabled/open/nonfatal generation and derive `StableWorktreePaths` from the process workflow path.
3. Read `read_optional_attempt` before acquiring the creation lock. This observation may race and authorizes no work.
4. If absent, construct `initial_attempt_record(identifier, route_pair_digest, workflow_generation, now)`, acquire
   the exact manifest lock, and persist it with `expected_revision=None` without a lock-bounded re-read. One contender
   wins; every expected-none CAS loser remains owned `cas_mismatch` and returns without retrying in that call.
5. Call public `admit_attempt(..., expected_revision, pair, generation, now, scope_attested=True)`; never duplicate its
   clock, retry, reset, or CAS logic.
6. Map `AttemptAdmission`:
   - admitted `provision` -> frozen `AidtProvisioningAdmission` with the consumed record revision;
   - admitted `resume` -> frozen admission with unchanged ready revision;
   - `manual` -> preserve its bounded durable category;
   - `backoff` -> `OWNED_PRESERVED("attempt_backoff")`;
   - `scope_reset` -> `OWNED_PRESERVED("scope_changed")` for this tick.
7. Recheck publication token before returning handled. A changed/rejected/fatal runtime cannot release the admission.

For a ready admission, re-read the exact ready manifest, non-tombstoned ownership record, and ready attempt under the
manifest lock before returning handled. Identifier, route pair, workflow generation, path, state, and revisions must
align. Missing/mismatch persists a manual owned failure and never reaches provisioner prepare.

Do not call `admit_attempt` while already holding its manifest lock; it acquires that lock itself
(`manifest.py:669-695`). Initial-record creation must release its bounded creation lock before the helper call. The
pre-lock optional read plus expected-none CAS is deliberate: it proves two process runtimes cannot both consume one
initial revision without deadlocking the executable two-thread barrier.

## Provisioner delegation

Keep one provisioner per current enabled material generation. Runtime does not inspect or modify provisioner internals.

- `create_or_reuse`: exact current generation/admission -> `provisioner.prepare(admission)`.
- `before_run`: exact current generation/guard -> `provisioner.attest_before_run(guard)`.
- `remove`: exact owned identifier/path/current generation -> `provisioner.cleanup(...)`.

Create/resume counters use `admission.action`, not `prepared.result.created_now`. Prepared-exact crash recovery returns
`created_now=False` but is still a create completion; the brief explicitly counts absent/prepared-to-ready as create
and ready entry as resume (`provisioner-test-brief.md:173-179`; transition distinction at `:103-118`). Increment once
immediately after successful `prepare`, before the final publication-token recheck. If a reload makes the result stale,
the physical create/resume remains counted and the returned `scope_changed` is separately counted as a failure.

## Fatal circuit and health

Fatal trigger categories:

- `persistence_failed` from provisioner failure-record loss (`provisioner.py:1116-1140`);
- `durability_failed` from runtime activation/initial-attempt/scope-reset/consume writes
  (`manifest.py:1268-1289,1418-1423`);
- `clock_invalid`, because invalid/non-UTC time is a permanent runtime failure
  (`PLAN.md:526-540`).

`cas_mismatch`, `registry_invalid`, card/scope drift, backoff/manual, and denied authority remain fail-closed owned
outcomes but do not by themselves open the process-global fatal latch. A write failure while trying to persist an
admission/reset does.

Health update rules:

| Event | Counters/detail/status |
|---|---|
| successful enabled publication | no create/resume increment; ready if not fatal; clear consecutive/category/ref |
| successful disabled publication | disabled if not fatal; clear consecutive/category/ref |
| successful `prepare` action `provision` | create +1; consecutive = 0; clear category/ref; set last-success |
| successful `prepare` action `resume` | resume +1; consecutive = 0; clear category/ref; set last-success |
| successful `before_run`/cleanup | no create/resume increment; cleanup preservation is not failure |
| owned runtime/provisioner error | failure +1; consecutive +1; sanitized category/ref; degraded |
| `reject_reload` | failure +1; consecutive +1; sanitized category; degraded |
| fatal trigger | failure +1 once for the triggering operation; latch fatal; preserve last bounded detail |
| repeated call rejected by already-open fatal latch | no repeated failure increment; return owned fatal outcome |

`enabled` reflects the last successfully published generation's settings, not the rejected candidate config.
`workflow_generation` is that last published digest or `None`. `status` precedence is `fatal` -> `degraded` ->
`ready|disabled`. `health_snapshot()` only copies locked in-memory scalars.

## Smallest vertical TDD order

Replace the current smoke assertions in `tests/test_aidt_worktree_runtime.py` before creating product code:

1. `test_never_enabled_unmanaged_runtime_is_inert`
   - exact DTO fields/frozen validation/redacted generation repr;
   - lazy facade import and unknown-name behavior;
   - disabled/unmanaged calls perform no activation, route, provisioner, Git, or write.
2. `test_stale_or_failed_reload_generation_cannot_reach_backend_barrier`
   - enabled publish, equivalent-publish idempotence, changed publish revision, disabled publish, reject reload;
   - race hooks before/after create and before-run; no stale handled guard.
3. `test_disabled_corrupt_missing_and_removed_ownership_never_falls_back`
   - registry identifier/path, root A -> B, malformed/symlink/case collision, missing manifest, tombstone/removed;
   - exact `UNMANAGED` negative controls and no generic-spy call.
4. `test_admission_handles_initial_manual_backoff_due_scope_reset_and_ready`
   - revision 1 creation, revision 2 consumption, manual/non-due preserve, due consume, reset-and-deny, ready resume;
   - concurrent initializer/CAS loser remains owned.
5. `test_ready_restart_admits_resume_once_without_fetch_or_add`
   - fake provisioner receives exact resume admission once; runtime performs no Git; counter is resume, not create.
6. `test_delegate_converts_post_recognition_exceptions_to_owned_error`
   - route/discovery/factory/prepare/attest/cleanup exception table;
   - only pre-recognition `None` yields unmanaged; authority preservation remains final and uncounted.
7. `test_persistence_failure_opens_fatal_circuit_for_process_lifetime`
   - activation write, initial attempt write, consume/reset write, and provisioner `persistence_failed`;
   - later valid publish/admit/create/before-run cannot reopen; unmanaged negative control remains unmanaged.
8. `test_health_counts_create_resume_failure_and_sanitizes_last_detail`
   - action-based counters, consecutive reset, status precedence, exact UTC timestamp;
   - hostile exception/path/URL/card/env sentinel absent from DTO repr, logs, and health.

Use fake provisioners and temporary workflow-relative metadata only. Do not use a real repository, network, route
service, Jira, backend, live AIDT checkout, or Core. Keep each product function <=50 lines and nesting <=4.

## Files and verification gates

Authorized product/test files for this slice:

- add `src/symphony/aidt_worktree/runtime.py`;
- edit `src/symphony/aidt_worktree/__init__.py` only for the three lazy runtime exports;
- replace/expand `tests/test_aidt_worktree_runtime.py` with behavioral tests;
- Binding Amendment 5 additionally permits the exact manual-ready validator correction in
  `src/symphony/aidt_worktree/manifest.py` plus its focused manifest regression;
- write builder/verifier evidence under this Frontier 003 changelog directory.

Explicitly unchanged except for Binding Amendment 5's exact manifest validator correction:

- `src/symphony/aidt_worktree/{contract,manifest,git_state,provisioner}.py`;
- `src/symphony/{workspace.py,orchestrator/core.py,orchestrator/entries.py}`;
- routing, tracker, workflow schema, health serialization, dashboard/TUI, prompt, deployment, and live profiles.

Verification order:

1. focused runtime RED, then GREEN;
2. accepted provisioner suite plus contract/manifest/Git-state foundation;
3. route dispatch/routing compatibility and facade lazy-import permutations;
4. Ruff and Pyright on runtime/facade/test slice;
5. AST function <=50/nesting <=4, `git diff --check`, no-index whitespace for untracked files;
6. static forbidden-import/side-effect scan proving provisioner independence and no workspace/core changes;
7. fresh independent verifier; no commit/network/live repository is part of this slice.

## Contradictions and missing seams to freeze before Build

1. **Equivalent publication is unstated but required.** The integration brief publishes every tick, while any later
   publication revision stales guards. Freeze same validated generation as idempotent; otherwise unchanged polling
   prevents every multi-turn worker from continuing.
2. **Factory invocation is underspecified.** `ProvisionerFactory` is named but not defined, while the accepted
   provisioner requires keyword-only `clock`. Freeze the internal call as `factory(config, settings, clock=clock)`; do
   not widen the public runtime constructor unless a behavioral test proves a missing dependency.
3. **Disabled restart versus zero metadata reads.** The plan requires prior activation to remain owned across restart
   and disable, but also says a never-enabled profile performs no metadata action. A new process cannot distinguish the
   two without one bounded no-follow probe of the exact workflow-relative activation/root. Safety requires that probe;
   it must never create directories, locks, or board/Git activity. Freeze this interpretation in the first and third
   tests.
4. **`ServiceConfig` is frozen but not deeply immutable.** Its `raw` field is a mutable dict
   (`src/symphony/workflow/config.py:434-461`). Treat published config as process-owned and never mutate it; a deep immutable config
   snapshot is outside this slice and must not be invented in runtime.
5. **Registration-only recognition has no standalone public observer.** Current APIs can recognize it through a fresh
   route or durable ownership and provisioner proof, but cannot enumerate an orphan registration with no card and no
   stable record. Do not reach into Git-state private helpers. If an executable RED fixture demands that impossible
   shape, stop and request a narrow public recognition seam rather than coupling runtime to Git parsing.
6. **Fatal category wording differs across layers.** Provisioner surfaces `persistence_failed`; manifest writes surface
   `durability_failed`; the plan makes invalid clock permanent. Freeze all three as fatal triggers and keep ordinary
   CAS/registry/card failures degraded, not fatal.
7. **Stale disposition spelling is not explicit.** Freeze stale admission/guard as `OWNED_ERROR("scope_changed")`,
   disabled/rejected reload as `OWNED_PRESERVED("profile_invalid" or rejected category)`, and already-fatal rejection as
   final owned error. Tests must assert the exact table before implementation.
8. **No runtime route-loader injection is in the frozen constructor.** Use the landed loader directly and patch the
   runtime module symbol in unit tests. Adding a public route-loader/registry-helper keyword would amend the frozen
   surface and is not authorized here.

None of these gaps authorizes edits to provisioner, manifest, Git-state, workspace, entries, or Core. If the first
behavioral RED tests show the current public APIs cannot satisfy the frozen mapping, pause at that exact seam and amend
the brief; preserve provisioner independence and defer integration.

## Binding runtime plan-attack corrections

This section and PLAN Binding Amendment 4 supersede any conflicting runtime wording above.

1. Publication builds one private immutable material key from validated settings/generation and the immutable config
   fields consumed by runtime/provisioner. It does not trust later equality of mutable `ServiceConfig.raw`. Validation,
   UTC-clock validation, activation, and factory construction complete before atomic publication. Factory/activation
   failure preserves the exact current DTO/provisioner; Core applies one bounded rejection. Durability and invalid
   clock latch fatal exactly once. Equivalent publication after fatal may return the current DTO; material change or
   disable raises the latched failure and publishes nothing.
2. The exception table is exact: loader `None` before recognition is unmanaged; production `AidtRoutingFailure` for a
   canonical child is owned `card_invalid`; bounded `AidtWorktreeFailure` retains its allowlisted category/ref; every
   other post-recognition exception is owned `internal_error`. Factory failures belong to publication, not delegation.
3. Fatal coverage includes activation, initial attempt, consume/reset, provisioner `persistence_failed`, and invalid or
   non-UTC clock. Repeated fatal calls/rejections do not increment failure again. `health_snapshot` copies locked
   scalars only and must remain identical with clock, route, registry, manifest, provisioner, and I/O seams replaced by
   raising sentinels.
4. Test/static gates exercise ready evidence missing/mismatch, factory/activation publication failure, action-based
   counting with `created_now=False` and a stale postcheck, real routing exceptions, a competing initializer/CAS loser,
   and product AST limits of 50 lines/four nesting levels. These checks retain the eight frozen public test names by
   using bounded helpers/parameterization.
