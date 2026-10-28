"""Public API / TUI state views and operator actions for ``Orchestrator``.

Extracted from ``core.Orchestrator`` (LR-3 lean-core split). Each method is a
module-level function whose first parameter is ``self: Orchestrator``;
``core.Orchestrator`` binds it as a class attribute, so ``self.<name>(...)``
calls, ``Orchestrator.<name>`` patches, and behaviour are unchanged.
Collaborators that tests monkeypatch on ``symphony.orchestrator.core`` are read
through the late-bound ``_core()`` handle at call time, never bound here.
"""

from __future__ import annotations

import asyncio
import copy
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .. import __version__
from ..errors import SymphonyError
from ..issue import Issue, normalize_state
from ..workflow import ServiceConfig
from ..workspace import HISTORY_PUSH_FAILED
from .constants import TICK_DEGRADED_AFTER_CONSECUTIVE_FAILURES
from .release_seam import is_release_evidence_issue as _is_release_evidence_issue
from .entries import RetryEntry, RunningEntry, _IssueDebug
from .helpers import (
    _is_successful_terminal_state,
    _from_monotonic_to_iso,
    _human_review_target_state,
    _requested_agent_kind,
    _task_debug,
    _to_iso,
    _utc_iso_z,
)
from .core_support import (
    _REOPEN_APPROVED_RE,
    _attention_signal,
    _blocked_rca_already_requested,
    _blocker_dependency_is_resolved,
)
from ..logging import get_logger

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()


def _core():
    """Late-bound handle to ``symphony.orchestrator.core`` (imports this module)."""
    from . import core

    return core


