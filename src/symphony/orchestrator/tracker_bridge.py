"""Tracker-client bridge calls (run in the thread executor) for ``Orchestrator``.

Extracted from ``core.Orchestrator`` (LR-3 lean-core split). Each method is a
module-level function whose first parameter is ``self: Orchestrator``;
``core.Orchestrator`` binds it as a class attribute, so ``self.<name>(...)``
calls, ``Orchestrator.<name>`` patches, and behaviour are unchanged.
Collaborators that tests monkeypatch on ``symphony.orchestrator.core`` are read
through the late-bound ``_core()`` handle at call time, never bound here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, TypeVar

from ..errors import SymphonyError
from ..issue import Issue
from ..trackers import TrackerClient
from ..workflow import ServiceConfig
from .entries import RunningEntry, _IssueDebug
from ..logging import get_logger

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()


def _core():
    """Late-bound handle to ``symphony.orchestrator.core`` (imports this module)."""
    from . import core

    return core


_T = TypeVar("_T")


class _TrackerClientUnavailable(SymphonyError):
    """The shared tracker client is closed or closing (stop() raced a poll).

    Raised by `_checkout_shared_tracker_client` and by bridge invocations
    that observe the pool closing mid-call. Callers already treat tracker
    bridge failures as retryable (next tick rebuilds or skips), so this is
    a clean, loggable dead-end rather than a thread-killing RuntimeError.
    """


# httpx raises a bare RuntimeError with this message from `Client.send`
# once `close()` has run — the exact text is its only stable contract.
_HTTPX_CLIENT_CLOSED_MARKERS = (
    "client has been closed",
    "client has already been closed",
)


def _is_closed_client_runtime_error(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and any(
        marker in str(exc) for marker in _HTTPX_CLIENT_CLOSED_MARKERS
    )


def _tracker_call_update_state(
    self: Orchestrator, cfg: ServiceConfig, issue: Issue, target_state: str
) -> None:
    client = _core().build_tracker_client(cfg)
    try:
        client.update_state(issue, target_state)
    finally:
        client.close()
    self._record_stats_transition(issue.identifier, issue.state, target_state)
    # Notifications fire after the tracker write succeeds. If the write
    # raised, we never reach here — operators see the failure in logs
    # instead of a misleading "moved to X" Slack ping. Lenient by
    # design: dispatch_notification swallows network errors.
    _core()._notify_state_transition(cfg, issue, target_state)


def _tracker_call_append_note(
    cfg: ServiceConfig, issue: Issue, heading: str, body: str
) -> None:
    client = _core().build_tracker_client(cfg)
    try:
        append_note = getattr(client, "append_note", None)
        if append_note is not None:
            append_note(issue, heading, body)
    finally:
        client.close()


def _tracker_call_fetch_issue_full_by_id(
    cfg: ServiceConfig, identifier: str
) -> Issue | None:
    client = _core().build_tracker_client(cfg)
    try:
        return client.fetch_issue_full_by_id(identifier)
    finally:
        client.close()


# ------------------------------------------------------------------
# tracker access
# ------------------------------------------------------------------

async def _fetch_candidates(self: Orchestrator, cfg: ServiceConfig) -> list[Issue]:
    return await asyncio.to_thread(self._tracker_call_candidates, cfg)


def _record_tracker_error(self: Orchestrator, issue_id: str, exc: Exception | str) -> None:
    message = str(exc) or type(exc).__name__
    message = " ".join(message.split())
    if len(message) > 500:
        message = message[-500:]
    self._issue_debug.setdefault(issue_id, _IssueDebug()).tracker_error = message


def _clear_tracker_error(self: Orchestrator, issue_id: str) -> None:
    debug = self._issue_debug.get(issue_id)
    if debug is not None:
        debug.tracker_error = None


def _shared_tracker_client(self: Orchestrator, cfg: ServiceConfig) -> TrackerClient:
    """Lazily built, per-lifetime tracker client for the poll bridges.

        The `_tracker_call_*` helpers below used to open (and close) a
        fresh client per call — for Jira/Linear that meant a new httpx
        connection pool on every poll tick. One client is built on first
        use and reused across calls; `stop()` closes it. It is rebuilt
        when the live config object changes (hot reload swaps the
        `ServiceConfig` instance). Once `stop()` starts closing the pool
        (or has finished closing it) checkouts raise
        `_TrackerClientUnavailable` instead of rebuilding — a rebuild
        would race the close, leak a client past shutdown, or hand the
        caller a pool that is about to be closed under it.
        """
    with self._tracker_client_lock:
        if self._tracker_client_closing or self._tracker_client_closed:
            raise _TrackerClientUnavailable(
                "tracker client is closed for this orchestrator lifetime"
            )
        client = self._tracker_client
        if client is None or cfg is not self._tracker_client_cfg:
            client = _core().build_tracker_client(cfg)
            self._tracker_client = client
            self._tracker_client_cfg = cfg
            self._tracker_client_closed = False
        return client


def _invoke_shared_tracker_client(
    self: Orchestrator, cfg: ServiceConfig, op: Callable[[TrackerClient], _T]
) -> _T:
    """Run one tracker call on the shared client, closing-race safe.

        A `stop()` racing this call on another thread can close the httpx
        pool between checkout and use; httpx then raises a bare
        `RuntimeError`. Convert it (logged once) into the retryable
        `_TrackerClientUnavailable` so the `asyncio.to_thread` bridge
        surfaces a clean failure the next tick can retry instead of an
        opaque RuntimeError from a dying context.
        """
    try:
        client = self._shared_tracker_client(cfg)
    except _TrackerClientUnavailable:
        self._log_tracker_close_race()
        raise
    try:
        return op(client)
    except _TrackerClientUnavailable:
        raise
    except Exception as exc:
        if not _is_closed_client_runtime_error(exc):
            raise
        self._log_tracker_close_race()
        raise _TrackerClientUnavailable(
            "tracker client closed during call"
        ) from exc


def _log_tracker_close_race(self: Orchestrator) -> None:
    if self._tracker_client_close_race_logged:
        return
    self._tracker_client_close_race_logged = True
    log.warning(
        "tracker_client_close_race",
        detail=(
            "shared tracker client closed while a bridge call was in "
            "flight; failing this poll retryably"
        ),
    )


def _tracker_call_candidates(self: Orchestrator, cfg: ServiceConfig) -> list[Issue]:
    return self._invoke_shared_tracker_client(
        cfg, lambda client: client.fetch_candidate_issues()
    )


def _tracker_call_states_by_ids(self: Orchestrator, cfg: ServiceConfig, ids: list[str]) -> list[Issue]:
    return self._invoke_shared_tracker_client(
        cfg, lambda client: client.fetch_issue_states_by_ids(ids)
    )


def _tracker_call_full_by_id(self: Orchestrator, cfg: ServiceConfig, issue_id: str) -> Issue | None:
    """Single-issue fetch with full body — used by contract validation."""
    return self._invoke_shared_tracker_client(
        cfg, lambda client: client.fetch_issue_full_by_id(issue_id)
    )


def _tracker_call_terminal_issues(self: Orchestrator, cfg: ServiceConfig) -> list[Issue]:
    return self._invoke_shared_tracker_client(
        cfg,
        lambda client: client.fetch_issues_by_states(
            cfg.tracker.terminal_states
        ),
    )


def _tracker_call_record_agent_kind(
    self: Orchestrator, cfg: ServiceConfig, identifier: str, agent_kind: str
) -> None:
    """Best-effort: persist the resolved backend onto the ticket.

        Adapters that don't implement ``record_agent_kind`` (e.g. Linear,
        where the field has no remote analogue) are silently skipped, and
        so is a call that races `stop()` closing the shared client.
        """

    def _record(client: TrackerClient) -> None:
        record = getattr(client, "record_agent_kind", None)
        if record is not None:
            record(identifier, agent_kind)

    try:
        self._invoke_shared_tracker_client(cfg, _record)
    except _TrackerClientUnavailable:
        return


def _pin_fallback_agent_kind(
    self: Orchestrator, cfg: ServiceConfig, identifier: str, agent_kind: str
) -> bool:
    """Replace the ticket's backend pin; False when the tracker cannot.

        Only adapters with ``record_agent_kind`` (the file board) can carry
        the pin, so a remote tracker keeps the pause-for-operator path.
        """
    pinned = False

    def _record(client: TrackerClient) -> None:
        nonlocal pinned
        record = getattr(client, "record_agent_kind", None)
        if record is None:
            return
        record(identifier, agent_kind, force=True)
        pinned = True

    try:
        self._invoke_shared_tracker_client(cfg, _record)
    except _TrackerClientUnavailable:
        return False
    except Exception as exc:
        log.warning(
            "backend_fallback_pin_failed",
            identifier=identifier,
            agent_kind=agent_kind,
            error=str(exc),
        )
        return False
    return pinned


def _next_fallback_agent_kind(
    self: Orchestrator, cfg: ServiceConfig, entry: RunningEntry, debug: _IssueDebug
) -> str | None:
    """First `agent.fallback_kinds` entry this ticket has not exhausted."""
    current = self._entry_agent_kind(entry)
    exhausted = set(debug.quota_exhausted_kinds) | {current}
    for kind in cfg.agent.fallback_kinds:
        if kind not in exhausted:
            return kind
    return None


def _tracker_call_record_last_agent_kind(
    self: Orchestrator, cfg: ServiceConfig, identifier: str, agent_kind: str
) -> None:
    """Best-effort: persist the audit-only `last_agent_kind` stamp.

        Used instead of the pin on `stage_kinds`-routed boards, where writing
        the pin would freeze the first lane's backend for the whole ticket.
        Races with `stop()` closing the shared client are silently skipped.
        """

    def _record(client: TrackerClient) -> None:
        record = getattr(client, "record_last_agent_kind", None)
        if record is not None:
            record(identifier, agent_kind)

    try:
        self._invoke_shared_tracker_client(cfg, _record)
    except _TrackerClientUnavailable:
        return
