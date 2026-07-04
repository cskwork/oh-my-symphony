# 2026-07-04 - README 9999 admin UI showcase

## Decision

Feature the built-in 9999 browser admin UI before the terminal TUI in both
README surfaces. The screenshot asset lives at `docs/admin-ui-screenshot.png`
and is rendered from the real static web app with sanitized demo board data, so
the public README shows the prettier operator surface without exposing a live
workflow's ticket content.

- Rejected: using the legacy `tools/board-viewer` screenshots. They show the
  secondary viewer, not the 9999 orchestrator admin app the operator asked to
  showcase.
- Rejected: screenshotting the currently running `jira-symphony` server
  directly. The app was live on `127.0.0.1:9999`, but public README assets
  should not include real local ticket data.
- Rejected: replacing the terminal screenshot. The TUI is still a major
  operator surface; the README should show both, with the 9999 UI first.

## Verification

- Generated `docs/admin-ui-screenshot.png` from the real web app static assets
  at the `http://127.0.0.1:9999/#/board` route with mocked API payloads and
  Playwright Chromium `--single-process`.
- Visually inspected the generated image: nonblank, board columns rendered,
  live/retry badges visible, terminal-state group visible.
- `git diff --check` -> pass.

---

# 2026-07-04 - Fix G2 empty-response-loop false-positive on OpenCode

## Goal

Stop the G2 empty-response-loop guard from falsely blocking healthy OpenCode
tickets. A ticket (observed in `Learn`) was moved to `Blocked` with
`empty_response_loop budget exceeded (consecutive_empty_turns=3, threshold=3)`
despite the agent producing real output every turn.

## Root cause

G2 counts a turn as empty when `RunningEntry.current_turn_message` is blank at
`EVENT_TURN_COMPLETED` (`orchestrator/core.py`). `current_turn_message` is only
populated when `_preview_from_payload(payload)` returns text, and that helper
reads the preview keys `message` / `lastMessage` / `text` / `summary` / `item`.

OpenCode's `EVENT_TURN_COMPLETED` payload delivered its response text only under
`result` / `response` (`backends/opencode.py`) — keys the preview helper never
inspects. So every OpenCode turn looked empty, the counter climbed monotonically
regardless of real work, and G2 escalated after exactly 3 turns.

Why it surfaced now: the recent OpenCode liveness-heartbeat fix (stall-loop)
stopped long turns from being stall-cancelled. Before it, each stall-cancel
minted a fresh `RunningEntry` (counter reset to 0), so turns never accumulated to
3 on one entry. Removing the false stall-kill exposed the latent G2 miscount —
same bug class (OpenCode's batch-per-turn shape not fitting an event model built
for streaming backends), next guard.

## Decisions

### 1. Emit the response under a preview key from the backend

`run_turn`'s `EVENT_TURN_COMPLETED` payload now carries `message` (= the response)
alongside the existing `result` / `response`. `_preview_from_payload` reads
`message` first, so a productive turn populates `current_turn_message` and resets
the G2 counter. A genuinely empty turn (`response == ""`) still yields no preview
text, so real empty-loops are still caught. Bonus: the OpenCode TUI preview
(`last_codex_message`, same code path) was always blank before and now shows the
turn's response.

- Rejected: **raising `EMPTY_TURN_LOOP_THRESHOLD`** (the operator's first
  hypothesis). It is a band-aid — OpenCode would still false-block after N turns
  instead of 3, and a higher threshold weakens genuine empty-loop detection for
  every backend. It does not address the miscount.
- Rejected: **teaching `_preview_from_payload` to read `result` / `response`.**
  That is shared code on the hot event path for all backends; `result` /
  `response` can carry non-message content for other drivers, risking masked
  empty-turns elsewhere. The backend-local fix keeps the blast radius to OpenCode
  and matches the sibling heartbeat fix's "backend quirks live in the backend"
  precedent.

## Verification

- `backends/opencode.py` — 1 key added to the turn-completed payload.
- New `tests/test_backends.py::test_opencode_turn_completed_payload_carries_message_for_preview`
  asserts the emitted payload carries `message`.