async def confirm_done(self: Orchestrator, identifier: str) -> dict[str, Any]:
    """Complete an operator-reviewed ticket through the host delivery gates."""
    async with self._manual_completion_lock:
        cfg = self._workflow_state.current()
        if cfg is None:
            raise ValueError("workflow is unavailable")
        found = await asyncio.to_thread(
            self._tracker_call_fetch_issue_full_by_id, cfg, identifier
        )
        if found is None:
            raise FileNotFoundError(identifier)
        issue: Issue = found
        if self.find_running_issue_id(identifier) is not None:
            raise ValueError(f"{identifier} has a running worker")
        if normalize_state(issue.state) == "done":
            return {"issue_identifier": identifier, "state": issue.state, "changed": False}
        if normalize_state(issue.state) != "human review":
            raise ValueError(f"{identifier} is not in Human Review")
        done_state = next(
            (s for s in cfg.tracker.terminal_states if normalize_state(s) == "done"),
            None,
        )
        if done_state is None:
            raise ValueError("workflow has no Done terminal state")
        manager = self._workspace_manager
        if manager is None:
            raise ValueError("workspace manager is unavailable")
        path = manager.path_for(identifier)
        if not path.is_dir():
            raise ValueError(f"workspace is missing for {identifier}; restore it before confirming")
        def check_release_identity(current: Issue) -> None:
            # Authority survives label removal and belongs to a verifier/finalizer run.
            if _is_release_evidence_issue(current) or self._release_registry_call(
                cfg, "manual_completion_identity",
                lambda registry: (
                    registry.get_release_gate_for_verifier(identifier) is not None
                    or registry.get_release_gate(identifier) is not None
                    or registry.get_release_evidence_identity(identifier) is not None
                ),
            ):
                raise ValueError("release evidence must finish through its authorized verifier/finalizer run")

        check_release_identity(issue)
        run_id = self._release_registry_call(
            cfg,
            "manual_completion_acquire",
            lambda registry: registry.acquire_run(
                issue, workspace_path=path, attempt=None,
                attempt_kind="manual-confirmation", agent_kind="operator",
            ),
        )
        if not run_id:
            raise ValueError(f"{identifier} has an active run lease")
        heartbeat_error: Exception | None = None
        heartbeat_stop = asyncio.Event()

        def check_ownership() -> None:
            if heartbeat_error is not None:
                raise SymphonyError("manual completion lease renewal failed") from heartbeat_error
            owned = self._release_registry_call(
                cfg, "manual_completion_heartbeat",
                lambda registry: registry.heartbeat(issue_id=issue.id, run_id=run_id),
            )
            if not owned:
                raise SymphonyError("manual completion lost its run lease")

        async def renew_lease() -> None:
            nonlocal heartbeat_error
            while not heartbeat_stop.is_set():
                try:
                    check_ownership()
                except Exception as exc:
                    heartbeat_error = exc
                    return
                try:
                    await asyncio.wait_for(
                        heartbeat_stop.wait(), timeout=min(cfg.poll_interval_ms / 1000, 30)
                    )
                except asyncio.TimeoutError:
                    pass

        heartbeat_task = asyncio.create_task(renew_lease())
        completed = False
        try:
            check_ownership()
            # Another service may have completed or relabeled our pre-lease snapshot.
            current = await asyncio.to_thread(
                self._tracker_call_fetch_issue_full_by_id, cfg, identifier
            )
            if current is None:
                raise FileNotFoundError(identifier)
            if current.id != issue.id:
                raise ValueError(f"{identifier} changed identity during confirmation")
            if normalize_state(current.state) == "done":
                return {"issue_identifier": identifier, "state": current.state, "changed": False}
            if normalize_state(current.state) != "human review":
                raise ValueError(f"{identifier} is no longer in Human Review")
            check_release_identity(current)
            issue = current
            check_ownership()
            debug = self._issue_debug.setdefault(issue.id, _IssueDebug())
            debug.last_workspace = path
            if cfg.agent.auto_commit_on_done:
                history = await _core().finalize_delivery_history(
                    path, identifier=identifier, title=issue.title,
                    state=issue.state, push=cfg.agent.auto_merge_push_target,
                )
                if not history.durable or history.status == HISTORY_PUSH_FAILED:
                    debug.last_error = f"history gate failed ({history.status}): {history.detail}"
                    raise ValueError(debug.last_error)
            check_ownership()
            if not await self._auto_merge_done_gate_or_block(
                cfg, issue, path, debug_target=debug
            ):
                raise ValueError("merge gate failed; workspace preserved; inspect the ticket")
            check_ownership()
            await asyncio.to_thread(self._tracker_call_update_state, cfg, issue, done_state)
            self._dispatch_state.cancel_pending_retry(issue.id)
            self._dispatch_state.claimed.discard(issue.id)
            check_ownership()
            await self._after_done_then_remove_per_policy(
                cfg, path, identifier=identifier, title=issue.title, debug_target=debug
            )
            check_ownership()
            await self._maybe_run_wiki_sweep(cfg, identifier=identifier)
            completed = True
            return {"issue_identifier": identifier, "state": done_state, "changed": True}
        finally:
            heartbeat_stop.set()
            await heartbeat_task
            self._release_registry_call(
                cfg,
                "manual_completion_finish",
                lambda registry: registry.complete_run(
                    issue_id=issue.id, run_id=run_id,
                    status="manual_confirm_done" if completed else "manual_confirm_failed",
                ),
            )
            self.request_refresh()
            await self._notify_observers()


def snapshot(self: Orchestrator) -> dict[str, Any]:
    cfg = self._workflow_state.current()
    running_rows = [
        self._running_row(eid, entry) for eid, entry in self._running.items()
    ]
    retry_rows = [self._retry_row(entry) for entry in self._retry.values()]
    active_seconds = sum(
        (datetime.now(timezone.utc) - entry.started_at).total_seconds()
        for entry in self._running.values()
    )
    return {
        "generated_at": _utc_iso_z(),
        "counts": {"running": len(running_rows), "retrying": len(retry_rows)},
        "running": running_rows,
        "retrying": retry_rows,
        "codex_totals": {
            "input_tokens": self._totals.input_tokens,
            "cache_input_tokens": self._totals.cache_input_tokens,
            "output_tokens": self._totals.output_tokens,
            "total_tokens": self._totals.total_tokens,
            "seconds_running": round(
                self._totals.seconds_running + active_seconds, 1
            ),
        },
        "rate_limits": self._latest_rate_limits,
        "workflow": {
            "default_agent_kind": cfg.agent.kind if cfg is not None else "",
            "branch_policy": self._branch_policy_snapshot(cfg),
        },
        "health": self._health_summary(),
    }


