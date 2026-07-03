# 2026-07-03 - OpenCode backend support

## Goal

Add OpenCode as a first-class Symphony backend so operators can set
`agent.kind: opencode` globally or on individual file-board tickets.

## Decisions

### 1. Use `opencode run` for the first backend

The backend will spawn `opencode run --format json --auto` once per turn,
append the Symphony prompt as the documented `message` argument, and use
`--session <id>` for continuation after OpenCode reports a session id and
`resume_across_turns` is enabled.

- Rejected: managing a persistent `opencode serve` process. It may reduce
  cold-start cost later, but it adds port/password/lifecycle failure modes to
  the first integration.
- Rejected: implementing the ACP protocol now. It is a larger protocol layer
  than needed to make OpenCode usable from Symphony's existing backend shape.

### 2. Keep token telemetry best-effort

OpenCode JSON output is parsed for known token keys when present. Missing or
unknown usage fields leave the standard buckets at zero instead of failing a
turn that otherwise completed.

- Rejected: requiring one exact JSON schema before accepting the backend. The
  documented CLI contract is `--format json` raw events, so tolerant parsing is
  safer across OpenCode versions.

### 3. Keep TUI issue descriptions plain text

Full-suite verification exposed that Textual raises when tree-sitter is
installed but its Markdown language package is absent. The new/edit issue
dialogs now use plain `TextArea` fields instead of requesting Markdown syntax
highlighting, because the field's purpose is ticket text entry and a missing
optional highlighter should not block the TUI.

- Rejected: adding a hard tree-sitter Markdown dependency. It would make a
  cosmetic editor feature part of the runtime install contract.

# 2026-07-03 - Operator Trust Program spec

## Goal

Define the next improvement program for Symphony as a product-facing trust
layer, not only a reliability backlog.

## Decisions

### 1. Combine options 1-3 into one phased spec

The selected scope covers operator trust signals, reliability backbone
completion, and onboarding polish. Keeping them in one spec makes the outcome
testable end to end: a healthy system must expose health, explain stuck work,
avoid leaked backend processes, and teach new operators how to verify the same
surfaces.

- Rejected: three separate specs. They would duplicate decisions around health,
  run history, and doctor checks.
- Rejected: extending only the reliability plan. That would keep the work as an
  engineering checklist and under-specify the user-facing trust surfaces.

### 2. Keep Symphony single-node and file-first

The spec preserves Markdown tickets as the human source of truth and the
existing SQLite run registry as the runtime ledger. Health, attention, and run
history are additive surfaces over the current architecture.

- Rejected: external queues, distributed locks, or managed observability for
  this program. They do not match the current product shape or failure budget.

### 3. Re-audit backend lifecycle before editing

Recent commits may already satisfy parts of the older R2/R7 handoff. The spec
therefore requires implementation to prove current behavior first, then change
only the failing gaps.

- Rejected: blindly reimplementing the older handoff. The live branch is ahead
  of that document, so direct replay risks churn and duplicate fixes.

# 2026-07-03 - Operator Trust Program audit and spec alignment

## Goal

Verify the day-old Operator Trust Program spec (`8838bfa`) against the code
actually on `dev`, update the spec to match reality, and leave a detailed
implementation plan for the remaining gaps.

## Decisions

### 1. Record the audit as a spec artifact, not a chat summary

`docs/spec/operator-trust-program/audit.md` completes spec task 1.1 with
file/test evidence per requirement. Verification: focused suites (48 passed)
and the full suite (942 passed, 2 skipped) are green on `dev`, confirming
`1818d60` superseded the older `feat/reliability-hardening` WIP and its five
red backend tests.

- Rejected: re-implementing from the 2026-07-02 reliability handoff. The
  audit shows backend lifecycle (R4) is fully landed; replaying the handoff
  would churn proven code.

### 2. Align spec data models with the landed implementation

The Health Snapshot ships as `ok`/`degraded` with `tick.*`/`run_registry.*`
sub-objects, and the Attention Signal ships as `{kind, label, message}`.
design.md now documents those shapes; `starting`, `workflow_path`,
`severity`, and `due_at` remain as additive planned fields.

- Rejected: renaming implemented fields to the spec's original names
  (`healthy`, `consecutive_tick_errors`, `severity`). That breaks
  `/api/v1/health` and attention consumers for cosmetic gain and violates
  the spec's own additive-only compatibility NFR.

### 3. Reuse `status` as the run-history terminal cause

The `runs` table has no `error` column; terminal causes such as
`force_ejected_zombie` already live in `status`. The Run History Row exposes
`status` as-is instead of adding a schema migration.

- Rejected: adding an `error` column. A migration plus dual-write brings new
  failure modes for information the ledger already stores.

### 4. Re-scope doctor's prompt-file check to a visibility row

Missing prompt files already fail config load with
`ConfigValidationError("prompt file not found")` before doctor's checks run,
so existence is enforced upstream. Doctor gains a row listing the resolved
prompt template paths instead.

- Rejected: a duplicate existence check inside doctor. It could never fire —
  config load raises first.

Remaining work is sequenced in
`docs/plans/2026-07-03-operator-trust-implementation.md` (slices A-K:
health `starting` status, owner-aware port messages, full attention
taxonomy, TUI rendering, run history query/API/CLI/drawer, doctor prompt
row, smoke health check, README proof path, final verification).

# 2026-07-03 - Public docs surface sync

## Goal

Bring the public documentation surfaces in line with the current `dev` branch:
five agent backends, the built-in web board, file-tracker write APIs, and the
SQLite reliability ledger.

## Decisions

### 1. Update current-facing docs, not historical plans

The README pair, landing page, architecture map, package description, and
top-level changelog are the surfaces a new operator reads first. Historical
plans under `docs/plans/` and ticket-specific notes stay as records of when
they were written.

- Rejected: sweeping old plan text. That would blur audit history and create a
  larger review surface without changing the current operator contract.

### 2. Describe reliability as single-node and local

The docs now say the run registry provides local SQLite leases and issue flags,
and that Markdown tickets remain the human source of truth. This matches the
implementation without implying a distributed queue or restartable worker
reattach.

- Rejected: marketing it as generic crash recovery. The current code persists
  claims and retry state, but an in-process worker is still lost on a hard
  crash.

### 3. Keep English, Korean, and landing-page copy in sync

The Korean README and landing-page i18n strings were updated in the same pass
as the English README so backend counts, endpoint names, and unsupported
surfaces do not diverge.

- Rejected: only updating the English README. The repo already presents Korean
  as a first-class operator surface.

