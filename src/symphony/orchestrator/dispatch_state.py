"""Single owner of the orchestrator's live dispatch/slot state.

Initiative A of docs/improvements/architecture-improvement-plan-2026-07-05.md
(Encapsulate Record + Tell, Don't Ask). The rules that used to be scattered
across dispatch/completion/retry/reconcile call sites are encoded once here:

- **Slot budget counts owned backoff, not contention waits.** A ticket holds
  its slot through ordinary retry/continuation backoff, while dependency and
  capacity waits stay in-flight without blocking work that can make progress.
- **Task identity before eviction.** A worker-done callback may fire after
  the 1s retry timer installed a *fresh* entry under the same key; only the
  entry whose `worker_task` is the finished task may be treated as stale.
- **One pending retry per issue.** Installing a retry cancels the previous
  timer so a ticket can never hold two timers.

The tick loop is the de-facto single writer of this state; keeping every
mutation behind these methods makes that explicit.
"""

from __future__ import annotations

import asyncio

from .entries import RetryEntry, RunningEntry


class DispatchState:
    """Owns live slot state; its methods are the intended mutation surface.

    ``Orchestrator`` still exposes the collections read-only for the many
    legacy read sites (and tests); new code should go through the mutators.
    """

    def __init__(self) -> None:
        self.running: dict[str, RunningEntry] = {}
        self.claimed: set[str] = set()
        self.retry: dict[str, RetryEntry] = {}
        self.persisted_retry_attempts: dict[str, int] = {}
        self.turn_budget_exhausted: set[str] = set()

    # ------------------------------------------------------------------
    # slot budget
    # ------------------------------------------------------------------

    def available_slots(self, max_concurrent_agents: int) -> int:
        """Slots left after running and explicitly slot-holding retries.

        Ordinary backoff holds ownership through the Todo -> Done lifecycle.
        Classified dependency/capacity waits remain in ``retry`` for duplicate
        prevention, but set ``holds_slot=False`` so their blocker can run.
        """
        held_retries = sum(entry.holds_slot for entry in self.retry.values())
        in_flight = len(self.running) + held_retries
        return max(max_concurrent_agents - in_flight, 0)

    def in_flight_ids(self) -> set[str]:
        return set(self.running) | set(self.retry)

    # ------------------------------------------------------------------
    # run lifecycle
    # ------------------------------------------------------------------

    def begin_run(self, issue_id: str, entry: RunningEntry) -> None:
        """Register a dispatched worker and mark the issue claimed."""
        self.running[issue_id] = entry
        self.claimed.add(issue_id)

    def abort_run(self, issue_id: str) -> RunningEntry | None:
        """Roll back `begin_run` when task creation fails."""
        self.claimed.discard(issue_id)
        return self.running.pop(issue_id, None)

    def entry_owned_by(
        self, issue_id: str, task: asyncio.Task[None]
    ) -> RunningEntry | None:
        """Return the running entry only if it belongs to `task`.

        The done-callback identity invariant: `_on_worker_exit` yields once,
        and the continuation retry timer can install a fresh entry under the
        same key inside that yield. A stale callback acting on the fresh
        entry would eject a live worker (slot leak + sibling double-start).
        """
        entry = self.running.get(issue_id)
        if entry is None or entry.worker_task is not task:
            return None
        return entry

    def entry_foreign_to(self, issue_id: str, task: asyncio.Task[None]) -> bool:
        """True when a running entry exists under `issue_id` but isn't `task`'s.

        Distinct from `entry_owned_by`, which also returns "not owned" when
        there is no entry at all — that ambiguity is fine for the
        done-callback (nothing to clean up either way), but the worker exit
        path (AF-01) must tell "no entry" (legitimate no-op) apart from
        "wrong owner" (a stale zombie whose exit must not touch the fresh
        entry that replaced it).

        `entry.worker_task is None` is treated as owned (not foreign): many
        callers — tests and other internal call sites — drive
        `_run_agent_attempt`/`_on_worker_exit_impl` directly against a
        hand-installed entry that never went through `_dispatch`, so its
        `worker_task` is never set. Only a *populated* `worker_task` that
        disagrees with `task` is a genuine identity conflict.
        """
        entry = self.running.get(issue_id)
        return (
            entry is not None
            and entry.worker_task is not None
            and entry.worker_task is not task
        )

    def prune_claims_not_in(self, keep: set[str]) -> set[str]:
        """G1 — drop claims with no in-flight owner; returns the pruned ids.

        A claim without a running worker, pending retry, terminal persist,
        or escalation is a leak from an interrupted dispatch; left alone it
        blocks the ticket from ever dispatching again.
        """
        stale = self.claimed - keep
        self.claimed -= stale
        return stale

    # ------------------------------------------------------------------
    # retry timers
    # ------------------------------------------------------------------

    def cancel_pending_retry(self, issue_id: str) -> RetryEntry | None:
        """Pop the pending retry (if any) and cancel its timer."""
        existing = self.retry.pop(issue_id, None)
        if existing is not None:
            existing.timer_handle.cancel()
        return existing

    def schedule_retry(self, issue_id: str, entry: RetryEntry) -> None:
        """Install `entry` as the one pending retry for `issue_id`.

        Cancels any previously pending timer first so an issue can never
        hold two live retry timers.
        """
        self.cancel_pending_retry(issue_id)
        self.retry[issue_id] = entry
