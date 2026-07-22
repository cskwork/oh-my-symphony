# Frontier 003 core integration test brief

## Decision

Integrate the landed AIDT worktree runtime through the existing `WorkspaceManager` and Core lifecycle. One
`AidtWorktreeRuntime` belongs to the `Orchestrator` process, while every attempt captures an immutable runtime
generation and the exact manager that admitted it. Only `DelegateResult.UNMANAGED` may enter existing generic
workspace behavior. Frontier 003 issues no production completion authority, so every AIDT terminal path preserves
the worktree and leaves cleanup pending.

Tests use fake delegates, frozen configs, temporary metadata, and command/hook spies. They never use a live AIDT
checkout, repository, network, Jira, backend process, merge, push, or deployment.

## Existing contracts that remain authoritative

- `AidtRoutingResult.provisionable_child_identifiers` is the nomination hint. Coordinators, review, stale, retained,
  and every non-provisionable managed identifier remain blocked.
- `filter_routing_candidates(candidates, blocked, provisionable=frozenset())` preserves the old two-argument behavior
  and input order.
- `AidtWorktreeRuntime` owns recognition, immutable generation publication, durable admission, create/resume,
  before-run attestation, cleanup disposition, the fatal circuit, and bounded health.
- `AidtProvisioningAdmission` freezes workflow generation, route-pair digest, admitted attempt-record revision, and
  action. `AidtRunGuard` freezes the prepared manifest revision and exact workspace.
- Only `UNMANAGED` permits fallback. `HANDLED`, `OWNED_PRESERVED`, and `OWNED_ERROR` are final for that Core attempt.
- `DenyAllCompletionAuthority` remains the production default. No Core, manager, tracker, or test helper in this
  slice becomes a production authorization issuer.

## Frozen product touch points

Only these integration files change after the red tests exist:

- `src/symphony/workspace.py`
- `src/symphony/orchestrator/entries.py`
- `src/symphony/orchestrator/core.py`
- public type exports in `src/symphony/aidt_worktree/__init__.py` only if imports require them

No routing, manifest, Git-state, provisioner, runtime algorithm, tracker, backend, run-registry, workflow schema,
dashboard, TUI, prompt, or deployment redesign is part of this slice.

## `WorkspaceManager` delegate seam

Append one optional constructor keyword; all existing positional and keyword construction stays valid:

```python
aidt_runtime: AidtWorktreeRuntime | None = None
```

Every replacement manager receives the same object. Runtime-only types are imported under `TYPE_CHECKING`; disabled
startup must not eagerly load `aidt_worktree.provisioner` or `aidt_worktree.git_state`.

Append only optional keywords to the four owned operations:

```python
def path_for(
    self,
    identifier: str,
    *,
    aidt_generation: AidtWorktreeGeneration | None = None,
) -> Path: ...

async def create_or_reuse(
    self,
    identifier: str,
    *,
    aidt_generation: AidtWorktreeGeneration | None = None,
    aidt_admission: AidtProvisioningAdmission | None = None,
) -> Workspace: ...

async def before_run(
    self,
    path: Path,
    *,
    aidt_generation: AidtWorktreeGeneration | None = None,
    aidt_guard: AidtRunGuard | None = None,
) -> None: ...

async def remove(
    self,
    path: Path,
    *,
    identifier: str | None = None,
    aidt_generation: AidtWorktreeGeneration | None = None,
    authorization: CompletionAuthorization | None = None,
    lease: ActiveCompletionLease | None = None,
) -> DelegateResult[None] | None: ...
```

`Workspace` appends `aidt_guard: AidtRunGuard | None = None`. Existing construction, equality fields, and unmanaged
return values remain valid.

The method rules are exact:

1. No AIDT generation/admission/guard/identifier keywords means the current generic implementation byte-for-byte in
   observable behavior: path sanitization, owner marker, mkdir, hooks, reuse, logging, and return values.
2. With an AIDT generation, ask the process runtime first. `HANDLED` returns its path/prepared guard/result and skips
   generic mkdir, markers, and hooks. `UNMANAGED` runs the current generic path. Owned preservation/error raises one
   bounded `AidtWorkspaceOperationError(category, ref)`; its message never includes paths, Git output, card text, or
   nested exception text.
3. `remove(path, identifier=..., aidt_generation=...)` is the non-destructive terminal guard. It returns the runtime
   `DelegateResult` and never invokes generic removal on `UNMANAGED`. Callers may run the old positional
   `remove(path)` only after observing exact `UNMANAGED`.
4. An AIDT `HANDLED` create returns `Workspace(..., aidt_guard=prepared.guard)`. AIDT before-run requires that exact
   guard. A missing or mismatched generation/admission/guard is owned failure, never generic fallback.
5. Core skips `after_run_best_effort` for an entry carrying an AIDT guard. Terminal ownership is resolved before
   generic `after_done`; therefore no AIDT worktree runs generic create/before/after/remove hooks.

## Process-lifetime runtime and reload order

`Orchestrator.__init__` constructs or receives exactly one `AidtWorktreeRuntime` from
`workflow_state.path`. An injectable runtime/factory is allowed only for isolated tests. Core stores the latest
successfully published `AidtWorktreeGeneration` separately from `WorkspaceManager`.

