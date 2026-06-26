# Symphony Workflow Improvements — supergoal learnings (2026-06)

This doc is the canonical brief for a four-item methodology upgrade that ports
proven patterns from the `supergoal` skill (baseline-first delivery) into
Symphony's per-stage pipeline. Subagents read this file (not chat history) for
scope, contracts, and acceptance criteria.

Each item has: goal, contract (what artefact / API exists when done), files to
touch, and an "out of scope" line to keep blast radius small. Items follow the
`workflow-v0.5.2.md` house style.

The current pipeline order is:
`Todo -> Explore -> Plan -> In Progress -> Review -> QA -> Learn -> Merge Gate -> Done`.

---

## Why these four

Symphony already solves the *horizontal* problem well — who works, where, in
parallel — and supergoal solves the *vertical* problem — is one change actually
correct. They compose: inject supergoal's verification discipline into each
Symphony stage. A read of `supergoal/reference/role-loop.md` against the current
prompts and `src/symphony/orchestrator/contracts.py` shows symphony is already
strong on run isolation (`WORKFLOW.md:42-80` worktree + `git merge-tree` proof)
and real-execution QA (`qa.md` boots As-Is/To-Be, captures diffs). The gaps:

| supergoal mechanism | symphony today | evidence | item |
|---|---|---|---|
| Independent critic turns prose spec into **FAILING tests** before sign-off | builder writes its own tests | `in-progress.md:10`, `plan.md:5` | **S1** |
| Gate checks **content** (verdict, evidence files, numeric thresholds) | gate checks heading **presence** only | `contracts.py:19-24,128-148` | **S2** |
| Difficulty gate: *very easy* skips the heavy loop | standard pipeline always runs all stages; only `chore` short-circuits | `plan.md:4-10`, `review.md:4-10` | **S3** |
| Skill behavior pinned by `tests/*.test.sh` (18 contract tests) | core has 30+ pytest; skills have **none** | `tests/` (Python only), `skills/**` | **S4** |

Reference anchors in the skill: `~/.agents/skills/supergoal/reference/role-loop.md`
(the Build->Critic->Fixer->Verify contract), `templates/qa-gate.sh` and
`templates/contrast-gate.mjs` (content gates), `templates/surfaced-requirements.md`
(the durable hidden-requirement ledger), `templates/skill-frontmatter-gate.mjs`
and `tests/*.test.sh` (skill contract tests).

## Design principles (carried over from supergoal — keep, do not Goodhart)

These govern S1 and S2 and are non-negotiable acceptance criteria:

1. **Generated tests are a SIGNAL, not the oracle.** Final verification is
   always the project's REAL tests + the prose spec / `## Done Signals`. Never
   weaken or delete a real test; never declare done because critic-written
   tests pass while real tests fail. (`role-loop.md:78-82`)
2. **Critic derives every test from the prose spec, not a guessed rubric.**
   A wrong generated test the fixer optimizes to is the failure mode — keep
   them black-box and spec-anchored.
3. **Bounded loop.** Cap critic->fixer at 3 cycles; a 4th escalates to a human
   (`Blocked`) with the open reds. 2+ cycles that find issues but change no code
   = "doubt theater" → stop and recut. (`role-loop.md:85-87`)
4. **Content gates verify external facts, never re-implement the prompt
   contract in regex.** This respects `contracts.py:19-24`. A gate may assert
   "the cited evidence file exists" or "no scorecard row says fail"; it may NOT
   re-encode "the section must contain a plan" — that lives in the prompt.

---

## S1. Independent Critic stage (failing-test-first)

**Depends on:** S3 (difficulty gate) to skip Critic on trivial tickets.

**Goal**: surface REQUIRED behaviors the brief and the builder's own tests miss,
as failing tests written by an agent that did not write the code — supergoal's
single highest-leverage move. Today nothing fills this role: the builder writes
the brief's tests (`in-progress.md:10`) and Review is read-only and writes no
tests (`review.md:11`).

