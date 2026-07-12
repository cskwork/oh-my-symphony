# EXECUTE - delivery

Frameworks for turning strategy into shipped work. The bias: outcomes over output, smallest
artifact that aligns the team, and a decision the artifact actually drives.

## create-prd (8 sections)

1. Problem / context (who, what pain, evidence) - 2. Goals + success metrics (measurable) -
3. Non-goals (explicit out-of-scope) - 4. User stories / use cases - 5. Requirements
(functional + non-functional, prioritized) - 6. UX / flows (or links) - 7. Risks, assumptions,
dependencies - 8. Rollout + measurement plan. Output: each section grounded in real inputs;
mark unknowns as assumptions. A PRD with no success metric is incomplete.

## brainstorm-okrs

Team-level OKRs: one ambitious **Objective** (qualitative, inspiring) + 3-5 **Key Results**
(measurable outcomes, not tasks). KRs measure results (activation +10%), never activity (ship 3
features). Output: 1-2 objectives, each with KRs that are outcome-shaped and a baseline.

## outcome-roadmap

Convert a feature list into an outcome-oriented roadmap: group features under the outcome they
serve, sequence by Now / Next / Later (not hard dates), and state the outcome metric per row.
Output: rows of `Outcome -> bets -> metric`, with features as supporting detail, not headers.

## sprint-plan

Sprint goal (one sentence), committed items mapped to the goal, capacity check, dependencies,
risks. Output: the goal + a committed list where every item ladders to the goal; cut anything
that does not.

## retro

Structured reflection: what went well / what didn't / what to change, or Start-Stop-Continue.
Output: observations + 2-3 concrete, owned action items with due dates. A retro with no owned
actions failed.

## release-notes

Audience-shaped changelog: what changed, why it matters to the user, any action needed. Lead
with user value, not internal feature names. Provide internal + external variants if relevant.

## pre-mortem (Klein)

Imagine the launch failed; work backward. Steps: assume failure 6 months out -> brainstorm every
plausible cause -> rank by likelihood x impact -> define a preventive action per top risk.
Output: ranked failure causes + mitigations + the one risk most worth de-risking now.

## stakeholder-map

Plot stakeholders on **Power x Interest** (manage closely / keep satisfied / keep informed /
monitor). Output: the grid + a comms plan per quadrant (cadence, channel, message). Names go in
the grid, not a flat list.

## summarize-meeting

Transcript/notes -> decisions, action items (owner + due), open questions, key context. Separate
decided from discussed. Output: a scannable block a non-attendee can act on.

## user-stories / job-stories / wwas

- **User story:** `As a <role>, I want <capability> so that <benefit>` + acceptance criteria
  (Given/When/Then). Keep them small and vertical.
- **Job story:** `When <situation>, I want to <motivation>, so I can <expected outcome>` -
  context over persona; use when the persona is less load-bearing than the trigger.
- **WWAS (Who-What-And-So):** who / what they do / and so what outcome - a compact framing for
  quick alignment.

## test-scenarios

Derive test cases from requirements: happy path, edge cases, error/permission cases, boundary
values. Output: a scenario table (precondition / action / expected) covering each requirement
and each failure mode.

## dummy-dataset

Generate realistic synthetic data matching a described schema for demos/tests: respect types,
ranges, distributions, and referential integrity; flag any field that must stay non-PII.

## prioritization-frameworks (pick one, show the math)

| Framework | Formula / rule | Best for |
|---|---|---|
| **ICE** | (Impact x Confidence x Ease), 1-10 each | fast, rough ranking |
| **RICE** | (Reach x Impact x Confidence) / Effort | backlog with reach data |
| **MoSCoW** | Must / Should / Could / Won't | scope negotiation |
| **Kano** | basic / performance / delight | feature-set balance |
| **WSJF** | Cost of Delay / Job Size | SAFe / flow-based teams |
| **Value vs Effort** | 2x2 quadrant | quick visual triage |
| **Opportunity scoring** | importance - satisfaction (Ulwick) | ODI / unmet needs |
| **Story mapping** | backbone + slices | release slicing |
| **Cost of Delay** | economic urgency over time | sequencing |

Always show the score components so the ranking is auditable. Never present a ranking without the
inputs that produced it.

## strategy-red-team

Adversarial stress-test of a plan/strategy/PRD. Argue the opposite, attack the strongest claim,
enumerate failure modes, find the unstated assumptions, and name what evidence would change the
call. This is the full version of `critic.md`'s optional deep red-team. Output: ranked
vulnerabilities + the single change that most de-risks the plan.

## Critic completeness anchors

PRDs name a measurable success metric and explicit non-goals; OKR key results are outcomes with
baselines, not tasks; roadmaps are organized by outcome; prioritization shows its score inputs;
retros and pre-mortems end in owned, dated actions.
