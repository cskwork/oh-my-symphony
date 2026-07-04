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
- Known follow-up (not G2-blocking): token usage still reads 0 for opencode
  1.17 — usage lives under the assistant message `info.tokens`, a path
  `_usage_dicts` does not yet traverse. This only affects `max_total_tokens`
  budgeting and telemetry for opencode, not dispatch correctness. Tracked in
  `skills/symphony-skill/reference/troubleshooting.md`.

Operator playbook added: "Backend CLI update broke response parsing" in
`skills/symphony-skill/reference/troubleshooting.md` — the `input_tokens=0`
smoking gun, the capture-the-real-schema recipe, and the fix pattern, so the
next CLI-upgrade drift is a 2-minute triage instead of a rediscovery.

Patch bump 0.9.2 -> 0.9.3 (restores opencode response parsing for opencode >= 1.x).
