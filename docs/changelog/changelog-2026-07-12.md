# Changelog — 2026-07-12

## Autonomous factory loop-back fixes

- Adversarial review: render each attached skill's absolute runtime root and
  instruction path in the backend-neutral prompt. Relative references now
  have one explicit base for OpenCode and every other backend; backend-specific
  discovery links were unnecessary.
- Re-entry safety: reuse an existing `symphony/<ID>` branch and preserve its
  recorded base metadata when the setup hook runs again. Also treat a dangling
  `kanban` symlink as already installed.
- Delivery gate: require a non-empty `criterion | command | result` table in
  factory Verify, with a concrete command and passing result per row. A bare
  prose claim can no longer satisfy the contract before Done/auto-merge.
- CLI compatibility: recognize a positional Wayfinder directory for the
  approved `factory sync ./wayfinder` and `factory start ./wayfinder` forms.
  A positional project path still works, and explicit `--wayfinder` retains
  precedence.
- Edge safety: include generated title, labels, and attached skills in the
  Ready-card managed hash; otherwise a re-sync could silently erase operator
  edits outside the hashed body region.
- Edge safety: constrain generated ticket prefixes to filename-safe values and
  accept spaces in provenance source paths. This prevents board-root traversal
  while keeping ordinary Wayfinder filenames idempotent.
- Skill routing: initialize with Supergoal only. Optional SuperPM,
  Superdesign, and SuperQA closures are installed when ticket metadata requests
  them, so a missing unrelated overlay does not block a beginner's first init.
- Packaging: replace recursive template package globs with the exact runtime
  asset paths and disable implicit package data. This keeps bytecode and the
  removed executable placeholder ticket out of clean wheels.
- Decision: promote the bounded `Ready -> Build -> Verify` file workflow and
  preserve the prior production workflow at
  `examples/advanced/WORKFLOW.file.example.md`.
- Safety: hash the generated Ready-card region, roll back prior refreshes after
  any later sync failure, merge verified Done work through the existing target
  branch policy, and bound OpenCode turns and tokens.
- Compatibility: retain Claude stream-json `--verbose`, accept
  `--agent-kind` as an alias for `--agent`, and make Doctor check the same
  overridden port the service will bind.
- Full-spec decision: accept only GREENFIELD, DEBUG, and LEGACY delivery
  routes in synchronized tickets. QA-only, review-only, and prototype routes
  do not execute the promised complete per-ticket Supergoal delivery loop.
- Full-spec decision: keep the starter prompts short, but name the load-bearing
  hand-offs explicitly: autonomous plan approval and current-worktree reuse in
  Build, and SuperQA REGRESSION evidence plus follow-up Wayfinder tickets in
  Verify. This preserves one method owner without leaving worker behavior
  implicit.
- Safety: reject known overlay skills whose installed directory lacks the
  runtime reference or gate files used by that skill, instead of copying a
  discoverable but non-functional `SKILL.md` placeholder.

## Beginner autonomous-development factory plan

- Problem: the file-board starter exposes a 322-line workflow example and 285
  lines of prompts, plus wiki, merge, history, backend, and evidence machinery.
  It also repeats Supergoal-style planning and verification inside Symphony,
  so beginners face two overlapping process owners before their first ticket.
- Decision: plan a separate beginner profile where Supergoal owns Wayfinder and
  per-ticket delivery, while Symphony owns queueing, isolated worktrees,
  dependencies, retries, and a fresh independent Verify turn. The visible flow
  is `Ready -> Build -> Verify -> Done`, with `Blocked` as the only failure
  terminal.
- Decision: add a thin, idempotent Wayfinder-to-file-board adapter instead of
  changing Supergoal's output format. Route skills from explicit ticket
  metadata: Supergoal for every delivery ticket, Superdesign for UI/design,
  SuperPM for customer research/product specification, and SuperQA for browser
  verification.
- Decision: keep SuperQA improvement findings scope-safe. Required-behavior or
  regression defects rewind the current ticket; non-blocking improvements
  become follow-up Wayfinder tickets instead of silently widening the work.
- Why: Symphony is strongest at horizontal orchestration; Supergoal is already
  the vertical spec-to-proof contract. Keeping one owner per concern reduces
  prompt drift while retaining independent delivery evidence.
- Why: the starter becomes small without deleting production capabilities. The
  current workflow remains an advanced profile until the new bundle passes
  importer, lifecycle, Doctor, mock-agent, and real-agent/browser proof.
- Rejected: make OneShot the beginner factory. Its separate constitution,
  bootstrap, and lane model would add a third orchestration contract.
- Rejected: remove Symphony Verify because Supergoal has Exact Verify/QA. A
  fresh queue-owned rerun is the trustworthy delivery decision and the natural
  SuperQA boundary.
- Rejected: attach all skills globally. It wastes context and creates router
  conflicts; explicit Wayfinder metadata is the stable selection seam.
- Rejected: guarantee a lucrative product. The loop can require customer
  evidence, prioritize a first sellable slice, and measure outcomes, but demand
  and revenue remain market results.
- Plan: `docs/plans/2026-07-12-autonomous-dev-factory-default-template.md`.

## Beginner autonomous-development factory implementation

- Implemented package-owned factory assets under `src/symphony/factory/templates/`
  so editable installs and wheels use the same runtime source.
- Implemented explicit Wayfinder YAML frontmatter and validate duplicate IDs,
  missing dependencies, and cycles before the first board mutation.
- Chose source `id` provenance rather than title matching because titles are
  user-facing and mutable. Ready tickets may refresh their managed section;
  Build/Verify/terminal tickets are immutable to sync so worker evidence is not
  overwritten after dispatch.
- Added the separate `factory` contract profile. Reusing the production
  contract was rejected because it would leave Build ungated and require the
  advanced security/merge/wiki evidence in the beginner Verify turn.
- Kept `WORKFLOW.md`, `WORKFLOW.file.example.md`, existing prompt files, and
  default active-state constants unchanged. The default user experience moves
  through README and `symphony factory init`, avoiding a compatibility break
  for existing operators.
