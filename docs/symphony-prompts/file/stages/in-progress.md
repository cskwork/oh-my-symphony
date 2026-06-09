### IMPLEMENT  -- when state is `In Progress`

**Allowed tools (advisory).** Read full repo + docs + ticket. Write source within the agreed Plan scope, `docs/{{ issue.identifier }}/work/`, and ticket comments. Run tests, formatters, `git add` / `git commit` (Symphony commits at turn end). Do NOT push branches, merge, or open PRs — the Learn Merge Gate handles integration.

Ship the smallest change that satisfies the brief.

1. **Read the plan first.** Re-read the latest `## Plan` and `docs/{{ issue.identifier }}/plan/implementation-plan.md` if it exists. That plan should be enough to implement. Use Explore notes, llm-wiki, or other docs only as reference material when the plan is ambiguous or incomplete. If `## Plan` is missing: set state to `Plan`, append `## Plan Missing`, stop. Fresh context — the markdown is the contract.
2. **Rewind scope.** `$SYMPHONY_REWIND_SCOPE` is a JSON `{severity, file, line, fix}[]` Symphony sets on Review→In Progress / QA→In Progress rewinds. Scope this turn to those files; touching any other file needs a one-line `## Scope Expansion` rationale (commit gets a `[scope-expand]` marker — non-blocking). Unset → follow `## Plan`; unset AND the latest section is `## QA Failure` / `## Review Findings` → scope to those rows anyway.
3. Implement the chosen option from `## Plan` (on rewind: only the flagged items). Reopen the plan only if the brief got a fact wrong — append a one-line `## Plan Adjustment` and proceed.
4. TDD loop: write the failing test the brief specified, make it pass, refactor. No production code without a test exercising it.
5. Write user-facing docs at `docs/{{ issue.identifier }}/work/feature.md` (`bug.md` if the ticket carries the `bug` label): what changed, how a user observes it, knobs/flags. Plain language, no jargon.
6. Before `Review`: write one concise commit subject to `.symphony/commit-message.txt`; append `## Implementation` — intent per change and decisions worth recording.
7. Set state to `Review`.
