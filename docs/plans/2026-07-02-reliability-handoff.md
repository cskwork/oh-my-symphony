# Reliability plan — implementation handoff

**Date:** 2026-07-02 · **Branch:** `feat/reliability-hardening` (local only, branched from `feat/web-kanban-revamp`)
**Master plan:** `docs/plans/2026-07-02-reliability-availability-usability.md` (read it first — item ids R1..R8, A1..A5, U1..U6 used below refer to it)
**Suite baseline:** `.venv/bin/python -m pytest -q` → 903 passed at `cb943a0`, EXCEPT:
- `tests/skills/test_symphony_oneshot_bootstrap.py::test_bootstrap_creates_vault_skeleton` — pre-existing, environment-dependent (bootstrap.sh runs real `symphony doctor`, which fails on this machine); also fails on a clean tree. Ignore.
- 5 known-red backend tests after `70dbc75` (see "In-flight" below).

## How to work (non-negotiable)

- TDD: failing test first, then fix, then targeted tests, full suite before each commit.
- Conventional commits, one plan item (or coherent pair) per commit. Commit early and often — this user's environment occasionally loses unstaged changes.
- Use `monkeypatch.setattr` for ALL stubbing. Module-level function stubs leak across this suite and cause far-away failures.
- No emojis anywhere. Comments only where reasoning isn't obvious.
- Decisions + rejected alternatives go in `docs/changelog/changelog-2026-07-02.md` (append; the file exists).
- Version bump waits until the end (task 19): pyproject.toml AND `src/symphony/__init__.py` in lockstep, its own `chore` commit. This work is feature-level → 0.9.0 → 0.10.0.

### Repo invariants (violating these = rejection)

1. NEVER call raw `proc.wait()` — always `symphony._shell.safe_proc_wait` (asyncio child-watcher hang, Textual+3.12 macOS).
2. `Orchestrator._on_worker_task_done` task-identity check; `_available_slots` subtracts `_running` AND `_retry`; `_reconcile_running` keeps the 30s two-stage cancel→force-eject grace; `last_progress_timestamp` only advances on progress-classified events.
3. Monkeypatch indirection: `orchestrator/core.py` reaches `build_backend` / `commit_workspace_on_done` / `auto_merge_on_done_best_effort` via `_pkg.<name>`; import order in `orchestrator/__init__.py` must keep those bound before `from .core import Orchestrator`. Same pattern `_tui_pkg` in `tui/app.py`. See `docs/architecture.md`.
4. Prompt templates carry byte-exact anchors pinned by `tests/test_workflow_pipeline_prompt.py` — don't touch prompt md files without reading that test.
5. `tests/test_web_static_contract.py` pins strings in `web/static/app.js` / `style.css` — check it when editing the SPA.

## Done (committed, verified, do not redo)

