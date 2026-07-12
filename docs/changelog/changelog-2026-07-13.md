# Changelog — 2026-07-13

## Factory stage context and dependency scheduling

- Root cause: the factory injected every attached skill into every stage, so a
  one-step Ready gate received the full Supergoal workflow and explored for
  eight steps. Skill context is now selected by stage: none in Ready, all in
  Build, and only SuperQA in Verify.
- Root cause: sync rendered dependency IDs only in the Markdown body. Although
  the file tracker can parse that section as a compatibility fallback, factory
  sync did not own a machine-readable dependency field. Create and Ready
  refresh now persist `blocked_by` in frontmatter and include it in the managed
  integrity hash, rollback, and idempotency contract.
- Final root cause: even without attached skills, clean Ready requests consumed
  20,962 and 87,668 tokens before editing. Ready is now a deterministic machine
  dependency gate, so it has no turn/token budget; Build and Verify retain
  their bounded worker budgets.

## Factory acceptance-to-proof coverage

- Root cause: the factory Verify contract validated table shape, commands, and
  pass results, but never compared table rows with the ticket's original
  `## Acceptance criteria`. One evidence row could therefore allow an
  incompletely verified ticket to reach Done.
- Decision: parse the strict Wayfinder list forms (`-`, `*`, `+`, `1.`, and
  `1)`, including task checkboxes) and require every declared item to consume
  one distinct Verification criterion row.
- Matching normalizes Unicode, case, whitespace, code spans, emphasis, and
  Markdown link labels, then requires exact normalized text. Fuzzy or substring
  matching was rejected because it could silently map a broad proof claim to a
  different criterion.
- Missing, empty, or list-free Acceptance criteria now fail the factory Verify
  contract. The worker prompt states the same one-row-per-item rule.

## Factory worker path locality

- Root cause: factory prompts named the host board and skill directories even
  though each worker already has local copies in its isolated worktree.
  OpenCode treated those host paths as external directories and spent turns on
  permission checks and rediscovering Symphony internals before product work.
- Decision: factory prompts now name the worktree-local ticket and attached
  skill paths. Factory prompts no longer inline complete skill bodies: they
  name the local `SKILL.md` for one deliberate read, avoiding the duplicate
  body plus file read observed in OpenCode. Advanced workflows retain their
  existing host-path and inline-body behavior.
- The Build contract now states that intake and Wayfinder planning are already
  complete and directs the worker to the ticket, relevant source, and tests.
  Inspecting `WORKFLOW.md`, factory prompt files, or the WAYFINDER map was
  rejected because those files describe orchestration, not the ticket's product
  behavior.
- Factory ticket prompt paths are derived directly from the current worktree's
  `kanban/<identifier>.md`. They no longer depend on an orchestrator-level
  tracker attribute that is not persistent between polling and dispatch.
- Mandatory Supergoal improve, edge, adversarial, and exact-QA roles remain
  distinct sequential passes in the current worker. Nested worktrees were
  rejected because Symphony already supplies the isolated ticket worktree;
  backends without subagents adapt fresh-role boundaries as concise passes.

## Factory OpenCode lifecycle ceiling

- Live OpenCode usage is reported for every internal model/tool step, so one
  Symphony turn can cross a state token cap before returning a completed turn.
  The 180,000-token Build cap stopped after about six discovery steps, before
  product code, even though the lifecycle remained on its first turn.
- After removing duplicate skill-body and factory-rediscovery overhead, clean
  runs showed that standalone Supergoal vault creation—not product work—was
  still the dominant cost. The ticket-ledger adapter removes that duplicate
  work, so the hard ceiling returns to 400,000 tokens for Build and 120,000 for
  Verify, with at most three and two completed turns respectively. The combined
  520,000 ceiling is less than half the original 1.2M failed run while retaining
  each mandatory Supergoal pass as a machine-gated ticket section.

## Factory shared-board ownership

- Root cause: the generated project had no runtime `.gitignore`, so sync cards
  were committed with the starter project. A worker checkout then contained a
  nonempty real `kanban/`; the setup hook silently kept it after `rmdir`
  failed. The worker edited that stale private Ready card while the host file
  tracker continued polling a different card, so Verify was unobservable.
- `factory init` now merges, without overwriting user rules, ignores for
  `kanban`, `.symphony/`, `log/`, and `WORKFLOW-PROGRESS.md`. These files
  are runtime coordination state; Wayfinder inputs and delivered product code
  remain versioned.
- The worktree hook still removes an empty checkout directory and is safe to
  rerun, but now stops with an actionable error when a nonempty real board
  directory or wrong symlink blocks the host-board link. Silently continuing
  was rejected because it creates two sources of truth.
