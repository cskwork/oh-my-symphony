# Clarifying interview - before plan freeze

After context gathering and before plan freeze, interview only on two triggers:

- **Ambiguity** (resolve *what* to build/fix): the request is underspecified, so clarify intent;
  *how* is then settled by grounding the plan in docs/code (`reference/plan-grounding.md`).
- **Blast radius beyond target** (confirm approach): grounded fix reaches past its explicit target -
  another function/module or observed behavior - so surface the impact and confirm. Explore already
  mapped side effects (`agents/explore.md`); this is confirmation, not discovery.

Applies to GREENFIELD, DEBUG, LEGACY only. LEARN/LEARN-DOMAIN skip.

## Where it runs

Ambiguity runs before grounding. Blast-radius confirm runs after grounding chooses the approach but
before freeze/Build.

| Mode | Ambiguity - before grounding | Blast-radius confirm - after grounding, before freeze/Build |
|---|---|---|
| GREENFIELD | End of Frame | once plan-grounding fixes the approach |
| LEGACY | End of Frame (Explore map in hand) | once plan-grounding fixes the approach |
| DEBUG | End of Diagnose, after ranked hypotheses, before Confirm | folded into Confirm, with the hypothesis re-ranking |

Context: GREENFIELD `brief.md` / `## Validation` / Domain Brief; LEGACY Explore map + Domain Brief;
DEBUG hypothesis ledger + current code.

## Gate - when to interview vs skip

Fire when **any** holds:

- **Ambiguity:** the request has multiple plausible interpretations, or a key detail is unclear across
  the coverage dimensions below (objective, definition of done, scope, constraints, environment,
  safety/reversibility), or
- **Blast radius beyond target:** the grounded fix changes a function/module past its explicit target,
  or alters existing observed behavior. This fires even when the request is unambiguous - the
  "already clear" skip below does NOT cover it.

Skip when **any** holds (and log the skip in `PLAN.md`):

- The request is clear AND the change stays within its explicit target (no cross-function or behavior
  spillover), or
- A quick, low-risk codebase/docs read can answer the missing detail (resolve it by reading, not
  asking), or
- The mode is LEARN / LEARN-DOMAIN.

Do not rely on model default. Asking with enough information is also failure. Detect missing goal,
missing premises, ambiguous terminology.

## Code-first rule

Before asking, resolve every code-answerable question by reading current docs/code. Only unresolved,
load-bearing, user-only choices reach the interview. Saved domain facts are pointers; current code wins.

## Coverage dimensions (selection menu, not a checklist to exhaust)

Pick from these axes; do not ask all six.

1. **Objective** - what changes vs what must stay the same.
2. **Definition of done** - acceptance criteria, concrete examples, edge cases.
3. **Scope** - which files / components / users are in vs out.
4. **Constraints** - compatibility, performance, style, dependencies, time budget.
5. **Environment** - language/runtime versions, OS, build/test runner.
6. **Safety / reversibility** - data migration, rollout/rollback, blast radius, risk.

When blast-radius fired, Safety / reversibility is REQUIRED: name touched functions/modules and behavior
that could change.

## Question selection

- **Cap at <=5 questions, one clarification round.** Ask only as many as the ambiguity requires; one
  or two questions are enough when they settle the load-bearing choice.
- **Maximize information gain.** Prefer the question that eliminates a whole branch of work.
- **Drop redundant questions.** If `brief.md`, the Domain Brief, or the Explore map already answers an
  aspect, do not ask it.
- **One at a time, recommend an answer.** Ask serially, wait for each reply before the next, and give
  your recommended answer for every question so the user can confirm or correct cheaply. Do not batch
  all questions into a single parallel turn.

## DEBUG variant - ranked hypothesis re-ranking

After Diagnose produces its hypothesis ledger, present 3-5 ranked root-cause hypotheses to the user for
re-ranking before confirming and writing the fix plan. **Non-blocking**: if AFK, proceed with your own
ranking. If re-ranked, advance the favored hypothesis only when direct evidence still supports it.

If chosen fix blast radius reaches past the cause site, present impact with the re-ranking before edit.

## Hard gate - block plan freeze

Do not freeze plan or confirm DEBUG root cause until must-have questions are answered or the user approves
stated assumptions. Unanswered must-haves block.

**Blast-radius confirm - strength by risk (tiered).** Default is non-blocking: present the impact
summary and proceed on your own best judgment if the user is AFK. Escalate to a hard gate - no Build
until the user explicitly approves, AFK or not - when **any** holds:

- **Wide:** spans multiple modules or crosses a service boundary, or
- **Destructive / irreversible:** a SKILL.md hard stop applies (drop data, force-push, external
  publish, migration), or
- **Behavior change:** alters an existing public contract or observed behavior callers depend on.

Approval confirms intent only; it never substitutes for independent spec/verification checks.

## Recording

Write a compact `## Interview` section in `PLAN.md` (DEBUG: next to the hypothesis ledger, also in
`PLAN.md`): each question, the chosen answer or user-approved assumption, and the decision it drove. Do
not paste the whole exchange. A skipped interview records one line in `PLAN.md` stating why it was
safe to skip. The plan approval gate (`reference/role-loop.md`) is separate and blocking in interactive
sessions: the interview confirms approach; the approval gate authorizes Build.

For a blast-radius confirm, record the impact presented (functions/modules touched, behavior that
could change), the strength applied (non-blocking / hard gate), and the user's approval or your
AFK-proceed decision.

## Exit

Requirements are crystallized. GREENFIELD/LEGACY proceed to plan-grounding and freeze; DEBUG proceeds
to Confirm and the fix plan. Build starts only after the plan is grounded and frozen, and any fired
blast-radius confirm has cleared - approved, AFK-proceeded, or safely skipped and logged.
