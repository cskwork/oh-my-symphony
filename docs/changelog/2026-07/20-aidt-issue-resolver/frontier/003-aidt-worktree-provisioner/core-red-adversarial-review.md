# Frontier 003 CORE RED Adversarial Review

Verdict: **REQUEST CHANGES**.

The strengthened file collects the exact 18 frozen names, all 18 are intentionally RED at absent product seams,
the three-extension combined run has the expected `20 failed, 1 passed`, and the unchanged narrow baseline remains
green. Most attack-report gaps are now real behavioral checks. Four false-green/masking gaps still permit a broken
GREEN implementation, so Core implementation should remain blocked until the tests are tightened under the same 18
public names.

## Blocking findings

### 1. Complete `RunningEntry` state before `asyncio.create_task` is not proved

`test_dispatch_captures_manager_generation_pair_and_attempt_revision_before_task` replaces the worker and inspects
`orchestrator._running` only after the scheduled coroutine begins at
`tests/test_aidt_worktree_core_integration.py:767-788`. Neither that test nor the ordered worker helper at lines
`387-403` observes the instant `asyncio.create_task` is called. An implementation can create the task first, then
install/populate the entry synchronously before the event loop yields, and the current assertions still pass.

The observed tuple at lines `770-774` also omits three of the frozen entry fields:

- `aidt_guard` (expected `None` before create returns);
- `aidt_owned_failure` (expected `False`);
- `aidt_failure_category` (expected `None`).

The helpers at lines `324-347` and `406-412` dynamically attach AIDT attributes to an old `RunningEntry`. Those
assignments succeed even if `src/symphony/orchestrator/entries.py` never adds the required defaulted dataclass
fields, so the public legacy-constructor contract can also false-green.

Required repair: in the existing dispatch test, assert the nine fields exist with exact defaults on a normally
constructed legacy entry. Replace `core_module.asyncio.create_task` with a spy that, at invocation time, reads the
already-installed entry and records exact manager, generation, admission, `aidt_guard=None`, workflow generation,
pair digest, admitted revision, `aidt_owned_failure=False`, and `aidt_failure_category=None`; then delegate task
creation through the running loop. Assert that snapshot before allowing the worker to run. Keep the existing
half-pair zero-path/lease/task assertions.

### 2. The rebuilt-backend barrier is masked by the earlier turn assertion

`test_reload_between_turns_rechecks_the_captured_guard` executes the real initial worker, then immediately asserts
its first trace at `tests/test_aidt_worktree_core_integration.py:856-860`. The current intentional RED fails at line
858, so execution never reaches the `_rebuild_backend_for_phase` call at lines `862-879`. The focused failure output
confirms this exact stop. Consequently the attack-report requirement to enter the real rebuild method and place the
captured guard before the rebuilt backend is not independently executable.

Required repair: record the initial-turn outcome without asserting it yet, execute the rebuild scenario in an
independent fixture/context, record its outcome, and assert both only after both calls have run. The rebuild half
must still prove the captured manager is used after Core's current manager changes and that its guard precedes the
new backend start. No new public test name is needed.

### 3. Owned retry release is a fixture-only call

`test_timer_retry_owned_disposition_releases_generic_retry_without_repark` replaces
`_release_retry_ownership` with `events.append("release")` at
`tests/test_aidt_worktree_core_integration.py:939` and asserts only `['admit', 'release']` at lines `949-951`. It
does not seed or inspect `_claimed`, `_persisted_retry_attempts`, retry/debug flags, or pause ownership. A product
that calls the helper but leaves the generic claim/persisted retry behind—or clears state it must retain—passes.
This is not executable proof of the brief's release semantics.

Required repair: keep the existing OWNED_PRESERVED/OWNED_ERROR loop, but use the real
`_release_retry_ownership`. Seed the generic claimed/persisted/debug state and a pause sentinel, invoke the real
`_process_retry`, and assert the generic retry claim/bookkeeping is released exactly as specified while pause and a
runtime durable-state sentinel remain unchanged. Continue denying `_repark_retry`, `_schedule_retry`, `_dispatch`,
and tracker mutation.

### 4. `OWNED_ERROR` can incorrectly mark reconciliation cleanup complete

The worker-exit test covers both `OWNED_PRESERVED` and `OWNED_ERROR` and correctly proves captured-manager use plus
`authorization=None` at `tests/test_aidt_worktree_core_integration.py:1007-1031`, but it never asserts
`entry.workspace_cleanup_started`. The two reconciliation tests assert the flag remains false at lines `1069-1070`
and `1087-1088`, yet both use `_terminal_fixture`'s default `OWNED_PRESERVED` result. No reconciliation case combines
`OWNED_ERROR` with the cleanup-pending assertion.