def schedule_snapshot(self: Orchestrator) -> dict[str, Any]:
    """Immutable projection authored by the last completed scheduler pass."""

    return copy.deepcopy(self._schedule_snapshot)


def dependency_state_resolved(self: Orchestrator, state: str | None) -> bool:
    """Use the dispatcher's dependency-success contract for projections."""

    cfg = self._workflow_state.current()
    return _blocker_dependency_is_resolved(state, cfg)


def health(self: Orchestrator) -> dict[str, Any]:
    """A1 — liveness/degradation surface for /api/v1/health.

        Cheap by design: reads counters only, no tracker or registry I/O,
        so the endpoint stays truthful even while the tick loop is wedged.
        """
    now = datetime.now(timezone.utc)
    tick_task = self._tick_task
    tick_started = tick_task is not None
    tick_alive = tick_task is not None and not tick_task.done()
    last = self._last_tick_completed_at
    degraded_reasons: list[str] = []
    if tick_started and not tick_alive and not self._stopping:
        degraded_reasons.append("tick_loop_dead")
    if self._consecutive_tick_failures >= TICK_DEGRADED_AFTER_CONSECUTIVE_FAILURES:
        degraded_reasons.append("tick_failures")
    if (
        self._consecutive_candidate_fetch_failures
        >= TICK_DEGRADED_AFTER_CONSECUTIVE_FAILURES
    ):
        degraded_reasons.append("tracker_fetch_failures")
    if self._last_registry_error is not None:
        degraded_reasons.append("run_registry_error")
    status = (
        "degraded" if degraded_reasons else ("starting" if last is None else "ok")
    )
    return {
        "status": status,
        "degraded_reasons": degraded_reasons,
        "version": __version__,
        "generated_at": _utc_iso_z(),
        "workflow_path": str(self._workflow_state.path),
        "service_instance_id": self._service_instance_id,
        "orchestrator_pid": os.getpid(),
        "tick": {
            "alive": tick_alive,
            "started": tick_started,
            "last_completed_at": last.isoformat() if last is not None else None,
            "seconds_since_last": (
                round((now - last).total_seconds(), 1) if last is not None else None
            ),
            "consecutive_failures": self._consecutive_tick_failures,
            "error_count": self._tick_error_count,
            "loop_restarts": self._tick_loop_restarts,
            "last_error": self._last_tick_error,
        },
        "tracker": {
            "consecutive_fetch_failures": self._consecutive_candidate_fetch_failures,
        },
        "run_registry": {
            "enabled": self._run_registry is not None,
            "error_count": self._registry_error_count,
            "last_error": self._last_registry_error,
        },
        "counts": {"running": len(self._running), "retrying": len(self._retry)},
    }


def _health_summary(self: Orchestrator) -> dict[str, Any]:
    full = self.health()
    return {
        "status": full["status"],
        "degraded_reasons": full["degraded_reasons"],
        "tick_alive": full["tick"]["alive"],
        "last_tick_completed_at": full["tick"]["last_completed_at"],
    }


def _branch_policy_snapshot(self: Orchestrator, cfg: ServiceConfig | None) -> dict[str, Any]:
    if cfg is None:
        return {
            "feature_branch_pattern": "symphony/<ID>",
            "base_branch": "current branch",
            "merge_target_branch": "current branch",
            "merge_timing": "after Document, before Done",
            "auto_merge_enabled": False,
            "merge_delivery": "disabled",
        }
    base = cfg.agent.feature_base_branch or "current branch"
    target = cfg.agent.auto_merge_target_branch or base
    return {
        "feature_branch_pattern": "symphony/<ID>",
        "base_branch": base,
        "merge_target_branch": target,
        "merge_timing": (
            "after Verify"
            if self._is_factory_profile(cfg)
            else "after Document, before Done"
        ),
        "auto_merge_enabled": bool(cfg.agent.auto_merge_on_done),
        "merge_delivery": (
            "upstream-publishing"
            if cfg.agent.auto_merge_push_target
            else "local-only"
        ),
    }


