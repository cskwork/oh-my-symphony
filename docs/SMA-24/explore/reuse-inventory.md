# Reuse inventory — SMA-24

Verification + test additions only. Every reuse candidate below is an existing
helper or pattern in the test suite that the new tests will copy without
modification.

| candidate | path:line | reuse_fit (0-1) | adapt_cost | notes |
|-----------|-----------|------------------|------------|-------|
| `_make_config` (dispatch tests) | `tests/test_orchestrator_dispatch.py:33-95` | 1.0 | low | Already parametrized on `workflow_path`, `workspace_root`, `hooks`. PR #19 follow-up test reuses verbatim. |
| `_make_config` (phase-transition tests) | `tests/test_orchestrator_phase_transition.py:118-185` | 1.0 | low | PR #21 follow-up test reuses verbatim. |
| `_make_issue` | `tests/test_orchestrator_phase_transition.py:188-198` | 1.0 | low | Same fixture issue, no adaptation. |
| `_orch` | `tests/test_orchestrator_phase_transition.py:201-205` | 1.0 | low | Constructs Orchestrator + fake workspace manager. |
| `_seed_running_entry` | `tests/test_orchestrator_phase_transition.py:208-215` | 1.0 | low | Seeds `_running[issue.id]` so `_run_agent_attempt` finds its entry. |
| `_install_fake_backend` factory | `tests/test_orchestrator_phase_transition.py:218-234` | 1.0 | low | Records `("factory", ...)` plus every backend call. The new test inverts which method raises — patch `_FakeBackend.start_session` instead of `.initialize`. |
| `_install_state_sequence` | `tests/test_orchestrator_phase_transition.py:237-264` | 1.0 | low | Scripted state walk. New test reuses `["In Progress", "Done"]` like the existing initialize-fails test. |
| `WorkspaceManager` direct construction in test | `tests/test_orchestrator_dispatch.py:2966-2970` (existing reload test) | 1.0 | low | New PR #19 test reuses the constructor signature, just swaps the `cfg` variant under test. |
| `HooksConfig` builder lines | `tests/test_orchestrator_dispatch.py:2961-2965` | 1.0 | low | `after_create='echo "$SYMPHONY_WORKFLOW_DIR ... " > wfdir'` style. New test combines workflow_dir + reuse_policy + hook_env in one shell line. |

## Non-reuse decisions
- _No new helpers._ The two new tests live inside the existing test files
  and use only the helpers listed above. Adding helpers would inflate the
  blast radius beyond the AC.
- _No production-code helper extraction._ The fix code is already
  minimal-surface; refactoring is explicitly out-of-scope per the ticket.
