# R-LOOP - Frontier 003 AIDT Worktree Provisioner

## Iteration Ledger

| Iteration | Build | Verify | Reflection | Next |
|---|---|---|---|---|
| 0 | not started | fresh attack failed twice, then passed Amendment 3 | exact schemas and lifecycle frozen | enter Build |
| 1 | route, manifest, and Git-state foundations built | failed: 5 MUST, 2 SHOULD in Git-state; 3 MUST, 1 SHOULD in manifest helpers | green happy paths did not prove trust-boundary semantics | repair every finding with executable semantic regressions |
| 2 | all original findings repaired; 349-test superset green | failed: 3 MUST, 2 SHOULD reopened by real Git and forged-record probes | parser compatibility, category coupling, and check/use races needed real producers and adversarial preimages | repair exact reopened findings; force redesign on another failure |
| 3 | iteration-2 repairs complete; 410-test superset green | failed: real Git worktree-side `.R`/`.C` rows rejected | three-loop limit reached; negative examples had produced a false index-only grammar | forced reflection before any further product edit |
| FR | no product edit | official Git 2.53 docs plus four producer families | exact two-sided type-2 matrix frozen; one bounded exception loop authorized | correct only `XY`/score-kind coupling, then require zero-MUST/SHOULD recheck |
| R1 | exact two-sided type-2 correction; 445-test superset green | PASS: zero MUST/SHOULD, independent producer and prior-finding audits | forced-reflection stop condition satisfied; foundation closed | build persisted recovery-proof APIs before provisioner repair |
| R2 | persisted recovery proofs built, acceptance defects reproduced and repaired; 153 focused/592 superset green | PASS: zero MUST/SHOULD; fresh matrix, DTO, closing-observation, command, and fingerprint audits | recovery must bracket collections/path/ticket and totalize every public result DTO before it can authorize lifecycle recovery | start provisioner repair with executable rejection of `_state_from_snapshot` and direct target-path probes |
| R3 | provisioner lifecycle TDD repaired all 12 MUST/3 SHOULD findings plus 3 verifier gaps; 65 focused/460 compatibility tests green | PASS: zero required/recommended corrections; fresh proof, sidecar, lock-lineage, and failure-persistence audits | recovery proof must precede suffix repair; sidecars bind exact owner identity; failure CAS reacquires the real lock lineage | build runtime behavior test-first, then integrate Core/workspace |
| R4 | process-lifetime runtime built test-first; repaired DTO totality, truthful counters, issued capabilities, lexical construction, and reverse removal ownership; 8 focused/125 controls/65 provisioner/204 routing green | PASS: independent final replay closed the last durable identifier/path mismatch with zero required findings | runtime capabilities must be exact, identifier-bounded, and fail closed before delegation; durable path ownership outranks an unknown supplied identifier | integrate the approved runtime through WorkspaceManager and Core, then repeat the pre-backend and unmanaged-parity gates |

Maximum Build/Verify iterations: 3.

## 2026-07-22 Core/workspace Integration RED Handoff

Status: fresh Builder re-entry. The Workspace and Core RED suites are intentionally failing only because the frozen
product integration is absent; both final adversarial review tails approve the executable RED contract. Implement
from `PLAN.md` plus this latest section only. Do not edit or weaken tests.

### Theory and real-world model

A routed child is a custody transfer for one exact ticket/service/base/branch/worktree identity. Routing nominates;
the process-lifetime `AidtWorktreeRuntime` durably admits; a `RunningEntry` captures the immutable generation,
admission, manager, and later guard; every backend boundary re-attests that captured custody. Reload may publish a
new generation for future work, but it may not split an existing attempt across managers or turn an owned AIDT path
into a generic workspace. Generic workspace behavior remains unchanged only for a result whose disposition is
exactly `UNMANAGED`.

### Frozen product surface

Change exactly these four product touch points:

- `src/symphony/workspace.py`
- `src/symphony/orchestrator/entries.py`
- `src/symphony/orchestrator/core.py`
- `src/symphony/aidt_worktree/__init__.py` only if the existing lazy public facade needs an integration type export

Do not redesign routing, runtime, provisioner, manifest, Git-state, tracker, backend, run registry, workflow schema,
dashboard, TUI, prompts, or deployment.

