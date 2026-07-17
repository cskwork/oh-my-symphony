# Changelog - 2026-07-17

## Retry-safe terminal auto-merge

- Decision: keep the merge transaction in one Bash subprocess, split its assembly into named preflight, merge, and upstream-sync phases, and invoke the same upstream-sync function for fresh merges and already-integrated/no-staged retries.
- Root cause: `_build_script` exited with `nothing_to_apply` before checking the configured upstream. After a rejected first push, the local target already contained the merge, so a retry returned `ok=True` even though the remote target still pointed at the old commit.
- Compatibility: the public Python signature and result statuses are unchanged. A rejected retry remains `push_failed`; a successful already-integrated retry remains `nothing_to_apply` after exact remote SHA verification.
- Rejected rollback: removing upstream verification would restore false Done success when the remote is stale.
- Rejected reset: resetting the local merge after push failure could discard unrelated commits on a shared target branch.
- Rejected unconditional no-op success: local branch integration alone does not satisfy the configured-upstream invariant.
- Rejected orchestration redesign: fresh and retry synchronization belong in the auto-merge module and do not require workflow-state changes.
- RED proof: `uv run --extra dev pytest -q tests/test_auto_merge.py -k retries_rejected_push_until_upstream_matches` -> `1 failed, 11 deselected in 3.27s`; the second call returned `ok=True` with `nothing_to_apply` while local and remote SHAs differed.
- GREEN proof: focused `12 passed in 17.99s`; full suite `1382 passed, 5 skipped` with `83.81%` coverage; Ruff passed; Pyright reported `0 errors`.

## Blocked workspace restart safety

- Decision: normalize the terminal issue state once in `Orchestrator._startup_terminal_cleanup` and preserve exact `blocked` workspaces before any snapshot or removal. `Blocked` is an operator-owned hold state, so its workspace and diagnostics must survive service restart.
- Root cause: startup cleanup special-cased only `Done`; every other terminal state fell through to auto-commit and workspace removal. A merge-gate failure moved the ticket to `Blocked`, so the next process start deleted the evidence that the failure path had promised to retain.
- Compatibility: existing already-merged and unmerged `Done` behavior is unchanged, while `Cancelled` and other non-`Blocked` terminal states still use the existing snapshot-and-remove path. Public APIs, tracker fields, hooks, and workflow configuration are unchanged.
- Rejected issue-text parsing: `## Merge Gate Failed` comments are tracker-specific and are not guaranteed to round-trip in `Issue.description`.
- Rejected persisted marker: a new retention marker would widen the schema and require stale-marker lifecycle rules for a state policy already represented by `Blocked`.
- Rejected Git-ancestry inference: after a rejected push, the local target is already merged while the remote is stale, so local ancestry cannot represent the operator hold.
- Rejected broad terminal retention: preserving every non-`Done` terminal workspace would unintentionally change `Cancelled` and archive cleanup.
- RED proof: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_startup_terminal_cleanup_preserves_blocked_workspace_across_restarts` -> `1 failed in 0.65s`; the workspace was absent after startup cleanup.
- GREEN proof: the same focused test, tightened to construct a fresh orchestrator and workspace manager for each startup pass, -> `1 passed in 1.45s`; all startup cleanup cases -> `4 passed`; immediate merge-gate preservation -> `1 passed`; owning file -> `185 passed`; auto-merge neighbor -> `13 passed`.
- Build gate proof: full suite `1385 passed, 5 skipped` with `83.92%` coverage; Ruff passed; Pyright reported `0 errors`; Symphony doctor passed; `git diff --check` passed.
