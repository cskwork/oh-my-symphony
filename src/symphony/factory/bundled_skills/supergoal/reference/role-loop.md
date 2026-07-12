# ROLE-LOOP - the default loop for GREENFIELD / DEBUG / LEGACY

Use when the user invokes `supergoal` for GREENFIELD / DEBUG / LEGACY feature, bug, or refactor
work. Once invoked, use this loop; do not downgrade to an inline shortcut.

The mandatory core is Build -> Improve full spec -> Improve edge cases -> Mandatory Adversarial Review ->
Exact Verify/QA. Historical contract string: Build -> Improve full spec -> Improve edge cases -> Final
Verify. After Build, one fresh-context improver compares the request/docs with current behavior; another
attacks edge cases; a separate fresh-context adversarial review always tries to disprove completeness;
then Exact Verify/QA runs the real proof layer. Exact verification outranks reviewer approval.
Critic/Fixer is not part of the default loop.

Use it when the task is under-specified, latent-correctness-heavy, security/edge/domain-rule-sensitive, or
Final Verify exposes an unexplained requirement gap. Do not use it when the spec is explicit, behavior is
already stated, the harness is single-process/context-limited, or the eval compares equal-compute vanilla
unless critic/fixer is the tested lever. Critic/Fixer is optional gated escalation: a critic classifies
inferred behavior, then a fixer clears accepted reds. Production/domain behavior-changing ambiguity needs
user feedback. Generic no-user coding ambiguity uses the most conservative, reversible default and
records the choice.

## Run setup - before any file mutation

For any GREENFIELD / DEBUG / LEGACY code edit, first resolve the source/base branch and
target/integration branch. Prefer repo policy or user-provided refs; ask if either is ambiguous. Then
verify both refs exist and create a branch-scoped run worktree:

```bash
git worktree add -b <run_branch> <worktree_path> <source/base branch>
```

Run Build, Improve, Mandatory Adversarial Review, Exact Verify/QA, optional Critic/Fixer, tests, and vault
writes inside that worktree; never edit the original checkout. Treat dirty original-checkout files as user
work. Commit or merge only
into the verified target/integration branch after green verification, user acceptance, and only once the
commit gate passes (`reference/delivery-gate.md`; `bash templates/commit-gate.sh <vault> <browser|cli|none>`).
Non-green blocks commit: resolve in-loop; escalate only when stuck.

Before any file mutation, create the run vault's `GOAL.md` from `templates/GOAL.md`, `PLAN.md` from
`templates/PLAN.md`, `QA.md` from `templates/QA.md`, and `run-state.json` from
`templates/run-state.json`, and start the Before/After Eval (`reference/delivery-gate.md`):

- `GOAL.md` is written FIRST: `## Original Request` quotes the user's prompt verbatim; `## Spec` refines
  it into a detailed spec; `## Success Criteria` seeds falsifiable checkboxes, each naming its
  verification method; web apps also seed `## QA Cases` (browser scenarios for playwright-cli);
- GREENFIELD scope gate: if the new app/tool request is broad, foggy, roadmap-shaped, or clearly
  multi-session, keep the mode `GREENFIELD` and use `reference/wayfinder.md` inside this same run vault
  before freezing `PLAN.md`; write `wayfinder/map.md`, write vertical tickets under
  `wayfinder/tickets/`, name the first unblocked frontier ticket, and carry only that ticket's
  acceptance checks into `GOAL.md` / `PLAN.md`; do not build sibling tickets in the same context;
- record eval intent in `PLAN.md` `## Intent`: user goal, constraints, tradeoffs, rejected approaches;
- record the completion promise in `PLAN.md` `## Intent`: promised outcome, required proof, stop
  condition, and `max_iterations` (default 8);
- `PLAN.md` `## Steps` + `## Tools & Skills` must be self-sufficient: a fresh-context implementer reads
  ONLY `PLAN.md` and builds it;
