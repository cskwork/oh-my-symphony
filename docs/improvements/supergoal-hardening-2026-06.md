# Symphony Workflow Hardening — supergoal round 2 (2026-06)

This doc is the canonical brief for a five-item hardening pass that closes the
gaps an audit of the just-landed S1-S4 work (`dd54c03`, see
`supergoal-learnings-2026-06.md`) surfaced. Subagents read this file (not chat
history) for scope, contracts, and acceptance criteria.

S1-S4 ported supergoal's verification *shapes* (a Critic stage, content gates, a
difficulty gate, skill tests). This round makes the shapes **honest and
enforced**: where a prompt promises a mechanism, the system must actually run it;
where a step is "mandatory", a gate must fail when it is skipped.

Current pipeline order:
`Todo -> Explore -> Plan -> In Progress -> Critic -> Review -> QA -> Learn -> Merge Gate -> Done`.

Each item has: goal, evidence (the audit finding), contract (the artefact/API
that exists when done), files, out-of-scope (blast-radius fence), and acceptance.

## Design principles (carried from supergoal — keep, do not Goodhart)

1. **Real execution is the oracle.** A stage that claims to run a tool must run
   it or fail loudly — never record a pass it did not execute (`role-loop.md:78-82`).
2. **Gates verify external facts, never re-encode the prompt** (S2 principle 4):
   a gate may assert "the cited file exists" or "the mandatory artefact is on
   disk"; it may NOT regex the prose for semantic correctness.
3. **Match the existing convention, not an idealized one.** New gates mirror the
   Done-branch path resolution (`docs_root / identifier / <subdir>`) exactly so
   they are consistent with the shipping `_directory_has_files` check; any latent
   path-prefix concern in that convention is shared and out of scope here.
4. **Agent owns state writes.** Difficulty (S3) and the Critic cap stay
   prompt-level branches the agent executes, not orchestrator state-jumps — the
   data-driven state machine is the reason S1 was cheap to add.

---

## H1. Honest bounded Critic loop (fix the false "S2 counts" claim)

**Evidence.** `critic.md:12` and `supergoal-learnings-2026-06.md:51-53,100-102`
tell the agent the 3-cycle Critic->In Progress->Critic cap is "enforced by S2's
gate counting `## Critic` rewinds." This is **false**: `contracts.py` counts
nothing; the only cap is the shared `cfg.agent.max_attempts` (default 3) rewind
budget in `core.py:1689-1719`, which sums *all* rewinds (Critic + Review + QA +
contract failures) into one `debug.rewind_count`. So a ticket that already
rewound at Review and QA can be force-Blocked on its first Critic bounce, and the
"2+ cycles that find issues but change no code = doubt theater -> recut"
discipline (principle 3 of the prior doc) is absent.

**Decision — prompt-level, not code.** Make the claim true the way S3 did: the
agent reads an external fact (count of prior `## Surfaced Requirements` dated
cycles in the ticket body) and self-routes. A code-enforced per-stage counter was
considered and rejected: it would have to thread state through the stateless
`evaluate_contract` or special-case the shared rewind budget in `core.py` — a
blast radius this honest-wording fix does not need. The shared `max_attempts`
stays the hard backstop.

**Contract.**
- `critic.md` (file + linear): replace the false "S2 counts `## Critic` rewinds"
  sentence. The Critic now: counts prior `## Surfaced Requirements` cycles
  already in the ticket body; on the **3rd** cycle that would still surface gaps,
  set state to `Blocked` and append `## Critic Cap` listing the open reds (escalate
  to a human) instead of rewinding a 4th time. Add the doubt-theater rule: if a
  prior cycle surfaced gaps but the fixer changed no code (the reds are still
  red), do not re-surface the same gaps — set `Blocked` with `## Critic Cap`.
  State plainly that the shared rewind budget is the orchestrator's hard backstop.
- `supergoal-learnings-2026-06.md`: correct the two passages (`:51-53`,
  `:100-102`) to describe the actual mechanism (agent-counted cap + shared
  `max_attempts` backstop), so the canonical brief no longer misstates it.

**Files.** `docs/symphony-prompts/file/stages/critic.md`,
`docs/symphony-prompts/linear/stages/critic.md`,
`docs/improvements/supergoal-learnings-2026-06.md`.
If `docs/symphony-prompts/file/base.md` length-cap table lacks `## Critic Cap`,
add a `<= 6 lines` row (mirror in the linear table block).

**Out of scope.** A code-level per-stage rewind counter; changing
`max_attempts` semantics or its default; touching `core.py`.

