# RESEARCH - market & user research

Frameworks for understanding the market and the people in it. Every output separates evidence
(data, quotes) from inference, and labels anything unvalidated as a hypothesis.

## live-market-signal (voice of customer)

When you lack first-party data, gather real, recent, engagement-ranked signal before asserting
anything about the market - see `reference/signal.md` (read-only, keyless; delegates to the
`last30days` skill if installed). It feeds `user-personas` (pains, trigger moments, verbatim
quotes), `sentiment-analysis` (themes + volume), `competitor-analysis` (positioning, what users
praise/hate), and the demand side of `market-sizing`. A claim about what customers want, sourced
to nothing, is a hypothesis - say so.

## user-personas

Evidence-based archetype, not a stock photo. Fields: name/role, context, the job they are hiring
the product for, goals, pains, current solutions, decision criteria, and the trigger moment.
Output: 1-3 personas, each tagged with the evidence behind it (or "hypothesis - validate with N
interviews"). A persona with no evidence is a hypothesis, say so.

## market-segments / user-segmentation

Divide the market into actionable groups. Bases: needs (best for product), behavior (usage,
adoption stage), firmographic (B2B: size, industry), demographic, psychographic. Each segment
must be measurable, reachable, and large enough to matter. Output: segments + the variable that
separates them + which one to serve first and why.

## customer-journey-map

Stages the customer moves through, end to end. Per stage: actions, touchpoints, thoughts,
emotions (the curve), and pain points / moments of truth. Output: a stage-by-stage map + the 2-3
highest-leverage pain points to fix. Anchor stages in real behavior, not an idealized funnel.

## market-sizing (TAM / SAM / SOM)

Three nested layers:
- **TAM** - total addressable market (everyone who could ever buy).
- **SAM** - serviceable available market (those your model/geo can serve).
- **SOM** - serviceable obtainable market (realistic near-term capture).

Two methods, ideally both for triangulation:
- **Top-down:** start from an industry figure, narrow by segment %. State the source.
- **Bottom-up:** (# target customers) x (annual contract value or ARPU). Usually more defensible.

Output: all three layers with the formula and every input sourced or labeled assumption. An
unsourced market number is the classic critic blocker.

## competitor-analysis

Map the competitive field: direct, indirect, and substitute competitors. Per competitor:
positioning, target segment, pricing, strengths, weaknesses, and the gap you exploit. Output: a
comparison table + where you win and the segment where you win it. Include "do nothing" /
status-quo as a competitor.

## sentiment-analysis

Synthesize qualitative feedback (reviews, tickets, social, NPS verbatims) into themes with
direction and intensity. When you have no first-party corpus, gather it via `reference/signal.md`
(HN, reviews, forums, web-surfaced Reddit threads - keyless; deep Reddit/X via the last30days
accelerator). Output: ranked themes (positive/negative),
representative quotes, the volume behind each, and 3-5 action items. Quantify ("38 of 120 reviews
mention onboarding"), do not just adjective-spray.

## Critic completeness anchors

Market sizing has all three layers with sourced/assumed inputs and a stated method; personas and
segments carry their evidence or a hypothesis tag; journey maps end in prioritized pain points;
competitor maps include substitutes and a stated point of difference; market/customer claims
drawn from live signal cite a real recent source (`reference/signal.md`) or are labeled a hypothesis.
