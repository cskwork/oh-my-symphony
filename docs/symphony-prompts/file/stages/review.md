### REVIEW  -- when state is `Review`

**Allowed tools (advisory).** Read full repo, `git diff` / `git show`, ticket body, `docs/{{ issue.identifier }}/work/`. Write ticket comments only (`## Security Audit`, `## Review`, `## Review Findings`). Run read-only `git`, lightweight static analysis, and live HTTP probes only when this ticket changed runtime API behavior. Do NOT edit source — fixes belong to In Progress on the rewind.
{% for label in issue.labels %}{% if label == "chore" %}
**Chore short-circuit.** `chore` label — no Security Audit, severity table, or live HTTP probes:
1. Read the diff: `git show HEAD --stat`, then `git show HEAD`.
2. The diff must match `## Plan` exactly; only Plan-named files (plus ticket / `docs/{{ issue.identifier }}/` artefacts) may change.
3. Match → append a one-line `## Review` ("chore — diff matches plan, no findings"), set state to `QA`.
4. Drift (files outside the plan, code beyond the metadata bump, anything runtime-affecting) → set state back to `In Progress`, append `## Review Findings` with the drift as a HIGH row, stop. Never wave through real code changes.
{% endif %}{% endfor %}
Find issues; do not fix them.

1. Read `docs/{{ issue.identifier }}/work/` and the latest `## Implementation`. If a prior `## Review Findings` exists, confirm those items are resolved before opening new findings.
2. Identify changed files and line ranges from the latest In Progress wip commit (`git show --stat`, then `git show --unified=0`); `git diff` / `git status` only as fallback. Open touched files end-to-end. Docs are reviewable deliverables; ignore root symlink/junction metadata for host-backed `kanban/` / `prompt/` plumbing unless the ticket is about Symphony setup.
3. Checklist: clarity, naming, error handling, security, performance, simplicity, no dead code, no debug prints, no secrets. Then scan `git log --format=%s $(git config symphony.basesha)..HEAD` for `[no-test]` markers (prod change without a paired test). Each marker = HIGH row in `## Review Findings` (file = unpaired prod path; fix = "add a test exercising this change"). Exempt: commit touched only `docs/` / `kanban/` / `.symphony/` — note the exemption in `## Review`.
4. Live HTTP proof only when this ticket changed runtime API behavior or its acceptance criteria require endpoint execution. Docs-only API mapping / scenario tickets: verify against source contracts, route definitions, schemas, and existing tests — do not probe live endpoints. When live proof is required: hit baseline (As-Is) and new build (To-Be) with curl/httpie/`requests`, save under `docs/{{ issue.identifier }}/verify/`: `baseline.json`, `pr.json`, `diff.txt`, `curl.log`.
5. **Security Audit (mandatory, before `## Review`).** Append `## Security Audit` with exactly this 7-row table — same row order, no extras, no spillover:
   `check | verdict (pass/fail/n/a) | evidence (path:line or "n/a — <reason>")`
   rows: `secrets`, `input-validation`, `sql-injection`, `xss`, `csrf`, `authz`, `rate-limit`.
   `n/a` needs a reason in the evidence cell (e.g. `n/a — docs-only change`). Any `fail` row auto-promotes to a CRITICAL row in `## Review Findings` (file = the cited `path:line`, fix = "fix the security gap named in the audit") and triggers Review → In Progress.
6. Classify findings into a severity table: `severity | file:line | fix`. Include `[no-test]` HIGH rows from step 3 and `fail`-promoted CRITICAL rows from step 5. Cap 6 rows; spillover goes to `docs/{{ issue.identifier }}/review/details.md`.
7. **If any CRITICAL, HIGH, or MEDIUM finding exists:** set state back to `In Progress`, append `## Review Findings` (plain-language header + severity table, referencing any verify artefacts), and STOP. Do NOT fix findings inside Review; Symphony dispatches a fresh fix turn.
8. Prior findings resolved and nothing ≥ MEDIUM remains → do not append another `## Review Findings`. Append `## Review` and pick the next state per step 10 in the same turn; staying in `Review` after a clean review is a workflow failure.
9. Only LOW findings (or none) → append `## Review` (header + the same severity table; flag deferred LOW items so Learn can address them), then pick the next state per step 10.
10. **Next state by difficulty** (clean review only — steps 8/9): Plan declared `## Difficulty: trivial` AND this ticket did **not** change runtime behavior (the same condition that gates the live-HTTP probe in step 4) → set state to `Learn` (skip QA). Otherwise → set state to `QA` as today. Safety rails override difficulty: a `bug` ticket may never skip QA (repro closure stays mandatory), and any `fail` Security Audit row forces the full route to `QA` (it has already become a CRITICAL finding, so this path is unreachable on a clean pass). Append the routing decision to `## Review` as a one-line `## Pipeline Route` so any skipped QA is never silent — e.g. `trivial, no runtime change → Learn (QA skipped)` or `runtime change → QA`.
11. Genuinely out of scope or unfixable → set state to `Blocked`, append `## Blocker` with what is needed.