For startup and every `_on_tick` reload, the synchronous no-`await` publication block is:

1. obtain and validate `ServiceConfig`;
2. call `runtime.publish(cfg)` and retain the returned immutable generation;
3. create or update the generic manager, passing the same process runtime;
4. atomically assign Core's current config-facing generation;
5. continue routing and candidate work.

If publication fails, call `runtime.reject_reload` with a bounded category, retain the previous manager and
generation only for ownership/old-guard recognition, and return before heartbeat reconciliation, candidate fetch,
normalization, or dispatch. A root change may replace the manager only after successful publication. Old
`RunningEntry` instances keep their old manager; both old and new managers point to the same process runtime and
workflow-relative stable registry. A later successful generation makes an earlier admission/guard fail at its next
runtime barrier rather than fall back.

## Candidate and dispatch boundary

Initial `_on_tick` uses this order:

1. routing succeeds and `allow_dispatch` is true;
2. fetch candidates;
3. call `filter_routing_candidates(candidates, blocked_identifiers,
   provisionable_child_identifiers)`;
4. for each survivor, call the shared `_admit_aidt_candidate(generation, issue)` before slot, conflict, persisted
   retry, lease, workspace path, or task creation;
5. `UNMANAGED` continues unchanged; `HANDLED(admission)` may dispatch; owned preservation/error stops locally with no
   generic retry or tracker mutation;
6. `_dispatch` receives the generation and admission as appended keyword-only arguments.

`_admit_aidt_candidate` returns the runtime's sealed `DelegateResult[AidtProvisioningAdmission]`; it does not reduce
owned states to `None` or booleans.

Before `asyncio.create_task`, `_dispatch` captures these defaulted fields in `RunningEntry`:

```python
workspace_manager: WorkspaceManager | None = None
aidt_generation: AidtWorktreeGeneration | None = None
aidt_admission: AidtProvisioningAdmission | None = None
aidt_guard: AidtRunGuard | None = None
aidt_workflow_generation: str | None = None
aidt_route_pair_digest: str | None = None
aidt_attempt_record_revision: int | None = None
aidt_owned_failure: bool = False
aidt_failure_category: str | None = None
```

Existing `RunningEntry(...)` call sites remain valid. For AIDT, the workflow generation, pair digest, and admitted
revision are copied from the admission and must equal the captured generation. `_dispatch` resolves the path through
the captured manager/generation, acquires the existing run lease, installs the complete entry, and only then creates
the task. Invalid paired arguments fail before lease/task creation.

## Worker barriers

`_run_agent_attempt` reads `entry.workspace_manager`; it never dereferences `self._workspace_manager` after dispatch.
The AIDT sequence is:

1. captured manager `create_or_reuse(identifier, generation, admission)`;
2. store the returned path and guard on the same owned entry;
3. captured manager `before_run(path, generation, guard)`;
4. only after successful attestation, construct and start the backend;
5. before every later backend turn or rebuilt phase backend, call the same captured manager and guard again;
6. skip generic after-run hooks for the guarded worktree.

Reload between nomination/create, create/before-run, or turns therefore stops at the next barrier. A typed owned
failure sets `aidt_owned_failure` and bounded category before worker exit. It never exposes raw exception text.
Unmanaged workers retain the current create, hooks, backend, and after-run sequence.

## Timer retry and durable failure ownership

`_process_retry` keeps its current fetch and eligibility handling, then calls the same
`_admit_aidt_candidate(current_generation, match)` immediately before `_dispatch`:

- `UNMANAGED`: preserve the current generic retry kind, attempt number, cap, and slot behavior;
- `HANDLED`: dispatch with the new generation/admission, not the old Issue/config nomination;
- `OWNED_PRESERVED` or `OWNED_ERROR`: release the generic retry entry/claim without clearing AIDT durable state,
  schedule no generic timer, and perform no tracker pause/state mutation.

An owned failure from path/create/before-run follows the same rule in `_on_worker_exit_impl`: finish the run lease,
clear generic in-flight bookkeeping, retain bounded debug category, and do not call `_schedule_retry`, generic
auto-pause, generic retry persistence, commit, merge, hook, or remove. Manual disposition remains non-dispatchable;
due backoff is reconsidered only by a later ordinary poll and fresh runtime admission. Generic backend and unmanaged
workspace failures keep the existing retry policy.

## Terminal preservation

Add one small Core helper that calls the captured manager's keyworded `remove` as the ownership guard before any
generic mutation. It returns the sealed result unchanged.

- `UNMANAGED`: run the current terminal sequence and finish with old positional `remove(path)`.
- `HANDLED`: cleanup was explicitly authorized and completed. This is fixture-only in Frontier 003.
- `OWNED_PRESERVED` or `OWNED_ERROR`: cancel/stop the worker as needed, but run no generic commit, auto-merge,
  after-done hook, raw removal, retry, or tracker mutation.

