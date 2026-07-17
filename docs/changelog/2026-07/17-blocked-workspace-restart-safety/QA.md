# QA - Blocked workspace restart safety

All testing results as succinct plain-language checklist sentences. Evidence lives in `qa/`.

- Verdict: PASS

## Before

- [x] Exact real-worker E2E reproduced the defect: `Blocked` workspace existed before restart, then startup ran auto-commit and `before_remove`, deleted the directory, and lost an ignored lifecycle diagnostic while the card remained `Blocked` - evidence: `qa/as-is-restart.md`

## Results

- [x] New focused test was RED on the current product code: `1 failed in 0.65s`; the first startup pass deleted the mixed-case `Blocked` workspace and its diagnostic (agent_detected)
- [x] Minimal fix is independently GREEN and sibling terminal behavior remains stable: Blocked selector `2 passed`, startup subset `4 passed`, immediate merge-gate preservation `1 passed`, owning file `185 passed`, and auto-merge neighbor `13 passed` (evaluator_owned)
- [x] Fresh real-worker restart/recovery lifecycle passes: rejected target push returned `push_failed`; Blocked restart retained the registered worktree and ignored diagnostic with one preservation event, zero pre-recovery ticket auto-commits, and zero pre-recovery `before_remove` events; recovery reused the same single merge and completed cleanup - evidence under `qa/` (evaluator_owned)
- [x] Full trusted gates pass with no unnamed drift: `1385 passed, 5 skipped`, `83.92%` coverage, Ruff clean, Pyright `0 errors`, doctor PASS, `git diff --check` clean, and the CLI QA gate exits `0` (evaluator_owned)
- [x] Retained-fixture readback is consistent: local `dev`, `origin/dev`, and remote `refs/heads/dev` equal `670c1c07141508f262047f7bf6f82fd2fdd92c27`; merge delta is `1`; feature `b6ec063acee74e41726b4378b58249745f5d8716` is an ancestor; ticket is `Done`; ticket workspace is absent; API was idle; `git fsck` is clean; shutdown completed; port `19117` is stopped (evaluator_owned)
- [x] Harness-only failures are separated from product proof: the initially tracked board fixture and sandboxed nested-Codex startup both stopped before the successful real-worker lifecycle and do not explain its result (evaluator_owned)

Backward-trace: clean

Codebase-memory reports `Orchestrator.start` as the direct production caller of `_startup_terminal_cleanup`. The final tracked diff contains the startup guard plus the preceding auto-merge retry patch: `_startup_terminal_cleanup` is covered through direct state-policy tests and the public restart lifecycle; `_build_script` and its seven phase helpers are covered through all 13 auto-merge tests and the real rejection/recovery consumer path. Every tracked hunk maps to the approved two run vaults; no consumer or scope extension is orphaned.

```text
GATE.owner=Orchestrator._startup_terminal_cleanup
GATE.alt_repro=fresh public service restart while ticket remains Blocked: pass
GATE.conformance=bare None returns preserved; Blocked and Done state paths use structured log plus continue, other terminals retain snapshot/remove
```

## Commands

