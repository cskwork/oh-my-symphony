# 2026-07-02 — Web Kanban revamp (multica/Archon-style)

## Goal

Revamp the UX layer into a full web + TUI product: user-registered issues with
attachable skills, user-editable workflow (kanban columns add/delete/rename),
per-column prompt editing, and a dedicated stats page — on top of the existing
orchestrator, which stays untouched in its core loop.

## Decisions

### 1. Web UI lives in the main package, served by the orchestrator's aiohttp server

`src/symphony/web/static/` (vanilla HTML/CSS/JS, no build step) served at `/`
by the existing `server.py` app. New REST endpoints get direct in-process
access to the tracker, workflow config, and orchestrator snapshot.

- Rejected: upgrading `tools/board-viewer` (separate stdlib server + proxy).
  Full CRUD needs workflow mutation + tracker writes; proxying doubles every
  endpoint and leaves two servers to keep in sync. The board-viewer is now
  deprecated in place.
- Rejected: React/Vite SPA. A node build step breaks the "pip install, no
  signup, no build" ethos; the multica look is achievable with plain CSS.

### 2. WORKFLOW.md stays the single source of truth; UI edits round-trip via ruamel.yaml

Column add/delete/rename and branch policy write back into WORKFLOW.md YAML
frontmatter using ruamel.yaml round-trip parsing, which preserves the file's
extensive comments and key order. Hand edits and UI edits coexist.
The orchestrator already hot-reloads WORKFLOW.md per poll (`WorkflowState.reload`),
so UI changes apply on the next tick without restart.

- Rejected: overlay file (`.symphony/workflow-overrides.yml`) merged at load —
  two sources of truth confuse hand-editing users.
- Rejected: PyYAML rewrite — destroys comments users rely on.

### 3. Skills = SKILL.md directories, attached per issue, injected into the first-turn prompt

`GET /api/v1/skills` scans `<workflow_dir>/skills/*/SKILL.md`. Issues gain a
`skills: [name, ...]` frontmatter list (new `Issue.skills` field). At dispatch,
the orchestrator appends each attached skill's SKILL.md body (size-capped)
under `## Attached skills` after the rendered stage prompt.

- Rejected: template-variable injection (`{{ skills }}`) — would require every
  existing workflow/prompt file to opt in; appending works with all of them.

### 4. Stats = append-only JSONL event store, aggregated on read

`.symphony/stats.jsonl` next to the existing `token_ema.json`. The orchestrator
appends events (turn tokens, phase transitions, run outcomes) via a failure-
tolerant `StatsStore`. `GET /api/v1/stats?days=N` computes aggregates on read.

- Rejected: SQLite — a second storage engine for data this small is overkill;
  JSONL is greppable and matches the file-first ethos.

## API surface (new)

```
GET    /api/v1/board                     columns + issues + live run info
POST   /api/v1/issues                    create issue
GET    /api/v1/issues/{id}               detail (frontmatter + body + run)
PATCH  /api/v1/issues/{id}               update fields / move state
DELETE /api/v1/issues/{id}               delete ticket file
GET    /api/v1/workflow                  states, descriptions, prompt map
PUT    /api/v1/workflow/states           add/remove/rename columns
GET    /api/v1/workflow/prompts/{state}  prompt file content
PUT    /api/v1/workflow/prompts/{state}  save prompt file
GET    /api/v1/skills                    available skills
GET    /api/v1/stats?days=N              aggregates for stats page
GET    /api/v1/git/branches              local branches (branch policy UI)
PUT    /api/v1/workflow/branch-policy    feature base / merge target
```

Guardrails: prompt paths must resolve inside the workflow dir; state and issue
identifiers validated; a state with a running worker cannot be removed; at
least one active and one terminal state must remain; removed states migrate
their tickets to the first active state.

## Review outcomes (2026-07-02)

Three independent reviews (security, backend, SPA JS) ran against the branch;
all CRITICAL/HIGH findings were fixed and regression-tested the same day:

- **CRITICAL** — ticket identifiers from GET/DELETE routes reached
  `board_root / f"{id}.md"` unvalidated; on Windows a `%5C`-encoded backslash
  traverses out of the board. Fixed with the same identifier whitelist used
  on create, applied to every route parameter.
