# G2 — Empty-response loop guard

**Status:** Shipped (commit `161d4e3` on dev → `main` 2026-05-20)
**Tests:** `test_g2_empty_response_loop_escalates_after_three_consecutive_turns`,
`test_g2_empty_response_loop_resets_on_non_empty_turn`,
`test_g2_empty_response_loop_no_op_when_budget_state_unset`,
`test_g2_empty_loop_does_not_double_cancel_after_threshold`

## Beginner view

### What you'd see on the board

A ticket sits in In Progress for half an hour. Token total keeps
crawling up. No turn produces visible output. Eventually
`max_total_turns` or `max_total_tokens` finally trips — but you just
burned through the entire budget on a no-op loop.

### What's happening underneath

The agent returned an "empty message" turn — sometimes a tool-only chain
that produced no model output, sometimes a degenerate failure mode. The
stall detector doesn't fire because turns *are* completing (just with
no content). The only floor that catches it is the per-attempt
`max_total_turns` ceiling, which is intentionally generous.

### The fix in one paragraph

We count *consecutive* empty `EVENT_TURN_COMPLETED` events on each
worker. After 3 in a row, we cancel the worker and persist a "Blocked
— empty_response_loop" state via the same path that `tokens` and
`max_turns` budget exhaustion already use. A single non-empty turn
resets the counter so a brief tool-only stretch doesn't escalate.

### How to recognize it's working

```
ts=...message="empty_response_loop" issue_id=MT-1 identifier=MT-1
       consecutive_empty_turns=3 threshold=3
ts=...message="budget_exhausted_persisted" issue_id=MT-1
       budget_kind=empty_response_loop target_state=Blocked
```

Two log lines, one ticket transitions to your configured
`budget_exhausted_state` (typically Blocked) within seconds.

## Expert view

### Code path

Three modules:

- `src/symphony/orchestrator/constants.py`
  ```python
  EMPTY_TURN_LOOP_THRESHOLD = 3
  ```

- `src/symphony/orchestrator/entries.py::RunningEntry`
  ```python
  consecutive_empty_turns: int = 0
  current_turn_message: str = ""  # per-turn buffer; reset at TURN_COMPLETED
  ```

- `src/symphony/orchestrator/core.py::Orchestrator._on_codex_event` —
  in the `EVENT_TURN_COMPLETED` branch, after the EMA update:
  ```python
  if entry.current_turn_message.strip():
      entry.consecutive_empty_turns = 0
  else:
      entry.consecutive_empty_turns += 1
  entry.current_turn_message = ""
  if (entry.consecutive_empty_turns >= EMPTY_TURN_LOOP_THRESHOLD
          and entry.cancelled_at is None):
      ...
      await self._persist_budget_exhausted_state(
          cfg=cfg, entry=entry, issue_id=issue_id,
          target_state=cfg.agent.budget_exhausted_state,
          budget_kind="empty_response_loop",
      )
      entry.worker_task.cancel()
      entry.cancelled_at = datetime.now(timezone.utc)
  ```

`_persist_budget_exhausted_state` gained a new branch for the
`empty_response_loop` `budget_kind` so the persisted note carries
the right reason in the tracker body.

### Invariant

`consecutive_empty_turns ≤ EMPTY_TURN_LOOP_THRESHOLD` at all times for
any `RunningEntry`. Once the threshold is reached, exactly one
escalation fires (guarded by `entry.cancelled_at is None`).

### Why a per-turn buffer (`current_turn_message`)?

`last_codex_message` is updated *only* when a payload yields preview
text (`if msg: entry.last_codex_message = msg[:400]`). That makes it
sticky across turns. If turn 1 produced "X" and turn 2 produced
nothing, `last_codex_message` is still "X" at turn 2's TURN_COMPLETED —
the guard would falsely conclude the turn was non-empty and reset the
counter.

The per-turn buffer is updated on the same preview hook but cleared at
TURN_COMPLETED *after* the empty check. That makes "did this turn
produce output" answerable from one field with one rule.

### Failure mode it replaces

Pre-fix, the only floors were:
- `max_total_turns` — generous, multi-stage
- `max_total_tokens` — generous, ceiling per state
- stall detector — only catches "no events at all" (turns *were*
  arriving, so stall stayed quiet)

Net effect: 30+ minutes of silent burn before any signal reached the
operator.

### Risk surface

False positives on tool-only turns. Mitigated by:
1. Threshold of 3 consecutive empties (not 1 or 2).
2. Counter resets on the *first* non-empty turn, so any single real
   model output unsticks the worker.
3. `current_turn_message` only counts preview text, not raw tool calls
   that already populate other event fields.

If a workflow legitimately runs >3 turns of pure tool execution with
no model commentary, raise `EMPTY_TURN_LOOP_THRESHOLD` for that
workflow. (No config knob yet — open an issue if you need one.)

### Related

- `_persist_budget_exhausted_state` is the shared persistence helper
  that G2, `max_total_tokens`, and `max_total_turns` all use. It
  guarantees the tracker write happens even if the worker is mid-cancel.
- The `cancelled_at` guard piggybacks on the same field the
  `max_total_tokens` path uses, so the stall reconciler's
  `STALL_FORCE_EJECT_GRACE_S` window applies the same way.
