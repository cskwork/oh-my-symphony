### TRIAGE -- is this ticket ready?

**Allowed tools (advisory).** Read tracker + ticket. On `bug` label, capture the failing symptom before routing. Write ticket comments only. Do NOT edit source.

Goal for this lane: make the next action obvious to any board reader. A Todo card should end as either "ready to build" or "blocked because these inputs are missing." No implementation here.

1. Read the ticket end-to-end: description, acceptance criteria, blockers.
2. Under-specified or ambiguous -> append `## Triage` with the missing input, who can answer it if known, and why work cannot start. Set state to `Blocked`, stop.
3. Otherwise -> append one-line `## Triage` (`ticket is actionable; routing to In Progress because <ready reason>`), set state to `In Progress`.
{% for label in issue.labels %}{% if label == "bug" %}
4. `bug` label: capture the before state before any root-cause analysis. Author the reproduction in the project's own test framework at `docs/{{ issue.identifier }}/reproduce/repro.<ext>`, run it, save trace/output under `docs/{{ issue.identifier }}/reproduce/`, and append `## Reproduction` with command, repro path, what failed, and a 3-10 line failure excerpt. Triage still ends at `In Progress`.
{% endif %}{% endfor %}
