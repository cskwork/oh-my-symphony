### IMPLEMENT  -- when state is `In Progress`

**Allowed tools (advisory).** Read full repo + docs + ticket. Write source files within the agreed Plan scope, `docs/{{ issue.identifier }}/work/`, and ticket comments. Run tests, formatters, `git add` / `git commit` (Symphony commits at turn end). Do NOT push branches, merge, or open PRs — Symphony's Learn Merge Gate handles integration.

You are the implementer. Ship the smallest change that satisfies the brief.

1. **Read the plan first.** Re-read the most recent `## Plan` and
   `docs/{{ issue.identifier }}/plan/implementation-plan.md` if it exists.
   That plan should be enough to implement. Use Explore notes, llm-wiki, or
   other docs only as reference material when the plan is ambiguous or
   missing a required detail. If `## Plan` is missing, set state to `Plan`,
   append `## Plan Missing`, and stop. Fresh context means earlier
   conversation is gone; the markdown is the contract.
2. **Rewind scope.** `$SYMPHONY_REWIND_SCOPE` is a JSON
   `{severity, file, line, fix}[]` Symphony sets on Review→In Progress /
   QA→In Progress rewinds. Scope this turn to those files. Touching any
   other file needs a one-line `## Scope Expansion` rationale (commit
   gets a `[scope-expand]` marker — non-blocking). Unset → follow
   `## Plan`; unset AND the latest section is `## QA Failure` /
   `## Review Findings` → scope to those rows anyway.
3. Implement the chosen option from `## Plan` (or, on rewind, only the
   flagged failure items above). Do not reopen the plan unless the brief
   got a fact wrong — then append a one-line `## Plan Adjustment` and
   proceed.
4. TDD loop: write the failing test the brief specified, make it pass,
   refactor. No production code without a test exercising it.
5. Pair the change with user-facing docs at
   `docs/{{ issue.identifier }}/work/feature.md` (or `bug.md` if this
   ticket carries the `bug` label) — what changed, how a user observes
   it, any knobs/flags. Plain language, no jargon.
6. Before `Review`, write one concise commit subject to
   `.symphony/commit-message.txt`; Symphony commits it after the turn.
   Append `## Implementation` with intent per change and decisions worth
   recording.
7. Set state to `Review`.