# 2026-07-03 - README navigation and Pages deploy ownership

## Goal

Make the long GitHub README easier to navigate and replace the opaque legacy
Pages dynamic deploy path with a repo-owned workflow that can be rerun and
debugged directly.

## Decisions

### 1. Add a compact top-level table of contents

The README now links to the major operator sections before the long feature
and setup walkthrough. The Korean README mirrors the same navigation so the two
entry docs stay structurally aligned.

- Rejected: a full heading-by-heading table. The README is already long, and a
  dense table would push the useful content farther down.

### 2. Use an explicit static Pages workflow

The generated `pages-build-deployment` job uploaded the `docs/` artifact, then
failed in the deploy step with only `Deployment failed, try again later.` A
checked-in workflow keeps the same `docs/` source but makes permissions,
actions versions, concurrency, and reruns visible in the repo.

- Rejected: rerunning the failed dynamic deployment as the only fix. The rerun
  failed at the same deploy step and did not expose a repo-side error.

# 2026-07-03 - Two release-blocker fixes

## Goal

Close the concrete blockers from the four-agent rerun plan before attempting
another operator gate: force-stop child process cleanup and Verify evidence
path clarity.

## Decisions

### 1. Persist backend agent PIDs in the run registry

Backends already emit `agent_pid`, and the orchestrator already uses the live
value for force-eject. Persisting that PID on registry heartbeat gives
`symphony service stop --force` a durable cleanup list after the orchestrator
process has been killed.

- Rejected: scanning the process table for agent command names. That would be
  platform-specific and could kill unrelated OpenCode/Pi sessions.
- Rejected: relying only on cooperative SIGTERM. The release rerun showed forced
  service stop can outlive the orchestrator cleanup path.

### 2. Resolve Verify evidence paths under `docs/<ticket>/`

Verify scorecard/security evidence now resolves `qa/...`, `work/...`, and
prefixed `docs/<ticket>/...` coordinates to files under the ticket docs
directory. Source anchors and prose are rejected with guidance to put those
details inside a durable evidence file.

- Rejected: accepting source file anchors as evidence cells. They prove where a
  reviewer looked, not where the durable QA artefact lives.
- Rejected: soft-warning fabricated evidence paths. Missing evidence remains a
  hard contract failure because it means the handoff is not auditable.

# 2026-07-03 - Release stop cleanup follow-up

## Goal

Close the leak that the fresh four-agent E2E exposed after the first
force-stop fix: cooperative shutdown closed the API port but left worker
subprocesses alive.

## Decisions

### 1. Reap backend subprocesses on task cancellation

OpenCode, Pi, Claude, and Gemini create subprocesses per turn. When the
orchestrator cancelled a worker during service shutdown, the backend `finally`
blocks cleared `_active_proc` before `client.stop()` could terminate the
subprocess. Cancellation now reaps the process tree first, then re-raises the
cancel so shutdown can continue normally.

- Rejected: treating the service fallback as the only cleanup layer. It is
  useful defense in depth, but the backend that owns the subprocess should
  stop it while it still has the process object.

### 2. Keep a force-stop fallback for completed owned rows and workspace helpers

The run registry can already contain `status: normal` rows by the time
`service stop --force` inspects it, even if a backend PID is still live. Force
stop now scans recent rows owned by the stopped orchestrator PID, and also
terminates POSIX processes whose command line names one of those registered
workspace paths. This catches helper processes that start a separate process
group, such as browser/tool kernels under a ticket workspace.

- Rejected: broad command-name killing. Cleanup stays constrained to the
  service's own registry owner PID and workspace paths.

### 3. Treat Claude `is_error` as authoritative

Claude Code can emit a terminal result with `subtype: success` and
`is_error: true` for quota/rate-limit failures. The backend now treats the
explicit error flag as authoritative so the operator sees a backend failure
instead of a successful empty turn loop.

- Rejected: relying on the terminal subtype alone. The live CLI returned a
  contradictory payload, and the explicit `is_error` flag carries the failure.

### 4. Make the static todo browser gate reusable

The operator-level browser acceptance script now lives at
`scripts/static_todo_browser_acceptance.py`. It drives Playwright against
`file://`, exercises add/toggle/filter/edit/reload/delete flows, and fails fast
on browser boot console errors such as Chromium blocking module scripts from
`file://`.

- Rejected: keeping the acceptance script as a temp-run artifact. The release
  gate needs a durable command workers and operators can both rerun.

# 2026-07-03 - Operator Trust Program implementation

## Goal

Complete the remaining Operator Trust Program tasks so an operator can trust
the system from public surfaces: health, attention signals, run history,
doctor, smoke checks, and fresh-clone documentation.

## Decisions

### 1. Keep health and attention changes additive

`health.status` now reports `starting` before the first completed tick and the
payload includes `workflow_path`. Attention payloads keep `kind`, `label`, and
`message`, then add `severity` and `due_at` where useful. This preserves
existing consumers while giving operators clearer startup and stuck-work
diagnostics.

- Rejected: renaming the existing health or attention fields to match newer
  spec wording. That would turn a trust-program polish pass into a breaking
  API change.

### 2. Derive attention from existing runtime facts

The attention taxonomy is built from existing retry entries, cancelled/stalled
running entries, lease-loss markers, budget-exhausted state, and issue-scoped
tracker errors. Priority is deterministic:
`stalled > lease_blocked > budget_exhausted > tracker_error > retry_scheduled`.

- Rejected: adding a second issue-state store for attention. It would create a
  reconciliation problem with Markdown tickets, leases, and the SQLite run
  registry without improving operator truth.

### 3. Reuse the run registry as the history ledger

Run history uses the existing SQLite registry and exposes bounded reads through
`/api/v1/runs`, `symphony runs`, and the web drawer. Filtering accepts both
the internal issue id and the operator-facing identifier so UI calls can stay
human-readable.

- Rejected: adding a schema migration or separate web history cache. The
  registry already stores attempt rows and terminal statuses; duplicating it
  would add failure modes for the same evidence.

### 4. Prefer deterministic local smoke over external-agent dispatch

Final runtime proof used a temporary file-board workflow under `/private/tmp`
with `python -m symphony.mock_codex`, a writable workspace root, and the
production server command. This verifies health, board CRUD, refresh, static
assets, workflow stats, and run-history reachability without depending on a
live Claude/Codex account.

- Rejected: claiming `doctor ./WORKFLOW.md` success in this sandbox. The repo
  workflow correctly reported environmental failures: the configured
  `~/symphony_workspaces` root is not writable here and the worktree has no
  local `kanban/` directory.

