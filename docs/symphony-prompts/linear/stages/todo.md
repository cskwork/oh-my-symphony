### TRIAGE -- when state is `Todo`

**Allowed tools (advisory).** Read tracker + ticket. On `bug` label, capture the failing symptom before routing. Write ticket comments only. Do NOT edit source.

Triage and route; no implementation here.

1. Read the ticket end-to-end: description, acceptance criteria, blockers.
2. Under-specified or ambiguous -> append `## Triage` listing the missing inputs, set state to `Blocked`, stop.
3. Otherwise -> append one-line `## Triage` (`ticket is actionable; routing to In Progress`), set state to `In Progress`.
{% for label in issue.labels %}{% if label == "bug" %}
4. `bug` label: capture the symptom before any RCA. Author the reproduction in the project's own test framework at `docs/{{ issue.identifier }}/reproduce/repro.<ext>`, run it, save trace/output under `docs/{{ issue.identifier }}/reproduce/`, and append `## Reproduction` with command, repro path, and 3-10 line failure excerpt. Triage still ends at `In Progress`.
{% endif %}{% endfor %}
