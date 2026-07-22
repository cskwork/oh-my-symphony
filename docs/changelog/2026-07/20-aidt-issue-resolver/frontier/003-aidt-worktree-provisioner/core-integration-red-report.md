# Frontier 003 Core Integration RED Report

Date: 2026-07-22
Scope: test-only AIDT runtime integration through routing, Core, worker, retry, terminal, and health boundaries
Verdict: **INTENTIONAL RED / CORE INTEGRATION REQUIRED**

## Decision and theory

The tests freeze the process-lifetime ownership model from the integration brief. Core must publish one immutable
runtime generation before manager handoff, release only nominated managed children, admit before slots or leases,
and capture the exact manager/generation/admission before task creation. Workers must use that captured manager for
create and every pre-backend guard. Timer retries must re-admit against the current generation. Worker-exit,
reconcile, inactive, and startup terminal paths must consult the captured ownership guard before generic mutation.
Production supplies no completion authority, so every AIDT terminal path preserves the workspace. Health copies one
bounded in-memory snapshot and adds one bounded degraded reason.

The fixtures use one ordered event trace, fake runtime/manager/backend delegates, frozen configuration and public
AIDT DTOs, and temporary paths. All commit, merge, hook, tracker, and terminal operations are replaced by ordered
sentinels before Core is called. No live AIDT checkout, Git command, repository, network, Jira, backend process,
completion authority, commit, merge, push, or deployment is used.

The attack and both adversarial review rounds are now executable contract, not prose. The frozen names additionally prove
default runtime construction and first `start()` publication, process runtime identity at initial and replacement
manager handoff, owned suppression through `_on_tick`, admission before per-candidate slot/eligibility/conflict
checks, real `WorkspaceManager` HANDLED path/create/before-run behavior, half-paired dispatch rejection before
path/lease/task, captured attestation inside `_rebuild_backend_for_phase`, and unmanaged initial/retry entry
installation through the real `_dispatch`. The dispatch test now snapshots the full nine-field owned entry at the
actual `asyncio.create_task` call and independently freezes the legacy-constructor defaults. Initial-turn and rebuild
guard traces run in separate Core fixtures and are asserted together. Owned retry dispositions use the real release
helper against claimed, persisted, debug, pause, registry, and runtime-durable sentinels. Reconcile aggregates both
`OWNED_PRESERVED` and `OWNED_ERROR` outcomes through cancellable worker spies, including guard-first cancellation,
cleanup-pending, captured-manager, and `authorization=None` evidence. Successful changed-config reload now queues a
distinct frozen generation and requires Core plus the replacement-manager handoff to capture that exact object while
the old generation remains unchanged. Health denies forbidden fake-runtime attribute access and actual stdlib
network construction at the Core composition boundary while leaving Core's legitimate wall-clock timestamp live.

## Installed coverage

`tests/test_aidt_worktree_core_integration.py` contains exactly the 18 frozen public test names. It exercises real
`Orchestrator._on_tick`, `_dispatch`, `_run_agent_attempt`, `_process_retry`, `_on_worker_exit_impl`,
`_reconcile_one`, `_startup_terminal_cleanup`, and `health` boundaries. The shared trace freezes:

```text
filter -> admit -> slot -> eligibility -> conflict -> path -> lease -> entry -> create -> guard -> backend
```

The routing and health suites add exactly the three frozen extension names:

- `test_core_releases_only_provisionable_managed_children_in_input_order`
- `test_never_enabled_core_does_not_load_provisioner_or_git_state`
- `test_worktree_degraded_and_fatal_health_add_one_bounded_reason`

Coverage includes runtime identity across root replacement, failed publication atomicity, final owned candidate
dispositions, captured manager/generation/revision, reload barriers before startup and later turns, retry kind and
durable ownership, specialized failure retry suppression, all existing terminal entry points, deny-all production
authority, exact unmanaged fallback order, bounded health fields, hostile-value absence, and lazy imports.

All seven adversarial blockers are closed without adding public cases. The first four closures require task construction to observe all nine frozen
fields before the event loop can run the worker; both guard-barrier probes complete before one aggregate assertion;
both owned retry results independently expose the real release state transition; and both terminal reconcile
results retain `workspace_cleanup_started=False`, with `OWNED_ERROR` proving Core's current manager is untouched.
The final three closures require a distinct immutable generation on successful reload, executable fake-runtime and
network denials around `health()`, and the same owned terminal assertions after a worker cancellation has occurred.

## Exact RED evidence

Clean collection preserves exactly the frozen 18 public names:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q \
  tests/test_aidt_worktree_core_integration.py
18 tests collected in 0.54s
```

Hardened Core file alone:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_core_integration.py
18 failed in 0.82s
```

Focused 18 plus the three frozen extensions:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_core_integration.py \
  tests/test_aidt_routing_runtime.py::test_core_releases_only_provisionable_managed_children_in_input_order \
  tests/test_aidt_routing_runtime.py::test_never_enabled_core_does_not_load_provisioner_or_git_state \
  tests/test_orchestrator_health.py::test_worktree_degraded_and_fatal_health_add_one_bounded_reason
