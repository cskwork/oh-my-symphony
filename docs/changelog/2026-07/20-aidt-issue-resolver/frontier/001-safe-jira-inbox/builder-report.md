# Builder Report — Frontier 001

## Red proof

- Command: `PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py`
- Result: exit 2 during collection; `ModuleNotFoundError: No module named 'symphony.jira_intake'`.
- Timing: captured after adding the binding public-interface tests and before adding product code.

## Design mapping

- `trackers/jira.py`: dedicated escaped fixed-form JQL; `/myself` identity gate; explicit complete token pagination;
  exact project/key/assignee validation; bounded ADF, response, card, and batch handling; parent hydration; GET only.
- `jira_intake.py`: default-off feature-local config and `$ENV_NAME` credential resolution; inert quoted source renderer;
  complete fetch-then-render-then-upsert coordination; stable allowlisted failures with optional HTTP status.
- `trackers/file.py`: strict Jira-key/source/marker/path ownership; symlink and collision rejection; sorted per-key locks;
  whole-batch preflight; latest-byte recomputation; bounded CAS with no unconditional fallback; atomic rename and exact
  unchanged-payload no-op.
- `orchestrator/core.py`: post-dispatch-validation/pre-candidate intake hook; local candidate fetch continues on intake
  failure; disabled/success/failure health transitions and `jira_intake_failure` degradation without secret logging.

## Commands and results

- `PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py`
  - red: exit 2, missing `symphony.jira_intake` during collection before product code.
  - final: 48 passed in 3.78s.
- `PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py tests/test_tracker_jira.py tests/test_tracker_jira_edges.py tests/test_tracker_file.py tests/test_orchestrator_health.py tests/test_service.py tests/test_webapi.py`
  - final: 213 passed in 10.01s.
- `PYTHONPATH=src ../../.venv/bin/pytest -q -p no:cacheprovider`
  - final: 1 failed, 1,462 passed, 6 skipped in 111.41s.
  - sole failure: documented baseline `test_run_continuous_improvement_real_git_target_worktree_e2e`; `CI-1.md` absent.
- `../../.venv/bin/ruff check --no-cache src/symphony/jira_intake.py src/symphony/trackers/jira.py src/symphony/trackers/file.py src/symphony/orchestrator/core.py tests/test_jira_intake.py`
  - passed. `--no-cache` was required because the sandbox denied `.ruff_cache`; the initial cache failure produced no
    lint finding.
- `../../.venv/bin/pyright --pythonpath ../../.venv/bin/python src/symphony/jira_intake.py src/symphony/trackers/jira.py src/symphony/trackers/file.py src/symphony/orchestrator/core.py`
  - 0 errors, 0 warnings.
- `git diff --check`
  - passed.
- `../../.venv/bin/symphony doctor ./WORKFLOW.md`
  - expected environment/path baseline only: sandbox cannot write `/Users/chaeseong-gug/symphony_workspaces`, and this
    isolated worktree has no `kanban/` directory; all other checks passed.

## Changed files

- `src/symphony/jira_intake.py`
- `src/symphony/trackers/jira.py`
- `src/symphony/trackers/file.py`
- `src/symphony/orchestrator/core.py`
- `tests/test_jira_intake.py`
- this evidence report

## Residual issue

- No frontier blocker. Intake remains absent/default-off; no credentials were configured and intake was not activated.
- The pre-existing continuous-improvement E2E baseline failure and doctor path/board environment failures remain
  unchanged and are outside this frontier.
