# Four-agent todo E2E production hardening plan

Date: 2026-07-03. Branch: dev. Status: PLANNED - no production code changed.

## Goal

Make a Symphony run reliably build and verify a small browser todo app through
all configured coding agents: OpenCode, Pi, Claude Code, and Codex. "Reliable"
means no unattended approval stalls, no retry loops that look active while no
useful work is happening, no concurrent workspace setup races, and real browser
QA for UI behavior before a ticket is accepted.

## Test run summary

I ran a real headless Symphony service from an isolated clone so the main
working tree and board stayed untouched.

- Temp clone: `/private/tmp/symphony-e2e-todo-MJxJP2/repo`
- Temp workspace root: `/private/tmp/symphony-e2e-todo-MJxJP2/workspaces`
- Service: `symphony service start ./WORKFLOW.md --port 10081 --no-viewer`
- Monitoring: `log/symphony.log` and `/api/v1/state`
- Preflight: `symphony doctor ./WORKFLOW.md` passed in the temp clone
- Tickets:
  - `E2E-101`: `agent.kind: opencode`
  - `E2E-102`: `agent.kind: pi`
  - `E2E-103`: `agent.kind: claude`
  - `E2E-104`: `agent.kind: codex`

Each ticket asked the agent to build a zero-dependency static todo app with
add, render, toggle, delete, filters, active count, clear completed, inline edit
commit/cancel, empty state, and `localStorage` persistence, plus verification
evidence.

## Result matrix

| Agent | Dispatch | Generated app | Verification result | Final observed state | Production-readiness verdict |
| --- | --- | --- | --- | --- | --- |
| OpenCode | Started and completed work | Yes | Generated DOM shim: `23 passed, 0 failed`; real Chromium found inline edit Escape-cancel bug | `Verify` | Not ready: Verify accepted incomplete UI behavior |
| Pi | Started and generated work | Yes | Real Chromium full interaction and persistence test passed | `In Progress` at shutdown | Not ready: useful work completed but ticket did not transition |
| Claude Code | Started | No | Three empty turns, then auto-paused | `In Progress`, `paused=true` retry churn | Not ready: empty-response loop is visible but not terminal enough |
| Codex | Started after retry | No | Repeated unattended MCP approval prompt, then stall cancellation | `In Progress` retry loop | Not ready: `approval_policy: never` did not prevent blocking approval prompts |

## Root causes and fixes

### P0. Codex unattended MCP approval stalls

Observed behavior:

- Codex ticket `E2E-104` repeatedly surfaced prompts like:
  `Allow the codebase-memory-mcp MCP server to run tool "index_status"?`
- The worker sat silent until the orchestrator logged `stalled_session`.
- The ticket was then retried and hit the same approval prompt again.

Relevant code:

- `src/symphony/backends/codex.py:675-751` handles notifications.
- `src/symphony/backends/codex.py:815-824` logs unsupported tool requests.
- `src/symphony/backends/codex.py:845-864` normalizes approval/tool events.
- `src/symphony/orchestrator/core.py:3824-3844` cancels stalled sessions.

Fix design:

1. Teach `CodexBackend` to classify MCP elicitation and approval-request
   messages as `EVENT_TURN_INPUT_REQUIRED` unless Symphony can answer them
   programmatically.
2. Under `approval_policy: never`, do not wait for a human prompt. Either:
   - auto-deny the request and emit a clear terminal diagnostic, or
   - preconfigure the worker session so repo MCP tools are disabled/trusted in
     noninteractive worker mode.
3. Add an orchestrator path that treats repeated `turn_input_required` for an
   unattended worker as a blocked outcome, not a fresh continuation.
4. Include the tool/server name in `last_error`, attention payloads, and
   `/api/v1/state` so the operator sees the exact blocker.

Rejected alternative:

- Keep relying on `stalled_session` to cancel Codex. That preserves the loop:
  no decision is made on the approval request, so every retry returns to the
  same prompt.

Tests:

- Fake Codex app-server emits an MCP approval/elicitation request; with
  `approval_policy: never`, assert no wait loop, no `stalled_session`, and a
  blocked/input-required state with the MCP tool name visible.
- Regression: normal Codex token usage and assistant-message notifications
  still update the state API.

Acceptance:

- A Codex ticket that hits an unattended MCP prompt stops in a visible terminal
  attention state within one turn.
- The same prompt is not retried more than the configured retry cap.

### P0. Claude empty-response auto-pause is not an operator-ready terminal state

Observed behavior:

- Claude ticket `E2E-103` completed three turns with no useful message or file
  changes.
- The orchestrator logged `empty_response_loop` and
  `empty_response_loop_auto_paused`.
