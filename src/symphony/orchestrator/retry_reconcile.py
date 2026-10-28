"""Worker exit, retry scheduling, running-issue reconciliation, and startup cleanup for ``Orchestrator``.

Extracted from ``core.Orchestrator`` (LR-3 lean-core split). Each method is a
module-level function whose first parameter is ``self: Orchestrator``;
``core.Orchestrator`` binds it as a class attribute, so ``self.<name>(...)``
calls, ``Orchestrator.<name>`` patches, and behaviour are unchanged.
Collaborators that tests monkeypatch on ``symphony.orchestrator.core`` are read
through the late-bound ``_core()`` handle at call time, never bound here.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from ..errors import SymphonyError
from ..issue import Issue, normalize_state
from ..workflow import ServiceConfig, SYMPHONY_BRANCH_PREFIX
from ..workflow.verify import verification_in_progress
from ..utils.auto_merge import AutoMergeResult
from ..workspace import COMMIT_FAILED, CommitOutcome
from .constants import (
    ESCALATION_MAX_ATTEMPTS,
    ESCALATION_RETRY_DELAY_MS,
    PAUSED_RETRY_HOLD_MS,
    RETRY_BASE_MS,
)
from .release_seam import is_release_success_state as _is_release_success_state
from .entries import RetryEntry, RunningEntry, _IssueDebug
from .helpers import _config_for_issue_agent, _is_successful_terminal_state, _requested_agent_kind
from .run_registry import ReleaseGate
from . import worker_exit
from .core_support import (
    _EligibilityDecision,
    _EligibilityDisposition,
    _clean_board_error_message,
    _has_app_release_label,
)
from ..logging import get_logger

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()


def _core():
    """Late-bound handle to ``symphony.orchestrator.core`` (imports this module)."""
    from . import core

    return core


# ------------------------------------------------------------------
# worker exit handling (§16.6)
# ------------------------------------------------------------------

async def _on_worker_exit(
    self: Orchestrator,
    issue_id: str,
    reason: str,
    error: str | None,
    *,
    owning_task: asyncio.Task[None] | None = None,
) -> None:
    # Treat the whole exit handler as in-flight. From the moment a worker
    # leaves `_running` until its terminal-state persist (or retry enqueue)
    # finishes, the ticket must stay ineligible: the `await`s inside the
    # body (auto-commit, the async budget persist) each yield to a poll tick
    # that would otherwise prune the in-tick `_claimed` lock and re-dispatch
    # the still-active ticket. See docs/improvements/
    # dispatch-double-dispatch-race-2026-06-28.md.
    owned_entry = self._running.get(issue_id)
    self._terminal_persist_pending.add(issue_id)
    try:
        await self._on_worker_exit_impl(
            issue_id,
            reason,
            error,
            owning_task=owning_task,
            defer_lease_finish=True,
        )
    finally:
        if (
            owned_entry is not None
            and owned_entry.workspace_cleanup_started
            and not owned_entry.workspace_cleanup_finished.is_set()
        ):
            await owned_entry.workspace_cleanup_finished.wait()
        if (
            owned_entry is not None
            and self._running.get(issue_id) is not owned_entry
        ):
            self._finish_run_lease(issue_id, owned_entry, reason, error)
        self._terminal_persist_pending.discard(issue_id)


async def _on_worker_exit_impl(
    self: Orchestrator,
    issue_id: str,
    reason: str,
    error: str | None,
    *,
    owning_task: asyncio.Task[None] | None = None,
    defer_lease_finish: bool = False,
) -> None:
    return await worker_exit.handle_worker_exit(
        self,
        issue_id,
        reason,
        error,
        owning_task=owning_task,
        defer_lease_finish=defer_lease_finish,
    )


def _force_eject_zombie(
    self: Orchestrator,
    issue_id: str,
    entry: RunningEntry,
    cfg: ServiceConfig,
    *,
    skip_kill: bool = False,
) -> None:
    """Forcibly free a worker slot when cancellation didn't propagate.

        Pops the entry from `_running` / `_claimed` and queues a backoff
        retry. Note this never calls `task.cancel()` on `entry.worker_task`
        — only the bookkeeping is dropped, so the original worker can still
        be running (parked on a non-cancellable await) when the retry
        installs a fresh entry under this same issue id. That is race-safe
        (AF-01) not because the stale task's exit is a no-op — a fresh entry
        may well exist by the time it unblocks — but because the worker's
        own `finally` and `_on_worker_exit_impl` both gate on task identity
        (`entry_foreign_to`) before touching `_running`, so a foreign exit
        skips the live replacement entry instead of ejecting it.

        `skip_kill=True` marks that the caller already performed (and
        logged) the process-group kill for this entry — used by the async
        reconcile path, which runs the kill off the event loop via
        `asyncio.to_thread` (M1) before dropping bookkeeping here.
        """
    removed_entry = self._running.pop(issue_id, None)
    owned_transition = self._app_release_transition_locks.get(issue_id)
    if (
        removed_entry is entry
        and owned_transition is not None
        and owned_transition[0] is entry
    ):
        self._app_release_transition_locks.pop(issue_id, None)
    self._claimed.discard(issue_id)
    try:
        try:
            if not skip_kill:
                cleanup_confirmed = self._confirm_entry_backend_cleanup(
                    issue_id, entry
                )
                if entry.run_id and self._run_registry is not None:
                    entry.backend_cleanup_unconfirmed = not cleanup_confirmed
        finally:
            self._finish_run_lease(issue_id, entry, "force_ejected_zombie")
    finally:
        pause_event = self._pause_events.pop(issue_id, None)
        if pause_event is not None and not pause_event.is_set():
            pause_event.set()
        next_attempt = (entry.retry_attempt or 0) + 1
        cap = cfg.agent.max_retry_backoff_ms
        delay_ms = min(RETRY_BASE_MS * (2 ** (next_attempt - 1)), cap)
        self._schedule_retry(
            issue_id,
            identifier=entry.issue.identifier,
            attempt=next_attempt,
            delay_ms=delay_ms,
            error="force_ejected_zombie",
            touched_files=frozenset(self._touched_files_for(entry.issue)),
        )
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.last_workspace = entry.workspace_path
        debug.last_error = "force_ejected_zombie"


# ------------------------------------------------------------------
# retry handling (§16.6)
# ------------------------------------------------------------------

def _schedule_retry(
    self: Orchestrator,
    issue_id: str,
    *,
    identifier: str,
    attempt: int,
    delay_ms: int,
    error: str | None,
    kind: str | None = None,
    holds_slot: bool = True,
    touched_files: frozenset[str] = frozenset(),
) -> None:
    if self._loop is None:
        return
    retry_kind = kind or ("continuation" if error is None else "retry")
    if self._retry_cap_exceeded(issue_id, identifier, attempt, error, retry_kind):
        return
    self._install_retry(
        issue_id=issue_id,
        identifier=identifier,
        attempt=attempt,
        delay_ms=delay_ms,
        error=error,
        kind=retry_kind,
        holds_slot=holds_slot,
        touched_files=touched_files,
    )


def _retry_cap_exceeded(
    self: Orchestrator,
    issue_id: str,
    identifier: str,
    attempt: int,
    error: str | None,
    retry_kind: str,
) -> bool:
    cfg = self._workflow_state.current()
    max_retries = cfg.agent.max_retries if cfg is not None else 0
    if max_retries > 0 and retry_kind != "continuation" and attempt > max_retries:
        log.error(
            "agent_retry_cap_exhausted",
            issue_id=issue_id,
            identifier=identifier,
            attempt=attempt,
            max_retries=max_retries,
            last_error=error,
        )
        # `_spawn_supervised` binds to the orchestrator's owned loop —
        # `_schedule_retry` is a sync method and may be reached from
        # worker_exit callbacks where the current task is in cleanup,
        # so a bare `asyncio.create_task` could hit "no running event
        # loop" errors.
        self._spawn_supervised(
            self._escalate_max_retries(
                issue_id=issue_id,
                identifier=identifier,
                attempt=attempt,
                error=error,
            ),
            name=f"symphony-escalate-{identifier}",
        )
        self._persisted_retry_attempts.pop(issue_id, None)
        self._clear_issue_flags(issue_id, retry_attempt=True)
        return True
    return False


def _install_retry(
    self: Orchestrator,
    *,
    issue_id: str,
    identifier: str,
    attempt: int,
    delay_ms: int,
    error: str | None,
    kind: str,
    holds_slot: bool,
    touched_files: frozenset[str] = frozenset(),
) -> None:
    assert self._loop is not None
    due = self._loop.time() + delay_ms / 1000.0
    handle = self._loop.call_later(
        delay_ms / 1000.0,
        lambda: self._spawn_supervised(
            self._on_retry_timer(issue_id),
            name=f"symphony-retry-{identifier}",
        ),
    )
    self._dispatch_state.schedule_retry(
        issue_id,
        RetryEntry(
            issue_id=issue_id,
            identifier=identifier,
            attempt=attempt,
            due_at_ms=due * 1000.0,
            timer_handle=handle,
            error=error,
            kind=kind,
            holds_slot=holds_slot,
            touched_files=touched_files,
        ),
    )
    debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
    debug.current_retry_attempt = attempt
    debug.current_attempt_kind = kind
    if kind == "continuation":
        self._persisted_retry_attempts.pop(issue_id, None)
        self._clear_issue_flags(issue_id, retry_attempt=True)
    else:
        self._persisted_retry_attempts[issue_id] = attempt
        self._set_issue_flags(issue_id, retry_attempt=attempt)


def _in_flight_ids(self: Orchestrator) -> set[str]:
    """Issue ids the G1 claim-prune must treat as legitimately claimed."""
    return (
        self._dispatch_state.in_flight_ids()
        | self._terminal_persist_pending
        | set(self._pending_escalations)
    )


async def _escalate_max_retries(
    self: Orchestrator,
    *,
    issue_id: str,
    identifier: str,
    attempt: int,
    error: str | None,
) -> None:
    """Move a ticket whose retry budget is exhausted to a terminal state.

        Surfaces a board-level ``## Escalation`` note and updates the
        tracker state to ``Blocked``, then another block-named terminal,
        then a human-named terminal if no failure lane exists. The ticket no longer cycles
        through ``_schedule_retry``; an operator inspecting the board
        sees both the state change and the explanatory comment.

        R8 — a tracker failure here must not discard the claim: a pruned
        claim re-enters dispatch and restarts the retry storm the cap
        exists to stop. Failures re-attempt on a timer (bounded), with the
        pending set holding the claim through the G1 prune meanwhile.
        """
    if self._stopping:
        return
    cfg = self._workflow_state.current()
    if cfg is None:
        self._claimed.discard(issue_id)
        self._retry.pop(issue_id, None)
        self._pending_escalations.pop(issue_id, None)
        return
    target_state = next(
        (
            state for state in cfg.tracker.terminal_states
            if normalize_state(state) == "blocked"
        ),
        "",
    )
    for keyword in ("block", "human"):
        if target_state:
            break
        target_state = next(
            (
                state for state in cfg.tracker.terminal_states
                if keyword in state.lower()
            ),
            "",
        )
    if not target_state and cfg.tracker.terminal_states:
        target_state = cfg.tracker.terminal_states[0]
    if not target_state:
        target_state = "Blocked"
    synthetic_issue = Issue(
        id=issue_id,
        identifier=identifier,
        title="",
        description=None,
        priority=0,
        state="",
        blocked_by=(),
        created_at=datetime.now(timezone.utc),
    )
    body = (
        f"Symphony stopped scheduling retries for `{identifier}` "
        f"after {attempt - 1} failed attempt(s) "
        f"(cap=`agent.max_retries={cfg.agent.max_retries}`).\n"
        f"Last error: {error or '<none>'}\n"
        "Ticket moved to a terminal state for a human to inspect."
    )
    # The retry entry must not fire while the escalation is pending;
    # the _pending_escalations entry keeps the claim alive through G1.
    self._retry.pop(issue_id, None)
    try:
        await asyncio.to_thread(
            self._tracker_call_append_note,
            cfg,
            synthetic_issue,
            "Escalation",
            body,
        )
        await asyncio.to_thread(
            self._tracker_call_update_state,
            cfg,
            synthetic_issue,
            target_state,
        )
        log.warning(
            "agent_retry_cap_escalated",
            issue_id=issue_id,
            identifier=identifier,
            attempt=attempt,
            target_state=target_state,
        )
        self._clear_tracker_error(issue_id)
    except Exception as exc:
        attempts = self._pending_escalations.get(issue_id, 0) + 1
        self._record_tracker_error(issue_id, exc)
        if attempts >= ESCALATION_MAX_ATTEMPTS:
            log.error(
                "agent_retry_cap_escalation_abandoned",
                issue_id=issue_id,
                identifier=identifier,
                error=str(exc),
                escalation_attempts=attempts,
            )
            self._claimed.discard(issue_id)
            self._pending_escalations.pop(issue_id, None)
            return
        self._pending_escalations[issue_id] = attempts
        log.warning(
            "agent_retry_cap_escalation_failed",
            issue_id=issue_id,
            identifier=identifier,
            error=str(exc),
            escalation_attempt=attempts,
        )
        if self._loop is not None:
            self._loop.call_later(
                ESCALATION_RETRY_DELAY_MS / 1000.0,
                lambda: asyncio.ensure_future(
                    self._escalate_max_retries(
                        issue_id=issue_id,
                        identifier=identifier,
                        attempt=attempt,
                        error=error,
                    )
                ),
            )
        return
    self._claimed.discard(issue_id)
    self._pending_escalations.pop(issue_id, None)


async def _on_retry_timer(self: Orchestrator, issue_id: str) -> None:
    retry = self._retry.pop(issue_id, None)
    if retry is None:
        return
    if issue_id in self._paused_issue_ids:
        self._repark_paused_retry(retry)
        return
    cfg = self._workflow_state.current()
    if cfg is None:
        self._release_retry_ownership(
            retry, clear_pause=True, reason="workflow config unavailable"
        )
        return
    await self._process_retry(retry, cfg)


def _repark_paused_retry(self: Orchestrator, retry: RetryEntry) -> None:
    error = self._pause_reasons.get(retry.issue_id) or retry.error or "paused"
    self._schedule_retry(
        retry.issue_id,
        identifier=retry.identifier,
        attempt=retry.attempt,
        delay_ms=PAUSED_RETRY_HOLD_MS,
        error=error,
        kind=retry.kind,
        holds_slot=True,
        touched_files=retry.touched_files,
    )


async def _process_retry(self: Orchestrator, retry: RetryEntry, cfg: ServiceConfig) -> None:
    try:
        candidates = await self._fetch_candidates(cfg)
    except Exception as exc:
        self._repark_retry(
            retry,
            cfg,
            identifier=retry.identifier,
            reason=f"retry poll failed: {exc}",
            holds_slot=retry.holds_slot,
        )
        return
    match = next(
        (issue for issue in candidates if issue.id == retry.issue_id), None
    )
    if match is None:
        self._release_retry_ownership(
            retry, clear_pause=True, reason="issue left active tracker view"
        )
        return
    decision = self._eligibility_decision(match, cfg, owning_retry=True)
    if self._handle_retry_decision(retry, match, cfg, decision):
        return
    self._dispatch(match, cfg, attempt=retry.attempt, attempt_kind=retry.kind)


def _handle_retry_decision(
    self: Orchestrator,
    retry: RetryEntry,
    issue: Issue,
    cfg: ServiceConfig,
    decision: _EligibilityDecision,
) -> bool:
    if decision.disposition is _EligibilityDisposition.READY:
        return False
    if decision.disposition is _EligibilityDisposition.REJECT:
        self._release_retry_ownership(
            retry, clear_pause=False, reason=decision.reason
        )
        return True
    self._repark_retry(
        retry,
        cfg,
        identifier=issue.identifier,
        reason=decision.reason,
        holds_slot=decision.disposition is _EligibilityDisposition.WAIT_SLOT,
    )
    return True


def _repark_retry(
    self: Orchestrator,
    retry: RetryEntry,
    cfg: ServiceConfig,
    *,
    identifier: str,
    reason: str,
    holds_slot: bool,
) -> None:
    delay_ms = min(
        RETRY_BASE_MS * (2**retry.attempt), cfg.agent.max_retry_backoff_ms
    )
    self._schedule_retry(
        retry.issue_id,
        identifier=identifier,
        attempt=retry.attempt,
        delay_ms=delay_ms,
        error=_clean_board_error_message(reason)[:300],
        kind=retry.kind,
        holds_slot=holds_slot,
        touched_files=retry.touched_files,
    )


def _release_retry_ownership(
    self: Orchestrator, retry: RetryEntry, *, clear_pause: bool, reason: str
) -> None:
    issue_id = retry.issue_id
    active_owner = (
        issue_id in self._running
        or issue_id in self._terminal_persist_pending
        or issue_id in self._pending_escalations
    )
    if not active_owner:
        self._claimed.discard(issue_id)
    self._persisted_retry_attempts.pop(issue_id, None)
    if clear_pause:
        self._paused_issue_ids.discard(issue_id)
        self._pause_reasons.pop(issue_id, None)
    self._clear_issue_flags(issue_id, retry_attempt=True, paused=clear_pause)
    log.info(
        "retry_release",
        issue_id=issue_id,
        identifier=retry.identifier,
        reason=reason,
        active_owner=active_owner,
    )


# ------------------------------------------------------------------
# reconciliation (§16.3)
# ------------------------------------------------------------------

async def _reconcile_stall_state(
    self: Orchestrator,
    issue_id: str,
    entry: RunningEntry,
    cfg: ServiceConfig,
    *,
    now: datetime,
    stall_timeout_ms: int,
) -> None:
    """Apply one issue's cancel/eject/stall state in priority order."""
    if entry.cancelled_at is not None:
        since_cancel = (now - entry.cancelled_at).total_seconds()
        if since_cancel > _core().STALL_FORCE_EJECT_GRACE_S:
            log.error(
                "stalled_worker_force_ejected",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                elapsed_since_cancel_s=round(since_cancel, 1),
            )
            # M1: win32 kill_process_group shells out to `taskkill /T`.
            # This coroutine runs on the tick loop, so the kill must go
            # through a worker thread before the (synchronous) eject
            # bookkeeping drops the entry. A kill failure still lets
            # the eject's finallies release the lease and schedule the
            # retry, then surfaces to the per-issue reconcile guard —
            # exactly the pre-M1 containment (AF-07).
            cleanup_confirmed = await self._confirm_entry_backend_cleanup_async(
                issue_id, entry
            )
            if entry.run_id and self._run_registry is not None:
                entry.backend_cleanup_unconfirmed = not cleanup_confirmed
            self._force_eject_zombie(
                issue_id, entry, cfg, skip_kill=True
            )
        return
    if self.is_paused(issue_id):
        return
    seen = max(
        timestamp
        for timestamp in (
            entry.last_progress_timestamp,
            entry.resumed_at,
            entry.started_at,
        )
        if timestamp is not None
    )
    elapsed_ms = (now - seen).total_seconds() * 1000
    if elapsed_ms <= stall_timeout_ms:
        return
    log.warning(
        "stalled_session",
        issue_id=issue_id,
        identifier=entry.issue.identifier,
        elapsed_ms=int(elapsed_ms),
    )
    if entry.worker_task is not None:
        entry.worker_task.cancel()
    entry.cancelled_at = now