| commit | item | summary |
|--------|------|---------|
| `0131325` | U6 | prompt-editor textarea collapsed to ~200px — swap wrapper `.prompt-editor-body` flex fix + `width:100%`; verified headless Chrome 198→720px |
| `b337fc2` | — | master plan doc + changelog |
| `7c67e93` | R1+R3+A1 | tick-loop supervision (per-tick guard, bounded backoff, done-callback restart ≤3 via `_spawn_tick_loop`/`_on_tick_task_done`); lease owner identity (`owner_pid`+`owner_boot_id` columns, in-place migration, `reclaim_dead_owner_leases` → status `orphaned`, called from `_ensure_run_registry`); heartbeat rowcount handling (re-acquire on lost lease, conflict → cancel worker via `_heartbeat_running_leases`, `RunningEntry.lease_lost`); `_registry_guard` fail-open wrapper (+ sqlite busy timeout 30→5s); `Orchestrator.health()` + `GET /api/v1/health` + `snapshot()["health"]`. Tests: `tests/test_orchestrator_health.py`, `tests/test_run_registry.py` |
| `aea806a` | R8 | reconcile per-issue isolation (`_reconcile_one`), drift-state workspace cleanup, `_escalate_max_retries` durability (`_pending_escalations` dict + `_in_flight_ids()` in G1 prune + 10s re-attempt timer bounded at 5). Tests: `tests/test_orchestrator_reconcile.py` |
| `cb943a0` | A2+U3 | direct-run-path port-conflict guard with actionable sentence + `board ready at http://…` line; doctor-lite preflight (`_startup_preflight_failures`: agent CLI on PATH + after_create placeholder); friendly sentences for workflow_path_missing / workflow_load_failed / startup_failed (`_fail_startup`); `--tui` without TTY exits with explanation. Tests: `tests/test_cli_run_startup.py`. NOTE: prompt-file existence check deliberately NOT added — `workflow/coercion.py:_read_prompt_file` already fails workflow load with a precise ConfigValidationError (the audit's claim it fails at first dispatch was wrong) |
| `70dbc75` | R2+R7 | **WIP — see next section** |

## In-flight: R2+R7 backends (`70dbc75`, finish this FIRST)

### Already implemented in that commit

- `src/symphony/_shell.py`: `terminate_process_tree(proc, *, term_timeout=2.0, kill_timeout=5.0)` (killpg SIGTERM → safe_proc_wait → killpg SIGKILL → safe_proc_wait; win32/no-pgid fallback ladder), `kill_process_group(pid)` (sync SIGKILL best-effort for force-eject), `_signal_process_group` helper.
- All four backends spawn with `start_new_session=os.name == "posix"` (claude_code/codex/gemini/pi spawn sites).
- `stop()`/`_reap()` ladders in all four replaced with `await terminate_process_tree(proc)`.
- claude_code + pi: post-stream `safe_proc_wait(proc)` now bounded by `POST_STREAM_REAP_TIMEOUT_S=10` (constant in `backends/__init__.py`), falls through to `terminate_process_tree`; `MALFORMED_LINE_LIMIT=10` consecutive-bad-line guard sets `self._stream_corrupt` → precise `TurnFailed` raised in run_turn (before the generic no-terminal-event check, after the bounded reap).
- codex `_stdout_reader`: malformed streak breaks the loop; post-loop failure `reason` distinguishes corrupt stream vs EOF; fails all `_pending` futures AND the `_turn_completion_waiter` with `TurnFailed(reason)` (fixes crash-misreported-as-TurnTimeout).
- pi `is_progress_event`: True only for `{message_start, message_update, message_end, turn_start, turn_end}` (`_PROGRESS_EVENT_TYPES`); gemini `is_progress_event`: always False (gemini emits no mid-turn events — stdout read in bulk at turn end).

### Remaining to finish R2+R7

1. **codex completion-wait TurnFailed handling** — in `_send_turn_and_resolve` (`codex.py` ~line 526-536), `await asyncio.wait_for(completion, ...)` currently catches only `asyncio.TimeoutError`. When the reader fails the waiter with `TurnFailed`, it propagates without an `EVENT_TURN_FAILED` emission. Add:
   ```python
   except TurnFailed as exc:
       await self._emit(EVENT_TURN_FAILED, {"reason": str(exc)})
       raise
   ```
2. **Fix the 5 known-red tests** (they pin the OLD contract):
   - `test_{codex,gemini,pi}_stop_reaps_with_safe_proc_wait` (tests/test_backends.py): they monkeypatch each backend module's `safe_proc_wait` import; stop() now reaps through `symphony._shell.terminate_process_tree` → rewrite to monkeypatch `symphony._shell.safe_proc_wait` (which terminate_process_tree calls) or assert on `terminate_process_tree` being invoked. Keep the test intent: stop() must never leave the child unreaped and never call raw `proc.wait()`.
   - `test_{pi,gemini}_backend_is_progress_event_defaults_to_true`: replace with real-predicate tests (pi: `message_end`→True, `tool_execution_end`→False, `{}`→False; gemini: anything→False). Do NOT change `test_base_backend_is_progress_event_is_true_by_default` — the base default stays conservatively True by prior recorded decision (regression guard for OLV-002 fix 499e787); the deviation from the master plan's "remove the default" is deliberate, record it in the changelog.
3. **New lifecycle tests** (new file `tests/test_backends_lifecycle.py` or extend existing):
   - all four spawn sites pass `start_new_session=True` on POSIX (monkeypatch `asyncio.create_subprocess_exec` capturing kwargs, raise FileNotFoundError to short-circuit; claude/pi/gemini raise PortExit, codex raises CodexNotFound).
   - `terminate_process_tree` escalation order: monkeypatch `os.killpg` recording `(pid, sig)`, monkeypatch `symphony._shell.safe_proc_wait` returning None then rc → assert `[(pid, SIGTERM), (pid, SIGKILL)]` and final rc; also already-dead short-circuit (returncode set → no signals).
   - codex EOF fails a pending completion waiter promptly: fake `self._process` with a stdout whose `readline()` returns `b""`; arm via `backend._arm_completion_waiter()`; run `await backend._stdout_reader()`; waiter must hold `TurnFailed`.
   - malformed streak: `_consume_stream` (claude, pi) with a fake proc stdout yielding 10 garbage lines → `_stream_corrupt` set; 9 garbage + 1 valid + 9 garbage → not set. Fake proc: `stdout.readline()` from a list, `stderr=None` (drain returns early), `returncode=0`.
   - bounded post-stream reap: monkeypatch `symphony.backends.claude_code.safe_proc_wait` → None and `...terminate_process_tree` recording call.
   - Follow `tests/test_backends.py` fixtures: `_make_cfg(kind, workspace_root=tmp_path)` + `BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)`.
4. **R2 core side — force-eject must kill the recorded child process group**:
   - Backends already stamp `"agent_pid": self.pid` on every emitted event (see `_emit` in each backend). Core currently captures only `event.get("codex_app_server_pid")` into `entry.codex_app_server_pid` (`orchestrator/core.py`, search `codex_app_server_pid`). Extend that capture: `pid = event.get("codex_app_server_pid") or event.get("agent_pid")`.
   - In `_force_eject_zombie` (core.py, search `def _force_eject_zombie`): before scheduling the retry, if `entry.codex_app_server_pid` → `from .._shell import kill_process_group` (module-level import, follow existing import block) and call it; log `force_eject_killed_process_group` with pid + result. Rationale: force-eject fires when the worker is stuck on a non-cancellable await, so nobody will ever call `backend.stop()`; without the kill the zombie agent and the retry worker write the same reused worktree concurrently.
   - Test: RunningEntry with `codex_app_server_pid=4242`, monkeypatch `symphony.orchestrator.core.kill_process_group` (import it into core namespace so tests can patch) recording the pid; drive `_force_eject_zombie` the way `tests/test_orchestrator_dispatch.py` force-eject tests do (grep `force_eject` there).
5. Full suite green, then squash-or-keep and reword `70dbc75`'s WIP message via a follow-up commit (do not rebase published history; branch is local-only so `git commit --amend` on top is fine only if nothing new landed after it — otherwise just add a `feat(backends): finish R2/R7` commit).
6. Changelog entry: decisions = process-group pattern mirrors service.py; base is_progress_event kept True (deviation); gemini bulk-read `.read()` memory concern deliberately deferred (LOW-MED, audit item 9).

## Remaining backlog (in recommended order)

### R4 — classified tracker retries (files: `trackers/jira.py`, `trackers/linear.py`, new `trackers/_retry.py`, maybe `workflow/config.py`+`builder.py`)

Audit facts: linear `_post` (~384-405) and jira `_request`/`_json_or_raise` (~351-381) are single-shot; `NETWORK_TIMEOUT_SECONDS=30.0` hardcoded (jira:39, linear:27); pagination loops unbounded (jira `_search_paginated` ~293, linear `_paginate` ~349); both clients are sync httpx called from executor threads (sync `time.sleep` in the helper is fine).

Design (already decided):
- `send_with_retry(send: Callable[[], httpx.Response], *, max_attempts=3, sleep=time.sleep) -> httpx.Response`.
- Retryable: `httpx.TransportError` + status {429, 500, 502, 503, 504}. Non-retryable statuses return the response for the caller's existing status handling — preserve each tracker's existing exception classes/messages exactly (no caller or test contract changes).
- Backoff: 0.5s * 2^attempt + jitter 0-250ms; honor numeric `Retry-After` header capped at 30s.
- After max attempts: re-raise last transport error / return last retryable response.
- Why retrying writes is acceptable: state updates are idempotent, 429 means not processed — one short comment.
- `MAX_PAGES = 20` module constant in both trackers; log a warning when hit.
- Config: optional `network_timeout_seconds: float = 30.0` on the tracker config dataclass (`workflow/config.py`, frozen — additive with default) parsed in `builder.py` following existing optional-field patterns; thread into both clients. If builder wiring is disproportionate, env var `SYMPHONY_TRACKER_TIMEOUT_S` fallback is acceptable — say so in changelog.
- Tests: extend `tests/test_tracker_jira_edges.py` + `tests/test_tracker_linear_full.py` (read their existing fake-transport patterns first). Cases: 429-then-success; Retry-After honored (assert injected sleep arg); transport-error-then-success; 400 fails immediately, no retry, existing error type; exhaustion raises existing error type. Inject `sleep` so tests are instant.

### R5 — board write serialization (files: `trackers/file.py`, tests `tests/test_tracker_file.py`)

Audit facts: `write_ticket_atomic` (~322) is atomic per write, but read-modify-write is unserialized across ≥5 in-process writers (core `_tracker_call_update_state` ~1052 / `record_agent_kind` ~1536, tui/app 642/769, webapi 432, cli/board 151) plus the agent subprocess editing the same md file; `next_identifier` (~523) is max+1 TOCTOU (webapi 427 carries a collision-retry workaround); `_auto_heal_markdown_in_front_matter` writes during a read path (~168).

Design (decided in master plan §R5):
- Per-ticket `fcntl.flock` advisory lock around every read-modify-write (lock file `<board>/.locks/<identifier>.lock` or flock the ticket file itself — pick one, justify in changelog; mind that `os.replace` swaps the inode, so a lockfile-per-ticket is safer than flocking the md).
- `updated_at` compare-and-swap: before writing, re-read; if `updated_at` moved since our read, re-apply the mutation on the fresh copy (reject-and-reread) — this covers the out-of-process agent writer that can't share flock.
- `next_identifier` allocation under the same lock dir (single `.locks/allocator.lock`).
- Remove the write-on-read: `_auto_heal_markdown_in_front_matter` should heal only inside mutation paths, or return the healed form without persisting on pure reads.
- Windows: `fcntl` unavailable — gate with `os.name == "posix"`, fall back to current behavior, note in changelog.
- Tests: concurrent read-modify-write via threads losing an update today (RED), lock preserves both writes; allocator uniqueness under threaded `create`; read path no longer mutates the file (mtime stable).

### R6 — persist safety valves (files: `orchestrator/run_registry.py`, `orchestrator/core.py`)

In-memory-only today (lost on crash): retry attempt counters (`RetryEntry.attempt`), `_turn_budget_exhausted`, `_paused_issue_ids`. Design: a small `issue_flags` table in the same `state.db` (issue_id PK, retry_attempts INT, budget_exhausted INT, paused INT, updated_at) with `RunRegistry` CRUD; core writes through on `_schedule_retry` / budget-exhaust / pause-resume; `_ensure_run_registry` rehydrates into the in-memory sets at startup. Wrap all calls in `_registry_guard`. Tests: crash-restart simulation — set flags via one registry instance, new Orchestrator + `_ensure_run_registry` → `_should_dispatch` blocked for exhausted/paused, retry attempt continues from persisted count (drive `_schedule_retry` and assert escalation fires at the correct total attempt).

### A4 — adopt-or-clean restart pass (files: `orchestrator/core.py`)

`reclaim_dead_owner_leases` (done in R3) already returns the orphaned `RunRecord`s with `workspace_path`. In `_ensure_run_registry` (or a startup pass right after it in `start()`), for each reclaimed record: if `workspace_path` exists → `git status --porcelain` clean? reuse policy decides; default: `commit_workspace_on_done`-style snapshot if `auto_commit_on_done`, else remove via `WorkspaceManager.remove`. Markdown ticket stays the checkpoint (do NOT attempt in-process reattachment — rejected in master plan §8). Careful: `_ensure_run_registry` is sync and called per-tick; do the workspace pass once at startup only (guard with a flag), async context available in `start()`. Tests: orphaned lease with a fake workspace dir → removed (or snapshotted) and lease not blocking dispatch.

### A5 — single-instance flock (files: `orchestrator/core.py` or `cli/main.py` + `service.py`)

Hold an exclusive non-blocking `fcntl.flock` on `<workflow_dir>/.symphony/orchestrator.lock` for the orchestrator's lifetime (acquire in `Orchestrator.start()`, release in `stop()`; store fd on self). On contention: raise SymphonyError "already running (pid N)" — write pid+port into the lockfile content for the message. The A2 startup path already turns SymphonyError into a friendly sentence. Windows: `msvcrt.locking` or skip with a warning. Tests: two Orchestrators same workflow dir → second `start()` raises; first `stop()` releases; stale lockfile from dead pid does not block (flock releases on process death automatically — that's the point; test documents it).

