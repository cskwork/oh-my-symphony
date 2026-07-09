# AF-08 — `stop()` awaits worker tasks unbounded

Route: LEGACY | Severity: P2 | Confidence: PLAUSIBLE | Blocked by: none

## Defect

`stop()` cancels every worker task then `await entry.worker_task` in a loop
with no timeout (`core.py:1064-1075`); `_drain_background_tasks`
(`core.py:665-678`) is bounded but the worker loop is not, and callers await
`stop()` unwrapped (`cli/main.py:236,308`). The whole force-eject machinery
exists because a worker can wedge in an await that ignores cancellation — if
shutdown fires while one is wedged, `stop()` hangs and the process must be
killed externally (which is exactly the messy teardown the two-stage eject
was built to avoid).

## Fix direction

Bound the worker drain (`asyncio.wait(..., timeout=...)` sized above the
force-eject grace); for tasks still pending past the bound, run the
force-eject path (kill recorded process group — synergizes with AF-02, but
do not hard-depend on it) and proceed with shutdown, logging each abandoned
task.

## Acceptance checks

- [ ] RED first: `stop()` with a worker task shielded from cancellation must
  return within the bound (fails/hangs on current `main` — use a test
  timeout).
- [ ] WHEN all workers cancel promptly THEN `stop()` behavior and ordering
  are unchanged (existing stop/drain tests green).
- [ ] Full suite green.

## Non-goals

Signal handling in launchers; changing `_drain_background_tasks`' bound.

## Resolution — 2026-07-10

Resolved with a bounded worker-task drain using the existing force-eject grace
window. Survivors are logged and their recorded process group is killed when
available. Every cancellation-resistant survivor's run lease is finalized as
`shutdown_abandoned` before in-memory ownership is cleared and the registry is
closed; prompt cancellation behavior remains green.