Append `aidt_guard: AidtRunGuard | None = None` to `Workspace`. Append the following optional constructor keyword to
`WorkspaceManager`, preserving every existing positional/keyword call:

```python
aidt_runtime: AidtWorktreeRuntime | None = None
```

Freeze the four owned-operation signatures as append-only optional keywords:

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

Append these defaulted `RunningEntry` fields so all existing constructors remain valid:

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

`_dispatch` receives `aidt_generation` and `aidt_admission` as appended keyword-only arguments. Reject either
half-pair before path lookup, lease acquisition, entry installation, or task creation.

### Required order and dispositions

1. **Startup publication:** construct exactly one process-lifetime `AidtWorktreeRuntime` from
   `workflow_state.path` in `Orchestrator.__init__`, without loading provisioner/Git-state modules. On `start()`,
   validate config, `runtime.publish(cfg)`, construct the manager with that same runtime, then publish Core's current
   immutable generation.
2. **Reload:** in one synchronous no-`await` block, validate config -> `runtime.publish(cfg)` -> create/update the
   manager with the same runtime -> assign Core's current generation. Publication failure calls bounded
   `runtime.reject_reload`, retains the prior manager/generation only for ownership recognition, and returns before
   heartbeat, reconciliation, candidate fetch, normalization, or dispatch.
3. **Candidate:** routing success/allow -> fetch ->
   `filter_routing_candidates(candidates, blocked_identifiers, provisionable_child_identifiers)` -> runtime admission
   before slot, eligibility, conflict, persisted retry, lease, path, or task. The executable trace is
   `filter -> admit -> slot -> eligibility -> conflict -> path -> lease -> entry -> create -> guard -> backend`.
4. **Dispatch:** capture the exact manager, generation, admission, workflow generation, route-pair digest, and
   attempt-record revision in `RunningEntry` before `asyncio.create_task`; acquire the lease only after the captured
   manager/generation resolves the path. Legacy entry defaults stay exactly null/null/null/null/null/null/null/false/null.
5. **Worker:** use `entry.workspace_manager` for create/reuse and every guard; store the returned path/guard; run the
   guard before initial backend construction, before every later turn, and after old-backend stop but before a
   rebuilt backend. Guarded AIDT work skips generic after-run hooks. A bounded owned failure sets the entry failure
   fields and exits without raw exception text or generic fallback.
6. **Retry:** `_process_retry` fetches the current issue, then re-admits against Core's current generation immediately
   before dispatch. `HANDLED` dispatches with the fresh generation/admission and preserves retry kind/attempt.
   `OWNED_PRESERVED`/`OWNED_ERROR` release only generic retry ownership, retain pause/debug/runtime durable state,
   and do not repark, schedule, mutate tracker state/notes, or dispatch. Specialized create/guard failure suppresses
   `_schedule_retry`; due durable backoff re-enters only on a later ordinary poll.
7. **Terminal:** call the captured manager's keyworded `remove(..., authorization=None, lease=None)` ownership guard
   before worker-exit Done, terminal/inactive reconcile, startup cleanup, commit, merge, hooks, tracker mutation, or
   recursive removal. `OWNED_PRESERVED`/`OWNED_ERROR` may cancel a live worker only after the guard, but leave
   `workspace_cleanup_started=False` and cleanup pending. Frontier 003 production authority is deny-all: Core never
   constructs or issues `CompletionAuthorization`; generic Done, identifier match, startup, or inactive state is not
   authority.
8. **Health:** copy only `runtime.health_snapshot()` into the exact bounded `aidt_worktree` sibling fields from the
   frozen brief. Add only `aidt_worktree_failure` for degraded/fatal status. Do not read provider clock, route,
   filesystem, registry, manifest, Git, tracker, network, or backend state; Core's own response timestamp remains
   legitimate.

Only exact `DelegateDisposition.UNMANAGED` permits generic behavior. `HANDLED`, `OWNED_PRESERVED`, and `OWNED_ERROR`
are final. For keyworded `WorkspaceManager.remove`, even `UNMANAGED` is only a non-destructive ownership probe: it
returns unchanged and never removes; only the caller's later explicit legacy positional `remove(path)` may execute
generic cleanup after observing that exact result. Once recognition starts, translate bounded owned outcomes to
`AidtWorkspaceOperationError(category, ref)` without path, Git output, card text, nested exception text, or other
unbounded data.