An implementation can guard first and preserve for `OWNED_ERROR` while still setting
`workspace_cleanup_started=True`; every current assertion passes, but later authorized cleanup is suppressed by a
false completion flag.

Required repair: exercise both owned final dispositions inside the existing terminal reconciliation test. For the
`OWNED_ERROR` iteration, replace Core's current manager with a distinct manager, then assert the captured manager
alone receives the first terminal call, receives `authorization=None`, no generic mutation occurs, and
`workspace_cleanup_started` remains `False`.

## Accepted contract evidence

- Default process-runtime construction and the initial `start()` ordering are covered by
  `_start_with_default_runtime` at lines `549-589`; it requires `runtime:init -> publish -> manager`, exact runtime
  identity at manager handoff, retained generation identity, and lazy provisioner/Git-state imports. The separate
  fresh-process never-enabled extension passes.
- Failed publication retains the old manager/generation and returns before heartbeat/fetch. Owned admission is
  suppressed through real `_on_tick`; candidate tracing places admission before per-candidate slot, eligibility,
  conflict, path, and lease. The task-order portion is the narrower finding 1.
- Half-paired generation/admission arguments have zero path, lease, and task effects. Real `WorkspaceManager`
  HANDLED path/create/before-run suppression is present, and the real worker reaches captured create/before/turn
  barriers. Only the later rebuild half is masked as described in finding 2.
- Initial and retry unmanaged candidates both enter the real `_dispatch`; attempt-kind/order assertions preserve
  the existing generic flow. Fresh managed retry admission is proved. Only the release helper's state effect is
  fixture-only as described in finding 3.
- Workspace delegate tests independently require exact `AidtWorkspaceOperationError`, so the Core owned-failure
  fixture resolves to the typed error once that product seam exists. Worker-exit proves bounded category, no generic
  retry/tracker mutation, and both owned final dispositions.
- Worker-exit, reconcile terminal/inactive, and startup paths all probe ownership before generic mutation.
  Worker-exit proves `OWNED_ERROR`, captured-manager identity, and `authorization=None`; reconcile proves cleanup
  pending for `OWNED_PRESERVED`; startup passes the current published generation. Finding 4 is limited to their
  missing combined `OWNED_ERROR` cleanup assertion.
- Health serialization requires the exact bounded ten-field DTO and ignores hostile extra attributes. Core denies
  filesystem, subprocess/Git, registry, route, tracker, Jira, and backend entry points. The real-runtime test
  `tests/test_aidt_worktree_runtime.py::_assert_health_snapshot_is_memory_only` separately poisons the injected
  clock/provisioner/route/manifest/filesystem readers and proves two equal memory-only snapshots. Therefore the
  decorative fake-runtime attributes at Core-test lines `534-538` should be removed for clarity, but duplicating
  provider-internal no-I/O proof in the Core test is not blocking.

## Independent execution evidence

Exact collection:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q \
  tests/test_aidt_worktree_core_integration.py
18 tests collected in 0.62s
```

Focused CORE RED:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_core_integration.py
18 failed in 1.12s
```

Every failure maps to a missing product seam: default runtime construction/publication; manager publication
atomicity; provisionable filtering/admission; paired dispatch arguments and entry fields; workspace delegate
keywords; captured worker barriers; fresh retry admission/release; specialized owned failure; terminal ownership
guards; unmanaged captured manager; or bounded health.

Focused file plus the three frozen extensions:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_core_integration.py \
  tests/test_aidt_routing_runtime.py::test_core_releases_only_provisionable_managed_children_in_input_order \
  tests/test_aidt_routing_runtime.py::test_never_enabled_core_does_not_load_provisioner_or_git_state \
  tests/test_orchestrator_health.py::test_worktree_degraded_and_fatal_health_add_one_bounded_reason
