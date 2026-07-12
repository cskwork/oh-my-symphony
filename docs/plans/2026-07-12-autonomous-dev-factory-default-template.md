# Plan: beginner autonomous development factory

Date: 2026-07-12

Status: plan only; no runtime or template behavior changed

Target: simplify the file-board default while preserving the current workflow
as an advanced profile

## Decision

Make Symphony the horizontal control plane and Supergoal the vertical delivery
contract:

- Supergoal owns Wayfinder planning and the complete per-ticket build loop.
- Symphony owns the queue, dependency order, isolated worktrees, retries, and
  an independent final verification turn.
- SuperPM supplies customer and market evidence when product/spec decisions
  need it.
- Superdesign owns UI/design execution and its visual gates.
- SuperQA supplies real-browser regression evidence and improvement findings.

Do not copy the Supergoal phase instructions into Symphony prompts. The current
default does this in two places, which makes the starter hard to understand and
lets the contracts drift.

## Problem and expected outcome

Today a beginner must understand a 322-line file example, 285 lines of file
prompts, four active stages, wiki maintenance, final-history rules, multiple
backend blocks, and detailed evidence sections before the first useful task.
The process is capable, but the starter exposes production machinery before the
user has a working loop.

The expected beginner activity is simpler:

1. Describe a product outcome.
2. Let Supergoal Wayfinder produce a destination map and vertical tickets.
3. Sync executable frontier tickets into Symphony.
4. Let each ticket run the Supergoal delivery loop in its own worktree.
5. Independently verify it; rewind defects or finish the ticket.
6. Use verified customer and QA findings to choose the next small slice.

Success is an autonomous, resumable software-delivery loop. It can optimize for
validated demand and measurable value, but it cannot guarantee that a product
will be lucrative; revenue remains a market outcome.

## Public mental model

