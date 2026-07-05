# Continuous improvement rubric

This document is the contract for the continuous-improvement heartbeat: a
default-off scheduler that periodically re-verifies the integrated baseline
and turns defects into normal Kanban tickets. It never edits product code.
See `docs/architecture.md` ("Continuous improvement heartbeat") for the
runtime surfaces and `docs/continuous-improvement/ticket-template.md` for the
ticket body format.

## Default configuration

```yaml
continuous_improvement:
  enabled: false
  interval_ms: 1800000   # minimum 60000
  max_turns: 48          # 0 = unlimited
  ticket_prefix: CI
  max_tickets_per_run: 5
  require_idle_board: true
```

- `enabled` defaults to `false`.
- `interval_ms` defaults to `1800000` (30 minutes). It accepts only
  positive integers, with a lower bound of `60000` ms (1 minute); values
  below the floor, non-integers, and booleans are rejected.
- `max_turns` defaults to `48` (24 hours at the default interval). `0`
  means unlimited.
- `ticket_prefix` defaults to `CI` and must be identifier-safe (used as
  the tracker's ticket-ID prefix, e.g. `CI-1`).
- `max_tickets_per_run` defaults to `5`.
- `require_idle_board` defaults to `true` (see "Idle-board requirement"
  below).

## Result semantics

Every rubric item resolves to exactly one of these states:

- `passed` — the check ran and succeeded. No ticket is created.
- `failed` — the check ran and revealed a product-readiness defect. A ticket
  is created (subject to de-duplication and `max_tickets_per_run`).
- `not_available` — an optional check is not configured, or a required tool
  is not installed. This is **not** a failure and never creates a ticket.
- `not_proven` — the baseline itself cannot be trusted: dirty worktree,
  missing target branch, unreachable upstream, or an infrastructure failure
  in the check runner (timeout, crash, unexpected exit before completion).

`not_proven` is stronger than `failed`. If the baseline proof step is
`not_proven`, the run stops after recording the report; no downstream check
runs and no tickets are created from that run. A heartbeat that cannot prove
what it tested must not manufacture findings about it.

## Baseline proof (always runs first)

Read-only Git commands only — the heartbeat never runs `git checkout`,
`git switch`, `git reset`, `git stash`, or any command that mutates the
working tree or HEAD in the host worktree. It proves:

- current branch name
- current commit SHA
- worktree dirty status (`git status --porcelain`)
- upstream alignment, when an upstream is configured (ahead/behind counts)

If the worktree is dirty, the target branch cannot be resolved, or the
upstream is configured but unreachable, the baseline proof is `not_proven`
and the run ends there.

## Default checks

| Check | Command | Rubric role |
| --- | --- | --- |
| Unit / integration tests | `python -m pytest -q` | `failed` on non-zero exit; `not_proven` on timeout/crash |
| Lint | `python -m ruff check src tests` | `failed` on any reported violation |
| Type check | `python -m pyright` | `failed` on any reported error |
| Browser QA | project-specific, optional | `not_available` unless dependencies and required environment flags are present |
| Read-only DB probes | project-specific, optional | `not_available` unless explicit read-only configuration exists |

Browser and DB checks are opt-in and read-only by construction: they must
never run destructive DB migrations, resets, or seed commands, and a missing
or unconfigured optional check is always `not_available`, never `failed`.

## No-code-edit invariant

The heartbeat **never edits product code**. Its only write surfaces are:

1. `docs/continuous-improvement/latest.md` (machine-owned sections only, see
   below).
2. New Kanban tickets through the tracker's normal creation path.

Any defect it finds becomes a ticket for a normal worker to pick up. The
heartbeat does not attempt fixes, does not open pull requests, and does not
touch files under `src/` or `tests/`.

## Default-off behavior and command safety

- The feature ships disabled (`continuous_improvement.enabled: false`).
  Enabling it is an explicit operator action from the web settings card or
  `WORKFLOW.md`.
- Only `enabled`, `interval_ms`, and `max_turns` are browser-editable. The
  check list, ticket template, environment variables, and file paths are
  trusted workflow configuration, not remotely configurable.
- Every check runs as a predefined `argv` array with `shell=False` — no
  shell string interpolation, no user-supplied command text.
- Every subprocess has an explicit timeout. A timeout is recorded as
  `not_proven` (or `not_available` for optional checks), never silently
  dropped.
- Captured output is capped in size and scanned for obvious secret patterns
  (tokens, keys, credentials) before it is written to the report or a ticket
  body; matches are redacted.
- The heartbeat never runs destructive DB migrations, resets, or seed
  commands. DB checks are limited to read-only probes from explicit
  configuration.

## Cross-process lease

Multiple orchestrator processes can point at the same workflow directory
(for example, two terminals running the same board). Concurrent heartbeat
runs against the same baseline would double-report findings and race on
`docs/continuous-improvement/latest.md`. A durable, fakeable lease (the same
family as `RunRegistry.acquire_run` in
`src/symphony/orchestrator/run_registry.py`) guards each run: a process must
acquire the lease before starting a heartbeat run and release it when the
run finishes or the process exits. A process that cannot acquire the lease
skips its scheduled run rather than blocking.

## Turn budget

Each completed run (any terminal outcome, including `not_proven`) consumes
one turn. `max_turns` defaults to 48 (24 hours at the default 30-minute
interval); `0` means unlimited. When `turns_used >= max_turns`, the
scheduler stops scheduling new runs and reports
`skipped_reason: max_turns_reached` until an operator resets the counter
(`POST /api/v1/workflow/continuous-improvement/reset-turns`) or restarts the
orchestrator. The counter is in-memory only.

## Idle-board requirement

`require_idle_board: true` (the only supported value in the first
implementation) postpones a due run while normal workers are running or
retrying. The heartbeat never competes with normal ticket dispatch for
`max_concurrent_agents` slots; it runs as a bounded background task outside
the tick loop and only when the board would otherwise be idle.

## Tracker support matrix

| Tracker | Ticket creation | Notes |
| --- | --- | --- |
| File board (`FileBoardTracker`) | Supported | Uses `create_with_next_identifier(prefix="CI")`; de-duplicated by `CI Fingerprint` |
| Jira / other remote trackers | Not supported (first implementation) | Registrar reports `skipped_reason: unsupported_tracker`; the run still completes and writes its report |

A tracker without a safe, idempotent creation contract must report
`unsupported_tracker` rather than crash the run or fall back to an unsafe
write path.

## De-duplication

Each finding is fingerprinted from a stable subset of its content (rubric
item, check name, normalized failure summary). Before creating a ticket, the
registrar searches active tickets for an existing `CI Fingerprint: <hash>`
line:

- match found → append an observation, or skip if nothing new to add; no new
  ticket is created.
- no match → create a new ticket, up to `max_tickets_per_run` (default 5) new
  tickets per run. Additional findings beyond the cap wait for the next run.

Ticket writes always go through tracker APIs (lock / compare-and-swap
identifier allocation), never direct Markdown rewrites.