### Smallest implementation sequence

1. Add the `RunningEntry`/`Workspace` defaults and only the lazy type exports/import guards required by the frozen
   annotations.
2. Implement the `WorkspaceManager` constructor bridge and four delegate operations, including half-pair rejection,
   bounded error translation, guarded `Workspace.aidt_guard`, and the non-destructive keyworded remove probe.
3. Wire process runtime startup/reload publication, provisionable filtering, shared candidate admission, paired
   dispatch capture, and immutable captured-manager ownership.
4. Wire initial/rebuilt worker guards and specialized failure exit, then fresh retry admission/release.
5. Put the terminal ownership guard before every generic mutation site, then add bounded in-memory health. Do not
   add production completion authority.

### Builder RED-to-GREEN and affected controls

Run the two intentional RED commands first; preserve their test names and make them GREEN only through the four
product touch points:

```bash
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q --tb=line \
  tests/test_workspace.py -k 'delegate or keyworded'

rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_core_integration.py \
  tests/test_aidt_routing_runtime.py::test_core_releases_only_provisionable_managed_children_in_input_order \
  tests/test_aidt_routing_runtime.py::test_never_enabled_core_does_not_load_provisioner_or_git_state \
  tests/test_orchestrator_health.py::test_worktree_degraded_and_fatal_health_add_one_bounded_reason
```

Current intentional state: Workspace `20 failed, 41 deselected`; combined Core/extensions `20 failed, 1 passed`
(the one pass is the required never-enabled lazy-import control). Re-run these affected controls after each cohesive
step:

```bash
rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_workspace.py::test_create_and_reuse \
  tests/test_workspace.py::test_sanitization \
  tests/test_workspace.py::test_before_run_aborts_attempt

rtk env PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_routing_runtime.py tests/test_orchestrator_health.py \
  tests/test_orchestrator_dispatch.py::test_retry_timer_capacity_wait_preserves_attempt_kind_and_cap \
  tests/test_orchestrator_dispatch.py::test_retry_timer_releases_durable_rejection \
  tests/test_orchestrator_dispatch.py::test_on_worker_exit_commits_workspace_at_done \
  tests/test_orchestrator_dispatch.py::test_reconcile_terminate_terminal_commits_before_remove \
  tests/test_orchestrator_reconcile.py \
  -k 'not core_releases_only_provisionable_managed_children_in_input_order and not never_enabled_core_does_not_load_provisioner_or_git_state and not worktree_degraded_and_fatal_health_add_one_bounded_reason'
```

Stop only when both intentional RED commands and affected controls are green, with no changed tests. Leave full
integration/static/literal-gate proof to a fresh verifier. Do not use a live AIDT checkout/profile, repository or
network mutation, Jira, real backend process, completion authority, commit, merge, push, deployment, dashboard, or
TUI action.

## 2026-07-22 12:33 KST Final Exact Verify - REVISE

Status: three required corrections remain. The independently rerun behavioral matrices are green, but the frozen
reload rule, clean backward trace, and full no-index whitespace gate are not satisfied. Do not edit unrelated product
behavior or weaken the 41-case integration contract.

### Criterion 1 / 8 - rejected disabled publication continues the tick

- [ ] Expected: every validation/publication failure calls bounded `runtime.reject_reload`, retains the prior
  manager/generation for ownership recognition, and returns before manager mutation, heartbeat, reconciliation,
  candidate fetch, or dispatch.
- [ ] Actual: `Orchestrator._publish_aidt_worktree_config` catches any failure, then
  `core.py:2270-2272` calls `_update_workspace_manager(cfg)` and returns `True` whenever raw `aidt_worktree` looks
  disabled. Legacy reload tests reach this path by constructing `WorkflowState(unused.md)` with a different
  `cfg.workflow_path`; that fixture mismatch is not production compatibility authority.
- [ ] Evidence: source audit of `src/symphony/orchestrator/core.py:2243-2274`; exact 41-case and 326-case matrices pass
  without a disabled-publication-failure early-return assertion.
