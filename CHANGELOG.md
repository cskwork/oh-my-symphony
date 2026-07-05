# Changelog

All notable changes to oh-my-symphony are documented in this file.
Full release notes (with verification steps and per-commit detail) live on
the [GitHub Releases page](https://github.com/cskwork/oh-my-symphony/releases);
this file is the in-repo summary.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Post-v0.11.0 changes will be listed here.

## [0.11.0] - 2026-07-05 - OpenCode terminal cleanup

### Fixed

- Terminal reconciliation now bounds the natural-exit grace window by the first
  poll tick that observes a running card in a terminal lane. OpenCode liveness
  heartbeats can no longer refresh the 60 second grace window forever and leave
  `Human Review` cards stuck in `/api/v1/state` as `running: 1`.
- Checked-in SMA-20 QA smoke evidence now passes the repository `ruff check .`
  gate.

### Added

- Regression coverage for terminal-state cleanup with recent backend
  heartbeats:
  `tests/test_orchestrator_dispatch.py::test_reconcile_terminal_grace_expires_despite_recent_heartbeat`.

### Verified

- Real OpenCode file-board E2E on `dev` reached `Human Review`, logged
  `reconcile_terminate_terminal` at `terminal_state_age_s=61.1`, and drained
  `/api/v1/state` to `running: 0`, `retrying: 0`.

## [0.10.1] - 2026-07-05 - Prompt compaction default-on

### Changed

- Prompt context compaction now defaults on for new workflows. Workflows that
  need the full raw ticket history in every first-turn prompt can set
  `agent.compact_issue_context: false`.

## [0.10.0] - 2026-07-05 - Codex E2E reliability hardening

### Added

- State-local same-state turn watchdogs via `agent.max_state_turns_by_state`,
  with budget notes that report the effective per-state limit.
- Token attention signals via `agent.token_attention_threshold_by_state`; high
  token use stays telemetry unless an explicit hard cap is configured.
- Stage-aware prompt context compaction behind
  `agent.compact_issue_context: true`, including full-ticket path links for file
  workflows so workers can audit the raw card when needed.
- Workspace owner sidecars and hook-output manifests under the workspace root,
  so reused `TASK-*` paths and failed `after_create` setup are diagnosable.
- Structured contract-failure rows and scoped rewind prompts for Verify evidence
  failures, including row numbers and expected artifact path shapes.

### Changed

- OpenCode token accounting now follows the 1.17 `step_finish.part.tokens`
  schema, including nested cache and reasoning fields without double-counting
  explicit cache totals.
- `symphony doctor` now reports masked setup commands such as `|| true` and
  piped `tail` as warnings by default, with
  `hooks.fail_on_warning_patterns: true` available when a workflow wants those
  checks to fail.
- Workflow state renames carry the new per-state turn-cap map along with the
  existing concurrency and token maps.
- Public docs now lead with the built-in 9999 browser admin UI and a sanitized
  screenshot, while still keeping the terminal TUI visible.

### Fixed

- `GET /api/v1/issues/<ID>` now serializes raw YAML frontmatter `date` and
  `datetime` values before returning JSON, fixing the `CODEX-E2E-001` detail
  drawer load failure.
- File tickets auto-heal shallow accidental indentation on canonical top-level
  metadata keys such as `updated_at`, preventing post-turn refresh failures from
  malformed agent-authored YAML.
- Verify contract checks reject placeholder or prose-only evidence cells, so
  quality gates require durable artifacts rather than hollow table entries.
- File-board dependency blockers now apply beyond `Todo`; unresolved dependency
  IDs stay visible through `blocked_dependency` instead of letting held work run.

### Verification

- `rtk pytest -q` -> 1,108 passed, 2 skipped during the reliability hardening
  pass.
- `pytest tests/test_webapi.py tests/test_web_api_smoke_script.py -q` -> 24
  passed for the issue-detail serialization fix.
- Fresh remote-clone Codex E2E rerun reached `Human Review` on `CODEX-E2E-002`
  with `worker_exit reason=normal` and the issue detail API returning string
  timestamps.
- Copied-board prompt measurement showed compact Verify context reducing
  `TASK-005` from 5,453 to 3,944 `o200k_base` tokens, a 27.7% reduction.

## [0.9.3] - 2026-07-04 - OpenCode telemetry and refresh hardening

### Fixed

- OpenCode 1.17 JSONL text extraction now reads `type=="text"` frames under
  `part.text`, preventing productive OpenCode turns from being counted as empty.
- OpenCode 1.17 token usage now reads `step_finish.part.tokens`, restoring
  token telemetry for `max_total_tokens` and operator visibility.
- File-tracker ticket parsing auto-heals the observed shallow `updated_at`
  indentation issue before normal tracker writes serialize canonical YAML.

## [0.9.2] - 2026-07-04 - OpenCode empty-turn preview

### Fixed

- OpenCode completed-turn events now emit the response under the `message`
  preview key so the orchestrator can reset the empty-response-loop guard.

## [0.9.1] - 2026-07-03 - Landing page version sync

### Changed

- Synchronized source version and public landing-page badge after the v0.9.1
  release closeout.

## [0.9.0] — 4-stage pipeline simplification

### Changed

- Default active workflow is now `Todo -> In Progress -> Verify -> Learn`,
  followed by `Human Review` and `Done`. `Plan`, `Critic`, `Review`, and `QA`
  are folded into In Progress and Verify contracts.
- Verify owns Review, QA, and Merge Gate, and is never skipped. Learn is
  lightweight wiki write-back and can be operator-skipped to Human Review with
  a `## Learn Skipped` audit note.
- Root examples now keep only the current workflow files; demo/smoke/Jira
  workflow examples and demo boards live under `examples/`.

### Added

- TUI stage badges (`[n/4]`), multiline ticket creation, focused-ticket edit
  modal (`e`), and Learn skip hotkey (`S`).
- Web and API Learn skip controls:
  `POST /api/v1/{identifier}/skip-learn` and
  `POST /api/v1/issues/{identifier}/skip-learn`.

### Removed

- Dedicated skills UI surfaces (web route/nav/forms and TUI create/detail
  fields). `skills:` frontmatter, `Issue.skills`, and prompt injection remain
  for power users and backward compatibility.

### Breaking

- Existing custom `WORKFLOW.md` files that still name `Explore`, `Plan`,
  `Critic`, `Review`, or `QA` must migrate their active states and prompt stage
  mapping to the 4-stage layout before using the new default prompts.

## [0.8.0] — built-in web Kanban app (multica/Archon-style revamp)

The orchestrator port now serves a full web app: the board is no longer
read-only. Everything the operator previously edited by hand in WORKFLOW.md —
kanban columns, per-stage prompts, branch policy — is editable from the
browser (comment-preservingly, via ruamel.yaml round-trip), and issues are
registered from the UI or the TUI with skills attached per ticket.

### Added

- **Web app at `/`** (`symphony --port 9999`, `symphony service start`):
  Linear-style board with issue create/edit/delete, drag-and-drop state
  moves, per-column "+", live run badges (turn count, tokens, pause/resume).
  Vanilla HTML/CSS/JS served by the existing aiohttp server — no build step,
  no CDN, no signup.
- **User-editable workflow**: add / delete / rename / reorder kanban columns
  and edit each column's stage prompt from the Workflow page. Writes go back
  into WORKFLOW.md frontmatter with comments preserved; tickets in renamed or
  removed columns migrate automatically; a column with a running worker
  refuses edits (409).
- **Skills**: drop `skills/<name>/SKILL.md` next to WORKFLOW.md, attach
  skills to a ticket (web modal, TUI form, or `skills:` frontmatter), and the
  orchestrator appends the skill bodies to that ticket's first-turn prompt.
- **Stats page** + TUI `s` screen: tokens/day, done/day, per-column dwell
  time, per-agent totals, avg cycle time — aggregated from a new append-only
  `.symphony/stats.jsonl` event store (turns, transitions, run outcomes).
- **TUI `n`**: register a new ticket without leaving the terminal.
- REST API: `GET/POST/PATCH/DELETE /api/v1/issues*`, `GET /api/v1/board`,
  `PUT /api/v1/workflow/states`, `GET/PUT /api/v1/workflow/prompts/<state>`,
  `PUT /api/v1/workflow/branch-policy`, `GET /api/v1/skills`,
  `GET /api/v1/stats`, `GET /api/v1/git/branches`.

### Changed

- `GET /` serves the web app instead of a plain-text hint (API endpoints are
  unchanged). Mutating endpoints require `Content-Type: application/json`
  and a loopback Host header (cross-origin form / DNS-rebinding guard).
- `tools/board-viewer/` is deprecated in place; the built-in app replaces it.
- New dependency: `ruamel.yaml` (comment-preserving WORKFLOW.md edits).

## [Unreleased] — supergoal verification discipline + hardening

Ports the `supergoal` skill's verification discipline into Symphony's per-stage
pipeline, then hardens it so every promised mechanism is honest and enforced: a
new independent Critic stage turns the prose spec into failing tests before
review, content-checking gates assert artefacts are real (not just that headings
are present), and a difficulty gate keeps the heavy loop off trivial tickets.
Backward-compatible: the new gates no-op on existing non-bug / clean-Critic
boards, and a ticket without `## Difficulty` keeps the full loop.

### Added

- **Critic stage** between In Progress and Review (data-driven `active_states`
  addition, no `core.py` rewrite): a fresh-context agent enumerates required
  behaviors the builder's tests miss and writes one failing test per gap,
  recording them in a durable `docs/<id>/critic/surfaced-requirements.md` ledger
  before rewinding to In Progress for the fix.
- **Content-checking contract gates** (`evaluate_contract`): cited evidence
  paths in the QA scorecard / Review security audit must exist on disk; a `fail`
  security-audit row cannot coexist with a clean Review; a bug ticket's populated
  `reproduce/` dir must be closed by `qa/repro-after.log`; a Critic rewind must
  persist its ledger file. Missing artefacts rewind the producing stage instead
  of passing on a hollow heading.
- **Difficulty gate**: Plan declares `## Difficulty: trivial|standard|complex`;
  a `trivial` non-bug ticket with no runtime change skips Critic and QA, and the
  route is always recorded in `## Pipeline Route` (never silent).
- **Skill contract tests** (`tests/skills/`) and a **scenario-proof suite**
  (`tests/test_supergoal_hardening_loop.py`) that walks a bug ticket through the
  full critic → fix → review → QA loop, asserting each gate fires and clears.

### Changed

- **Bug reproduction is language-agnostic**: the reproduction authored at Todo
  and re-run at QA is the project's own test framework (pytest / go test / …),
  not a hardcoded Playwright `.spec.ts`.
- **QA confirms a runner exists before trusting it**: before running playwright /
  a boot command / a non-Python runner, QA verifies availability and fails with
  `## QA Failure` rather than recording a scorecard pass it never executed.

### Fixed

- **Critic bounded-loop claim corrected**: the prompt and spec said the 3-cycle
  cap was "enforced by S2's gate counting `## Critic` rewinds" — it was not (the
  gate counts nothing). The Critic now counts prior `## Surfaced Requirements`
  cycles itself and escalates to `Blocked` with `## Critic Cap` on the 3rd, with
  the shared `cfg.agent.max_attempts` budget as the hard backstop.

### Added

- **`symphony --version`** prints the package version and exits 0. Previously
  the flag errored with `unrecognized arguments: --version`. (`cli/main.py`,
  regression test in `test_cli_main_routing.py`.)

### Fixed

- **Symphony OneShot bootstrap test failed on CI** (`test_symphony_oneshot_bootstrap.py::test_bootstrap_creates_vault_skeleton`).
  The hermetic run asserts `bootstrap.sh` exits 0, but the script's final
  `symphony doctor` preflight fails (exit 1) when no agent CLI is on `$PATH` —
  and CI installs `symphony` without `claude`, so doctor's `check_agent_cli`
  failed and bootstrap exited 1. The test now honors its own "no agent CLI"
  contract by putting a never-invoked `claude` stub on `$PATH` (doctor only
  `shutil.which`-es it). bootstrap.sh's strict preflight is intentionally
  unchanged. Also hardened the test's bash invocation for the Windows
  maintainer box (`resolve_bash()` to dodge the WSL launcher, `as_posix()` to
  stop bash collapsing a backslash script path) — both no-ops on Linux/CI.
- **Launcher install hint** in `tui-open.sh` suggested `python3.11 -m venv`,
  which fails `command not found` on hosts that ship only the declared 3.12+
  floor (pyproject, CI matrix, README badge, and bootstrapping docs are all
  3.12). Changed the hint to `python3`.
- **README key-binding docs** were missing the load-bearing `c` (confirm a
  Done-gated card) and `P` (pause/resume) bindings in both the key table and
  the footer mockup — `c` clears the manual Done gate, so its absence could
  strand a reader. The `doctor` "five first-run failures" summary also
  enumerated only four. Both now match the running app (`tui/app.py`) and the
  doctor's own later enumeration.
- **Double-dispatch race on per-attempt `max_turns` exhaustion** (found by the
  live run-path smoke, root-caused in
  `docs/improvements/dispatch-double-dispatch-race-2026-06-28.md`). The
  worker-exit handler popped the worker from `_running` and then `await`ed
  auto-commit and the async `budget_exhausted_state` persist; a poll tick
  firing in that window pruned the in-tick `_claimed` lock and re-dispatched
  the still-active ticket, producing a second worker and a `git index.lock`
  collision. `_on_worker_exit` now holds the ticket in a
  `_terminal_persist_pending` in-flight set for its whole duration, so it stays
  ineligible until its terminal state is durably written. The live smoke now
  shows exactly one dispatch and one worker exit. (`orchestrator/core.py`,
  regression test in `test_orchestrator_dispatch.py`.)

### Changed

- **Auto-archive sweep throttled** to a 5-minute cadence
  (`ARCHIVE_SWEEP_INTERVAL_SEC`) instead of a full terminal-board rescan on
  every poll tick. Archivability is day-granular (`archive_after_days`,
  default 30), so the per-tick rescan was wasted work; the `archive_after_days
  <= 0` disable still wins and the first tick after start always sweeps once.
  (`orchestrator/core.py`, regression test in `test_orchestrator_archive.py`.)

## [0.7.2] — 2026-06-10 — agent-terse workflow prompts + token-budget directive

Prompt-cost release. All 18 stage templates under
`docs/symphony-prompts/{file,linear}/` rewritten as terse agent-directives:
rendered dispatch prompts shrink ~12% (282,009 → 247,942 chars across the
28 tracker × language × stage combinations) with every section anchor,
transition rule, command, table spec, and liquid branch preserved —
verified by the 51-test prompt contract suite plus an independent
old-vs-new rule audit. Human-facing artefact templates (Plain-language
header, caps table, wiki entry, As-Is→To-Be report, Human Review handoff)
are untouched.

### Added

- **Soft token-budget directive** in both `base.md` templates: when
  `agent.max_total_tokens_by_state` sets a budget, the prompt now renders
  `Token budget: keep this turn under N completion tokens` from the
  previously unused C3 `token_ema` / `token_budget` env vars. Off by
  default (budget 0). Regression tests pin both states.
- **Chore short-circuits on linear boards**: the file tracker's
  Explore/Plan/Review/Learn `chore`-label fast paths are now ported to the
  linear prompt set, so metadata-only tickets skip the full stage
  contracts there too.

### Changed

- **linear Learn delegates bulk wiki sweeping** to `symphony wiki-sweep`
  (the orchestrator already runs it every `wiki.sweep_every_n` Done
  transitions regardless of tracker kind); the per-ticket invalidated-entry
  update, beginner-block shape check, and `Wiki Conflict` flag stay in the
  prompt.
- **linear wiki entry template gains the Observability hooks block**, so
  file- and linear-authored `docs/llm-wiki/` entries share one schema.

## [0.7.0] — 2026-05-20 — TUI import hotfix + contract-validation hydration + viewer doctor

Follow-up release after v0.6.7 contract enforcement. Fixes a TUI startup
regression introduced when `symphony.tui` was split from a single module
into a package, plus a hydration gap in stage-contract validation where
the validator could read a stale in-memory ticket body and falsely mark
freshly written sections as missing. Also lands the long-tail board-viewer
doctor improvements that surfaced silent viewer-launch failures.

### Fixed

- **TUI startup crash** (`src/symphony/cli/main.py`): `symphony --tui` exited
  immediately with `ModuleNotFoundError: No module named 'symphony.cli.tui'`
  because commit `d5c4477` moved `KanbanTUI` from `src/symphony/tui.py` to the
  `src/symphony/tui/` package but the relative `from .tui import KanbanTUI`
  in `cli/main.py` still resolved against `symphony.cli`. Switched to the
  absolute `from symphony.tui import KanbanTUI`.
- **Contract validation reads fresh ticket body** (PR #51, `720ce4f`).
  Stage-contract validator now hydrates the ticket description before each
  check so producer-stage sections written in the current turn cannot be
  reported as missing on the very next dispatch.

### Added

- **File-tracker contract regression test** (PR #50, `ee6ca9d`) plus an
  xfail covering the v0.6.7 hotfix gap so the contract path is locked
  against future stale-snapshot regressions.
- **Board-viewer doctor improvements** (`fd70379`): doctor now emits a
  WARN rather than silently skipping when the board-viewer launcher is
  missing or the configured `--viewer-port` cannot be honored.

### Changed

- **Documentation** (`e58f989`): the Symphony skill now documents that
  `--viewer-port` is silently skipped when the `board-viewer/` directory
  is absent, so operators stop chasing phantom 8765 listeners.

## [0.6.7] — 2026-05-19 — Stage-contract enforcement + escalation + tool advisories

Hardens the autonomous loop for weak models (Haiku, GPT-4o-mini, open).
Stage prompts already encoded contracts (Plan must have Acceptance Tests,
Review must produce a Security Audit, QA an AC Scorecard, Done an
artefact-backed report) — strong models obey from prose, weak models
skipped silently. v0.6.7 turns those prose contracts into machine-checkable
gates: on a forward phase transition where the producing stage's required
sections are missing, the orchestrator writes the tracker state back, posts
a `## Contract Failure` note, and lets the existing rewind machinery
rebuild the backend for another attempt.

### Added

- **Stage-contract validator** (`src/symphony/orchestrator/contracts.py`)
  parses the ticket body before each forward transition. Plan must produce
  `## Plan`, `## Acceptance Tests`, `## Done Signals`; Review must produce
  a `## Security Audit` plus either `## Review` or `## Review Findings`;
  QA must produce `## QA Evidence` and `## AC Scorecard`; Done must produce
  `## As-Is -> To-Be Report` and `## Merge Status`, AND the artefact
  directories `docs/{id}/qa/` and `docs/{id}/work/` must actually contain
  files. Explore, In Progress, and Learn pass through.
- **`agent.max_retries`** config (default `3`) caps the number of auto-retries
  Symphony schedules after a worker exits with a non-normal outcome
  (timeout, crash, transient backend error). On exhaustion the orchestrator
  appends an `## Escalation` note explaining what failed and moves the
  ticket to a terminal state — preferring a configured `Needs Human` /
  `Blocked` state. `0` disables the cap (legacy: retry forever with
  backoff).
- **Allowed-tools advisory section** on every stage prompt (file/ and
  linear/, 16 files). Each stage names its expected READ/WRITE/RUN surface
  and explicitly forbids writes outside its scope. Advisory only — does not
  change tool dispatch in 0.6.7; informs the agent and reviewers.

### Changed

- Forward phase transitions now consult the producing stage's contract
  before allowing the new stage to dispatch. Existing `max_attempts` rewind
  budget applies to contract-driven rewinds.

### Verified

- `pytest -q`: 582+ tests green, 17 new contract tests, 5 new max_retries
  tests.

## [0.6.5] — 2026-05-18 — Slack notifications on state transitions

Opt-in notification channel for tracker state transitions. The orchestrator
posts one Slack message per successful state write when a webhook URL is
configured; omit the new `notifications.slack` block and the feature stays
dormant. No behavior change for existing workflows.

### Added
- **Slack notifications on tracker state transitions** — opt-in via a new
  `notifications.slack` block in `WORKFLOW.md`. When configured, every
  successful tracker write fires one message to the supplied incoming-webhook
  URL. Default behaviour with the block present is to notify on every
  transition; populate `notify_on_states` to subscribe selectively (e.g.
  `[Done, Blocked]` for PMs). Per-state `templates` override the default
  message; placeholders include `${identifier} ${title} ${prev_state}
  ${next_state} ${workflow}`. Webhook URL accepts `$VAR` indirection so
  secrets stay out of the workflow file. Network/HTTP failures are logged
  and never block the orchestrator's transition path. Module:
  `src/symphony/notifications/`. Tests: `tests/test_notifications.py`
  (19 new tests). Docs: README, `WORKFLOW.example.md`,
  `skills/using-symphony/reference/workflow-config.md`.

## [0.6.4] — 2026-05-18 — Package reorganization (no behavior change)

Pure structural cleanup of the `symphony` package on top of v0.6.3. No
signature, behavior, or CLI surface changes; the published console script,
`python -m symphony`, `python -m symphony.cli`, and
`python -m symphony.mock_codex` entry points all resolve unchanged. No
migration required.

### Changed
- **`trackers/` subpackage** — `tracker.py`, `tracker_file.py`, and
  `tracker_linear.py` consolidated into `symphony.trackers.{__init__,
  file, linear}`, mirroring the existing `backends/` layout. Callers and
  tests now import from the canonical `symphony.trackers.*` path.
- **`utils/` subpackage** — four self-contained helpers grouped under
  `symphony.utils`: `archive`, `auto_merge`, `keep_awake`, `wiki_sweep`.
  Each module is functionally unchanged; only its dotted path moved.
- **`cli/` subpackage** — `cli.py`, `board_cli.py`, and `doctor.py`
  consolidated into `symphony.cli.{main, board, doctor}`. New
  `cli/__init__.py` re-exports `main` so the
  `symphony = "symphony.cli:main"` console script keeps resolving; new
  `cli/__main__.py` preserves `python -m symphony.cli`, which
  `symphony.service` launches as a managed subprocess.
- **Package `__init__.py` module index** — top-level docstring now lists
  every module / subpackage with a one-line role, so the layout is
  greppable from the package root.

### Notes
- Test imports under `tests/` updated to the new paths;
  `tests/test_keep_awake.py` `monkeypatch.setattr` dotted-path strings
  also migrated to `symphony.utils.keep_awake.*`.
- Full pytest suite stays at 525 passed / 15 pre-existing Windows
  symlink failures — identical pre/post on the same host.
- Cross-platform: the move is purely Python module structure; no
  platform-specific code (`sys.platform`, `_shell`, symlink logic) was
  touched.

## [0.6.3] — 2026-05-18 — Monorepo bootstrap, multi-orchestrator board, bigger turn budget

Quality-of-life release on top of v0.6.2. Three feature additions (none
breaking), two reliability fixes, and a README rewrite aimed at first-time
visitors. No signature changes; no migration required.

### Added
- **`symphony-monorepo` bootstrap skill** — generic recipe that wires
  Symphony into an existing monorepo (or polyrepo): each ticket lands in
  an isolated git worktree under `.symphony/workspaces/<id>`, the
  upstream 7-stage prompts are installed, and Claude Code permissions
  are wired bidirectionally between host repo and worktree. Companion
  to `using-symphony` (operator-level) and `symphony-oneshot`
  (one-shot dispatch). Files: `skills/symphony-monorepo/SKILL.md`,
  `references/workflow-template.md`, `scripts/setup-monorepo.sh`.
- **board-viewer aggregates multiple orchestrators** —
  `tools/board-viewer/` now accepts multiple `--symphony URL` values
  and surfaces them as named, per-source toggleable pills in the UI.
  CLI accepts `--symphony URL1,URL2` (auto names `s1`, `s2`) or
  `--symphony api=URL1,web=URL2` (explicit names); same syntax works
  via `SYMPHONY_BASE`. New endpoints:
  `GET /api/symphony/sources`, source-routed
  `GET /api/symphony/<id>` and pause/resume, `POST /api/symphony/refresh`
  fan-out. Single-URL invocations remain backward compatible; toggle
  state persists in `localStorage` under `boardViewer.sourceToggles`.

### Changed
- **Default per-ticket turn budget raised 5×** — `DEFAULT_MAX_TURNS`
  20 → 100, `DEFAULT_MAX_TOTAL_TURNS` 60 → 200. Long QA / Learn phases
  on sandboxed build tooling were budget-exhausting before the worker
  could resolve transient blockers. `WORKFLOW.md`, `WORKFLOW.example.md`,
  and `WORKFLOW.file.example.md` updated so freshly copied workflows
  inherit the new ceiling; `WORKFLOW.smoke.md` keeps its intentionally
  small budget (3 / 6) to exercise the budget-exhaustion path in tests.

### Fixed
- **Claude `--add-dir` widened to project root** — narrowing
  `--add-dir` to a sub-path (e.g. `./kanban`) let state transitions
  write back but silently blocked Read/Grep into sibling directories
  under the project root, so Claude agents could not verify
  cross-service code and stalled in `Blocked`. The flag now points at
  `$SYMPHONY_WORKFLOW_DIR` so reads (any sibling) and writes (kanban)
  both work. Codex (workspace-write), Gemini (`--skip-trust`), and Pi
  already permit reads outside cwd; an inline comment documents that
  to prevent the same narrowing in other backends.
- **Backend session boundaries** — session cleanup at backend
  teardown no longer leaks across orchestrator restarts.

### Docs
- **README intro restructured for first-time visitors** (#40, #41):
  punchier tagline, "Stop juggling AI coding CLIs" value-prop paragraph,
  CTA to the 60-second mock demo, "Why Symphony?" with six concrete
  benefits, "Who is this for?" self-identification block, GitHub stars
  badge for social proof, and TUI screenshot moved above the fold with
  a one-line caption. No structural changes below the fold.
- **TUI + JSON API combined startup recipe documented** —
  this fork's TUI is the operator view and the JSON API is the
  programmatic view. README now shows the recommended single-process
  launch (`--tui` + `server.port` pinned in `WORKFLOW.md`) plus
  override notes (`--port`, dropping the `server` block).
- **Removed inaccurate "no HTML dashboard" claim** —
  `tools/board-viewer/` is the in-browser kanban (separate from the
  upstream server-rendered `/` route, which was removed). README now
  points readers at board-viewer instead of asserting no dashboard
  exists.

### Internal
- `.gitignore` housekeeping.

## [0.6.0] — 2026-05-17 — Workflow accuracy + harness upgrade

Eleven coordinated changes across the agent prompts and the orchestrator
to make ticket outcomes more predictable and the system observable. Plan
doc lives at `docs/improvements/workflow-v0.5.2.md`.

### Added — Prompt contracts (agent-visible)
- **Plan emits a Definition of Done** (`A1`): `plan.md` now requires two
  new sections — `## Acceptance Tests` (test signatures, one per AC) and
  `## Done Signals` (observable file paths, stdout substrings, exit
  codes, HTTP shapes). `qa.md` scores them in a new `## AC Scorecard`
  sub-block; missing rows fail QA. No more guessing what "done" means.
- **Rewinds scope to flagged items** (`A2` — prompt half): when the
  orchestrator dispatches a Review → In Progress or QA → In Progress
  rewind, it injects `SYMPHONY_REWIND_SCOPE` as JSON. `in-progress.md`
  step 2 instructs the agent to touch only those files; touching others
  appends a `## Scope Expansion` rationale (non-blocking).
- **Explore emits a scored reuse inventory** (`A3`):
  `reuse-inventory.md` becomes a required output with a
  `candidate | path:line | reuse_fit | adapt_cost | notes` table.
  `plan.md` gains a `reuse_from` column and requires `## Plan Rationale`
  when any `reuse_fit >= 0.7` row is rejected.
- **Review emits a dedicated Security Audit** (`B2`): `review.md` adds a
  `## Security Audit` section with exactly 7 rows (secrets,
  input-validation, sql-injection, xss, csrf, authz, rate-limit). Any
  `fail` row auto-promotes to a CRITICAL row in `## Review Findings`
  and triggers rewind.
- **Wiki entries record observability hooks** (`B3`): the Learn wiki
  template adds an `**Observability hooks:**` block under
  `## Technical Reference` (log / metric / trace anchors with
  `path:line`). `plan.md` candidate table gains an `observability`
  column so Plan declares intent up front. Both KO and EN templates
  updated.

### Added — Harness (orchestrator / hooks)
- **System-side conflict pre-check at dispatch** (`C1`): the orchestrator
  parses `## Touched Files` from every in-flight ticket and refuses to
  dispatch a candidate whose touched paths overlap. The candidate is
  moved to `Blocked` with an auto-generated `## Conflict` section. The
  agent-side conflict pre-check in `in-progress.md` step 1 is removed.
- **Adaptive token budget feedback to prompts** (`C3`): per-state EMA
  (alpha 0.3) of completion tokens persists to `.symphony/token_ema.json`
  across orchestrator restarts. Dispatch injects `SYMPHONY_TOKEN_EMA`
  and `SYMPHONY_TOKEN_BUDGET` so the agent sees both the historical
  per-stage cost and the hard cap.
- **TDD enforcement marker in after_run hook** (`B1`): the per-turn
  commit subject is prefixed `[no-test]` when the diff has production
  code outside `tests/ docs/ kanban/ .symphony/` and no paired test
  file. `review.md` step 3 scans for the marker and promotes each
  occurrence to a HIGH severity finding (docs-only turns exempted).
- **Backend stall-progress predicate is abstract** (`C2`): the meta-event
  filter that fixed OLV-002 lives on a `BaseAgentBackend` superclass via
  `is_progress_event(event)`. `ClaudeCodeBackend` and
  `CodexAppServerBackend` override; `pi.py` and `gemini.py` inherit the
  conservative default (every event counts as progress). New backends
  opt in by overriding one method.
- **`after_create` hook extracted to a versioned script** (`C4`): the
  200-line bash heredoc previously embedded in WORKFLOW.md is now
  `scripts/symphony-setup-worktree.sh`. The hook is a one-liner that
  invokes the script. Cross-platform behaviour (Git Bash on Windows
  with `mklink /J`) preserved.
- **`symphony wiki-sweep` CLI + scheduled invocation** (`C5`): new
  subcommand scans `docs/llm-wiki/` for duplicate slugs, INDEX↔file
  orphans, missing files, and entries older than 90 days. Exit code is
  non-zero on duplicate / orphan / missing-file findings. The
  orchestrator runs the sweep after every Nth `Done` transition
  (default `wiki.sweep_every_n: 10`; set 0 to disable). The Learn
  prompt is reduced to per-ticket integrity updates plus the sweep
  delegation, saving model tokens.

### Fixed
- `after_run` hook now uses a POSIX-portable `IFS=` trick
  (`NL=$(printf '\nx'); NL=${NL%x}`) so the YAML literal block parses
  cleanly. The previous `IFS='\n'` literal terminated the YAML block
  prematurely on the bare closing quote at column 0.

### Note
- This release ships the eleven items as one logical bundle because the
  prompt contracts depend on the harness-injected env vars (`A2` ↔
  orchestrator, `B1` prompt half ↔ `after_run` marker). Partial rollout
  is not supported — upgrade `WORKFLOW.md` and the package together.

## [Pre-0.6.0]

### Added
- macOS host wake-lock: while the orchestrator (and the `service start`
  detached child) is running, Symphony spawns `caffeinate -d -i -w <pid>`
  so the display and system stay awake, preventing the screen lock from
  cancelling long unattended runs. Disable with `--no-keep-awake` or set
  `system.keep_awake: false` in WORKFLOW.md. Non-macOS hosts are no-ops.

## [0.4.8] — 2026-05-16

Workspace review handoff hardening for long-running file-board workflows.
This release is a drop-in patch over 0.4.7 and is aimed at preventing
Review loops caused by stale workspace symlinks, missing per-turn commits,
and token-budget exhaustion edge cases.

### Added
- `workspace.reuse_policy` with `preserve` (default) and `refresh`.
  File-board workflows can opt into `refresh` so reused workspaces rerun
  `after_create` and repair host-backed `kanban/`, `docs/`, or `prompt/`
  links before the next agent turn.
- Example hooks now support an agent-authored commit subject via
  `.symphony/commit-message.txt`; per-turn snapshots are still stored as
  `wip:` commits so Review can inspect the latest implementation diff.

### Changed
- Successful turns now run `after_run` immediately, not only when the
  whole attempt exits. This guarantees a Review turn can see the preceding
  In Progress wip commit with `git show`.
- File-board examples hide host-owned symlink/junction roots from the
  workspace branch using `skip-worktree`, worktree-local `info/exclude`,
  and `git add -A` pathspec excludes.
- Default token budgets now use a 10M global cap with a larger 100M
  `In Progress` budget, matching the agentic workflow profile observed in
  the dograh-demo run.
- Review prompts stay concise but clarify that docs are reviewable
  deliverables; only root symlink/junction metadata for host-backed
  workflow plumbing should be ignored.

### Fixed
- Prevents Review from misclassifying host-board/docs/prompt symlink roots
  as product-code deletes or `120000` symlink blobs.
- Token-budget exhaustion no longer needs to be treated as a failure when
  the worker already advanced to the next stage.

## [0.4.7] — 2026-05-16

Board viewer gains runtime controls and the codex backend stops burning
turns on workspace-write sandbox symlink traps.

### Added
- `tools/board-viewer/` now mirrors the TUI's most-used runtime controls:
  per-card **Pause / Resume** buttons on running tickets, and the header
  refresh button now triggers an `orchestrator refresh` (poll +
  reconcile) before the local poll. `server.py` whitelists three POST
  proxies under `/api/symphony/*` (`refresh`, `<id>/pause`,
  `<id>/resume`); everything else stays read-only. Paused cards get a
  yellow-toned badge and stopped pulse dot for at-a-glance status.

### Fixed
- `codex` backend auto-injects `sandbox_workspace_write.writable_roots`
  for symlinked host paths so workers no longer burn turns repeating
  "쓰기 불가" when `hooks.after_create` symlinks repo dirs (kanban/,
  docs/, …) into the workspace. Targets are also exported as
  `SYMPHONY_CODEX_WRITABLE_ROOTS` so wrapper scripts can forward the
  same override. No-op when the sandbox is not `workspace-write`.

## [0.4.3] — 2026-05-16

Managed background service launch, stricter default run serialization, and
cleaner repo-local learning docs. Drop-in over 0.4.2; existing `symphony
./WORKFLOW.md --port ...` and `symphony tui` flows still work, while normal
headless operation can now use `symphony service ...`.

### Added
- `agent.auto_merge_capture_untracked` (default `[]`, **opt-in**) — list
  of host-repo paths whose currently-untracked files are folded into the
  same auto-merge commit on Done. Closes the gap where
  `hooks.after_create` installs host directories as **symlinks inside
  the agent workspace**: the agent writes files via the symlink (so
  they land in the host repo's real directories), but the
  `symphony/<ID>` branch only sees the symlink as a single blob, so the
  branch diff never reports the agent's per-ticket notes (e.g.
  `docs/<ID>/*`). Listing those host paths here lets auto-merge
  `git add` them alongside the branch-side checkout, producing one
  cohesive commit. Distinct from `auto_merge_exclude_paths` (which
  controls what is *skipped from branch-side checkout*); capture is
  *additive on the host side*. Default empty so existing deployments
  are unchanged.
- `symphony service start/status/stop/restart/logs` — a managed background
  launcher for the orchestrator plus `tools/board-viewer/`, with per-workflow
  run-state under `.symphony/run/<workflow-hash>.json`. It refuses to start the
  same `WORKFLOW.md` a second time on another port, runs without shell/batch
  wrappers, and uses platform-aware process cleanup for macOS/Linux/Windows.

### Changed
- Default Symphony runs now serialize by default with
  `agent.max_concurrent: 1`, keeping dispatch FIFO unless the operator
  explicitly raises the concurrency cap.
- The llm-wiki reference set now lives under `docs/llm-wiki/`, matching the
  prompt and gitignore guidance used by Explore/Learn stages.

## [0.4.2] — 2026-05-16

Builtin auto-merge on Done, board-viewer launcher integration, and a
StreamReader limit fix that unblocks long agent turns. Drop-in over
0.4.1; auto-merge is ON by default but safe-by-default (dirty host or
missing branch skips silently).

### Added
- `agent.auto_merge_on_done` (default **true**) — when a ticket reaches
  Done, fold the `symphony/<ID>` branch into the host repo's
  development branch as one selective-apply commit. Paths in
  `agent.auto_merge_exclude_paths` (default
  `kanban/llm-wiki/prompt/docs`) are stripped first so the workspace
  symlinks that `hooks.after_create` installs never reach the host
  repo. `agent.auto_merge_target_branch` defaults to `""` = the host
  repo's currently-checked-out branch.
- `tui-open.sh` now auto-starts `tools/board-viewer/server.py` in the
  background at `http://127.0.0.1:8765/` when the workflow ships one.
  Skipped silently if the port is held or the file is absent.
- `src/symphony/auto_merge.py` — new module owning the selective-apply
  flow with five outcome events: `auto_merge_completed`,
  `auto_merge_skipped_dirty`, `auto_merge_skipped_missing_branch`,
  `auto_merge_nothing_to_apply`, `auto_merge_failed`.
- Learn-stage wiki entries now open with a **beginner explainer block**
  (`## 감 잡기` in Korean, `## Getting the Feel` in English) ahead of
  the existing Summary / Invariants / Files / Decision-log technical
  reference. Same file, two layered audiences — PMs and 기획자 land on
  the beginner block, engineers scroll past it. The prompt enforces the
  tutor shape: 3-5 step core flow, exactly five plain-language terms,
  one realistic scenario, one-sentence takeaway, and a "ready to go
  deeper" pointer. Branch is `{% if language == 'ko' %}` keyed on the
  same `{{ language }}` env that drives the chrome/doc directive, so
  switching the TUI language (or `SYMPHONY_LANG`) flips both halves of
  the wiki entry in lockstep. Both `docs/symphony-prompts/file/stages/learn.md`
  and `docs/symphony-prompts/linear/stages/learn.md` updated.

### Fixed
- `claude_code`, `pi`, `gemini` backends now spawn their subprocess with
  `limit=10 MiB` on the asyncio `StreamReader`, matching `codex`. The
  asyncio default of 64 KiB was raising `LimitOverrunError` on
  stream-json events whose `result`/tool-use payload exceeded that on a
  single line, dropping the rest of the turn into `stalled_session`
  recovery. Caught on a live IB-002 turn before the fix landed.
- `_on_worker_exit(reason="normal")` at Done now fires
  `auto_merge_on_done`, `after_done` user hook, and `workspace.remove`
  inline. Previously those only ran on the reconcile-driven termination
  path; a worker that finished cleanly at Done was popped from
  `_running` *before* the next reconcile cycle, so the entire
  terminal-state post-processing was silently skipped — the host repo
  never saw the auto-merge commit and the workspace lingered.
  `_reconcile_running` remains the safety net for stale workers.

### Tests
- `tests/test_auto_merge.py` — five scenarios: happy path,
  dirty host, missing branch, implicit current-branch target,
  all-paths-excluded. Full suite: **353 passed, 6 skipped**.

## [0.4.1] — 2026-05-16

Browser HUD for headless operators, plus i18n cleanup for the
prompt-base templates. No breaking changes — drop-in over 0.4.0.

### Added
- `tools/board-viewer/` — vanilla HTML/CSS/JS + Python-stdlib browser
  HUD for Symphony kanban boards. Read-only, runs alongside the
  headless orchestrator and the textual TUI without conflict. Two
  modes: **live** proxies `/api/v1/state` every 5s (setTimeout-recursive
  polling avoids overlapping cycles); **file-only** scans `kanban/*.md`
  when Symphony is down.
- `tools/board-viewer/board-viewer-open.sh` — launcher with kanban
  auto-discovery (`$CWD/kanban` → env → CLI flag) and python3.11+
  selection.
- Progress mirror (`WORKFLOW-PROGRESS.md`) now advertises a clickable
  board-viewer URL header. Defaults to `http://127.0.0.1:8765/`
  (board-viewer-open.sh default port); override with the
  `SYMPHONY_BOARD_URL` env var; disable with `SYMPHONY_BOARD_URL=""`.

### Changed
- `docs/symphony-prompts/{file,linear}/base.md` now branch the
  "Audience & writing style" block on `{{ language }}`. English operators
  (the default) see a `**What** / **Why** / **As-Is → To-Be**` Plain-language
  header; Korean operators (`tui.language: ko`, `SYMPHONY_LANG=ko`, or `L`
  hotkey) keep the existing `**무엇** / **왜** / **As-Is → To-Be**` block.
  The doc-language preamble and the prompt body are now consistent under
  both defaults.

### Docs
- Add `CHANGELOG.md` mirroring GitHub Releases through v0.4.0.
- `llm-wiki/agent-observability.md`: drop the dated "fixed in 0.3.3"
  parenthetical from the stall-signature table — the behavior is now
  baseline, not historical.

### Security
- Board viewer sanitizes ticket markdown via DOMPurify before insertion
  (kanban .md is agent-authored, prompt-injection surface). `<script>`,
  `<iframe>`, `on*=` handlers are explicitly forbidden.
- Path-traversal defense on both static and kanban routes in
  `tools/board-viewer/server.py`.

## [0.4.0] — 2026-05-16

First release with day-one Windows support, a lifecycle hook surface, and
a per-ticket git workspace model. The 7-stage workflow becomes the
supported default; headless runs leave a human-readable progress trail.

### Added
- Cross-platform Windows support: dispatch pipeline, hooks, and host-board
  sync via directory junction + `claude --add-dir`.
- `after_done` lifecycle hook plus `qa.boot` and `qa.regression_budget`
  config keys, all surfaced in `WORKFLOW.md`.
- Per-ticket workspaces default to a git worktree of the host repo,
  with a one-commit-per-ticket guarantee on the issue branch.
- `WORKFLOW-PROGRESS.md` mirror so headless runs can be tailed without
  attaching a TUI.
- `docs/skills/` cross-platform compatibility reference.

### Changed
- 7-stage prompts (Todo / Explore / In Progress / Review / QA / Learn /
  Done) become the supported default.
- Review now rewinds to In Progress on MEDIUM findings, not just
  HIGH/CRITICAL.
- Operator skills (`using-symphony`, `symphony-oneshot`) resolve from
  any working directory.

### Fixed
- Windows hook execution and test isolation.
- `claude` backend: success-result parsing; continuation turn budget cap.
- Hook failure output surfaced instead of swallowed.
- Operator pause (Shift+P) persists across worker exits.
- Auto-commit and `basesha` scoped to the workspace.

## [0.3.4] — 2026-05-11

Turns the ticket-order rule from an implementation detail into an
operator-visible contract.

### Added
- Shift+P TUI hotkey to pause/resume the focused running worker.
- Stage-specific prompt loading from `docs/symphony-prompts/{tracker}/stages/*.md`.

### Fixed
- Dispatch sorts candidates by stable ticket registration suffix with
  `created_at` fallback, so newer or higher-priority work cannot jump
  ahead of earlier tickets in single-slot workflows.
- Hydrates blocker state from current ticket files so stale `blocked_by`
  metadata cannot let dependent work outrun its blocker.

## [0.3.3] — 2026-05-11

Safer long-running workflows: phase isolation, stricter retry/slot
handling, clearer TUI state, stronger stall detection.

### Added
- Workspace snapshot at Done: `agent.auto_commit_on_done` (default `true`)
  produces a single commit named `<identifier>: <title>`.
- Review → In Progress rewind for CRITICAL/HIGH review findings, parallel
  to the existing QA → In Progress failure loop.
- `is_rewind` prompt context so agents can distinguish a workflow rewind
  from a normal retry.

### Changed
- Rebuilds the agent backend on each phase transition so stages do not
  silently inherit prior conversation context.

### Fixed
- Worker cleanup races that could leak a running slot or let a stale
  done callback eject a live replacement worker.
- Stall timer no longer reset by claude API tool-result echoes or
  keepalive-style events; only real model progress advances the clock.
- Retry-pending tickets count against capacity, preventing a sibling
  ticket from starting during the continuation retry delay window.
- macOS/Textual child-process hangs reduced via a safer process-wait
  helper.

## [0.3.0] — 2026-05-10 — TUI quality of life + auto-archive

First release after the Textual TUI rewrite.

### Added
- Textual rewrite of the Kanban board: real focus, modals, mouse handling.
- Dense defaults: compact one-line cards, lane pagination (`t`/`T`),
  always-on detail pane.
- `L` hotkey: toggle TUI chrome language (en ↔ ko) without restart.
- `a` hotkey: archive the focused terminal-state card.
- `[` / `]` hotkey: park focus inside the detail pane.
- Auto-archive sweep: terminal-state tickets older than
  `tracker.archive_after_days` (default 30, `0` disables) move to
  `tracker.archive_state` on each poll tick. Works on Linear and file
  trackers.
- `TrackerClient.update_state(issue, target_state)` — first mutation
  method on the tracker protocol.
- Doctor: pi-auth preflight check.
- Plain-Korean header policy with stage-specific length caps (overflow
  → `docs/<id>/<stage>/details.md`).

### Changed
- File-tracker workspaces symlink `kanban/` `docs/` `llm-wiki/` back to
  the host so agent edits land in the right place.
- `SYMPHONY_WORKFLOW_DIR` env var injected into hooks so cloned workspaces
  can resolve back to the host repo.

## [0.1.0] — 2026-05-09 — symphony-multi-agent

First public release of the multi-agent fork.

### Added
- Four agent backends behind one Protocol: `agent.kind: codex | claude |
  gemini | pi`.
- Seven-stage production pipeline baked into the default prompt: Todo →
  Explore → In Progress → Review → QA → Learn → Done.
- CLI Kanban TUI on `rich`: live status indicators, per-stage column
  descriptions, per-card token breakdown, EN/KO chrome via `SYMPHONY_LANG`.
- File-based tracker — no Linear or external board required.
- Mock backend (`python -m symphony.mock_codex`) for zero-install demos.
- Per-state concurrency caps, `$VAR`/`~` expansion, dynamic WORKFLOW
  reload, structured stderr logging, `symphony doctor`.

[Unreleased]: https://github.com/cskwork/oh-my-symphony/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.11.0
[0.10.1]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.10.1
[0.10.0]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.10.0
[0.9.3]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.9.3
[0.9.2]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.9.2
[0.9.1]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.9.1
[0.9.0]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.9.0
[0.4.8]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.4.8
[0.4.7]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.4.7
[0.4.3]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.4.3
[0.4.2]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.4.2
[0.4.1]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.4.1
[0.4.0]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.4.0
[0.3.4]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.3.4
[0.3.3]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.3.3
[0.3.0]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.3.0
[0.1.0]: https://github.com/cskwork/oh-my-symphony/releases/tag/v0.1.0