- **Plan approval gate (blocking, unlike the interview's confirms):** after `PLAN.md` freezes and before
  Build is dispatched — interactive session: present `PLAN.md` and WAIT for the user's explicit OK;
  record it in `## Approval` and set `run-state.json` `plan_approval: "user"`. Autonomous run
  (harness-eval arm, scheduled/background, pre-authorized autonomy): set `Status: auto-approved` with the
  reason and `plan_approval: "auto"`, then proceed;
- record the before state in `QA.md` `## Before`: absent feature/red acceptance check for GREENFIELD,
  reproduced symptom for DEBUG, preserve-baseline capture for LEGACY/brownfield, plus neighbor
  characterization baseline for any shared code/state change;
- DEBUG first tries exact live reproduction. If exact reproduction is unavailable, preserve the
  failure-triggering properties in synthetic/similar data and fill `QA.md` `## Reproduction Fidelity` with
  fidelity level, data source, prod-vs-test deltas, residual risk, and post-deploy confirmation plan;
- record the after target and the command manifest (`QA.md` `## Commands`) from repo-owned or
  evaluator-owned proof commands.
- keep `run-state.json` current: phase, iteration, plan_approval, unresolved gates, blockers, next
  action, regression_ledger, last proof command.

## LEGACY entry (order)

1. Map first (`agents/explore.md`, `reference/domain-context.md`).
2. Existing API touched: capture its preserve-baseline (`reference/qa.md` "API behavior baseline").
3. Shared code/state changes: capture the neighbor characterization baseline (`reference/qa.md`).
4. Proceed with the default loop; the before state lands in `QA.md` `## Before` (`reference/delivery-gate.md`).

## Completion promise + loop cap

Frame writes the completion promise (`PLAN.md` `## Intent`) before Build. Loop only while the promise is
unproven and a fresh, actionable gap remains. Default `max_iterations`: 8 for Build/Verify; critic->fixer cap below. At cap,
write forced reflection in `run-state.json`: failing check, likely root cause, unproven requirement,
previous-green regression status, smallest next action. Each iteration re-runs `regression_ledger`; if a
previously green check turns red, stop, fix it, and record
`forced_reflection.regressed_previously_green`. Ask only for requirement-level blockers or consent.

## Roles (each role = a fresh-context subagent by default)

Dispatch by default: conductor runs each role as a fresh-context subagent so heavy references load inside
that role, not the conductor. A user invoking `supergoal` for GREENFIELD, DEBUG, or LEGACY work is
explicit authorization to spawn the role-loop subagents described here. Do not ask a second "may I use
subagents?" question unless the user limited delegation, tooling is unavailable, or a normal
safety/permission gate requires consent. Return short structured status only:
changed files, proof output, concerns. Parallelize independent units; order dependent roles. Each dispatch's
model tier is the conductor's choice at dispatch time (stronger tier for novel/algorithmic slices).
Build (1), Improve full spec (2), Improve edge cases (3), Mandatory Adversarial Review (4), and Exact
Verify/QA (5) are mandatory. Critic/Fixer is optional gated escalation, usually after the edge pass or
when Exact Verify finds an unexplained gap.

0. **Adversarial plan attack (conditional, no src edits)** - only for under-specified, wide-blast-radius,
   security/data/concurrency, or latent-correctness work. Before Build, dispatch critics for security,
   scope, correctness, performance, and operability. Accept only findings grounded in request/docs, current
   code/data, or platform rules; convert required risks into tests, decision gates, or residual risk.

1. **Build** - before first edit, confirm blast-radius beyond the explicit target (`reference/interview.md`).
   Implementation must run as a separate fresh-context builder subagent; the conductor should not
   implement code inline. The builder is briefed by the approved `PLAN.md` alone (on an
   R-LOOP re-entry, also the LATEST `R-LOOP.md` section); Build does not start before the plan approval
   gate clears. Then smallest correct change; match surrounding style. Bug:
   failing test first. For any shared
   code/state change, capture a neighbor characterization baseline FIRST
   (`reference/qa.md` "Characterization baseline"). Refactor/integrate an existing API: capture its
   exact-behavior baseline FIRST. Capture run setup in `QA.md` `## Before` and `run-state.json`.

2. **Improve full spec** (`agents/executor.md`; fresh-context improver)
   - Re-read the request/ticket, README, design/API docs, current code, existing tests,
     `GOAL.md` `## Success Criteria`, and repo/data rules. Fix the smallest gap between those requirements
     and current behavior.
   - If production/source-code domain ambiguity would change user-visible behavior, data semantics,
     permissions, migrations, or API compatibility, stop and record an `ask-user` decision gate. Do not
     guess business meaning.
   - Generic no-user coding utility/eval fixture: choose the most conservative, reversible default,
     record the choice in `GOAL.md` `## Decision Gates` (resolved), and prefer preserving existing
     values/no-op behavior unless spec or safety requires strictness.
   - Add or adjust tests only for grounded `must` behavior. Do not turn silence into stricter semantics.

3. **Improve edge cases** (`agents/executor.md`; fresh-context improver)
   - Attack degenerate inputs, missing/extra fields, duplicates, ordering, idempotency, error/recovery,
     state/protocol transitions, concurrency, compatibility, security side effects, resource cleanup.
   - For each gap, apply the same threshold: production/domain behavior-changing ambiguity needs user
     feedback; generic no-user coding ambiguity gets a conservative, reversible default and a recorded
     rationale.
   - Re-run the targeted tests after every fix. Keep the diff minimal; no padding, rewrites, or unrelated
     cleanup.

4. **Mandatory Adversarial Review (mandatory core; no src edits)** (`agents/code-reviewer.md`)
   - Fresh-context adversarial review: re-read the request/docs, `GOAL.md`, `PLAN.md`, `QA.md`, current
     diff, tests, and repo/data rules. Try to disprove the change against required behavior, edge cases,
     and execution evidence. Fresh gap -> route back to Improve full spec or Improve edge cases.
   - The reviewer does not edit source, does not weaken tests, and does not declare done. Findings become
     fixes, `ask-user` decision gates, or residual risk. Reviewer approval is not a substitute for exact
     verification.

5. **Exact Verify/QA vs ground truth (mandatory core; Final Verify/QA)** (browser proof:
   `agents/qa-tester.md`; non-browser/artifact verify: `agents/qa-auditor.md`; security:
   `agents/security-reviewer.md`)
   - Re-run REAL tests and report output. Run the command/browser/API/E2E layer promised in
     `PLAN.md` `## Intent`. If the user expected an actual E2E/live/API/browser run, run it; otherwise mark
     that layer not proven with blocker/residual risk. Exact verification outranks reviewer approval. Fresh
     gap -> route back to Improve full spec or Improve edge cases.
   - Diff the implementer's changes (git diff in the run worktree) against `GOAL.md`: tick each Success
     Criterion / QA Case proven met (only the verifier ticks); untick a regressed previously-green
     criterion with the regression evidence.
   - Code-change scenarios use `reference/qa.md` "Scenario stencil (code changes)", including regression
     scenarios and metamorphic relations when no exact oracle exists.
   - Browser UI changes require `reference/qa.md` browser evidence: `Tool: playwright-cli`,
     fixed-route as-is/to-be captures, and passing `bash templates/qa-gate.sh <vault> browser`.
   - Re-run every captured neighbor characterization baseline; unnamed drift is red. API refactor:
     re-capture the same call and diff against the pre-refactor baseline; unintended drift is a red to
     resolve.
   - Close `GOAL.md`: every Success Criterion checkbox is ticked with a verifying check, and
     `Backward-trace: clean` in `QA.md` proves no diff hunk is orphan scope.
   - Final Verify for neighbor baselines and trace closure is fresh-context: self-review is not a
     regression gate. Unchecked criteria, unresolved REVISE items, and stub/placeholder implementations
     block done.
   - Tick each surfaced criterion in `GOAL.md`, or leave it unchecked with why.
   - Any criterion still unchecked: APPEND a timestamped section to the vault's `R-LOOP.md`
     (`templates/R-LOOP.md`) - checklist of missing/broken items (criterion #, expected vs actual,
     evidence path), regression note, smallest next fix - then the conductor relaunches the implementer,
     which reads `PLAN.md` plus the LATEST `R-LOOP.md` section. This is the Improve loop-back, still
     capped by `max_iterations`.
   - Update `QA.md`: `## Results` checklist sentences (succinct, plain language), `## Commands`, decision
     gates, intentional drift, residual risk, `Verdict:`. Unresolved `ask-user` findings or missing
     trusted commands block a final done claim and commit (`reference/delivery-gate.md` Commit gate).
   - Every `GOAL.md` box checked: write `Z-<YYYY-MM-DD>.md` (`templates/Z-DONE.md`) with the run branch
     and completion timestamp - never earlier.
   - Update `run-state.json`: phase, iteration, gate status, last proof command, blockers, next action,
     and completion-promise status.

6. **Critic** (`agents/code-reviewer.md`; OPT-IN escalation for under-specified / latent-correctness work) - DO NOT edit src or weaken/delete existing tests.
   - Re-read request/docs and repo/data rules. Enumerate REQUIRED behaviors existing tests miss, especially
     boundary inputs, error/recovery, scoping/precedence, filters, incremental update, concurrency,
     protocol/state.
   - Requirement threshold: classify each candidate as `must`, `should`, or `ask-user`. Only `must`
     requirements grounded in request/docs, current/API behavior, repo/data rules, or platform safety
     get failing tests. Do not turn silence into stricter semantics (for example throwing on degenerate
     inputs) when multiple reasonable behaviors exist; record that as an `ask-user` decision gate or
     residual risk.
   - For each `must`, write a NEW FAILING black-box/property test in a separate file, derived strictly
     from request/docs.
   - Record each surfaced requirement by APPENDING an unchecked criterion to the run vault's `GOAL.md`
     `## Success Criteria` (`docs/changelog/<YYYY-MM>/<DD-topic>/GOAL.md`; format in
     `templates/GOAL.md`): one row per `must` - what request/docs imply,
     why it is required though the prompt never stated it (`(surfaced: ...)` tag), and the failing test
     that now covers it (unchecked box = open; only the verifier ticks it).
   - Leave the failing tests red.

7. **Fixer** (`agents/executor.md`) - DO NOT edit test files.
   - Read NOTES + run the suite. Make the failing tests pass with the SMALLEST change.
   - If a critic-authored test appears to encode an `ask-user` choice, contradict current/API behavior, or
     harden semantics not required by request/docs or safety, stop and report the decision gate instead of
     optimizing source to it.
   - No padding: add no code that is not required by a failing test or a listed defect. Do not break
     passing tests.

The improve-pass core is mandatory for invoked GREENFIELD / DEBUG / LEGACY runs: docs-vs-behavior
improve, edge-case improve, mandatory adversarial review, exact REAL tests/E2E evidence, and DB evidence
for data-backed bugs (`reference/db-access.md`). DB proves data state; it does not replace red-green. Loop
the opt-in critic->fixer escalation only while a fresh red appears.

Board (optional): if enabled (`reference/observability.md`), conductor may call `sg-emit --phase <P>` at
phase transitions (Frame/Build/ImproveFullSpec/ImproveEdgeCases/MandatoryAdversarialReview/ExactVerify/Critic/Fixer/Done). Opt-in,
best-effort; observes only, never blocks or gates the loop.

## Guardrails (keep it baseline-first, not Goodhart)

- Generated tests are a SIGNAL to surface hidden requirements, not the oracle. Final verification is REAL
  tests + request/docs. Never weaken/delete a real test; never declare done because self-written tests pass.
- Ambiguous edges are not REDs. If a production/domain behavior change has multiple reasonable meanings,
  classify it as `ask-user` or residual risk; do not invent stricter semantics. If generic/no-user, choose
  a conservative, reversible default and record it.
- Characterization baseline is a regression signal, not a correctness oracle. A known-bug snapshot changes
  only when the bug fix is intentional and named.
- Self-review is not a regression gate: generated explanations can approve behavior drift. Require
  mandatory adversarial review, execution evidence, exact verification, and no stub done claims.
- Exact verification outranks reviewer approval: no critic, reviewer, summary, or self-grade can replace the
  actual command/browser/API/E2E run promised for the task. Missing exact proof is `not_proven`, not done.
- Derive every generated test from request/docs, not from a guessed rubric; a wrong generated test the
  fixer optimizes to is the failure mode - keep them black-box and spec-anchored.
- Stop condition: cap the Build->Verify loop at `max_iterations` (default 8) with forced reflection, and
  cap the critic->fixer loop at 3 cycles; if a 4th critic cycle would start, escalate to the user with
  the open reds instead of grinding. Doubt-theater anti-signal: 2+ cycles that produce findings but
  change no code means the critic is validating, not doubting - stop and recut the critic's spec focus.