## Verification

- Focused attention/API/UI batch:
  `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src .../.venv/bin/python -m pytest -q tests/test_orchestrator_dispatch.py tests/test_run_registry.py tests/test_webapi.py tests/test_web_static_contract.py tests/test_tui.py`
  -> `190 passed`.
- Focused touched-slices batch:
  `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src .../.venv/bin/python -m pytest -q tests/test_orchestrator_health.py tests/test_run_registry.py tests/test_webapi.py tests/test_cli_main_routing.py tests/test_cli_run_startup.py tests/test_doctor.py tests/test_web_api_smoke_script.py tests/test_web_static_contract.py tests/test_tui.py`
  -> `137 passed, 2 warnings`.
- Full suite:
  `PYTHONPATH=/private/tmp/symphony-operator-trust-run/src .../.venv/bin/python -m pytest -q`
  -> `965 passed, 2 skipped, 2 warnings`.
- Static checks: `compileall` on touched Python packages, `git diff --check`,
  README proof-command grep, and workflow/snippet lane grep passed.
- Runtime checks: temp workflow `doctor` exited 0 with only the legacy
  board-viewer warning; live smoke against `http://127.0.0.1:54017` passed
  all nine checks; `/api/v1/health` returned `status: ok` and
  `/api/v1/runs?limit=5` returned an empty run list.

# 2026-07-03 - OpenCode long-turn stall fix

## Goal

Stop healthy OpenCode turns from being cancelled as stalled when the
per-turn CLI subprocess is still alive but has not emitted final JSON yet.

## Decisions

### 1. Emit backend liveness as a normalized heartbeat event

`OpenCodeBackend` now emits `other_message` payloads with
`type: opencode_heartbeat` while its turn subprocess is alive. That gives the
orchestrator's existing event-based stall detector a real progress signal
without changing shared scheduler semantics.

- Rejected: probing subprocess liveness from the orchestrator. That would put
  backend-specific process knowledge in the shared stall path and skip the
  existing run-lease heartbeat refresh done by progress events.

### 2. Keep progress filtering conservative

OpenCode marks only `opencode_heartbeat` as progress. Assistant text, metadata,
and future catch-all frames still do not reset the stall timer unless the
backend explicitly classifies them.

- Rejected: treating every `other_message` payload as progress. Prior stall
  fixes showed that echo/meta frames can mask real model silence.

### 3. Leave cancel exit classification unchanged

The fix does not reclassify `CancelledError` worker exits. Existing
`reason=normal` handling still protects token-budget persistence and the
auto-commit snapshot path for cancelled turns.

- Rejected: adding a separate cancelled outcome for stall kills. That would
  risk losing budget-exhausted state and mid-turn work snapshots.

## Verification

- Backend regression tests: `.venv/bin/python -m pytest tests/test_backends.py -x -q`
  -> `86 passed`.
- Full suite: `.venv/bin/python -m pytest -q` -> `967 passed, 2 skipped,
  2 warnings`.
- Long-turn run-path smoke: temp file-board workflow with `agent.kind:
  opencode`, `stall_timeout_ms=35000`, and a 65 s subprocess. `/api/v1/state`
  at 57 s showed `last_event=other_message`; final log had one dispatch,
  normal turn completion, and no `stalled_session`.
- Real OpenCode CLI E2E: temp file-board workflow using
  `/Users/danny/.opencode/bin/opencode run --format json --auto`; one dispatch
  completed normally and reported an OpenCode session id.
- Real Pi CLI E2E: temp file-board workflow using `pi --mode json -p ""`; one
  dispatch completed normally with token totals reported.

# 2026-07-03 - Four-agent todo E2E triage plan

## Goal

Record the production-readiness gaps found by a real four-agent Symphony run
against a static todo-app task, then preserve the fix path as a detailed plan.

## Decisions

### 1. Keep the live run isolated from the main board

The E2E used a temporary clone and temp workspace root while preserving the
real `dev` checkout and existing `kanban/` tickets. The temp workflow kept the
same hooks and agent backends, with only workspace root, polling interval, and
concurrency adjusted for the test.

- Rejected: running the four generated-app tickets on the main board. That
  would have mixed test artifacts with operator work and made cleanup risky.

### 2. Treat the result as a failed production gate

OpenCode and Pi both produced useful todo apps, but OpenCode missed a real
browser Escape-cancel behavior and Pi did not transition out of `In Progress`.
Claude and Codex did not produce apps because they hit orchestration-level
failure modes first. The plan therefore does not claim production readiness.

- Rejected: accepting generated DOM-shim evidence as sufficient. Real Chromium
  found behavior the shim missed.

### 3. Prioritize unattended-loop failures before UI polish

The fix plan starts with Codex approval prompts, Claude empty-response loops,
and Pi productive-without-transition behavior because those can leave the board
looking active while no reliable delivery path exists.

- Rejected: starting with the todo app bug itself. The app defect matters, but
  the larger production risk is that Verify and orchestration allowed the
  broken or unfinished states to persist.

### 4. Serialize only git metadata setup, not whole workspace creation

The concurrent `.git/config` lock failure points at the git worktree critical
section. The plan scopes locking to worktree setup and git config writes, while
keeping venv install outside the lock to preserve useful concurrency.

- Rejected: lowering global agent concurrency. That would avoid the symptom
  while failing the requested four-agent production scenario.

## Artifacts

- Plan: `docs/plans/2026-07-03-four-agent-todo-e2e-production-hardening.md`
- Temp clone used for evidence:
  `/private/tmp/symphony-e2e-todo-MJxJP2/repo`
- Temp workspace root:
  `/private/tmp/symphony-e2e-todo-MJxJP2/workspaces`

## Verification

- Temp `symphony doctor ./WORKFLOW.md` passed before the service run.
- OpenCode generated harness: `node docs/e2e-todo/E2E-101/harness/run.js`
  -> `23 passed, 0 failed`.
- OpenCode real Chromium interaction found inline edit Escape-cancel failure.
- Pi real Chromium interaction and persistence test passed.
- Final documentation checks for this plan are recorded with the work that
  added the plan file.

# 2026-07-03 - Auto-commit destructive snapshot guard

## Goal

Prevent a worker's bad in-turn commit from being squashed into a branch that
deletes core repository files during the release E2E gate.

## Decisions

### 1. Refuse protected root-file and high-volume deletions at auto-commit time

`commit_workspace_on_done` now inspects the staged squash before creating the
final ticket commit. It refuses deletion of root contract files
(`pyproject.toml`, `WORKFLOW*.md`) and refuses unusually large delete sets.

