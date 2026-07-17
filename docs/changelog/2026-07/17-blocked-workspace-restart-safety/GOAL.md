# GOAL - Blocked workspace restart safety

Single source of "done". Only the verifier ticks a box; unticking needs regression evidence.
Never delete or reword an unmet criterion - append. Mid-run discovered musts are APPENDED as new
unchecked criteria tagged `(surfaced: ...)`. Ambiguous/product-changing candidates go to
`## Decision Gates` as `ask-user`, not into criteria.

## Original Request

> continue with next fix

## Spec

Fix the restart durability defect found by the real auto-merge rejection E2E. When terminal
auto-merge fails, Symphony moves the ticket to `Blocked` and promises that its workspace is
preserved for operator inspection and retry. A later process start must honor that promise: an
existing workspace for an exact, case-insensitive `Blocked` terminal state remains untouched,
including ignored and untracked diagnostics. Existing `Done` merge-gate handling and cleanup for
other terminal states must remain unchanged. Keep the change inside startup cleanup; do not parse
tracker comments, add persistence metadata, redesign workflow states, or alter the public API.

The real-world model is an operator hold: `Blocked` means the automation cannot safely decide the
next action, so restart recovery must not destroy the evidence the operator needs to decide it.

## Success Criteria

Each item is falsifiable and names its verification method.

- [x] A startup pass leaves an existing `Blocked` workspace and a workspace-only diagnostic intact - verify: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py -k 'startup_terminal_cleanup and blocked'`
- [x] Repeated startup cleanup remains idempotent for the same `Blocked` workspace - verify: the focused restart regression in `tests/test_orchestrator_dispatch.py`
- [x] Existing already-merged and unmerged `Done` startup behavior remains green - verify: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py -k 'startup_terminal_cleanup'`
- [x] A non-`Blocked` terminal workspace still follows the existing snapshot-and-remove policy - verify: a mixed-state or sibling regression in `tests/test_orchestrator_dispatch.py`
- [x] A fresh real Codex/Git lifecycle survives rejected push -> `Blocked` -> service restart without losing the workspace, then recovers without a duplicate merge - verify: fresh disposable Symphony CLI E2E evidence under `qa/`
- [x] The owning tests, full suite, lint, type, workflow health, and diff gates pass - verify: commands frozen in `PLAN.md`
- [x] The root cause, state-semantics decision, rejected alternatives, and exact proof are recorded without touching unrelated work - verify: `git diff --check`, `git status --short`, and backward-trace review

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | auto-fix | resolved | The prior E2E proved that merge-gate failure changes the ticket to `Blocked`, then startup treats every non-`Done` terminal as disposable. | Preserve exact case-insensitive `Blocked` workspaces during startup; keep all sibling terminal semantics unchanged. | Focused mixed-state test plus fresh real restart E2E. |
| d2 | auto-fix | resolved | A tracker-note discriminator is not portable, while a durable workspace marker adds schema and stale-marker lifecycle. | Use the existing workflow state as the owner signal; reject comment parsing and new metadata. | Independent QA review of cross-tracker behavior. |
| d3 | auto-fix | resolved | The defect belongs to the unaccepted auto-merge E2E delivery, which already has an isolated worktree with required uncommitted changes. | Reuse that worktree and keep the original `dev` checkout untouched. | Final status and diff scope check. |
