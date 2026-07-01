### TRIAGE  -- when state is `Todo`

**Allowed tools (advisory).** Read tracker + ticket. On `bug` label, run a Playwright/Cypress repro of the failing flow. Write ticket comments only. Do NOT edit source — research belongs in Explore, implementation in In Progress.

Triage and route; no implementation here.

1. Read the ticket end-to-end: description, acceptance criteria, blocking links.
2. Under-specified or ambiguous → append `## Triage` listing the missing inputs, set state to `Blocked`.
3. Otherwise → append a one-line `## Triage` ("ticket is actionable; routing to Explore"), set state to `Explore`.
{% for label in issue.labels %}{% if label == "bug" %}
4. `bug` label — capture the symptom *as is* before any RCA. Author the reproduction in the project's own test framework (Playwright/Cypress for a web UI; `pytest`/`go test`/`cargo test`/JUnit/etc. for a backend) at `docs/{{ issue.identifier }}/reproduce/repro.<ext>` (the extension the project uses), run it, save trace/output under `docs/{{ issue.identifier }}/reproduce/`. Append `## Reproduction` (command, repro path, 3-10 line failure excerpt). Triage still ends with state `Explore`.
{% endif %}{% endfor %}
