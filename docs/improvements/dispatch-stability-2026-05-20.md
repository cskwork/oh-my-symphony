# Dispatch stability — 2026-05-20 punch list

Captured from a live OLV/CMR diagnostics run. G1 shipped today; G2–G5
are deferred but planned in detail so the next session can pick up
without re-discovering the surface.

## G1 — `_claimed` prune at `_on_tick` start (SHIPPED — dev `54c6c63`)

### Symptom
`_block_ticket_for_conflict`, `hit_max_turns`, and the token / turn
budget paths add a ticket id to `self._claimed` to keep it out of the
current tick. The set had no symmetric clear path, so once the worker
that triggered the lock exited and the operator moved the ticket back
to Todo, the entry remained sticky for the rest of the session. Live:
`CMR-008/009/010` starved for ~45 min.

### Fix
At the top of `_on_tick`, after `_reconcile_running`, prune `_claimed`
and `_turn_budget_exhausted` to the ids currently in
`_running ∪ _retry`. Stale entries release the moment the worker is
gone; live tracker state still gates re-dispatch via `_eligible`.

### Test
`tests/test_orchestrator_dispatch.py::test_g1_stale_claimed_pruned_after_conflict_resolves`.
Full dispatch suite 72/72.

---

## G2 — silent empty-response loop (DEFERRED)

### Symptom
When the agent returns an empty `last_message` on many consecutive
turns, the only floors that catch the loop are `max_total_turns` and
`max_total_tokens`. Operators see a long, slow burn instead of a fast
Blocked escalation.

### Plan
- Add `RunningEntry.consecutive_empty_turns: int = 0`.
- In the `EVENT_TURN_COMPLETED` branch of `_on_codex_event`: reset to
  `0` when `entry.last_codex_message.strip()` is non-empty; otherwise
  increment.
- On hitting threshold (start at `3`): log `empty_response_loop`, set
  `debug.last_error`, reuse `_persist_budget_exhausted_state` with a
  new `budget_kind="empty_response_loop"`. Worker cancel +
  `cancelled_at` so the existing exit / persistence flow fires.

### Touch points
- `orchestrator/entries.py` — extend `RunningEntry`.
- `orchestrator/core.py` — `_on_codex_event` `EVENT_TURN_COMPLETED`
  block (~line 2235) and `_persist_budget_exhausted_state` budget_kind
  literal set.

### Risk
False positives on tool-only chains. Mitigation: only count
*consecutive* empties on `EVENT_TURN_COMPLETED` (not on inter-turn
events) and keep the threshold conservative at 3.

---

## G3 — restored-card starvation in dispatch sort (DEFERRED)

### Symptom
`_sort_for_dispatch_fifo` is pure registration order
(`OLV-001` < `OLV-002` < …). A card that spent 45 min in a
Blocked/conflict cycle and only just re-entered the candidate set
still sorts behind unrelated numbered tickets, even though it has been
waiting longest in wall-clock time.

### Plan
- Record the moment a ticket leaves `_claimed` (the new G1 prune
  block) in `self._claim_released_at: dict[str, datetime]`.
- Extend `_sort_for_dispatch_fifo` (or its caller) so candidates whose
  `_claim_released_at` is older than `WAIT_AGE_BUMP_MIN` (default 10
  min) get bumped ahead of registration order, while keeping numbered
  FIFO as the dominant key for fresh tickets.

### Touch points
- `orchestrator/core.py` — `_on_tick` (record release timestamp inside
  the new prune block).
- `orchestrator/helpers.py` — `_sort_for_dispatch_fifo`.
- `tests/test_orchestrator_dispatch.py` — new regression.

### Risk
Priority inversion against newer high-priority tickets. Mitigation:
gate the bump on a generous wait-age threshold so it only fires on
starvation cases, not normal ordering.

---

## G4 — TUI mode file-logging gap (DEFERRED)

### Symptom
`log/symphony.log` is almost empty when Symphony is launched via the
TUI launcher; the same workload run headless produces a full
structured log. Post-hoc diagnostics depend on the file log, so this
leaves TUI sessions unobservable.

### Plan
- Extract the headless file-handler setup into a reusable helper in
  `logging.py` (idempotent — no-op when a `FileHandler` for the same
  path is already attached).
- Call the helper from both the headless service entry point and the
  TUI app startup (before `App.run()`), defaulting to
  `log/symphony.log` when `SYMPHONY_LOG_FILE` is unset.

### Touch points
- `src/symphony/logging.py`
- `src/symphony/tui/app.py`
- `tui-open.sh` — only if env defaulting moves into the script.

### Risk
Double-handler duplication. Mitigation: the idempotent helper.

---

## G5 — stale `## Conflict` / `## Budget Exceeded` sections on restore (DEFERRED)

### Symptom
When an operator moves a ticket back from Blocked → Todo, the markdown
body still carries the `## Conflict` or `## Budget Exceeded` heading
from the previous block. Board UIs keep showing the warning long after
it stopped applying.

### Plan
- In `tracker/file.py:update_state`, when the target state is in
  `active_states`, strip orchestrator-owned heading blocks
  (`## Conflict`, `## Budget Exceeded`).
- Non-file trackers (Jira / Linear) get a no-op for now — those bodies
  are owned by the remote tracker and we will not rewrite them
  silently.

### Touch points
- `src/symphony/tracker/file.py` — `update_state` + a new strip
  helper.
- `tests/test_tracker_file.py` — regression.

### Risk
Stripping legitimate operator-authored content that happens to use the
same heading. Mitigation: only strip sections whose body matches the
shape `_tracker_call_append_note` produces for those headings, and
only on transitions *into* an active state.
