# PLAN: beginner autonomous development factory

## Intent

- Outcome: make the minimal Wayfinder -> Symphony -> Supergoal delivery loop
  the default file-board experience, preserving the current production flow as
  an advanced profile.
- Proof: focused red-green tests, full repository gates, Doctor on the shipped
  default, and one disposable real OpenCode-agent run.
- Stop condition: every GOAL criterion is proven, the branch is pushed, and a
  PR into `dev` is open; otherwise report the exact Not proven layer.
- `max_iterations`: 8.

## Approval

- Status: approved-by-user.
- Evidence: user replied “good implement with pr and make this default in oh my
  symphony and do a test run with opencode agents” after reviewing the plan.

## Architecture

- Supergoal owns Wayfinder planning and the per-ticket build/improve/review/QA
  loop.
- Symphony owns file-board scheduling, dependency order, isolated worktrees,
  retry/rewind, and an independent Verify turn.
- Superdesign applies only to UI/design tickets; SuperPM only to customer
  research/product-spec tickets; SuperQA only to browser/runtime verification.
- The default states are `Ready -> Build -> Verify -> Done`; Verify failures
  rewind to Build; missing authority/environment moves to Blocked.
- Existing production prompts/workflow move to a clearly named advanced path.

## Steps

1. Add failing default-template contract tests for lane set, prompt size and
   ownership anchors, then add the minimal template bundle.
2. Add failing Wayfinder parser/sync tests, then implement cohesive parser and
   sync modules using `FileBoardTracker.create_with_next_identifier()` and
   existing mutation APIs.
3. Add `board new --skills`, then add `factory init`, `factory sync`, and
   `factory start` CLI routing with overwrite and preflight safety.
4. Add metadata-based overlay routing and focused skill-discovery checks.
5. Add starter lifecycle proof: dependency order, Verify rewind, Done, and
   Blocked behavior.
6. Promote the minimal file workflow to the root default; preserve the current
   workflow and prompts under an advanced example with compatibility tests.
7. Update README/README.ko, operator skill routes, bootstrapping docs, and
   release changelog.
8. Run focused/full gates, then run a disposable real OpenCode-agent workflow.
9. Run adversarial review, fix grounded findings, exact verify again, commit,
   push, and open the PR into `dev`.

## Files

- Default/advanced templates: `WORKFLOW.file.example.md`,
  `templates/autonomous-dev/**`, `examples/advanced/**`.
- Prompts: default `docs/symphony-prompts/file/**`, preserved advanced prompt
  tree under `examples/advanced/` if moving the root default requires it.
- Factory implementation: `src/symphony/factory/**`,
  `src/symphony/cli/factory.py`, `src/symphony/cli/main.py`.
- Skill CLI seam: `src/symphony/cli/board.py`.
- Tests: new `tests/factory/**`, template contracts, CLI tests, lifecycle and
  existing workflow prompt tests.
- Docs: README pair, Symphony operator skill/bootstrapping reference,
  `CHANGELOG.md`, plan and changelog records.

## Tools & Skills

- `supergoal`: conductor/build/improve/review/verify contract.
- `symphony-skill`: default template, board, Doctor, and real-run rules.
- codebase-memory graph tools before code discovery.
- `pytest`, `ruff`, `pyright`, `symphony doctor`, `git diff --check`.
- Real OpenCode CLI through Symphony for the final runtime proof.

## Verification Strategy

- Before: prove no `factory` CLI exists, the default file template has four
  active states, and no Wayfinder importer exists.
- Red: each new contract test fails for the missing behavior before code.
- Green: focused suites after each cohesive slice.
- Regression: existing tracker, CLI, Doctor, prompt, orchestrator, and full
  repository tests.
- Runtime: temporary repository and board using the shipped default and
  `agent.kind: opencode`; capture service state/log/ticket transitions.
- Backward trace: every diff hunk maps to one GOAL criterion; advanced behavior
  is preserved rather than silently removed.

## Rejected Approaches

- Expanding OneShot: creates a second planning constitution.
- Encoding the Supergoal role loop again in prompts: duplicates ownership.
- Removing independent Verify: loses a fresh queue-owned proof boundary.
- Attaching every skill globally: wastes context and creates router conflicts.
- Deleting production features: breaks advanced users; preserve them as an
  explicit profile.
