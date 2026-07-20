# Runtime and QA exploration

Target: Symphony `0fe78e2`, branch
`run/symphony-aidt-orchestrator-20260720`. Read-only inspection; no product
code changed and no service started or stopped.

## Decision

Use the built-in web app on loopback port **9918** as the dedicated AIDT
dashboard. Reuse the managed service, file board, API, SQLite leases, and
existing QA harnesses. Start with `--no-viewer`: `tools/board-viewer` is a
deprecated secondary server, while the primary port already serves the full
SPA and API.

The complete goal is not runtime-ready yet:

- no `kanban/` exists in this worktree;
- no runnable project venv exists; the parent checkout's `symphony` launcher
  fails during import because `ruamel.yaml` is missing;
- `caffeinate` prevents macOS sleep but does not restart after crash/reboot;
- the Textual TUI starts another orchestrator and cannot attach to the managed
  service's live runtime state.

## Current architecture

### Managed service and dashboard

`src/symphony/service.py` provides `service start|status|restart|stop|logs`.

1. An atomic per-workflow `.symphony/run/<hash>.lock` serializes lifecycle
   commands.
2. A saved live PID, or a responsive saved `/api/v1/state`, prevents starting
   the same workflow again on another port.
3. `start` runs doctor with the CLI port substituted, then detaches the
   orchestrator with stdin closed and output in `log/symphony.log`.
4. `.symphony/run/<hash>.json` records host, ports, PIDs, commands, logs, and
   start time.
5. Stop sends SIGTERM to process groups; `--force` also kills recorded backend
   PIDs and processes tied to workflow-owned workspaces. A live survivor keeps
   the record and makes stop fail.

The primary aiohttp process serves both the SPA and API:

- `/`, `/#/board`: built-in dashboard;
- `/api/v1/health`: tick/tracker/registry degradation;
- `/api/v1/state`: running/retrying rows, tokens, branch policy, health;
- `/api/v1/board`, issue CRUD, workflow/prompts/branch settings, stats, runs,
  refresh, pause/resume, skip-Learn, and blocked recovery.

Default bind is `127.0.0.1`. Loopback API requests reject non-loopback Host
headers, and mutations with bodies require JSON. Do not expose this operator
UI on a non-loopback bind.

### Durable state

| Location | Purpose |
| --- | --- |
| `kanban/*.md` | Ticket/evidence source of truth |
| `.symphony/run/<hash>.json` | Managed service ownership |
| `.symphony/state.db` | SQLite WAL run leases, backend PID, flags, history |
| `.symphony/stats.jsonl` | Turn/token/transition statistics |
| `WORKFLOW-PROGRESS.md` | Atomic human-readable lane/transition mirror |
| `log/symphony.log` | Structured runtime diagnostics |

`RunRegistry` gives each issue one cross-process lease, heartbeats it, persists
pause/retry/budget flags, and reclaims dead-owner leases and backend PIDs on
restart. Registry I/O errors degrade health but dispatch fails open, so a
broken registry plus multiple orchestrators remains a duplicate-dispatch risk.

### Dashboard and TUI behavior

- Built-in web: full supported UI; polls the board every five seconds.
- Legacy viewer: separate optional process/port; duplicated lifecycle and no
  reason to include it in the first AIDT slice.
- TUI: `symphony --tui WORKFLOW.md` requires a TTY and runs an orchestrator,
  HTTP server, and Textual app in one foreground process. It reads the same
  file board but overlays runtime from its own orchestrator. `tui-open.sh`
  refuses to launch if a non-TUI process owns the workflow port. There is no
  API-backed attach/read-only mode for an existing managed service.

## Doctor and current runtime

Doctor checks the primary port, bash, stage turn budget, agent executable,
backend-specific auth/state, prompts, `after_create`, writable workspace,
tracker/board, and viewer presence. Viewer absence is WARN. Viewer-port
bindability is not checked.

Important lifecycle limits:

- `service start` checks child PID survival for one to two seconds, not API
  readiness;