Apply the guard before mutation in all current sites: worker-exit Done handling, `_reconcile_one` terminal and
inactive branches, `_startup_terminal_cleanup`, and the helper that commits/merges/hooks/removes after Done. Every
running path uses `entry.workspace_manager` and `entry.aidt_generation`; startup uses the current successfully
published generation and the process runtime's stable recognition.

Production passes `authorization=None` and issues no `CompletionAuthorization`. Consequently AIDT returns
`OWNED_PRESERVED("authorization_invalid")`. Reconcile may cancel the worker, but it must not set
`workspace_cleanup_started`; the active run lease remains authoritative until normal worker teardown. Startup,
manual Done, blocked, inactive, disabled, corrupt, missing-manifest, removed, stale-generation, and wrong-root AIDT
identities all preserve. Generic paths run only after exact `UNMANAGED`.

## Bounded health

`Orchestrator.health()` adds exactly this sibling of `aidt_routing`, copied from `runtime.health_snapshot()`:

```python
"aidt_worktree": {
    "enabled": bool,
    "status": "disabled" | "ready" | "degraded" | "fatal",
    "workflow_generation": str | None,
    "create_count": int,
    "resume_count": int,
    "failure_count": int,
    "consecutive_failures": int,
    "last_category": str | None,
    "last_ref": str | None,
    "last_success_at": str | None,
}
```

`degraded` and `fatal` add only `aidt_worktree_failure` to `degraded_reasons`. Serialization is an in-memory snapshot:
no filesystem, Git, registry, route, or tracker read. Unknown runtime fields are ignored. Health and logs never
include absolute or relative paths, lock keys, Git argv/output, repository URLs, card prose, environment, nested
exceptions, or authorization values.

## Exact red tests

Add `tests/test_aidt_worktree_core_integration.py` with:

- `test_process_runtime_identity_survives_workspace_manager_root_replacement`
- `test_failed_generation_publication_keeps_manager_and_denies_candidate_work`
- `test_provisionable_child_is_filtered_then_admitted_before_slot_or_lease`
- `test_owned_candidate_disposition_never_reaches_generic_dispatch`
- `test_dispatch_captures_manager_generation_pair_and_attempt_revision_before_task`
- `test_initial_attempt_uses_captured_manager_create_guard_before_backend`
- `test_reload_between_create_and_before_run_blocks_backend_without_fallback`
- `test_reload_between_turns_rechecks_the_captured_guard`
- `test_timer_retry_uses_fresh_runtime_admission_and_preserves_attempt_kind`
- `test_timer_retry_owned_disposition_releases_generic_retry_without_repark`
- `test_specialized_create_or_guard_failure_schedules_no_generic_retry`
- `test_worker_exit_done_owned_preserved_skips_commit_merge_hooks_and_remove`
- `test_reconcile_terminal_owned_preserved_leaves_cleanup_pending`
- `test_reconcile_inactive_owned_preserved_leaves_cleanup_pending`
- `test_startup_terminal_owned_preserved_skips_every_generic_mutation`
- `test_production_terminal_paths_never_issue_completion_authority`
- `test_unmanaged_initial_retry_worker_and_terminal_paths_keep_existing_order`
- `test_health_serializes_only_the_bounded_worktree_snapshot`

Surgically extend `tests/test_workspace.py` with:

- `test_delegate_unmanaged_preserves_workspace_create_hooks_marker_and_return`
- `test_delegate_handled_create_returns_guard_without_generic_side_effects`
- `test_delegate_owned_create_and_before_run_never_fall_back`
- `test_keyworded_remove_is_a_non_destructive_unmanaged_probe`
- `test_keyworded_owned_remove_preserves_before_generic_hook_or_rmtree`

Surgically extend `tests/test_aidt_routing_runtime.py` with:

- `test_core_releases_only_provisionable_managed_children_in_input_order`
- `test_never_enabled_core_does_not_load_provisioner_or_git_state`

Surgically extend `tests/test_orchestrator_health.py` with:

- `test_worktree_degraded_and_fatal_health_add_one_bounded_reason`

Every integration fake records a single ordered event list. Assertions cover `filter -> admit -> path -> lease ->
entry -> create -> guard -> backend`, absence of forbidden generic calls, exact manager/runtime object identity, and
no leaked hostile sentinel in health/logs. Parameterize final delegate states where useful, but do not duplicate the
provisioner/runtime state-machine tests already frozen in `provisioner-test-brief.md`.

## Compatibility and verification gates

- Old `WorkspaceManager` calls and `RunningEntry` constructors pass unchanged.
- Never-enabled/unmanaged runs create no AIDT metadata, lock, manifest, Git command, network call, or board mutation.
- Enabled-to-disabled and failed reload preserve known ownership; they never regain generic behavior.
- Keep new functions at 50 lines or fewer and nesting at four or fewer; extract admission and terminal guards instead
  of expanding `_on_tick`, `_run_agent_attempt`, `_process_retry`, or `_reconcile_one` monolithically.
- Run the new focused file, affected routing/workspace/dispatch/reconcile/health suites, full tests, Ruff, Pyright,
  AST structure checks, whitespace checks, and the lazy-import sentinel. Report exact commands and results; no live
  profile or AIDT checkout is an accepted verification step.
