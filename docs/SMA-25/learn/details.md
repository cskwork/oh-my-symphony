# SMA-25 Learn Details

**What**: Learn promoted the auto-commit exclude behavior into the shared wiki.
**Why**: Future tickets can understand when to use `symphony.autocommitExclude` without re-reading the full SMA-25 evidence trail.

## Brief Versus Reality

- The original brief assumed the cherry-picked PR #23 mechanism only needed end-to-end verification for uncommitted paths.
- Explore found a stronger lifecycle risk: an excluded path already captured in a prior `wip:` commit could survive the final squash.
- Implementation fixed that by resetting the index to `symphony.basesha` before the final exclude-aware staging pass.
- QA later found a separate sandbox-sensitive doctor test fixture, then verified the full suite after replacing real socket setup with a fake occupied socket.

## Wiki Decision

- Created `docs/llm-wiki/workspace-auto-commit-excludes.md` because no existing entry covered final auto-commit exclusions.
- Updated `docs/llm-wiki/INDEX.md` with one row for the new topic.
- No existing wiki entry was invalidated; no cross-entry contradiction was found.

## Merge Gate Inputs

- Target branch resolution: `agent.auto_merge_target_branch` is empty, `agent.feature_base_branch` is empty, so the host branch `main` is the target.
- Expected preflight command: `git merge-tree --write-tree main symphony/SMA-25`.
- Dirty-overlap check should compare tracked host changes against `git diff --name-only main..symphony/SMA-25` after the committed merge preflight is clean.

## Merge Gate Result

- `git add docs/llm-wiki/INDEX.md docs/llm-wiki/workspace-auto-commit-excludes.md docs/SMA-25/learn/details.md` failed before commit because Git tried to write `/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.git/worktrees/SMA-25/index.lock` outside this sandbox's writable roots.
- `git merge-tree --write-tree main symphony/SMA-25` exited 128 with `error: unable to create temporary file: Operation not permitted` and `fatal: failure to merge`.
- Conflict paths are unknown because the merge-tree command failed before Git could produce a committed merge analysis.
