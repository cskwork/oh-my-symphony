# WAYFINDER - map a large effort into a ticket frontier

Use when the user asks to break down a large or foggy objective: roadmap, issue plan, ticket split,
multi-session effort, blocker graph, "what should we do first?", or "turn this plan into tickets".
WAYFINDER is planning and tracker work, not product delivery. It can create or update issue records, but
it writes no product code by default.

## GREENFIELD scope gate

When the user asks to build/make/ship a new app or tool, keep the top-level mode `GREENFIELD`. If that
GREENFIELD objective is broad, foggy, roadmap-shaped, or clearly multi-session, use this file as an
internal Frontier Map before Build instead of switching the user-facing route to WAYFINDER.

The run vault root still carries the original GREENFIELD objective. Put the map under
`wayfinder/map.md`, put slice tickets under `wayfinder/tickets/`, pick one unblocked frontier ticket,
then copy only that ticket's acceptance checks into the delivery `GOAL.md` / `PLAN.md`. Do not build
sibling tickets in the same context.

## Theory

The map is not the work. It is a routing artifact: destination, settled decisions, open decisions,
blocked work, and the next unblocked ticket. Delivery still happens when a frontier ticket routes to
GREENFIELD, DEBUG, LEGACY, QA-ONLY, REVIEW-ONLY, or PROTOTYPE.

## Tracker choice

Use the target repo's native tracker when it is already configured, named by the user, or documented in
repo rules. Otherwise write local markdown under the current run vault's `wayfinder/` subfolder, beside
the root `GOAL.md`/`PLAN.md`/`QA.md` for the large objective. In this repo's default vault shape, that is
`docs/changelog/<YYYY-MM>/<DD-topic>/wayfinder/` - the same dated task folder that holds `GOAL.md`, with
WAYFINDER details nested below it.

Local shape:

```text
docs/changelog/<YYYY-MM>/<DD-topic>/
  GOAL.md
  PLAN.md
  QA.md
  wayfinder/
    map.md
    tickets/
      001-short-title.md
```

Ask only when the choice changes visibility or permissions, for example publishing GitHub issues instead
of local docs.

## Map contract

`map.md` or the parent issue is an index, not a long spec. Keep these headings:

- `Destination` - one concrete endpoint the effort is trying to reach.
- `Current state evidence` - links to source docs/code/issues used to build the map.
- `Decisions so far` - durable choices already settled; do not re-ask them on resume.
- `Not yet specified` - load-bearing decisions still open.
- `Out of scope` - tempting adjacent work to keep out.
- `Ticket graph` - ticket list with status and `Blocked by:` edges.
- `Frontier` - the next unblocked ticket(s), ordered by risk and leverage.

## Ticket contract

Each ticket is a vertical slice by default and acts as a `GOAL.md` detail slice:

- Complete enough to demo or verify by itself.
- Owns one context or seam; do not mix unrelated modules.
- Names the route: `Route: GREENFIELD|DEBUG|LEGACY|QA-ONLY|REVIEW-ONLY|PROTOTYPE`.
- Carries acceptance checks and proof commands, not only tasks.
- Lists `Blocked by:` ticket ids or `none`.
- Lists `Unblocks:` ticket ids when known.
- Names scope boundaries and explicit non-goals.

Research-needed tickets stay WAYFINDER tickets. When the decision needs knowledge outside the current
repo or recorded Domain Brief, write `Research: reference/research.md -> <question>` in the ticket and
link the resulting Markdown asset from the resolution. Research answers a decision; it does not deliver
the destination.

The run vault root `GOAL.md` preserves the original large objective and links `wayfinder/map.md`. When
one frontier ticket is selected for delivery, copy only that ticket's acceptance checks into the delivery
run's `GOAL.md`; do not copy the whole map or sibling tickets.

## Ticket depth

Use ticket-depth sections when the user asks to spec the work, when the ticket has high rework cost, or
when acceptance is ambiguous. Keep the depth inside the WAYFINDER ticket or its same ticket subfolder;
do not create a parallel `docs/spec/<feature-slug>/` workflow.

Add only the sections that help this ticket become executable:

- `Glossary` - one name per domain concept; do not use synonyms for the same concept.
- `User story` - `As a [role], I want [feature], so that [benefit]`.
- `Acceptance criteria` - EARS-style checks such as `WHEN [event] THEN [system] SHALL [response]`.
- `Edge cases` - empty/null input, boundaries, errors, authorization, recovery, and concurrency.
- `Design notes` - how this ticket meets its criteria; record only current requirements.
- `Decision records` - options considered, decision, and rationale for choices hard to reverse.
- `Task checklist` - small coding tasks with `_Requirements:_` or acceptance-check references.

Grill load-bearing decisions one question at a time with a recommended answer. If code can answer the
question, inspect the code instead of asking. Challenge vague terms, stress-test boundaries with concrete
scenarios, and write settled answers into the ticket immediately. The user may say "draft the rest"; then
record remaining assumptions in the ticket and continue unless a genuine ambiguity changes product
behavior.

EARS criteria, user stories, and task references strengthen the ticket; they never replace ground truth.
Final verification is still the project's real tests plus the proof commands named by the ticket.

Wide refactors use an expand-contract split instead of one broad ticket: add the new seam/adapter with
coverage, migrate callers, then delete the old path. One adapter is still hypothetical; two real callers
make the seam worth a ticket.

## Research assets

Use `reference/research.md` when a ticket needs official docs, upstream source, specs, first-party APIs,
standards, release notes, or other high-trust evidence before the decision can be made. Keep the
research output as a linked asset under the current run vault's `wayfinder/tickets/` folder. The map
records only the decision gist and link; the cited details live in the research file or ticket.

## Frontier rule

Work one frontier ticket per session. A frontier ticket is unblocked, high-leverage, and small enough to
verify independently. After it closes, update the map, recompute the frontier, then stop.

For large efforts, do not start a second ticket in the same context. Ask the user to clear context before
the next ticket, and ask for the integration test / end-to-end check that should run before continuing.
If no integration test exists, ask whether to add one as the next frontier ticket or record the gap as
`Not covered`.

## Resume

On resume, read the map and closed ticket trail first. Do not re-ask decisions already recorded in
`Decisions so far` or closed ticket comments. If the current code contradicts the map, update the map with
the fresh evidence and mark the stale assumption.

## Exit

WAYFINDER is done when the map exists, tickets are vertical, blocker edges are explicit, and the next
frontier is named. If the user wants implementation, route exactly one frontier ticket into its mode and
carry only that ticket's acceptance checks into `GOAL.md`. After that ticket verifies, stop with:

- the closed ticket id,
- the integration test / end-to-end check status or requested command,
- the next frontier id,
- a request to clear context before the next task.
