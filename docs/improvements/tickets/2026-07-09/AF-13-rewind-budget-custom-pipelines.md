# AF-13 — Rewind budget only counts the hard-coded English state pair

Route: LEGACY | Severity: P2 | Confidence: CONFIRMED (code) / PLAUSIBLE (trigger)
Blocked by: none

## Defect

`_REWIND_TRANSITIONS` is a static English pair set —
`("verify","in progress")`, `("learn","in progress")`
(`orchestrator/constants.py:79-84`; predicate `helpers.py:36-43`; budget
increment gated on it at `core.py:3617-3647`). On boards using the
review/qa pipeline or Korean Jira status names, an agent-chosen
`qa → in progress` move is not recognized as a rewind, so
`debug.rewind_count` never increments and the rewind → Blocked ceiling never
fires. The pipeline can oscillate qa → in progress → qa indefinitely,
burning turns and tokens. (Contract-forced rewinds still count because the
caller passes `is_rewind=True` explicitly — only agent-chosen rewinds
escape.)

## Fix direction (decide exact rule at ticket time — map "Not yet specified")

Derive rewind detection from the configured pipeline: any transition from a
later `active_states` index to an earlier one counts, or make
`_REWIND_TRANSITIONS` configurable per workflow. Index-order is the
recommended default (no config surface, matches operator intuition).

## Acceptance checks

- [ ] RED first: board configured with review/qa states; agent-driven
  `qa → in progress` transitions increment `rewind_count` and trip the
  Blocked ceiling at the cap (fails on current `main`).
- [ ] Same for non-English (Korean) state names.
- [ ] Default verify/learn pipeline behavior unchanged (existing rewind
  tests green). Full suite green.

## Non-goals

Changing the rewind cap value; contract-eval rewind mechanics.
