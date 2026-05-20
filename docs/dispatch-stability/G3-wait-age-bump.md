# G3 — Wait-age dispatch bump

**Status:** Shipped (commit `161d4e3` on dev → `main` 2026-05-20)
**Tests:** `test_g3_wait_age_bumps_starved_recovered_ticket_ahead_of_fifo`,
`test_g3_fresh_release_keeps_fifo_order`,
`test_g3_claim_release_timestamp_recorded_by_prune`,
`test_g3_wait_age_bump_orders_multiple_starved_oldest_first`,
`test_g3_worker_exit_clears_claim_released_at`

## Beginner view

### What you'd see on the board

CMR-008 spent 45 minutes blocked on a conflict. The conflict cleared.
But every tick now, fresh tickets like CMR-130, CMR-131, CMR-132 keep
dispatching first. CMR-008 hangs around in Todo forever even though
it has been waiting longer than any of them.

### What's happening underneath

The dispatch sort was pure *registration order*: the trailing number on
the ticket id (e.g. `008 < 130`). That makes ordering predictable but
ignores how long each ticket has actually been waiting. A recovered
ticket re-entering the candidate set always re-joins at its
registration position, even if it spent 45 minutes blocked.

### The fix in one paragraph

When the G1 prune block releases an id from `_claimed`, we now also
record *when* it released in `_claim_released_at`. The dispatch sort
partitions candidates into "bumped" (released longer than 10 minutes
ago) and "normal" (everything else). Bumped candidates come first,
ordered by oldest release. Normal candidates keep FIFO. So a ticket
that's been waiting 45 minutes leapfrogs newer numbered tickets, while
freshly-registered work isn't disrupted.

### How to recognize it's working

There's no dedicated log event yet — but the *symptom* disappears.
Compare dispatch order before and after a known starvation event: a
ticket that was previously hanging forever now dispatches on its next
candidate-list appearance after the threshold.

## Expert view

### Code path

- `src/symphony/orchestrator/constants.py`
  ```python
  WAIT_AGE_BUMP_MIN = 10.0   # minutes
  ```

- `src/symphony/orchestrator/core.py::Orchestrator.__init__`
  ```python
  self._claim_released_at: dict[str, datetime] = {}
  ```

- `Orchestrator._on_tick` — inside the existing G1 prune block:
  ```python
  if stale_claimed:
      log.info("stale_claimed_pruned", ids=sorted(stale_claimed))
      self._claimed -= stale_claimed
      now_release = datetime.now(timezone.utc)
      for stale_id in stale_claimed:
          self._claim_released_at[stale_id] = now_release
  ```

- `Orchestrator._sort_with_wait_age_bump` (new method, replaces the
  direct call to `_sort_for_dispatch_fifo` in `_on_tick`):
  ```python
  bumped, normal = [], []
  for issue in candidates:
      released_at = self._claim_released_at.get(issue.id)
      if released_at and (now - released_at).total_seconds() / 60.0 >= WAIT_AGE_BUMP_MIN:
          bumped.append(issue)
      else:
          normal.append(issue)
  bumped.sort(key=lambda i: self._claim_released_at[i.id])
  return bumped + _sort_for_dispatch_fifo(normal, cfg)
  ```

- `Orchestrator._on_worker_exit` clears the entry once the ticket
  finishes its dispatched cycle, so the bump can't carry over to a
  later, unrelated appearance of the same id.

### Invariant

Inside the dispatch sort:
1. `bumped` comes strictly before `normal`.
2. Within `bumped`, oldest release wins.
3. Within `normal`, registration FIFO holds (the existing rule).

### Why 10 minutes?

Conservative enough to ignore ordinary recovery (a worker exits, the
tick fires within seconds, the candidate reappears under FIFO with no
bump). Aggressive enough to catch the symptom that motivated G3
(45-minute starvation observed live).

### Why oldest-release-first within bumped, not registration order?

Otherwise two starved tickets would compete by their original
registration number, and the *more* starved one (older release) could
still lose to a freshly-recovered higher-numbered one. Live diagnosis
showed multi-ticket starvation, so the within-bumped order matters.

### Why drop the entry on worker exit, not on dispatch?

Dropping at dispatch would invalidate the `_claim_released_at` record
on the same tick we just recorded it (since the bumped ticket
immediately dispatches on the next tick after recording). Dropping on
worker exit lets the bump apply for the dispatch lifetime and reset
when the work completes — clean lifecycle.

### Failure mode it replaces

Registration-order FIFO with no wait-age compensation. Stable, but
starvation-prone whenever a recovered ticket has to compete against
lower-numbered fresh tickets. (Higher-numbered ticket recovered from
a 45-min block: always loses.)

### Risk surface

Priority inversion against newer high-priority tickets that just
appeared. Mitigated by the 10-minute threshold: under normal load,
nothing crosses it. Only true starvation cases trigger the bump.

### Related

- G1 prune block does the recording — without G1, `_claim_released_at`
  is never populated.
- `_sort_for_dispatch_fifo` is still the inner sort for both buckets;
  G3 wraps it, doesn't replace it.
