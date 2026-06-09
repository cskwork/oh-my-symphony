### PLAN  -- when state is `Plan`

**Allowed tools (advisory).** Read filesystem, `docs/{{ issue.identifier }}/explore/`, ticket body. Write only under `docs/{{ issue.identifier }}/plan/` and as tracker comments. Read-only commands only. Do NOT edit source, install dependencies, or run state-mutating tests — Plan is design, not execution.
{% for label in issue.labels %}{% if label == "chore" %}
**Chore short-circuit.** `chore` label — skip the full Plan contract:
1. Post `## Plan` with 2-3 plain bullets: files to edit, exact string substitution, one verification step (usually `pytest -q`).
2. Transition state to `In Progress` and stop.
{% endif %}{% endfor %}
Turn Explore into a plan the next agent can execute by reading only `## Plan`. Do not write production code in this stage.

1. Read `docs/{{ issue.identifier }}/explore/`, the Domain Brief, Plan Candidates, Recommendation, and any Triage / Reproduction comments.
2. Choose or refine the recommended approach. If Explore missed a blocking fact: move the issue to `Blocked`, post `## Plan Blocker` with the exact missing input. Do not guess.
3. Create `docs/{{ issue.identifier }}/plan/implementation-plan.md` when the plan needs more than the concise comment.
4. Post `## Plan` — precise enough that a fresh In Progress agent implements without re-reading Explore:
   - chosen approach and why it wins,
   - exact file/module ownership and expected write scope,
   - ordered implementation steps with dependencies and stop conditions,
   - data/API contracts, env vars, migrations, or UI states that must work,
   - first failing test, verification commands, and required evidence,
   - acceptance criteria, user-visible behavior, rollback/risk notes.
   Replace any vague bullet ("wire it up", "handle errors") with concrete files, commands, states, or payloads.
5. Transition state to `In Progress`. In Progress must read this `## Plan` before editing code.
