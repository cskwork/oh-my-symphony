# Explore Notes — SMA-25

## Goal

Verify the `symphony.autocommitExclude` escape hatch in the real `commit_workspace_on_done` path, then hand Plan a small test-and-doc scope.

## Current Code Facts

- `commit_workspace_on_done` builds a Bash script and executes it through `resolve_bash()` with the workspace as `cwd` (`src/symphony/workspace.py:397-405`).
- The cherry-picked mechanism reads `git config --get-all symphony.autocommitExclude`, appends each non-empty value as `:(exclude)<path>`, and passes the Bash array to `git add -A --` (`src/symphony/workspace.py:366-370`).
- The final commit can also squash prior per-turn commits when `symphony.basesha` is set (`src/symphony/workspace.py:374-390`). This matters because the current script stages first, then soft-resets.
- `tests/test_workspace.py` already has real Git tests for fresh repo, parent repo reuse, host-root leakage, no-op, non-Done subject tags, and base squash (`tests/test_workspace.py:375-738`). It does not mention `autocommitExclude`.
- `pytest tests/test_workspace.py --collect-only -q` collected 27 tests on 2026-05-17; the ticket's "19 tests" count is stale.

## History Read

- `9ef5812` added only the config-to-exclude-pathspec loop in `src/symphony/workspace.py`; the commit message explicitly says the PR #23 WORKFLOW policy change was not adopted.
- `abfcba1` changed `git add -A` to `git add -A .` after a smoke test found host-root untracked files leaking into ticket commits.
- `3950952` introduced the one-commit-per-ticket behavior: per-turn wip commits are squashed back to `symphony.basesha` during `commit_workspace_on_done`.

## Manual Probes

These probes were only exploratory; Plan/In Progress must codify them as tests.

- Uncommitted excluded directories worked: with `vendor`, `generated`, `path with space`, and `:weird:name` in `symphony.autocommitExclude`, the final tree contained only `app.txt`.
- Prior committed excluded content leaked: with `symphony.basesha` set and a prior `wip:` commit adding `vendor/cached.txt`, the final tree still contained `vendor/cached.txt`.
- Likely reason: `git reset --soft "$BASE"` happens after the exclude-aware `git add`, and `--soft` keeps the old HEAD index, including excluded paths already captured by wip commits.

## Risk Reviewer Notes

- The ticket's explicit directory test can pass while the real Symphony lifecycle still leaks an excluded path that was captured by `after_run` before final squash.
- A robust regression should cover both final uncommitted paths and already-committed wip paths.
- The smallest likely production fix is to reset the index to the recorded base before the final exclude-aware add when `HAS_NEW_COMMITS=1`; do not change config keys or public function signatures.

## Candidate Test Names

- `test_commit_workspace_on_done_respects_autocommit_exclude_entries`
- `test_commit_workspace_on_done_default_adds_all_files_without_excludes`
- `test_commit_workspace_on_done_autocommit_exclude_survives_base_squash`
