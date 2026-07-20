# 009 - Managed resolver surfaces

Route: GREENFIELD

Status: pending

Blocked by: 001, 004

Unblocks: 010, 011

## Goal

Run one supervised loopback resolver on port 9918 while the HTML dashboard and API-backed read-only TUI
expose the same durable card and runtime state.

## Acceptance criteria

- The profile binds 127.0.0.1:9918, uses the built-in dashboard/API, disables the legacy viewer, and fails
  visibly on collision instead of changing port.
- Doctor passes and managed start waits for API health, not only PID survival.
- A foreground entrypoint plus LaunchAgent RunAtLoad/KeepAlive restores one crashed child at the same URL,
  preserves ownership, and prevents duplicate dispatch.
- Registry degradation is dispatch-fatal for this profile or single-process ownership is otherwise proven.
- Browser, API, and TUI show the same card, lane, running/retry/token/pause/failure state.
- TUI attach never starts a second orchestrator; plist, commands, records, logs, and UI contain no secrets.

## Proof commands and surfaces

- pytest -q tests/test_service.py tests/test_macos_launchagent.py tests/test_tui.py
- pytest -q tests/test_server_routes.py tests/test_webapi.py tests/test_web_browser_e2e.py
- Disposable doctor/start/health/state/board/API smoke plus browser and PTY TUI parity on 9918.

## Scope boundaries

- Owns supervision, dedicated-port profile, dashboard/API readiness, and TUI attach/parity.
- Does not run real Jira, create AIDT worktrees, merge, deploy, or expose beyond loopback.

## External blockers

- Python 3.12 environment, ruamel/aiohttp/Textual/Playwright, Chromium, and a board are absent.
- LaunchAgent installation/reboot proof are operator/machine actions; crash proof uses a disposable profile.