def issue_snapshot(self: Orchestrator, identifier: str) -> dict[str, Any] | None:
    for issue_id, entry in self._running.items():
        if entry.issue.identifier == identifier:
            debug = self._issue_debug.get(issue_id, _IssueDebug())
            return {
                "issue_identifier": entry.issue.identifier,
                "issue_id": issue_id,
                "status": "running",
                "workspace": {"path": str(entry.workspace_path)},
                "attempts": {
                    "restart_count": debug.restart_count,
                    "current_retry_attempt": debug.current_retry_attempt,
                    "current_attempt_kind": debug.current_attempt_kind,
                    "completed_turn_count": debug.completed_turn_count,
                },
                "running": self._running_row(issue_id, entry),
                "retry": None,
                "logs": {"codex_session_logs": []},
                "recent_events": list(debug.recent_events[-20:]),
                "last_error": entry.last_error,
                "tracked": {},
            }
    for issue_id, retry in self._retry.items():
        if retry.identifier == identifier:
            debug = self._issue_debug.get(issue_id, _IssueDebug())
            return {
                "issue_identifier": identifier,
                "issue_id": issue_id,
                "status": "retrying",
                "workspace": {
                    "path": str(debug.last_workspace)
                    if debug.last_workspace
                    else None
                },
                "attempts": {
                    "restart_count": debug.restart_count,
                    "current_retry_attempt": retry.attempt,
                    "current_attempt_kind": retry.kind,
                    "completed_turn_count": debug.completed_turn_count,
                },
                "running": None,
                "retry": self._retry_row(retry),
                "logs": {"codex_session_logs": []},
                "recent_events": list(debug.recent_events[-20:]),
                "last_error": retry.error,
                "tracked": {},
            }
    return None


async def issue_budget_snapshot(self: Orchestrator, issue: Issue) -> dict[str, Any]:
    """Read current guard counters; missing or truncated history is unknown."""
    cfg = self._workflow_state.current()
    if cfg is None:
        return {"available": False, "items": []}
    entry = self._running.get(issue.id)
    debug = self._issue_debug.get(issue.id)
    generated_at = _utc_iso_z()
    current_state = (entry.state_at_turn_start or issue.state) if entry else issue.state
    attempt_turns = entry.turn_count if entry else None
    total_turns = (
        (debug.completed_turn_count if debug else 0) + entry.turn_count
        if entry else debug.completed_turn_count if debug else None
    )
    state_tokens = (
        entry.codex_state_total_tokens
        if entry and entry.last_reported_total_tokens > 0 else None
    )
    rewinds = debug.rewind_count if debug else None
    retry_used = self._persisted_retry_attempts.get(issue.id)
    if entry is not None and entry.attempt_kind == "retry":
        retry_used = entry.retry_attempt
    elif entry is not None and entry.retry_attempt is None:
        retry_used = 0

    def item(name: str, limit: int, used: int | None) -> dict[str, Any]:
        status = "disabled" if limit <= 0 else (
            "unknown" if used is None else "exhausted" if used >= limit else "remaining"
        )
        return {
            "name": name, "limit": limit, "used": used,
            "remaining": max(limit - used, 0) if limit > 0 and used is not None else None,
            "status": status,
        }

    reopen_used = None
    registry = self._run_registry
    if registry is not None:
        records = await asyncio.to_thread(
            self._registry_guard,
            "budget_reopen_history",
            lambda: registry.recent_runs(issue.id, limit=200, status="normal"),
            None,
        )
        # A full page may omit older runs. Never infer an exact count from it.
        if (
            records is not None and len(records) < 200
            and self._running.get(issue.id) is entry
        ):
            prior_done = sum(
                1 for record in records if _is_successful_terminal_state(record.state)
            )
            reopen_used = max(prior_done - 1, 0)
            if entry is not None and prior_done:
                reopen_used += 1
    reopen_limit = cfg.agent.max_reopens
    if reopen_limit > 0:
        reopen_limit += len(_REOPEN_APPROVED_RE.findall(issue.description or ""))
    return {
        "available": True,
        "generated_at": generated_at,
        "state": current_state,
        "items": [
            item("attempt_turns", cfg.agent.max_turns, attempt_turns),
            item("total_turns", cfg.agent.max_total_turns, total_turns),
            item("state_tokens", self._token_budget_for_state(cfg, current_state), state_tokens),
            item("retries", cfg.agent.max_retries, retry_used),
            item("rewinds", cfg.agent.max_attempts, rewinds),
            item("reopens", reopen_limit, reopen_used),
        ],
    }


