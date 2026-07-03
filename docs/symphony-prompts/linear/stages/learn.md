### LEARN -- make the next ticket easier

**Allowed tools (advisory).** Read `docs/{{ issue.identifier }}/{work,qa}/`, prior ticket sections, and `docs/llm-wiki/`. Write wiki files and ticket comments only. Do NOT edit source or run the Merge Gate here; Verify already did it.

Goal for this lane: turn one ticket's evidence into reusable memory and hand the card to a human with a plain-language decision package. Do not repeat the whole transcript; extract what the next operator or worker needs.

1. Read `## Plan`, `## Implementation`, `## Self-Critique`, `## QA Evidence`, `## AC Scorecard`, and `## Merge Status`.
2. Compare brief vs reality: the user's goal, before state, after target, assumptions that held or broke, constraints that surfaced, prior wiki entries that were incomplete or misleading, and evidence that remains `Not proven`.
3. Update `docs/llm-wiki/`: append a Decision-log row to an existing entry, or create/update `docs/llm-wiki/<topic-slug>.md`, then add/refresh its row in `INDEX.md`.
4. Append `## Learnings` -- 3-4 bullets of new facts, constraints, surprises, or rejected approaches that future work should not rediscover.
5. Append `## Wiki Updates` -- paths created/modified/removed, one line each.
6. Append `## Human Review` with this shape:
   - `### What Changed` -- 2-3 bullets.
   - `### Why It Matters` -- 1-2 bullets.
   - `### Evidence` -- commands/proofs with pass/fail, top evidence path, and how to re-run.
   - `### Risks` -- residual risks, not-covered areas, follow-ups, or `none`.
   - `### Human Checklist` -- 3-5 quickly verifiable checkboxes.
   - `### Decision Needed` -- exactly one line: `Confirm Done` or `Do not confirm; move back to <state> because <reason>`.
7. Set state to `Human Review`. Do not set `Done`; a human must confirm.

Operator skip: the TUI/web skip action may append `## Learn Skipped` and move this ticket directly to `Human Review` with zero agent turns. Agents must not simulate that skip themselves.
