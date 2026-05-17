# Workspace Auto-Commit Excludes

## Getting the Feel (For Beginners)

### Why workspace auto-commit excludes exist

Symphony workers often create helpful temporary folders while they work, such as generated files or cached vendor output. Those files can help the agent finish the task, but they should not always become part of the final product change.

The simplest way for a beginner to picture it:

`Core flow: Worker writes files → Symphony checks exclude list → Final commit keeps only allowed files`

There are five terms you need to internalise at this stage.

| Term | Plain-English meaning |
|---|---|
| Workspace | The ticket's private work area where the agent edits files |
| Final commit | The single package of changes Symphony prepares when a ticket is done |
| Exclude path | A folder or file the operator wants left out of that final package |
| Git config | A local settings notebook Git can read while it runs commands |
| Squash | Combining several small work-in-progress commits into one final commit |

To make it concrete:

An agent may generate a `vendor/` cache so tests run locally, while the product change is only in `src/` and `tests/`. The operator adds `vendor` to the exclude list, so the cache stays available during the run but does not land in the final ticket commit.

The decision rule that matters at this stage:

**Just remember this: use `symphony.autocommitExclude` for files that are useful during agent work but unsafe or noisy in the final merge.**

When you're ready to go deeper, read `docs/features/SMA-25/index.md`.

## Technical Reference

**Summary:** `commit_workspace_on_done` reads every `symphony.autocommitExclude` Git config value, converts each value into a Git exclude pathspec, resets the index to `symphony.basesha` when final-squashing prior `wip:` commits, and then stages the collapsed workspace snapshot through the exclude-aware `git add` command.

**Invariants & Constraints:**
- An empty `symphony.autocommitExclude` config must behave like `git add -A -- .`.
- Exclude values must be passed as Bash array entries so paths with spaces or leading colons remain literal.
- Excludes must apply after collapsing prior `wip:` commits, not only to uncommitted files.
- The config key is opt-in and must not revive the rejected PR #23 docs/llm-wiki host-symlink policy.

**Files of interest:**
- `src/symphony/workspace.py:366` — reads `symphony.autocommitExclude` and builds exclude pathspecs.
- `src/symphony/workspace.py:377` — resets the index before the final exclude-aware staging pass.
- `tests/test_workspace.py:464` — starts the default, configured-exclude, and prior-wip regression coverage.
- `docs/features/SMA-25/index.md:1` — explains the operator-facing As-Is/To-Be usage.

**Observability hooks:**
- log: `auto_commit_start` at `src/symphony/workspace.py:407` — final auto-commit began for a workspace.
- log: `auto_commit_failed` at `src/symphony/workspace.py:424` — Git/Bash auto-commit returned a nonzero exit.
- log: `auto_commit_done` at `src/symphony/workspace.py:436` — final auto-commit completed successfully.

**Decision log:**
- 2026-05-17 | SMA-25 | Added regression coverage for default add-all, configured excludes, quoted path values, and prior-wip squash leakage; moved the final index reset before exclude-aware staging.

**Last updated:** 2026-05-17 by SMA-25.