def issue_attention(self: Orchestrator, issue: Issue) -> dict[str, str | None] | None:
    if self._issue_is_terminal(issue):
        if normalize_state(issue.state) == "blocked":
            if (
                _blocked_rca_already_requested(issue)
                or issue.id in self._blocked_rca_source_ids
            ):
                return _attention_signal(
                    "blocked_recovery_pending",
                    "Fix pending",
                    "Fix ticket already opened; source stays Blocked until the fix resolves",
                    "info",
                )
            return _attention_signal(
                "blocked_recovery_available",
                "Blocked fix",
                "A fix ticket will open automatically before this issue returns to an active lane",
                "warning",
            )
        return None
    entry = self._running.get(issue.id)
    if entry is not None:
        stalled = self._stalled_attention(entry)
        if stalled is not None:
            return stalled
        if entry.lease_lost:
            return _attention_signal(
                "lease_blocked",
                "Lease blocked",
                "run lease was lost to another active holder",
                "error",
            )
    if issue.id in self._lease_blocked:
        return _attention_signal(
            "lease_blocked",
            "Lease blocked",
            self._lease_blocked[issue.id],
            "error",
        )
    if issue.id in self._paused_issue_ids:
        return _attention_signal(
            "paused",
            "Paused",
            self._pause_reasons.get(
                issue.id,
                "paused; resume via resume_worker after inspecting the ticket",
            ),
            "warning",
        )
    if issue.id in self._turn_budget_exhausted:
        debug = self._issue_debug.get(issue.id, _IssueDebug())
        return _attention_signal(
            "budget_exhausted",
            "Budget exhausted",
            debug.last_error or "agent budget exhausted",
            "warning",
        )
    debug = self._issue_debug.get(issue.id, _IssueDebug())
    if debug.tracker_error:
        return _attention_signal(
            "tracker_error",
            "Tracker error",
            debug.tracker_error,
            "warning",
        )
    if issue.blocked_by:
        cfg = self._workflow_state.current()
        for blocker in issue.blocked_by:
            if _blocker_dependency_is_resolved(blocker.state, cfg):
                continue
            identifier = blocker.identifier or blocker.id or "unknown"
            # F-13: a blocker id that is not on the board hydrates to
            # `state=None` and deadlocks the ticket forever. "waiting on
            # unresolved dependency" reads like normal queueing, so say
            # what actually happened.
            if not normalize_state(blocker.state).strip():
                return _attention_signal(
                    "dangling_dependency",
                    "Unknown blocker",
                    f"blocker {identifier} is not on the board — fix the id "
                    f"with `symphony board update {issue.identifier} "
                    "--blocked-by <ID>` or clear it",
                    "error",
                )
            return _attention_signal(
                "blocked_dependency",
                "Blocked dependency",
                f"waiting on unresolved dependency: {identifier}",
                "warning",
            )
    if debug.token_attention:
        return debug.token_attention
    retry = self._retry.get(issue.id)
    if retry is not None:
        return self._retry_attention(retry)
    return None


def _issue_is_terminal(self: Orchestrator, issue: Issue) -> bool:
    state = normalize_state(issue.state)
    cfg = self._workflow_state.current()
    if cfg is not None:
        return state in {normalize_state(s) for s in cfg.tracker.terminal_states}
    return state in {"done", "cancelled", "canceled", "blocked", "archive"}


def _stalled_attention(self: Orchestrator, entry: RunningEntry) -> dict[str, str | None] | None:
    if entry.cancelled_at is None:
        return None
    seconds = int(
        max(0.0, (datetime.now(timezone.utc) - entry.cancelled_at).total_seconds())
    )
    return _attention_signal(
        "stalled",
        "Stalled",
        f"worker cancellation pending for {seconds}s",
        "error",
    )


def _retry_attention(self: Orchestrator, entry: RetryEntry) -> dict[str, str | None]:
    reason = entry.error or f"{entry.kind} attempt {entry.attempt} scheduled"
    return _attention_signal(
        "retry_scheduled",
        "Retry scheduled",
        reason,
        "info",
        due_at=_from_monotonic_to_iso(entry.due_at_ms),
    )