```text
Product idea
    |
    v
Supergoal WAYFINDER
    |-- SuperPM: customer, demand, positioning, product-spec evidence
    |-- wayfinder/map.md
    `-- wayfinder/tickets/*.md
              |
              v
      idempotent factory sync
              |
              v
     Ready -(machine)-> Build -> Verify -> Done
                ^         |
                |         `-- acceptance failure -> Build
                |
                `-- Supergoal delivery loop
                    `-- Superdesign for UI/design tickets

     missing authority, credentials, or irrecoverable proof -> Blocked
     browser surface in Verify -> SuperQA regression + improvement findings
```

`Ready`, `Build`, and `Verify` are the only active beginner states. `Done` and
`Blocked` are the only terminal states shown by default.
`Ready` remains visible while dependencies are open; Symphony promotes it
directly after the validated dependency graph clears, without an agent turn.

## Ownership boundaries

| Concern | Owner | Boundary |
| --- | --- | --- |
| Destination, scope, frontier graph | Supergoal WAYFINDER | No product code |
| Customer/market assumptions | SuperPM | Evidence or explicit assumption; no invented demand |
| Ticket implementation | Supergoal GREENFIELD/DEBUG/LEGACY | Full role loop and exact proof |
| UI/design quality | Superdesign | Only UI/design tickets; its gates feed Supergoal QA |
| Queue, dependencies, worktrees, retries | Symphony | No duplicate product methodology |
| Independent delivery decision | Symphony Verify | Re-run declared proof; do not trust Build's summary |
| Browser regression and UX findings | SuperQA | Real run directory/report; no pass without evidence |

The Symphony ticket worktree satisfies Supergoal's isolation requirement. When
`SYMPHONY_ISSUE_ID` is present, the worker must treat the current attached
worktree as the Supergoal run worktree instead of creating a nested worktree.
The ticket branch/base/target refs remain the source of truth.

## Beginner template contract

Add a separate bundle first; promote it to the file-board default only after
the focused end-to-end test is green.

Proposed bundle:

```text
templates/autonomous-dev/
  WORKFLOW.md
  prompts/
    base.md
    ready.md
    build.md
    verify.md
  scripts/
    setup-worktree.sh
```

Targets, treated as usability budgets rather than correctness proxies:

- `WORKFLOW.md`: at most 100 lines, including comments.
- All four prompt files: at most 120 lines combined.
- One backend block selected during initialization; no all-provider catalog.
- No wiki sweep, Learn lane, Human Review, Archive, Slack, QA boot comparison,
  performance budget, or inline WIP-marker implementation in the starter.
- Advanced examples keep those capabilities; no runtime feature is removed.

The visible workflow keeps only tracker, workspace, one safe worktree hook,
agent choice/concurrency/retry cap, server port, and prompt paths. Hook mechanics
live in the small helper script because safety logic should be tested rather
than explained inline.

### Stage prompts

`base.md`:

- Identify the ticket, current state, dependencies, and evidence location.
- State the ownership boundary above.
- Require compact ticket updates and honest `Not proven` language.
- On retry/rewind, read the latest failure section first.

`ready.md`:

- Validate that the imported ticket has scope, acceptance checks, proof
  commands, route, and resolved blockers.
- Actionable -> `Build`; missing load-bearing information -> `Blocked`.
- No source edits and no second planning framework.

`build.md`:

- Load the attached `supergoal` skill and execute exactly one Wayfinder ticket.
- Use the ticket's declared route: GREENFIELD, DEBUG, or LEGACY.
- Auto-approve the Supergoal plan only because this is an explicitly
  autonomous Symphony run; record that reason in the run vault.
- Use the current Symphony worktree as the Supergoal run worktree.
- Load `superdesign` when the ticket is tagged UI/design.
- Finish Supergoal's complete Build -> Improve full spec -> Improve edge cases
  -> Mandatory Adversarial Review -> Exact Verify/QA loop.
- Green -> `Verify`; unresolved authority/domain choice -> `Blocked`.

`verify.md`:

- Use a fresh turn and independently re-run the ticket's acceptance/proof
  commands; do not repeat the full Supergoal method in prose.
- Browser surface -> run attached `superqa` in REGRESSION mode and record the
  report path plus pass/fail and side-effect counts.
- Required behavior or regression failure -> append a concise failure section
  and rewind to `Build`.
- Non-blocking UX/product improvement -> create or update a follow-up Wayfinder
  ticket; do not expand the current ticket silently.
- All acceptance checks proven -> `Done`; missing environment/credentials ->
  `Blocked` with the exact blocker.

## Wayfinder-to-board contract

Add a thin adapter rather than changing Supergoal's file format or expanding
OneShot.

Input:

- `wayfinder/map.md`
- `wayfinder/tickets/*.md`

Required ticket fields:

- stable Wayfinder ID and title;
- user outcome and vertical scope;
- acceptance checks;
- exact proof commands or proof type;
- `Route` (`GREENFIELD`, `DEBUG`, or `LEGACY` for delivery tickets);
- `Blocked by` and `Unblocks` edges;
- non-goals;
- optional `Kind` and QA metadata.

Output card keeps only:

- Goal
- Scope
- Acceptance Criteria
- Proof
- Dependencies
- Route / attached skills
- Non-goals
- a machine-owned sync marker containing source path and content hash

Sync rules:

1. Preserve stable IDs when valid; otherwise allocate atomically through
   `FileBoardTracker.create_with_next_identifier()` and persist the mapping.
2. Import the full graph with `blocked_by` edges by default so downstream work
   is already scheduled; `--frontier-only` opts into the current frontier.
3. Preserve `blocked_by` edges.
4. Re-sync is idempotent.
5. Re-sync may update the machine-owned specification region only while a card
   is `Ready` and untouched by a worker.
6. Never overwrite execution notes, evidence, state, agent selection, or human
   edits on `Build`, `Verify`, `Done`, or `Blocked` cards.
7. Parse errors identify the source file and missing/invalid field; partial
   imports roll back rather than leaving a half-created dependency graph.

## Skill routing

Skill routing comes from explicit Wayfinder metadata, not keyword guessing in
the worker prompt.

| Ticket metadata | Attached skills |
| --- | --- |
| Any delivery `Route` | `supergoal` |
| `Kind: ui` or `Kind: design` | `supergoal`, `superdesign`, `superqa` |
| Browser launch/URL declared | `supergoal`, `superqa` |
| `Kind: customer-research` or `Kind: product-spec` | `supergoal`, `superpm` |
| Ambiguous kind | `supergoal` only; do not guess |

For the initial Wayfinder intake, SuperPM is invoked only when customer demand,
market choice, positioning, or product requirements are load-bearing. The map
must distinguish observed evidence from assumptions and name the metric the
first sellable slice is expected to move.

The factory preflight must verify that every referenced skill is discoverable
by the selected backend. Project-local skill attachment currently injects only
the main `SKILL.md`; a full skill directory or verified backend-global install
is required when that skill references supporting files.

## Beginner command surface

Target interaction:

```bash
symphony factory init --agent codex
symphony factory sync ./wayfinder
symphony factory start ./wayfinder
```

- `init`: copy the starter bundle, initialize `kanban/`, select one installed
  backend, and print the exact Supergoal WAYFINDER prompt for the product idea.
- `sync`: validate and idempotently import the Wayfinder frontier.
- `start`: run `sync`, `symphony doctor ./WORKFLOW.md`, then start the managed
  service. It stops before launch if either earlier step fails.

After the adapter path is proven, add the optional one-command intake:

```bash
symphony factory start --goal "<product outcome>"
```

This creates one planning-only intake ticket. Its Supergoal WAYFINDER run may
use SuperPM, writes the map/tickets, syncs them, and then exits. It must not
write product code. Keeping this as the last slice avoids hiding unproven
planning/import behavior behind a convenience wrapper.

## Implementation slices

Each slice is independently reviewable and test-first.

### 1. Pin the starter contract

Files:

- Create `docs/autonomous-dev-factory.md`.
- Create focused contract tests under
  `tests/templates/test_autonomous_dev_template.py`.

Red tests:

- Starter has exactly `Ready`, `Build`, `Verify` active states and `Done`,
  `Blocked` terminals.
- Workflow/prompt line budgets hold.
- Starter contains no advanced-only blocks.
- Build delegates to Supergoal without restating its role-loop phases beyond
  the required contract anchor.
- Verify contains independent proof, rewind, Blocked, and SuperQA routing.

Check:

```bash
.venv/bin/python -m pytest -q tests/templates/test_autonomous_dev_template.py
```

### 2. Build the minimal template bundle

Files:

- Create `templates/autonomous-dev/WORKFLOW.md`.
- Create `templates/autonomous-dev/prompts/{base,ready,build,verify}.md`.
- Create `templates/autonomous-dev/scripts/setup-worktree.sh` by extracting
  only the required safe worktree behavior from
  `scripts/symphony-setup-worktree.sh`.

Rules:

- One selected backend; concurrency defaults to 1.
- `max_attempts` remains a small explicit backstop.
- No nested Supergoal worktree.
- Keep the current advanced templates unchanged in this slice.

Checks:

```bash
.venv/bin/symphony doctor ./templates/autonomous-dev/WORKFLOW.md
.venv/bin/python -m pytest -q tests/templates/test_autonomous_dev_template.py tests/test_doctor.py
```

### 3. Add the Wayfinder parser and atomic sync

Files:

- Create `src/symphony/factory/wayfinder.py` for parsing/validation.
- Create `src/symphony/factory/sync.py` for mapping and atomic board writes.
- Add unit tests in `tests/factory/test_wayfinder.py` and
  `tests/factory/test_sync.py`.

Reuse:

- `FileBoardTracker.create_with_next_identifier()` for collision-safe IDs.
- Existing ticket Markdown and dependency parsing helpers.
- Existing tracker mutation methods; do not create a parallel board writer.

Red tests:

- Complete ticket parses; missing route/acceptance/proof fails with source
  location.
- Dependency graph maps correctly and rejects cycles that leave no frontier.
- First sync creates the expected cards and skill metadata.
- Second sync creates nothing and preserves state/evidence/human edits.
- A multi-ticket validation or write failure leaves no partial import.
- Full-graph default and `--frontier-only` behavior are distinct and deterministic.

Check:

```bash
.venv/bin/python -m pytest -q tests/factory/test_wayfinder.py tests/factory/test_sync.py tests/test_tracker_file.py
```

### 4. Expose normal skill attachment and factory commands

Files:

- Add `--skills` support to `src/symphony/cli/board.py`.
- Create `src/symphony/cli/factory.py`.
- Route `factory` in `src/symphony/cli/main.py`.
- Add `tests/test_factory_cli.py` and extend
  `tests/test_board_cli_subcommands.py`.

Behavior:

- `board new --skills supergoal,superdesign` uses the existing tracker field.
- `factory init` refuses to overwrite an existing workflow unless the user
  explicitly selects a different target path.
- `factory start` is sequential: sync -> doctor -> managed service start.
- No service starts after a failed sync, missing skill, backend auth failure,
  or failed Doctor check.

Check:

```bash
.venv/bin/python -m pytest -q tests/test_factory_cli.py tests/test_board_cli_subcommands.py tests/test_cli_main_routing.py
```

### 5. Add skill discovery preflight

Files:

- Add a focused factory preflight module under `src/symphony/factory/`.
- Extend Doctor only through a factory-specific check; do not make global
  skills mandatory for existing workflows.
- Add tests beside the factory and Doctor suites.

Red tests:

- Full project-local skill directory passes.
- Verified backend-global install passes.
- Main `SKILL.md` without required referenced files fails with a useful path.
- Missing `superdesign`, `superpm`, or `superqa` fails only when a synced ticket
  requests that overlay.

Check:

```bash
.venv/bin/python -m pytest -q tests/factory tests/test_doctor.py
```

### 6. Prove the per-ticket autonomous loop

Files:

- Add a mock-agent lifecycle test for the starter profile.
- Add prompt-render fixtures for all three active states.

Scenario:

1. Sync two Wayfinder tickets where the second depends on the first.
2. Let Symphony promote the first ticket from `Ready` to `Build` without an
   agent dispatch.
3. Assert the Build prompt loads Supergoal and the correct overlays.
4. Simulate green Supergoal evidence and move to `Verify`.
5. Simulate a failed browser regression; assert rewind to `Build` and no
   second ticket dispatch.
6. Simulate repaired behavior plus a SuperQA report; assert `Done`.
7. Assert the dependent ticket becomes eligible only after finalization.

Checks:

```bash
.venv/bin/python -m pytest -q tests/test_agent_lifecycle_e2e.py tests/test_orchestrator_phase_transition.py tests/test_workflow_pipeline_prompt.py
```

### 7. Add the beginner quick start and preserve the advanced path

Files:

- Rewrite the first-run path in `README.md` and `README.ko.md` around
  `symphony factory`.
- Update `skills/symphony-skill/SKILL.md` and its bootstrap reference.
- Move or copy the current production template to an explicitly named
  advanced example only after compatibility tests pin its path.
- Update `CHANGELOG.md` with migration notes; do not overwrite user workflows.

Docs must explain:

- the five ownership boundaries;
- the three beginner states;
- how to customize states/prompts after the first successful run;
- the advanced profile for wiki, Human Review, Linear/Jira, multi-provider,
  Slack, performance comparison, and custom merge policy;
- why “lucrative” is a validated-demand goal rather than a guaranteed result.

Check:

```bash
.venv/bin/python -m pytest -q tests/skills tests/test_workflow_pipeline_prompt.py
git diff --check
```

### 8. Add one-command intake only after the base path is green

Files:

- Extend `src/symphony/cli/factory.py` and factory tests.
- Add a planning-only intake fixture.

Red tests:

- `--goal` produces a Wayfinder intake, not a product-build card.
- Intake attaches `supergoal` and conditionally `superpm`.
- Successful intake syncs vertical tickets exactly once.
- Failed/ambiguous planning produces no product-code ticket and stops in
  `Blocked` with the decision needed.

Check:

```bash
.venv/bin/python -m pytest -q tests/factory tests/test_factory_cli.py tests/test_agent_lifecycle_e2e.py
```

## Final verification

Run only after all slices are integrated:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m pyright
.venv/bin/symphony doctor ./templates/autonomous-dev/WORKFLOW.md
git diff --check
```

Then run a disposable real-agent proof in a temporary repository:

1. `factory init` with one available backend.
2. Wayfind a small browser product with one UI ticket and one dependent
   behavior ticket.
3. Sync twice and prove no duplicate cards.
4. Run the first ticket through Supergoal + Superdesign.
5. Run SuperQA in Verify, capture its real report path, and exercise one rewind.
6. Confirm the dependent starts only after the first ticket is finalized.
7. Confirm the target branch contains the verified change and the host working
   tree has no unrelated edits.

Report static/mock proof separately from this real-agent proof. If the live
backend, browser, or skill install is unavailable, mark that layer `Not proven`.

## Non-goals

- Removing advanced Symphony runtime features.
- Merging Supergoal WAYFINDER and delivery modes into one public mode.
- Reimplementing Supergoal, Superdesign, SuperPM, or SuperQA inside Symphony.
- Inferring customer demand or revenue without evidence.
- Running every backend in the beginner template.
- Auto-publishing, deploying, spending money, or contacting customers without
  explicit authority.

## Rejected alternatives

- Expand OneShot into the default: rejected because it adds another planning
  constitution and lane model instead of reusing Supergoal Wayfinder.
- Keep the four-stage production prompt and shorten comments only: rejected
  because duplicate process ownership remains.
- Remove Verify because Supergoal already verifies: rejected because a fresh,
  independent rerun is the queue's delivery decision and is where SuperQA
  belongs.
- Attach every skill to every ticket: rejected because it increases context and
  causes overlapping routers; metadata should select only relevant overlays.
- Replace the advanced template in the first commit: rejected because a new
  default needs an end-to-end proof and migration path before promotion.
- Promise a lucrative outcome: rejected because automation can test product
  hypotheses and improve delivery speed, not guarantee market demand or sales.
