# Frontier 002 iteration 3 verifier report

## Verdict

FAIL.

The import-cycle correction is sound and all five isolated routing suites pass, but the public routing-result
contract is not closed. `AidtRoutingResult` accepts hostile non-boolean/non-count values and malformed blocked card
identifiers. Its repr, the structured routing failure log, and orchestrator health then expose those values verbatim.
This violates PLAN Iteration 2 Binding Amendment 8 and the GOAL privacy criterion. Broad affected/full parity cannot
override a public-output leak and was intentionally stopped at this blocking semantic gate.

No product/test code, GOAL, QA, run-state, R-LOOP, or finalization artifact was changed by this verifier.

## Scope and base audit

- Base: `HEAD == f7b05851d7143d6ab5a58050380ff4b1e65ddde6` (`f7b0585`).
- Tracked product/test changes are limited to five approved paths:
  `jira_intake.py`, `orchestrator/core.py`, `trackers/file.py`, `trackers/jira.py`, and
  `tests/test_jira_intake.py`.
- Untracked product/test changes are limited to the other twelve approved paths: the five-file
  `aidt_routing` package, `trackers/aidt_routes.py`, shared routing test support, and the five split routing suites.
- All other untracked paths are Frontier 002 run-vault documentation/exploration artifacts. No out-of-scope
  product/test path was found.
- The flat iteration-1 prototypes are absent.
- The public facade allowlist is the exact approved 11 names; storage/package/runtime/core import order is lazy and
  identity-preserving.

Inventory commands:

```bash
rtk git status --short
rtk git rev-parse HEAD
rtk git diff --name-status f7b0585
rtk git ls-files --others --exclude-standard
```

## Fresh import and focused evidence

Fresh-process import commands all exited 0:

```bash
rtk env PYTHONPATH=src ../../.venv/bin/python -c 'import sys; import symphony.trackers.aidt_routes; assert "symphony.aidt_routing.runtime" not in sys.modules; from symphony.aidt_routing import filter_routing_candidates, run_aidt_routing; from symphony.aidt_routing.runtime import filter_routing_candidates as rf, run_aidt_routing as rr; assert filter_routing_candidates is rf and run_aidt_routing is rr; print("storage-first: ok")'
rtk env PYTHONPATH=src ../../.venv/bin/python -c 'import sys; import symphony.aidt_routing as package; assert "symphony.aidt_routing.runtime" not in sys.modules; assert len(package.__all__) == 11; from symphony.aidt_routing import run_aidt_routing; from symphony.aidt_routing.runtime import run_aidt_routing as runtime_run; assert run_aidt_routing is runtime_run; print("package-first: ok")'
rtk env PYTHONPATH=src ../../.venv/bin/python -c 'from symphony.aidt_routing import filter_routing_candidates, run_aidt_routing; from symphony.aidt_routing.runtime import filter_routing_candidates as rf, run_aidt_routing as rr; assert filter_routing_candidates is rf and run_aidt_routing is rr; print("public-runtime-first: ok")'
rtk env PYTHONPATH=src ../../.venv/bin/python -c 'import symphony.orchestrator.core as core; import symphony.aidt_routing as package; assert core.run_aidt_routing is package.run_aidt_routing; assert core.filter_routing_candidates is package.filter_routing_candidates; print("core-public-facade: ok")'
```

Exact results: `storage-first: ok`, `package-first: ok`, `public-runtime-first: ok`, and
`core-public-facade: ok`.

Each split suite was collected and executed in its own process:

```bash
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_git_objects.py
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_decision.py
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_storage.py
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_runtime.py
```

Exact results: contract 25 passed; Git objects 39 passed; decision 8 passed; storage 11 passed; runtime 18 passed;
101 passed total. The isolated storage/runtime collections and the former storage-first cycle are clean.

## Empty and all-disabled catalog audit

PASS under the exact PLAN clauses. The enabled schema requires a bounded `services` list but does not require a
non-empty or enabled member; disabled services remain known and cannot score. Fresh direct probes established:

- enabled empty catalog plus empty board: status `success`, dispatch allowed, zero blocked IDs, zero Git-runner
  calls;
- enabled all-disabled catalog plus one valid Jira coordinator: status `review`, dispatch allowed only for unrelated
  cards, coordinator `A20-1188` blocked, `review_count == 1`, zero Git-runner calls.

Thus an empty catalog is a no-work success and an all-disabled catalog deterministically sends managed coordinators
to review without constructing a checkout/revision observation. Neither case guesses a service.

## Blocking defect: injected public result values leak