def _running_row(self: Orchestrator, issue_id: str, entry: RunningEntry) -> dict[str, Any]:
    debug = self._issue_debug.get(issue_id, _IssueDebug())
    total_turn_count = debug.completed_turn_count + entry.turn_count
    return {
        "issue_id": issue_id,
        "issue_identifier": entry.issue.identifier,
        "state": entry.issue.state,
        "agent_kind": self._entry_agent_kind(entry),
        "turn_count": total_turn_count,
        "total_turn_count": total_turn_count,
        "attempt_turn_count": entry.turn_count,
        "attempt": entry.retry_attempt,
        "attempt_kind": entry.attempt_kind,
        "last_event": entry.last_codex_event,
        "last_message": entry.last_codex_message,
        "last_error": debug.last_error or entry.last_error,
        "started_at": _to_iso(entry.started_at),
        "last_event_at": _to_iso(entry.last_codex_timestamp),
        "paused": self.is_paused(issue_id),
        "attention": self.issue_attention(entry.issue),
        "tokens": {
            "input_tokens": entry.codex_input_tokens,
            "cache_input_tokens": entry.codex_cache_input_tokens,
            "output_tokens": entry.codex_output_tokens,
            "total_tokens": entry.codex_total_tokens,
            "state_input_tokens": entry.codex_state_input_tokens,
            "state_cache_input_tokens": entry.codex_state_cache_input_tokens,
            "state_output_tokens": entry.codex_state_output_tokens,
            "state_total_tokens": entry.codex_state_total_tokens,
        },
        "worker_task": _task_debug(entry.worker_task),
    }


def _entry_agent_kind(self: Orchestrator, entry: RunningEntry) -> str:
    if entry.agent_kind:
        return entry.agent_kind
    requested = _requested_agent_kind(entry.issue)
    cfg = self._workflow_state.current()
    if cfg is None:
        return requested or ""
    return cfg.agent.kind_for_state(entry.issue.state, requested)


# ------------------------------------------------------------------
# operator-driven pause / resume
# ------------------------------------------------------------------

def is_paused(self: Orchestrator, issue_id: str) -> bool:
    return issue_id in self._paused_issue_ids


def pause_worker(self: Orchestrator, issue_id: str, reason: str | None = None) -> bool:
    """Queue a pause that takes effect at the next turn boundary.

        Returns True if the issue is currently running and a pause was
        registered, False if the id is unknown or already paused. The
        currently-running turn (if any) is allowed to finish — abruptly
        cancelling mid-turn would waste tokens and risk partial artefacts.

        The pause persists across worker exit / retry: the wakeup event is
        per-worker, but `_paused_issue_ids` is per-issue. So a paused
        ticket whose turn ends with `turn_error` (or max_turns, or any
        other natural exit) won't auto-unpause — dispatch + retry both
        consult `is_paused` and refuse to start a fresh worker.
        """
    if issue_id not in self._running:
        return False
    if issue_id in self._paused_issue_ids:
        return False
    pause_reason = reason or "operator pause"
    self._paused_issue_ids.add(issue_id)
    self._pause_reasons[issue_id] = pause_reason
    self._set_issue_flags(
        issue_id,
        paused=True,
        pause_reason=pause_reason,
    )
    event = self._pause_events.get(issue_id)
    if event is None:
        event = asyncio.Event()
        self._pause_events[issue_id] = event
    event.clear()
    log.info(
        "worker_pause_requested",
        issue_id=issue_id,
        identifier=self._running[issue_id].issue.identifier,
    )
    return True


