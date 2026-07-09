# GOAL - AF-02 force-eject kills all backend process groups

Single source of done. Only the verifier ticks a box.

## Original Request

> supergoal implement - docs/improvements/tickets/2026-07-09/AF-02-force-eject-kills-all-backends.md

## Spec

Replace the Codex-specific running-entry process identifier with a backend-agnostic process-group identifier populated from every backend's `agent_pid` event. Force-eject must kill that recorded group for Codex and non-Codex workers, log the backend kind with the kill result, then preserve the existing slot-release and retry behavior. A missing pid remains a valid early-spawn-failure path: force-eject must still release the claim and schedule retry without attempting a kill.

Scope is limited to the running-entry pid/pgid contract, event recording, force-eject behavior, backend-contract coverage, focused regression tests, and the required changelog record. AF-01 exit identity checks, AF-10 startup reclaim, and `safe_proc_wait` reaping are non-goals.

## Success Criteria

Each item is falsifiable and names its verification method.

- [x] A non-Codex running entry with a recorded process group is killed during force-eject - verify: `python -m pytest -q tests/test_orchestrator_dispatch.py -k force_eject`
- [x] `force_eject_killed_process_group` includes the backend kind and recorded pid/pgid - verify: `python -m pytest -q tests/test_orchestrator_dispatch.py -k force_eject`
- [x] A running entry without a recorded pid still releases its slot and schedules retry without a kill attempt - verify: `python -m pytest -q tests/test_orchestrator_dispatch.py -k force_eject`
- [x] Every backend contract exposes a non-null `agent_pid` during a live turn, and the orchestrator records it in the backend-agnostic entry field - verify: `python -m pytest -q tests/test_backend_contract.py tests/test_orchestrator_dispatch.py -k 'agent_pid or full_lifecycle'`
- [x] Each per-turn backend publishes the new child pid immediately at every turn spawn so a later hung turn replaces the prior turn's recorded process group - verify: `env PYTHONPATH=src python -m pytest -q tests/test_backend_contract.py -k 'turn_spawn or agent_pid'`
- [x] Existing lease-heartbeat pid persistence and neighboring force-eject behavior do not regress - verify: `python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'backend_agent_pid or force_eject'`
- [x] Process ownership is cleared in memory and the run registry only after confirmed teardown; failed phase cleanup retains the recorded process group and does not start a replacement - verify: `env PYTHONPATH=src python -m pytest -q tests/test_orchestrator_phase_transition.py -k 'phase_stop_failure_stays_unconfirmed_after_idempotent_final_stop or phase_transition_stop_failure or replacement_stop_failure_stays_unconfirmed_after_old_final_stop'`
- [x] Repository lint, type check, and full coverage suite pass - verify: `python -m ruff check src tests`; `python -m pyright`; `python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80`

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | no-op | resolved | `.domain-agent/` is absent | Keep domain evidence ephemeral in this run vault; do not expand product scope with a new local knowledge pack | Verify current-code citations in `PLAN.md` |
| d2 | no-op | resolved | The checked-in ticket already chooses the backend-agnostic PGID design and names non-goals | Treat `supergoal implement` as approval of that design; reject broader lifecycle changes | Diff against ticket and non-goals |