- Rejected: relying on auto-merge to catch the damage later. The failed
  OpenCode release lane showed the destructive branch can still reach Human
  Review with browser-passing artifacts, which is too late for a safe release
  signal.
- Rejected: blocking all deletions. Small, intentional cleanup can be valid;
  the guard targets repo-contract loss and mass deletion.

# 2026-07-03 - E2E hardening item 1: Codex approvals

## Goal

Keep unattended Codex app-server turns from stalling on server-initiated
approval requests.

## Decisions

### 1. Reply to every JSON-RPC request that carries an id

Codex server-initiated requests now bypass notification handling and receive an
immediate JSON-RPC response on stdin. Known approval methods are answered
affirmatively, except dangerous commands are declined so the turn can continue.
Unknown request methods receive a `-32601` JSON-RPC error instead of being left
pending.

- Rejected: relying on stall cancellation. It only retries the same unanswered
  approval request.
- Rejected: using `cancel` for denied commands. `decline`/`denied` preserves the
  turn and gives the worker a chance to choose a safer command.

### 2. Keep the denylist tight and allow-biased

The new approval policy blocks only recursive-force `rm`, `sudo`, `mkfs`, `dd`
to `/dev`, `shred`, `find -delete`, and `git clean` with both `-f` and `-x`.
The classifier intentionally scans command text without shell-quote parsing, so
quoted examples such as `echo "rm -rf"` may be denied.

- Rejected: full shell parsing. It adds complexity and still cannot safely model
  every chained shell expression in an unattended safety guard.

### 3. Surface denials in state

`approval_denied` updates issue debug `last_error`, logs a warning, and appears
in running state rows so `/api/v1/state` can show the operator what was blocked.

- Rejected: emitting only backend telemetry. That would require log-diving and
  would not satisfy the board/state attention requirement.

## Verification

- Focused gate: `.venv/bin/python -m pytest tests/test_codex_approvals.py tests/test_orchestrator_dispatch.py::test_on_codex_event_records_approval_denial_last_error -q`
  -> `32 passed`.
- Broader backend/dispatch gate: `.venv/bin/python -m pytest tests/test_codex_approvals.py tests/test_backends.py tests/test_backends_edges.py tests/test_orchestrator_dispatch.py -q`
  -> `281 passed`.

# 2026-07-03 - E2E hardening item 2: Pause reasons

## Goal

Make empty-response and operator pauses explain themselves after worker exit,
restart, and API refresh.

## Decisions

### 1. Persist pause reasons with issue flags

`issue_flags` now has nullable `pause_reason`, migrated in place for existing
SQLite registries. The reason is stored only while `paused` is true and is
cleared with the paused flag.

- Rejected: storing the reason only in `_IssueDebug.last_error`. Debug state is
  process-local and does not survive restart.

### 2. Keep manual and automatic pauses on one path

Manual `pause_worker()` records `operator pause` unless the caller supplies a
reason. Empty-response auto-pause records the threshold/count and the
`resume_worker` recovery path in the same registry field.

- Rejected: adding a separate empty-loop registry table. The pause flag is
  already the scheduler gate, so splitting the reason would add another source
  of truth.

### 3. Show paused tickets as attention before retry/budget signals

`issue_attention()` now reports `{kind: paused, label: Paused}` after
stalled/lease checks and before budget, tracker, and retry branches. This keeps
a non-running paused ticket from looking idle on the board.

- Rejected: making retry timers carry the pause explanation. Paused tickets may
  be non-running without a retry row, and retry attention is less direct than
  the paused gate itself.

## Verification

- Focused gate: `.venv/bin/python -m pytest tests/test_run_registry.py::test_run_registry_persists_issue_flags_across_reopen tests/test_run_registry.py::test_run_registry_clears_issue_flags_independently tests/test_orchestrator_dispatch.py::test_persisted_issue_flags_block_dispatch_after_restart tests/test_orchestrator_dispatch.py::test_pause_resume_write_through_issue_flags tests/test_orchestrator_dispatch.py::test_pause_worker_persists_custom_reason tests/test_orchestrator_dispatch.py::test_issue_attention_reports_paused_non_running_ticket tests/test_orchestrator_dispatch.py::test_g2_empty_response_loop_pause_reason_persists_and_rehydrates -q`
  -> `7 passed`.
- Broader registry/dispatch/API gate: `.venv/bin/python -m pytest tests/test_run_registry.py tests/test_orchestrator_dispatch.py tests/test_webapi.py -q`
  -> `139 passed`.

# 2026-07-03 - E2E hardening item 3: No-stage-change watchdog

## Goal

Stop workers that keep completing turns in the same stage without moving the
ticket state.

## Decisions

### 1. Count completed turns per normalized state

`agent.max_state_turns` defaults to `30` and counts completed turns while the
ticket stays in one normalized state. `0` disables the watchdog. The counter is
stored in issue debug state so it survives attempt continuations and resets to
zero on any state change or phase-transition rebuild.

- Rejected: using token growth or file changes as the primary signal. Those
  prove activity, not workflow progress, and were the failure mode in the Pi
  run.

### 2. Block by default, move only when explicitly configured

The default `agent.no_stage_change_action: block` stops continuation, claims
and pauses the ticket, optionally persists `budget_exhausted_state`, and writes
a clear pause reason. A configured state such as `Verify` writes a
`Stage Watchdog Handoff` note and moves the ticket there without pausing.

- Rejected: always auto-advancing to Verify. That can promote incomplete work
  and hides that the worker ignored the stage contract.

### 3. Reuse budget persistence for blocked outcomes

The block path extends `Budget Exceeded` notes with a `no_stage_change` detail
instead of inventing a second blocked-note mechanism. Explicit move actions use
the separate handoff note because they are not budget-blocked outcomes.

- Rejected: leaving block outcomes in-memory only. Without a tracker note or
  pause reason, restarts and board views would lose the cause.

## Verification

- Focused gate: `.venv/bin/python -m pytest tests/test_workflow.py::test_default_no_stage_change_watchdog_is_block_after_thirty_turns tests/test_workflow.py::test_no_stage_change_watchdog_can_disable_or_move_to_state tests/test_workflow.py::test_no_stage_change_action_must_be_block_or_configured_state tests/test_orchestrator_dispatch.py::test_no_stage_change_counter_resets_on_state_change tests/test_orchestrator_dispatch.py::test_worker_loop_no_stage_change_watchdog_blocks_and_pauses tests/test_orchestrator_dispatch.py::test_worker_loop_no_stage_change_action_moves_to_verify tests/test_orchestrator_dispatch.py::test_worker_loop_no_stage_change_watchdog_disabled -q`
  -> `7 passed`.