**Placement (recommended — option A in Open Decisions):** a new `Critic` state
between `In Progress` and `Review`:
`... In Progress -> Critic -> Review -> ...`. A fresh-context agent (the
existing per-stage subagent model already gives independence) reads the built
diff + prose spec + `## Plan` + `## Acceptance Tests`.

**Contract**:
- New prompt `docs/symphony-prompts/file/stages/critic.md` (+ `linear/`). The
  Critic agent MUST NOT edit source or weaken/delete existing tests. It:
  1. Re-reads `## Plan`, `## Acceptance Tests`, `## Done Signals`, and repo/data
     rules. Enumerates REQUIRED behaviors the existing tests do not exercise —
     edges: boundary inputs, error/recovery paths, scoping/precedence, prefix/
     filter behavior, incremental update, concurrency, protocol/state.
  2. Writes one NEW FAILING test per gap, in a separate test file, derived
     strictly from the spec (prefer black-box / property tests: roundtrip,
     idempotency, invariants). Leaves them red.
  3. Appends `## Surfaced Requirements` to the ticket AND writes the durable
     ledger `docs/{{ issue.identifier }}/critic/surfaced-requirements.md`
     (format: `templates/surfaced-requirements.md` in the supergoal skill — a
     dated heading, one bullet per requirement: what the spec implies, why it
     is required though the prompt never stated it, the failing test that now
     covers it, status `open`).
  4. Lists the new failing test signatures under `## Critic Tests`.
  5. If gaps were found → set state to `In Progress` (rewind), so the existing
     `SYMPHONY_REWIND_SCOPE` machinery (`in-progress.md:8`) dispatches a fixer
     turn scoped to clearing the reds. If NO gaps → append `## Critic` ("no
     surfaced requirements") and set state to `Review`.
- Fixer side (reuse `In Progress`, no new stage): on a Critic rewind, the
  builder makes the failing tests pass with the smallest change, adds no code
  not required by a red test, breaks no passing test, and updates the ledger
  (`fixed` / why-still-`open`). The 3-cycle cap (principle 3) is enforced by
  S2's gate counting `## Critic` rewinds.
- `contracts.py`: add `Critic` to the enforcement set —
  `_CRITIC_REQUIRED = ("## Surfaced Requirements", "## Critic Tests")` when it
  rewinds, OR a clean `## Critic`. Mirror the `_REVIEW_OUTCOMES` either/or shape
  (`contracts.py:88-97`).
- Orchestrator: register `Critic` in the state machine between In Progress and
  Review; ensure the rewind Critic->In Progress is a recognized transition
  (`_is_rewind_transition`).

**Files**:
- `WORKFLOW.md`, `WORKFLOW.example.md`, `WORKFLOW.file.example.md`, `WORKFLOW.jira.example.md`
  (`active_states`: insert `Critic` between `In Progress` and `Review`; add its
  `state_descriptions` row)
- `docs/symphony-prompts/file/stages/critic.md` (new), `linear/stages/critic.md` (new)
- `docs/symphony-prompts/file/stages/in-progress.md` (set state to `Critic`, not
  `Review`; fixer note on Critic rewind)
- `src/symphony/orchestrator/constants.py` (`_REWIND_TRANSITIONS` += `("critic", "in progress")`)
- `src/symphony/orchestrator/contracts.py` (`_CRITIC_REQUIRED`, `state == "critic"` branch)
- `docs/symphony-prompts/file/base.md` (length-cap table: the two new sections)
- `tests/test_orchestrator_contracts.py`, `tests/test_workflow_pipeline_prompt.py` (new cases)
- Verify `prompt.py` maps state `Critic` -> `critic.md` automatically (same
  lowercase+hyphen rule that maps `In Progress` -> `in-progress.md`); add an
  explicit mapping only if it does not.

**Out of scope**: making the critic's generated tests the acceptance oracle
(principle 1 forbids it); auto-running tests inside the Critic turn beyond
confirming red; a standalone Fixer state (reuse In Progress).

---

## S2. Content-checking gates (extend `evaluate_contract`)

**Depends on:** none (independent of S1, but also gates S1's outputs).

**Goal**: close the "weak model emits the heading but the content is hollow or
self-contradictory" hole. Today `_section_present_nonempty` (`contracts.py:128`)
accepts any non-whitespace body, and `_directory_has_files` (`:151`) only runs
for Done. A QA turn can write `## AC Scorecard` with a `fail` row and still
transition; a Review can cite `evidence: path:line` for a file that does not
exist. supergoal's `qa-gate.sh` closes exactly this by asserting facts about
artefacts, not prose.

**Contract** (each is an external-fact check, per principle 4 — NOT a re-encoding
of the prompt):
- **QA scorecard consistency.** Parse the `## AC Scorecard` table
  (`signal | source | result (pass/fail) | evidence path`, per
  `workflow-v0.5.2.md` A1). If any `result` cell is `fail`/`error`/empty →
  contract fails (rewind to In Progress, not a silent pass). Add
  `_scorecard_all_pass(body) -> (bool, list[str])`.
- **Evidence-path realness.** For every `evidence path` cell in the AC Scorecard
  and every `path:line` in the Review `## Security Audit` (`review.md:17-20`),
  the referenced file must exist under `docs_root` (strip `:line`). Missing →
  list it in `missing`. Reuse the `docs_root` plumbing already passed to
  `evaluate_contract` (`contracts.py:72,109`). Add `_cited_paths_exist(...)`.
- **Security verdict enforcement.** If `## Security Audit` contains a `fail`
  verdict row, the producing Review turn must have rewound (state == In
  Progress) — a `## Review` clean pass with a `fail` audit row is a contract
  violation. This makes `review.md:20`'s "auto-promotes to CRITICAL" mechanical
  instead of model-dependent.
- Keep the existing presence checks unchanged; these are additive. Gate output
  still flows through the existing `## Contract Failure` rewind note
  (`_build_result`, `:161`).

**Files**:
- `src/symphony/orchestrator/contracts.py` (new helpers + wire into `qa`/`review` branches)
- `src/symphony/orchestrator/core.py` (additive `elif contract.warnings:` branch that
  surfaces soft warnings as a `## Contract Warning` note WITHOUT rewinding — the only
  path that lets otherwise-inert `ContractResult.warnings` reach the ticket; does not
  touch the existing rewind / state-transition logic)
- `tests/test_orchestrator_contracts.py` (fail-row rewind; missing-evidence-file rewind; clean pass; soft scorecard warn)
- `tests/test_orchestrator_contract_integration.py` (end-to-end soft-warning note path)

**Out of scope**: parsing free-form prose for semantic correctness; running the
tests named in the scorecard (QA already executes — the gate only checks QA's
recorded verdict is internally consistent and its evidence is real); any regex
that re-states what a section "should say".

---

## S3. Difficulty gate (generalize the `chore` short-circuit)

**Depends on:** none. Enables S1 to be affordable.

**Goal**: stop spending the full `Explore -> Plan -> In Progress -> Critic ->
Review -> QA` machine on trivial tickets. supergoal routes *very easy* work to a
direct edit and reserves the heavy critic->fixer loop for behavior the visible
tests miss (`role-loop.md:7,68-70`). Symphony already proves the pattern with the
`chore` label short-circuit (`plan.md:4-10`, `review.md:4-10`) and
`auto_triage_actionable_todo` (`WORKFLOW.md:252`); this generalizes it to a
declared difficulty.

**Contract** (prompt-level branch, matching the existing `chore` short-circuit —
no orchestrator state-jump, honoring "agent owns state writes"):
- Plan declares difficulty: append `## Difficulty` with exactly one of
  `trivial` / `standard` / `complex` plus a one-line rationale, after
  `## Done Signals`. Cap 2 lines. No `## Difficulty` = `standard`
  (backward-compatible: existing boards keep the full loop).
- `in-progress.md` final step branches: `## Difficulty: trivial` AND not a
  `bug` ticket -> set state to `Review` (skip Critic); else -> set state to
  `Critic`. Append a one-line `## Pipeline Route` recording the elision so it is
  never silent (supergoal: "no silent caps").
- `review.md` final step: `## Difficulty: trivial` AND no runtime behavior
  changed (the same condition that already gates the live-HTTP probe,
  `review.md:16`) -> set state to `Learn` (skip QA); else -> `QA` as today.
- Safety rails (hard, override difficulty): a `bug` ticket may never skip QA
  (repro closure stays mandatory, `qa.md:27-35`); any `fail` Security Audit row
  (S2) forces the full route.

**Files**:
- `docs/symphony-prompts/file/stages/plan.md` + `linear/stages/plan.md` (`## Difficulty` output)
- `docs/symphony-prompts/file/stages/in-progress.md` + `review.md` (+ `linear/` mirrors) (difficulty branch)
- `docs/symphony-prompts/file/base.md` (length-cap entry for `## Difficulty`, `## Pipeline Route`)
- `tests/test_workflow_pipeline_prompt.py` (the branch text renders per difficulty)

**Out of scope**: an orchestrator-level state-jump router (rejected — violates
"agent owns state writes"); auto-classifying difficulty from diff size (agent
declares it); removing the `chore` short-circuit (it stays the zero-config fast
path).

---

## S4. Skill contract tests

**Depends on:** none.

**Goal**: pin the behavior of the bundled skills the way `supergoal/tests/`
pins its own (18 `*.test.sh`: role-loop, qa-only, spec, skill-frontmatter, …).
Today symphony's 30+ pytest suite covers the orchestrator core but nothing
verifies that `symphony-oneshot`, `symphony-monorepo`, or `using-symphony`
bootstrap or render correctly — a broken `WORKFLOW.oneshot.md` template or a
malformed SKILL frontmatter ships undetected.

**Contract**:
- New suite `tests/skills/` (pytest, so it runs in the existing CI `tests.yml`
  job — no second runner). One module per skill plus a shared frontmatter test:
  - `test_skill_frontmatter.py`: every `skills/*/SKILL.md` has valid YAML
    frontmatter with non-empty `name` + `description`, and `name` matches the
    directory. Port the assertions from `supergoal/templates/skill-frontmatter-gate.mjs`.
  - `test_skill_reference_links.py`: every relative link in each `SKILL.md`
    (e.g. `reference/operations.md`, `templates/bootstrap.sh`) resolves to an
    existing file in the skill bundle. Catches the most common rot.
  - `test_symphony_oneshot_bootstrap.py`: run `skills/symphony-oneshot/templates/bootstrap.sh`
    in a tmp dir (hermetic, no network/agent CLI) and assert it produces the
    expected vault skeleton (`.oneshot/vault/` with the files the Deliver gate
    in `reference/lanes.md:39-51` greps for). If the script needs a CLI, gate
    the test on availability and assert the dry-run path instead.
  - `test_symphony_monorepo_setup.py`: `scripts/setup-monorepo.sh --dry-run` (or
    equivalent) emits a parseable WORKFLOW with the worktree `after_create` hook
    the skill promises.
- These are CONTRACT tests (does the skill produce what it claims), not unit
  tests of the orchestrator — keep them in `tests/skills/` so the boundary is
  obvious.

**Files**:
- `tests/skills/__init__.py`, `test_skill_frontmatter.py`, `test_skill_reference_links.py`,
  `test_symphony_oneshot_bootstrap.py`, `test_symphony_monorepo_setup.py` (all new)
- `skills/symphony-oneshot/templates/bootstrap.sh`, `skills/symphony-monorepo/scripts/setup-monorepo.sh`
  (add a `--dry-run` / `--check` flag ONLY if absent — confirm before editing)

**Out of scope**: testing agent output quality (that is the pipeline's job);
porting supergoal's bash `.test.sh` harness (reuse pytest); networked or
real-CLI integration tests.

---

## Cross-cutting

### Sequencing & ownership (subagent dispatch map)

| Order | Item | Risk | Rationale |
|-------|------|------|-----------|
| 1 | S4 skill contract tests | low | fully independent; lands value immediately, no pipeline change |
| 2 | S2 content gates | low-med | additive to `contracts.py`; also pre-builds the gate S1 leans on |
| 3 | S1 critic stage | med | config (`active_states`) + `critic.md` + `contracts.py` critic branch + In Progress "set state Critic"; the data-driven state machine keeps it contained |
| 4 | S3 difficulty branch | low-med | prompt-only; depends on S1 (the `else -> Critic` branch needs Critic to exist) |

S3 depends on S1 (its In Progress branch falls through to `Critic`), so land S1
first, then add the `## Difficulty` trivial branch on top. S2 and S1 both edit
`contracts.py` (different branches — `qa`/`review` vs new `critic`); no line
overlap. No item rewrites `core.py` state-transition logic (S2 adds only an
additive warning-note branch beside the existing rewind block); the data-driven
state machine absorbs the new Critic stage via `active_states` + prompts.

### Testing

- All existing tests must still pass: `pytest -q`.
- New tests are part of each item's contract above. Difficulty routing (S3) and
  the critic state (S1) need `test_orchestrator_phase_transition.py` cases.
- Prompt-only edits update `tests/test_workflow_pipeline_prompt.py` if it
  asserts specific section names.

### Docs language & publicity

- New prompt sections follow the existing bilingual `{% if language == 'ko' %}`
  pattern (base.md, learn.md).
- Repo is public: every `WORKFLOW.example.md` addition must work from a fresh
  clone; `difficulty_routing` ships default-off so existing workflows are
  unchanged.

### Release

- 1 new pipeline stage + 1 contract-enforcement expansion + 1 routing feature +
  a test suite → minor bump. `pyproject.toml` + `src/symphony/__init__.py` in
  lockstep, own `chore(release)` commit. `CHANGELOG.md` block in PM-readable
  plain language.

---

## Resolved decisions (2026-06)

Confirmed with the maintainer; the contracts above already reflect them.

1. **Critic placement = option A** — a dedicated `Critic` state between
   `In Progress` and `Review`. Cheap to add because Symphony's state machine is
   data-driven: states are declared in `WORKFLOW.md` `active_states` and the
   agent writes `state:` itself (the orchestrator only reads, then enforces the
   contract on the producing stage at `core.py:1615`). Adding `Critic` is a
   config + prompt addition, not a state-machine rewrite. The per-stage
   fresh-context subagent already gives the independence supergoal requires.
   Rejected option B (fold into Review) because it would break Review's
   read-only contract (`review.md:3,11`) and mix "find issues" with "author
   tests".

2. **Content-gate severity = split.** Evidence-realness (a cited file exists) is
   a HARD rewind immediately — unambiguous, no format risk. Scorecard verdict
   parsing ships as a soft `[contract-warn]` marker for one release (table
   format variance on weak models), then promotes to HARD. Security `fail`-row
   enforcement is HARD immediately (the prompt already declares it CRITICAL,
   `review.md:20`).

3. **Critic cost = ON for `standard`/`complex`, OFF for `trivial`/`chore`,**
   driven by the S3 difficulty branch in the prompts (not an orchestrator
   route). A ticket with no `## Difficulty` is treated as `standard`, so
   existing boards keep the full loop unless a ticket opts into `trivial`.