20 failed, 1 passed in 1.83s
```

The sole pass is the required never-enabled lazy-import case.

Unchanged narrow baseline:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider -q \
  tests/test_aidt_routing_runtime.py tests/test_orchestrator_health.py \
  tests/test_orchestrator_dispatch.py::test_retry_timer_capacity_wait_preserves_attempt_kind_and_cap \
  tests/test_orchestrator_dispatch.py::test_retry_timer_releases_durable_rejection \
  tests/test_orchestrator_dispatch.py::test_on_worker_exit_commits_workspace_at_done \
  tests/test_orchestrator_dispatch.py::test_reconcile_terminate_terminal_commits_before_remove \
  tests/test_orchestrator_reconcile.py \
  -k 'not core_releases_only_provisionable_managed_children_in_input_order and not never_enabled_core_does_not_load_provisioner_or_git_state and not worktree_degraded_and_fatal_health_add_one_bounded_reason'
40 passed, 3 deselected in 2.45s
```

Static gates:

```text
rtk ../../.venv/bin/ruff check --no-cache \
  tests/test_aidt_worktree_core_integration.py \
  tests/test_aidt_routing_runtime.py tests/test_orchestrator_health.py
All checks passed!

rtk ../../.venv/bin/pyright tests/test_aidt_worktree_core_integration.py
0 errors, 0 warnings, 0 informations

AST scan of every helper/public test in the Core file
functions: 80
maximum lines: 46
maximum control nesting: 3
over_50: []
over_nesting_4: []

rtk git diff --check
exit 0, no output

rtk git diff --no-index --check /dev/null tests/test_aidt_worktree_core_integration.py
exit 1, no output (expected content-difference status for the untracked file)
```

No product or test file was edited by this review. No live AIDT checkout, Git mutation, network, Jira, backend,
completion authority, commit, merge, push, or deployment operation was used.

## Resolution append — 2026-07-22

Verdict: **THE FOUR BLOCKERS ARE CLOSED TEST-ONLY**. The original review above remains unchanged as the audit
history; this append records the bounded repair under the same frozen 18 public names.

1. The dispatch test now normally constructs a legacy `RunningEntry`, requires exact defaults for all nine frozen
   fields, and spies on `core_module.asyncio.create_task`. At invocation the spy reads the already-installed entry,
   records `(manager, generation, admission, None, workflow_generation, pair_digest, revision, False, None)`, and
   delegates through the running loop. The snapshot is asserted before the first `await`, while the half-pair
   zero-path/lease/task attack remains intact.
2. The initial-turn and `_rebuild_backend_for_phase` paths now use independent orchestrators, event lists, entries,
   captured managers, and current managers. Both calls capture bounded exceptions and complete before one aggregate
   comparison, so the initial RED cannot mask rebuild execution. The rebuild row requires
   `backend:stop -> guard -> backend` and captured/current guard counts `(1, 0)`.
3. Both owned retry dispositions now run the real `_release_retry_ownership`. The fixture seeds the claim, persisted
   retry attempt, historical debug fields, in-memory pause ownership, a persistent retry/pause registry record, and
   an immutable runtime durable sentinel. The expected result releases claim/retry state and only the persistent
   retry flag, preserves both pause layers, debug history, and runtime state, and reaches no repark, dispatch,
   scheduled retry, tracker state, or tracker note mutation.
4. The terminal reconcile test now aggregates `OWNED_PRESERVED` and `OWNED_ERROR`. The error case replaces Core's
   current manager with a distinct manager; both rows require exactly one captured-manager guard with
   `authorization=None`, no current-manager call, no generic mutation, and `workspace_cleanup_started=False`.

Fresh evidence after repair:

```text
PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider --collect-only -q \
  tests/test_aidt_worktree_core_integration.py
18 tests collected in 0.76s

PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_core_integration.py
18 failed in 1.18s

PYTHONPATH=src rtk ../../.venv/bin/pytest -p no:cacheprovider -q --tb=short \
  tests/test_aidt_worktree_core_integration.py \
  tests/test_aidt_routing_runtime.py::test_core_releases_only_provisionable_managed_children_in_input_order \
  tests/test_aidt_routing_runtime.py::test_never_enabled_core_does_not_load_provisioner_or_git_state \
  tests/test_orchestrator_health.py::test_worktree_degraded_and_fatal_health_add_one_bounded_reason
20 failed, 1 passed in 1.53s

Unchanged narrow baseline: 40 passed, 3 deselected in 1.77s
Ruff: All checks passed
Pyright: 0 errors, 0 warnings, 0 informations
AST: functions 88; maximum lines 50; maximum control nesting 3; no violations
Whitespace: tracked diff clean; untracked test no-index check produced no diagnostic
```

No product source, production authority, frozen public name, external-operation denial, network/Jira/backend
boundary, live AIDT checkout, or repository state was expanded by this resolution.

## Final adversarial verdict — 2026-07-22

Verdict: **REQUEST CHANGES**.