- `service status` says running for a live PID even when health/API is down;
- health must be an acceptance gate, not PID status alone;
- a crash while holding `.symphony/run/*.lock` can leave an unreclaimed lock.

Observed without launching:

- `WORKFLOW.md`: file tracker, port 9999, QA ports 8801/8802, Claude default,
  30-second polling;
- `kanban/` absent, so doctor will FAIL `tracker.board_root`;
- bash, Claude, Codex, prompt files, setup hook, and viewer script present;
- project `.venv/bin/symphony` absent;
- parent `../../.venv/bin/symphony service status ./WORKFLOW.md` cannot import
  `ruamel.yaml`; system Python also lacks aiohttp/YAML/Textual/Playwright;
- no service record, registry DB, stats, progress mirror, or runtime logs;
- no listeners on 9999, 8765, 9918, or 9919. Process listing was
  sandbox-blocked, so this proves those ports are unused, not that no unrelated
  Symphony process exists.

## Dedicated port strategy

Port **9918** and adjacent 9919 accepted simultaneous `127.0.0.1` binds during
inspection. Persist 9918 as `server.port`; use only 9918 with `--no-viewer`.
Do not use port `0`: aiohttp discovers the ephemeral port, but the managed
record retains the requested value.

Reusable selection probe:

```bash
python3 - <<'PY'
import socket
for port in range(9918, 9998):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        sock.close()
        continue
    sock.close()
    print(port)
    break
else:
    raise SystemExit("no free AIDT Symphony port")
PY
```

Persist the result, run doctor, and start immediately. The probe has a race;
collision must fail visibly, never silently move the published dashboard URL.

## macOS always-running behavior

Current CLI defaults `system.keep_awake` to true. On Darwin it launches
`caffeinate -d -i -w <symphony-pid>`; the assertion follows the Symphony PID
and disappears after any exit. The managed child is session-detached, and the
tick loop has bounded in-process restarts with health reporting.

This is not crash/reboot supervision. There is no LaunchAgent, `launchctl`
installer, login start, or external health monitor. A safe design needs a user
LaunchAgent with `RunAtLoad` and `KeepAlive` supervising a new foreground
managed-service entrypoint. Do not point launchd at current `service start`:
that command detaches and exits, so launchd would supervise the launcher, not
the orchestrator. Direct `python -m symphony.cli` is foreground but bypasses
the service record/duplicate-start contract.

## Reusable QA commands

Bootstrap blocker removal:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev,browser]'
.venv/bin/python -m playwright install chromium
```

Frozen-repository gates:

```bash
.venv/bin/python -m pytest -q \
  tests/test_service.py tests/test_doctor.py tests/test_keep_awake.py \
  tests/test_run_registry.py tests/test_cli_run_startup.py

.venv/bin/python -m pytest -q \
  tests/test_server_routes.py tests/test_webapi.py \
  tests/test_web_api_smoke_script.py tests/test_web_static_contract.py

.venv/bin/python -m pytest -q tests/test_tui.py
.venv/bin/python -m pytest -q tests/test_agent_lifecycle_e2e.py
SYMPHONY_BROWSER_E2E=1 \
  .venv/bin/python -m pytest -q -rs tests/test_web_browser_e2e.py
```

Harness coverage:

- API: aiohttp TestServer plus real temporary file board; live
  `scripts/smoke_web_api.py` health/state/board/static assets, disposable issue
  create/detail/patch/delete, refresh, workflow, and stats.
- Browser: Playwright Chromium covers active/all lanes, terminal grouping,
  issue CRUD, desktop/mobile overflow, and console/page errors. It uses a stub
  orchestrator and temporary TestServer, not a live managed service.
- TUI: Textual Pilot covers rendering, runtime badges, details, filters,
  pagination, pause/resume, archive/confirm, issue creation, and stats.
  `capture_tui_screenshot.py` renders fixture data, not a live service.
- Lifecycle: real temporary Markdown board plus fake backend drives Todo -> In
  Progress -> Verify -> Learn -> Done and checks contract artifacts.

Eventual profile probes:

```bash
.venv/bin/symphony doctor profiles/aidt/WORKFLOW.md
.venv/bin/symphony service start profiles/aidt/WORKFLOW.md \
  --port 9918 --no-viewer