- `/api/v1/state` kept showing retry metadata for a paused `In Progress`
  ticket, so the board looked unfinished rather than clearly blocked.

Relevant code:

- `src/symphony/orchestrator/constants.py:55-60` sets the empty-turn threshold.
- `src/symphony/orchestrator/core.py:3038-3080` pauses after the threshold.

Fix design:

1. Keep the pause guard, but make it an explicit operator outcome:
   - if `agent.budget_exhausted_state` is configured, persist that state;
   - otherwise move to a configured attention lane or write a blocking marker
     that the scheduler treats as terminal until manual resume.
2. Stop scheduling visible retry timers for paused empty-response tickets.
3. Add a ticket note explaining the last three empty turns and the resume path.
4. Surface the condition in `/api/v1/state` as `kind: empty_response_loop`,
   `paused: true`, and `action: manual_resume_required`.

Rejected alternative:

- Leave the ticket paused only in memory. That protects the worker from
  immediate redispatch but does not create a production-grade audit trail on
  the board or in the API.

Tests:

- Existing empty-turn tests should be extended to assert that a paused empty
  loop does not churn `due_at`.
- File tracker integration test: after three empty turns, the ticket contains a
  human-readable blocker note or has moved to the configured blocked state.

Acceptance:

- Claude empty-output loops become a clear blocked/manual-resume condition, not
  an ambiguous `In Progress` item with retries.

### P0. Pi can complete useful work without ending the stage

Observed behavior:

- Pi ticket `E2E-102` built a working app and evidence files.
- Real Chromium verification passed add, toggle, filter, edit cancel, delete,
  clear completed, and persistence behavior.
- The ticket was still `In Progress` when the service was stopped.
- Token usage was very high for the scope, about 1.49M tokens.

Relevant code paths:

- Stage completion is still worker-driven through ticket edits.
- `src/symphony/orchestrator/core.py:3824-3844` only detects silence stalls,
  not "productive but never transitions" loops.

Fix design:

1. Add a no-stage-change watchdog:
   - track meaningful file changes and token growth per ticket;
   - if a worker produces files/evidence but does not change state after a
     configurable turn/token/time budget, stop the run and write a blocker or
     verification handoff note.
2. Add an optional stage-completion validator for file-board workflows:
   - implementation tickets with app files plus evidence but unchanged state
     can be moved to `Verify` only if the workflow explicitly enables that
     policy;
   - default should be conservative: block with a clear note instead of
     silently advancing.
3. Tighten worker prompts for Pi to require an explicit state transition after
   implementation evidence is complete.

Rejected alternative:

- Auto-advance every changed ticket to `Verify`. That could promote incomplete
  work and hides the fact that a worker ignored the workflow contract.

Tests:

- Fake Pi backend emits high token usage and writes files without changing the
  ticket. Assert the watchdog stops the loop and records the reason.
- Verify that a worker that moves the ticket normally is unaffected.

Acceptance:

- A productive-but-nontransitioning Pi run ends in `Verify` only under an
  explicit policy; otherwise it ends in a clear blocked/handoff state.
- No single todo-app ticket can burn unbounded tokens while remaining active.

### P1. Concurrent after-create worktree setup races on `.git/config`

Observed behavior:

- When concurrency was raised to four, Codex initially failed during
  `after_create`:
  `error: could not lock config file .../.git/config: File exists`.
- A retry later succeeded, but this is not production-safe at higher
  concurrency.

Relevant code:

- `scripts/symphony-setup-worktree.sh:37-46` runs `git worktree remove`,
  `git worktree prune`, and `git worktree add`.
- `scripts/symphony-setup-worktree.sh:53-56` writes worktree config.
- `src/symphony/workspace.py:217-304` runs hooks concurrently through
  `asyncio.to_thread`.

Fix design:

1. Serialize the host-repo git critical section for `after_create`.
2. Prefer a script-level lock so custom workflows that reuse the setup script
   inherit the fix:
   - POSIX: use `flock` on a lock file under `.git/`;
   - fallback: mkdir lock with timeout and stale-lock cleanup.
3. The locked section should cover:
   - `git worktree remove --force`
   - `git worktree prune`
   - `git worktree add`
   - shared config writes that touch host/worktree git metadata
4. Keep venv install outside the lock so slow dependency installation does not
   serialize all agents.

Rejected alternative:

- Lower global concurrency. That hides the race and prevents the requested
  four-agent run from being a valid production test.

Tests:

- Launch four simultaneous workspace creations against the same host repo and
  assert no `.git/config` lock failure.
- Existing Windows junction behavior must remain covered.

Acceptance:

- Four tickets can enter `In Progress` concurrently without hook failures.

### P1. Browser UI Verify must use a real browser for browser apps