- Broader workflow/dispatch gate: `.venv/bin/python -m pytest tests/test_workflow.py tests/test_orchestrator_dispatch.py -q`
  -> `157 passed`.

# 2026-07-03 - E2E hardening item 4: Worktree setup lock

## Goal

Prevent concurrent `after_create` hooks from colliding on host git worktree
admin files and shared `.git/config` writes.

## Decisions

### 1. Lock only the git admin critical section

`symphony-setup-worktree.sh` now acquires a host-repo lock before
`git worktree remove` and releases it immediately after the `symphony.*`
`git config --worktree` writes. Symlink/junction setup and venv priming stay
outside the lock.

- Rejected: wrapping the whole script. Python venv installation can be slow and
  does not touch shared git admin state, so serializing it would reduce worker
  startup concurrency without fixing the race more completely.

### 2. Use flock when available, mkdir lock otherwise

Linux hosts use fd-based `flock` on `.git/symphony-worktree.lock`. macOS-style
hosts without `flock` fall back to a mkdir spin lock with timeout, stale-lock
breakage, and a pid file for diagnosis.

- Rejected: relying on git's own lock retries. The observed failure happens
  before git retries can coordinate separate worktree admin operations.

### 3. Preserve failure cleanup with an EXIT trap

The lock release path is registered before the critical section, so `set -e`
failures cannot leave the mkdir fallback lock behind.

- Rejected: manual unlock only on the success path. A failed `git config` would
  leak the lock and turn one transient failure into a two-minute stall.

## Verification

- Reproduced baseline failure with the new concurrency test before the fix:
  `.venv/bin/python -m pytest tests/test_workspace.py::test_setup_worktree_script_serializes_concurrent_git_admin_writes -q`
  -> failed with `could not lock config file`.
- Syntax gate: `bash -n scripts/symphony-setup-worktree.sh` -> passed.
- Focused gate: `.venv/bin/python -m pytest tests/test_workspace.py::test_setup_worktree_script_serializes_concurrent_git_admin_writes -q`
  -> `1 passed`.
- Existing hook regression: `.venv/bin/python -m pytest tests/test_workspace.py::test_file_workflow_after_create_hides_host_symlink_roots_from_git -q`
  -> `1 passed`.
- Broader workspace gate: `.venv/bin/python -m pytest tests/test_workspace.py -q`
  -> `28 passed`.

# 2026-07-03 - E2E hardening item 5: Verify QA and telemetry

## Goal

Make Verify reject browser-app shim-only QA and pin the live state telemetry
shape across supported agent kinds.

## Decisions

### 1. Browser UI QA must use a real browser

Both file and linear Verify prompts now require Playwright or headless Chromium
for browser UI deliverables, against `file://` or a tiny static server. DOM
shims are allowed only as smoke tests, not final Verify evidence. Missing
browser dependencies require `## Environment Block`, `Blocked`, and stop.

- Rejected: letting zero-dependency DOM shims pass Verify. They do not exercise
  browser behavior, storage, focus, reload, or event semantics reliably enough
  for QA signoff.

### 2. Keep telemetry normalization in the orchestrator row

The existing `/api/v1/state` path returns `orchestrator.snapshot()`, whose
running rows already include `agent_kind`, `session_id`, `attention`, and the
normalized token block. A new regression test pins that shape for codex,
claude, pi, and opencode by feeding normalized backend events through
`_on_codex_event`.

- Rejected: adding backend-specific fields to the API row. The UI needs one
  normalized shape regardless of which CLI produced the events.

### 3. Skip `tokens_per_completed_stage`

The current counters expose state-local token totals and turn counts, but not a
completed-stage denominator. Adding the metric now would either divide by the
wrong value or require a broader stats schema change, so this patch leaves it
out.

- Rejected: deriving it from `completed_turn_count`. Turns are not stages, and
  the resulting number would be misleading during long-running Verify/Learn
  work.

## Verification

- Focused gate: `.venv/bin/python -m pytest tests/test_workflow_pipeline_prompt.py::test_verify_stage_demands_review_qa_and_merge_evidence tests/test_orchestrator_dispatch.py::test_running_snapshot_carries_live_telemetry_for_supported_agent_kinds -q`
  -> `3 passed`.
- Prompt regression gate: `.venv/bin/python -m pytest tests/test_prompt.py tests/test_workflow_pipeline_prompt.py -q`
  -> `53 passed`.
- State route/snapshot gate: `.venv/bin/python -m pytest tests/test_orchestrator_dispatch.py::test_running_snapshot_carries_live_telemetry_for_supported_agent_kinds tests/test_server_routes.py::test_state_route_returns_orchestrator_snapshot -q`
  -> `2 passed`.

# 2026-07-03 - SMA-27 Pi 429 RCA

## Goal

Explain the observed Pi backend-internal 429 retries during the live Symphony
run for `SMA-27`.

## Decision

Treat the failure as a Pi upstream availability/rate-limit signal, not an app
quality failure or Symphony dispatch defect.

Evidence:

- `log/symphony.log` shows `SMA-27` dispatched with `agent_kind=pi`, then Pi
  emitted repeated backend-internal retry events with `429 The service may be
  temporarily overloaded, please try again later`.
- Each failed Pi turn ended as `turn_error`, then Symphony scheduled the normal
  worker retry attempts.
- After `agent.max_retries=3`, Symphony logged `agent_retry_cap_exhausted` and
  moved the ticket to `Human Review`, matching the configured safety valve.
- `kanban/SMA-27.md` records the same escalation and last error.

Rejected causes:

- Rejected: application test or implementation failure. The failing evidence is
  the Pi upstream 429 before app-quality verification could complete.
- Rejected: Symphony retry-loop bug. Focused tests confirm Pi retry events are
  surfaced and max-retry exhaustion escalates instead of looping forever.
- Rejected: local service-state proof. The service was no longer listening on
  `127.0.0.1:9999` during this investigation, so persisted logs and the ticket
  file are the authoritative evidence for this run.

## Verification

- `symphony doctor ./WORKFLOW.md` -> workflow checks passed except the known
  sandbox-local workspace writability failure for `/Users/danny/symphony_workspaces`.
- `curl -s http://127.0.0.1:9999/api/v1/state` -> connection refused; service
  was not running at investigation time.
