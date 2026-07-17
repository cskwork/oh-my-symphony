# QA report - terminal auto-merge rejection and recovery

- Date: 2026-07-17
- Target: disposable local Symphony/Codex/Git fixture at `/private/tmp/symphony-e2e-auto-merge-20260717-g6CdGQ`
- Driver: Symphony CLI, HTTP state API, Git CLI, and a real Codex app-server worker
- Comparison: functional
- Verdict: FAIL
- Actions used: 0/100 browser actions

The patch-specific retry behavior passes. The overall verdict is still FAIL because M4 is a Must scenario and restart removed the merge-gate-blocked workspace before operator recovery.

## Impact coverage

- M1 harness/preflight: PASS; the disposable fixture passed doctor and left the original checkout/board unchanged (`qa/harness-and-preflight.md`).
- M2 real worker lifecycle: PASS; the Codex worker traversed In Progress, Verify, Learn, and Done with contract evidence (`qa/failure-gate.md`).
- M3 rejected-push gate: PASS; `push_failed` moved the ticket to Blocked, kept the remote stale, created one local merge, and preserved the workspace immediately (`qa/failure-gate.md`).
- M4 restart/retry: FAIL overall. The existing merge was later pushed and verified without duplication, but startup first deleted the Blocked workspace and its ignored lifecycle artifact (`qa/restart-probe.md`, `qa/recovery.md`).
- M5 final consistency: PASS; refs matched, one merge remained, the API was idle, Git integrity passed, and normal completion cleanup finished (`qa/recovery.md`).
- S1 configured-empty capture: PASS at library level; the staged-empty no-op synchronized the upstream (`qa/recovery.md`).

### Changed-symbol reconciliation

| Changed symbol | Consumer path | Independent coverage |
| --- | --- | --- |
| `_build_script` | `auto_merge_on_done_best_effort` and the direct conflict test | Full `tests/test_auto_merge.py`; generated-script conflict ordering; real M3/recovery lifecycle |
| `_build_preflight_phase` | `_build_script` | Both retry parameter arms plus existing target/safety tests |
| `_build_target_preflight_block` | `_build_preflight_phase` | Missing branch, empty target, excluded path, and real fixture preflight |
| `_build_merge_safety_block` | `_build_preflight_phase` | Dirty overlap, committed conflict precedence, and the real non-overlapping-host warning in M3 |
| `_build_nothing_to_apply_block` | `_build_preflight_phase` | No-capture retry unit arm and real `auto_merge_nothing_to_apply` recovery |
| `_build_merge_phase` | `_build_script` | Fresh merge, rejected push, capture success, empty-capture staged no-op, and M3 |
| `_build_capture_block` | `_build_merge_phase` | Existing non-empty capture test and new empty-capture retry arm/S1 replay |
| `_build_upstream_sync_block` | `_build_script`; invoked after fresh merge and both no-op paths | Fresh push, rejection, remote mismatch, repeated rejection, recovery, exact remote readback |
| `_git_output` (test helper) | New retry regression only | Both parameter arms passed |
| `test_retry_after_push_failure_retries_rejected_push_until_upstream_matches` | Pytest collection | `no-capture-preflight-noop` and `empty-capture-staged-noop` both passed |

The unchanged public consumer is `Orchestrator._auto_merge_done_gate_or_block`, reached from worker exit and reconciliation. Its immediate failure behavior is covered by the targeted orchestration test and M2/M3. The downstream restart consumer is where M4 fails.

## What worked

- A real rejected target push returned `push_failed`; local `dev` advanced to merge `571950551e1dccb56f702e1221ab63383f8c7a38`, remote `dev` remained stale, and the merge count increased by exactly one (`qa/failure-gate.md`).
- Repeating the rejected retry did not create a second merge. After removing the rejection, the no-op retry pushed and verified the existing merge; local and remote both resolved to `571950551e1dccb56f702e1221ab63383f8c7a38` (`qa/recovery.md`).
- Both generated no-op branches pass independently: no configured capture exits through `SKIP: nothing differs`; an empty configured capture exits through `SKIP: nothing staged after merge`.
- The final ticket reached Done, the completed workspace was cleaned, the API reported no running/retrying workers, and only the main worktree remained.

