### EXPLORE  -- when state is `Explore`

**Allowed tools (advisory).** Read filesystem, `git log` / `git show`, `docs/llm-wiki/`, ticket body. Write only under `docs/{{ issue.identifier }}/explore/` and as ticket comments. Run read-only commands only (`grep`, `find`, `git log`, `pytest --collect-only`). Do NOT edit source — Explore is research, not implementation.
{% for label in issue.labels %}{% if label == "chore" %}
**Chore short-circuit.** `chore` label = metadata-only change (version bump, rename, comment fix, dep pin, config nudge). Skip the full Explore contract:
1. Append a one-line `## Domain Brief` naming the change (e.g. "Chore: bump version 0.6.5 → 0.6.6 in pyproject.toml and src/symphony/__init__.py").
2. Append a one-line `## Plan Candidates` ("Single approach: edit the named files; no alternatives needed.").
3. Append a one-line `## Recommendation` pointing at the ticket's `## Acceptance criteria`.
4. Set state to `Plan` and stop. No `reuse-inventory.md`, no `vendor-docs.md`, no git-history sweep, no `docs/llm-wiki/`.

If the diff would touch source beyond the named metadata files, the ticket was mislabeled — drop the short-circuit, append a one-line `## Triage` flagging the mislabel, and run the full contract below.
{% endif %}{% endfor %}
Research through three lenses in one turn: **domain expert** (what does this code mean?), **implementer** (smallest sustainable change?), **risk reviewer** (what could go wrong?).

1. Read shared context first: `docs/{{ issue.identifier }}/` if it exists. On a re-explore (usually after a Blocked rewind), start from the prior brief and any `## Triage`.
2. Open `docs/llm-wiki/INDEX.md` (default `./docs/llm-wiki/`, respects `$LLM_WIKI_PATH`). Read every plausibly related entry and follow links into the entry files. Missing wiki → note it and continue; Learn seeds it later.
3. Skim git history per likely-touched file: `git log --oneline -- <path>`, then `git show <sha>` on the 1-2 most relevant commits. Capture *why* prior changes were made, not just what.
4. Read the touched source files end-to-end — the brief must reflect current state, not stale memory.
5. Drop boost material (citations, vendor-doc snippets, candidate helpers) into `docs/{{ issue.identifier }}/explore/` (e.g. `notes.md`, `vendor-docs.md`). **Required**: write `docs/{{ issue.identifier }}/explore/reuse-inventory.md` with this table (one row per candidate; `- none` line if nothing exists):
   `candidate | path:line | reuse_fit (0-1) | adapt_cost (low/med/high) | notes`
   Plan reads this file to justify any `reuse_from = none` choice.
6. Apply each lens explicitly and append three sections:
   - `## Domain Brief` — key facts, invariants, references (`path:line`, wiki entry titles, commit SHAs) the implementer must know. Include a `## Touched Files` bullet list (repo-relative paths, ≤12; group by directory if more) so in-flight tickets can detect overlap.
   - `## Plan Candidates` — 2-3 concrete approaches with trade-offs (complexity, blast radius, reversibility); files touched and tests added per option.
   - `## Recommendation` — the chosen option, why this lens won, risks accepted, and the first failing test the implementer should write.
7. Set state to `Plan`.