**Acceptance.** `critic.md` contains no "S2 counts" wording; `## Critic Cap` +
the Blocked-on-3rd + doubt-theater rules are present in both trees; the spec doc
matches. `tests/test_workflow_pipeline_prompt.py` still passes (update the
asserted Critic section list if it enumerates section names).

---

## H2. Enforce bug repro closure in the QA gate

**Evidence.** `qa.md:27` calls bug repro closure "mandatory ... Never skip" —
re-run the reproduction against To-Be, save `qa/repro-after.log`, it must pass.
But the QA contract (`contracts.py:174-178`, `_QA_REQUIRED =
("## QA Evidence", "## AC Scorecard")`) only checks section presence + cited-path
realness. A QA turn on a `bug` ticket can omit the repro re-run entirely and
still transition QA -> Learn. supergoal's discipline: the gate asserts the
mandatory artefact exists, not that the prose mentions it.

**Contract (external-fact check, principle 2).** In the `qa` branch of
`evaluate_contract`, add: if the reproduction directory exists and is non-empty
(`docs_root / identifier / "reproduce"`, the dir Todo creates for `bug` tickets,
`todo.md:11`), then `docs_root / identifier / "qa" / "repro-after.log"` MUST
exist; missing -> a HARD contract failure (rewind QA -> In Progress) listing the
absent log. Mirror the Done-branch resolution exactly (principle 3). No
reproduce dir (non-bug ticket) -> no-op. `docs_root is None` -> no-op (mirrors
the existing path checks). Add helper
`_bug_repro_closed(docs_root, identifier) -> list[str]` and call it from the qa
branch alongside `_cited_paths_exist`.

**Files.** `src/symphony/orchestrator/contracts.py`,
`tests/test_orchestrator_contracts.py`.

**Out of scope.** Parsing the log contents for pass/fail (QA already executes;
the gate only asserts the mandatory artefact is on disk); reading ticket labels
in the contract (the reproduce-dir existence *is* the bug signal — labels are not
plumbed into `evaluate_contract` and threading them in is a larger change).

**Acceptance.** New tests: (a) reproduce dir present + no `repro-after.log` ->
`passed is False`, missing names the log; (b) reproduce dir present + log present
-> `passed is True`; (c) no reproduce dir -> unaffected (still passes on
evidence+scorecard). Full suite green.

---

## H3. Language-agnostic reproduction spec (de-hardcode `.spec.ts`)

**Evidence.** `todo.md:11` authors the bug reproduction as a Playwright/Cypress
`reproduce/repro.spec.ts`, and `qa.md:27,44` re-runs that exact `.ts` path.
Symphony orchestrates agents on *any* project; a Python/Go/Java/Rust backend bug
cannot be reproduced by a TypeScript Playwright spec. The hardcoded `.ts` forces
web-E2E framing on every bug ticket and makes the "valid and available" question
fail for non-web repos.

**Contract (prompt-only).**
- `todo.md` (file + linear) step for `bug` tickets: author the reproduction *in
  the project's own test framework* — Playwright/Cypress for a web UI, but
  `pytest`/`go test`/`cargo test`/JUnit/etc. for a backend — saved as
  `docs/{{ issue.identifier }}/reproduce/repro.<ext>` (the `<ext>` the project
  uses). Keep the existing "capture symptom as-is, run it, save trace/output,
  append `## Reproduction`" flow.
- `qa.md` (file + linear) step 7 and the non-API fallback note: re-run *the
  reproduction authored at Todo under `docs/{{ issue.identifier }}/reproduce/`*
  (whatever its extension) against To-Be, save `qa/repro-after.log`. Drop the
  hardcoded `.spec.ts`.

**Files.** `docs/symphony-prompts/file/stages/todo.md`,
`docs/symphony-prompts/linear/stages/todo.md`,
`docs/symphony-prompts/file/stages/qa.md`,
`docs/symphony-prompts/linear/stages/qa.md`.

**Out of scope.** Auto-detecting the project language in code; changing the
`reproduce/` directory name (H2 keys its gate on it).

**Acceptance.** No `repro.spec.ts` literal remains in `todo.md`/`qa.md` (both
trees); the reproduction instruction is framework-neutral; H2's gate still keys
on the `reproduce/` dir, not an extension.

---

## H4. Tool-availability honesty guard in QA

**Evidence.** `qa.md:3` lists `pytest` / `playwright` / boot recipes as tools the
QA turn runs, but never tells the agent to confirm the tool exists. `playwright`
is not installed in this repo (verified), so on a web ticket a weak model could
emit a green scorecard without ever executing the spec — the exact "fake success"
failure supergoal principle 1 forbids.

