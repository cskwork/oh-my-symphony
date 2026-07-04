### IMPLEMENT -- make the change and leave a proof map

**Allowed tools (advisory).** Read full repo + docs + ticket. Write source, tests, `docs/{{ issue.identifier }}/work/`, and ticket comments. Run tests and formatters. Do NOT push, merge, or open PRs; Verify owns the Merge Gate.

Goal for this lane: make the smallest correct change and leave a card that explains the goal, before state, after target, proof plan, and remaining risk. A fresh verifier should be able to audit the work without guessing what you intended.

1. Read `docs/llm-wiki/INDEX.md` first when it exists. Reuse current knowledge before broad repo search.
2. If the CLI supports subagents (for example Claude Code Task tool), delegate broad exploration, repo search, or verification sweeps to fresh-context subagents and keep the main context focused on ticket, plan, and diff. If no subagent support exists, do the same work locally and keep notes brief.
3. Produce or refresh these sections before editing source:
   - `## Plan` -- user goal, before state, after target, rejected approaches if any, and concrete implementation steps.
   - `## Acceptance Tests` -- one proof per acceptance criterion, with the signal and command or artifact that will prove it.
   - `## Done Signals` -- exact visible state Verify should see, including what would still be `Not proven`.
   - `## Difficulty` -- `trivial`, `standard`, or `complex` with one-line rationale.
4. TDD loop: write the failing test the brief implies, make it pass, refactor. No production code without a test or an explicit `chore` rationale.
5. Save durable work notes under `docs/{{ issue.identifier }}/work/` (at least one file). Include useful before/after notes, decisions, and how a user observes the change.
6. Append `## Implementation` with what changed, why this approach, and any alternatives rejected because they were too risky, broad, or speculative.
7. Self-critique before moving on: re-read the ticket/spec, check null/empty/boundary/error paths, add any missing tests, and append `## Self-Critique` with risk, not-covered items, and the exact focus for Verify.
   - Static browser apps that claim direct `file://` support must boot from `file://`; do not use `<script type="module">` or dynamic `import()` unless the acceptance path explicitly serves the app over HTTP.
8. Append `## Pipeline Route`: always route to `Verify`. Record whether Verify may use the trivial non-runtime QA short path, but never skip Verify.
9. Set state to `Verify`.

On rewind: `$SYMPHONY_REWIND_SCOPE` may contain JSON rows from `## Review Findings` or `## QA Failure`. Limit this turn to those rows unless a one-line `## Scope Expansion` explains why more files are required. Preserve the original goal; only widen scope when the recorded finding cannot be fixed inside the listed files.
