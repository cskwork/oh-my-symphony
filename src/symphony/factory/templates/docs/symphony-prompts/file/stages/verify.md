### VERIFY — independent proof

Re-read the acceptance criteria and run the exact real tests independently.
For a browser surface with attached `superqa`, run SuperQA in REGRESSION mode
and record its report path, pass/fail counts, and side-effect counts. A required
behavior or regression defect rewinds this ticket. Record a non-blocking UX or
product improvement as a follow-up Wayfinder ticket; do not widen this ticket.
Append `## Verification` with a `criterion | command | result` table. Include
exactly one row for each bullet or numbered item under `## Acceptance criteria`.
Copy the complete item wording into the criterion cell (Markdown formatting may
differ). Do not split one item into multiple rows or merge multiple items into
one row. Name the exact relevant test or proof command, and make the result cell
exactly `pass`; put command output and supporting detail after the table. A bare
claimed pass without the named command is not evidence. Record remaining risk
after the table. If every row passes, set state to `Done`. If a product defect exists,
append `## QA Failure` with expected versus actual evidence and set state to
`Build`. If proof needs missing authority or environment, append `## Blocker`
and set state to `Blocked`.