- [ ] Smallest next fix: first add a regression that forces publication failure for a disabled-looking config and
  requires trace `publish -> reject` only with the exact prior manager/generation retained. Then remove the disabled
  failure continuation. Update legacy reload fixtures so `WorkflowState.path == cfg.workflow_path`; valid disabled
  publication must succeed normally rather than relying on a safety bypass.

### Criterion 9 - remove test-only Core aliases and restore clean backward trace

- [ ] Expected: every product hunk maps to the frozen process runtime/current-generation contract.
- [ ] Actual: `Orchestrator.__init__` assigns `_aidt_runtime` and `_aidt_generation`, and
  `_set_aidt_generation` keeps the latter synchronized, but product search finds no read of either alias. Test helpers
  assign both aliases alongside `_aidt_worktree_runtime`/`_aidt_worktree_generation`.
- [ ] Evidence: `rg -n '_aidt_runtime|_aidt_generation' src tests` shows product writes only at
  `core.py:642`, `core.py:644`, and `core.py:2229`; consumers use only the canonical worktree-prefixed fields.
- [ ] Smallest next fix: remove the two aliases and alias-only synchronization, keep one canonical runtime/generation
  pair, and update test setup to assign/assert only that pair. Re-run the exact 41, 459, and 326 matrices.

### Criterion 9 - full untracked no-index whitespace gate

- [ ] Expected: `git diff --check` and no-index `--check` for every untracked file intended for the Frontier commit
  emit no whitespace diagnostic.
- [ ] Actual: tracked diff and all 16 untracked product/test files are clean, but the complete 73-file no-index scan
  reports six documentation files: `exploration/frontier003-route-dispatch-contract.md`,
  `exploration/worktree-integration-plan.md`, `core-integration-red-attack-report.md`, `plan-attack.md`,
  `runtime-implementation-map.md`, and `runtime-plan-attack-report.md`.
- [ ] Evidence: evaluator-owned no-index scan reports `untracked_checked=73 whitespace_violations=6`; five files have
  trailing spaces and `core-integration-red-attack-report.md` has a new blank line at EOF.
- [ ] Smallest next fix: remove only the reported whitespace, rerun the complete no-index scan, and preserve document
  wording. Then rerun Ruff, Pyright, AST, full pytest parity, doctor, and fresh Exact Verify before any literal gate.

## 2026-07-22 13:07 KST Fail-Closed Repair Builder Evidence

Status: the smallest R-LOOP repair is implemented and the mandatory builder matrices are green. Fresh Exact Verify
still owns the verdict, GOAL ticks, run-state, literal gate, and commit.

### RED -> GREEN

- [x] The frozen publication test now supplies a disabled-looking rejected config with a different workspace root and
  retains an exact prior manager/generation. Before the source fix, the focused command failed with the trace
  `publish -> reject:profile_invalid -> heartbeat` and logged `workspace_root_changed`; the expected trace was only
  `publish -> reject:profile_invalid`.
- [x] The same focused command passes after removing the disabled-looking failure continuation:
  `rtk env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/pytest -p no:cacheprovider -q tests/test_aidt_worktree_core_integration.py::test_failed_generation_publication_keeps_manager_and_denies_candidate_work`
  -> `1 passed in 0.49s`.
- [x] `Orchestrator._publish_aidt_worktree_config` now returns `False` after every caught publication failure and one
  bounded `reject_reload`; it does not mutate the workspace manager or generation on that path.
- [x] Product write-only `_aidt_runtime` and `_aidt_generation` aliases and their test-helper assignments are removed;
  the canonical fields are `_aidt_worktree_runtime` and `_aidt_worktree_generation` only.
- [x] Valid disabled legacy tick/reload fixtures now bind `WorkflowState.path` to `ServiceConfig.workflow_path`,
  including the two custom retry configs, two workspace-manager reload configs, and archive-throttle config.
- [x] Only the reported trailing spaces and extra EOF blank line were removed from the six named documentation files;
  wording is unchanged.

### Final-byte builder checks

