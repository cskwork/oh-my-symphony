# PLAN - Blocked workspace restart safety

Frozen after approval. A fresh-context implementer reads ONLY this file (plus the latest
`R-LOOP.md` section on re-entry) and builds it. Changes after approval append a dated `## Amendment`.

## Approval

- Status: approved-by-user
- Record: 2026-07-17T09:45:44Z; user: `continue with next fix`, immediately after the prior delivery identified preserving merge-gate-blocked workspaces across restart as the next fix.

## Intent

- Goal: make the merge-gate promise "workspace preserved" durable across process restart by treating
  `Blocked` as an operator-owned hold state in startup cleanup.
- Constraints: source/integration ref is `dev@ee04e5dcc2f188f6686127be537039e5b197006a`; work only in
  `/private/tmp/symphony-auto-merge-retry-safety-20260717` on
  `codex/auto-merge-retry-safety-20260717`; preserve its existing auto-merge patch and evidence;
  preserve the original checkout's two untracked July 12 documents; no dependency, API, workflow,
  release, commit, merge, or push changes.
- Design: normalize `issue.state` once inside `Orchestrator._startup_terminal_cleanup`; before any
  auto-commit or removal, log and continue for exact `blocked`; reuse the normalized value for the
  existing `done` branch. Keep the guard inline because it is one state-policy decision in the
  cleanup owner; do not introduce a pass-through helper or refactor the surrounding 72-line method.
- Tradeoff: all exact `Blocked` terminal workspaces, not only auto-merge failures, survive restart.
  This is intentional: `Blocked` consistently means human/operator intervention, and preserving
  diagnostics is safer than silently pruning them. Operators resolve retention by moving the ticket
  to its next terminal/active state.
- Rejected: parse `## Merge Gate Failed` from issue text (comments are tracker-specific and may not
  round-trip in `Issue.description`); persist a new marker (wider schema and stale-marker cleanup);
  infer from Git ancestry (the local target is already merged while the remote is stale); preserve
  every non-`Done` terminal state (would change `Cancelled`/archive cleanup); redesign the terminal
  state model (outside this root-cause fix).
- Completion promise: one behavior test fails on the current code, the minimal state guard makes it
  pass, a sibling terminal baseline proves selectivity, the full trusted gates pass, and a fresh real
  rejected-push/restart/recovery E2E confirms workspace durability and no duplicate merge. Stop at
  `max_iterations=3`, or earlier on a requirement-level blocker.

## Hypothesis ledger

| ID | Hypothesis | Evidence | Status |
|---|---|---|---|
| H1 | Startup cleanup deletes the merge-gate workspace because only `Done` is special-cased and every other terminal falls through to commit/remove. | Real E2E restart log plus `core.py:6004-6075`; graph trace reaches `WorkspaceManager.remove`. | confirmed root cause |
| H2 | A workspace hook or external cleanup independently deletes the directory. | The hook record follows the orchestrator's explicit `remove(path)` call; it is an effect, not the owner. | rejected |
| H3 | Tracker state is stale or incorrectly rehydrated on restart. | The API and board still reported `Blocked`; cleanup received that terminal issue and deterministically followed the non-`Done` branch. | rejected |

## Steps

1. In `tests/test_orchestrator_dispatch.py`, add one vertical regression for observable startup
   behavior using a real temporary workspace: a terminal issue in mixed-case `Blocked` retains a
   workspace-only diagnostic after cleanup. Run only that test and record the expected RED deletion.
2. In `src/symphony/orchestrator/core.py::Orchestrator._startup_terminal_cleanup`, add the minimal
   normalized-state guard before the existing `Done` and non-`Done` cleanup paths. Emit a structured
   `startup_terminal_cleanup_preserved_blocked_workspace` warning with identifier and path. Run the
   new test to GREEN before adding any second case.
3. Add one selective sibling case through the same startup boundary: a `Cancelled` workspace still
   uses the existing snapshot/remove path while `Blocked` remains. Keep the test behavior-focused and
   avoid assertions about private helper call order unless needed to isolate a boundary. Re-run all
   startup-terminal-cleanup tests and the immediate auto-merge failure preservation test.
4. Append the root cause, state-policy decision, alternatives, RED/GREEN evidence, and compatibility
   boundary to `docs/changelog/changelog-2026-07-17.md`. Do not reformat prior entries.