- **HIGH** — stats appends and skill-file reads ran synchronously on the
  event loop. StatsStore now enqueues to a single-worker FIFO executor
  (order preserved, `read_events` flushes first); skill rendering moved to
  `asyncio.to_thread` at both dispatch sites.
  - Rejected: queue + hand-rolled daemon thread — the one-worker executor
    gives the same non-blocking behavior with exact flush semantics for free.
- **HIGH** — a YAML typo in hand-edited WORKFLOW.md became an unlogged 500;
  now a 400 carrying the ruamel parse message.
- **MEDIUM** — Host allowlist extended to GET (DNS-rebinding reads); omitted
  column descriptions preserved on PUT (None=keep, ""=clear); unstable
  stats-store singleton key; per-request catch-all logging.
- Deliberately NOT fixed: stats.jsonl rotation (greppable local file, small
  at realistic ticket volume — revisit if a board ever exceeds ~100k events).

SPA JS review (verdict: XSS clean; two HIGH fixed):
- **HIGH** — drawer state/priority/agent/skills controls kept showing a
  failed edit; every field now reverts to the last saved value on PATCH
  failure, matching title/labels.
- **HIGH** — the 5s poll re-rendered the board mid-drag, removing the drag
  source node and silently cancelling the HTML5 drag. The poll now always
  fetches (so the connection dot stays truthful even while a form is
  focused — the review's MEDIUM) but holds DOM updates while an overlay
  input is focused or a `.dragging` card exists.
- **MEDIUM** — labels lowercase client-side to mirror server normalization.
- **LOW** — dead `_key`/`nextWfKey`/`pollTimer` removed; markdown links get
  `noreferrer`.

---

# 2026-07-02 — 4-stage pipeline simplification

## Goal

Collapse the default agent pipeline from many operator-visible lanes into four
active states: `Todo`, `In Progress`, `Verify`, and `Learn`. Keep the quality
gates, but move them inside fewer stages so the board is easier to scan.

## Decisions

### 1. Merge stages, not just UI groups

`Plan` and `Critic` are now sections inside `In Progress`; `Review`, `QA`, and
Merge Gate are sections inside `Verify`. The contract validator follows the
same state names, so the UI, prompt, and state machine describe one real
workflow.

- Rejected: grouping old lanes visually while keeping old state names. That
  would make the web/TUI simpler but leave retries, contracts, and prompts
  harder to explain.
- Rejected: skipping Verify for trivial tickets. Verify owns Merge Gate, so
  skipping it risks shipping unmerged or unreviewed work.

### 2. Keep Learn lightweight and skippable

Learn is now only wiki write-back and Human Review handoff. Operators can skip
it from the TUI (`S`) or web/API, which appends `## Learn Skipped` and moves the
ticket to `Human Review` without starting an agent turn.

- Rejected: deleting Learn. The wiki write-back is still useful when work
  produces durable repository knowledge.
- Rejected: letting running Learn workers be skipped. The skip action refuses
  running tickets to avoid racing worker-owned ticket writes.

### 3. Remove skills UI but preserve the engine

The web nav/page, web create/detail controls, and TUI create/detail controls no
longer expose skills. Existing `skills:` ticket frontmatter still round-trips
through the backend and prompt injection remains intact for power users.

- Rejected: deleting `skills.py` and `Issue.skills`. That would break existing
  boards and remove a useful advanced feature.

### 4. Move demos under `examples/`

The repo root now keeps the current workflow examples; demo/smoke/Jira files
and demo boards live under `examples/`. Moved workflows use `../docs/...`
prompt/wiki paths because workflow-relative resolution changes after the move.

### 5. Keep tracked fixtures on the new defaults

Docs-only test fixtures now point at the retained `verify.md` prompt instead of
removed stage filenames. That keeps tracked fixtures from teaching removed
default lanes while preserving custom-state tests that intentionally exercise
arbitrary board names.

### 6. Make the web board default to active work

The web board now opens on the four active lanes only. Non-empty terminal lanes
render as a compact terminal group, and the `All` toggle expands every
configured column when an operator needs full board surgery.

- Rejected: deleting terminal states from `/api/v1/board`. They are still
  workflow data and are needed for scripts, stats, and manual recovery.
- Rejected: always showing `Human Review` as a fifth full lane. It is operator
  work, not agent pipeline work, so the compact group keeps the 4-stage board
  promise while leaving review cards visible.

### 7. Preserve exhausted turn-budget guards across polls

Full E2E exposed a redispatch loop: a Learn ticket that hit
`max_total_turns` was marked exhausted, then the next stale-claim prune removed
that guard and dispatched the same ticket again. `_claimed` still prunes when no
worker owns the ticket, but `_turn_budget_exhausted` now remains until process
restart or explicit operator movement.

- Rejected: making the E2E harness tolerate repeated dispatch. The loop burns
  agent turns and hides a real scheduler bug.
- Rejected: moving every exhausted ticket to `Blocked` by default. Existing
  boards rely on `agent.budget_exhausted_state` as an opt-in persistence policy.

## Breaking Change

Boards with custom active states or prompt mappings that still use `Explore`,
`Plan`, `Critic`, `Review`, or `QA` need a manual `WORKFLOW.md` migration to the
4-stage layout before adopting the new prompt templates.

---

# 2026-07-02 - post-E2E hardening research plan

## Decision

Create `docs/plans/2026-07-02-post-e2e-hardening-plan.md` as a follow-up plan,
not implementation, because the 4-stage branch is already committed and pushed.
The plan prioritizes tracked browser E2E and API smoke coverage first, then
budget-exhausted UI, mobile lane controls, and terminal-state wording.

- Rejected: folding these follow-ups into the shipped 4-stage commit. That would
  obscure the already-verified pipeline migration.
- Rejected: making browser E2E mandatory in default `pytest`. Browser binaries
  and local sandbox behavior are too environment-sensitive for every developer
  run.

---

# 2026-07-02 - post-E2E hardening implementation

## Decision

Ship the plan as additive hardening: optional Python Playwright E2E, a stdlib
live API smoke script, budget-exhausted attention payloads, mobile active-lane
tabs, and clearer terminal-state wording.

- Rejected: parsing every smoke response as JSON. `/static/app.js` is plain
  JavaScript, so the smoke helper now falls back to raw text for non-JSON
  responses.
- Rejected: applying grid-only mobile CSS to the board. The board uses flex
  columns, so the mobile lane behavior changes rendered columns and flex sizing
  directly while leaving `All` as the full editable board.
- Rejected: duplicating README board text. The existing active-lane sentences
  were refined in English and Korean to name **Review and parked**.

---

# 2026-07-02 - crash-safe orchestrator state and leases

## Decision

Add a SQLite WAL run registry at `.symphony/state.db` and use it as a
single-node dispatch lease before worker task creation. `_running` remains the
live in-process source for task handles, but a fresh orchestrator now refuses
to dispatch a ticket while an unexpired persisted lease exists. Active workers
heartbeat their lease on poll/progress, and worker exit or force-eject marks
the run terminal.

- Rejected: starting with multi-node HA. Symphony still uses local worktrees,
  local hooks, and file-backed tickets, so a single-node crash-safe lease closes
  the immediate duplicate-dispatch failure without pretending the whole system
  is distributed-safe.
- Rejected: tracker write locks in the same patch. Board read-modify-write
  locking is still needed, but mixing it with worker leases would make two
  independent concurrency models harder to verify.
- Rejected: JSONL run state. The registry needs "claim if no active lease"
  semantics; SQLite `BEGIN IMMEDIATE` plus WAL gives that atomically with no new
  dependency.
- Not implemented yet: reattaching live workers after restart. Current workers
  are in-process asyncio tasks, so a hard process crash leaves no task to
  reattach. The registry records run/workspace metadata so a later recovery
  slice can decide whether to reclaim a worktree or resume an external backend.

## Verification

- `PYTHONPATH=src pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'run_registry or persisted_lease or worker_exit_releases_persisted_lease'` failed before implementation on missing `symphony.orchestrator.run_registry`.
- `.venv/bin/python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py -k 'run_registry or persisted_lease or worker_exit_releases_persisted_lease'` -> 5 passed, 89 deselected.
- `.venv/bin/python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py` -> 94 passed.
- `.venv/bin/symphony doctor ./WORKFLOW.md` -> all PASS.
- `.venv/bin/python -m pytest -q` -> 871 passed, 2 skipped.

---

# 2026-07-02 - R2/R7 backend lifecycle hardening

## Decision

Finish the WIP backend lifecycle slice by treating the subprocess group as the
shutdown unit for every agent backend. `stop()` still reaps through
`symphony._shell.safe_proc_wait`, but the first signal is now process-group
SIGTERM so the real agent CLI behind `bash -lc` does not keep running after the
wrapper exits. Codex completion waiters failed by stdout EOF now emit
`turn_failed` before re-raising, so crashes are visible immediately instead of
silently bypassing event handling.

Force-eject now consumes the generic backend `agent_pid` event field as the
recorded process id and calls `kill_process_group` before scheduling the retry.
That closes the zombie-worker case where the asyncio task is stuck on a
non-cancellable await and `backend.stop()` will never run.

- Rejected: restoring per-backend `safe_proc_wait` imports only to satisfy old
  tests. The shell helper owns the process-tree contract, so tests patch
  `symphony._shell.safe_proc_wait` and the signal helper at the root.
- Rejected: changing `BaseAgentBackend.is_progress_event` to False. The base
  default stays conservatively True as the existing regression guard records;
  Pi and Gemini document their stricter backend-specific predicates instead.
- Deferred: bounding Gemini `.read()` memory use. It is a real LOW-MED concern
  but independent of the process-leak and EOF-fast-fail lifecycle slice.

## Verification

- `.venv/bin/python -m pytest -q tests/test_backends.py tests/test_orchestrator_dispatch.py -k 'stop_reaps_with_safe_proc_wait or backend_is_progress_event_defaults_to_true or force_eject'` -> 5 failed, 1 passed before the test-contract update.
- `.venv/bin/python -m pytest -q tests/test_backends.py tests/test_backends_lifecycle.py tests/test_orchestrator_dispatch.py -k 'stop_reaps_with_safe_proc_wait or backend_is_progress_event or start_new_session or terminate_process_tree or completion_waiter or malformed or post_stream or force_eject or records_backend_agent_pid'` -> 18 passed, 166 deselected.
- `.venv/bin/python -m pytest -q tests/test_backends.py tests/test_backends_lifecycle.py tests/test_orchestrator_dispatch.py` -> 184 passed.
- `.venv/bin/python -m pytest -q` -> 907 passed, 2 skipped, 1 documented bootstrap failure (`server.port=9999` in use).
- `.venv/bin/python -m pytest -q -k 'not test_bootstrap_creates_vault_skeleton'` -> 907 passed, 2 skipped, 1 deselected.
- `.venv/bin/symphony doctor ./WORKFLOW.md` -> blocked in this sandbox: port 9999 is already in use, and `/Users/danny/symphony_workspaces` is outside the writable roots. An escalated rerun was rejected by policy.

---

# 2026-07-02 - OneShot bootstrap local-port hardening

## Decision

Make OneShot bootstrap own the local API port selection instead of assuming the
operator can bind `9999`. Bootstrap now uses `9999` when it is free,
auto-selects an available localhost port when the default is occupied, supports
`SYMPHONY_ONESHOT_PORT=auto`, and rejects an explicitly requested occupied
`SYMPHONY_ONESHOT_PORT=<port>` before writing `.oneshot/` or `WORKFLOW.md`.

The selected port is substituted into generated `WORKFLOW.md` and
`.oneshot/SYSTEM.md`, and the operator docs now read the generated port before
launching or polling the API.

- Rejected: killing the process that owns `9999`. Bootstrap should not destroy
  unrelated operator state just to make tests pass.
- Rejected: changing the repository's checked-in `WORKFLOW.md` port. That is a
  local operator configuration, while the failing contract was the generated
  OneShot workflow.
- Rejected: deselecting the bootstrap test in the final suite. The root issue
  was deterministic enough to fix in the generator.

## Verification

- `bash -n skills/symphony-oneshot/templates/bootstrap.sh` -> pass.
- `.venv/bin/python -m pytest -q tests/skills/test_symphony_oneshot_bootstrap.py` -> 3 passed, 1 skipped.
- `.venv/bin/python -m pytest -q` -> 909 passed, 2 skipped, 1 warning.
- `git diff --check` -> pass.
- `.venv/bin/symphony doctor ./WORKFLOW.md` -> still blocked by the live
  operator environment: `127.0.0.1:9999` is occupied, and the sandbox cannot
  write `/Users/danny/symphony_workspaces`. The required escalated rerun was
  rejected by policy.
- `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` -> 1 skipped because Playwright Chromium is not installed. The install command was requested and rejected by policy.

---

# 2026-07-02 - R4 classified tracker retries

## Decision

Add a shared tracker retry helper and route Jira `_request` plus Linear `_post`
through it. The helper retries only transport failures and HTTP
`429/500/502/503/504`, preserves existing tracker-specific exception types,
honors numeric `Retry-After` capped at 30 seconds, and returns non-retryable
responses to the caller's existing status handling.

Jira and Linear pagination now stop after `MAX_PAGES=20` and log a warning
instead of looping forever when an API keeps returning a next-page marker.
Tracker HTTP timeout is now configurable as
`tracker.network_timeout_seconds` with a default of `30.0`.

- Rejected: wrapping all tracker failures in a new shared exception. Existing
  callers and tests rely on `JiraApiStatusError`, `LinearApiStatusError`, and
  the request-error variants.
- Rejected: retrying all non-2xx statuses. `400/401/403/404` are validation or
  auth problems and should fail immediately.
- Rejected: making the helper async. Both clients are synchronous and already
  run in executor threads, so sync `time.sleep` keeps the change small and
  consistent with the current architecture.
- Deferred: worker-outcome deterministic/transient classification. The
  handoff's R4 tracker slice is now complete; backend outcome taxonomy belongs
  with the later operator-attention and retry-state work.

## Verification

- `.venv/bin/python -m pytest -q tests/test_tracker_jira.py tests/test_tracker_jira_edges.py tests/test_tracker_linear_full.py tests/test_tracker_linear_archive.py tests/test_workflow.py` -> 126 passed.
- `.venv/bin/python -m pytest -q` -> 922 passed, 2 skipped, 1 warning.
- `.venv/bin/symphony doctor ./WORKFLOW.md` -> still blocked by live operator
  environment: `127.0.0.1:9999` is occupied, and the sandbox cannot write
  `/Users/danny/symphony_workspaces`.
- `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` -> 1 skipped because Playwright Chromium is not installed; installed Chrome channel also aborts under the sandbox.

---

# 2026-07-02 - R5 file tracker locking and no-write reads

## Decision

Add file-board write serialization without changing the Markdown ticket format.
Generated ticket creation now goes through
`FileBoardTracker.create_with_next_identifier()`, which holds
`.locks/allocator.lock` across ID scan and file creation. Web API and TUI
generated-create paths use that API instead of `next_identifier()` followed by
`create()`.

Every file-tracker read-modify-write mutation now runs through a per-ticket
lockfile under `.locks/<identifier>.lock`. The lock is separate from the
ticket file because writes use `os.replace()`, which swaps the ticket inode and
would weaken a lock held directly on the `.md` file. Before writing, the helper
re-reads the ticket and re-applies the mutation when `(updated_at, mtime_ns)`
moved.

`parse_ticket_file()` still returns a healed in-memory view for Markdown
accidentally inserted into frontmatter, but it no longer persists that repair
during a read.

- Rejected: locking only `next_identifier()`. The race is between ID selection
  and file creation, so the public generated-create operation must be atomic.
- Rejected: keeping the web API collision-retry loop as the only guard. Retry
  hides collisions after they happen; the tracker should own allocation.
- Rejected: flocking the ticket `.md` file directly. Atomic replace changes
  the inode, so a stable lockfile is the safer coordination point.
- Rejected: preserving write-on-read auto-heal. A board refresh should not
  dirty the user's worktree or race with an agent edit.
- Deferred: native Windows locking. POSIX uses `fcntl`; non-POSIX falls back
  to the previous behavior and is documented as residual risk.

## Verification

- `.venv/bin/python -m pytest -q tests/test_tracker_file.py::test_parse_ticket_file_auto_heals_markdown_inside_front_matter` -> failed before implementation because parse rewrote the file; now covered in the file-tracker suite.
- `.venv/bin/python -m pytest -q tests/test_tracker_file.py::test_create_with_next_identifier_is_unique_under_concurrent_calls` -> failed before implementation because the atomic generated-create API did not exist; now passes.
- `.venv/bin/python -m pytest -q tests/test_tracker_file.py::test_append_note_preserves_concurrent_writes` -> failed before locking with only one concurrent note surviving; now passes.
- `.venv/bin/python -m pytest -q tests/test_tracker_file.py` -> 35 passed.
- `.venv/bin/python -m pytest -q tests/test_tracker_file.py tests/test_webapi.py` -> 54 passed.
- `.venv/bin/python -m compileall -q src/symphony/trackers/file.py src/symphony/webapi.py src/symphony/tui/app.py` -> pass.
- `.venv/bin/python -m pytest -q` -> 926 passed, 2 skipped, 1 warning.
- `git diff --check` -> pass.
- `.venv/bin/symphony doctor ./WORKFLOW.md` -> still blocked by the live
  operator environment: `127.0.0.1:9999` is occupied, and the sandbox cannot
  write `/Users/danny/symphony_workspaces`.
- `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` -> 1 skipped because Playwright Chromium is not installed.
- Delivery proof: `docs/changelog/2026-07-02-r5-file-tracker-locking-delivery-proof.md`.

---

# 2026-07-02 - R6 persisted safety valves

## Decision

Persist the orchestrator's crash-sensitive per-issue guards in the existing
`.symphony/state.db` run registry. `issue_flags` stores `retry_attempt`,
`budget_exhausted`, `paused`, and `updated_at` by issue id. Startup rehydrates
those flags before dispatch eligibility runs, and runtime paths write through
via `_registry_guard` when scheduling retries, marking a budget exhaustion, or
pausing/resuming a worker.

Retry attempts are cleared on clean worker exit and when a continuation retry
is scheduled, so a successful retry does not poison the next restart. Budget
exhaustion clears the retry attempt because the budget guard supersedes retry
backoff.

- Rejected: a separate JSON sidecar. The run registry already owns crash-safe
  orchestrator state and has the existing busy-timeout/guard behavior.
- Rejected: persisting pause in worker lease rows. Pauses survive worker exit,
  so tying them to a particular active lease would drop the operator's hold
  exactly when a retry is being parked.
- Rejected: adding a new public resume path for budget exhaustion in this
  slice. Existing behavior is an operator stop condition; R6 only preserves
  that meaning across restart.

## Verification

- `.venv/bin/python -m pytest -q tests/test_run_registry.py::test_run_registry_persists_issue_flags_across_reopen tests/test_run_registry.py::test_run_registry_clears_issue_flags_independently tests/test_orchestrator_dispatch.py::test_persisted_issue_flags_block_dispatch_after_restart tests/test_orchestrator_dispatch.py::test_persisted_retry_attempt_drives_next_dispatch_and_cap tests/test_orchestrator_dispatch.py::test_pause_resume_write_through_issue_flags tests/test_orchestrator_dispatch.py::test_retry_schedule_write_through_and_continuation_clears_issue_flag tests/test_orchestrator_dispatch.py::test_total_turn_budget_exhaustion_write_through_issue_flags` -> 7 passed.
- `.venv/bin/python -m pytest -q tests/test_run_registry.py tests/test_orchestrator_dispatch.py` -> 105 passed.
- `.venv/bin/python -m pytest -q` -> 933 passed, 2 skipped, 1 warning.
- `git diff --check` -> pass.
- `SYMPHONY_BROWSER_E2E=1 .venv/bin/python -m pytest tests/test_web_browser_e2e.py -q -rs` -> 1 passed after installing Playwright Chromium and running outside the sandbox.
- `.venv/bin/symphony doctor ./WORKFLOW.md` -> all PASS after stopping the stale local Symphony service that had been occupying port 9999.

---

# 2026-07-02 - reliability/availability/usability system-design plan

## Decision

Write `docs/plans/2026-07-02-reliability-availability-usability.md` from a
same-day three-way code audit (orchestrator reliability; backend/tracker
resilience; availability + usability surface). Sequenced into five phases:
stop silent halts (tick-loop supervision, lease owner identity, health
endpoint) → stop process leaks (process-group kill, codex EOF fast-fail) →
external-call resilience (classified tracker retries, reconcile isolation) →
durable state and board write locking → operator experience (attention
taxonomy, doctor v2, run-history surface).

- Rejected: multi-node HA, DB-backed board, in-process worker reattachment,
  and a separate always-up status server — all conflict with the single-node
  file-first ethos; rationale recorded in the plan's §8.

## fix(web): prompt-editor textarea collapsed to intrinsic width

`openPromptEditorModal` cleared the swap wrapper's class after load, so the
textarea sat in a plain block div outside the `.prompt-modal-content` flex
column and collapsed to its intrinsic ~20-col width (198px in a 760px modal),
leaving the modal's right side empty. The wrapper now keeps a
`.prompt-editor-body` flex-column class and `.prompt-textarea` gains
`width: 100%` as a guard.

- Rejected: widening the modal (`modal-xl`) — the root cause was the broken
  flex chain, not modal width; widening is listed as a follow-up candidate.
- Verified: headless Chrome against the browser-E2E server fixture — textarea
  bounding box 198px → 720px; `tests/test_web_static_contract.py` +
  `tests/test_webapi.py` -> 20 passed.