- Exact integration barrier: `41 passed in 1.37s`.
- Frozen affected matrix: `459 passed, 1 skipped, 23 deselected in 66.77s`.
- Orchestrator compatibility: `326 passed in 18.56s`.
- Ruff: `All checks passed!`; Pyright: `0 errors, 0 warnings, 0 informations`.
- Executable lazy/AST sentinel: `1 passed in 0.54s`. Baseline-delta AST scan: 864 product functions, 496 new
  functions, maximum new size 47 lines, maximum nesting 4, zero new limit crossings.
- `git diff --check` emitted no diagnostic. The complete no-index scan reports
  `untracked_checked=73 whitespace_violations=0`.
- The optional 752-case AIDT matrix returned `751 passed, 1 skipped, 1 failed` because
  `tests/test_aidt_worktree_recovery_proofs.py::test_removed_recovery_rejects_incomplete_and_drifted_shapes[branch_absent-collision]`
  hit the fixed 10-second temporary `git worktree add` timeout. Its exact isolated rerun passed in `1.43s`; no source,
  timeout, or assertion was changed for this unrelated transient.
- No live AIDT/Jira/network/backend, completion authority, merge, push, deploy, dashboard, TUI, or commit action ran.

## 2026-07-22 13:51 KST Fresh Exact Verify - REVISE

Status: the fail-closed repair itself is proven, but full repository parity is red. One stale default-off lifecycle
fixture still publishes a config under a different workflow identity, and real temporary-Git worktree creation has
now exceeded the frozen 10-second deadline in three separate suite executions. Do not restore the disabled-publication
bypass, raise the frozen timeout, simulate worktree add/remove, weaken assertions, or classify a killed Git process as
success.

### Criterion 1 / 8 - default-off lifecycle fixture publishes the wrong workflow identity

- [ ] Expected: the real file-board lifecycle test publishes a valid disabled configuration whose
  `ServiceConfig.workflow_path` exactly equals the process-lifetime `WorkflowState.path`; default-off triage and
  dispatch then preserve legacy behavior through the normal publication path.
- [ ] Actual: `tests/test_agent_lifecycle_e2e.py::_orch` hardcodes `WorkflowState(Path("/tmp/no.md"))`, while
  `test_file_board_e2e_auto_triage_dispatches_and_reaches_done` reloads a config at the temporary
  `<tmp>/WORKFLOW.md`. The fail-closed runtime correctly rejects that identity mismatch as `profile_invalid`, so the
  first tick returns and the card remains `Todo` instead of reaching `In Progress`.
- [ ] Evidence: full repository failure
  `tests/test_agent_lifecycle_e2e.py::test_file_board_e2e_auto_triage_dispatches_and_reaches_done`; captured log
  `aidt_worktree_publication_failed category=profile_invalid stage=publication`; assertion actual `Todo`, expected
  `In Progress`. Source trace confirms the mismatch is test setup, not compatibility authority.
- [ ] Smallest next fix: preserve the product's universal fail-closed return. Parameterize the lifecycle test helper
  so this `_on_tick` test constructs `WorkflowState(cfg.workflow_path)` while its fake workspace path remains a
  separate argument. Use the existing full-suite failure as RED; prove the focused lifecycle E2E GREEN, then rerun
  the exact 41-case barrier and 326-case orchestrator matrix.

### Criterion 8 / 9 - repeated fixed-deadline temporary-Git worktree-add failures

- [ ] Expected: every real temporary `git worktree add --no-track -b ...` fixture completes within the frozen
  `GIT_LOCAL_TIMEOUT_SECONDS = 10.0`; the 752-case matrix and full repository have no failure beyond the accepted
  missing `kanban/CI-1.md` baseline.
- [ ] Actual: the builder first observed one killed add at
  `test_removed_recovery_rejects_incomplete_and_drifted_shapes[branch_absent-collision]`. The fresh 752-case matrix
  then passed, but the immediately following full repository run killed two more independent add processes after
  10 seconds. Both returned `GitCommandResult(returncode=-9, timed_out=True)` with bounded stderr
  `Preparing worktree (new branch 'fix/A20-1188')`, which product correctly mapped to `protocol_invalid`.
