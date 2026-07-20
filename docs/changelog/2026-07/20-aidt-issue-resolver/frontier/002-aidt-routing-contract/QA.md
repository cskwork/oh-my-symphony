# QA - Frontier 002 AIDT Routing Contract

- Verdict: PASS

## Before

- [x] Frontier 001 is committed at `f7b0585` and the isolated worktree is clean.
- [x] Current Jira snapshot omits component/status/priority/updated fields needed for routing/card completeness.
- [x] Current file tracker preserves unknown frontmatter but has no route-owned coordinator/child mutation.
- [x] Current tick has no routing hook between Jira intake and candidate fetch.
- [x] Read-only AIDT evidence routes A20-1188 to `viewer-api` at 95 and finds no LMS change anchor.
- [x] Baseline: 51 Jira-intake tests and 216 affected regressions passed; full suite has one accepted pre-change
  continuous-improvement E2E failure and 1,465 passes/6 skips.

## Results

- [x] Iteration 1 conditional plan attack passed after all 12 original MUST amendments were bound.
- [x] Red proof: the new routing tests initially failed collection because `symphony.aidt_routing` did not exist.
- [x] Iteration 1 partial build reached 14 focused passes before a trusted cross-frontier review found that the
  clean-working-tree/`HEAD` trust rule blocks the live dirty checkout and violates user-state preservation.
- [x] Iteration 2 immutable-object/cohesion attacks found and bound eight MUST corrections; fresh recheck passed all
  eight MUSTs and three SHOULDs.
- [x] Red-green implementation complete: split routing suites 98 passed; plan-listed affected regressions 327 passed;
  broader orchestrator/service/web regressions 375 passed; builders report Ruff/Pyright/structure/diff clean.
- [x] Fresh iteration-2 verification failed on isolated storage import: eager facade runtime export creates
  `aidt_routes -> aidt_routing.__init__ -> runtime -> aidt_routes` before test collection.
- [x] Iteration-3 lazy facade correction: standalone import permutations pass; isolated split suites total 101;
  affected matrix 331; Ruff/Pyright/structure/diff clean.
- [x] Fresh iteration-3 verification failed the public-output gate: malformed result counts/blocked IDs leak injected
  payload/path text through repr, structured logs, and health. Three-loop contract exhausted; correction moved to
  Frontier 002a.
- [x] Fresh post-correction verification passed four imports, five isolated routing suites (177), the affected matrix
  (407), the broad current-file matrix (722), and the preserved Frontier 001 matrix (230).
- [x] Full pytest matched the accepted ledger: 1,656 passed, 6 skipped, and only the known missing-`CI-1.md` E2E
  failed. Ruff, Pyright, changed/new product AST, tracked/no-index whitespace, and scope audits passed.
- [x] Doctor matched the accepted environment baseline exactly: external workspace-root permission and absent
  worktree board root only.

Backward-trace: clean

## Trusted Commands

| Command | Source | Result |
|---|---|---|
| five isolated routing pytest suites | evaluator_owned | 177 passed |
| plan-listed affected pytest matrix | evaluator_owned | 407 passed |
| broad current-file pytest matrix | evaluator_owned | 722 passed |
| preserved Frontier 001 pytest matrix | frozen_repo | 230 passed |
| repository-wide pytest | frozen_repo | 1,656 passed, 6 skipped, one accepted pre-change failure |
| Ruff, Pyright, AST, and whitespace gates | evaluator_owned | passed |
| `symphony doctor ./WORKFLOW.md` | frozen_repo | accepted two external environment failures only |

## Reproduction Fidelity

Fidelity level: synthetic-representative

Residual risk from data gap: no live Jira component snapshot or active routing profile exists; A20 ownership evidence
comes from current checked-out code and a representative local source fixture.

Post-deploy confirmation plan: before later activation, run one read-only Jira snapshot and one catalog validation
against the approved AIDT root, then confirm A20-1188 resolves to the recorded viewer-api revision without dispatch.
