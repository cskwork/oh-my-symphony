# Implementation Plan — SMA-25

## Target Outcome

Prove the cherry-picked `symphony.autocommitExclude` mechanism through real Git+Bash integration tests, then fix only the discovered final-squash leak if the strict regression fails.

## Chosen Approach

Candidate A is the execution path: add focused integration tests in `tests/test_workspace.py`, let the stricter base-squash regression fail first, make the smallest `src/symphony/workspace.py` ordering fix needed, and document the operator-facing behavior in `docs/features/SMA-25/index.md`.

This wins because it reuses the real `commit_workspace_on_done` path, existing Git helpers, and existing base-squash fixtures. It also catches the lifecycle risk Explore found: excluded paths already captured in a prior `wip:` commit can remain staged after `git reset --soft "$BASE"`.

## Write Scope

| path | ownership |
|---|---|
| `tests/test_workspace.py` | Add the three regression tests and small local assertions/helpers only if needed. |
| `src/symphony/workspace.py` | Edit only `commit_workspace_on_done` if the base-squash regression fails. Expected fix is staging/reset ordering, not a new config key or signature. |
| `docs/features/SMA-25/index.md` | Add the PM-friendly As-Is/To-Be behavior note and usage snippets. |
| `docs/SMA-25/work/*.md` | In Progress evidence only, if the next stage needs spillover. |
| `log/changelog-2026-05-17.md` | Append the reason for any production-code ordering change. |

## Implementation Steps

1. Add `test_commit_workspace_on_done_default_adds_all_files_without_excludes`: initialize a real repo, do not set `symphony.autocommitExclude`, create normal plus would-be-generated files, run `commit_workspace_on_done`, and assert every file is in `HEAD`.
2. Add `test_commit_workspace_on_done_respects_autocommit_exclude_entries`: set two config entries (`vendor`, `generated`) plus special entries (`path with space`, `:weird:name`), create files under each, run `commit_workspace_on_done`, and assert only the non-excluded file lands in the final tree.
3. Add `test_commit_workspace_on_done_autocommit_exclude_survives_base_squash`: record `symphony.basesha`, commit `vendor/cached.txt` in a prior `wip:` commit, set `symphony.autocommitExclude vendor`, run `commit_workspace_on_done`, and assert the final ticket commit omits `vendor/cached.txt`.
4. Run the strict test alone and expect it to fail before the production fix if current staging order still leaks prior `wip:` content.
5. If it fails, update the Bash script inside `commit_workspace_on_done` so the recorded-base reset happens before the final exclude-aware staging snapshot; keep "nothing to commit" behavior intact for no-diff and no-new-commit cases.
6. Re-run the three focused tests, then `pytest tests/test_workspace.py -q`.
7. Create `docs/features/SMA-25/index.md` with As-Is, To-Be, usage snippets for `git config --add symphony.autocommitExclude <path>`, and 1-3 code anchors.
8. Record Implementation evidence in `kanban/SMA-25.md` and stage artefacts, then transition to Review.

## Contracts And Boundaries

- Config key stays exactly `symphony.autocommitExclude`.
- Values are Git pathspec exclusions, converted to `:(exclude)<value>` by the Bash array.
- Empty config keeps default behavior equivalent to `git add -A -- .`.
- Special path values with spaces or leading colon must be passed as array elements, not shell-concatenated strings.
- PR #23's rejected WORKFLOW/docs symlink policy remains out of scope.
- `scripts/symphony-setup-worktree.sh` skip-worktree behavior remains out of scope except for documentation examples.

## Verification Commands

```bash
pytest tests/test_workspace.py::test_commit_workspace_on_done_autocommit_exclude_survives_base_squash -q
pytest tests/test_workspace.py::test_commit_workspace_on_done_respects_autocommit_exclude_entries -q
pytest tests/test_workspace.py::test_commit_workspace_on_done_default_adds_all_files_without_excludes -q
pytest tests/test_workspace.py -q
test -f docs/features/SMA-25/index.md && grep -q "symphony.autocommitExclude" docs/features/SMA-25/index.md
```

## Rollback And Risk Notes

- If the base-squash fix creates a no-op commit when every post-base change is excluded, stop and move to `Blocked`; the expected behavior needs operator confirmation.
- If Git rejects `:weird:name` as a literal path on the local platform, preserve the space-path test and document the exact Git error before moving forward.
- If touching `src/symphony/workspace.py` requires changes outside `commit_workspace_on_done`, stop and move to `Blocked`; that would exceed the ticket's surgical scope.