**Contract (prompt-only).** Add one rule near `qa.md` step 1 (file + linear):
before running any external runner (playwright, a boot command, a non-Python test
runner), confirm it is available (e.g. `npx playwright --version`,
`command -v <tool>`). If a required runner is missing: install it via the
project's standard manager if that is in scope, else set state to `In Progress`
(or `Blocked` if unfixable) and append `## QA Failure` naming the missing tool —
never record a scorecard pass for a check you could not execute.

**Files.** `docs/symphony-prompts/file/stages/qa.md`,
`docs/symphony-prompts/linear/stages/qa.md`. Add a `## QA Failure` length-cap row
to `base.md` only if absent.

**Out of scope.** Symphony-side preflight that probes target-project tools (the
agent owns its environment); a tool-manifest config.

**Acceptance.** Both `qa.md` trees contain the availability-or-fail rule;
existing QA steps unchanged otherwise; prompt-render test green.

---

## H5. Gate the Critic surfaced-requirements ledger

**Evidence.** `critic.md:10` requires the durable ledger
`docs/{{ issue.identifier }}/critic/surfaced-requirements.md` on every rewind,
but the Critic contract (`contracts.py:141-148`) only checks the `##
Surfaced Requirements` + `## Critic Tests` section pair. A Critic turn can append
the sections and never write the ledger file — the durable record supergoal's
`surfaced-requirements.md` template exists to provide. Mirror the
evidence-realness check S2 added for QA/Review.

**Contract (external-fact check, principle 2).** In the `critic` branch, when the
turn took the rewind path (`## Surfaced Requirements` present, i.e. not the clean
`## Critic`), require `docs_root / identifier / "critic" /
"surfaced-requirements.md"` to exist; missing -> add to `missing` (HARD, same
rewind-to-Critic path as a missing section). `docs_root is None` -> no-op.

**Files.** `src/symphony/orchestrator/contracts.py`,
`tests/test_orchestrator_contracts.py`.

**Out of scope.** Parsing the ledger's bullet format / status words (presence +
non-empty is the fact; the prompt owns the format).

**Acceptance.** New tests: rewind pair present + no ledger file -> fail; + ledger
present -> pass; clean `## Critic` -> unaffected. Full suite green.

---

## Cross-cutting

### Sequencing & ownership (subagent dispatch map)

| Order | Item | File set (disjoint -> safe in parallel) | Risk |
|-------|------|------------------------------------------|------|
| P1a | H2 + H5 | `contracts.py`, `tests/test_orchestrator_contracts.py` | low-med (additive branches) |
| P1b | H1 | `file|linear/stages/critic.md`, `supergoal-learnings-2026-06.md`, `base.md` cap row | low (prompt + doc) |
| P1c | H3 + H4 | `file|linear/stages/{qa,todo}.md`, `base.md` cap row | low (prompt) |
| P2 | Scenario proof | `tests/test_orchestrator_contract_integration.py` (+ `test_workflow_pipeline_prompt.py` fixups) | med |

H2 and H5 share `contracts.py` -> one agent owns both. H3 and H4 share `qa.md` ->
one agent owns both. The three P1 agents touch disjoint files and run in
parallel; P2 runs after and verifies the merged result. `base.md` cap rows: only
H1's agent edits `base.md` (it is the one adding `## Critic Cap`); H4 appends its
`## QA Failure` row only if absent — to avoid a write race, H1's agent adds both
cap rows and H4's agent leaves `base.md` alone (noted in each brief).

### Testing

- All existing tests must still pass: `pytest -q` (baseline 851 passed, 1
  skipped). New unit tests are part of H2 and H5. Prompt-render assertions
  (`test_workflow_pipeline_prompt.py`) update only if they enumerate section
  names H1/H3/H4 reworded.
- **Scenario proof (P2):** one focused integration test that drives a `bug`
  ticket through the full loop and asserts each gate fires:
  Critic surfaces a gap -> rewind to In Progress (`_REWIND_TRANSITIONS`) ->
  fixer turn -> Critic clean -> Review -> QA. Assert: (1) Critic rewind without a
  ledger file fails the gate (H5); (2) QA on the bug ticket without
  `repro-after.log` fails the gate (H2); (3) with both artefacts the loop reaches
  Learn. This is the project's existing proof idiom
  (`test_orchestrator_contract_integration.py`), not a live-CLI run.

### Docs language & release

- Bilingual prompt edits keep the `{% if language == 'ko' %}` pattern where the
  surrounding section uses it.
- Repo is public: every change works from a fresh clone; no new default-on
  behavior (H2/H5 gates are no-ops when the keyed artefact dir is absent, so
  existing non-bug / clean-Critic boards are unaffected).
- 2 gate expansions + 3 prompt-honesty fixes + tests -> patch/minor bump per the
  maintainer's call; `CHANGELOG.md` block in PM-readable plain language.
