# Plan grounding - before plan freeze

At Frame end, before Build, ground `PLAN.md` in current docs/code/domain. Ask the human only when current
evidence cannot decide a load-bearing choice. If the grounded approach's blast radius reaches past its
explicit target, run the blast-radius confirm in `reference/interview.md` before freeze.

## Required input

Read the `## Domain Brief` recorded in `PLAN.md`'s grounding ledger first when present: selected
knowledge files, terms, invariants, current-code verification, entry points, test commands, gaps.

Saved domain facts are pointers. Load-bearing choices cite current docs/code or name the gap. Current
code wins conflicts; record them in `PLAN.md`.

No Explore map yet and grounding needs more than a handful of files or unfamiliar code: dispatch a
fresh-context explore subagent (`agents/explore.md`) to write the map into `PLAN.md` grounding notes
before freeze. Small or well-known scope grounds inline.

## Decision-tree pressure test

Use this for Track A and whenever Track B depends on domain language or system boundaries.

1. Build the decision tree: root choice, dependent choices, hidden assumptions, and risk branches.
   Resolve parent decisions before child decisions.
2. Ask one precise question per branch. If current docs/code can answer it, answer it yourself and cite
   the source; ask the human only for unresolved load-bearing choices.
3. Challenge terminology against the Domain Brief, selected knowledge files, and current repo docs if
   they exist. If a term conflicts, choose a canonical term or name the conflict.
4. Stress concrete scenarios: normal case, boundary case, failure case, and cross-context ownership
   case when relevant.
5. Cross-check every "how it works" claim against code. Contradictions go into `PLAN.md` as gaps or
   corrected assumptions.
6. Update docs only after a decision is resolved. Glossary entries define domain terms only;
   `decisions/*.md` entries are for hard-to-reverse, surprising tradeoffs with real alternatives.
7. Record a compact grounding ledger in `PLAN.md`: question, answer/source, decision, and remaining
   gap. Do not paste the whole interview.

## Track A - feature / novel work

1. Locate Domain Brief, `GOAL.md`, Explore map, repo docs/decisions. Do not re-litigate settled decisions.
2. Walk the design tree. For each open question, choose the best option and justify it from docs/code.
   Escalate only unresolved load-bearing choices.
3. Resolve vague or overloaded terms against the glossary. Cross-check every "how it works" claim
   against code.
4. Update docs only when useful: glossary terms or hard-to-reverse surprising tradeoffs.
5. Put resolved choices and definitions into `PLAN.md`.
6. Put the Before/After Eval strategy into `PLAN.md` `## Verification strategy`: before proof, after
   commands, step -> `GOAL.md` criterion mapping, residual risk.

## Track B - refactor / improve codebase

Vocabulary: Module is a cohesive capability; Interface is its public boundary;
Depth is useful behavior hidden behind a small interface; Seam is a stable
boundary; Adapter connects that boundary to an external shape; Leverage is the
benefit per changed surface; Locality keeps related reasoning together.

1. Read Domain Brief and relevant repo docs/decisions first.
2. Find friction: shallow modules, cross-module bouncing, test-only extractions with no locality, leaky
   seams, untestable interfaces.
3. Deletion-test suspected shallow code: does removing it concentrate complexity or merely move it?
4. Rank by leverage. Pressure-test top candidates: what sits behind the seam, what tests survive. One
   adapter is hypothetical; two adapters make a real seam.
5. Write chosen deepenings into `PLAN.md`: files, problem, solution, locality/leverage benefit.
6. Write brownfield Before/After Eval strategy: behavior to preserve, baseline artifact, after comparison,
   intentional drift.

## Exit

`PLAN.md` is now grounded and must be self-sufficient - steps, tools & skills, verification strategy - so
a fresh-context implementer can build from it alone. Run the blast-radius confirm
(`reference/interview.md`) if the approach reaches past its target, ensure the Before/After Eval strategy
(`reference/delivery-gate.md`) is explicit, then freeze and clear the plan approval gate
(`reference/role-loop.md`: interactive = the user's explicit OK; autonomous = auto-approved, recorded in
`## Approval`). Build implements the approved frozen plan.