5. Run the owning orchestration file, existing auto-merge tests, full repository coverage gate, Ruff,
   explicit-venv Pyright, Symphony doctor, diff check, and backward trace. Before accepting the diff,
   print the DEBUG hidden-contract gate as exactly three lines:
   `GATE.owner=Orchestrator._startup_terminal_cleanup`,
   `GATE.alt_repro=<fresh service restart lifecycle>: pass`, and
   `GATE.conformance=<return and sibling-path audit result>`.
6. Hand the frozen plan and diff to a fresh `qa-auditor`. It must build a fresh disposable file-board,
   local bare remote, and real Codex worker lifecycle: reject target push, prove `Blocked` plus one
   local merge and stale remote, restart while still `Blocked`, prove workspace-only evidence remains,
   then allow recovery and prove exact local/remote SHA equality, one merge total, Done cleanup, and
   idle final state. Store independent evidence under this vault's `qa/` and loop via `R-LOOP.md` only
   for grounded failures.

## Acceptance checklist

- [ ] A startup pass leaves an existing `Blocked` workspace and a workspace-only diagnostic intact.
- [ ] Repeated startup cleanup remains idempotent for the same `Blocked` workspace.
- [ ] Existing already-merged and unmerged `Done` startup behavior remains green.
- [ ] A non-`Blocked` terminal workspace still follows the existing snapshot-and-remove policy.
- [ ] A fresh real Codex/Git lifecycle survives rejected push -> `Blocked` -> service restart without losing the workspace, then recovers without a duplicate merge.
- [ ] The owning tests, full suite, lint, type, workflow health, and diff gates pass.
- [ ] The root cause, state-semantics decision, rejected alternatives, and exact proof are recorded without touching unrelated work.

## Tools & Skills

- Process: `supergoal` DEBUG role loop plus `tdd` one-test-at-a-time red-green-refactor.
- Discovery: codebase-memory `search_graph`, `trace_path`, and `get_code_snippet`; `rg` only for
  literals/config/non-code evidence.
- Focused: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py -k 'startup_terminal_cleanup'`.
- Neighbor: `.venv/bin/python -m pytest -q tests/test_auto_merge.py`.
- Owning file: `.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py`.
- Full: `.venv/bin/python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80`.
- Static: `.venv/bin/python -m ruff check src tests`; `PATH="$PWD/.venv/bin:$PATH" .venv/bin/pyright --pythonpath .venv/bin/python`.
- Workflow/diff: `.venv/bin/symphony doctor ./WORKFLOW.md`; `git diff --check`; `git status --short`; codebase-memory backward trace.
- E2E: Symphony CLI and state API, Git CLI against a disposable bare remote, and a real Codex
  app-server worker. No browser or database participates in this CLI lifecycle.

## Verification strategy

- Before proof: the prior exact E2E shows `Blocked` and workspace present before restart, then
  `auto_commit_start`/`before_remove` and missing workspace after restart. The new focused test must
  fail for the same deletion before production code changes.
- Step -> GOAL criterion: step 1 -> criteria 1-2; steps 2-3 -> criteria 1-4; step 4 -> criterion 7;
  step 5 -> criteria 3-4 and 6-7; step 6 -> criterion 5 and independent confirmation of 1-7.
- Trusted commands: focused/owning/full Pytest, Ruff, Pyright, doctor, and diff commands above are
  `frozen_repo`; the fresh real lifecycle and backward-trace audit are `evaluator_owned`.
- Regression ledger: preserve the existing 13 auto-merge cases; already-merged and unmerged `Done`
  startup cases; immediate merge-gate failure preservation; `Cancelled` cleanup; full-suite coverage.

## Grounding ledger

- Who owns the loss? -> graph search/trace identifies `_startup_terminal_cleanup` as the startup
  caller of `WorkspaceManager.remove`; its public caller is `Orchestrator.start`.
- Why `Blocked` rather than merge-note parsing? -> `_block_done_ticket_for_merge_gate` hardcodes
  `Blocked`, while issue comments are not a stable cross-tracker field on `Issue`.
- Why not Git ancestry? -> after rejected push, local target ancestry is already correct and remote
  state is stale; ancestry cannot encode the operator-evidence retention promise.
- What remains compatible? -> exact `Done` handling, merge retry logic, `Cancelled`/other terminal
  snapshot-and-remove, public signatures, tracker APIs, workspace hooks, and configured workflow.
- Isolation -> original `dev` checkout remains clean except its two pre-existing untracked July 12
  documents; this run continues the isolated unaccepted delivery where the E2E surfaced the defect.
