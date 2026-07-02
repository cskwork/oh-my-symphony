# Delivery Proof

## Eval Intent

- Goal: implement the first "Crash-safe orchestrator state and leases" slice from the pasted reliability plan.
- Constraints: keep Symphony's current file-backed Kanban workflow; avoid new operator config unless needed; do not change worker prompts, tracker state names, or web UI behavior.
- Tradeoffs: a single-node persisted lease prevents duplicate dispatch across process restart; it does not yet reattach live in-process asyncio workers after a hard crash.
- Rejected approaches: multi-node HA/event sourcing first; tracker-level write locks in this slice; JSONL lease state instead of SQLite WAL.

## Before State

- Mode: LEGACY
- Proof: the code graph shows `_running`, `_claimed`, and `_retry` are process memory owned by `Orchestrator.__init__`; `_dispatch` installs `_running`; `_on_worker_exit_impl` pops it; `_reconcile_running` can only see in-memory entries.
- Command or artifact: code graph snippets for `Orchestrator.__init__`, `_dispatch`, `_on_worker_exit_impl`, and `_reconcile_running`; red tests in `tests/test_run_registry.py` and `tests/test_orchestrator_dispatch.py`.
- What this proves: dispatch claims do not currently survive a process restart.
- What this does not prove: real OS process reattachment, tracker write serialization, or multi-node coordination.

## After Target

- Expected behavior: Symphony creates `.symphony/state.db` with WAL, persists one active run lease per issue before worker task start, heartbeats live runs, blocks dispatch while an unexpired lease exists, expires stale leases deterministically, and marks leases terminal on worker exit/force-eject.
- Compatibility to preserve: existing in-memory `_running`, `_retry`, pause, stall, token-budget, and tracker behavior; no required workflow config migration.
- Intentional drift: `.symphony/state.db` becomes a new local runtime artifact.

## Command Manifest

| Name | Command | Source | Proves | Used when |
|---|---|---|---|---|
| registry-red | `PYTHONPATH=src pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'run_registry or persisted_lease or worker_exit_releases_persisted_lease'` | evaluator_owned | New tests fail before implementation and pass after | before |
| focused-lease | `.venv/bin/python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'run_registry or persisted_lease or worker_exit_releases_persisted_lease'` | evaluator_owned | Registry and lease-boundary tests | after |
| focused-dispatch | `.venv/bin/python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py` | frozen_repo | Registry plus dispatch regressions | after |
| workflow-doctor | `.venv/bin/symphony doctor ./WORKFLOW.md` | frozen_repo | Repo-required workflow sanity check | after |
| full-tests | `.venv/bin/python -m pytest -q` | frozen_repo | Broad Python regression check | after |

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | no-op | resolved | Reattaching live workers after restart is requested in the broader plan but current workers are in-process asyncio tasks. | Persist enough metadata for future work; first slice expires stale leases instead of claiming reattach. | Changelog follow-up |
| d2 | no-op | resolved | Board write locking is part of the pasted risk map but not required to make dispatch claims durable. | Keep first slice to lease/registry to avoid mixing tracker concurrency changes. | Tests stay lease-focused |

## After Evidence

| Check | Status | Evidence | Verifies | Does not verify |
|---|---|---|---|---|
| registry-red | pass | Failed on missing `symphony.orchestrator.run_registry` before implementation. | Tests prove the new surface did not already exist. | Behavior after implementation |
| focused-lease | pass | `5 passed, 89 deselected in 0.27s`. | Active lease blocks second claim; stale lease expires; completed run releases; fresh orchestrator cannot dispatch through a live lease. | Whole scheduler regression |
| focused-dispatch | pass | `94 passed in 7.47s`. | Existing dispatch/stall/retry behavior still passes with leases wired in. | Full repo |
| workflow-doctor | pass | All checks PASS, including writable `/Users/danny/symphony_workspaces` and `kanban (9 tickets)`. | Current `WORKFLOW.md` remains dispatch-valid. | Runtime worker execution |
| full-tests | pass | `871 passed, 2 skipped in 56.33s`. | Broad Python regression check. | Browser E2E skipped by default |

## Residual Risk

- Not proven: live worker reattachment after a host crash; multi-node dispatch exclusion; tracker read-modify-write locking.
- Follow-up: board write lock + optimistic ticket versioning; idempotent event log; readiness/ops endpoints.
