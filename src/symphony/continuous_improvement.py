"""Continuous-improvement heartbeat: runner seam and durable lease.

This module is the skeleton the orchestrator scheduler delegates to. The
full product-readiness check runner lands in a later task (plan §5); here we
define only the seams the scheduler needs:

* :class:`ImprovementRunResult` — what a run reports back.
* :class:`ImprovementRunner` — the injectable async callable that does the
  work. Tests inject a fake so the scheduler never spawns real subprocesses.
* :class:`Lease` / :class:`FileLease` — a fakeable, cross-process advisory
  lock so two orchestrators sharing one workflow dir never run concurrent
  heartbeats.

Keep this module dependency-light: it must not import the orchestrator.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from symphony.workflow import ServiceConfig

# Lockfile name under `<workflow_dir>/.symphony/`.
LEASE_FILENAME = "continuous_improvement.lock"
# A lease older than this (seconds) is considered abandoned and may be stolen
# — covers an orchestrator that crashed mid-run without releasing.
DEFAULT_LEASE_TTL_SECONDS = 1800.0


@dataclass(frozen=True)
class ImprovementRunResult:
    """Outcome of one heartbeat run, surfaced to the web-API status."""

    tickets_created: int = 0
    verified_branch: str | None = None
    verified_sha: str | None = None


# The scheduler passes the live config, the resolved workflow dir, and a
# `report_phase` callback the runner uses to publish coarse progress
# (e.g. "checking", "verifying") into the status dict. Injectable so tests
# swap in a fake that records the call and returns a canned result.
ImprovementRunner = Callable[
    ["ServiceConfig", Path, Callable[[str], None]], Awaitable[ImprovementRunResult]
]


async def default_improvement_runner(
    cfg: "ServiceConfig",
    workflow_dir: Path,
    report_phase: Callable[[str], None],
) -> ImprovementRunResult:
    """Placeholder runner. Replaced by the real check runner in plan §5.

    Raising keeps the seam honest: if the scheduler ever fires without an
    injected runner, the failure is caught and recorded rather than silently
    doing nothing.
    """
    raise NotImplementedError(
        "continuous-improvement check runner is not implemented yet"
    )


@runtime_checkable
class Lease(Protocol):
    """Cross-process advisory lock. All methods must be non-blocking."""

    def acquire(self) -> bool:
        """Try to take the lease. Return True on success, False if held."""
        ...

    def refresh(self) -> None:
        """Renew the lease timestamp during a long-running hold."""
        ...

    def release(self) -> None:
        """Release the lease. Idempotent; safe to call if never acquired."""
        ...


def lease_path_for(workflow_dir: Path) -> Path:
    return workflow_dir / ".symphony" / LEASE_FILENAME


class FileLease:
    """Lockfile-backed :class:`Lease` under the workflow dir.

    The file holds ``{"pid": ..., "acquired_at": <epoch>}``. Acquisition uses
    an exclusive create so two processes racing the empty state cannot both
    win; a lease older than ``ttl_seconds`` is treated as abandoned and
    stolen. This is advisory (best-effort), which is all the heartbeat needs
    — the durable turn counter and idle-board check are the real guards.
    """

    def __init__(
        self,
        path: Path,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._now = now
        self._held = False

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps({"pid": os.getpid(), "acquired_at": self._now()}),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    def _is_stale(self) -> bool:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            acquired_at = float(data.get("acquired_at", 0.0))
        except (OSError, ValueError, TypeError):
            # Unreadable/corrupt lockfile — treat as abandoned.
            return True
        return (self._now() - acquired_at) >= self._ttl

    def acquire(self) -> bool:
        if self._held:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if not self._is_stale():
                return False
            # Steal the abandoned lease.
            self._write()
            self._held = True
            return True
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "acquired_at": self._now()}, fh)
            self._held = True
            return True

    def refresh(self) -> None:
        if self._held:
            self._write()

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
