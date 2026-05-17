# Explore notes — SMA-24

## PR #19 (12b4610) — refresh workflow dir on hook reload

### Code change recap
- `src/symphony/workspace.py:91-97` — `WorkspaceManager.update_hooks` gained a
  keyword-only `workflow_dir: Path | None = None`. When non-None, mutates
  `self._workflow_dir`. Default-None preserves old behavior for callers that
  only rotate hooks.
- `src/symphony/workspace.py:217-244` — `_run_hook` reads `self._workflow_dir`
  every time it builds the env block. `SYMPHONY_WORKFLOW_DIR` is set to
  `str(self._workflow_dir)` (or empty string when unset). So a mutation via
  `update_hooks` flows into the next hook invocation immediately.
- `src/symphony/orchestrator.py:1087-1092` — the reload branch on
  `_on_tick` now passes `workflow_dir=cfg.workflow_path.parent` alongside
  `cfg.hooks`. The adjacent calls `update_reuse_policy` and
  `update_hook_env(_branch_hook_env(cfg))` are unchanged and still fire on
  every tick. No ordering conflict: each setter mutates one field.

### Regression test (PR-bundled)
- `tests/test_orchestrator_dispatch.py:2949-2995` —
  `test_reload_refreshes_workflow_dir_for_existing_workspace_manager`.
  Boots `Orchestrator` with a `WorkspaceManager` constructed against
  `tmp_path/old/`. Monkeypatches `WorkflowState.reload` to return a new
  `ServiceConfig` with workflow file at `tmp_path/new/WORKFLOW.md` and an
  `after_create` hook of `echo "$SYMPHONY_WORKFLOW_DIR" > wfdir`. After one
  `_on_tick`, calls `create_or_reuse("MT-WFDIR")` and asserts the hook wrote
  `tmp_path/new/`. This exercises the post-reload code path end-to-end.

### Coverage gap (AC #2 target)
The PR-bundled regression test does NOT exercise the parallel updaters
(`update_reuse_policy`, `update_hook_env`) in the same tick. A regression
that, say, reorders or drops one of those calls in `_on_tick` wouldn't be
caught by `test_reload_refreshes_workflow_dir_*`. AC #2 asks for an
additional integration test pinning the simultaneous-update invariant.

## PR #21 (dfdbdc8) — stop failed phase-transition backend

### Code change recap
- `src/symphony/orchestrator.py:2113-2197` — `_rebuild_backend_for_phase`.
  The body splits into three regions:
  1. **Pre-try setup** (`old_client.stop` best-effort, `build_backend`,
     `_apply_dispatch_env`). These run BEFORE the try block. Note: if
     `build_backend` itself raises, there is no new_client to stop, so
     no leak. If `_apply_dispatch_env` raises after `build_backend`, then
     the freshly-built `new_client` would leak — but `_apply_dispatch_env`
     only mutates `os.environ` keys, no subprocess work, so the risk is
     low. Out of scope for SMA-24.
  2. **Try-block** wrapping `new_client.start()`, `new_client.initialize()`,
     `build_first_turn_prompt(...)`, `new_client.start_session(...)`. The
     prompt-build call carries every main-side argument the ticket lists
     (`prompt_template_for_state`, `token_ema`, `token_budget`,
     `max_attempts`, `auto_merge_on_done`, `rewind_scope`, `is_rewind`,
     `language`). So a raise from any of those is caught.
  3. **except BaseException** clause — calls `new_client.stop()` inside a
     nested try (swallowing stop failures with a `phase_transition_new_stop_failed`
     warning), then re-raises. `BaseException` (not just `Exception`) is
     intentional: cancellation and KeyboardInterrupt also need to release
     the new backend.

### Regression test (PR-bundled + local follow-up)
- `tests/test_orchestrator_phase_transition.py:399-420` —
  `test_phase_transition_stops_new_backend_when_rebuild_initialize_fails`.
  Monkeypatches `_FakeBackend.initialize` to raise only for the second
  backend instance (init_id==1). Asserts the second backend's call sequence
  equals `["factory", "start", "initialize", "stop"]`. The `"factory"` entry
  is appended in `_install_fake_backend._factory` (line 229) and was the
  one cherry-pick conflict the local commit 3a8bb7e patched up.

### Coverage gap (AC #3 target)
Initialize-only coverage. AC #3 asks for a `start_session()` raise variant
to prove the try/except still releases the new backend when the failure
happens later in the sequence (after `start` + `initialize` both succeed).
The current test does NOT prove that — a hypothetical regression that
moved `start_session` outside the try would not be caught.

## Touched files (lightest path for AC #2 + AC #3)

- `tests/test_orchestrator_dispatch.py` — append one new test for PR #19
  (workflow_dir + reuse_policy + hook_env simultaneous update). Pattern
  copied from `test_reload_refreshes_workflow_dir_for_existing_workspace_manager`.
- `tests/test_orchestrator_phase_transition.py` — append one new test for
  PR #21 (start_session raises on second backend). Pattern copied from
  `test_phase_transition_stops_new_backend_when_rebuild_initialize_fails`.
- `docs/SMA-24/<stage>/...` — evidence/plan artefacts.
- `docs/features/SMA-24/index.md` — AC #4 As-Is/To-Be summary.

No production code changes. No new helpers. No signature changes.

## Cross-references
- `src/symphony/workspace.py:217-244` — env injection point
- `src/symphony/orchestrator.py:1067-1093` — hot-reload tick body
- `src/symphony/orchestrator.py:2113-2197` — `_rebuild_backend_for_phase`
- `tests/test_orchestrator_dispatch.py:218-234` — `_install_fake_backend`
  factory pattern recap (not in dispatch test file — that helper lives in
  phase_transition's file; dispatch tests use `_make_config` from inside
  the same module).
- `tests/test_orchestrator_phase_transition.py:218-234` — actual factory
  helper anchor.
- `docs/llm-wiki/agent-observability.md` — log-line vocabulary
  (`phase_transition_new_stop_failed` is a peer of the warnings listed there).
