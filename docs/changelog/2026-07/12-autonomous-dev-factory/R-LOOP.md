# R-LOOP: 2026-07-12 23:35 KST

## Missing or broken requirements

- [ ] Promote the minimal Ready -> Build -> Verify workflow to the root file
  default and preserve the production workflow under an explicit advanced
  path.
- [ ] Route `kind: ui` and `kind: design` to both `superdesign` and `superqa`.
- [ ] Refuse Ready-card refresh when the machine-managed region was edited;
  restore refreshed existing cards as well as deleting new cards if sync
  fails later.
- [ ] Support the documented `--agent` option, print the exact Supergoal
  WAYFINDER next-step prompt, and make Doctor honor `factory start --port`.
- [ ] Ensure Done delivers the verified ticket change to the configured target
  branch without silently stranding it in `symphony/<ID>`.
- [ ] Copy only the Supergoal runtime closure needed by workers; exclude
  development history, experiments, tests, TUI files, bytecode, and the
  executable placeholder ticket.
- [ ] Keep generated backend commands compatible, including Claude
  stream-json `--verbose`.
- [ ] Add a bounded OpenCode turn/token budget appropriate for the beginner
  template.

## Live-run evidence

- Disposable repo: `/private/tmp/symphony-factory-opencode-e2e-KnEvcb`.
- OpenCode proved `Ready -> Build`, created `factory_probe.py` and
  `test_factory_probe.py`, and the declared unittest passed.
- The first worktree attempt failed because `after_create` expected
  `SYMPHONY_ISSUE_ID`; hooks receive no ticket-specific environment. The
  template now derives the ID from `basename "$PWD"`.
- The Build turn exceeded 1.2M reported input tokens and did not reach Verify.
  The operator paused it and the managed service was stopped. This is a failed
  economics/usability gate, not completion proof.

## Regression note

Before the latest review, `1411 passed, 5 skipped`, Ruff, and focused Pyright
were green. Re-run them after fixes; do not treat that earlier green result as
proof of the corrected template.

## Smallest next fix

Implement the checklist above test-first, run focused factory/template tests,
then repeat mandatory adversarial review and a bounded real OpenCode lifecycle.

## 2026-07-13 live OpenCode failure fix

- Disposable run `/private/tmp/symphony-factory-opencode-e2e-y2mHpd` consumed
  204,595 tokens in Ready and ended Blocked after eight exploratory steps.
  Ready had received the full attached Supergoal skill even though it owns only
  the dependency/actionability gate.
- Factory skill injection is now stage-owned: Ready receives none, Build
  receives all ticket skills, and Verify receives only `superqa`. Supergoal is
  deliberately excluded from Verify so the independent proof turn does not
  rerun the delivery loop.
- Factory dependencies remain readable in `## Dependencies` and are now also
  persisted in managed `blocked_by` frontmatter. The existing file tracker
  still hydrates blocker states from the live board, so dependents stay idle
  until their prerequisites are Done.
- The reduced-context policy caps Ready at one turn/80,000 tokens, Build at
  eight turns/180,000 tokens, Verify at three turns/60,000 tokens, and the
  fallback state cap at 240,000 tokens across at most 12 turns.
- A clean replacement run at
  `/private/tmp/symphony-factory-opencode-e2e-YGvVfw` proved the first
  reduced-context request costs about 21,000 tokens before OpenCode can act;
  the 20,000 Ready cap cancelled that request before its first read. Ready is
  now 80,000 tokens, enough for a small read-and-transition sequence while
  remaining far below the original 204,595-token exploratory failure.
- Exact runtime lifecycle proof remains pending another clean run.

## 2026-07-13 deterministic Ready correction

- Two clean Ready requests consumed 20,962 and 87,668 tokens before editing,
  even with no attached skills and a direct prompt. Budget calibration was the
  wrong control point: Ready owns no product work.
- Factory Ready is now a machine dependency gate. Normal eligibility keeps
  unresolved dependencies visible; actionable cards move to Build through the
  tracker without a workspace, backend, or agent slot.
- Focused RED: promotion and tracker-failure tests failed before the poll-loop
  change. GREEN: four Ready orchestrator cases pass.

## 2026-07-13 workspace-local prompt failure

- A clean bounded run reached Build with zero Ready agent tokens, but OpenCode
  stopped at 423,500/400,000 Build tokens without creating product files.
- Exported ground truth identified two remaining causes: `full_ticket_path`
  was blank because the helper expected a persistent tracker object the
  orchestrator does not own, and the prompt embedded the full Supergoal body
  before directing OpenCode to read the same `SKILL.md` again.
- Raising the cap again is rejected. The smallest next fix is to derive the
  factory ticket path directly from the worktree and make factory skill
  injection location-only while keeping the full Supergoal process contract
  in the stage prompt. Advanced skill injection stays unchanged.

## 2026-07-13 shared-board correction

- A later clean run exposed a deeper boundary defect: sync cards had been
  committed because generated repositories lacked runtime ignore rules.
  Checkout created a nonempty real `kanban/`, the hook ignored its failed
  `rmdir`, and the worker updated a private card the host tracker never saw.
- Factory init now merges runtime ignore rules. The hook rejects nonempty real
  board directories instead of continuing, and local prompt paths require
  identity with the host tracker's card.
- Existing repositories are not rewritten automatically. Re-running
  `factory init --force` merges the ignores; already tracked cards must be
  untracked deliberately before recreating the worker workspace.

## 2026-07-13 closure

- Terminal transitions now enforce the producing-stage contract, including
  cap-time advances. Wrapped acceptance bullets are parsed as one logical
  criterion and must map one-to-one to passing Verification rows.
- The factory adapter stores all Supergoal role evidence on the ticket; it
  forbids separate process-vault files and ignores generated Python bytecode.
- Clean generated-project proof reached Done through real OpenCode Build and
  Verify sessions, committed exactly two product files, and auto-merged to
  disposable main. Full tests, lint, types, Doctor, wheel, and diff gates pass.
- Published as PR #57 into `dev`; no unresolved delivery gate remains.
