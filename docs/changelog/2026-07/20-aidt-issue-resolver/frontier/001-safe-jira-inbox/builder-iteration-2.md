# Builder Iteration 2 - Frontier 001 Safe Jira Inbox

## Scope

Frozen correction of the three verifier-observed failures in `R-LOOP.md`: aged-card CAS refresh, exact parent response
identity, and Jira-controlled acceptance wording reaching auto-triage. No broader product behavior is in scope.

## RED Proof

Command:

```bash
PYTHONPATH=src rtk ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py -k 'source_refresh_with_old_updated_at or parent_response_key or imported_acceptance_criteria'
```

Result: exit 1; 3 failed, 48 deselected in 0.27s.

- `test_parent_response_key_must_match_requested_parent`: `JiraUnknownPayload` was not raised; the mismatched
  `A20-99` response was accepted for requested parent `A20-10`.
- `test_imported_acceptance_criteria_does_not_trigger_auto_triage`: the complete parser matrix remained inert until
  `_is_auto_triage_todo_candidate`, which returned `True`.
- `test_source_refresh_with_old_updated_at_does_not_exhaust_cas`: refresh raised
  `SymphonyError: external source CAS retries exhausted` because the prospective timestamp was compared as current
  disk state.

## GREEN Proof

| Command | Exact result |
|---|---|
| Focused three-test rerun | exit 0; 3 passed, 48 deselected in 0.20s |
| `tests/test_jira_intake.py` | exit 0; 51 passed in 4.21s |
| Seven targeted Jira/file/orchestrator/service/web suites from `R-LOOP.md` | exit 0; 216 passed in 10.72s |
| Six exact preserved-contract regressions | exit 0; 6 passed in 0.26s |
| Ruff with `--no-cache` over the prescribed five paths | exit 0; all checks passed |
| Pyright with `../../.venv/bin/python` over the prescribed four product paths | exit 0; 0 errors, 0 warnings, 0 informations |
| `rtk git diff --check` | exit 0; no output |
| Full pytest | exit 1; 1 failed, 1465 passed, 6 skipped in 106.68s |
| `symphony doctor ./WORKFLOW.md` | exit 1; two known environment failures, all other checks passed |

The focused three-test command was:

```bash
PYTHONPATH=src rtk ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py -k 'source_refresh_with_old_updated_at or parent_response_key or imported_acceptance_criteria'
```

The six exact preserved-contract regressions covered byte/mtime no-op behavior, exhausted-CAS no-write behavior,
local evidence preservation, concurrent local-note preservation, valid parent hydration, and hostile Markdown inertness.

## Changed Lines and Decisions

- `tests/test_jira_intake.py:5,11,29-33,270-295,483-530,567-579`: added exactly the three named regressions and
  only their imports. The acceptance regression executes the real auto-triage candidate path and retains direct
  checks for dependencies, touched files, findings, prompt sections, the Verify contract, and readable rendered
  context.
- `src/symphony/trackers/file.py:824-834`: reread frontmatter and mtime from the actual path immediately before
  writing, then compare that disk token with the plan token. Prospective write frontmatter is never used as current
  state. The existing unchanged-source return, bounded retry, and exhausted-retry no-write paths remain intact.
- `src/symphony/trackers/jira.py:686-688`: normalize the response key, require exact equality with the requested
  `parent_key`, and fail before reading fields or populating the cache.
- `src/symphony/jira_intake.py:30-32,132-140`: after escaping Jira-controlled text, replace only matching whitespace
  between `acceptance` and `criteria` with the trusted `&#32;` display entity. Markdown/HTML renders the same readable
  phrase, while Symphony's raw regex cannot interpret it. Editing the orchestrator parser, weakening auto-triage,
  and invisible Unicode were rejected as out of scope or less robust.
- This report is the only additional evidence file. No GOAL, QA, R-LOOP, run-state, Z, commit, WORKFLOW,
  credentials, activation, or other product/test file was edited in this iteration.

## Residual Issues

- The sole full-suite failure is the accepted pre-change baseline
  `tests/test_continuous_improvement.py::test_run_continuous_improvement_real_git_target_worktree_e2e`; its temporary
  `kanban/CI-1.md` is absent. There were no other failures.
- Doctor remains environment-blocked only because `/Users/chaeseong-gug/symphony_workspaces` is not writable in the
  sandbox and the worktree-local `kanban/` directory does not exist. All other doctor checks passed.
- No frontier-specific residual failure is known. The iteration is ready for a fresh verifier.