def _stall_timeout_ms_for_entry(
    self: Orchestrator, cfg: ServiceConfig, entry: RunningEntry
) -> int:
    """Stall budget for one running worker.

        F-02: `cfg.backend_timeouts()` keys off the *workflow default*
        backend, so a ticket pinned (or stage-routed) to another backend was
        cancelled on the wrong backend's clock — a claude worker configured
        for 900 s died at codex's 300 s. Resolve per entry, then let
        `agent.stall_timeout_ms_by_state` widen it for heavy lanes.
        """
    entry_cfg = _config_for_issue_agent(cfg, entry.issue)
    kind = entry.agent_kind or entry_cfg.agent.kind
    if kind != entry_cfg.agent.kind:
        entry_cfg = replace(entry_cfg, agent=replace(entry_cfg.agent, kind=kind))
    _, _, stall_timeout_ms = entry_cfg.backend_timeouts()
    return cfg.agent.stall_timeout_ms_for_state(entry.issue.state, stall_timeout_ms)


async def _reconcile_running(self: Orchestrator, cfg: ServiceConfig) -> None:
    # Part A: isolate each heartbeat/stall/eject lifecycle.
    now = datetime.now(timezone.utc)
    for issue_id, entry in list(self._running.items()):
        try:
            self._heartbeat_run_lease(issue_id, entry)
            if verification_in_progress(self, issue_id):
                continue
            stall_timeout_ms = self._stall_timeout_ms_for_entry(cfg, entry)
            if stall_timeout_ms > 0:
                await self._reconcile_stall_state(
                    issue_id,
                    entry,
                    cfg,
                    now=now,
                    stall_timeout_ms=stall_timeout_ms,
                )
        except Exception as exc:
            log.warning(
                "reconcile_issue_failed",
                phase="stall",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                error=str(exc),
            )
            self._record_tracker_error(issue_id, exc)
    # Part B: tracker state refresh.
    running_ids = list(self._running.keys())
    if not running_ids:
        return
    try:
        refreshed = await asyncio.to_thread(
            self._tracker_call_states_by_ids, cfg, running_ids
        )
    except Exception as exc:
        log.warning("reconciliation_state_refresh_failed", error=str(exc))
        return
    refreshed_ids = {issue.id for issue in refreshed}
    for missing_id in set(running_ids) - refreshed_ids:
        entry = self._running.get(missing_id)
        if entry is None:
            continue
        detail = (
            f"tracker state refresh omitted running issue {entry.issue.identifier}"
        )
        log.warning(
            "reconciliation_running_issue_missing",
            issue_id=missing_id,
            identifier=entry.issue.identifier,
        )
        self._record_tracker_error(missing_id, detail)
    terminal = {s.lower() for s in cfg.tracker.terminal_states}
    active = {s.lower() for s in cfg.tracker.active_states}
    # Grace period: a worker that just emitted an event is almost
    # certainly already inside its own natural-exit path (post run_turn).
    # Cancelling it now races the worker's own _refresh_issue_state and
    # tends to: (a) drop the in-flight EVENT_TURN_COMPLETED listener,
    # losing observability; (b) wipe the workspace before after_run can
    # capture artefacts. Reserve cancellation for genuinely-stuck
    # workers — the worker's own loop will exit cleanly within a tick
    # or two when the agent transitions to a terminal state.
    RECONCILE_RECENT_EVENT_GRACE_S = 60.0
    now = datetime.now(timezone.utc)
    for issue in refreshed:
        self._clear_tracker_error(issue.id)
        entry = self._running.get(issue.id)
        if entry is None:
            continue
        # Paused workers must not be cancelled by reconcile — the
        # operator already chose to hold them. Without this guard a
        # remote state-move while paused would tear the worker down,
        # `_on_worker_exit` would clear the wakeup event, and the
        # ticket would auto-unpause through retry-or-release.
        if self.is_paused(issue.id):
            continue
        # R8 — one issue's cleanup failure (workspace op, merge gate)
        # must not abort reconciliation for the rest of the board.
        try:
            await self._reconcile_one(
                issue,
                entry,
                cfg,
                active=active,
                terminal=terminal,
                now=now,
                recent_grace_s=RECONCILE_RECENT_EVENT_GRACE_S,
            )
        except Exception as exc:
            log.warning(
                "reconcile_issue_failed",
                issue_id=issue.id,
                identifier=issue.identifier,
                error=str(exc),
            )
            self._record_tracker_error(issue.id, exc)