.venv/bin/symphony service status profiles/aidt/WORKFLOW.md
curl -fsS http://127.0.0.1:9918/api/v1/health | jq
curl -fsS http://127.0.0.1:9918/api/v1/state | jq
.venv/bin/python scripts/smoke_web_api.py \
  --base-url http://127.0.0.1:9918 --prefix AIDTE2E
.venv/bin/symphony runs profiles/aidt/WORKFLOW.md --limit 20
.venv/bin/symphony service logs profiles/aidt/WORKFLOW.md --lines 100
```

Run the mutating smoke only on a disposable board. It cleans up in `finally`,
but a killed process can strand its card.

## Exact integrated E2E plan

1. Create a disposable AIDT-profile workflow/board in a temporary git repo;
   use mock Codex, never the real Jira inbox or AIDT repos.
2. Persist 9918; run doctor; require all checks PASS except accepted WARNs.
3. Managed start with `--no-viewer`; verify record/PID/port. A second start on
   another port must reuse 9918 and not spawn.
4. Poll `/api/v1/health` with a deadline; require tick alive, registry enabled,
   no degraded reasons, then compare `/state` and `/board` identity.
5. Run API smoke; pause/resume a running mock ticket; restart and prove pause
   persistence and no duplicate dispatch.
6. Run existing Playwright E2E, then a live 9918 browser pass for mirrored card,
   stage/detail, refresh, pause/resume badge, failure attention, mobile layout,
   and zero console errors. Current browser test needs live-base parameterization
   or a small external Playwright scenario.
7. Drive one disposable card through the full local pipeline; assert ticket
   history, `state.db`, stats, progress mirror, workspace, and evidence at each
   transition. Restart once mid-run.
8. After API-backed TUI attach mode exists, assert the same card, state,
   running/retry/tokens/pause/failure data in API, browser, and a PTY TUI
   screenshot. Current code cannot pass this while the service stays active.
9. After LaunchAgent support, kill the supervised child, require one recovery
   at the same URL with no duplicate dispatch, and repeat health/state. Reboot
   remains a manual macOS acceptance.
10. Stop only the disposable service; assert 9918 is free and real AIDT state
    was untouched.

## Key risks

| Risk | Control |
| --- | --- |
| TUI starts a second orchestrator | Add API-backed attach/read-only mode |
| Detached process dies/reboots | Foreground managed entrypoint + LaunchAgent |
| PID status is false green | Gate on `/api/v1/health` |
| Registry fail-open duplicates | Make degradation dispatch-fatal for AIDT or guarantee one process |
| Viewer silently absent/collides | Use `--no-viewer`; otherwise probe and verify `viewer_pid` |
| Port check race | Stable port, immediate start, fatal collision |
| Stub UI tests miss integration | Add live-base browser and TUI attach E2E |
| Missing `ruamel.yaml`/board | Rebuild Python 3.12 venv; create isolated board |

## Minimal first slice (maximum five files)

Deliver macOS supervised managed runtime before Jira intake or deployment:

1. `src/symphony/service.py`: foreground managed entrypoint, API readiness,
   record ownership, signal forwarding, child-exit propagation.
2. `tests/test_service.py`: readiness, duplicate start, crash, termination,
   record cleanup.
3. `scripts/install-macos-launchagent.sh`: idempotent user LaunchAgent for
   explicit Python/workflow/host/port; no secrets.
4. `tests/test_macos_launchagent.py`: plist args, RunAtLoad/KeepAlive, loopback
   9918, install/uninstall idempotence.
5. `skills/symphony-skill/reference/operations.md`: install/status/health/log/
   recovery/uninstall commands.

Acceptance: a disposable workflow is supervised at 9918, doctor and health
pass, forced child exit restores once without duplicate dispatch, SIGTERM
drains, and plist/logs contain no secrets. Keep the AIDT profile and TUI attach
as later independent slices; combining all three violates the repository's
one-contract and <=5-file rule.
