# Builder D - routing runtime and orchestrator integration

## Outcome

Implemented the default-off AIDT routing composition root and the orchestrator dispatch barrier. One enabled poll now
scans the complete bounded Jira coordinator set, observes the immutable catalog once, resolves every coordinator,
and sends one resolution tuple to the storage adapter. Storage invokes a closure under all route-card locks that runs
the injected test hook and then rechecks the complete catalog before its final board/source preflight.

The real-world mapping is: Jira intake establishes the current work description, immutable Git objects establish the
candidate service bases, the pure resolver decides ownership, and the file adapter records the whole poll. The
orchestrator may fetch unrelated candidates only when that chain is globally valid; every route-managed coordinator,
desired child, and retained child remains blocked pending Frontier 003's fresh base equality.

## Decisions

- Disabled or absent routing returns before constructing a board, Git runner, identity probe, or catalog.
- `same_tick_jira` accepts only a successful enabled intake result; `static_snapshot` accepts only an explicit
  disabled result. Coupling failures stop before catalog observation.
- Coordinator files are canonical-order, top-level regular Jira-managed cards. Coordinator and managed-child counts
  are bounded before Git observation.
- Runtime catches every unexpected board/catalog/decision/storage defect and emits only `internal_error`; known
  `AidtRoutingFailure` categories and their contract-sanitized refs are preserved.
- Runtime success statuses are exactly `success` or `review`; all selected route projections continue to carry the
  decision layer's `pending_fresh_base_equality` status.
- Jira intake now returns a bounded `JiraIntakePoll` status to the next hook while preserving the existing Jira
  health vocabulary and default-off behavior.
- The AIDT hook runs after intake and before legacy normalization/candidate fetch. Global failure returns immediately;
  success filters blocked identifiers without sorting or mutating `Issue` objects.
- Enabled last-good workflow reload failures record only `workflow_reload_error`, notify observers, and stop the tick.
  Last-good disabled routing keeps the legacy reload path.
- Routing health starts disabled/null/zero. Success/review updates `last_success`; failure retains it and increments
  consecutive failures; disable clears current error/count/failure state without constructing readers.

Rejected alternatives:

- per-card catalog observation or storage application, which would break the whole-poll precommit boundary;
- catching routing failures in the outer tick loop, whose generic logging can expose exception strings;
- filtering through a set or re-sorting candidates, which would change legacy dispatch order;
- migrating the obsolete flat prototype's `HEAD`, status, index, or working-tree behavior.

## TDD evidence

Initial facade red:

```text
tests/test_aidt_routing_runtime.py -x
ImportError: cannot import name 'run_aidt_routing' from 'symphony.aidt_routing'
```

Full split-matrix red before product code:

```text
tests/test_aidt_routing_runtime.py -x
ModuleNotFoundError: No module named 'symphony.aidt_routing.runtime'
```

After the cohesive runtime increment, ten runtime/facade/coupling/sanitizer/filter cases passed. The first remaining
core red was exact:

```text
10 passed, then 1 failed
AttributeError: symphony.orchestrator.core has no attribute run_aidt_routing
```

The separate core increment made the runtime/core suite green. The contract owner's status-sanitizer follow-up then
froze `success`, so runtime changed its former prototype `ok` value and added an enabled-empty-poll regression.

```text
tests/test_aidt_routing_runtime.py
15 passed in 0.55s

tests/test_aidt_routing_contract.py
tests/test_aidt_routing_git_objects.py
tests/test_aidt_routing_decision.py
tests/test_aidt_routing_storage.py
tests/test_aidt_routing_runtime.py
98 passed in 39.38s
```

## Verification

```text
Plan-listed routing/Jira/tracker/health/service/web regressions:
327 passed in 36.59s

Broader orchestrator/service/web regressions:
375 passed in 22.11s

Ruff over the complete approved product/test scope:
All checks passed

Pyright over the complete approved product scope:
0 errors, 0 warnings, 0 informations

git diff --check:
passed

Owned runtime/test AST gate:
no function over 50 lines; no control-flow nesting over four
```

`symphony doctor ./WORKFLOW.md` passed server, shell, agent, prompt, hook, and viewer checks. It exited 1 only for the
known external categories `workspace.root not writable` (`Operation not permitted`) and
`tracker.board_root does not exist`. No activation or external directory mutation was performed.

The repository-wide pytest suite is intentionally left to the fresh Frontier verifier after the focused and affected
integration gates above.

## Handoff

Public facade exports now include the frozen contract names plus `filter_routing_candidates` and
`run_aidt_routing`. `run_aidt_routing` accepts optional intake result, board factory, binary Git runner, identity
probe, clock, precommit hook, and rename fault hook. Core consumes only the facade.

The untracked iteration-1 prototypes `src/symphony/aidt_routing.py` and `tests/test_aidt_routing.py` were deleted via
the approved migration patch after all five split suites passed. No network, live Jira/AIDT mutation, catalog
activation, commit, worktree provisioning, or Frontier 003 dispatch release was performed.
