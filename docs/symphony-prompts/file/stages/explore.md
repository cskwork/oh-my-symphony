### EXPLORE  -- when state is `Explore`
{% for label in issue.labels %}{% if label == "chore" %}
**Chore short-circuit.** This ticket carries the `chore` label — treat it as a metadata-only change (version bump, file rename, comment fix, dep pin, config nudge). Skip the full Explore contract:
1. Append a one-line `## Domain Brief` describing the change (e.g. "Chore: bump version 0.6.5 → 0.6.6 in pyproject.toml and src/symphony/__init__.py").
2. Append a one-line `## Plan Candidates` ("Single approach: edit the named files; no alternatives needed.").
3. Append a one-line `## Recommendation` pointing at the ticket's `## Acceptance criteria` as the source of truth.
4. Set state to `Plan` and stop. Do NOT write `reuse-inventory.md`, `vendor-docs.md`, or do a git-history sweep. Do NOT open `docs/llm-wiki/`.

If the diff would touch source code paths beyond the named metadata files, the ticket was mislabeled — drop the `chore` short-circuit, append a one-line `## Triage` flagging the mislabel, and run the full Explore contract below.
{% endif %}{% endfor %}
Research the ticket through three lenses in one turn: **domain expert** (what does this code mean?), **implementer** (smallest sustainable change?), **risk reviewer** (what could go wrong?).

1. **Read shared context first.** Open `docs/{{ issue.identifier }}/` if it exists (may be absent on first stage). On a re-explore (rare — usually after a Blocked rewind), the prior brief and any `## Triage` are your starting point.
2. Open `docs/llm-wiki/INDEX.md` (path defaults to ./docs/llm-wiki/ but respects $LLM_WIKI_PATH). Read every entry plausibly related to the ticket and follow links into the entry files. If `docs/llm-wiki/` does not exist yet, note that and continue — Learn will seed it later.
3. Skim git history in adjacent areas: for each file the ticket likely touches, run `git log --oneline -- <path>` and read the one or two most relevant commits in full (`git show <sha>`). Capture *why* prior changes were made, not just what.
4. Read the actual source files end-to-end (not just hunks) so the brief reflects current state, not stale memory.
5. Drop boost material — citations, vendor-doc snippets, candidate helpers — into `docs/{{ issue.identifier }}/explore/` (e.g. `notes.md`, `vendor-docs.md`). The brief sections below cite these files. **Required**: write `docs/{{ issue.identifier }}/explore/reuse-inventory.md` with this table (one row per candidate; `- none` line if nothing exists):
   `candidate | path:line | reuse_fit (0-1) | adapt_cost (low/med/high) | notes`
   Plan reads this file to justify any `reuse_from = none` choice.
6. Apply each lens explicitly and append three sections to the ticket:
   - `## Domain Brief` — key facts, invariants, and references (`path:line`, wiki entry titles, commit SHAs) the implementer must know before writing code. Include a `## Touched Files` bullet list (repo-relative paths, ≤12; group by directory if more) so other in-flight tickets can detect overlap.
   - `## Plan Candidates` — 2-3 concrete approaches with trade-offs (complexity, blast radius, reversibility). Name files touched and tests added per option.
   - `## Recommendation` — the option you choose, the rationale (why this lens won), the risks accepted, and the first failing test the implementer should write.
7. Set state to `Plan`.