### U1 — attention taxonomy (files: `orchestrator/core.py` `issue_attention` ~451, `webapi.py` `_issue_card`/detail, `web/static/app.js` drawer ~1238, `style.css`)

Backend already computes: `entry.last_error`, retry `_retry_row` (error + due_at, core ~627-638), `entry.cancelled_at` (stalled), `lease_lost`. Extend `issue_attention(issue)` to return kinds: `budget_exhausted` (existing), `stalled` (cancelled_at set), `retry_scheduled` (in `_retry`, include `due_at`+`reason`), `lease_blocked` (lease_lost), plus `escalation_pending` (`_pending_escalations`). Thread through `webapi` issue card + detail `attention` payload (shape already exists from the budget-exhausted slice — see changelog 2026-07-02 "post-E2E hardening implementation" and `buildAttentionBadge` in app.js). Drawer Live-run panel: add `last_error`, retry reason, next attempt time. Mind `tests/test_web_static_contract.py` and `tests/test_webapi.py::test_issue_detail_includes_attention`. TUI already shows retry badges — web reaches parity.

### A3 — SPA degraded-state truthfulness (files: `web/static/app.js`, `style.css`)

Poll loop ~1656-1681, connection dot ~703-711. Three states instead of two: `connected` / `degraded` (fetch returned 4xx/5xx — server up; show the error message from the JSON body, e.g. broken WORKFLOW.md 400) / `unreachable` (fetch threw TypeError/network). Add "data as of HH:MM:SS" staleness stamp when not connected; back off the 5s poll to 15s after 3 consecutive failures; manual "Retry now" button. Also poll `/api/v1/health` (new from A1) opportunistically and show `degraded_reasons` as a banner when status != ok. Keep the 'hold DOM updates while dragging/focused' behavior (commit f980b0e) intact. Update `tests/test_web_static_contract.py` with new selector strings; browser E2E optional (SYMPHONY_BROWSER_E2E=1).

