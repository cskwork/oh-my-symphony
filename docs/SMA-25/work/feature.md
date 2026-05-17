# SMA-25 Work Note — Auto-Commit Exclude Verification

**What**: The auto-commit exclude setting is now covered by real Git+Bash tests and protected during the final squash.
**Why**: Operators can keep generated or workflow-only paths out of ticket commits without changing the default commit behavior.
**As-Is → To-Be**:
- As-Is: Configured excludes worked for uncommitted files, but an excluded path already inside a prior `wip:` commit could leak into the final ticket commit.
- To-Be: Symphony resets the index to the recorded base first, then stages the collapsed workspace with `symphony.autocommitExclude` applied.

## User-Visible Behavior

- With no config entries, `commit_workspace_on_done` still commits all workspace files.
- With `git config --add symphony.autocommitExclude <path>`, matching paths stay in the workspace but are omitted from the final auto-commit.
- Paths with spaces and leading colons are passed as Bash array entries, so quoting-sensitive values remain protected.
- A previous `wip:` commit containing an excluded path is no longer enough for that path to survive the final squash.

## Verification Snapshot

- Red check: `pytest tests/test_workspace.py::test_commit_workspace_on_done_autocommit_exclude_survives_base_squash -q` failed with `vendor/cached.txt` in `HEAD` before the fix.
- Green check: `.venv/bin/pytest tests/test_workspace.py::test_commit_workspace_on_done_autocommit_exclude_survives_base_squash tests/test_workspace.py::test_commit_workspace_on_done_respects_autocommit_exclude_entries tests/test_workspace.py::test_commit_workspace_on_done_default_adds_all_files_without_excludes -q` passed with `3 passed`.