- New `tests/test_orchestrator_dispatch.py::test_g2_opencode_shaped_payload_resets_only_with_message_key`
  drives the real `_on_codex_event` path: `result`/`response`-only payloads still
  count as empty (climb 1 -> 2); adding `message` resets the counter to 0.
- Full suite: 1058 passed, 2 skipped. No shared code touched;
  `EMPTY_TURN_LOOP_THRESHOLD` unchanged at 3.

Patch bump 0.9.1 -> 0.9.2 (fix restores intended behavior).

## Follow-up (0.9.3) — the dominant cause: opencode 1.17 schema drift

Live verification against the paused `jira-symphony` board (TASK-001, blocked
in `Learn` by exactly this loop) showed 0.9.2 alone was necessary but not
sufficient. Every `agent_turn_completed` reported `input_tokens=0
output_tokens=0` with an empty `last_message` — yet opencode had done real work
(glm-5.2, 8 agentic steps, 27-file commits). Input tokens cannot be zero for a
12 KB prompt: Symphony's opencode backend was parsing *nothing* out of
opencode 1.17.13's output. So `response` itself was empty, and 0.9.2's
`message: response` carried an empty string — the preview stayed blank and G2
still counted every turn empty.

Root cause: `opencode run --format json` (src/cli/cmd/run.ts `emit`) streams
JSONL frames `{"type": ..., "sessionID": ..., "part": {...}}`; assistant prose
is in `type=="text"` frames under `part.text`. Symphony's `_extract_text` only
scanned flat keys (`response`/`result`/`message`/`text`/`content`/`output`) plus
`data`, so it never saw `part.text` and returned "". (Confirmed by unit probe:
`_extract_text({"type":"text","part":{"text":"..."}}) == ""`.)

Fix: `OpenCodeBackend._text_from_event` reads `part.text` from `type=="text"`
frames, keeping the flat-key scan as a fallback for the raw-stdout and
non-opencode shapes. tool_use/step_start frames carry no prose, so a genuinely
empty turn (no text frame) still counts as empty — G2's real detection is
preserved. New `tests/test_backends.py::test_opencode_extracts_text_from_jsonl_part_frames`
pins the JSONL shape.

- Rejected: **a deep/generic text search** across every nested dict — risks
  grabbing tool-call arguments and file contents as "response". The
  `type=="text"` predicate is precise to opencode's serialization.
- Known follow-up (not G2-blocking, fixed in the v0.9.3 release update below): token usage still
  reads 0 for opencode 1.17. This only affects `max_total_tokens` budgeting and
  telemetry for opencode, not dispatch correctness. Tracked in
  `skills/symphony-skill/reference/troubleshooting.md`.

Operator playbook added: "Backend CLI update broke response parsing" in
`skills/symphony-skill/reference/troubleshooting.md` — the `input_tokens=0`
smoking gun, the capture-the-real-schema recipe, and the fix pattern, so the
next CLI-upgrade drift is a 2-minute triage instead of a rediscovery.

Patch bump 0.9.2 -> 0.9.3 (restores opencode response parsing for opencode >= 1.x).

## Follow-up (v0.9.3 release update) — OpenCode 1.17 token accounting

Live probe:

```bash
opencode run --format json --auto "reply with DONE only"
```

OpenCode 1.17.13 emitted usage on a `step_finish` frame:
`part.type == "step-finish"` with `part.tokens` containing `input`, `output`,
`reasoning`, `cache.read`, `cache.write`, and `total`. Symphony's `_usage_dicts`
never descended through `part`, so the backend emitted useful `message` text but
left every token bucket at 0. That matches the TASK-002 monitor evidence:
nonblank `last_message`, `input_tokens=0`, then a separate
`worker_exit reason=issue_state_refresh_failed` after the turn.

Fix: `OpenCodeBackend` now descends into `part` and `info` usage containers,
reads `totalTokens` as an alias, folds nested `cache.read` / `cache.write` into
input-side tokens, and treats `reasoning` as output-side tokens when totals need
to be derived. New
`tests/test_backends.py::test_opencode_extracts_usage_from_jsonl_step_finish_part_tokens`
pins the real OpenCode 1.17 `step_finish.part.tokens` schema.