- A factory prompt uses `kanban/<id>.md` only when that path resolves to the
  same card returned by the host tracker. Otherwise it names the host card, or
  omits the path when neither source is available. File existence alone was
  rejected because a stale tracked card also exists.

## Factory ticket-ledger adapter

- Root cause: applying Supergoal's standalone run-vault setup inside each
  Symphony ticket duplicated the Wayfinder scope and spent worker turns
  creating and rereading GOAL, PLAN, QA, run-state, and completion files.
- Decision: in the beginner factory only, the Wayfinder ticket is the approved
  scope and plan and its sections are the run ledger. Advanced workflows and
  direct Supergoal runs keep their full standalone artifact contract.
- Build still executes the complete sequential Supergoal delivery loop. Its
  transition gate now requires concise nonempty `Implementation`, `Full Spec
  Review`, `Edge Case Review`, `Adversarial Review`, and `Test Evidence`
  sections before independent Verify can run.
- A single generic self-review section was rejected because it could claim the
  loop without proving that the full-spec, edge-case, and adversarial roles
  were each performed.

## Phase-local token budget accounting

- Live OpenCode proved two timing hazards. A Build turn can move the shared
  card to Verify before its final usage event, and a replacement Verify backend
  can emit usage from `start_session` before `_rebuild_backend_for_phase`
  returns.
- Hard-cap selection now stays pinned to `state_at_turn_start` while a turn is
  live, so a mid-turn board refresh cannot charge Build usage to Verify's lower
  cap. Logs and the persisted error name that same source state.
- A cohesive phase reset now runs after the old backend stops but before the
  replacement starts. Session high-water marks, state-local totals, the EMA
  window, and budget-hit flags reset there; ticket-lifetime token and stats
  counters remain cumulative.
- Resetting after `start_session` was rejected because synchronous usage events
  can inherit the prior phase's total and cancel the new backend before its
  first prompt. Using the card's newest state for every event was rejected
  because the card can move before the producing turn has actually ended.

## Factory intake schema and graph visibility

- The init handoff now prints the exact strict Wayfinder ticket schema,
  including required YAML keys and headings plus every supported route,
  overlay, kind, and browser value. A prose-only instruction was rejected
  because a planning agent could produce reasonable Markdown that the strict
  factory parser could not import.
- Factory sync and start now import the complete dependency graph with
  `blocked_by` edges by default. This keeps downstream work visible and lets it
  become actionable without a human running sync after every completed slice.
  `--frontier-only` preserves the narrower behavior as an explicit operator
  choice; the old `--all` spelling remains a compatibility alias.

## Factory worktree sentinel and scoped auto-commit

- The root default keeps `kanban/.gitkeep` so a fresh checkout has the board
  directory required by Doctor. The factory setup hook now removes that one
  regular sentinel before linking the host board, but still rejects a sentinel
  accompanied by any stale card or other real content.
- Git rejects an explicit negative pathspec when the excluded `kanban` path is
  an ignored symlink replacing a tracked directory. Auto-commit now stages only
  the current workspace (`.`), then restores configured exclusions from `HEAD`
  both before and after the squash reset. This preserves scoped staging and the
  base sentinel without committing the shared runtime board.

## Factory terminal gate and live OpenCode calibration

- Root cause: forward contracts ran only at the next active-stage loop. A
  Verify worker could move directly to terminal Done, exit before that next
  loop, and reach auto-commit/merge without a valid Verification table.
  Terminal transitions now evaluate the producing-stage contract immediately;
  invalid evidence is noted and rewound before cleanup.
- Root cause: the token-cap exit path trusted any card state change as progress.
  A capped Build turn could set Verify before appending its required ledger and
  bypass the Build contract. Cap-time advances now use the same full-body
  contract gate; an invalid advance rewinds and parks in Blocked with both
  contract and budget evidence.
- The latest real OpenCode run created and tested the requested product files
  but reached 412,607 cache-inclusive tokens before its deferred ticket-ledger
  edit. Build now tells agents to skip generic workspace/tool inventory and to
  append each ledger section immediately after its pass. The rerun measured
  640,068 tokens after all five mandatory passes and exact green evidence but
  before the final state edit, while another clean seed reached 532,812 before
  writing its ledger. OpenCode's cache-inclusive step totals vary materially
  between seeds, so the hard ceilings are calibrated above the observed tail:
  900,000 for Build and 250,000 for Verify, with a 1,150,000 combined cap.
  Turn and per-state-turn watchdogs remain independently bounded.