- [ ] Evidence: full repository failures
  `tests/test_aidt_worktree_provisioner.py::test_removing_recovery_rejects_every_wrong_authority_or_lease_field[authorization_change0-lease_change0]`
  and
  `tests/test_aidt_worktree_provisioner.py::test_every_individual_multi_file_partial_write_restart_is_recoverable[removing-attempt]`;
  both fail during `ProvisionerFixture.prepare_ready -> add_worktree -> _run_mutation -> _checked_output`. The
  independent 752-case run was `752 passed, 1 skipped in 633.53s`, so the failure is timing-sensitive but repeated
  and cannot be waived.
- [ ] Root-cause hypothesis to disprove: cumulative real-Git fixture/process/filesystem pressure makes an otherwise
  valid tiny worktree add cross the total 10-second wall deadline; the bounded runner then correctly kills it. A
  runner wait/reap defect remains possible. Compare the same exact argv under the runner and an evaluator-owned
  monotonic observer, recording only process-exit timing, worktree-registration appearance, path appearance, and
  reader/process liveness. If raw Git exits before 10 seconds while the runner reports timeout, fix runner wait/reap;
  if both exceed 10 seconds, find and close the fixture/resource lifecycle leak.
- [ ] Red-first acceptance: add a bounded repeat characterization that makes at least 20 exact real-fixture adds
  finish with safety margin below 10 seconds and leaves no child, reader thread, registration, or temp resource
  behind. Separately prove a controlled command that exits before the deadline is returned as success, while one
  still alive at the deadline is process-group killed, fully reaped, and returned as `timed_out=True`. This
  distinguishes legitimate bounded completion from a hang without changing the timeout or accepting partial Git
  state. Then require both exact failed nodes repeatedly green, the exact 752-case matrix green, and the full
  repository red only at the accepted CI-1 baseline.

### Independent green evidence retained

- [x] Focused fail-closed publication regression: `1 passed in 0.60s`.
- [x] Exact Core/workspace barrier: `41 passed in 1.09s`.
- [x] Frozen affected controls: `459 passed, 1 skipped, 23 deselected in 163.54s`.
- [x] Orchestrator compatibility: `326 passed in 17.81s`.
- [x] Full AIDT/worktree/recovery/workspace matrix: `752 passed, 1 skipped in 633.53s`.
- [ ] Full repository: `4 failed, 2189 passed, 6 skipped in 1366.88s`; one failure is the accepted CI-1 baseline,
  while the lifecycle identity mismatch and two repeated Git timeouts are new reds.
- [ ] Ruff, Pyright, AST/lazy import, all-untracked whitespace, doctor, Z marker, and literal commit gate were not
  rerun after repository parity failed. `GOAL.md`, `run-state.json`, and completion-marker state remain untouched.

## 2026-07-22 14:00 KST Lifecycle Fixture Repair Builder Evidence

Status: the lifecycle fixture mismatch is repaired with no product change. Fresh Exact Verify still owns the
criterion, verdict, GOAL ticks, run-state, completion marker, and commit gate.

- [x] RED reproduced on the existing lifecycle node: the card remained `Todo`, with
  `aidt_worktree_publication_failed category=profile_invalid`.
- [x] `_orch` now accepts a keyword-only `workflow_path` independently from `workspace_path`; only the real
  `_on_tick` lifecycle test passes `cfg.workflow_path`. Other lifecycle tests preserve the existing default.
- [x] Focused lifecycle E2E: `1 passed in 0.36s`; complete lifecycle file: `5 passed in 0.72s`.
- [x] Exact Core/workspace barrier: `41 passed in 1.43s`; orchestrator compatibility: `326 passed in 20.16s`.
- [x] Ruff and targeted whitespace checks passed. No product, timeout, runner, Git fixture, assertion, GOAL,
  run-state, Z marker, or verdict state was changed.

## 2026-07-22 14:22 KST Fixed-Deadline Git Diagnosis Builder Evidence

Status: no product runner or fixture leak was reproduced. The killed Git children remain real fail-closed timeouts,
but the repeat evidence localizes their recurrence to competing evaluator load rather than a wait/read/reap defect.
Do not change `GIT_LOCAL_TIMEOUT_SECONDS`, add a production global lock, or accept a killed partial worktree.

