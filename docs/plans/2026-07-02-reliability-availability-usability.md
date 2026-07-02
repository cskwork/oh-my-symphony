# Symphony Reliability / Availability / Usability Improvement Plan

**Date:** 2026-07-02 · **Baseline:** v0.9.0, `feat/web-kanban-revamp` @ `0131325`
**Scope:** system-design plan; no code in this document. Evidence comes from a
same-day code audit of `orchestrator/`, `backends/`, `trackers/`, `server.py`,
`webapi.py`, `service.py`, `cli/`, and `web/static/`.

## 1. Problem statement

Symphony is a single-node, file-first multi-agent orchestrator. The recent
crash-safe run-lease slice (`347379b`) closed duplicate dispatch across
restarts, but the audit shows the remaining failure surface clusters in four
places:

1. **Unsupervised in-process state.** One unhandled exception in the tick loop
   halts all orchestration silently; most safety valves (retry counts, budget
   guards, pause flags) live only in memory.
2. **Unbounded/unclassified external interactions.** Tracker HTTP calls have
   zero retry/backoff; backend kills signal only the `bash` wrapper, orphaning
   the real agent CLI; several waits have no timeout.
3. **No health surface.** Operators (and the SPA) cannot distinguish "idle and
   healthy", "tick loop dead", and "workflow file broken".
4. **Stuck-work opacity.** The system computes why a ticket is stalled or
   retrying but does not show it in the web UI, and the run-history table has
   no reader.

## 2. Design principles

- **Stay single-node and file-first.** No HA, no external services. Reliability
  comes from crash-safety, supervision, and graceful degradation — consistent
  with the rejected-alternatives record in `docs/changelog/changelog-2026-07-02.md`.
- **Markdown ticket = source of truth; `state.db` = orchestration ledger.**
  Durable safety state (leases, attempts, budget flags) belongs in the SQLite
  registry; human-meaningful state stays in the ticket file.
- **Every async task supervised; every subprocess in a process group; every
  external call bounded** (timeout + classified retry).
- **Small verifiable slices.** Each item ships with a failing test first and an
  explicit acceptance check; no cross-cutting rewrite.

## 3. Workstream R — Reliability

### R1. Supervise the tick loop (top priority)
`_tick_loop` awaits `_on_tick()` with no exception guard and `_tick_task` has
no done-callback (`orchestrator/core.py:854-864`). Any unhandled error —
including a RunRegistry SQLite error surfacing through `_has_active_run_lease`
or `acquire_run` — permanently kills orchestration while the process keeps
serving HTTP, looking alive.
- Wrap each tick in a guard: log structured error, increment a crash counter,
  continue with backoff; after N consecutive failures mark the orchestrator
  `degraded` (consumed by A1) instead of dying silently.
- Add a done-callback on `_tick_task` that logs and restarts (bounded).
- Acceptance: injected `_on_tick` exception → next tick still runs, health
  endpoint reports `tick_errors > 0`.

### R2. Process-group termination for backends
All four backends spawn `bash -lc <cli>` without `start_new_session`
(`backends/claude_code.py:171-181` and siblings), so `terminate()/kill()`
signal only the wrapper — the agent CLI and its children keep running and
burning tokens. `service.py:344-401` already shows the correct
`start_new_session=True` + `os.killpg` pattern.
- Apply the same pattern in the shared spawn path; escalate
  `killpg(SIGTERM)` → `safe_proc_wait(2s)` → `killpg(SIGKILL)`.
- `_force_eject_zombie` must kill the recorded child PID before scheduling a
  retry into the same workspace (`core.py:3003-3031`) — today the zombie and
  the retry worker can write the same worktree concurrently.
- Acceptance: kill test with a child-spawning fake CLI → no surviving
  descendants after stop/force-eject.

### R3. Lease hardening (slice 2 of crash-safe state)
- **Owner identity:** add `owner_pid` + boot nonce to lease rows. On startup,
  reclaim self-owned dead leases immediately instead of waiting up to the full
  5-min TTL (`run_registry.py:169-179` only expires by timestamp today).
- **Lost-lease detection:** `heartbeat`/`complete_run` already return
  `rowcount > 0` but callers ignore it (`core.py:210-238`). On a failed
  heartbeat, flag the run and stop the worker rather than running leaseless.
- **Event-loop isolation:** run SQLite ops via `asyncio.to_thread` with
  guarded exceptions; a locked DB must degrade one tick, not stall or kill
  the loop (couples with R1).

### R4. Classified retries for tracker HTTP
Jira/Linear clients are single-shot: any 429/5xx/DNS blip aborts a state
transition (`trackers/linear.py:384-405`, `trackers/jira.py:351-381`); no
`Retry-After`, no backoff, and the orchestrator does not wrap the calls.
- Add a small shared retry helper: max 3 attempts, exponential backoff +
  jitter, honor `Retry-After`, retry only idempotent reads and 429/502/503/
  timeout classes; 4xx auth/validation fails fast.
- Make `NETWORK_TIMEOUT_SECONDS` (30s hardcoded) and pagination aggregate cap
  configurable; today N pages = N × 30s worst case on a blocking executor.
