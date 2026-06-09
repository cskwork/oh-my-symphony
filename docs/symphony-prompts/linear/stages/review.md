### REVIEW  -- when state is `Review`

**Allowed tools (advisory).** Read full repo, `git diff` / `git show`, ticket body, `docs/{{ issue.identifier }}/work/`. Post tracker comments only (Security Audit, Review, Review Findings). Run read-only `git`, lightweight static analysis, and live HTTP probes only when this ticket changed runtime API behavior. Do NOT edit source — fixes belong to In Progress on the rewind.
{% for label in issue.labels %}{% if label == "chore" %}
**Chore short-circuit.** `chore` label — no severity table or live HTTP probes:
1. Read the diff: `git show HEAD --stat`, then `git show HEAD`.
2. The diff must match `## Plan` exactly; only Plan-named files (plus `docs/{{ issue.identifier }}/` artefacts) may change.
3. Match → post a one-line Review comment ("chore — diff matches plan, no findings"), transition state to `QA`.
4. Drift (files outside the plan, code beyond the metadata bump, anything runtime-affecting) → transition state back to `In Progress`, post a Review Findings comment with the drift as a HIGH row, stop. Never wave through real code changes.
{% endif %}{% endfor %}
Find issues; do not fix them.

1. Read `docs/{{ issue.identifier }}/work/` and the most recent Implementation comment. If a prior Review Findings comment exists, confirm those items are resolved before opening new findings.
2. Identify changed files and line ranges from the latest In Progress commit or PR diff, then open touched files end-to-end. Docs are reviewable deliverables; ignore root symlink/junction metadata for host-backed `kanban/` / `prompt/` plumbing unless the issue is about Symphony setup.
3. Checklist: clarity, naming, error handling, security, performance, simplicity, no dead code, no debug prints, no secrets.
4. Live HTTP proof only when this issue changed runtime API behavior or its acceptance criteria require endpoint execution. Docs-only API mapping / scenario issues: verify against source contracts, route definitions, schemas, and existing tests — do not probe live endpoints. When live proof is required: hit baseline (As-Is) and new build (To-Be) with curl/httpie/`requests`, save under `docs/{{ issue.identifier }}/verify/`: `baseline.json`, `pr.json`, `diff.txt`, `curl.log`.
5. Classify findings into a severity table: `severity | file:line | fix`. Cap 6 rows in the comment body; spillover goes to `docs/{{ issue.identifier }}/review/details.md`.
6. **If any CRITICAL, HIGH, or MEDIUM finding exists:** transition state back to `In Progress`, post a Review Findings comment (plain-language header + severity table, referencing any verify artefacts), and STOP. Do NOT fix findings inside Review; Symphony dispatches a fresh fix turn.
7. Prior findings resolved and nothing ≥ MEDIUM remains → do not post another Review Findings comment. Post a Review comment and transition state to `QA` in the same turn; staying in `Review` after a clean review is a workflow failure.
8. Only LOW findings (or none) → post a Review comment (header + the same severity table; flag deferred LOW items so Learn can address them), transition state to `QA`.
9. Genuinely out of scope or unfixable → transition state to `Blocked`, post a Blocker comment with what is needed and stop.