### U2 — doctor v2 (files: `cli/doctor.py`, tests `tests/test_doctor.py`)

Add checks: (a) agent auth probes — cheap `--version`-style invocations for codex/claude/gemini are NOT reliable auth probes; instead check auth artifacts like the existing `check_pi_auth` does (claude: `~/.claude.json` or keychain — if nothing cheap exists, WARN with guidance, don't fake it); (b) optional live tracker ping behind `--online` flag (linear viewer query / jira myself endpoint, 5s timeout); (c) port check "already ours": when bind fails, read `<workflow_dir>/.symphony/service.json` (see `service.py` run record) and if a live pid owns the port report "already served by symphony service (pid N)" as WARN not FAIL. Keep exit-code semantics (0/1/2).

### U4 — run-history surface (files: `orchestrator/run_registry.py` add `recent_runs(limit, issue_id=None)`, `server.py` or `webapi.py` `GET /api/v1/runs?issue=`, `cli/` new `symphony runs` subcommand routed in `cli/main.py` (see `test_cli_main_routing.py` for the dispatcher pattern), `web/static/app.js` drawer History section)

Read-only; rows exist since the lease slice (`runs` table keeps completed/expired/orphaned). Columns: identifier, attempt_kind, agent_kind, status, started_at, completed_at, workspace_path. Paginate LIMIT 50.

### U5 — onboarding fixes (README.md, README.ko.md, examples/)

Remaining after A2/U3 shipped the TTY guard + placeholder preflight + ready line: (a) single canonical quickstart — make `WORKFLOW.file.example.md` the first path in both READMEs, Linear example clearly secondary; (b) first-tick feedback — `_tick_loop` already ticks immediately; verify and document; (c) README troubleshooting section pointing at `/api/v1/health` and `symphony runs`. Repo is going public: every claim must be true from a fresh clone; no dead references.

### Task 19 — final gate

1. `python -m pytest -q` full green (modulo the documented bootstrap env failure).
2. `.venv/bin/symphony doctor ./WORKFLOW.md` all PASS.
3. Launcher smoke per the run-path rule: run the real launcher (`.tui-launcher.command` / `symphony service start`) against a real board — the olive-clone board (`/Users/danny/Documents/PARA/Resource/olive-clone`, port 9991, file backend) is the canonical live target. Verify `/api/v1/health`, board loads, prompt editor opens full-width.
4. Version bump 0.10.0 lockstep (pyproject + `src/symphony/__init__.py`), own chore commit.
5. Changelog: one section per shipped item with decisions + rejected alternatives (several are pre-written above and in the master plan §8).
6. Do NOT push or open a PR without asking the user. Do NOT modify `.tui-launcher.command`.

## Gotchas discovered while implementing (save yourself the hour)

- `Orchestrator.__init__` runs fine without `start()` — `_orch()` test helper is just `Orchestrator(WorkflowState(Path("/tmp/no.md")))`; `snapshot()`/`health()` work on it.
- `aiohttp` test pattern: `TestClient(TestServer(build_app(orch)))`, asyncio_mode=auto, plain `async def test_*`.
- `RunRegistry` sqlite connections are thread-affine (`isolation_level=None`, manual BEGIN IMMEDIATE); do NOT move calls to `asyncio.to_thread` without reworking the connection handling — the guarded-inline + 5s busy timeout compromise is recorded in changelog.
- `event.get("agent_pid")` is stamped by every backend `_emit`; codex additionally has `codex_app_server_pid`.
- The pinned base `is_progress_event=True` test (`test_base_backend_is_progress_event_is_true_by_default`) documents a deliberate prior decision — keep it.
- pyright pre-existing noise: core.py:110 `_pkg = sys.modules[__package__]` str|None complaint; "code is unreachable" hints around `safe_proc_wait` narrowing. Not yours to fix.
- `subprocess start_new_session=True` raises on Windows — gate with `os.name == "posix"` (already done at the four spawn sites).
