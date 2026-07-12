# Research - high-trust source pass

Use when a planning, docs, API, or design decision needs facts outside the current working set. In
WAYFINDER, this is the helper for a ticket whose answer depends on official docs, upstream source,
specs, first-party APIs, standards, or current vendor behavior.

Research resolves a knowledge question. It does not satisfy delivery Done, does not ship product code,
and does not replace the selected route's real tests when implementation starts.

## Source contract

- Prefer primary sources: official docs, source code, specs, standards, first-party APIs, changelogs,
  release notes, issue/PR discussion by the owning project, or live behavior you can verify.
- Follow each important claim back to the source that owns it. Secondary write-ups can suggest leads,
  but they do not settle facts that primary sources can answer.
- Record dates for unstable facts: current APIs, prices, legal/regulatory rules, release status,
  product limits, compatibility, and security guidance.
- Name uncertainty. If sources conflict or a primary source is unavailable, write the gap and the
  conservative assumption instead of smoothing it over.

## Output

Write one single Markdown asset. Match the repo's existing note location. In a WAYFINDER run, prefer:

```text
docs/changelog/<YYYY-MM>/<DD-topic>/wayfinder/tickets/<ticket-id-or-slug>/research.md
```

If there is no WAYFINDER ticket, use:

```text
docs/changelog/<YYYY-MM>/<DD-topic>/research/<slug>.md
```

Use these headings:

- `Question` - the exact thing the research must decide.
- `Sources consulted` - source name, URL/path, access date, and why it is authoritative.
- `Findings` - concise claims; cite each claim with a source URL or local path.
- `Applicability` - what this means for the current repo, ticket, or plan.
- `Gaps` - unresolved facts, conflicts, stale sources, and what would prove them.
- `Next step` - one action: update the map, ask the user, prototype, or route a delivery ticket.

## Dispatch

If background subagents are available, dispatch one research agent with the question, output path, and
source preferences. Keep working only on independent planning work while it reads. If subagents are not
available, run the same source contract inline.

Do not paste long source text. Summarize, cite, and link. Keep the final answer small enough that a
fresh-context agent can load it before resolving the ticket.

## Return To WAYFINDER

When the research asset is written:

- Link it from the WAYFINDER ticket.
- Add the answer summary to the ticket resolution or map decision.
- Graduate newly specific fog into tickets only if the research made the question sharp.
- Keep out-of-scope findings out of `Decisions so far`; record them under `Out of scope` when useful.