- Rejected: **deep traversal of every nested dict**. OpenCode frames also carry
  tool and text payloads; scanning all children would risk treating user-facing
  content or tool arguments as telemetry. The parser only follows known telemetry
  containers.
- Not part of this fix: `issue_state_refresh_failed`. The failure fires after a
  successful turn when tracker state refresh returns `None`; it is a separate
  board/workspace reliability issue, not a token parser symptom.

Included in the v0.9.3 release tag update (restores OpenCode token telemetry for opencode >= 1.x).

## Follow-up (v0.9.3 release update) — issue-state refresh RCA and operator UI

Live `jira-symphony` showed TASK-002 stuck in paused retry with
`worker_exit reason=issue_state_refresh_failed` after a successful nonblank
OpenCode turn. This was separate from the token parser issue. The ticket file
had malformed YAML:

```yaml
created_at: '2026-07-04T00:00:02Z'
  updated_at: '2026-07-04T02:35:57Z'
```

That made the file tracker skip TASK-002, so the worker could not refresh the
post-turn state and paused for operator inspection. Current-board repair was to
unindent `updated_at`, after which `symphony board ls --workflow ...` showed
TASK-002 back in `Learn`, and `POST /api/v1/TASK-002/resume` moved it from
`retrying` to a running `Learn` retry.

Hardening: `parse_ticket_file` now auto-heals the common case where an agent
accidentally indents a canonical top-level key by one or two spaces. It does not
rewrite the file during read, but the next normal tracker write serializes
canonical YAML. New
`tests/test_tracker_file.py::test_parse_ticket_file_auto_heals_misindented_updated_at`
pins the observed TASK-002 shape.

Operator UI: Settings now shows the workflow default agent on both web surfaces.
The 9999 Symphony app renders `Default agent` from `/api/v1/workflow.agent.kind`
under `Settings > Board info`; the board-viewer Settings modal renders
`Agent default` from `/api/symphony/state.workflow.default_agent_kind`. The value
on the live board is `opencode`.

- Rejected: **only patch the current ticket**. That restores one board but lets
  the next agent-authored YAML indentation mistake remove a ticket from refresh.
- Rejected: **deep YAML text surgery on every read**. The heal only runs after a
  YAML parse failure and only unindents known ticket frontmatter keys with shallow
  accidental indentation, avoiding legitimate nested data such as `agent.kind`
  and `blocked_by[].state`.

## Follow-up (operator) — TASK-003 workspace collision RCA

After TASK-002 resumed and completed, live `jira-symphony` moved to TASK-003
and paused before the first agent turn:

```text
hook before_run exited 42;
workspace kanban points to /Users/danny/Documents/PARA/Resource/learn-codex-kr/kanban,
expected /Users/danny/Documents/PARA/Resource/jira-symphony/kanban
```

The path `/Users/danny/symphony_workspaces/TASK-003` was not a `jira-symphony`
worktree. It was a clean, older `learn-codex-kr` worktree registered under
`learn-codex-kr/.git/worktrees/TASK-003`, with `kanban` linked to that project's
board. Symphony derives workspace paths from `workspace.root / ticket-id`, so
two boards using the same global `~/symphony_workspaces` root and the same
identifier (`TASK-003`) collide.

Operator fix: change the `jira-symphony` workflow root to
`~/symphony_workspaces/jira-symphony`, then resume the ticket. The orchestrator
reloads WORKFLOW on each tick and rebuilds the `WorkspaceManager` when
`workspace.root` changes, so no stale foreign worktree has to be deleted.

- Rejected: **remove the foreign worktree as the primary fix**. Its status was
  clean, but it belongs to another project; deleting it would only free this one
  identifier and would not prevent the next cross-board `TASK-*` collision.
- Rejected: **only patch the `kanban` symlink inside the existing directory**.
  That would mutate a worktree registered to `learn-codex-kr` and mix project
  ownership in one checkout.