The fresh reproduction constructed one `AidtRoutingResult` with a hostile string count, negative count, mapping
payload, and traversal-shaped blocked identifier, then applied it to orchestrator health. It exited 0 because all
four forbidden observations were present:

```text
structured log: routed_count=TOP-SECRET-/private/count review_count=-7 child_count={"payload": "SECRET"}
repr: routed_count='TOP-SECRET-/private/count', review_count=-7, child_count={'payload': 'SECRET'}
blocked_identifiers: frozenset({'../../SECRET-CARD'})
health: routed_count='TOP-SECRET-/private/count', child_count={'payload': 'SECRET'}
```

The constructor currently allowlists only `status`, `error_category`, and `error_ref`. It does not require exact
booleans for `enabled`/`global_allow_dispatch`, canonical coordinator/child IDs in `blocked_identifiers`, or exact
non-negative bounded integers for the four counts. `_apply_aidt_routing_result` and `_log_aidt_routing_failure` then
trust those fields. This is a direct failure of the frozen output contract: repr, health, and structured logs may
contain paths, payload text, and arbitrary injected objects instead of only statuses/categories/allowed refs/counts.

## Smallest correction

Keep the facade and runtime/storage seams unchanged. Close `AidtRoutingResult.__post_init__` as the single public
normalization boundary:

1. require exact booleans for `enabled` and `global_allow_dispatch`;
2. require a real `frozenset` containing only canonical Jira coordinator IDs or canonical deterministic route-child
   IDs;
3. require exact non-boolean, non-negative integers within the coordinator/child result bounds for all counts;
4. require `error_category`/`error_ref` to be strings or null before allowlist lookup;
5. if any public field is malformed, replace the whole value with a sanitized fail-closed `internal_error` result
   (`enabled=True`, dispatch false, empty blocked set, zero route/review/child counts, failure count one), so invalid
   fields cannot survive into repr, health, or logs.

Add one regression that sends hostile values through result repr, `_apply_aidt_routing_result`, captured structured
logs, and health, plus valid-boundary cases proving normal routing results remain unchanged.

## Required trusted rerun after correction

First rerun the exact injected-result regression and all four fresh import permutations. Then rerun each of the five
split suites independently. If green, run the complete remaining PLAN matrix:

```bash
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py tests/test_aidt_routing_decision.py tests/test_aidt_routing_storage.py tests/test_aidt_routing_runtime.py tests/test_jira_intake.py tests/test_tracker_jira.py tests/test_tracker_jira_edges.py tests/test_tracker_file.py tests/test_orchestrator_health.py tests/test_service.py tests/test_webapi.py
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py tests/test_tracker_jira.py tests/test_tracker_jira_edges.py tests/test_tracker_file.py tests/test_orchestrator_health.py tests/test_orchestrator.py tests/test_orchestrator_*.py tests/test_service.py tests/test_service_*.py tests/test_webapi.py tests/test_webapi_*.py
rtk env PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider
rtk ../../.venv/bin/ruff check --no-cache src/symphony/aidt_routing src/symphony/trackers/aidt_routes.py src/symphony/jira_intake.py src/symphony/trackers/jira.py src/symphony/trackers/file.py src/symphony/orchestrator/core.py tests/aidt_routing_support.py tests/test_aidt_routing_contract.py tests/test_aidt_routing_git_objects.py tests/test_aidt_routing_decision.py tests/test_aidt_routing_storage.py tests/test_aidt_routing_runtime.py tests/test_jira_intake.py
rtk ../../.venv/bin/pyright --pythonpath ../../.venv/bin/python src/symphony/aidt_routing src/symphony/trackers/aidt_routes.py src/symphony/jira_intake.py src/symphony/trackers/jira.py src/symphony/trackers/file.py src/symphony/orchestrator/core.py
rtk git diff --check
rtk ../../.venv/bin/symphony doctor ./WORKFLOW.md
```

Also rerun the AST gate for function length `<=50` and control-flow nesting `<=4`. Repository-wide parity may retain
only the accepted pre-change failure
`tests/test_continuous_improvement.py::test_run_continuous_improvement_real_git_target_worktree_e2e` for missing
`kanban/CI-1.md`; doctor may retain only the known external workspace-root permission and absent board-root
categories.

## Gates intentionally not run after the blocker

The affected matrix, preserved Frontier 001 suites, Ruff, Pyright, AST, diff check, repository-wide pytest parity,
and doctor were not promoted as iteration-3 evidence after the public-output contract failed. They must be rerun by
a fresh verifier after the correction; an otherwise-green broad suite cannot waive this defect.
