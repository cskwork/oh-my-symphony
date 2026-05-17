# SMA-25 — Auto-Commit Exclude Safety

## What Changed

Symphony now verifies that `symphony.autocommitExclude` protects the final ticket commit, even when an excluded file was already captured in an earlier `wip:` commit.

## Why It Matters

A Symphony worker may create generated files, cached vendor files, or host-mounted workflow plumbing while it works. Those files can be useful during the run but unsafe to merge as product changes. This setting lets an operator opt out specific paths from the automatic final commit.

## As-Is

Before this verification, the mechanism was present but only the uncommitted path behavior had been checked manually. A prior `wip:` commit could still carry an excluded path through the final `symphony.basesha` squash.

## To-Be

The final commit now collapses previous work back to the recorded base with a clean index, then stages files through the exclude-aware `git add -A -- . ':(exclude)<path>'` path. The result is one ticket commit that omits configured paths.

## Usage

Add one or more paths in a workflow setup hook before workers start creating commits:

```bash
git config --add symphony.autocommitExclude vendor
git config --add symphony.autocommitExclude generated
git config --add symphony.autocommitExclude "path with space"
```

Use this for workflow-local generated output, host-mounted support folders, or other paths that should remain in the workspace but never land in the final ticket commit.

## Code Anchors

- `src/symphony/workspace.py:366` reads `symphony.autocommitExclude` and builds Git exclude pathspecs.
- `src/symphony/workspace.py:379` resets the index before the final exclude-aware staging pass.
- `tests/test_workspace.py:461` starts the real Git+Bash regression coverage for default, configured, quoted-path, and prior-wip behavior.
