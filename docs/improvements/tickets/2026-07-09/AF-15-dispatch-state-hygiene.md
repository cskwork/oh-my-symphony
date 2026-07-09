# AF-15 — Dispatch-state hygiene: `_completed` / `_issue_debug` unbounded growth

Route: LEGACY | Severity: P3 | Confidence: CONFIRMED | Blocked by: none

## Defect

- `DispatchState.completed` (`dispatch_state.py:39`) is written on every
  completion (`core.py:4836`) and **read nowhere** in `src/symphony/`; never
  pruned, not cleared by `stop()` (`core.py:1080-1087`).
- `_issue_debug` (`core.py:521`) is `setdefault`-only — grows with every
  distinct ticket id for process lifetime.

A long-lived orchestrator polling for weeks leaks memory proportional to
total tickets ever processed; state also survives in-process stop/restart.

## Fix direction

Delete `_completed` if truly reader-less (check TUI/web consumers first via
the `completed` property, `core.py:609`); otherwise bound both structures
(prune on terminal persist or LRU) and clear them in `stop()` with the other
collections.

## Acceptance checks

- [ ] Either `_completed` removed (and property consumers migrated) or a
  bound is enforced by test.
- [ ] `stop()` clears/bounds `_completed` and `_issue_debug`
  (`tests/test_dispatch_state.py` + a stop() assertion).
- [ ] Full suite green.

## Non-goals

`_persisted_retry_attempts` semantics (persisted by design — verify before
touching); any dispatch-behavior change.
