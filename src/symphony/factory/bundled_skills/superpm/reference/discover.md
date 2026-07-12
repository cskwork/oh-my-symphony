# DISCOVER - product discovery

Frameworks for finding what to build and de-risking it before commitment. Branch hard on
**new product** (no users/data yet) vs **existing product** (real users, telemetry, requests).

## brainstorm-ideas

Generate solution ideas from multiple lenses, then converge.

- **Lenses:** jobs-to-be-done, current pain points, competitor gaps, adjacent workflows, tech
  enablers, business-model shifts. Existing product: also mine support tickets, churn reasons,
  feature requests. New product: also mine analogous markets and first-principles needs. With no
  first-party data, mine real pains and competitor gaps from live signal (`reference/signal.md`).
- **Divergent output:** 10-20 ideas, each as `<who> can now <do what> so that <outcome>`.
  Cluster by theme; flag the 3 worth testing first and why. Use when the user wants a wide menu.

### Idea-proposal format (default when the user wants a recommendation, not a long list)

The house style for "what should we build / bet on" and every "another idea / different one"
follow-up. Fewer, sharper, punchier bets the user can act on.

**Batch rule.** 3 bets, each on a *distinct customer/model axis* - never three flavors of one
idea. Open with a header naming this round's axis theme (e.g. "다른 아이디어 3개 (모델 전환형)").
On "another / different one", jump to a genuinely new axis (new customer, new model), not a
re-skin of the last round.

**Per-idea skeleton** (letter them P/Q/R or A/B/C; mark the winner inline with ` <- 추천`):

    X. <one-line concept> (<customer + model tag>) <- 추천
    - 누가→무엇: <who> -> <what job> (JTBD, one line)
    - 무료 미끼: the customer-attraction item (lead magnet) that pulls users in
    - 수익화: one model (link strategy.md monetization-strategy)
    - 차별화(복제난이도): why a competitor *or AI* cannot copy it, tied to the user's named
      unfair advantage. "AI X + Y" with no moat is not differentiation - say so out loud.
    - 왜 지금 (winner) / 주의 (runners-up): timing thesis for the pick; the two-sided caveat
      (cannibalization, arming competitors, chicken-and-egg, regulation) for the rest.

**Then:**
- **Comparison table** across decision axes: revenue ceiling | fit to the user's strengths |
  marketing/CAC burden | copy-difficulty (moat) | validation speed.
- **One decisive pick** + a one-line *why* with a concrete, memorable frame (an analogy or a
  sharp economic phrase - "곡괭이 파는 청바지", "인프라 지대" - beats a generic sentence). Do not
  dodge with "it depends".
- **Cheapest validation** for the pick, timeboxed: experiment, success threshold (a number),
  decision rule (link `brainstorm-experiments`).
- **Critic** (the skill's gate, `critic.md`): keep it compact when the per-idea 주의 already
  carried the two-sided risks; always label market numbers/competitor claims as assumptions.
- **Next move:** offer to narrow the chosen bet one level deeper.

Voice: concrete and punchy over hedged and generic. Principle: *lucrative lives in distribution
and moat, not the product* - lead each idea's free hook as the growth asset, and never present a
market size or revenue figure as fact.

## brainstorm-experiments

Design the cheapest test that could disconfirm an assumption.

- **Existing:** A/B test, fake door, feature flag rollout, concierge, painted door, instrumented
  prototype. Define the metric and the decision threshold up front.
- **New (pretotyping, Savoia):** pinocchio, mechanical turk, provincial, fake-door landing page,
  YouTube/one-night-stand. Measure with skin-in-the-game data (signups, pre-orders), not opinions.
- **Output per experiment:** assumption tested, method, success metric + threshold, cost/time,
  what each outcome decides.

## identify-assumptions

List what must be true for the idea to succeed - the things that, if false, kill it.

- **Existing product - four risks (Cagan):** Value (will they want it?), Usability (can they use
  it?), Feasibility (can we build it?), Viability (does it work for the business?).
- **New product - eight risks:** value, demand, usability, feasibility, viability, channel,
  market-timing, legal/regulatory.
- **Output:** assumptions grouped by category, each phrased as a falsifiable statement.

## prioritize-assumptions

Plot assumptions on **Impact x Risk** (Torres "leap of faith"): high-impact + high-uncertainty
= test first. For each top assumption, suggest the cheapest experiment (link to
brainstorm-experiments) and the decision its result drives.

## prioritize-features

Rank a backlog by **impact, effort, risk, strategic alignment**. Use a named framework from
`execute.md` (RICE/ICE) for the score; never rank on gut alone. Output a ranked table with the
score components visible and the cut line called out.

## analyze-feature-requests

Categorize raw requests by theme, underlying job, and fit with strategy. Output: themes ranked
by frequency x segment-value, the job behind each, and a keep/park/decline call per theme with
reasoning - not a promise to build.

## opportunity-solution-tree (Teresa Torres)

Structure discovery as a tree:

```
Desired Outcome (one measurable business/product outcome)
└── Opportunities (customer needs, pains, desires - from research, not solutions)
    └── Solutions (ideas that address the opportunity)
        └── Experiments (tests that de-risk the solution)
```

Rules: opportunities are needs phrased in the customer's words (sourced from research or live
signal - `reference/signal.md`), never solutions. One outcome per tree. Compare solutions
*within* an opportunity, not across. Output the tree + the opportunity you would pursue first and why.

## interview-script (JTBD)

Customer interview script anchored in jobs-to-be-done and the moment of struggle.

- **Structure:** warm-up -> last time you faced <problem> (story, not hypotheticals) -> what did
  you do / try / hire -> what was hard -> what would "great" look like. Ask about the past, not
  the future. No leading questions, no pitching.
- **Output:** 8-12 open questions + probes, sequenced.

## summarize-interview

Transcript -> structured signal: the job, the struggling moment, current solutions + workarounds,
desired outcomes, surprises, verbatim quotes, and 3-5 action items. Separate observation
(what they said) from interpretation (what you infer).

## metrics-dashboard

Define the measurement frame: **North Star metric** (the one value-capture metric), 3-5 **input
metrics** that move it, guardrail metrics, and alert thresholds. Tie each input metric to a lever
the team controls. Output: the metric tree + why the North Star captures delivered value.

## Critic completeness anchors

OST has exactly one outcome and no solutions masquerading as opportunities; assumptions are
falsifiable and risk-categorized; experiments name a threshold and a decision; metric trees link
inputs to the North Star.
