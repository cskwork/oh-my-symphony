### PLAN  -- when state is `Plan`

**Allowed tools (advisory).** Read filesystem, `docs/{{ issue.identifier }}/explore/`, ticket body. Write only under `docs/{{ issue.identifier }}/plan/` and as ticket comments. Read-only commands only. Do NOT edit source, install dependencies, or run state-mutating tests — Plan is design, not execution.
{% for label in issue.labels %}{% if label == "chore" %}
**Chore short-circuit.** `chore` label — skip the candidate table, reuse cross-references, and observability declarations:
1. Append `## Plan` with 2-3 plain bullets: files to edit, exact string substitution, one verification step (usually `pytest -q`).
2. Append a one-line `## Acceptance Tests` — `pytest -q` if the chore touches Python; `none — chore` if non-code (rename, comment, README, dep pin).
3. Append a one-line `## Done Signals` (e.g. "`grep '^version' pyproject.toml` shows `0.6.6`").
4. Set state to `In Progress` and stop.
{% endif %}{% endfor %}
Turn Explore into a plan the next agent can execute by reading only `## Plan`. Do not write production code in this stage.

1. Read `docs/{{ issue.identifier }}/explore/` (including the required `reuse-inventory.md`), `## Domain Brief`, `## Plan Candidates`, `## Recommendation`, and any `## Triage` / `## Reproduction`.
2. Choose or refine the recommended approach. If Explore missed a blocking fact: set state to `Blocked`, append `## Plan Blocker` with the exact missing input. Do not guess.
3. Create `docs/{{ issue.identifier }}/plan/implementation-plan.md` when the plan needs more than the concise ticket section.
4. Append `## Plan` — precise enough that a fresh In Progress agent implements without re-reading Explore:
   - chosen approach and why it wins,
   - exact file/module ownership and expected write scope,
   - ordered implementation steps with dependencies and stop conditions,
   - data/API contracts, env vars, migrations, or UI states that must work,
   - first failing test, verification commands, and required evidence,
   - acceptance criteria, user-visible behavior, rollback/risk notes.
   Replace any vague bullet ("wire it up", "handle errors") with concrete files, commands, states, or payloads.
   The candidate set inside `## Plan` (or a refreshed `## Plan Candidates`) MUST be a Markdown table — not a bullet list — with exactly this header (extra columns allowed at the end); Plan Rationale and Learn depend on these two columns:

   ```
   | option | summary | reuse_from | observability |
   |--------|---------|------------|---------------|
   | A      | ...     | path:line  | add           |
   | B      | ...     | none       | none          |
   ```

   - `reuse_from`: a `path:line` from `reuse-inventory.md`, or `none`.
   - `observability`: `add` / `change` / `none` — whether this candidate adds, modifies, or skips logs/metrics/traces.
5. Append `## Acceptance Tests` — one bullet per AC, each a runnable test signature (`tests/test_foo.py::test_bar`, `pytest -k "expr"`, `npm test -- --grep "..."`). Empty list is invalid: set state back to `Explore`, append `## Plan Gaps` with what is missing, STOP.
6. Append `## Done Signals` — one bullet per observable signal QA can check (file path that must exist, stdout substring, exit code, HTTP status + body shape). Cap 8 lines; QA scores against this list row-for-row.
7. If you rejected any `reuse-inventory.md` row with `reuse_fit >= 0.7`: append `## Plan Rationale`, one line per rejected row (e.g. `path:line — reuse_fit 0.8 rejected: API shape mismatch`).
8. Set state to `In Progress`. In Progress must read this `## Plan` before editing code.