- The first terminal-gated Verify run exposed a prompt/validator mismatch: the
  agent split one acceptance bullet into two rows and wrote verbose command
  output in each result cell. The validator correctly rejected the noncanonical
  rows. Verify now says one complete bullet or numbered item per row, no
  splitting or merging, exact `pass` in the result cell, and detailed output
  after the table.
- A subsequent clean seed completed Build at 681,154 tokens, then used 262,996
  cache-inclusive tokens in Verify. The 250,000 Verify cap blocked the ticket
  before contract evaluation. Verify is therefore raised to 350,000 and the
  matching total cap to 1,250,000; the two-turn Verify watchdog remains the
  independent hard bound.
- Another seed interpreted the ban on named run-vault files narrowly and
  created `docs/TASK-1/` role outputs, then reached the Build cap. The adapter
  now forbids every separate process-evidence file or directory and requires
  all role evidence in the shared ticket ledger. Raising Build again was
  rejected because it would preserve the non-minimal behavior instead of
  correcting it.
- The first clean-ledger completion reached Done, passed the exact Verify
  table contract, and auto-merged, but its checkpoint commit captured Python
  bytecode recreated by the final test run. Factory init now adds standard
  Python cache rules while preserving every existing user ignore rule. This
  keeps generated proof artifacts out of autonomous commits without asking
  agents to edit project configuration.
- Final adversarial review found that stale-worker reconciliation could accept
  a terminal card after its grace window without running the producing-stage
  contract. Reconcile now full-refreshes and enforces that same factory gate
  before cancellation, commit, merge, or removal; invalid evidence rewinds
  while preserving the worker and workspace.
- The same review found a prompt/validator mismatch: the beginner prompt
  requires literal `pass` and exactly one row per declared criterion, while
  the validator accepted broader green tokens and extra rows. The factory
  contract now enforces the documented exact multiset; advanced scorecard
  semantics remain unchanged.
- GitHub CI then exposed that factory CLI tests inherited the developer's
  installed Supergoal skill. The runtime correctly requires that skill, but
  unit tests must not depend on HOME. They now install a minimal valid skill
  fixture and the exact coverage command passes with an empty HOME.

## Factory-owned skill runtime bundle

- The beginner factory now resolves its four standard skills from pinned
  package resources before checking mutable home-directory installs. This
  removes a hidden second download while retaining local fallback for an
  arbitrary custom skill and preserving already customized project copies.
- The bundle contains the runtime closure named by each factory overlay,
  including both Superdesign JavaScript gates and attribution sources, the
  SuperPM intent-to-critic references, and SuperQA's shell launcher,
  references, package metadata, and TUI runtime. A manifest records the source
  repositories and commits; every skill retains its upstream license.
- A network installer was rejected because it makes init availability- and
  version-dependent. Assuming package resources are real filesystem paths was
  also rejected; the copier walks the `Traversable` resource tree so a zipped
  wheel works as well as an editable checkout.
- Bundling a browser skill does not imply a bundled browser. Documentation now
  names SuperQA's Python extras and Playwright Chromium requirement, including
  the possible first-run network dependency.
- Edge-case review found that `--force` assumed an existing skill destination
  was a real directory. Factory replacement now unlinks files and symlinks
  without following them, removes only real directories recursively, and
  detects broken skill symlinks during init preflight before copying assets.
- R-LOOP iteration 2 made every shipped shell gate executable in the source
  tree, wheel metadata, filesystem-installed copies, and zip-import-generated
  copies. The resource copier sets the executable bits explicitly because a
  generic `Traversable` exposes bytes, not portable source mode metadata.
- SuperQA now includes the README required by its advertised editable install.
  Beginner English, Korean, and operator docs also name Superdesign's actual
  Node.js 18+, `@playwright/cli`, skill-install, and browser prerequisites.
- Recursive closure checks now follow `.cmd`, `.html`, `.tcss`, and `.toml`
  references in addition to the earlier script and Markdown extensions. That
  stricter scan found the storyboard template's missing `scripts/shoot.sh`, so
  the pinned runtime now includes an executable adapted helper instead of
  weakening the reference check.
- The redistribution notice now inventories every adapted or method source
  named by shipped files and retains the authoritative MIT or Apache-2.0 text
  available during the audit. Follow-up evidence pins stitch-landing-skill at
  `4b4c7fb00d7d77d48403f6b7682c3fb502e0db0c`, retains its exact cskwork MIT
  notice, and limits its attribution to the shipped `assets.md` and `web.md`
  inspiration. This engineering inventory is not legal approval; public
  release still requires maintainer legal review.
