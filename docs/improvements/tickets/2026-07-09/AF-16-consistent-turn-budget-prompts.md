# AF-16 — First-turn vs continuation prompts use different turn-budget denominators

Route: LEGACY | Severity: P3 | Confidence: CONFIRMED (plumbing) / PLAUSIBLE (impact)
Blocked by: none

## Defect

First-turn prompt env gets `max_turns=cfg.agent.max_turns` (per-attempt cap,
`core.py:3419`; rendered at `prompt.py:563-564`); continuation prompts get
`turn_number=completed+turn` against `cfg.agent.max_total_turns` (lifetime
cap, `core.py:3735-3736`; `prompt.py:587-592`). A template rendering
`{{ turn_number }} / {{ max_turns }}` tells the agent "1 of 8" then
"N of 60" — mixed bases can mislead an agent pacing itself. Latent with
shipped templates (`docs/symphony-prompts/file/base.md` doesn't reference
these variables), so custom templates are the exposure.

## Fix direction

Pass one consistent basis to both builders (recommend: lifetime numerator /
lifetime denominator on both, since continuation already uses it), and
document the variable semantics in the WORKFLOW template docs.

## Acceptance checks

- [ ] Test asserting first-turn and continuation prompts render the same
  numerator/denominator basis.
- [ ] Prompt anchor contract intact: `tests/test_workflow_pipeline_prompt.py`
  byte-exact anchors reviewed BEFORE editing (grep the test first; audit for
  rule drops), updated deliberately if anchors move.
- [ ] Full suite green.

## Non-goals

Changing cap values; template content redesign.