- `PYTHONPATH=src .venv/bin/pytest tests/test_backends.py::test_pi_consume_stream_surfaces_compaction_events tests/test_orchestrator_max_retries.py::test_max_retries_exhausted_triggers_escalation_task`
  -> `2 passed`.

# 2026-07-03 - Four-agent rerun release gate

## Goal

Decide whether the post-fix four-agent todo E2E run is clean enough to merge
`feat/e2e-production-hardening` to `dev`, merge `dev` to `main`, and cut the
next release.

## Decision

Do not merge or release yet.

Evidence:

- At triage time, the latest local release tag was `v0.8.0`; release numbering
  still needed reconciliation before tagging.
- Version source files currently say `0.9.1` in both `pyproject.toml` and
  `src/symphony/__init__.py`, so release numbering needs reconciliation before
  tagging.
- The fresh temp run dispatched OpenCode, Pi, Claude, and Codex tickets and all
  four `after_create` hooks completed.
- OpenCode external Node harness and browser QA passed, but the Symphony Verify
  worker was still active when the run was stopped.
- Pi Node harness passed, but real browser proof failed: `#empty-state` had
  `display: block` while parent `#main` had `display: none`, so the empty state
  was not visible.
- Claude was intentionally paused/stopped by operator request, so a complete
  Claude Verify/Learn lifecycle was not proven.
- Codex reached Human Review after repairing a Verify evidence-citation
  contract failure.
- Forced service stop left temp-run OpenCode and Pi child process groups alive;
  they were terminated manually.

Rejected alternatives:

- Rejected: merge because most checks passed. The Pi browser defect and
  incomplete lifecycle fail the release condition.
- Rejected: rely on Node DOM shims for browser UI signoff. The Pi empty-state
  failure is a concrete counterexample.
- Rejected: tag from current version files. `v0.8.0` is the latest release tag
  while source files say `0.9.1`; the next tag must use the operator-approved
  release number after the gate is green.

## Verification

- Release-blocker plan recorded at
  `docs/plans/2026-07-03-four-agent-rerun-release-blockers.md`.
- Temp service on port `10082` was stopped.
- Temp-run OpenCode and Pi orphan process groups were terminated.

# 2026-07-03 - Human Review confirmation gate spec

## Goal

Specify the fix for a live-run handoff gap: a ticket can correctly reach
`Human Review` and ask the operator to `Confirm Done`, while the service web
board has no visible confirm action.

## Decision

Add a focused spec for the service-board Human Review confirmation gate and the
Verify evidence path rule that caused RERUN-204 to rewind.

Evidence:

- TUI and standalone `tools/board-viewer` already have Human Review confirm
  behavior.
- The service web board (`src/symphony/web/static/app.js`) has `Skip Learn`
  but no `Confirm Done` action.
- The service API (`src/symphony/webapi.py`) has issue CRUD and `skip-learn`
  but no narrow `/api/v1/issues/{id}/confirm-done` route.
- The host checkout lacks the `docs/llm-wiki/verify-evidence-contract.md`
  page produced by the RERUN-204 workspace.

Rejected alternatives:

- Rejected: let agents mark Done. That weakens the production handoff rule.
- Rejected: rely on drag/drop or generic PATCH. That is not an explicit human
  confirmation affordance.
- Rejected: fix only the standalone viewer. The live service board is the
  missing path.

## Verification

- `symphony doctor ./WORKFLOW.md` -> expected sandbox-local FAIL for
  `/Users/danny/symphony_workspaces` writability; prompt, tracker, and viewer
  checks passed.
- Spec written at `docs/spec/human-review-confirmation-gate/`.

# 2026-07-03 - Worker errors pause issue board

## Goal

Make backend or worker error codes visible on the issue board and stop automatic
progress until an operator inspects the ticket.

## Decision

Persist a paused issue flag on non-normal worker exits, using a sanitized
operator-facing reason such as `worker error: turn_error: 429 ...; paused for
operator inspection`.

The existing retry entry remains scheduled under the pause. This keeps the
recovery path intact, while the pause gate prevents hidden retry cycling until
the operator resumes the issue.

Rejected alternatives:

- Rejected: convert every worker error to a terminal state. Transient provider
  errors such as Pi `429` can still be retried after operator inspection.
- Rejected: only log the error. Logs are not enough when the board is the live
  operational surface.
- Rejected: show raw stderr. ANSI/control bytes can make board text hard to
  read, so the board message is normalized while preserving the error code and
  backend text.

## Verification

- `PYTHONPATH=src .venv/bin/pytest tests/test_orchestrator_dispatch.py::test_worker_exit_error_auto_pauses_with_visible_reason -q`
  -> `1 passed`.
- `PYTHONPATH=src .venv/bin/pytest tests/test_orchestrator_dispatch.py::test_worker_exit_error_auto_pauses_with_visible_reason tests/test_orchestrator_dispatch.py::test_worker_exit_preserves_pause_flag_for_held_ticket tests/test_orchestrator_dispatch.py::test_retry_timer_reparks_paused_ticket_without_dispatching tests/test_orchestrator_dispatch.py::test_issue_attention_reports_paused_non_running_ticket tests/test_orchestrator_dispatch.py::test_retry_schedule_write_through_and_continuation_clears_issue_flag -q`
  -> `5 passed`.

# 2026-07-03 - Paused retry keeps Pi error visible

## Goal

Verify the worker-error pause behavior against a live Pi dispatch and keep the
board message clear after the paused retry hold timer fires.

## Decision

When a paused issue's retry timer fires, re-park the retry with the stored pause
reason instead of replacing the visible error with the generic string
`paused`.

Evidence from Pi E2E:

- Temp run root: `/private/tmp/symphony-pi-e2e-BmEKJq`.
- `symphony doctor ./WORKFLOW.md` passed after temp board init.
- Pi dispatch for `PI-E2E-301` failed before app generation because the Pi CLI
  could not create its session directory:
  `code: 'EPERM'`, `syscall: 'mkdir'`,
  `path: '/Users/danny/.pi/agent/sessions/--private-tmp-symphony-pi-e2e-BmEKJq-workspaces-PI-E2E-301--'`.
- The initial fix paused the issue, but the retry re-park overwrote the state
  API error with `paused`.
- After the re-park fix, `PI-E2E-302` stayed `paused: true` and repeated
  `/api/v1/state` polls kept the full `EPERM` / `mkdir` error in both
  `retrying[].error` and `retrying[].attention.message`.

Rejected alternatives:

- Rejected: rely on logs only. The issue board state API is the operator
  surface during a headless Symphony run.
- Rejected: consume another retry attempt while paused. Pause is an operator
  hold, not a failed retry attempt.