## What didn't

- M4 restart durability -> FAIL. Expected: a workspace explicitly preserved after a merge-gate failure remains available while the ticket is Blocked. Actual: startup ran auto-commit and `before_remove`, removed the workspace, and lost `docs/E2E-AMR-001/qa/lifecycle.log` before the operator resumed the ticket (`qa/restart-probe.md`).

## What I discovered

- The defect is outside the two-file patch but inside the accepted lifecycle. `_block_done_ticket_for_merge_gate` changes the failed ticket from Done to Blocked and records “workspace preserved.” On startup, `_startup_terminal_cleanup` special-cases only state `Done`; every other terminal state, including this merge-gate `Blocked`, falls through to auto-commit and removal.
- Existing startup-cleanup tests cover already-merged Done and unmerged Done workspaces. They do not cover a merge-gate-blocked workspace across restart, which is why all three focused orchestration tests pass while the real M4 lifecycle fails.
- Tracked feature evidence survived on the branch, so retry recovery succeeded. That does not recover ignored, untracked, or otherwise workspace-only diagnostics.

## Independent commands

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_auto_merge.py` | PASS: 13 passed in 23.78s |
| `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_auto_merge_failure_blocks_done_ticket_and_preserves_workspace tests/test_orchestrator_dispatch.py::test_startup_terminal_cleanup_skips_done_workspace_when_branch_already_merged tests/test_orchestrator_dispatch.py::test_startup_terminal_cleanup_preserves_unmerged_done_workspace_without_replay` | PASS: 3 passed in 1.70s |
| `.venv/bin/python -m ruff check src/symphony/utils/auto_merge.py tests/test_auto_merge.py` | PASS: All checks passed |
| `.venv/bin/python -m pyright --pythonpath .venv/bin/python` | PASS: 0 errors, 0 warnings, 0 informations |
| `.venv/bin/symphony doctor ./WORKFLOW.md` | PASS: all reported preflight checks passed |
| `git diff --check` | PASS: exit 0, no output |
| `git -C /private/tmp/symphony-e2e-auto-merge-20260717-g6CdGQ/repo rev-parse dev origin/dev symphony/E2E-AMR-001` plus merge-count/worktree readback | PASS: target and upstream `5719505...`, feature `88f35e3...`, merge delta `1`, main worktree only |
| `bash /Users/danny/Documents/PARA/Resource/supergoal-skill/templates/qa-gate.sh docs/changelog/2026-07/17-qa-auto-merge-orchestrator-e2e cli` | PASS: `QA GATE PASS` |
| `bash /Users/danny/Documents/PARA/Resource/supergoal-skill/templates/qa-only-gate.sh docs/changelog/2026-07/17-qa-auto-merge-orchestrator-e2e cli` | PASS: `QA-ONLY GATE PASS` after adding explicit shard outcome anchors and labeling the skipped DB surface as `Database` |

The first `.venv/bin/python -m pyright` invocation resolved a different interpreter environment and reported 24 missing-import errors plus 3 warnings for installed optional packages. Selecting the disposable venv explicitly made the full configured Pyright run green; this was a command-environment failure, not a product failure.

## Reproduction notes

- M4 - Blocked workspace is removed on restart
  1. Target: disposable file-board Symphony service with a local bare origin; no account or credentials.
  2. Starting state: reject the target push after the worker reaches Done. Confirm ticket `E2E-AMR-001` is Blocked, local target contains one merge, remote target is stale, and the workspace plus `docs/E2E-AMR-001/qa/lifecycle.log` exist.
  3. Stop the service cleanly while the ticket remains Blocked, then start the same workflow without first moving the ticket to an active state.
  4. Expected: the failed workspace remains for operator diagnosis and later recovery.
  5. Actual: startup invoked `auto_commit_start`, then `before_remove` at `2026-07-17T09:09:56Z`; the workspace and ignored lifecycle log disappeared while the card remained Blocked.
  Evidence: `qa/restart-probe.md`; persisted hook record `/private/tmp/symphony-e2e-auto-merge-20260717-g6CdGQ/workspaces/.symphony-workspace-hook-output/E2E-AMR-001/20260717T090956919531Z-before_remove.json`.

## Not covered

- Hosted Git authentication and branch-protection policy -> not covered because the fixture used a local bare remote; risk: provider-specific rejection/readback behavior may differ.
- Concurrent Done tickets -> not covered because the run isolated one failure transaction; risk: target-branch races and lock behavior remain unproven.
- Browser/TUI presentation -> not covered because this is a CLI/library lifecycle; risk is limited to how the failure is presented, not the verified Git transaction.
- DB evidence: Not covered because no database participates in this path; no data-integrity risk applies.
- Pre-existing rare script error arms (not-a-repo, detached HEAD, checkout/commit failure, timeout, unknown return code) were inspected but not independently re-exercised; risk: this refactor could still hide a regression outside the targeted retry transaction.
- S1 was replayed through the library after the service run, not through a second real worker lifecycle; the generated staged-empty synchronization is proven, but worker-level capture wiring was not repeated.
- A full repository suite was not run: the diff is confined to the auto-merge utility and its owning tests, and the exact orchestrator consumers plus static/type/workflow gates were green.

## Harness-only failures

- Nested Codex could not initialize its SQLite state under the outer filesystem sandbox; the disposable service was rerun in its permitted environment.
- The fixture initially selected an unsupported model; only the disposable workflow changed to a supported local model.
- The first fixture commit included the board symlink; the merge gate correctly rejected it, and the disposable autocommit exclusion was corrected before the product scenario.
- The initial Pyright command selected the wrong analysis interpreter; the explicit-venv rerun passed with zero diagnostics.
- The QA-ONLY wrapper initially rejected the populated matrix's table form and interpreted `DB: skipped` as a database-use declaration. The conductor added explicit shard outcome anchors and relabeled the skipped surface as `Database`; the final wrapper gate passed without changing any scenario result or runtime evidence.

None of these harness failures explains or weakens the M4 workspace-loss reproduction.

## How to re-run

- Patch regression: `.venv/bin/python -m pytest -q tests/test_auto_merge.py`.
- Orchestrator boundary: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_auto_merge_failure_blocks_done_ticket_and_preserves_workspace tests/test_orchestrator_dispatch.py::test_startup_terminal_cleanup_skips_done_workspace_when_branch_already_merged tests/test_orchestrator_dispatch.py::test_startup_terminal_cleanup_preserves_unmerged_done_workspace_without_replay`.
- Static/type/workflow gates: `.venv/bin/python -m ruff check src/symphony/utils/auto_merge.py tests/test_auto_merge.py`; `.venv/bin/python -m pyright --pythonpath .venv/bin/python`; `.venv/bin/symphony doctor ./WORKFLOW.md`; `git diff --check`.
- Evidence gates: `bash /Users/danny/Documents/PARA/Resource/supergoal-skill/templates/qa-gate.sh docs/changelog/2026-07/17-qa-auto-merge-orchestrator-e2e cli` and `bash /Users/danny/Documents/PARA/Resource/supergoal-skill/templates/qa-only-gate.sh docs/changelog/2026-07/17-qa-auto-merge-orchestrator-e2e cli`; both pass.
- Live lifecycle steps and frozen evidence: `qa/shards/cli-lifecycle.md`, `qa/failure-gate.md`, `qa/restart-probe.md`, and `qa/recovery.md`.
- No reusable `.domain-agent/qa/` suite or single-command live E2E script was persisted in this run; a future regression should add a startup test for merge-gate `Blocked` workspace retention before repeating the real worker lifecycle.