async def _reconcile_one(
    self: Orchestrator,
    issue: Issue,
    entry: RunningEntry,
    cfg: ServiceConfig,
    *,
    active: set[str],
    terminal: set[str],
    now: datetime,
    recent_grace_s: float,
) -> None:
    state = normalize_state(issue.state)
    if verification_in_progress(self, issue.id) and (
        state in active or _is_successful_terminal_state(state)
    ):
        return
    if state in terminal:
        prior_state = normalize_state(entry.issue.state)
        refreshed_full = await self._refresh_issue_full(cfg, issue.id)
        if refreshed_full is not None:
            issue = refreshed_full
            state = normalize_state(issue.state)
        if (
            not entry.known_app_release
            and not entry.known_release_cycle_verifier
            and not entry.known_app_release_finalizer
            and _has_app_release_label(issue)
        ):
            entry.known_app_release = True
            try:
                authority = self._prepare_release_dispatch(
                    replace(issue, state=entry.issue.state or "Verify"),
                    cfg,
                )
                if authority.gate is None:
                    raise SymphonyError(
                        "late app-release label did not create host authority"
                    )
                entry.known_release_cycle_verifier = True
                entry.release_gate_finalizer = authority.gate.finalizer_identifier
                entry.release_gate_expected_contract_sha256 = (
                    authority.gate.expected_contract_sha256
                )
                entry.release_gate_cycle_fingerprint = (
                    authority.gate.cycle_fingerprint
                )
                entry.release_gate_generation = authority.gate.generation
                note = (
                    "The app-release label was added after this run acquired "
                    "an ordinary lease. Fresh host-bound verification is required."
                )
            except Exception as exc:
                note = (
                    "The app-release label was added after dispatch and release "
                    f"authority could not be established: {exc}"
                )
            entry.issue = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=entry.issue.state or "Verify",
                note_body=note,
            )
            return
        if entry.known_app_release_finalizer:
            try:
                finalizer_gate = cast(
                    ReleaseGate | None,
                    self._release_registry_call(
                        cfg,
                        "read_finalizer_gate_during_reconcile",
                        lambda registry: registry.get_release_gate(
                            entry.release_gate_finalizer or issue.identifier
                        ),
                    ),
                )
                if finalizer_gate is None:
                    raise SymphonyError(
                        "application release finalizer authority disappeared",
                        finalizer=issue.identifier,
                    )
                issue, completion_token = (
                    self._guard_release_finalizer_with_version(
                        cfg=cfg,
                        issue=issue,
                        gate=finalizer_gate,
                        rewind_state=(
                            entry.release_finalizer_rewind_state
                            or entry.issue.state
                        ),
                        expected_run_id=entry.run_id,
                        require_run_authority=True,
                    )
                )
                if _is_release_success_state(cfg, issue.state):
                    finalizer_gate = self._mark_release_finalizer_completed(
                        cfg=cfg,
                        issue=issue,
                        gate=finalizer_gate,
                        completion_token=completion_token,
                        rewind_state=(
                            entry.release_finalizer_rewind_state
                            or entry.issue.state
                        ),
                    )
            except Exception as exc:
                entry.issue = await self._rewind_app_release_transition(
                    cfg=cfg,
                    issue=issue,
                    producing_state=(
                        entry.release_finalizer_rewind_state or entry.issue.state
                    ),
                    note_body=(
                        "Final delivery was stopped during reconciliation "
                        f"because the release approval is invalid: {exc}"
                    ),
                )
                return
        elif entry.known_app_release and prior_state == "verify":
            issue, release_rewound = await self._enforce_app_release_transition(
                cfg=cfg,
                issue=issue,
                workspace_path=entry.workspace_path,
                producing_state=entry.issue.state or "Verify",
                known_app_release=True,
                running_entry=entry,
            )
            entry.issue = issue
            state = normalize_state(issue.state)
            if release_rewound or state not in terminal:
                return
        if entry.terminal_seen_at is None:
            entry.terminal_seen_at = now
        entry.issue = Issue(
            id=issue.id,
            identifier=issue.identifier or entry.issue.identifier,
            title=issue.title or entry.issue.title,
            description=entry.issue.description,
            priority=entry.issue.priority,
            state=issue.state,
            branch_name=entry.issue.branch_name,
            url=entry.issue.url,
            labels=entry.issue.labels,
            blocked_by=entry.issue.blocked_by,
            created_at=entry.issue.created_at,
            updated_at=entry.issue.updated_at,
        )
        if entry.exit_started_at is not None:
            log.info(
                "reconcile_skip_exiting_worker",
                issue_id=issue.id,
                identifier=issue.identifier,
                state=issue.state,
                exit_started_at=entry.exit_started_at.isoformat(),
            )
            return
        last_seen = entry.last_codex_timestamp
        last_event_age = (now - last_seen).total_seconds() if last_seen else None
        last_progress = entry.last_progress_timestamp
        last_progress_age = (
            (now - last_progress).total_seconds() if last_progress else None
        )
        terminal_age = (now - entry.terminal_seen_at).total_seconds()
        if terminal_age < recent_grace_s or (
            last_progress_age is not None and last_progress_age < recent_grace_s
        ):
            log.info(
                "reconcile_skip_active_worker",
                issue_id=issue.id,
                identifier=issue.identifier,
                state=issue.state,
                last_event_age_s=(
                    round(last_event_age, 1) if last_event_age is not None else None
                ),
                last_progress_age_s=(
                    round(last_progress_age, 1)
                    if last_progress_age is not None
                    else None
                ),
                terminal_age_s=round(terminal_age, 1),
            )
            return
        if not await self._enforce_factory_exit_contract(cfg, entry):
            entry.terminal_seen_at = None
            return
        issue = entry.issue
        state = normalize_state(issue.state)
        log.info(
            "reconcile_terminate_terminal",
            issue_id=issue.id,
            identifier=issue.identifier,
            state=issue.state,
            last_event_age_s=(
                round(last_event_age, 1) if last_event_age is not None else None
            ),
            last_progress_age_s=(
                round(last_progress_age, 1)
                if last_progress_age is not None
                else None
            ),
            terminal_age_s=round(terminal_age, 1),
        )
        if entry.worker_task is not None:
            entry.worker_task.cancel()
        if self._workspace_manager is not None:
            entry.workspace_cleanup_started = True
            try:
                release_evidence_only = (
                    entry.known_app_release
                    or entry.known_release_cycle_verifier
                    or entry.known_app_release_finalizer
                )
                if cfg.agent.auto_commit_on_done:
                    # Snapshot before remove — `git worktree remove
                    # --force` would otherwise discard whatever the
                    # agent left uncommitted in the worktree.
                    outcome = await _core().commit_workspace_on_done(
                        entry.workspace_path,
                        identifier=entry.issue.identifier,
                        title=entry.issue.title,
                        exit_reason="reconcile_terminate_terminal",
                        state=issue.state,
                        extra_excludes=self._artifact_commit_excludes(cfg),
                    )
                    if not outcome.durable:
                        entry.issue = await self._preserve_history_failure(
                            cfg, entry.issue, entry.workspace_path, outcome
                        )
                        return
                if release_evidence_only:
                    if entry.known_app_release_finalizer:
                        try:
                            finalizer_gate = cast(
                                ReleaseGate | None,
                                self._release_registry_call(
                                    cfg,
                                    "reconcile_finalizer_pre_cleanup_gate",
                                    lambda registry: registry.get_release_gate(
                                        entry.release_gate_finalizer
                                        or issue.identifier
                                    ),
                                ),
                            )
                            if finalizer_gate is None:
                                raise SymphonyError(
                                    "application release finalizer authority disappeared",
                                    finalizer=issue.identifier,
                                )
                            entry.issue = self._guard_release_finalizer(
                                cfg=cfg,
                                issue=issue,
                                gate=finalizer_gate,
                                rewind_state=(
                                    entry.release_finalizer_rewind_state
                                    or entry.issue.state
                                ),
                                expected_run_id=entry.run_id,
                                require_run_authority=True,
                            )
                        except Exception as exc:
                            entry.issue = await self._rewind_app_release_transition(
                                cfg=cfg,
                                issue=issue,
                                producing_state=(
                                    entry.release_finalizer_rewind_state
                                    or entry.issue.state
                                ),
                                note_body=(
                                    "Final delivery was stopped immediately before "
                                    "reconciliation cleanup because the approval "
                                    f"is invalid: {exc}"
                                ),
                            )
                            return
                    await self._workspace_manager.remove(entry.workspace_path)
                elif (issue.state or "").strip().lower() == "done":
                    merge_ok = await self._auto_merge_done_gate_or_block(
                        cfg,
                        issue,
                        entry.workspace_path,
                        debug_target=self._issue_debug.get(issue.id),
                    )
                    if merge_ok:
                        await self._after_done_then_remove_per_policy(
                            cfg,
                            entry.workspace_path,
                            identifier=entry.issue.identifier,
                            title=entry.issue.title,
                            debug_target=self._issue_debug.get(issue.id),
                        )
                        # C5 — see _on_worker_exit for the rationale.
                        await self._maybe_run_wiki_sweep(
                            cfg, identifier=entry.issue.identifier
                        )
                else:
                    # Non-Done terminal state (e.g. Cancelled, Blocked):
                    # no after_done hook, just reap the workspace.
                    await self._workspace_manager.remove(entry.workspace_path)
            finally:
                entry.workspace_cleanup_finished.set()
    elif state in active:
        entry.terminal_seen_at = None
        # Update in-memory issue snapshot.
        entry.issue = Issue(
            id=issue.id,
            identifier=issue.identifier or entry.issue.identifier,
            title=issue.title or entry.issue.title,
            description=entry.issue.description,
            priority=entry.issue.priority,
            state=issue.state,
            branch_name=entry.issue.branch_name,
            url=entry.issue.url,
            labels=entry.issue.labels,
            blocked_by=entry.issue.blocked_by,
            created_at=entry.issue.created_at,
            updated_at=entry.issue.updated_at,
        )
    else:
        entry.terminal_seen_at = None
        entry.issue = Issue(
            id=issue.id,
            identifier=issue.identifier or entry.issue.identifier,
            title=issue.title or entry.issue.title,
            description=entry.issue.description,
            priority=entry.issue.priority,
            state=issue.state,
            branch_name=entry.issue.branch_name,
            url=entry.issue.url,
            labels=entry.issue.labels,
            blocked_by=entry.issue.blocked_by,
            created_at=entry.issue.created_at,
            updated_at=entry.issue.updated_at,
        )
        # R8 — a state outside both active and terminal sets is
        # out-of-workflow drift (column deleted or renamed remotely).
        # Reap the workspace like the terminal path; leaking the
        # worktree here was the old behavior's slot-adjacent leak.
        log.info(
            "reconcile_terminate_inactive",
            issue_id=issue.id,
            identifier=issue.identifier,
            state=issue.state,
        )
        if entry.worker_task is not None:
            entry.worker_task.cancel()
        if self._workspace_manager is not None:
            entry.workspace_cleanup_started = True
            try:
                if cfg.agent.auto_commit_on_done:
                    outcome = await _core().commit_workspace_on_done(
                        entry.workspace_path,
                        identifier=entry.issue.identifier,
                        title=entry.issue.title,
                        exit_reason="reconcile_terminate_inactive",
                        state=issue.state,
                        extra_excludes=self._artifact_commit_excludes(cfg),
                    )
                    if not outcome.durable:
                        entry.issue = await self._preserve_history_failure(
                            cfg, entry.issue, entry.workspace_path, outcome
                        )
                        return
                await self._workspace_manager.remove(entry.workspace_path)
            finally:
                entry.workspace_cleanup_finished.set()


