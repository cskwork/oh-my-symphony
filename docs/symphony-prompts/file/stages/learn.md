### LEARN -- make the next ticket easier

**Allowed tools (advisory).** Read `docs/{{ issue.identifier }}/{work,qa}/`, prior ticket sections, and `docs/llm-wiki/`. Write wiki files and ticket comments only, then run final git history commands from the host repo. Do NOT edit source or run the Merge Gate here; Verify already did it.

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
7. Final History Gate:
   - Update the ticket frontmatter to `Human Review` as part of this gate, then commit that final delivery record from the host repo before exposing it to a human.
   - Stage exact paths only: the ticket file, `docs/llm-wiki/` files you changed, and any `docs/{{ issue.identifier }}/learn/` or `docs/{{ issue.identifier }}/work/` artifact you wrote in Learn; do not use `git add -A`.
   - If there is a diff, run `git commit` with a message like `chore({{ issue.identifier }}): record human review handoff`.
   - If the target branch has a remote/upstream, run `git push`, then verify the remote tip with `git ls-remote`. Record the local SHA, remote SHA, branch, and exact commands in `### Evidence`.
   - If commit, push, or remote-tip verification fails, append `## History Failure`, set state to `Blocked`, name the failing command/stdout/stderr, and stop. Do not leave a card in `Human Review` when its final history is only local or dirty.
8. Set state to `Human Review` only after the Final History Gate passes. Do not set `Done`; a human must confirm.

Operator skip: the TUI/web skip action may append `## Learn Skipped` and move this ticket directly to `Human Review` with zero agent turns. Agents must not simulate that skip themselves.
