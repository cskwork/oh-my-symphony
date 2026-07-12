# AI-SHIP - shipping AI-built software

For the PM accountable for code an AI wrote. The goal: make an AI-built app reviewable and find
the gap between what was intended and what was actually built. This is the PM-facing mirror of
supergoal's "verify against ground truth" - here the artifact is the evidence package, and the
ground truth is the running code.

## shipping-artifacts

Produce the documentation set that makes an AI-built app reviewable by a human who did not write
it. The set:

1. **Architecture map** - components, services, data stores, and how they connect.
2. **Critical flows** - the key user/data flows end to end (auth, payment, the core action).
3. **Permissions / access model** - who can do what; where authorization is enforced.
4. **Secrets + config** - what secrets exist, where they live, how they are injected (never paste
   secret values - reference their location).
5. **External dependencies** - third-party APIs/services and the blast radius if each fails.
6. **Test-coverage map** - what is tested, what is not, and which untested paths carry the most
   risk.

Each artifact must cite the actual files/functions it describes (read the code; do not narrate
from the prompt). Output: the set above, with a "highest-risk unreviewed area" call-out.

## intended-vs-implemented

Find the gap between documented intent (PRD, spec, ticket) and the actual code, with cited
evidence. Steps:

1. **Extract intent** - the explicit requirements and behaviors from the spec/PRD.
2. **Trace implementation** - for each requirement, find the code that implements it (file +
   function). Read it; do not assume.
3. **Classify each requirement** - implemented / partial / missing / contradicted / undocumented-
   extra (built but never specified).
4. **Cite evidence** - file:line for every claim. An uncited gap is a hypothesis, not a finding.

Output: a requirement-by-requirement table with status + evidence, the highest-severity gaps
first, and the tests that would lock each gap once fixed. This pairs naturally with supergoal:
hand the missing/contradicted requirements to a build loop as failing tests.

## Critic completeness anchors

Every artifact cites real files/functions (not prompt narration); secrets are referenced by
location, never pasted; the intended-vs-implemented table cites file:line per row and classifies
every requirement; the highest-risk unreviewed area is named explicitly.
