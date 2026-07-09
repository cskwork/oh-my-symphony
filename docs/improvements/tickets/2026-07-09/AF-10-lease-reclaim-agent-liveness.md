# AF-10 — Lease reclaim checks orchestrator PID, not the live agent subprocess

Route: LEGACY | Severity: P2 | Confidence: PLAUSIBLE | Blocked by: AF-02

## Defect

`reclaim_dead_owner_leases` (`orchestrator/run_registry.py:270-316`) reclaims
a lease when the **orchestrator** owner PID is dead. Agent subprocesses run
in their own process groups and survive an orchestrator crash. On restart the
lease is reclaimed, the ticket re-dispatched, and `create_or_reuse` binds the
same per-identifier worktree — the orphaned old agent and the new agent then
write the same worktree/branch concurrently (lost work, git index races).

## Fix direction

Persist the agent pid/pgid in the lease record (available once AF-02 records
it on the entry). During startup recovery, before re-dispatching a reclaimed
lease: check agent-process liveness and `kill_process_group` any recorded
survivor, logging `reclaim_killed_orphan_agent`.

## Acceptance checks

- [ ] RED first: recovery test with a lease whose owner PID is dead but whose
  recorded agent pgid is alive (spawn a dummy sleeper); assert the group is
  killed before re-dispatch (fails on current `main`).
- [ ] WHEN no agent pid is recorded (old lease format) THEN reclaim behaves
  as today (backward compatible).
- [ ] Full suite green, including run-registry tests.

## Non-goals

Force-eject kill coverage (AF-02); worktree-per-attempt isolation redesign.

## Resolution — 2026-07-10

Resolved with a backward-compatible two-phase recovery state: dead-owner rows
first become lease-blocking `reclaiming`, the recorded process group is reaped
outside SQLite, and only then is the row finalized as `orphaned`. Interrupted
or failed cleanup stays fenced and is retried on the next startup. Legacy
null-pid rows retain their previous effective reclaim behavior.
