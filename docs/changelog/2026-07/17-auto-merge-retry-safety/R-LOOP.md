# R-LOOP - verifier -> implementer loop channel

The verifier APPENDS one timestamped section per failed verification pass; the relaunched implementer
reads `PLAN.md` plus ONLY the latest section here. Never edit older sections; never delete this file.
A regressed previously-green criterion is unticked in `GOAL.md` and listed first.

## 2026-07-17T06:48 iteration 1

- [ ] GOAL criteria 1-2: expected the frozen `-k retry_after_push_failure` command to execute the rejected-push retry proof; actual result is exit `5` with `12 deselected`. Evidence: `QA.md` `## Results`. Smallest next fix: give the regression test a name that matches both the frozen selector and the assigned `retries_rejected_push_until_upstream_matches` selector, then rerun both.
- [ ] Surfaced criterion 7: expected a black-box retry/recovery test to reach `_build_merge_phase`'s changed nothing-staged synchronization with `capture_untracked` configured; actual tests cover only the earlier no-capture no-op branch and a fresh staged capture merge. Evidence: `src/symphony/utils/auto_merge.py:297`, `src/symphony/utils/auto_merge.py:308`, and `tests/test_auto_merge.py:179`. Smallest next fix: extend the rejected-push test with a real capture directory, prove repeated rejection and exact-SHA recovery, and assert the merge count remains unchanged.
- [ ] GOAL criterion 5: expected the exact `.venv/bin/pyright` invocation to pass; actual unactivated invocation exits `1` with 24 unresolved-import errors and 3 warnings, while `--pythonpath .venv/bin/python` and an activated environment are clean. Evidence: `QA.md` `## Results`. Smallest next fix: make the frozen direct invocation select the worktree interpreter without adding lockfile noise, then rerun it exactly.
- [ ] Surfaced criterion 8: expected `.venv/bin/symphony doctor ./WORKFLOW.md` to pass; actual result exits `1` because ignored `kanban/` is absent from the isolated worktree. Evidence: `QA.md` `## Results`. Smallest next fix: provision the disposable board root through the approved workspace setup, then rerun doctor; do not commit generated board files.
Regression: none; the main retry test, module suite, full coverage, Ruff, explicit-interpreter Pyright, and diff integrity remain green.
Next: close the two automated-proof gaps and the two environment setup gates, then relaunch a fresh `qa-auditor` for iteration 2.