- [x] The three historical nodes passed on four consecutive ordinary repeats. The same nodes plus the three new
  boundary characterizations passed under one dedicated basetemp: `6 passed in 21.59s`.
- [x] An early-exit binary returned exact success in `0.07-0.13s`. A still-running process and its child were
  process-group killed at a `0.2s` deadline, the leader was reaped with return code `-9`, the child disappeared, and
  both reader threads terminated in `0.21s`.
- [x] Twenty exact real `git worktree add --no-track -b ...` operations, each followed by plain product removal,
  passed repeatedly. The characterization asserts the exact product argv prefix, each add below 80% of the frozen
  deadline, only the root worktree registered afterward, every ticket path absent, descriptor count not increased,
  no new multiprocessing child, and no live reader thread. Total cycle time was `8.41-8.83s` under isolated runs and
  `11.96-12.82s` under the shared pytest temp/session conditions.
- [x] Full binary-runner/Git-state file: `121 passed in 33.96s`. Exclusive real provisioner and persisted-recovery
  suites: `218 passed in 516.28s`; no Git timeout occurred even though the slowest whole test took `14.52s`.
- [x] Shared pytest session roots advanced while this builder launched no pytest and eight roots coexisted. The repo
  has no cross-process pytest serialization contract; its CI-parity quality gate invokes one serial full-suite
  process. This is bounded evidence of competing evaluator activity, not evidence for a production lock.
- [x] Ruff and targeted whitespace passed. Source, timeout, fixture behavior, assertions, lifecycle fixture, GOAL,
  run-state, Z marker, verdict, commit, merge, push, deploy, and live systems were unchanged.

Binary recommendation: retain the three focused tests, run the next exact verifier as the sole broad pytest process
with a dedicated basetemp, and mutate no product code. A new timeout under that exclusive command is the condition
that should reopen product/fixture diagnosis.

## 2026-07-22 15:09 KST Ask Matt Cross-Review - REVISE

The exclusive final matrices are green within the accepted `CI-1` baseline, but mandatory standards/spec review
found three Frontier 003 completion blockers. Preserve all exclusive evidence above; do not change the fixed Git
timeout, add a production global lock, simulate Git mutations, or weaken assertions.

### Criterion 1 / 8 - manager-stage failure is not an atomic publication failure

- [ ] Expected: any reload failure publishes nothing and preserves the exact current runtime generation,
  provisioner, manager, and captured ownership state; the tick returns before later work.
- [ ] Actual: `_publish_aidt_worktree_config()` calls `runtime.publish(cfg)` before `_update_workspace_manager(cfg)`.
  `AidtWorktreeRuntime.publish()` immediately installs `_generation` and `_provisioner`. If new-manager construction
  or root creation raises, Core rejects the tick but cannot restore the prior runtime generation.
- [ ] Evidence: `src/symphony/orchestrator/core.py:2238-2262`,
  `src/symphony/aidt_worktree/runtime.py:262-270,442-463`, PLAN amendments H.4 and 4.4, and mandatory review
  `/private/tmp/f003-ask-matt-spec-review.md`.
- [ ] Smallest next fix: add a red reload test with a valid changed publication and an injected manager-stage
  exception; assert exact prior runtime generation/provisioner and manager remain, admission stays closed, and no
  heartbeat/reconcile/fetch follows. Implement a real prepare/commit boundary or equivalent ordering that makes both
  publications atomic; do not add rollback-by-republishing or a success bypass.

### Criterion 9 - operator configuration is undocumented

- [ ] Expected: configuration-shape changes update a shipped example/operator reference and validation, per
  `CONTRIBUTING.md:46-48`.
- [ ] Actual: `jira_intake`, `aidt_routing`, and `aidt_worktree` validation exists, but no `WORKFLOW*.md`, README,
  operator reference, or runnable default-off example documents their exact safe shape and environment indirection.
- [ ] Evidence: mandatory standards review `/private/tmp/f003-ask-matt-standards-review.md`; repository search finds
  only internal plan/test-fixture prose.
- [ ] Smallest next fix: assign a separate bounded docs/config ticket that adds one default-off operator example for
  all three blocks, exact secret indirections, safety constraints, and config/doctor validation without activating
  Jira or AIDT repositories.

