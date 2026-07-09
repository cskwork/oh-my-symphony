# AF-05 — Per-turn backends: preview-key mismatch and exit-0/empty-stdout

Route: DEBUG | Severity: P1 | Confidence: CONFIRMED | Blocked by: none

Two defects in the same seam (turn-completion content contract for the
per-turn CLI family). Fix together; test separately.

## Defect 1 — productive turns read as empty → false empty-loop cancellation

`_preview_from_payload` (`core.py:4262-4283`) extracts turn text only from
payload keys `message` / `lastMessage` / `text` / `summary` (or nested
`item`). But `PlainCliBackend._complete_turn`
(`backends/plain_cli.py:44-59`, used by agy/kiro) and
`GeminiBackend` (`backends/gemini.py:64-76`) emit the assistant text under
`result`/`response` only — and emit no intermediate events. So
`current_turn_message` stays empty on every productive turn, G2
(`core.py:4483-4508`, threshold `EMPTY_TURN_LOOP_THRESHOLD=3`,
`orchestrator/constants.py:66`) counts 3 "empty" turns in a stage, and the
worker is cancelled/paused as an empty-response loop while doing real work.
opencode escaped this exact bug only because its 0.9.2 fix added a top-level
`"message"` key (`opencode.py:105-114`); codex emits a string `message`
(`codex.py:742-749`). Claude emits the raw assistant frame whose `message` is
a **dict** (`claude_code.py:314`) — affected in principle, usually masked by
stage-transition counter resets.

## Defect 2 — exit 0 with empty stdout is a "completed" turn

`PerTurnCliBackend` trusts the exit code alone (`per_turn.py:185-190`):
zero → `_complete_turn(stdout_text)` even when stdout is empty. claude/pi
raise when their terminal event is missing; the plain family has no content
check, so a silent no-op turn logs `worker_turn_completed` and consumes a
turn slot as if productive.

## Fix direction

1. Backend-side (decided; mirrors the opencode fix): add a `"message"` key
   carrying the response text to the `EVENT_TURN_COMPLETED` payload in
   `PlainCliBackend` and `GeminiBackend`. For claude, flatten the assistant
   `message.content` text blocks into a string preview (either in the backend
   payload or as a narrow dict-handling branch in `_preview_from_payload`).
2. Treat exit-0-with-empty-stdout in the per-turn family as a soft turn
   failure (distinct event/flag), not a completed productive turn.

## Acceptance checks

- [ ] RED first: contract test asserting every backend's `TURN_COMPLETED`
  payload yields a non-empty `_preview_from_payload` result for a productive
  turn — fails for plain/gemini (and claude) on current `main`.
- [ ] RED first: drive 3 consecutive productive plain-CLI turns through
  `_on_codex_event`; assert `consecutive_empty_turns` stays 0 and no
  `empty_response_loop` flag — fails on current `main`.
- [ ] WHEN a per-turn CLI exits 0 with empty stdout THEN the turn is surfaced
  as failed/flagged, not `worker_turn_completed`
  (`PerTurnBackendContract` gains an exit-0/empty-stdout case).
- [ ] G2's real purpose intact: an actually-empty 3-turn loop still trips the
  guard. Full suite green.

## Non-goals

StreamingCliBackend extraction (07-07 P1-5); changing the G2 threshold;
token accounting (AF-14).