Observed behavior:

- OpenCode ticket `E2E-101` generated a DOM-shim harness that passed
  `23 passed, 0 failed`.
- A real Chromium file-url test found inline edit Escape did not cancel the
  changed value. The generated app committed the edited value after Escape.

Root cause:

- The shim did not model the browser's actual keyboard and blur ordering well
  enough.
- The app code committed on blur after Escape instead of suppressing that blur
  commit.

Fix design:

1. Add a browser-app Verify gate template:
   - detect `index.html` or configured browser-app paths;
   - run Playwright/Chromium against `file://` or a tiny local static server;
   - fail if browser dependencies are unavailable unless the ticket explicitly
     records an environment block.
2. Include a canonical todo browser regression:
   - add two tasks;
   - double-click second task;
   - change text;
   - press Escape;
   - assert the old text remains;
   - reload and assert persistence is correct.
3. Keep DOM shims as fast unit smoke only. They cannot be the final Verify
   authority for UI behavior.

Rejected alternative:

- Accept generated zero-dependency test harnesses as sufficient. This run
  proved that such harnesses can miss real browser event-order bugs.

Tests:

- Add a small browser fixture under tests that reproduces Escape-then-blur
  ordering.
- Verify the gate reports missing browser dependencies as `blocked`, not
  `passed`.

Acceptance:

- A browser todo app cannot enter `Learn` from `Verify` unless real Chromium
  interaction passes or the environment block is explicitly recorded.

### P2. Agent telemetry gaps

Observed behavior:

- OpenCode produced substantial work but token counters were not useful in the
  live matrix.
- Pi token totals were visible and showed runaway scale.

Fix design:

1. Normalize token/usage extraction per backend where the CLI exposes it.
2. Add `tokens_per_completed_stage` to the run summary so runaway behavior is
   visible without reading raw logs.
3. Avoid blocking completion on missing token telemetry; this is observability,
   not a worker-success condition.

Acceptance:

- `/api/v1/state` and run history show useful token totals for every backend
  that exposes them.

## Implementation order

1. P0 Codex approval/input-required handling.
2. P0 Claude empty-response terminal attention.
3. P0 Pi no-stage-change watchdog.
4. P1 worktree setup lock.
5. P1 browser Verify gate.
6. P2 token telemetry normalization.

This order stops infinite/unattended loops before improving concurrency and UI
QA. It also keeps each change independently testable.

## End-to-end acceptance test

After the fixes, rerun the same scenario from a fresh temp clone:

```bash
tmp="$(mktemp -d /private/tmp/symphony-e2e-todo-XXXXXX)"
git clone . "$tmp/repo"
cd "$tmp/repo"
# Patch only temp WORKFLOW.md:
# - workspace.root: "$tmp/workspaces"
# - polling.interval_ms: 5000
# - max concurrent agents: 4
symphony doctor ./WORKFLOW.md
symphony board init ./kanban
symphony service start ./WORKFLOW.md --port 10081 --no-viewer
curl -s http://127.0.0.1:10081/api/v1/state
```

Pass criteria:

- OpenCode, Pi, Claude Code, and Codex each produce one of:
  - `Verify` or `Learn` with evidence, or
  - a terminal blocked/manual-resume state with the exact root cause.
- No ticket stays indefinitely in `In Progress` because of an approval prompt,
  empty response loop, missing stage transition, or stall retry loop.
- Four concurrent `after_create` hooks do not fail on `.git/config` locks.
- Browser todo apps pass real Chromium QA before final acceptance.
- `/api/v1/state` shows accurate agent kind, session id, attention reason, and
  token totals where available.

## Verification commands for the implementation branch

```bash
symphony doctor ./WORKFLOW.md
python -m pytest -q tests/test_backends.py
python -m pytest -q tests/test_orchestrator_dispatch.py
python -m pytest -q tests/test_workspace.py
python -m pytest -q tests/test_webapi.py
python -m pytest -q tests/test_web_static_contract.py
git diff --check
```

Add focused tests alongside the implementation if exact filenames differ. The
required coverage is behavior, not these filenames specifically.

## Rollback plan

- Codex approval handling: guard behind backend behavior tests; rollback by
  removing only the approval/input-required branch if it misclassifies normal
  Codex events.
- Empty-response terminal attention: rollback by disabling the new state
  persistence while keeping the existing pause guard.
- Pi watchdog: add a workflow flag for enforcement; rollback by setting it to
  observe-only.
- Worktree lock: rollback by narrowing the lock scope if it causes slow setup,
  but do not remove serialization around `git worktree add`.
- Browser Verify gate: allow an explicit environment-block outcome so CI hosts
  without browsers do not falsely pass.
