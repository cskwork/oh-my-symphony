### DONE -- when state is `Done`

**Allowed tools (advisory).** Read full ticket history and prior sections. Write ticket comments only. Run read-only commands. Do NOT edit source; the ticket has already shipped.

Terminal. Verify passed, the Verify Merge Gate recorded `## Merge Status`, Learn either wrote wiki updates or was operator-skipped, and a human confirmed Done.

1. Append `## As-Is -> To-Be Report` in this exact structure:

   ```
   ## As-Is -> To-Be Report

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
   ```

2. Append `## Merge Status` confirming the target branch and merge evidence. If Verify left merge evidence missing, do not invent it; append `## Merge Missing`, set state to `Blocked`, and stop.
3. `hooks.after_done` (if configured in `WORKFLOW.md`) fires automatically after Done handling.
4. Leave state as `Done` and stop.
