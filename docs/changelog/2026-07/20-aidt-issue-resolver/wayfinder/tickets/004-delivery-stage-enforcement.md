# 004 - Delivery stage enforcement

Route: GREENFIELD

Status: current - plan attacked and 004a contract green; 004b-004v pending

Blocked by: none; 001, 002, and 003 are closed

Unblocks: 005, 006, 009

## Goal

Enforce the full delivery sequence with durable, freshness-bound evidence and explicit approvals; prompts
instruct workers but cannot authorize transitions.

## Acceptance criteria

- The profile exposes Intake, Route, Plan, Plan Approval, Worktree, Build, Review, Local QA, Commit, Merge,
  Deploy, Dev QA, Learn, and terminal failure/review states.
- A code validator rejects skipped stages and missing, failed, stale, ambiguous, or mismatched evidence.
- Evidence records issue revision, plan hash, relevant SHAs, proof command/result/time, and side-effect review.
- Every live issue waits for human plan approval bound to its revision/hash; infrastructure approval is not reused.
- Low-confidence work gets at most three fresh-context attempts, then Human Review.
- Merge, Deploy, and Dev QA serialize per service/environment; generic auto-merge is disabled.

## Proof commands and surfaces

- pytest -q tests/test_aidt_stage_gates.py tests/test_workflow.py
- Transition-matrix cases for valid flow, skips, stale proof, rewinds, restart, and concurrency.
- Board history/API showing approvals, evidence freshness, and blocking reasons.

## Scope boundaries

- Owns lanes, evidence schema, transition authorization, rewinds, and serialization.
- Does not implement service QA, Git promotion, Jenkins triggering, or Jira write-back.

## External blockers

- Fixture proof is unblocked.
- Future plan policy is unresolved; mandatory per-issue human approval is the safe default.
- Git pushes, Jenkins runs, dev-data writes, and Jira writes remain separately unauthorized.