- Classify worker outcomes too: `outcome="error"` currently treats a bad
  config identically to a transient crash (`core.py:2121-2133`); deterministic
  failures should escalate immediately instead of consuming the retry budget.

### R5. Board write serialization (lease changelog follow-up d2)
`write_ticket_atomic` makes each write atomic, but read-modify-write is
unserialized across ≥5 in-process writers plus the agent subprocess itself
(`trackers/file.py`, callers in `core.py:1052`, `tui/app.py:642`,
`webapi.py:432`, `cli/board.py:151`). `next_identifier` is a TOCTOU race
(`file.py:523-531`) — webapi already carries a collision-retry workaround.
- Recommended: per-ticket `fcntl` advisory lock around read-modify-write +
  an `updated_at` compare-and-swap (reject-and-reread on mismatch) to cover
  the agent-CLI writer that cannot share an in-process lock.
- Alternative considered: single-writer queue in the orchestrator — rejected
  because the agent subprocess and CLI write the same files out-of-process.
- Move ID allocation under the same lock (or a counter in `state.db`).
- Also remove the write-on-read surprise: `_auto_heal_markdown_in_front_matter`
  writes during a read path (`file.py:168`).

### R6. Persist safety valves across restarts
In-memory-only today: retry attempt counters, `_turn_budget_exhausted`,
`_paused_issue_ids`, backoff timers (`core.py:117-148` region). A crash resets
retry caps (retry storms), re-runs budget-exhausted tickets, and silently
un-pauses paused work.
- Store attempts / budget-exhausted / paused flags in the run registry keyed
  by issue; rehydrate on startup. Ticket markdown stays clean of this
  machine state.

### R7. Backend stream robustness
- Codex crash mid-turn currently blocks for the full `turn_timeout_ms` and is
  misreported as `TurnTimeout` — EOF fails only `_pending` futures, never the
  turn-completion waiter (`backends/codex.py:638-641` vs `534-540`). Resolve
  the waiter on process exit; report `TurnFailed(rc, stderr tail)`.
- Bound the post-stream `safe_proc_wait(proc)` calls that have no timeout
  (`claude_code.py:211`, `pi.py:213`) with the standard 2s → kill escalation.
- Remove the always-True `is_progress_event` default for pi/gemini
  (`core.py:2472-2490`): a keepalive-emitting backend can never stall out.
  Each backend must declare its real progress predicate (the claude fix
  `499e787` already did this for one backend; generalize it).
- After N consecutive malformed JSON lines, fail the turn with a parse error
  instead of degrading to a generic "no terminal event" (gemini's strict mode
  is the model).

### R8. Reconcile isolation
A tracker fetch failure aborts reconciliation for the whole tick
(`core.py:3321-3323`), so a remotely-Done ticket keeps its slot; the
"neither active nor terminal" drift path bare-cancels with no workspace
cleanup (`core.py:3421-3429`). Isolate per-issue failures; give the drift
path the same cleanup as terminal reconciliation. Fix
`_escalate_max_retries` clearing `_claimed`/`_retry` in `finally` even when
the tracker update raised (`core.py:3176-3185`) — the ticket ends up neither
escalated nor retried.

## 4. Workstream A — Availability

### A1. Health/readiness endpoint + degraded snapshot
No `/health` exists; `/api/v1/state` has no liveness signal (`server.py:140-151`).
- Add `GET /api/v1/health`: `last_tick_at`, tick-loop alive/degraded (from R1),
  consecutive tracker failures, registry status, version. Cheap, no heavy work.
- Extend `snapshot()` with the same degraded flags so TUI/SPA/scripts share
  one truth.

### A2. Graceful startup on the direct run path
`symphony ./WORKFLOW.md --port` calls `site.start()` unguarded → raw
`OSError: address in use` traceback after the orchestrator already started
(`cli/main.py:155`, `server.py:170-171`); doctor runs only in the service
path. Guard the bind with a friendly, actionable error; run a doctor-lite
preflight (port, agent CLI on PATH, prompt-file existence) before starting
the orchestrator.

### A3. SPA degraded-state truthfulness
The connection dot conflates transport-down with app-level 4xx/5xx: a broken
WORKFLOW.md hot-reload returns HTTP 400 and the board claims "Orchestrator
unreachable" while the server is fine (`app.js:703-711`, `webapi.py:143-149`).
- Distinguish three states: connected / server-up-but-workflow-broken (show
  the 400 message) / unreachable. Add a data-staleness timestamp and gentle
  backoff with a manual retry button on the 5s poll (`app.js:1656-1681`).

### A4. Restart/resume slice 3 — adopt or clean
Restart today re-dispatches interrupted tickets from turn zero and never
consults `active_leases()`; mid-run workspaces are untouched
(`core.py:3495+` handles terminal states only). Add a startup pass: for each
lease left by a dead owner, decide **reclaim** (workspace exists and clean →
reuse worktree, restart current stage) or **clean** (remove worktree,
release lease). Markdown-as-checkpoint stays the resume mechanism — this
aligns with the existing phase-boundary fresh-handoff design; do not attempt
in-process task reattachment.