### Criterion 9 - untraced generated lockfile and oversized Build slice

- [ ] Expected: every commit file traces to the approved cohesive scope, and each Build ticket owns one bounded
  contract (rough guide: at most five files and 500 net lines).
- [ ] Actual: untracked `uv.lock` is 226,031 bytes, outside the Frontier 003 product/test/vault scope, and no
  dependency changed. Frontier 003 combines route attestation, config, manifest/Git/recovery/cleanup, runtime,
  workspace/Core integration, and health across about 15 product files and roughly 8,854 added product lines.
- [ ] Evidence: `PLAN.md` Cohesive File Scope, `skills/symphony-skill/SKILL.md` ticket-quality gate, `wc`/diff audit,
  and both mandatory Ask Matt reviews.
- [ ] Smallest next fix: exclude `uv.lock` from the Frontier diff. Before further Build work, split the atomicity and
  operator-doc corrections into independent, verifiable tickets/files; record the historical oversized slice as a
  process deviation rather than adding another omnibus iteration.

## 2026-07-22 Post-Ask-Matt Final QA - Docs-only Hygiene Handoff

Status: REVISE for branch-level merge readiness only. All current 001a/003 behavioral, full-suite, static,
structure, lazy-import, and example-doctor evidence is green within the accepted `CI-1.md` baseline, but the fixed
`origin/dev` comparison still reports three whitespace diagnostics in already committed Frontier 002 exploration
documents. Do not finalize PASS, GOAL ticks, run-state, Z markers, or literal gates until a fresh docs executor
removes only these spaces and a fresh verifier rechecks the fixed-base diff.

- [ ] `docs/changelog/2026-07/20-aidt-issue-resolver/exploration/routing-aidt-evidence.md:3` - remove the two trailing
  spaces after `Date: 2026-07-20`; preserve wording.
- [ ] `docs/changelog/2026-07/20-aidt-issue-resolver/exploration/routing-git-object-trust.md:3` - remove the two
  trailing spaces after `Date: 2026-07-20`; preserve wording.
- [ ] `docs/changelog/2026-07/20-aidt-issue-resolver/exploration/worktree-provisioning.md:7` - remove the two trailing
  spaces after `Date: 2026-07-20`; preserve wording.

Failing evaluator command:

```bash
git diff --check 0fe78e28b11398060ebda5f86a14f607c2e7177e
```

Required recheck after the docs-only edit:

```bash
git diff --check 0fe78e28b11398060ebda5f86a14f607c2e7177e
git diff --check
```

No product, test, example, timeout, assertion, GOAL, QA verdict, run-state, Wayfinder status, Z marker, commit,
merge, push, deploy, Jira, or live AIDT state may change in this handoff.

## 2026-07-22 16:37 KST Post-Ask-Matt Final QA Closure

Status: PASS within the accepted repository/doctor baseline. The docs-only hygiene handoff removed exactly the three
assigned trailing-space pairs; fresh fixed-base, tracked, and 87-file untracked gates are clean.

- [x] 001a exact returned-status membership and zero-write failure proof passed: 5 focused, 235 affected.
- [x] Atomic publication and final barrier passed: 3 focused, 42 expanded. Lifecycle/example validation passed 7.
- [x] Frozen affected matrix passed 459 with 1 skip/23 deselections; orchestrator matrix passed 326; complete AIDT/
  worktree/Git matrix passed 756 with 1 skip and no Git timeout.
- [x] Full repository retained exactly the accepted ledger: 2202 passed, 6 skipped, sole missing-`CI-1.md` failure.
- [x] Ruff, Pyright, executable AST/lazy checks, baseline-delta structure, example doctor, root doctor baseline, and
  fresh Ask Matt standards/spec reviews passed.
- [x] No unapproved live/system action occurred. The remaining six Ask Matt standards smells are advisory follow-up,
  not completion blockers.
- [x] Frontier 001 literal gate passed. Frontier 003's first mechanical attempt exposed only the stale string-form
  Finalize `forced_reflection`; after correcting verifier-owned state to schema-valid `null`, the exact same literal
  command passed every gate. No product/test/example file changed for this correction.