async def _startup_terminal_cleanup(self: Orchestrator, cfg: ServiceConfig) -> None:
    try:
        terminals = await asyncio.to_thread(self._tracker_call_terminal_issues, cfg)
    except Exception as exc:
        log.warning("startup_terminal_fetch_failed", error=str(exc))
        return
    if self._workspace_manager is None:
        return
    for issue in terminals:
        if issue.id in self._paused_issue_ids:
            continue
        (
            issue,
            release_evidence_only,
            release_stopped,
        ) = await self._startup_release_terminal_guard(cfg, issue)
        if release_stopped:
            continue
        path = self._workspace_manager.path_for(issue.identifier)
        if path.exists():
            if release_evidence_only:
                cleanup_acquisition = self._try_acquire_run_lease(
                    cfg=cfg,
                    issue=issue,
                    workspace_path=path,
                    attempt=None,
                    attempt_kind="startup-release-evidence-cleanup",
                    agent_kind=cfg.agent.kind_for_state(
                        issue.state, _requested_agent_kind(issue)
                    ),
                    release_required=True,
                )
                if cleanup_acquisition is None:
                    log.info(
                        "startup_release_cleanup_claim_refused",
                        identifier=issue.identifier,
                    )
                    continue
                claimed_run_id = cleanup_acquisition.run_id
                try:
                    (
                        issue,
                        _evidence_only,
                        release_stopped,
                    ) = await self._startup_release_terminal_guard(
                        cfg,
                        issue,
                        owned_cleanup_run_id=claimed_run_id,
                    )
                    if release_stopped:
                        continue
                    if cfg.agent.auto_commit_on_done:
                        outcome = await _core().commit_workspace_on_done(
                            path,
                            identifier=issue.identifier,
                            title=issue.title,
                            exit_reason="startup_release_evidence_cleanup",
                            state=issue.state,
                        )
                        if not outcome.durable:
                            await self._preserve_history_failure(cfg, issue, path, outcome)
                            continue
                    await self._workspace_manager.remove(path)
                finally:
                    try:
                        self._release_registry_call(
                            cfg,
                            "finish_startup_release_cleanup",
                            lambda registry: registry.complete_run(
                                issue_id=issue.id,
                                run_id=claimed_run_id,
                                status="startup_release_cleanup",
                            ),
                        )
                    except SymphonyError as exc:
                        log.error(
                            "startup_release_cleanup_finish_failed",
                            identifier=issue.identifier,
                            error=str(exc),
                        )
                continue
            state = (issue.state or "").strip().lower()
            if state in {"blocked", "human review"}:
                log.warning(
                    f"startup_terminal_cleanup_preserved_{state.replace(' ', '_')}_workspace",
                    identifier=issue.identifier,
                    path=str(path),
                )
                continue
            if state == "done":
                branch = f"{SYMPHONY_BRANCH_PREFIX}{issue.identifier}"
                already_merged = False
                if cfg.agent.auto_merge_on_done:
                    already_merged = await _core()._branch_already_merged_into_target(
                        cfg.workflow_path.parent,
                        branch=branch,
                        target_branch=cfg.agent.auto_merge_target_branch,
                    )
                if already_merged:
                    status = await asyncio.to_thread(_core()._worktree_status_snapshot, path)
                    if status is None or status:
                        detail = (
                            "Startup cleanup could not inspect Git worktree status."
                            if status is None
                            else "Startup cleanup found unrecorded work despite merged branch ancestry: "
                            + ", ".join(status)
                        )
                        await self._preserve_history_failure(
                            cfg, issue, path, CommitOutcome(COMMIT_FAILED, detail=detail)
                        )
                        continue
                    log.info(
                        "startup_terminal_cleanup_skipped_already_merged",
                        identifier=issue.identifier,
                        branch=branch,
                        target=cfg.agent.auto_merge_target_branch or "HEAD",
                        path=str(path),
                    )
                    # Ancestry proves local delivery, not an opted-in remote push.
                    if not cfg.agent.auto_merge_push_target:
                        await self._resolve_delivered_factory_blockers(cfg, issue)
                    await self._workspace_manager.remove(path)
                elif cfg.agent.auto_merge_on_done:
                    await self._block_done_ticket_for_merge_gate(
                        cfg,
                        issue,
                        path,
                        result=AutoMergeResult(
                            ok=False,
                            status="startup_unmerged",
                            detail=(
                                f"`{branch}` is not merged into "
                                f"`{cfg.agent.auto_merge_target_branch or '(current branch)'}`"
                            ),
                        ),
                        debug_target=self._issue_debug.get(issue.id),
                    )
                    log.warning(
                        "startup_terminal_cleanup_blocked_unmerged_done",
                        identifier=issue.identifier,
                        branch=branch,
                        path=str(path),
                    )
                else:
                    log.warning(
                        "startup_terminal_cleanup_preserved_done_workspace",
                        identifier=issue.identifier,
                        branch=branch,
                        path=str(path),
                    )
                continue
            if cfg.agent.auto_commit_on_done:
                # Workspaces lingering across orchestrator restarts often
                # hold the last in-progress changes the agent never got
                # to commit. Snapshot before remove so a force-prune
                # doesn't lose them.
                outcome = await _core().commit_workspace_on_done(
                    path,
                    identifier=issue.identifier,
                    title=issue.title,
                    exit_reason="startup_terminal_cleanup",
                    state=issue.state,
                )
                if not outcome.durable:
                    await self._preserve_history_failure(cfg, issue, path, outcome)
                    continue
            await self._workspace_manager.remove(path)
