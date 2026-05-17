# Implementation plan — SMA-24

Scope: add two regression tests that prove the cherry-picked fixes (PR #19
workflow_dir refresh, PR #21 phase-transition stop) hold in scenarios the
PR-bundled tests left uncovered. No production code edits. No new helpers.

## File ownership

| file | edit kind | what changes |
|------|-----------|--------------|
| `tests/test_orchestrator_dispatch.py` | append one test | PR #19 simultaneous-update regression |
| `tests/test_orchestrator_phase_transition.py` | append one test | PR #21 `start_session()`-fails regression |
| `docs/features/SMA-24/index.md` | create | AC #4 As-Is/To-Be summary + test citations |
| `docs/SMA-24/plan/implementation-plan.md` | create (this file) | full plan |
| `kanban/SMA-24.md` | append Plan section + transition | pipeline state |

Production code (`src/symphony/orchestrator.py`, `src/symphony/workspace.py`)
is read-only this stage. Touching either pushes the ticket back to `Blocked`.

## Ordered steps

1. **PR #21 test first (RED → GREEN)** — append
   `test_phase_transition_stops_new_backend_when_rebuild_start_session_fails`
   to `tests/test_orchestrator_phase_transition.py` just below the existing
   `test_phase_transition_stops_new_backend_when_rebuild_initialize_fails`
   (line 421). Reuse `_make_config`, `_make_issue`, `_orch`,
   `_seed_running_entry`, `_install_fake_backend`, `_install_state_sequence`.
   Patch `_FakeBackend.start_session` so init_id==1 raises after recording
   the call (preserve the existing call-recording shape from `_FakeBackend`
   so the asserted sequence stays observable). Run the test alone — confirm
   it passes against the merged fix (the try/except already covers
   `start_session`).
2. **PR #19 test (RED → GREEN)** — append
   `test_reload_refreshes_reuse_policy_and_hook_env_alongside_workflow_dir`
   to `tests/test_orchestrator_dispatch.py` just below the existing
   `test_reload_refreshes_workflow_dir_for_existing_workspace_manager`
   (line 2995). Build `old_cfg` with `reuse_policy="preserve"` and empty
   feature/merge-target branches. Build `new_cfg` via
   `dataclasses.replace(...)` with:
   - new `workflow_path` (different parent dir),
   - `workspace_reuse_policy="refresh"`,
   - `agent.feature_base_branch="develop"`,
   - `agent.auto_merge_target_branch="main"`,
   - `hooks.after_create` echoes `$SYMPHONY_WORKFLOW_DIR|$SYMPHONY_FEATURE_BASE_BRANCH|$SYMPHONY_MERGE_TARGET_BRANCH`
     into `snapshot`.
   Monkeypatch `state.reload` to return the new cfg. After one `_on_tick`:
   - call `create_or_reuse("MT-WFDIR")` — assert first `snapshot` contents.
   - call `create_or_reuse("MT-WFDIR")` again (same id) — assert the file
     was re-written. That is the proof `reuse_policy="refresh"` was applied;
     under the old "preserve" policy the second call is a no-op.
3. **Run the two test files together** — `pytest
   tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py -q`.
   All previously-passing tests stay green; two new tests green.
4. **Run the full suite** — `pytest -q` (target ≤ ~5 min). Guard against
   helper-stub leak (see `reference_test_module_level_stub_leak` memory).
5. **Write `docs/features/SMA-24/index.md`** — single page covering AC #4:
   one paragraph per fix (As-Is / To-Be / cited tests with file:line).
6. **Run pyright / black / ruff if hooks demand** — none of our edits
   touch types beyond what the surrounding tests already use. No prod code
   changed, so type-check surface is unchanged.

## First failing test (red ledger)

`tests/test_orchestrator_phase_transition.py::test_phase_transition_stops_new_backend_when_rebuild_start_session_fails`

```python
def test_phase_transition_stops_new_backend_when_rebuild_start_session_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=5)
    issue = _make_issue(state="Todo")
    o = _orch(tmp_path)
    _seed_running_entry(o, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    async def _start_session(
        self_inst: _FakeBackend, *, initial_prompt: str, issue_title: str
    ) -> None:
        self_inst.calls.append(
            (
                "start_session",
                {
                    "initial_prompt": initial_prompt,
                    "issue_title": issue_title,
                },
            )
        )
        if self_inst.init_id == 1:
            raise RuntimeError("second backend start_session failed")

    monkeypatch.setattr(_FakeBackend, "start_session", _start_session)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 2
    second_calls = [name for name, _ in instances[1].calls]
    assert second_calls == [
        "factory", "start", "initialize", "start_session", "stop",
    ]
```

Pre-fix (PR #21 reverted), `start_session` would raise without a paired
`stop`, leaking the new backend. The merged try/except now wraps
`start_session` too, so this test passes against `main`.

## Verification commands

| stage | command | expected |
|-------|---------|----------|
| New tests alone | `pytest tests/test_orchestrator_phase_transition.py::test_phase_transition_stops_new_backend_when_rebuild_start_session_fails tests/test_orchestrator_dispatch.py::test_reload_refreshes_reuse_policy_and_hook_env_alongside_workflow_dir -q` | 2 passed |
| AC #1 surface | `pytest tests/test_orchestrator_dispatch.py tests/test_orchestrator_phase_transition.py -q` | all green |
| Full suite | `pytest -q` | all green, no regressions |
| Doc artefact present | `test -f docs/features/SMA-24/index.md` | exit 0 |

## Acceptance Criteria mapping

| AC | Closed by |
|----|-----------|
| 1  | Step 3 — full target-file run green |
| 2  | Step 2 — new PR #19 test exercises workflow_dir + reuse_policy + hook_env in one tick |
| 3  | Step 1 — new PR #21 test exercises `start_session()` failure path |
| 4  | Step 5 — `docs/features/SMA-24/index.md` with As-Is/To-Be + cited tests |

## Risk + fallback

- **Risk**: `_FakeBackend.start_session` is a method on a dataclass; monkeypatching
  via `monkeypatch.setattr(_FakeBackend, "start_session", _start_session)` must
  preserve the keyword-only signature (`*, initial_prompt`, `issue_title`).
  Fallback: define `_start_session` as a regular `async def` with the same
  keyword-only signature (matches the existing PR-bundled initialize-fails test
  pattern verbatim).
- **Risk**: `_make_config` in dispatch tests does NOT accept
  `workspace_reuse_policy` or `agent.feature_base_branch` kwargs. Fallback:
  build base cfg via `_make_config(...)` then mutate via
  `dataclasses.replace(cfg, workspace_reuse_policy="refresh", agent=replace(cfg.agent, feature_base_branch="develop", auto_merge_target_branch="main"))`.
  `replace` is already imported (line 6).
- **Risk**: `WORKFLOW.md` sets `max_concurrent_agents=2`, so SMA-25 may run
  in parallel. Both tickets touch disjoint test files (SMA-24 → orchestrator
  tests, SMA-25 → workspace tests), so no merge conflict.
- **Rollback**: each new test is a single `def test_*` function in a single
  file; revert by deleting the function.

## Rollback / blast radius

- 2 test files touched, ~60 lines added total. 1 doc file created.
- Zero production code changes.
- Zero new helpers, fixtures, or imports beyond what the existing tests
  already import.
- `git revert` of the SMA-24 commit removes the new tests cleanly.
