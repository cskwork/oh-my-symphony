# SMA-24 — Verify orchestrator fixes from PR #19 + PR #21 cherry-picks

**Audience**: PM / engineer / future maintainer. Reading time ~30 s.

## What changed

Two regression tests were added (no production code touched) to close
coverage gaps left by the PR #19 and PR #21 cherry-picks. Both gaps were
explicit Acceptance Criteria on this ticket (AC #2, AC #3); the
PR-bundled tests exercised only the simplest failure shape for each fix.

## PR #19 — refresh workflow dir on hook reload

- **As-Is (pre-merge)**: `_on_tick` swapped only `update_hooks(..., workflow_dir=...)` on reload; a config tick that **also** changed `workspace_reuse_policy` or `agent.feature_base_branch` / `agent.auto_merge_target_branch` left the workspace manager half-refreshed. After_create hooks ran with stale `SYMPHONY_FEATURE_BASE_BRANCH` and the reuse policy never escaped "preserve".
- **To-Be (post-merge `12b4610` + this regression test)**: a single tick now propagates all three (`update_hooks`, `update_reuse_policy`, `update_hook_env`) — see `src/symphony/orchestrator.py:1087-1092`. The new test fails if any one of those three calls is dropped.
- **Cited test**: `tests/test_orchestrator_dispatch.py:2999` — `test_reload_refreshes_reuse_policy_and_hook_env_alongside_workflow_dir`. One `_on_tick` then two `create_or_reuse` calls; asserts both the env-var contents (proves `update_hook_env` + `update_hooks` ran) and the duplicate `after_create` invocation (proves `update_reuse_policy` flipped from `preserve` to `refresh`).

## PR #21 — stop failed phase-transition backend

- **As-Is (pre-merge)**: `_rebuild_backend_for_phase` wrapped only `initialize()` in try/except. If `start_session()` raised, the new backend stayed half-built — `stop()` was never called, the process leaked, and the worker exited with a dangling subprocess.
- **To-Be (post-merge `dfdbdc8` + this regression test)**: the try block now covers `start → initialize → build_first_turn_prompt → start_session`; the `except` branch always calls `new_client.stop()` and re-raises — see `src/symphony/orchestrator.py:2164-2196`. The new test fails if `start_session` is moved outside the try block.
- **Cited test**: `tests/test_orchestrator_phase_transition.py:423` — `test_phase_transition_stops_new_backend_when_rebuild_start_session_fails`. Patches `_FakeBackend.start_session` so `init_id==1` raises; asserts the second backend's call sequence is exactly `["factory", "start", "initialize", "start_session", "stop"]`.

## Acceptance Criteria mapping

| AC | Status | Evidence |
|----|--------|----------|
| 1  | green  | `pytest tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py -q` → 91 passed, 1 skipped |
| 2  | green  | new `test_reload_refreshes_reuse_policy_and_hook_env_alongside_workflow_dir` (dispatch:2999) |
| 3  | green  | new `test_phase_transition_stops_new_backend_when_rebuild_start_session_fails` (phase_transition:423) |
| 4  | green  | this file |

## Out of scope (per ticket)

- No production-code edits.
- No new helpers, fixtures, or imports beyond what the surrounding tests already use.
- PR #23 `autocommitExclude` is handled in SMA-25.
