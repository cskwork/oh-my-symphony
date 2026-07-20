# R-LOOP - Frontier 001 Safe Jira Inbox

## Iteration 1 Verifier Failures

1. **Aged-card refresh exhausts CAS.** In `src/symphony/trackers/file.py:813-833`, the plan token contains the
   on-disk `updated_at`, but `current` reads `latest.front.updated_at` after the prospective mutation changed it.
   Smallest fix: keep the on-disk token separate from the write frontmatter and compare the pre-write re-read token
   to the expected token. Preserve sorted locks, latest-byte recomputation, exact no-op bytes/mtime, and the
   exhausted-retry no-write guarantee. Add
   `test_source_refresh_with_old_updated_at_does_not_exhaust_cas` using a deliberately old stored timestamp.
2. **Parent response identity is not exact.** In `src/symphony/trackers/jira.py:681-696`, the returned key is checked
   only against the configured project pattern. Smallest fix: require the validated payload key to equal the
   requested `parent_key` before consuming fields or caching content. Add
   `test_parent_response_key_must_match_requested_parent`.
3. **Imported acceptance text triggers auto-triage.** The source renderer blocks the cited Markdown heading/fence
   parsers, but `orchestrator.helpers._is_auto_triage_todo_candidate` searches raw text for `acceptance criteria`.
   Smallest in-scope fix: neutralize upstream whitespace/text in `jira_intake.py` so rendered content stays readable
   while no Jira-controlled phrase matches this raw parser. Do not edit `helpers.py`, which is outside the frozen
   five-file scope. Add `test_imported_acceptance_criteria_does_not_trigger_auto_triage` and retain the existing
   direct checks for dependencies, touched files, findings, prompt sections, and stage contracts.

## Iteration Contract

Record only verifier-observed failures, the smallest required correction, and the next trusted command.
Maximum build/verify iterations: 3.

## Trusted Rerun Commands

```bash
PYTHONPATH=src rtk ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py -k 'source_refresh_with_old_updated_at or parent_response_key or imported_acceptance_criteria'
PYTHONPATH=src rtk ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py
PYTHONPATH=src rtk ../../.venv/bin/pytest -q -p no:cacheprovider tests/test_jira_intake.py tests/test_tracker_jira.py tests/test_tracker_jira_edges.py tests/test_tracker_file.py tests/test_orchestrator_health.py tests/test_service.py tests/test_webapi.py
rtk ../../.venv/bin/ruff check --no-cache src/symphony/jira_intake.py src/symphony/trackers/jira.py src/symphony/trackers/file.py src/symphony/orchestrator/core.py tests/test_jira_intake.py
rtk ../../.venv/bin/pyright --pythonpath ../../.venv/bin/python src/symphony/jira_intake.py src/symphony/trackers/jira.py src/symphony/trackers/file.py src/symphony/orchestrator/core.py
rtk git diff --check
PYTHONPATH=src rtk ../../.venv/bin/pytest -q -p no:cacheprovider
PYTHONPATH=src rtk ../../.venv/bin/symphony doctor ./WORKFLOW.md
```

## Iteration 2 Closure

- The three named evaluator regressions passed: aged stored timestamps refresh successfully while forced/exhausted
  CAS remains no-write; parent payload identity must equal the requested key before fields or cache; Jira-rendered
  acceptance text remains readable but inert to dependencies, touched files, findings, prompt sections, Verify
  contracts, and `_is_auto_triage_todo_candidate`.
- Complete intake and relevant regressions passed: 51 intake tests, 216 exact seven-suite tests, and six preserved-
  contract tests. Ruff, Pyright, and `git diff --check` passed.
- Full pytest matched the accepted ledger exactly: 1 failed, 1,465 passed, 6 skipped; the sole failure is
  `test_run_continuous_improvement_real_git_target_worktree_e2e` because `kanban/CI-1.md` is absent.
- Doctor matched the accepted environment baseline: external workspace root is not writable and the isolated
  worktree has no `kanban/`; every other check passed.
- No new material defect was found. Iteration 2 is closed for finalization.
