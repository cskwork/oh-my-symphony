# 003 - AIDT worktree provisioner rollup

Route: ROLLUP

Status: closed - aggregate verification passed

Blocked by: none

Unblocks: 004, 005

Contains: 003a, 003b, 003c, 003d, 003e, 003f, 003g, 003h, 003i, 003v

## Goal

Coordinate independent deep-module contracts that create/resume one service-local ticket worktree from a recorded
origin/aidt-prd SHA while preserving dirty user state and preventing new occupancy of aidt-dev or aidt-stg.

## Acceptance criteria

- 003a-003g each attribute one already executed module/interface contract and its evidence without claiming a new
  worker-sized Build or independent closure.
- 003h has a bounded atomic runtime-generation/manager publication build with fresh verification.
- 003i has a verified shipped default-off operator example for `jira_intake`, `aidt_routing`, and `aidt_worktree`,
  without live activation or secrets.
- 003v repeats aggregate verification and is the only child allowed to recommend rollup closure.
- Tickets 004 and 005 remain blocked until this rollup closes; no child status implies Frontier 003 PASS.

## Proof commands and surfaces

- Child-ticket commands and the preserved Frontier `R-LOOP.md` evidence.
- 003v isolated, affected, full, static, structure, whitespace, doctor, and literal-gate decision.
- Temporary Git fixtures only; no live AIDT/Jira/backend/merge/push/deploy action.

## Scope boundaries

- Owns dependency order, commit partition, aggregate verification, and final Frontier 003 closure decision.
- Does not own implementation hunks directly; those are attributed to 003a-003i.
- Does not edit AIDT product repositories, merge to aidt-dev, push, trigger Jenkins, or activate the profile.

## External blockers

- Temporary-repository proof is unblocked.
- Live provisioning requires accessible service checkouts and fetchable origin/aidt-prd; unexpected state blocks.
- The 001a, 003h, and 003i builds passed fresh verification; 003v passed the aggregate completion gate.
