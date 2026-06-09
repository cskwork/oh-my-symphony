### EXPLORE  -- when state is `Explore`

**Allowed tools (advisory).** Read filesystem, `git log` / `git show`, `docs/llm-wiki/`, ticket body. Write only under `docs/{{ issue.identifier }}/explore/` and as tracker comments. Run read-only commands only (`grep`, `find`, `git log`, `pytest --collect-only`). Do NOT edit source — Explore is research, not implementation.
{% for label in issue.labels %}{% if label == "chore" %}
**Chore short-circuit.** `chore` label = metadata-only change (version bump, rename, comment fix, dep pin, config nudge). Skip the full Explore contract:
1. Post a one-line `## Domain Brief` comment naming the change.
2. Add a one-line `## Plan Candidates` ("Single approach: edit the named files; no alternatives needed.").
3. Add a one-line `## Recommendation` pointing at the ticket's acceptance criteria.
4. Transition state to `Plan` and stop. No reuse inventory, no vendor-doc sweep, no git-history sweep, no `docs/llm-wiki/`.

If the diff would touch source beyond the named metadata files, the ticket was mislabeled — drop the short-circuit, post a one-line Triage comment flagging the mislabel, and run the full contract below.
{% endif %}{% endfor %}
Research through three lenses in one turn: **domain expert** (what does this code mean?), **implementer** (smallest sustainable change?), **risk reviewer** (what could go wrong?).

1. Read shared context first: `docs/{{ issue.identifier }}/` if it exists. On a re-explore (usually after a Blocked rewind), start from the prior brief and any Triage comment.
2. Open `docs/llm-wiki/INDEX.md` (default `./docs/llm-wiki/`, respects `$LLM_WIKI_PATH`). Read every plausibly related entry and follow links into the entry files. Missing wiki → note it and continue; Learn seeds it later.
3. Skim git history per likely-touched file: `git log --oneline -- <path>`, then `git show <sha>` on the 1-2 most relevant commits. Capture *why* prior changes were made, not just what.
4. Read the touched source files end-to-end — the brief must reflect current state, not stale memory.
5. Drop boost material (citations, vendor-doc snippets, candidate helpers, reuse inventory) into `docs/{{ issue.identifier }}/explore/` (e.g. `notes.md`, `vendor-docs.md`, `reuse-inventory.md`). The brief sections below cite these files.
6. Apply each lens explicitly and post one consolidated Explore comment with three sections:
   - `## Domain Brief` — key facts, invariants, references (`path:line`, wiki entry titles, commit SHAs) the implementer must know. Include a `## Touched Files` bullet list (repo-relative paths, ≤12; group by directory if more) so in-flight tickets can detect overlap.
   - `## Plan Candidates` — 2-3 concrete approaches with trade-offs (complexity, blast radius, reversibility); files touched and tests added per option.
   - `## Recommendation` — the chosen option, why this lens won, risks accepted, and the first failing test the implementer should write.
7. Transition state to `Plan`.
