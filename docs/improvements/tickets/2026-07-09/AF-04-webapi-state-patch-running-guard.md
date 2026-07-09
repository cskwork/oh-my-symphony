# AF-04 — Web API issue PATCH mutates a running ticket's state unguarded

Route: DEBUG | Severity: P1 | Confidence: CONFIRMED | Blocked by: none

## Defect

`handle_issue_patch` (`src/symphony/webapi.py:549-596`) accepts a `state`
field and writes it straight through `tracker.update_fields` with **no
running-worker guard** — unlike its siblings `handle_issue_delete`
(`webapi.py:625`) and `handle_states_put` (`webapi.py:684-697`), which both
consult the orchestrator's running set and 409.

Consequences when a PM drags a card whose ticket has a live worker:

- Drag to **Done**: within the reconcile grace the terminal branch cancels
  the worker and calls `_auto_merge_done_gate_or_block`
  (`core.py:5439-5531`) on the half-finished branch — premature merge-gate
  entry on partial work.
- Drag to any other column: the worker loop treats it as a phase transition
  (`core.py:3495-3497`) — spurious contract eval / backend rebuild / rewind.

The Kanban audience explicitly includes non-developer PMs, so this is an
operator-reachable footgun, not a hypothetical.

## Fix direction

In `handle_issue_patch`, when `state` is present and differs from the current
state, apply the same guard as delete/states_put: if
`orchestrator.find_running_issue_id(identifier)` (or the running-states
check) matches, return 409 `state_in_use` with a "pause or wait" hint.
Non-state field edits (title, priority, labels, …) on a running ticket remain
allowed.

## Acceptance checks

- [ ] RED first: web API test PATCHing `{state: "Done"}` for an id present in
  `_running` expects 409 — fails on current `main` (currently 200).
- [ ] WHEN the ticket is running THEN a state PATCH returns 409 and the board
  file is unchanged; non-state PATCHes still return 200.
- [ ] WHEN the ticket is idle THEN state PATCH behaves exactly as today
  (existing tests stay green).
- [ ] Full suite green, including web API tests.

## Non-goals

Board-file locking semantics (AF-12); TUI-side guards; changing the
reconcile terminal branch itself.

## Resolution — 2026-07-10

Resolved by rejecting only a running ticket's actual state change before any
field is written. Running metadata and same-state edits remain allowed, and
idle state changes retain their existing behavior. Evidence:
`tests/test_webapi.py` focused PATCH regressions.
