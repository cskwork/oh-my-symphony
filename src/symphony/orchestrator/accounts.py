"""Provider-account pooling for a single agent backend kind.

An account is a named env overlay (``AgentAccount.env``) merged into the
per-dispatch environment. Nothing here knows what those variables mean, which
is what keeps the feature backend-neutral.

The bench registry is board-wide and in-memory: when one ticket discovers an
account is quota-exhausted, every other ticket skips it too, instead of each
rediscovering it by burning its own dispatch. A restart re-probes, which costs
at most one dispatch per account.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from symphony.workflow.config import AgentAccount


class AccountBench:
    """Board-wide record of quota-exhausted accounts and when they recover."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._until: dict[tuple[str, str], float] = {}

    def bench(self, kind: str, account_id: str, cooldown_ms: int) -> None:
        self._until[(kind, account_id)] = self._clock() + max(cooldown_ms, 0) / 1000.0

    def expiry(self, kind: str, account_id: str) -> float:
        return self._until.get((kind, account_id), 0.0)

    def is_benched(self, kind: str, account_id: str) -> bool:
        return self.expiry(kind, account_id) > self._clock()


def resolve_account(
    accounts: tuple[AgentAccount, ...],
    kind: str,
    pinned_id: str | None,
    bench: AccountBench,
) -> AgentAccount | None:
    """Which account this dispatch runs as. ``None`` only for an empty pool.

    Resolution and rotation are separate moments: this answers "run as whom",
    while the escalation past the pool belongs to the quota error path. A
    fully benched pool therefore still yields a candidate — the one recovering
    soonest — because dispatching with no overlay would run the backend against
    an undefined profile.
    """
    if not accounts:
        return None
    if pinned_id:
        pinned = next((a for a in accounts if a.id == pinned_id), None)
        if pinned is not None and not bench.is_benched(kind, pinned.id):
            return pinned
    for account in accounts:
        if not bench.is_benched(kind, account.id):
            return account
    return min(accounts, key=lambda a: bench.expiry(kind, a.id))
