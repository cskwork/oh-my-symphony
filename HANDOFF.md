# Session Handoff — 2026-08-23

## Where things stand

Symphony (`oh-my-symphony` v0.20.1) had a full review-and-harden cycle on this
Windows host. **17 commits are pushed to `origin/dev`** (`91a19c1..41a07d8`),
working tree is clean, and every gate is green locally:

- ruff ✓ · pyright 0 errors ✓ · i18n 551 keys (en/ko) ✓
- Full pytest suite **~2350 tests, 0 failures** (85 capability-probed win32
  skips; all no-ops on Linux so CI coverage is unchanged)
- Live e2e verified: real `opencode/x-preview-f-free` workers took two
  tickets through Todo → In Progress → Verify → Document → Done on a scratch
  project, artifacts checked on disk

## What the 17 commits did

| Range | Content |
|---|---|
| `306a6d4` | Security: optional `SYMPHONY_API_TOKEN` bearer gate on all `/api/` routes; `_debug/tasks` loopback-only; 500s stop leaking internals |
| `72b9dbf` | Windows: `terminate_process_tree` kills the full tree via `taskkill /T /F` (agents no longer orphaned behind the bash wrapper) |
| `bc3acb1` | Windows bugs: artifact sanitizer, skill-dir junctions, auto-merge via stdin (MSYS argv truncation), preview-cwd escape check, msvcrt file-tracker lock |
| `cc6e575` | UX quick wins: TUI `q` confirm, compact-card error+agent chip; web keyboard-accessible cards, empty states, retry cause, stale-board dimming, CSS token fix, contrast, 13 i18n keys |
| `466ed92` | Refactor: workflow↔orchestrator layering fixed, backend helpers deduped into `per_turn.py`, wiki sweep off-loop, shared tracker httpx client |
| `c6047c3` | `symphony doctor` keeps backslash paths intact on win32 (`shlex posix=False`) |
| `7636d29` | Test infra: `tests/_win_skips.py` capability probes (symlink privilege / NTFS names); autocrlf pins |
| `587a28d` | Remaining gate failures: PowerShell CIM process enumerator for force-stop; getattr'd `killpg`/`SIGKILL` (pyright clean both platforms) |
| `a288874` | SPA token support (banner + Bearer on fetches + `?token=` on chat-WS handshake only); stale-board keyboard gating |
| `c794660` | Kill-path/junction hardening: bounded taskkill (10s), pid-reuse identity gate, junction stays machine-local, Git-Bash trailer stripped product-side |
| `664775c` | "Startup deadlock" proven environmental (console Ctrl+C via venv trampoline — skip removed); off-loop stall/drain kills; tracker-client close-race safety |
| `6605d42` | Off-loop reclaim kills (async ensure path), identity-gated shutdown kills (persisted fingerprints), real win32 `_pid_alive` (ctypes OpenProcess) |
| `9021e5d` | CONTRIBUTING: detached-pytest workaround documented |
| `41a07d8` | browser-e2e stub seam methods matched to async webapi handlers |

An adversarial review (@oracle) verified all commit claims and found 16
defects — all remediated in `a288874`/`c794660`/`664775c`/`6605d42`.

## Open questions / next steps

1. **Big refactor #1 — `orchestrator/core.py` god class** (10,945 lines;
   `_run_agent_attempt` alone is 1,042). Oracle's plan: split
   `_run_agent_attempt` per-phase first, then extract the worker-exit state
   machine, then delegate release-gate orchestration to `release_cycle.py`.
   Multi-day effort; do incrementally.
2. **Big refactor #2 — claude/pi backends** still copy the per-turn skeleton
   instead of subclassing `PerTurnCliBackend` (helpers deduped; structural
   migration not done; use opencode's `_read_stdout` pattern).
3. **win32 `process_identity()`** has no fingerprint implementation (returns
   `None` → kills stay ungated warn-once on Windows). A `GetProcessTimes`-
   based implementation in `_shell.py` would enable pid-reuse protection.
4. **Deferred UX items** (designer audit): scrollable HelpScreen (M4), modal
   focus trap (M6), web-board rendering for Linear/Jira trackers (H2, needs
   an API change), TUI↔web action parity (M8/M9).
5. Check CI results on `origin/dev` (ubuntu-only; the win32 skips are no-ops).

## Environment notes (this Windows host)

- **Run full pytest DETACHED** — hidden-console Ctrl+C pollution kills
  venv-trampoline runs mid-suite (see CONTRIBUTING.md for the command).
  Launcher pattern that works: `subprocess.Popen(..., creationflags=
  DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)` with
  `--junitxml` for results.
- **Watch the C: temp disk** — pytest basetemp dirs (git-worktree clones)
  filled it once this session (~490 MB reclaimed). Clean `pt-*` dirs under
  `%TEMP%\opencode` when it fills.
- Default pytest temp root `Temp\pytest-of-a` has broken ACLs — always pass
  `--basetemp`.
- ruff/pyright live in the dev extra: `uv run --extra dev ...`.
- The `rtk` wrapper masks stdout to "ok" — use `--junitxml` or file redirects
  when you need real output.
- Scratch e2e project (reusable live-smoke template) is at
  `%TEMP%\opencode\sym-e2e` (WORKFLOW.md + 2 Done tickets, opencode backend).

## Files touched this session

src: `webapi.py`, `server.py`, `_shell.py`, `service.py`, `projects.py`,
`workspace.py`, `artifacts.py`, `trackers/file.py`, `workflow/{presets,config,builder}.py`,
`orchestrator/{core,run_registry,contracts}.py`, `backends/{per_turn,claude_code,pi,codex}.py`,
`cli/doctor.py`, `tui/{app,widgets}.py`, `web/static/{app.js,i18n.js,style.css}` ·
tests: 12 files + new `_win_skips.py` + `test_webapi_auth.py` ·
`CONTRIBUTING.md`, `uv.lock`
