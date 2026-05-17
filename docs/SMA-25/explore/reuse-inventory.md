# Reuse Inventory — SMA-25 Explore

| candidate | path:line | reuse_fit (0-1) | adapt_cost (low/med/high) | notes |
|---|---:|---:|---|---|
| `commit_workspace_on_done` real bash hook | `src/symphony/workspace.py:299` | 1.0 | low | Target under test; tests should call this async function so Git and Bash execute for real. |
| `_git` helper | `tests/test_workspace.py:27` | 1.0 | low | Existing subprocess wrapper isolates Git identity and raises on command failure. |
| `_git_id_env` helper | `tests/test_workspace.py:365` | 1.0 | low | Prevents global Git config, signing, and user identity from changing test behavior. |
| Existing scoped auto-commit regression | `tests/test_workspace.py:418` | 0.8 | low | Good fixture shape for parent repo + tree assertions; can be adapted for exclude pathspec tests. |
| Base squash regression | `tests/test_workspace.py:625` | 0.8 | med | Covers `symphony.basesha` + wip commits; useful for the stricter exclude-leak test. |
| Worktree setup script config pattern | `scripts/symphony-setup-worktree.sh:53` | 0.6 | low | Shows how after_create writes Symphony-owned Git config; usage docs can mirror this with `symphony.autocommitExclude`. |
| Existing feature docs template | `docs/features/SMA-25/index.md` | 0.0 | low | No existing file; create a small new As-Is/To-Be report. |