The four repaired false-greens are closed: the dispatch spy samples all nine owned fields at the actual
`asyncio.create_task` call and separately checks legacy defaults; initial-turn and rebuild barriers execute in
independent fixtures before their aggregate assertion; both owned retry dispositions exercise the real release
helper and preserve pause/debug/runtime sentinels; and terminal reconcile aggregates `OWNED_PRESERVED` plus
`OWNED_ERROR`, captured/current-manager calls, `authorization=None`, and cleanup-pending state. Original attack
items 1–7 also have real Core/manager/dispatch entry points rather than helper-only assertions. Three blockers
remain.

1. **A successful reload can retain a stale Core generation and still pass.** `_FakeRuntime.publish` mutates
   `self.generation.config` and returns the same object at
   `tests/test_aidt_worktree_core_integration.py:138-143`. The root-replacement test then asserts publication,
   manager replacement, and process-runtime identity at lines 742-747, but never asserts that Core installed a
   distinct generation returned by the successful reload or that the old captured generation stayed immutable.
   A Core implementation that calls `publish` and rebuilds the manager but forgets the atomic current-generation
   assignment therefore passes because the stale object was mutated in place. Repair under the existing public
   name by queueing two distinct immutable generation objects, asserting the replacement is Core's current
   generation, and asserting the initial generation remains unchanged/captured.
2. **The Core health `clock` and `network` sentinels are decorative.** Lines 635-639 only attach attributes named
   `clock` and `network` to the fake runtime. The actual denial patches at lines 640-647 cover filesystem,
   subprocess/Git, routing, tracker, registry, Jira, and backend calls, but no clock or network callable. Thus an
   incorrect Core health serializer can perform an extra network or clock operation without touching either fake
   attribute and remain green. The real-runtime memory-only test protects provider internals, not Core composition.
   Replace these labels with executable denials at the real Core boundary (or remove the unsupported claims).
3. **Reconcile cleanup-pending is not exercised through the worker-cancellation branch.** `_owned_entry` constructs
   `RunningEntry(..., worker_task=None, ...)`, and the repaired reconcile loop at lines 1178-1196 never installs a
   cancellable task. An incorrect reconcile implementation that calls the captured guard correctly but sets
   `workspace_cleanup_started=True` only when it cancels a live worker passes both owned rows, while real dispatched
   entries do have a worker task and would suppress later authorized cleanup. Install a cancellable task spy for
   both dispositions and assert cancellation is allowed while cleanup remains pending, the captured manager alone
   receives `authorization=None`, and generic mutation remains absent.

Fresh execution evidence:

```text
Focused collection: 18 tests collected in 0.60s
Focused CORE RED: 18 failed in 1.01s
Focused plus three extensions: 20 failed, 1 passed in 1.75s
Unchanged narrow baseline: 40 passed, 3 deselected in 2.33s
Ruff: All checks passed
Pyright: 0 errors, 0 warnings, 0 informations
AST: 88 functions; maximum 50 lines; maximum control nesting 3; no violations
Whitespace: tracked diff clean; all three untracked no-index checks emitted no diagnostic
```

The RED failures remain bounded to missing product seams, but implementation should stay blocked until the three
false-green paths above are executable contract. No product or test file was edited, and no commit was created.

## Final adversarial resolution append — 2026-07-22

Verdict: **THE THREE FINAL BLOCKERS ARE CLOSED TEST-ONLY**. The final adversarial verdict above remains unchanged as
audit history; this append records the bounded repair under the same frozen 18 public names.

1. `_FakeGeneration` is now frozen. `_FakeRuntime.publish` never mutates a prior generation and can queue the exact
   distinct generation returned by a changed-config reload. The root-replacement case requires the initial manager
   handoff to capture the initial object, the replacement handoff and Core current-generation field to capture the
   exact replacement object, runtime current generation to match it, and both generations to retain their original
   config identities.
2. Health no longer installs decorative callable labels. `_HealthRuntime.__getattr__` raises on every forbidden
   provider attribute, including `clock` and `network`, while the scoped Core call also denies `socket.socket`,
   `socket.create_connection`, and `urllib.request.urlopen` plus the existing filesystem, subprocess/Git, routing,
   tracker, registry, Jira, and backend boundaries. Core's existing `datetime.now(timezone.utc)` call is deliberately
   not patched because its response timestamp is legitimate. The real-runtime memory-only test remains the proof for
   provider-internal clock/provisioner/route/manifest/filesystem behavior; this Core test proves composition does not
   dereference those provider internals or construct network I/O.
