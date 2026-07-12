# DELIVERY-GATE - Before/After Eval for code projects

Use for GREENFIELD, DEBUG, and LEGACY code edits. It records before state, after target, proof commands,
and residual risk.

## Contract

Before/After Eval is required for every GREENFIELD / DEBUG / LEGACY code-project change. Before Build mutates files, the
run vault must contain `GOAL.md` (from `templates/GOAL.md`), an approved `PLAN.md`
(from `templates/PLAN.md`), `QA.md` with `## Before` filled (from `templates/QA.md`), and
`run-state.json` from `templates/run-state.json`. Keep all compact: proof/resume ledgers, not transcripts.

Required fields and where each lives:

- `eval_intent` -> `PLAN.md` `## Intent`: user goal, constraints, tradeoffs, rejected approaches.
- `completion_promise` -> `PLAN.md` `## Intent`: promised outcome, required proof, stop condition, and
  `max_iterations` (default 8).
- `requirement_trace` -> `GOAL.md` `## Success Criteria`: falsifiable checkbox per requirement, each
  naming its verifying check; plus `Backward-trace: clean` in `QA.md` when no diff hunk is orphan scope.
- `before_state` -> `QA.md` `## Before`: observed current behavior before the change.
- `after_target` -> `GOAL.md` `## Spec` + `## Success Criteria`: falsifiable expected behavior after the
  change.
- `reproduction_fidelity` -> `QA.md` `## Reproduction Fidelity`: for DEBUG/prod issues, exact/proxy data
  level, failure-triggering properties, prod-vs-test deltas, residual risk, and post-deploy confirmation
  plan.
- `command_manifest` -> `QA.md` `## Commands`: exact commands used for proof, with source:
  - `frozen_repo` - repo-owned command from AGENTS.md, package scripts, Makefile, CI, docs, or config.
  - `evaluator_owned` - command authored by the evaluator outside either arm's solution.
  - `agent_detected` - command found by the implementation agent; useful, but not enough alone.
- `decision_gates` -> `GOAL.md` `## Decision Gates`: findings classified as `auto-fix`, `no-op`, or
  `ask-user`.
- `after_evidence` -> `QA.md` `## Results` + `## QA`: checklist sentences with command outputs, artifact
  paths, screenshots, DB reads, or API captures proving the after target.
- `residual_risk` -> `QA.md` `## Residual Risk`: what the checks do not prove.
- `run_state` -> `run-state.json`: phase, iteration, plan_approval, gates, blockers, next action, last
  proof command.

## Before State

- **GREENFIELD:** prove the start: feature absent, scaffold empty, acceptance test red, route 404, command
  missing, UI unavailable, or "no implementation exists" plus the first failing acceptance check.
- **DEBUG:** reproduce the live symptom first. Record the failing command/request/screenshot/log/data.
  If exact dev reproduction is unavailable, synthetic/similar data must preserve failure-triggering
  properties; fill `## Reproduction Fidelity`; done is conditional on residual risk plus post-deploy
  confirmation plan.
- **LEGACY / brownfield:** preserve current behavior before changing it. Capture exact API calls,
  screenshots, CLI output, DB rows, or tests for behavior that must not drift.
- **Shared code/state:** in any code mode, capture neighbor characterization baseline snapshots before
  Build (`reference/qa.md`), not only refactors.

No meaningful before proof -> say why and mark `Not proven` until another proof source exists.

## After Eval

Run real verification commands, then compare against before state:

- New behavior works.
- Required old behavior still works: captured neighbor snapshots re-run with no unnamed drift.
- Intended drift is named.
- Unintended drift is fixed or reported.
- Every `GOAL.md` Success Criterion and QA Case is checked, and Backward-trace is clean.
- Browser UI changes include browser evidence from `qa-gate.sh <vault> browser`.
- Data-backed behavior includes read-only DB evidence when available.

Done requires at least one trusted command (`frozen_repo` or `evaluator_owned`) in `QA.md`.
Agent-detected commands can supplement proof, but cannot be the whole proof.

## Decision Gates

- `auto-fix`: mechanical and low-risk; fix and recheck.
- `no-op`: informational; record why no action is needed.
- `ask-user`: changes product behavior, scope, data, security posture, external publishing, or user intent.

Unresolved `ask-user` findings block a final done claim.

## Done

Done means the vault shows: before state (`QA.md`), completion promise fulfilled or blocked (`PLAN.md`),
every `GOAL.md` Success Criterion checked, trusted command outputs/artifacts, resolved decision gates,
residual risk, clean backward trace, DEBUG/prod reproduction fidelity, final `run-state.json`,
`Z-<YYYY-MM-DD>.md` written with run branch and completion timestamp, changelog alternatives, and
commit gate passed (`## Commit gate`).

## Commit gate

Commit or merge into the target/integration branch only when proof is green and the user has accepted.
Block while any holds: REAL tests or request/docs not satisfied; QA verdict FAIL or PARTIAL (incomplete);
an unchecked Success Criterion or QA Case in `GOAL.md` (including surfaced criteria); `PLAN.md` approval
still pending; a missing or non-clean `Backward-trace` in `QA.md` (scope-creep orphan); an unresolved
`ask-user` decision gate; a missing `Z-*.md` completion marker (or one written while a criterion is still
unchecked); non-exact reproduction fidelity without residual risk and post-deploy confirmation plan; or
fulfillment is uncertain.

Blocked is fix-first: the role-loop resolves it (fix the red, finish QA, tick the criterion). Ask the
user about the requirement only when it is requirement-level (ambiguous or unmet), genuinely uncertain, or
the critic->fixer loop hit its cap (`reference/role-loop.md`); route via `reference/interview.md`. Never
commit on an assumption - resolve it or get explicit acceptance.

Backstop: `bash templates/commit-gate.sh <vault> <browser|cli|none>` must exit 0 before commit/merge.
Never edit the gate to pass.
