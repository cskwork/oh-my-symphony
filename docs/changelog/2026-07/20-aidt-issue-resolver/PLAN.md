# PLAN - AIDT Jira Issue Resolver

This root plan routes the broad GREENFIELD objective. Each selected frontier ticket receives its own
frozen delivery plan and proof vault.

## Approval

- Status: auto-approved
- Record: 2026-07-20; autonomous background run pre-authorized by the user's "accept the plan made" instruction.

## Intent

- Goal / constraints / tradeoffs / rejected approaches: reuse Symphony's existing dashboard, TUI,
  service manager, file board, Codex backend, and worktree isolation. Add only the missing AIDT Jira
  intake and evidence-gated delivery integration. Reject direct Jira-stage orchestration because the A20
  workflow lacks the required internal gates; reject hardcoded credentials and broad project polling.
- Completion promise: an always-running dedicated-port issue resolver, proven by real intake and
  browser/TUI/API E2E through final dev QA. Stop only when all root criteria are verified. Each frontier
  Build/Verify loop has `max_iterations: 3`.

## Priority Rules

Domain(s): local orchestration + Jira integration + git worktrees + CI/CD safety + operator UI
1. Import only explicitly assigned, actionable issues; fail closed when identity or status scope is unknown.
2. Preserve Jira as external source context while local Symphony state owns detailed delivery gates.
3. Never expose API tokens, account credentials, raw QA identities, or Jenkins secrets in git or logs.
4. One Jira issue maps to one isolated worker/worktree lifecycle; duplicate dispatch is prevented across restarts.
5. AIDT feature branches start from `origin/aidt-prd`; protected integration branches are never directly occupied.
6. Tests and exact behavior evidence outrank agent reports or reviewer approval.
7. Merge and deploy are serialized, idempotent, and blocked by missing or stale evidence.
8. The dashboard and TUI are projections of the same durable runtime state.
9. External failures remain visible and retryable; no fake success or swallowed error path.
10. Reuse current Symphony service, board, contracts, and browser surfaces before adding abstractions.

## User-directed supervised worker environment

- `EXAMBANK_TWIN_IMAGE_JUDGE_ENABLED` is an explicit user-supplied, non-secret ticket 009 setting with exact value
  `true`; it is not inferred configuration or credential scope.
- Ticket 009 owns injection into the supervised resolver worker plus startup/health proof of enabled state. Proof
  must not serialize or log the surrounding environment.
- Frontier 003 remains implementation/default-off verification only: it does not activate this variable, start the
  managed resolver, or claim ticket 009 implementation.

## Steps

1. Complete the Frontier Map from current code, A20-1188 evidence, Jira adapter behavior, AIDT branch rules,
   Jenkins CLI behavior, and the existing Symphony service/UI tests.
2. Deliver one unblocked vertical ticket at a time in a nested frontier vault with red-green tests.
3. Integrate the closed tickets, run real local service/API/browser/TUI E2E, then validate live Jira intake.
4. Only after green evidence, merge to Symphony `dev`, start the managed service, and exercise one AIDT issue
   through local QA, aidt-dev Jenkins deploy, and final dev E2E. The ticket 009 managed start supplies the exact
   non-secret worker setting `EXAMBANK_TWIN_IMAGE_JUDGE_ENABLED=true` and proves its enabled health state.

## Acceptance checklist

- [x] The Frontier Map names all safety and credential blockers and selects one independently testable slice.
- [ ] Every delivery slice has red-green proof and a fresh builder plus fresh verifier.
- [ ] The final integrated system satisfies every root `GOAL.md` criterion.

## Tools & Skills

- `supergoal` GREENFIELD role loop and Wayfinder
- `symphony-skill` CONFIGURE, CUSTOMIZE, OPERATE, and MONOREPO routes
- `jira-resolve` evidence and AIDT branch/deploy safety rules
- `pytest`, Symphony doctor/API smoke, playwright-cli browser proof, TUI screenshot harness, `jk` Jenkins CLI

## Verification strategy

- Before proof: existing `WORKFLOW.md` is file-only; Jira adapter polls the full project/state set and has no
  assignee filter or parent-context mirror; Atlassian CLI is unauthenticated.
- Step -> GOAL.md criterion: Frontier tickets link each implementation step to one or more root criteria.
- Trusted commands: `pytest` and documented Symphony smoke commands (frozen_repo); scenario fakes and live
  read-only intake checks (evaluator_owned).

## Grounding ledger

- What is A20-1188? -> Jira UI, 2026-07-20 -> assigned backend subtask, status `백로그`, empty own description.
- Where are its requirements? -> parent A20-1186 -> import parent description/comments with the subtask.
- Can direct Jira states represent all gates? -> current Jira UI + Symphony stage model -> no; mirror locally.
- Can unattended Jira be proven now? -> `acli auth status` and `jira me` -> no token; keep live proof blocked.
- Which Symphony refs are safe? -> local `dev` and `origin/dev` both `0fe78e2`; run branch is isolated.