def resume_worker(self: Orchestrator, issue_id: str) -> bool:
    """Lift a pause registered via `pause_worker`.

        Returns True if a paused ticket was resumed, False if the id is
        not paused. Works on any ticket in `_paused_issue_ids`, including
        ones currently sitting in the retry queue (their worker exited
        while paused). On resume, a pending retry timer is fired
        immediately so the operator doesn't wait out the original backoff
        — they already chose to hold the ticket, they shouldn't pay a
        second hold on top.
        """
    if issue_id not in self._paused_issue_ids:
        return False
    self._paused_issue_ids.discard(issue_id)
    self._pause_reasons.pop(issue_id, None)
    self._clear_issue_flags(issue_id, paused=True)
    running = self._running.get(issue_id)
    if running is not None:
        running.resumed_at = datetime.now(timezone.utc)
    event = self._pause_events.get(issue_id)
    if event is not None and not event.is_set():
        event.set()
    identifier = (
        self._running[issue_id].issue.identifier
        if issue_id in self._running
        else self._retry[issue_id].identifier
        if issue_id in self._retry
        else None
    )
    log.info(
        "worker_resume_requested",
        issue_id=issue_id,
        identifier=identifier,
    )
    # Retry held by the pause gate? Fire it now so the resume feels
    # immediate. We cancel the pending timer but leave the entry in
    # `_retry` so `_on_retry_timer` can pop it normally (its `pop`
    # is the single source of truth for "retry consumed").
    retry = self._retry.get(issue_id)
    if retry is not None and self._loop is not None:
        retry.timer_handle.cancel()
        self._spawn_supervised(
            self._on_retry_timer(issue_id),
            name=f"symphony-retry-now-{identifier}",
        )
    return True


def find_running_issue_id(self: Orchestrator, identifier: str) -> str | None:
    """Resolve a human-readable identifier (e.g. `OLV-002`) to issue.id.

        Used by the HTTP API so callers can target tickets without knowing
        the tracker's internal id.
        """
    for issue_id, entry in self._running.items():
        if entry.issue.identifier == identifier:
            return issue_id
    return None


def find_resumable_issue_id(self: Orchestrator, identifier: str) -> str | None:
    """Resolve an identifier for resume across running, retry, or idle pause."""
    issue_id = self.find_running_issue_id(identifier)
    if issue_id is not None:
        return issue_id
    for issue_id, retry in self._retry.items():
        if retry.identifier == identifier:
            return issue_id
    if identifier in self._paused_issue_ids:
        return identifier
    return None


async def skip_document(self: Orchestrator, identifier: str) -> tuple[bool, str]:
    """Move an idle Document ticket to Human Review with an audit note.

        Accepts the legacy `Learn` lane name so pre-rename boards keep the
        skip control without migration.
        """
    cfg = self._workflow_state.current()
    if cfg is None:
        cfg, err = self._workflow_state.reload()
        if cfg is None:
            return False, f"workflow config unavailable: {err}"

    if self.find_running_issue_id(identifier) is not None:
        return False, f"{identifier} has a running worker; wait or pause first"

    issue = await asyncio.to_thread(
        self._tracker_call_fetch_issue_full_by_id, cfg, identifier
    )
    if issue is None:
        return False, f"unknown issue {identifier}"
    if normalize_state(issue.state) not in {"document", "learn"}:
        return False, (
            f"only Document tickets can be skipped (state={issue.state})"
        )
    if self.find_running_issue_id(identifier) is not None:
        return False, f"{identifier} started running; retry after it stops"

    target_state = _human_review_target_state(cfg)
    if not target_state:
        return False, (
            "this board declares no terminal lane to skip into; add a "
            "`Human Review`-style terminal state to tracker.terminal_states"
        )
    await asyncio.to_thread(
        self._tracker_call_append_note,
        cfg,
        issue,
        "Document Skipped",
        f"Operator skipped documentation write-back from the {issue.state} lane.",
    )
    await asyncio.to_thread(
        self._tracker_call_update_state,
        cfg,
        issue,
        target_state,
    )
    self.request_refresh()
    return True, f"moved {identifier} to Human Review"


def _retry_row(self: Orchestrator, entry: RetryEntry) -> dict[str, Any]:
    return {
        "issue_id": entry.issue_id,
        "issue_identifier": entry.identifier,
        "attempt": entry.attempt,
        "kind": entry.kind,
        "due_at": _from_monotonic_to_iso(entry.due_at_ms),
        "error": entry.error,
        "attention": self._retry_attention(entry),
        # Pause now persists across worker exit, so a retry-queued
        # ticket can carry a paused flag the TUI surfaces for resume.
        "paused": self.is_paused(entry.issue_id),
    }