| Command | Source | Proves |
|---|---|---|
| `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py -k 'startup_terminal_cleanup'` | evaluator_owned | Blocked retention plus Done and sibling terminal policy; `4 passed` |
| `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py -k 'startup_terminal_cleanup and blocked'` | evaluator_owned | Exact case-insensitive Blocked retention and idempotence; `2 passed` |
| `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_auto_merge_failure_blocks_done_ticket_and_preserves_workspace` | evaluator_owned | Immediate merge-gate failure preservation; `1 passed` |
| `.venv/bin/python -m pytest -q tests/test_auto_merge.py` | evaluator_owned | Original retry-safe merge behavior does not regress; `13 passed` |
| `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py` | evaluator_owned | Owning orchestrator surface; `185 passed` |
| `.venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80` | evaluator_owned | Full suite `1385 passed, 5 skipped`; coverage `83.92%` |
| `.venv/bin/python -m ruff check src tests` | evaluator_owned | Static style and defect checks; all checks passed |
| `PATH="$PWD/.venv/bin:$PATH" .venv/bin/pyright --pythonpath .venv/bin/python` | evaluator_owned | Type correctness; `0 errors, 0 warnings, 0 informations` |
| `.venv/bin/symphony doctor ./WORKFLOW.md` | evaluator_owned | Workflow health; all checks PASS |
| `git diff --check` | evaluator_owned | Patch formatting/integrity; exit `0` |
| Fresh disposable Symphony/Codex/Git restart lifecycle | evaluator_owned | Exact rejected-push, restart durability, retry idempotency, and final cleanup |
| `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_startup_terminal_cleanup_preserves_blocked_workspace_across_restarts` | agent_detected | Run-to-prove: two startup passes preserve a mixed-case Blocked workspace and workspace-only diagnostic; RED `1 failed`, GREEN `1 passed` |
| `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py::test_startup_terminal_cleanup_removes_cancelled_but_preserves_blocked` | agent_detected | Run-to-prove: Cancelled snapshots and removes while Blocked remains; `1 passed` |
| `git fsck --no-dangling --no-progress` plus ref/worktree/port readback in the retained fixture | evaluator_owned | Final Git integrity, exact refs, one merge, Done cleanup, and stopped service |
| `bash /Users/danny/Documents/PARA/Resource/supergoal-skill/templates/qa-gate.sh docs/changelog/2026-07/17-blocked-workspace-restart-safety cli` | evaluator_owned | CLI evidence structure; `QA GATE PASS` |

## QA

Tool: CLI
Action count: n/a
Fixture: `/private/tmp/symphony-e2e-blocked-restart-fix-XyUtkh`
Port: `19117`

- PASS C1: doctor passed and a real Codex worker completed In Progress -> Verify -> Learn -> Done with branch-local work plus tracked and ignored QA artefacts.
- PASS C2: rejected target push produced `push_failed`, Blocked, stale origin, one local merge, and an intact workspace-only lifecycle log.
- PASS C3: clean restart while still Blocked preserved the same worktree and diagnostic, logged `startup_terminal_cleanup_preserved_blocked_workspace`, and emitted no ticket auto-commit or `before_remove` event.
- PASS C4: recovery reused the existing merge; local/remote refs matched, merge delta stayed one, the feature branch remained, Done removed the workspace, and the API was idle.
- PASS C5: service shutdown completed, port stopped, Git integrity passed, and the product checkouts/boards remained unchanged outside assigned vault evidence.
- Evidence: `qa/cli-preflight-and-worker.md`, `qa/rejected-push-and-restart.md`, `qa/recovery-and-isolation.md`, `qa/shards/cli-restart-lifecycle.md`, and `qa/qa-gate.txt`.
- Teardown: service stopped cleanly; disposable fixture intentionally retained for auditor readback.
- CLI QA gate: PASS (exit `0`; `== QA GATE PASS ==`).

## Reproduction Fidelity

- Fidelity level: exact
- Residual risk from data gap: hosted Git authentication and branch-protection policy are not part of the workspace-retention owner; a local bare remote exactly exercises rejection, restart, and SHA synchronization.
- Post-deploy confirmation plan: no deployment requested; rerun `symphony doctor` and inspect the first real Blocked restart before release.

## Residual Risk

- Hosted-provider authentication and branch-protection messages remain unproven; the local bare remote proves the product's rejection, retry, and exact-ref invariant but not provider-specific policy text.
- Concurrent terminal-ticket startup cleanup remains unproven; this isolated one-ticket fixture and the full suite found no shared-state regression, but target-branch race behavior is outside the requested retention owner.
- Follow-up: OpenCode terminal heartbeat and malformed scorecard false-green findings remain separate slices.
