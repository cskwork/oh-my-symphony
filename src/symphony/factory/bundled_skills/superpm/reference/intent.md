# Intent capture - classify, then interview only if ambiguous

Two jobs, in order: (1) classify the request into a domain + a target artifact, (2) if it is
genuinely underspecified, run a short clarifying interview before drafting. This crystallizes
*what to produce*; the Deliver step still confirms outward actions.

## 1. Classify

Pick one domain from the SKILL.md intent table and name the concrete artifact. The artifact
named in the request usually decides it ("write a PRD" -> EXECUTE / PRD). Resolve these by
reading first, never by asking:

- **Product / repo / data attached?** Read it for the real inputs (audience, metrics, prior
  decisions) before drafting.
- **A framework is named?** Use it. Do not interview about which framework to apply.
- **Multiple artifacts implied?** Name the primary deliverable; treat the rest as supporting
  frameworks pulled in during Draft, not separate artifacts.
- **A buildable thing to plan end-to-end?** "기획하자 / 이거 개발하고 싶어 / 기획 도와줘 / plan this
  feature / PRD부터 화면까지" with no single named artifact -> route to **PLAN**
  (`reference/plan.md`): a stepped `EXECUTE -> FLOW -> STORYBOARD -> PACKAGE` session that shares
  the SPEC as SSOT and gates after each stage. This overrides "name the primary deliverable" - the
  whole chain is the deliverable. (Only one stage named, e.g. "PRD만" -> stay single-artifact.)

Record the classification in one line: `Domain: <X> | Artifact: <Y> | Decision it serves: <Z>`.

## 2. Interview gate - when to ask vs skip

Interview only when the request is genuinely underspecified. Fire when **either** holds:

- The request has multiple plausible interpretations that would produce different artifacts, or
- A load-bearing input is missing and cannot be read from the product, docs, or data.

Skip when **any** holds (and log the skip in one line):

- The request is already clear and single-interpretation, or
- A quick read of the attached product/docs/data answers the missing input, or
- The artifact is a low-stakes draft the user will obviously edit (e.g. a brainstorm list).

Do not rely on the model default: LLMs default to not asking and misjudge underspecification.
But asking when the inputs already exist is a failure too - unnecessary questions burden the
user. Detect ambiguity against three triggers: missing goal, missing premises (audience, data,
constraints), ambiguous terminology.

## Coverage dimensions (selection menu, not a checklist to exhaust)

Draw questions from these axes. Pick the few that change the artifact; do not ask all of them.

1. **Audience / decision-maker** - who acts on this artifact, and what call do they make?
2. **Inputs / evidence** - what data, research, or prior decisions exist to ground it?
3. **Scope** - which product, segment, time horizon, or market is in vs out.
4. **Constraints** - budget, stage (new vs existing product), regulatory, brand, format.
5. **Definition of done** - depth expected, must-cover sections, examples, format.
6. **Stakes / reversibility** - is this going to a board, a customer, a public channel?

DISCOVER / STRATEGY / RESEARCH lean on audience + inputs + scope. EXECUTE leans on scope +
definition of done. ANALYTICS leans on inputs (the actual schema/metric) + definition of done.

## Question selection

- **Cap at <=5 questions, one round.** Ask only as many as the ambiguity requires; one or two
  usually settle it.
- **Maximize information gain.** Prefer the question that most narrows the space of viable
  artifacts - one that eliminates a whole branch.
- **Drop redundant questions.** If the product, docs, or data already answer an aspect, do not ask.
- **One batched round, a recommended answer per question.** Ask the selected questions
  together, each with your recommended answer, so "go with your recommendations" is a valid
  one-line reply. At most one follow-up round, only if an answer conflicts or unlocks a new
  must-have.

## Hard gate - block the draft

Do not start the Draft until must-have inputs are answered, or the user explicitly approves
drafting on stated assumptions. Every unanswered must-have becomes either an explicit
user-approved assumption or a blocker. New-vs-existing-product is almost always a must-have:
many frameworks (assumptions, experiments, GTM) branch hard on it.

## Recording

Write a compact `## Intent` block at the top of the artifact (or alongside it): the
classification line, the completion bar (the Frame step's "done and trustworthy" criterion -
what the critic checks against), and each question with its chosen answer or approved
assumption. Do not paste the whole exchange. A skipped interview gets one line stating why it
was safe to skip.