## Verification

- `PYTHONPATH=src .venv/bin/pytest tests/test_orchestrator_dispatch.py::test_worker_exit_error_auto_pauses_with_visible_reason tests/test_orchestrator_dispatch.py::test_retry_timer_reparks_paused_ticket_without_dispatching tests/test_orchestrator_dispatch.py::test_resume_worker_releases_held_retry_immediately -q`
  -> `3 passed`.

# 2026-07-03 - Claude error result reason for release E2E

## Goal

Make the four-agent release gate diagnosable when Claude Code emits an error
result whose `subtype` is still `success`.

## Decision

Format Claude failed-result messages from actionable fields (`error`,
`message`, `result`, API error fields) before considering `subtype`, and add
that computed text as the emitted turn-failure `reason`.

Evidence from the clean `dev` four-agent attempt:

- `dev` was fast-forwarded and pushed to `998bdee`.
- Temp run root: `/private/tmp/symphony-release-e2e-14zh0I`.
- Four tickets dispatched from a clean `origin/dev` clone on port `10086`.
- `REL-303` (Claude) failed immediately and auto-paused with
  `turn_error: turn_failed: success`, losing the provider's actionable cause.
- `service stop ./WORKFLOW.md --timeout 15 --force` closed the port and a
  process sweep found no command line referencing the temp root.

Rejected alternatives:

- Rejected: treat `subtype: success` as the displayed reason for error
  results. It is a transport label in this failure shape, not the operator
  action text.
- Rejected: only attach the raw payload. The issue board and logs need a short
  reason string that tells the operator what happened.

## Verification

- `PYTHONPATH=src .venv/bin/pytest tests/test_backends.py::test_claude_success_subtype_with_is_error_true_fails_turn -q`
  failed before the fix with `Actual message: 'turn_failed: success'`.
- `PYTHONPATH=src .venv/bin/pytest tests/test_backends.py::test_claude_success_subtype_with_is_error_true_fails_turn -q`
  -> `1 passed`.
- `PYTHONPATH=src .venv/bin/pytest tests/test_backends.py::test_claude_success_subtype_with_is_error_true_fails_turn tests/test_backends_edges.py::TestClaudeIsErrorResultBranches -q`
  -> `7 passed`.

# 2026-07-03 - Static todo browser gate profile isolation

## Goal

Make the release browser gate reliable inside Symphony worker workspaces instead
of depending on the operator's default Chromium profile directories.

## Decision

Launch Chromium with a temporary writable `HOME`, `XDG_CONFIG_HOME`, and
`XDG_CACHE_HOME`, plus crash reporter disable flags, for each
`scripts/static_todo_browser_acceptance.py` run.

Evidence from the fresh `dev` rerun:

- Temp run root: `/private/tmp/symphony-release-e2e-r3-ywgA6n`.
- `REL-304` (Codex) generated a valid app, but its worker-side browser gate
  failed before app load on
  `~/Library/Application Support/Chromium/Crashpad/settings.dat: Operation not
  permitted`.
- Running the same generated app through the host acceptance script after
  isolating browser state passed, proving the failure was browser launch
  environment, not the todo app behavior.

Rejected alternatives:

- Rejected: telling workers to rerun outside the sandbox. The release gate must
  be a repeatable command in worker workspaces.
- Rejected: ignoring the browser gate when Node checks pass. Earlier Pi evidence
  already proved DOM shims and Node harnesses can miss real browser failures.
- Rejected: sharing a persistent browser profile. A release gate should not
  depend on mutable operator-local state.

## Verification

- `PYTHONPATH=src .venv/bin/pytest tests/test_static_todo_browser_acceptance.py -q`
  failed before the fix because the module had no isolated browser profile
  launcher.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_static_todo_browser_acceptance.py -q`
  -> `1 passed`.
- `.venv/bin/python -m py_compile scripts/static_todo_browser_acceptance.py`
  -> passed.
- `.venv/bin/python scripts/static_todo_browser_acceptance.py /private/tmp/symphony-release-e2e-r3-ywgA6n/workspaces/REL-304/examples/e2e-todo/codex`
  -> `PASS`.
- `PYTHONPATH=src SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs`
  -> `1 passed`.
- `PYTHONPATH=src .venv/bin/python -m symphony.cli doctor ./WORKFLOW.md`
  -> all checks `PASS`.
- `PYTHONPATH=src .venv/bin/python -m pytest -q`
  -> `1024 passed, 2 skipped, 2 warnings`.

# 2026-07-03 - Worktree browser dependency setup

## Goal

Make Symphony-created workspaces use a Python environment compatible with the
current package metadata and the checked-in browser acceptance gate.

## Decision

The worktree setup hook now prefers `python3.12`/`python3.13` before `python3.11`
and installs `.[dev,browser]` instead of only `.[dev]`.

Evidence from the fresh `dev` rerun:

- Temp run root: `/private/tmp/symphony-release-e2e-r4-rDTUem`.
- `REL-404` (Codex) generated a valid app; the host acceptance gate passed
  against that app.
- The worker `.venv` was created with Python 3.11 even though
  `pyproject.toml` requires `>=3.12`, so editable install failed and the venv
  had no `playwright`.
- The ticket command used host/global `python`, which had Playwright 1.50 but
  missed the required `chromium_headless_shell-1155` executable. This made the
  worker mark a valid app `Blocked`.

Rejected alternatives:

- Rejected: relying on host/global `python`. It changes with operator shell
  state and can carry mismatched Playwright/browser revisions.
- Rejected: keeping browser dependencies out of worker venvs while requiring
  browser gates in release tickets. That creates a guaranteed environment block
  for browser UI work.

## Verification

- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workspace.py::test_setup_worktree_script_uses_pyproject_compatible_browser_env -q`
  failed before the fix because the script still preferred `python3.11` and
  installed only `.[dev]`.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workspace.py::test_setup_worktree_script_uses_pyproject_compatible_browser_env -q`
  -> `1 passed`.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workspace.py -q`
  -> `29 passed`.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workspace.py tests/test_static_todo_browser_acceptance.py -q`
  -> `30 passed`.
- `PYTHONPATH=src SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs`
  -> `1 passed`.
- `git diff --check` -> passed.
- `PYTHONPATH=src .venv/bin/python -m pytest -q`
  -> `1025 passed, 2 skipped, 2 warnings`.

# 2026-07-03 - Terminal cleanup and retry resume hardening

## Goal

Remove the r6 release blockers where a paused retry could not be resumed over
HTTP and terminal-state reconciliation raced worker-exit auto-commit.

## Decision

Resume now resolves both running workers and held retry entries, while pause
remains running-only. Reconcile also copies the terminal tracker state into the
running entry, gives recent terminal workers a 60-second natural-exit grace, and
marks workspaces whose cleanup has already started so worker exit cannot run a
second git snapshot against the same worktree.

Evidence from r6:

- `POST /api/v1/REL-601/resume` returned `issue_not_running` even though
  `/api/v1/state` showed a paused retry for `REL-601`.
- `REL-601` reached `Human Review`, but reconcile and worker-exit cleanup both
  attempted auto-commit; one hit `index.lock`, then the final snapshot refused
  a protected root-file deletion.
- Branch tips did not match board state after the race, so the full release
  gate stayed red despite the browser app passing.

Rejected alternatives:

- Rejected: make pause operate on retry entries too. A retry is not currently
  executing a backend process; resume is the action operators need there.
- Rejected: silence auto-commit failures. Protected deletion refusals are
  release-safety signals and must remain visible.
- Rejected: rely on a global git lock around auto-commit. The root issue was
  duplicate cleanup ownership, not generic git concurrency.

## Verification

- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_server_routes.py::test_resume_route_releases_paused_retry_worker tests/test_server_routes.py::test_resume_route_returns_404_for_unknown_identifier tests/test_orchestrator_dispatch.py::test_reconcile_terminate_terminal_commits_before_remove -q`
  -> `3 passed`.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_server_routes.py -q`
  -> `16 passed`.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_orchestrator_dispatch.py::test_reconcile_part_b_skips_paused_worker_on_terminal_state tests/test_orchestrator_dispatch.py::test_reconcile_terminate_terminal_commits_before_remove tests/test_orchestrator_dispatch.py::test_reconcile_terminate_terminal_skips_commit_when_auto_off tests/test_orchestrator_dispatch.py::test_on_worker_exit_commits_workspace_at_done tests/test_orchestrator_dispatch.py::test_on_worker_exit_commits_workspace_for_non_done_terminal_state -q`
  -> `5 passed`.

