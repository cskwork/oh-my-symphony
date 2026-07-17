# GOAL - retry-safe terminal auto-merge

Single source of "done". Only the verifier ticks a box; unticking needs regression evidence.
Never delete or reword an unmet criterion - append. Mid-run discovered musts are APPENDED as new
unchecked criteria tagged `(surfaced: ...)`. Ambiguous/product-changing candidates go to
`## Decision Gates` as `ask-user`, not into criteria.

## Original Request

> check and fix potential issues in the program update so agent can easily navigate the program and refactor improve efficiency and effectiveness and robust tests so changes can detect bugs issues and fix automiatcally

## Spec

Audit the post-v0.13.0 update and repair the smallest confirmed reliability defect. The selected
surface is terminal branch auto-merge: when a local merge commit exists but upstream synchronization
fails, later attempts must not report success until the configured upstream ref exactly matches the
local target. Keep the public `auto_merge_on_done_best_effort` interface and existing merge, dirty-tree,
excluded-path, and no-upstream behavior stable. Improve locality by separating script phases into
small, named helpers, and add black-box Git-repository regression tests that exercise first attempt,
failure retry, and successful recovery. No workflow-state redesign, dependency change, release, push,
or edit to the original checkout's untracked July 12 planning documents.

## Success Criteria

Each item is falsifiable and names its verification method.

- [x] A retry after a rejected upstream push remains non-success while the remote ref is stale and re-attempts synchronization - verify: `uv run --extra dev pytest -q tests/test_auto_merge.py -k 'retry_after_push_failure'`
- [x] Once the upstream accepts the retry, the existing local merge is pushed and verified without creating a duplicate merge commit - verify: `uv run --extra dev pytest -q tests/test_auto_merge.py -k 'retry_after_push_failure'`
- [x] Existing successful merge, missing branch, dirty overlap, excluded path, capture, conflict, and no-upstream behavior remains green - verify: `uv run --extra dev pytest -q tests/test_auto_merge.py`
- [x] Script generation is split into cohesive named phases while the public Python interface and result statuses remain compatible - verify: `uv run --extra dev ruff check src tests` and verifier diff review
- [x] The repository's trusted test, lint, type, and coverage gates pass - verify: `uv run --extra dev pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80`, `uv run --extra dev ruff check src tests`, and `uv run --extra dev pyright`
- [x] The decision and rejected alternatives are recorded without modifying unrelated work - verify: `git diff --check` and verifier backward-trace review
- [x] A rejected-push retry with `capture_untracked` configured exercises the nothing-staged path, remains non-success while upstream is stale, and recovers without another merge commit - verify: a black-box `tests/test_auto_merge.py` regression (surfaced: approved PLAN step 3 changes a distinct retry branch at `_build_merge_phase` that the current no-capture regression does not reach)
- [x] The isolated workflow passes its required health check - verify: `.venv/bin/symphony doctor ./WORKFLOW.md` (surfaced: independent Verify requires this exact command before PASS)

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | ask-user | resolved | `.domain-agent/` has an index but no required `config.json`; the code change can use an ephemeral Domain Brief instead of creating local knowledge files. | Keep context in this run vault; do not create local knowledge files. | User approved the issue-fix plan. |
| d2 | ask-user | resolved | The audit also found separate OpenCode terminal-heartbeat and malformed-scorecard false-green candidates. | Keep this run surgical and handle them as follow-up slices. | User approved the narrow issue fix. |
