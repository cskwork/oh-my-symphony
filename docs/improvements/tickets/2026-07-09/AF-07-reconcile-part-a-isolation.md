# AF-07 — Reconcile Part A: per-issue isolation + cancelled-zombie vs pause ordering

Route: LEGACY | Severity: P2 | Confidence: CONFIRMED (ordering) / PLAUSIBLE (isolation) | Blocked by: none

Two edges of the same reconcile Part A loop (`core.py:5325-5371`).

## Defect A — pause check runs before the force-eject escalation

The `is_paused → continue` at `core.py:5336-5337` is evaluated before the
`cancelled_at > grace` force-eject at `:5343-5353`. Pausing a worker that was
already stall-cancelled (still "running", so `pause_worker` accepts it)
suppresses the force-eject for the whole pause — the cancelled zombie holds
its slot indefinitely, defeating the grace window's slot-leak protection.

## Defect B — Part A lacks the per-issue try/except Part B has

Part B wraps each `_reconcile_one` (`core.py:5408-5425`, R8); Part A does
not. If `kill_process_group` or `_finish_run_lease` raises inside
`_force_eject_zombie`, the entry is already popped (`:4995-4996`) but
`_schedule_retry` (`:5013`) never runs — ticket orphaned until restart — and
the exception aborts stall checks for all remaining issues that tick.

## Fix direction

- Evaluate the `cancelled_at` escalation before (or independent of) the
  `is_paused` skip — a system-cancelled worker is not an operator hold.
  Alternatively reject `pause_worker` when `cancelled_at` is set.
- Wrap Part A's per-issue body in the same isolating try/except as Part B;
  inside `_force_eject_zombie`, ensure the retry is scheduled even when the
  kill/lease steps raise (schedule first or use `finally`).

## Acceptance checks

- [ ] RED first: entry with `cancelled_at` past grace + paused → assert
  force-eject still fires (fails on current `main`).
- [ ] RED first: `kill_process_group` raising during force-eject → assert
  the retry is still scheduled AND remaining running issues are still
  reconciled that tick (fails on current `main`).
- [ ] Two-stage timing unchanged: cancel → grace → eject intervals identical
  to today (`test_reconcile_force_ejects_zombie_after_grace` green).
- [ ] Full suite green.

## Non-goals

Exit identity checks (AF-01); killing non-codex processes (AF-02);
resume stamping (AF-03).
