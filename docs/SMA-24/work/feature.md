# SMA-24 — Work artefact

## What changed (user-observable)

Two new pytest regression tests now lock in the PR #19 and PR #21
orchestrator fixes against the exact failure shapes the ticket called
out. There is no behavioural change for the runtime, no new commands,
no config flags, and no UI surface.

## Files touched

| file | edit | purpose |
|------|------|---------|
| `tests/test_orchestrator_phase_transition.py` | +47 lines (one test) | pins PR #21 `start_session()` failure path |
| `tests/test_orchestrator_dispatch.py` | +75 lines (one test) | pins PR #19 simultaneous workflow_dir + reuse_policy + hook_env refresh |
| `docs/features/SMA-24/index.md` | new file | AC #4 As-Is/To-Be summary + test citations |
| `docs/SMA-24/work/feature.md` | new file (this) | work-stage artefact |

Zero production code touched. `git diff main -- src/` is empty.

## How a user observes the change

- `pytest tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py -q` → 91 passed, 1 skipped (was 89/1).
- `pytest -q` (full suite) → 524 passed, 6 skipped — unchanged baseline outside the two new tests.
- Future PR that drops `update_reuse_policy` or `update_hook_env` from `_on_tick` immediately fails the new dispatch test.
- Future PR that moves `start_session()` outside the `_rebuild_backend_for_phase` try block immediately fails the new phase-transition test.

## Knobs / flags

None added; none touched.