3. Both `OWNED_PRESERVED` and `OWNED_ERROR` reconciliation rows now install a cancellable worker-task spy. The exact
   expected trace is `terminal_guard -> worker:cancel`: cancellation occurs, but only after the captured ownership
   guard; `workspace_cleanup_started` remains false; the captured manager records `authorization=None`; the distinct
   current manager is untouched; and commit, merge, hook, raw remove, retry, and tracker mutation remain absent.

Fresh evidence after the final repair:

```text
Focused collection: 18 tests collected in 0.54s
Focused CORE RED: 18 failed in 0.82s
Focused plus three extensions: 20 failed, 1 passed in 1.47s
Unchanged narrow baseline: 40 passed, 3 deselected in 1.52s
Ruff: All checks passed
Pyright: 0 errors, 0 warnings, 0 informations
AST: 92 functions; maximum 50 lines; maximum control nesting 3; no violations
Whitespace: tracked diff and all three untracked no-index checks emitted no diagnostic
```

The final RED remains intentional and bounded to absent product integration. No product source, production authority,
frozen public name, legitimate Core timestamp, real-runtime provider proof, or external-operation boundary was
weakened. No live AIDT checkout or product-side Git/network/Jira/backend operation, commit, merge, push, or deployment
ran, and no commit was created.

## Final binary closure audit — 2026-07-22

Verdict: **REQUEST CHANGES**.

All documented false-green blockers are now executable: distinct frozen reload generations, the nine pre-task
entry fields, independently executed initial/rebuild barriers, real retry-ownership release for both owned
dispositions, live-worker cancellation for both reconcile dispositions with cleanup still pending, and Core health
composition guarded by executable provider/network/filesystem/process denials. The binary gate still fails because
Ruff reports `E731` at `tests/test_aidt_worktree_core_integration.py:580-581`; replace the two assigned lambdas with
named functions without changing behavior.

Fresh evidence: focused Core RED `18 failed`; combined extensions `20 failed, 1 passed`; unchanged baseline
`40 passed, 3 deselected`; Pyright `0 errors`; AST `93 functions`, maximum `50` lines and nesting `3`, no violations;
`git diff --check` clean; Ruff `2 errors`. No product or test file was edited, and no commit was created.

## Final binary closure resolution — 2026-07-22

Verdict: **ACCEPT**. The two assigned callback-factory lambdas are now small named local functions. Their behavior is
unchanged: synchronous callbacks append the same generic trace, while asynchronous callbacks append at invocation
and return the same `_async_value(...)` awaitable. No product code or other test semantics changed.

Fresh evidence: focused Core remains intentionally RED with `18 failed in 1.59s`; Ruff reports
`All checks passed!`; Pyright reports `0 errors, 0 warnings, 0 informations`; AST reports `97 functions`, maximum
`50` lines and control nesting `3`, with no violations; and `git diff --check` exits `0` with no output. No commit was
created.

Final binary closure recheck — 2026-07-22: **APPROVE**. The two named callback factories are behavior-equivalent
to the removed lambdas, surrounding targets/assertions are unchanged, Ruff passes, and focused Core remains the
same intentional RED contract with `18 failed in 1.18s`.

## Fixture contradiction correction — 2026-07-22

Verdict: **ACCEPT THE TWO TEST-ONLY CORRECTIONS**. Independent audit showed that the admission fixture constructed
two equal but distinct objects while `_FakeManager` correctly required identity, and that the unmanaged failed-turn
trace omitted the legacy `generic:after_run` call between backend stop and terminal guarding. The candidate fixture
now installs `DelegateResult.handled(admission)` on the runtime, and the unmanaged trace includes the missing event
at the audited position. Identity was not weakened, no other assertion changed, and no product source was edited.

Fresh evidence: the focused pair moved from `2 failed in 0.58s` to `2 passed in 0.56s`; the complete 18-test Core
file plus three frozen extensions passed `21 passed in 1.38s`; and the Workspace delegate/keyworded matrix passed
`20 passed, 41 deselected in 0.31s`. The recorded affected-control command is not fully green in the current
worktree: `32 failed, 427 passed, 1 skipped, 23 deselected in 88.25s`, with failures outside the corrected pair in
orchestrator-dispatch compatibility paths. Ruff passes, Pyright reports zero diagnostics, and AST reports 97
functions with maximum 50 lines and control nesting 3. The tracked diff and all three untracked no-index whitespace
checks emitted no diagnostic. No commit was created.