# 2026-07-03 - Codex browser sandbox release blocker RCA

## Goal

Remove the r7 Codex release blocker where a valid todo app could not be proven
inside the Codex worker because Playwright Chromium could not launch.

## Decision

The shipped workflow now runs Codex with `danger-full-access` for both
`thread_sandbox` and `turn_sandbox_policy`.

Evidence from r7:

- Codex worker evidence for `REL-704` failed before app interaction with
  `bootstrap_check_in ... Permission denied (1100)`.
- The operator-shell rerun of the exact same app and browser gate passed:
  `.venv/bin/python scripts/static_todo_browser_acceptance.py
  /private/tmp/symphony-release-e2e-r7-VCauY2/workspaces/REL-704/examples/e2e-todo/codex`
  -> `PASS`.
- The local Codex app-server schema supports `dangerFullAccess`, while
  `workspaceWrite` is the sandbox profile that adds the outer macOS sandbox.

Rejected alternatives:

- Rejected: another browser profile isolation patch. The gate already isolates
  `HOME`, cache, and config, and passes outside the Codex worker.
- Rejected: treating the failure as an app defect. The identical app passes the
  host browser gate.
- Rejected: skipping browser proof for Codex. The release condition requires
  real browser evidence from every backend.

## Verification

- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workflow.py::test_repo_workflow_codex_is_browser_capable -q`
  failed before the fix because the workflow still used `workspace-write`.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workflow.py::test_repo_workflow_codex_is_browser_capable tests/test_backends.py::test_codex_sandbox_policy_workspace_write_to_v2_payload -q`
  -> `2 passed`.
- `PYTHONPATH=src .venv/bin/python -m symphony.cli doctor ./WORKFLOW.md`
  -> all checks `PASS`.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workflow.py tests/test_backends.py -q`
  -> `138 passed`.
- `git diff --check` -> passed.
- `PYTHONPATH=src .venv/bin/python -m pytest -q`
  -> `1029 passed, 2 skipped, 2 warnings`.

# 2026-07-03 - v0.9.1 release closeout

## Goal

Close the release after the blockers were fixed, pushed, merged to `dev` and
`main`, and proven by a fresh four-agent E2E run.

## Decision

Publish `v0.9.1`, matching the source version already declared in
`pyproject.toml` and `src/symphony/__init__.py`.

Evidence:

- Remote `origin/dev` and `origin/main` both point to
  `d129b450755067a5018e4f21a64ad59cc42945a3`.
- Tag `v0.9.1` dereferences to
  `d129b450755067a5018e4f21a64ad59cc42945a3`.
- GitHub release `v0.9.1` was published.
- The landing-page version badge was updated from `v0.9.0` to `v0.9.1`.

Rejected alternatives:

- Rejected: leave the landing page at `v0.9.0`. It is a visible public version
  marker and would contradict the published release.
- Rejected: back-tag `v0.8.1`. The source version is already `0.9.1`, and the
  verified release tag should match package metadata.

## Verification

- Fresh r8 four-agent E2E from pushed `dev` reached `Human Review` for
  OpenCode, Pi, Claude, and Codex.
- `/api/v1/state` ended with `running=0` and `retrying=0`.
- Branch-archive browser gates passed for all four generated apps:
  OpenCode, Pi, Claude, and Codex.
- `symphony service stop --force` closed the r8 service ports and left no
  process referencing the temp root.
- `PYTHONPATH=src .venv/bin/python -m pytest -q`
  -> `1029 passed, 2 skipped, 2 warnings`.

# 2026-07-03 - Human-readable board prompt refresh

## Goal

Make the shipped file and Linear Kanban prompts easier for non-specialist
operators to understand while preserving the four-stage workflow and existing
contract headings.

## Decision

Refresh `docs/symphony-prompts/{file,linear}/` around a plain board-card
mental model: each lane answers one human question, and every evidence claim
should name the goal, before state, after target, proof, not-covered risk, and
how to re-run. The required top-level sections stay the same so existing
contract enforcement continues to work.

Rejected alternatives:

- Rejected: add new required top-level sections such as `## Goal and Proof
  Map`. That would force orchestrator contract changes and make older in-flight
  tickets rewind for a readability-only improvement.
- Rejected: change the active states. The problem is prompt usability, not the
  pipeline shape.
- Rejected: update only the file-board prompts. Linear ships the same operator
  workflow and should not drift from the file-board guidance.

## Verification

- `PYTHONPATH=src .venv/bin/python -m pytest -q`
  -> `1029 passed, 2 skipped, 2 warnings`.
