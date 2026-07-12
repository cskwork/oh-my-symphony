---
name: supergoal
description: Factory-pinned Supergoal runtime for WAYFINDER, GREENFIELD, DEBUG, and LEGACY delivery.
---

# Supergoal factory runtime

One objective -> smallest correct change -> verified against ground truth. This
pinned router intentionally exposes only the routes emitted by Oh My Symphony's
autonomous factory; every advertised support file ships with the factory.

## Standing rules

- Read `.supergoal/rules/RULES.md` first when it exists. Use
  `reference/rules.md` for the standing-rules contract.
- Ground truth beats proxy. Run the real proof commands named by the ticket.
- Keep changes surgical, test first, and report blocked proof honestly.
- Destructive or outward-facing actions require explicit operator consent.

## Route

| Ticket signal | Mode | Runtime |
|---|---|---|
| map a product, decompose work, identify the next frontier | WAYFINDER | `reference/wayfinder.md`; no product code |
| build a new vertical slice | GREENFIELD | `reference/role-loop.md` |
| fix broken behavior | DEBUG | `reference/debugging.md`, then `reference/role-loop.md` |
| add or change an existing system | LEGACY | `reference/domain-context.md`, then `reference/role-loop.md` |

WAYFINDER may use `reference/research.md` when a ticket needs external evidence.
Delivery work uses `reference/delivery-gate.md` and `reference/qa.md`. When
persisted data is load-bearing, use `reference/db-access.md`. Optional board
observability is defined by `reference/observability.md`.

## Delivery loop

1. Frame the approved ticket in `GOAL.md`, `PLAN.md`, `QA.md`, and
   `run-state.json` from `templates/`.
2. Build in a fresh isolated worktree. Bugs start with a failing reproduction.
3. Improve the full ticket, then edge cases.
4. Run a no-source-edit adversarial review.
5. Re-run exact proof. Unmet criteria go to `R-LOOP.md`; complete runs write
   `Z-DONE.md`. Never commit before `templates/commit-gate.sh` is green.

Read `reference/role-loop.md` for the complete execution and isolation
contract. Runtime roles live in `agents/executor.md`,
`agents/code-reviewer.md`, `agents/qa-auditor.md`, `agents/qa-tester.md`, and
`agents/security-reviewer.md`. DEBUG also uses `agents/debugger.md`; LEGACY
discovery uses `agents/explore.md`.

## Reference map

| Read | When |
|---|---|
| `reference/wayfinder.md` | map destination, dependency graph, and frontier |
| `reference/role-loop.md` | all delivery routes |
| `reference/debugging.md` | DEBUG reproduction and hypothesis ledger |
| `reference/domain-context.md` | LEGACY code and domain grounding |
| `reference/delivery-gate.md` | run-vault and commit gates |
| `reference/qa.md` | exact verification |
| `reference/interview.md` | load-bearing ambiguity only |
| `reference/db-access.md` | read-only database evidence |
| `reference/research.md` | source-backed Wayfinder research |
| `reference/observability.md` | optional live board events |

## Credit

This factory runtime is derived from the pinned Supergoal source identified in
Oh My Symphony's bundle manifest. The applicable license is beside this file;
the distribution also carries bundle-level attribution and redistribution
notices.