20 failed, 1 passed in 1.47s
```

The one pass is the required never-enabled lazy-import compatibility case. The 20 failures are bounded and map to
the missing integration, not fixture I/O:

- default construction never calls the runtime factory, and `start()` hands off a manager without first publishing;
- Core omits the provisionable set and `_admit_aidt_candidate`;
- owned results never reach `_on_tick` admission, while the legitimate post-loop slot check remains isolated;
- `_dispatch` rejects the frozen generation/admission keywords as unexpected `TypeError` and has no half-pair guard;
- real `WorkspaceManager` rejects the runtime/generation/admission/guard delegate seam;
- workers use the mutable generic manager and generic create/guard/after-run paths;
- phase backend rebuild does not call the captured guard;
- unmanaged initial/retry dispatch entries do not capture their manager;
- retries bypass fresh runtime admission and owned release;
- specialized failures schedule the generic retry;
- `OWNED_PRESERVED` and `OWNED_ERROR` worker-exit reach commit/retry/tracker deny sentinels rather than stopping;
- worker-exit, reconcile, inactive, and startup paths reach generic commit/merge/hook/remove sentinels;
- production terminal probes are absent, so no `authorization=None` observation exists;
- `aidt_worktree` health and degraded reason are absent; no health I/O deny sentinel fired before that bounded failure.

No hardened focused run emitted a Git command, network/Jira response, backend process, or external-operation log.

## Compatibility and static evidence

Narrow unchanged baselines:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_routing_runtime.py tests/test_orchestrator_health.py \
  tests/test_orchestrator_dispatch.py::test_retry_timer_capacity_wait_preserves_attempt_kind_and_cap \
  tests/test_orchestrator_dispatch.py::test_retry_timer_releases_durable_rejection \
  tests/test_orchestrator_dispatch.py::test_on_worker_exit_commits_workspace_at_done \
  tests/test_orchestrator_dispatch.py::test_reconcile_terminate_terminal_commits_before_remove \
  tests/test_orchestrator_reconcile.py \
  -k 'not core_releases_only_provisionable_managed_children_in_input_order and not never_enabled_core_does_not_load_provisioner_or_git_state and not worktree_degraded_and_fatal_health_add_one_bounded_reason'
40 passed, 3 deselected in 1.52s
```

No workspace baseline is accepted as zero-external evidence in this report. An exploratory full workspace run
returned `16 failed, 40 passed, 1 skipped in 26.75s`; the 16 failures are the already-frozen delegate RED cases. A
follow-up excluding those RED names returned `40 passed, 1 skipped, 16 deselected in 28.89s`. Inspection immediately
afterward found that the legacy file includes local temporary-repository Git integration tests. The runs touched only
pytest temporary directories and no live AIDT checkout or remote, but both are excluded because this task required
zero Git attempts. No further workspace suite run was made. The hardened Core file itself installs Git deny sentinels
and made zero Git, network, Jira, or backend process attempts.

Static gates:

```text
rtk ../../.venv/bin/ruff check --no-cache \
  tests/test_aidt_worktree_core_integration.py \
  tests/test_aidt_routing_runtime.py tests/test_orchestrator_health.py
All checks passed!

rtk ../../.venv/bin/pyright tests/test_aidt_worktree_core_integration.py
0 errors, 0 warnings, 0 informations
```

AST scan of every helper and public test in the new file:

```text
functions: 92
maximum lines: 50
maximum control nesting: 3
over_50: []
over_nesting_4: []
```

Whitespace gates:

```text
rtk git diff --check
exit 0, no output

rtk git diff --no-index --check /dev/null tests/test_aidt_worktree_core_integration.py
exit 1, no output (expected content-difference status)

rtk git diff --no-index --check /dev/null <each changed RED report>
exit 1 for each, no output (expected content-difference status)
```

Ruff, Pyright, AST, tracked-diff whitespace, and all three untracked-file whitespace gates are clean.

## Scope

Changed paths are limited to:

- `tests/test_aidt_worktree_core_integration.py`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/core-integration-red-report.md`
- `docs/changelog/2026-07/20-aidt-issue-resolver/frontier/003-aidt-worktree-provisioner/core-red-adversarial-review.md`

No product source or production authority was changed.

## Fixture contradiction correction — 2026-07-22

The two independently audited contradictions were corrected in the existing Core fixture only. The candidate test
now binds `_FakeRuntime.admission_result` to `DelegateResult.handled(admission)`, so Core must forward the exact
manager-asserted issued object and the identity assertion remains unchanged. The unmanaged worker trace now includes
`generic:after_run` after backend stop and before the terminal ownership guard, preserving the legacy unmanaged
order without changing guarded AIDT behavior. No product file or other assertion changed.

Fresh evidence:

```text
Pre-correction focused reproduction: 2 failed in 0.58s
Post-correction focused pair: 2 passed in 0.56s
Full Core 18 plus three frozen extensions: 21 passed in 1.38s
Workspace delegate/keyworded matrix: 20 passed, 41 deselected in 0.31s
Affected control command: 32 failed, 427 passed, 1 skipped, 23 deselected in 88.25s
Ruff: All checks passed!
Pyright: 0 errors, 0 warnings, 0 informations
AST: 97 functions; maximum 50 lines; maximum control nesting 3; no violations
Whitespace: tracked diff clean; all three untracked no-index checks emitted no diagnostic
```

The 32 affected-control failures are outside the corrected pair and remain visible rather than being masked or
repaired through product changes; the reported failures are in existing orchestrator-dispatch compatibility paths,
principally reload publication rejection and legacy workspace fixtures without `aidt_guard`. This bounded fixture
correction did not commit or alter product code.
