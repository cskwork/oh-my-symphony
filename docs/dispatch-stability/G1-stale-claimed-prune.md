# G1 — Stale `_claimed` prune

**Status:** Shipped (commit `54c6c63` on dev → `main` 2026-05-20)
**Test pinning:** `tests/test_orchestrator_dispatch.py::test_g1_stale_claimed_pruned_after_conflict_resolves`

## Beginner view

### What you'd see on the board

CMR-008, CMR-009, CMR-010 all sit in Todo for 45 minutes. The board
viewer shows them eligible. Nothing dispatches. Eventually somebody
restarts the orchestrator and they immediately move.

### What's happening underneath

When two tickets touch the same files at the same time, Symphony marks
the second one as "conflict-blocked" and puts its id into an in-process
`_claimed` set. The set means: "skip this id on the rest of this tick;
the operator's next move can unstick it."

The bug: there was no symmetric *clear* path for that set. Once the
operator moved CMR-008 back to Todo (after fixing the conflict), the id
was still in `_claimed`. Every tick said "skip CMR-008" forever — until
restart wiped the in-memory set.

### The fix in one paragraph

At the top of each `_on_tick`, prune `_claimed` to ids that are still
in `_running` or `_retry`. If the worker that triggered the claim has
exited, the id is no longer "in flight" and the claim must release.
Eligibility still gates re-dispatch through `_eligible`, so a Blocked
ticket stays skipped via *its tracker state*, not via the stale lock.

### How to recognize it's working

Look for `stale_claimed_pruned` events in `log/symphony.log`:

```
ts=...message="stale_claimed_pruned" ids=["CMR-008","CMR-009"]
```

That line means the prune ran and a starved ticket should appear in
the next dispatch loop.

## Expert view

### Code path

`src/symphony/orchestrator/core.py::Orchestrator._on_tick` — after
`_reconcile_running`, before `_fetch_candidates`:

```python
in_flight_ids = set(self._running) | set(self._retry)
stale_claimed = self._claimed - in_flight_ids
if stale_claimed:
    log.info("stale_claimed_pruned", ids=sorted(stale_claimed))
    self._claimed -= stale_claimed
```

`_turn_budget_exhausted` is pruned with the same invariant in the
same block.

### Invariant

`_claimed ⊆ _running ∪ _retry` at the start of every dispatch loop.

Anything outside that set is either (a) a closed worker whose id
linger-leaked, or (b) a retry timer that already fired and removed
itself. Either way, the live tracker state — not the in-memory lock —
is the source of truth for "should we dispatch."

### Why the invariant is safe

The three call sites that add to `_claimed`
(`_block_ticket_for_conflict`, `hit_max_turns`, the token/turn budget
exhaustion paths) all run *while* the ticket is in `_running` (or about
to be). So when `_running.pop()` happens in `_on_worker_exit`, the only
correct next state for that id is "no longer claimed." The prune makes
that automatic instead of relying on the exit path to remember.

### Failure mode it replaces

Before G1, `_claimed` was monotonically growing within a process
lifetime. A single conflict block effectively burned a slot of attention
forever. Restart was the only way to recover.

### Risk surface

The prune deletes only entries whose backing worker is gone, so a
mid-tick race where the worker exits *during* the dispatch loop is
already safe: `_eligible` will refuse to dispatch a ticket whose
tracker state is terminal (Blocked / Done / Cancelled), independently
of `_claimed`.

### Related

- [G3](./G3-wait-age-bump.md) builds on this prune: it records the
  release timestamp inside the same block so the dispatch sort can
  promote ticket waiting longer than `WAIT_AGE_BUMP_MIN`.
- `_reconcile_running` runs immediately before the prune; that's where
  in-flight workers are eligible to be cancelled or force-ejected.
  Without that step finishing first, the prune would see stale `_running`
  membership.
