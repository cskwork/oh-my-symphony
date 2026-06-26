### IMPLEMENT  -- when state is `In Progress`

**Allowed tools (advisory).** Read full repo + docs + ticket. Write source within the agreed Plan scope, `docs/{{ issue.identifier }}/work/`, and tracker comments. Run tests, formatters, `git add` / `git commit` (Symphony commits at turn end). Do NOT push branches, merge, or open PRs beyond the draft PR below — the Learn Merge Gate handles integration.

Ship the smallest change that satisfies the brief.

1. **Conflict pre-check.** List Linear active issues (state `In Progress`, `Review`, or `QA`) and read their `docs/<other-id>/explore/` Touched Files for overlap with this ticket's `## Touched Files`. On overlap: transition state to `Blocked`, post a `## Conflict` comment with the other issue id, overlapping path(s), and a one-line reason ("waiting on <ID> to finish editing <path>"); STOP.
2. **Read the plan first.** Re-read the latest `## Plan` and `docs/{{ issue.identifier }}/plan/implementation-plan.md` if it exists. That plan should be enough to implement. Use Explore notes, llm-wiki, or other docs only as reference material when the plan is ambiguous or incomplete. If `## Plan` is missing: transition state to `Plan`, post `## Plan Missing`, stop. If the most recent comment is a QA Failure or Review Findings comment, scope this turn to ONLY those flagged items — no drive-by changes. Fresh context — the markdown and Linear comments are the contract.
   - **Critic rewind.** Most recent comment is a Surfaced Requirements comment (with Critic Tests): make exactly those failing tests pass with the smallest change, add no code no red test requires, break no passing test, then mark each row `fixed` (or why-still-`open`) in `docs/{{ issue.identifier }}/critic/surfaced-requirements.md`.
3. Implement the chosen option from `## Plan` (on rewind: only the flagged items). Reopen the plan only if the brief got a fact wrong — post a one-line note and proceed.
4. TDD loop: write the failing test the brief specified, make it pass, refactor. No production code without a test exercising it.
5. Write user-facing docs at `docs/{{ issue.identifier }}/work/feature.md` (`bug.md` if the ticket carries the `bug` label): what changed, how a user observes it, knobs/flags. Plain language, no jargon.
6. Write one concise commit subject to `.symphony/commit-message.txt` (Symphony commits it after the turn). Open a draft PR. Post an Implementation comment with the PR link, intent per change, and decisions worth recording.
7. Set the next state by difficulty (Plan's `## Difficulty`; treat a missing section as `standard`):
   - Plan declared `## Difficulty: trivial` AND this is **not** a `bug` ticket → transition state to `Review` (skip Critic — the visible tests already cover this single-spot change).
   - Otherwise (`standard` / `complex`, or any `bug` ticket regardless of difficulty) → transition state to `Critic`.
   Post a one-line `## Pipeline Route` comment recording the choice and why, so any elision is never silent — e.g. `trivial, non-bug → Review (Critic skipped)` or `standard → Critic`. A `bug` ticket may never skip Critic+QA: state this in the route when difficulty was `trivial` but the `bug` label forced the full path.