### A5. Single-instance enforcement
Leases stop same-issue double dispatch but not slot overcommit: two
orchestrators on one workflow allow 2× `max_concurrent_agents`
(`core.py:1185-1193`); the service lock is held only during start/stop
(`service.py:88-105`). Hold an exclusive `flock` on a workflow-scoped
lockfile for the orchestrator's lifetime; a second instance exits with a
clear "already running (pid N, port P)" message.

## 5. Workstream U — Usability

### U1. Stuck-ticket explainability (biggest operator win)
The backend already computes `last_error`, retry reason, and next-attempt
time (`core.py:627-638`) but the web drawer's Live-run panel shows only
Status/Turn/Tokens/Last event (`app.js:1238-1256`), and `issue_attention`
knows exactly one cause (`budget_exhausted`, `core.py:451-459`).
- Extend the attention taxonomy: `stalled`, `retry_scheduled(due_at, reason)`,
  `lease_blocked`, `tracker_error`, `budget_exhausted`.
- Render all of it in the drawer + as card badges; TUI already shows retry
  badges, so this closes the web/TUI gap.

### U2. Doctor v2
Today doctor never checks prompt-file existence, agent auth (only `pi`), or
tracker connectivity (key presence only), and its port check false-FAILs
against your own running service (`cli/doctor.py:63-67, 113-134, 186-201`).
- Add: `prompts.base`/`prompts.stages.*` existence, auth probes for
  codex/claude/gemini (cheap `--version`/whoami equivalents), an optional
  live tracker ping, and "port busy but owned by this workflow's service"
  detection with a distinct message.

### U3. Error-message parity on the headless path
`workflow_load_failed` / `startup_failed` print as key=value log events
(`cli/main.py:101-123`), while the service path prints sentences. Map the
run-path failures to the same friendly, actionable wording (what failed,
why, what to run next).

### U4. Run-history surface
The `runs` table is a real audit trail with zero readers
(`run_registry.py:124-141`). Add `symphony runs [--issue ID]`,
`GET /api/v1/runs`, and a small "History" section in the issue drawer
(attempts, outcomes, durations, errors). Read-only; no schema change.

### U5. Onboarding sharp edges
- One canonical quickstart (file tracker) — today two example workflows
  invite copying the Linear one and stalling on an API key.
- The shipped `after_create` placeholder (`my-org/my-repo`) makes every
  dispatch fail rc=128 unless doctor was run; detect the placeholder at
  startup preflight (A2) and warn loudly, not only in doctor.
- Trigger an immediate first poll on start so first feedback is seconds, not
  the 30s poll interval; print a "board ready at http://…" line.
- TUI without a TTY should exit with an explanatory message instead of
  silently dying.

### U6. Web polish (rolling)
- Done today: prompt-editor textarea collapsed to ~200px leaving the modal
  right side empty — swap wrapper lost the flex column; fixed and verified
  with headless Chrome (`0131325`, before 198px → after 720px).
- Candidates behind it: wider (`modal-xl`) prompt editor on large screens,
  keyboard save (Cmd/Ctrl+S) in the editor, drawer retry/pause affordances
  once U1 lands.

## 6. Sequencing

| Phase | Items | Rationale |
|-------|-------|-----------|
| 1. Stop silent halts | R1, R3 (owner+rowcount), A1 | Tick-loop SPOF and invisible death are the worst failure modes; health endpoint makes everything after this observable. |
| 2. Stop process leaks | R2, R7 | Orphaned agent CLIs burn tokens and corrupt worktrees; codex crash-as-timeout wastes whole stall windows. |
| 3. External-call resilience | R4, R8, A2 | Classified retries + reconcile isolation make tracker blips non-events. |
| 4. Durable state + writes | R5, R6, A4, A5 | Locking and persisted safety valves; adopt-or-clean restart completes the crash-safety story. |
| 5. Operator experience | U1–U5, A3 | Explainability and onboarding, built on the health/attention data from earlier phases. |

Each item: failing test first (`tests/` has established fault-injection
patterns), then fix, then `pytest -q` full suite; phases 2 and 4 additionally
get a launcher smoke against a real board per the run-path verification rule.

## 7. Measurement

Derive from the existing `stats.jsonl` + run registry (no new infra):
run success rate, stall-eject rate, retry rate per outcome class, restart
recovery time (lease reclaim → first dispatch), and "tick gap" (max seconds
between ticks — the R1/A1 health signal). Track before/after per phase in
`docs/changelog/`.

## 8. Rejected alternatives

- **Multi-node HA / distributed queue** — Symphony is deliberately local
  (worktrees, hooks, file boards); already rejected in the lease slice.
- **Move the board to SQLite/Postgres** — markdown tickets are the product's
  ethos and the agent-writable contract; we serialize writers instead.
- **In-process worker reattachment after crash** — asyncio tasks cannot be
  reattached; adopt-or-clean at stage granularity (A4) gets the value with
  none of the false promises.
- **Separate always-up status webserver** — a second process to babysit
  contradicts single-command operation; A1's health endpoint plus truthful
  SPA states (A3) covers the practical need.
