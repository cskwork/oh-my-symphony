### DONE -- final readable report

**Allowed tools (advisory).** Read full ticket history and prior sections. Write ticket comments only. Run read-only commands. Do NOT edit source; the ticket has already shipped.

Terminal. Verify passed, the Verify Merge Gate recorded `## Merge Status`, and Learn wrote wiki updates plus the final delivery report. If this card came through Human Review, a human explicitly confirmed the intervention handoff.

Goal for this lane: leave one concise As-Is -> To-Be report that a human can read later to understand the delivered change, why it was chosen, what evidence proves it, and what still is not proven.

1. Append `## As-Is -> To-Be Report` in this exact structure:

   ```
   ## As-Is -> To-Be Report

   ### Goal
   - <user outcome in plain language>

   ### As-Is
   - <prior behaviour, with evidence>

   ### To-Be
   - <new behaviour, with matching evidence>

   ### Reasoning
   - Why this approach over alternatives.
   - Trade-offs accepted.
   - Follow-ups intentionally deferred.

   ### Evidence
   - Commands run during Verify, with exit codes.
   - Test names, file paths, artefact locations.
   - `docs/{{ issue.identifier }}/reproduce/` -- bug reproduction, if any.
   - `docs/{{ issue.identifier }}/work/` -- implementation notes.
   - `docs/{{ issue.identifier }}/qa/` -- review, QA, and merge evidence.

   ### Not Covered
   - <remaining risk, follow-up, or `none`>

   ### How To Re-run
   - <exact command or evidence path a later operator should use>
   ```

2. Append `## Merge Status` confirming the target branch and merge evidence. If Verify left merge evidence missing, do not invent it; append `## Merge Missing`, set state to `Blocked`, and stop.
3. `hooks.after_done` (if configured in `WORKFLOW.md`) fires automatically after Done handling.
4. Leave state as `Done` and stop.
