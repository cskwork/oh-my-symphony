"""SPEC §7, §8, §16 — Orchestrator class (state machine + worker driver).

The orchestrator is the single authority for scheduling state. All worker
outcomes are reported back through asyncio queues and converted into
explicit state transitions (§7.0).

Concurrency model:
- One asyncio event loop owns mutation of `running`, `claimed`, and
  `retry_attempts`. Workers run as tasks; tracker calls run in a thread
  executor; codex events arrive via async callbacks routed through a queue.

Three names — ``build_backend``, ``commit_workspace_on_done``, and
``auto_merge_on_done_best_effort`` — are looked up via the parent
package (``_pkg.<name>``) at call time so tests that
``monkeypatch.setattr("symphony.orchestrator.<name>", stub)`` see the
patch reach this code path. A direct local import would bind the
function at module load and ignore the patch.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine

from .. import __version__
from .._shell import kill_process_group
from ..backends import (
    EVENT_AGENT_RETRY,
    EVENT_APPROVAL_DENIED,
    EVENT_COMPACTION,
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_FAILED,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    AgentBackend,
    BackendInit,
)
from ..utils.archive import select_archivable
from ..backends.codex import linear_graphql_tool
from ..errors import (
    SymphonyError,
    TurnFailed,
    TurnInputRequired,
    TurnTimeout,
    TurnCancelled,
)
from ..issue import Issue, normalize_state
from ..logging import get_logger
from ..prompt import build_continuation_prompt, build_first_turn_prompt
from ..skills import render_skill_block
from ..stats import StatsStore, stats_store_for
from ..trackers import build_tracker_client
from ..utils.wiki_sweep import sweep as _wiki_sweep_run
from ..workflow import (
    DEFAULT_TERMINAL_STATES,
    ServiceConfig,
    SUPPORTED_AGENT_KINDS,
    WorkflowState,
    validate_for_dispatch,
)
from ..utils.auto_merge import AutoMergeResult
from ..workspace import WorkspaceManager
from .constants import (
    ARCHIVE_SWEEP_INTERVAL_SEC,
    AUTO_TRIAGE_NOTE,
    AUTO_TRIAGE_TARGET_STATE,
    CONTINUATION_RETRY_DELAY_MS,
    EMPTY_TURN_LOOP_THRESHOLD,
    ESCALATION_MAX_ATTEMPTS,
    ESCALATION_RETRY_DELAY_MS,
    PAUSED_RETRY_HOLD_MS,
    RETRY_BASE_MS,
    STALL_FORCE_EJECT_GRACE_S,
    STOP_BACKGROUND_TASKS_TIMEOUT_S,
    TICK_DEGRADED_AFTER_CONSECUTIVE_FAILURES,
    TICK_FAILURE_BACKOFF_MAX_S,
    TICK_LOOP_MAX_RESTARTS,
    WAIT_AGE_BUMP_MIN,
    _TOKEN_EMA_ALPHA,
)
from .contracts import evaluate_contract
from .dispatch_state import DispatchState
from .entries import RetryEntry, RunningEntry, _CodexTotals, _IssueDebug
from .helpers import (
    _branch_hook_env,
    _branch_already_merged_into_target,
    _config_for_issue_agent,
    _from_monotonic_to_iso,
    _is_auto_triage_todo_candidate,
    _is_rewind_transition,
    _max_turns_exhausted_target_state,
    _notify_state_transition,
    _requested_agent_kind,
    _sort_for_dispatch_fifo,
    _task_debug,
    _to_iso,
    _utc_iso_z,
)
from .parsing import _parse_findings_rows, _parse_touched_files
from .run_registry import RunRecord, RunRegistry, registry_path_for_workflow


# Parent-package indirection. ``_pkg.build_backend`` (and the two other
# entries below) re-resolve at call time so test monkeypatches on
# ``symphony.orchestrator.<name>`` reach the orchestrator's call sites.
# The package __init__ binds these names before importing this module.
assert __package__ is not None  # always imported as symphony.orchestrator.core
_pkg = sys.modules[__package__]


log = get_logger()

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FAILED_BLOCKER_TERMINAL_STATES = {
    "archive",
    "archived",
    "blocked",
    "cancelled",
    "canceled",
    "duplicate",
}


def _clean_board_error_message(message: str) -> str:
    without_ansi = _ANSI_ESCAPE_RE.sub("", message)
    without_controls = _CONTROL_CHAR_RE.sub("", without_ansi)
    return " ".join(without_controls.split())


def _worker_error_pause_reason(reason: str, error: str | None) -> str:
    detail = f"{reason}: {error}" if error else reason
    clean = _clean_board_error_message(detail)
    return f"worker error: {clean}; paused for operator inspection"


def _update_state_turn_counter(debug: _IssueDebug, state: str) -> int:
    state = normalize_state(state)
    if not debug.state_turn_state:
        debug.state_turn_state = state
        debug.state_turn_count = 1
    elif debug.state_turn_state == state:
        debug.state_turn_count += 1
    else:
        debug.state_turn_state = state
        debug.state_turn_count = 0
    return debug.state_turn_count


def _run_record_payload(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "issue_id": record.issue_id,
        "identifier": record.identifier,
        "attempt": record.attempt,
        "attempt_kind": record.attempt_kind,
        "agent_kind": record.agent_kind,
        "status": record.status,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": (
            record.completed_at.isoformat() if record.completed_at else None
        ),
        "workspace_path": str(record.workspace_path) if record.workspace_path else None,
    }


def _attention_signal(
    kind: str,
    label: str,
    message: str,
    severity: str,
    *,
    due_at: str | None = None,
) -> dict[str, str | None]:
    return {
        "kind": kind,
        "label": label,
        "message": message,
        "severity": severity,
        "due_at": due_at,
    }


def _successful_blocker_terminal_states(cfg: ServiceConfig | None) -> set[str]:
    if cfg is None:
        terminal_states = DEFAULT_TERMINAL_STATES
        archive_state = "Archive"
    else:
        terminal_states = cfg.tracker.terminal_states
        archive_state = cfg.tracker.archive_state
    failed = set(_FAILED_BLOCKER_TERMINAL_STATES)
    failed.add(normalize_state(archive_state).strip())
    return {normalize_state(s).strip() for s in terminal_states} - failed


def _blocker_dependency_is_resolved(
    blocker_state: str | None, cfg: ServiceConfig | None
) -> bool:
    state = normalize_state(blocker_state).strip()
    if not state:
        return False
    return state in _successful_blocker_terminal_states(cfg)


class Orchestrator:
    def __init__(
        self,
        workflow_state: WorkflowState,
    ) -> None:
        self._workflow_state = workflow_state
        self._loop: asyncio.AbstractEventLoop | None = None
        # Single owner of live dispatch/slot state (initiative A). The
        # read-only properties below keep the many legacy read sites (and
        # tests) working; mutations should go through its methods.
        self._dispatch_state = DispatchState()
        # C5 — `Done`-transition counter for the periodic wiki sweep. Lives
        # in-process; restart resets it (acceptable — the sweep is a
        # housekeeping nudge, not a correctness gate). Wraparound at
        # `sys.maxsize` is a non-issue at any realistic ticket throughput.
        self._done_count: int = 0
        # Throttle the per-tick auto-archive sweep to a multi-minute cadence
        # (ARCHIVE_SWEEP_INTERVAL_SEC). Monotonic clock so a wall-clock jump
        # can't wedge it; None = never swept, so the first tick sweeps once.
        self._last_archive_sweep_monotonic: float | None = None
        self._lease_blocked: dict[str, str] = {}
        # Tickets whose worker-exit handler is mid-flight. `_on_worker_exit`
        # adds the id on entry and clears it in a `finally`, so from the moment
        # a worker leaves `_running` until its terminal-state persist (or retry
        # enqueue) finishes the ticket stays ineligible and counts as in-flight
        # for the G1 `_claimed` prune. Without it the `await`s inside the exit
        # body yield to a poll tick that re-dispatches the still-active ticket.
        # See docs/improvements/dispatch-double-dispatch-race-2026-06-28.md.
        self._terminal_persist_pending: set[str] = set()
        # G3 — wait-age dispatch bump. Each id leaves `_claimed` via the G1
        # prune block; record the moment it left so the sort can promote
        # candidates older than `WAIT_AGE_BUMP_MIN` ahead of FIFO. Entries
        # are dropped as soon as the ticket dispatches (so a fresh
        # registration doesn't keep inheriting a stale wait-age bonus).
        self._claim_released_at: dict[str, datetime] = {}
        self._totals = _CodexTotals()
        self._latest_rate_limits: dict[str, Any] | None = None
        self._issue_debug: dict[str, _IssueDebug] = {}
        self._workspace_manager: WorkspaceManager | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._tick_event = asyncio.Event()
        self._stopping = False
        self._refresh_pending = False
        self._observers: list[Callable[[], Awaitable[None]]] = []
        # Operator-driven pause is split into two pieces:
        #   * `_paused_issue_ids` — the authoritative "this ticket is held"
        #     flag. Set on pause_worker, cleared only on resume_worker (or
        #     when the ticket leaves the orchestrator entirely). Survives
        #     worker exits + retries so a paused ticket doesn't auto-unpause
        #     when its turn ends, errors, or hits max_turns.
        #   * `_pause_events` — per-worker wakeup gate. The currently-running
        #     worker awaits this between turns; `pause_worker` clears it,
        #     `resume_worker` (and worker_exit, for cleanup) sets it. Lifetime
        #     is the in-flight worker only; a fresh worker dispatched for a
        #     ticket still in `_paused_issue_ids` is born-paused via a
        #     pre-cleared event in `_dispatch`.
        self._paused_issue_ids: set[str] = set()
        self._pause_reasons: dict[str, str] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        # Rolling EMA of completion `total_tokens` per state. Keys are the
        # lowercased state name (normalize_state). Persisted to
        # `<workflow_dir>/.symphony/token_ema.json` so the soft budget the
        # agent sees survives restarts. Updated on each EVENT_TURN_COMPLETED
        # via `_update_token_ema_for_completed_turn`. C3 (workflow-v0.5.2).
        self._token_ema: dict[str, float] = {}
        self._token_ema_loaded: bool = False
        # Run-stats event store (`.symphony/stats.jsonl`). Bound in start()
        # once the workflow dir is known; every record call is failure-
        # tolerant inside StatsStore, so hooks never guard beyond None.
        self._stats: StatsStore | None = None
        self._run_registry: RunRegistry | None = None
        # R1/A1 — supervision + health counters. One bad tick must degrade
        # the tick, never kill the loop; these counters make the difference
        # between "idle and healthy" and "silently dead" observable.
        self._last_tick_completed_at: datetime | None = None
        self._consecutive_tick_failures: int = 0
        self._tick_error_count: int = 0
        self._tick_loop_restarts: int = 0
        self._last_tick_error: str | None = None
        self._consecutive_candidate_fetch_failures: int = 0
        self._registry_error_count: int = 0
        self._last_registry_error: str | None = None
        # R8 — issue_id -> failed escalation attempts. Keeps a retry-capped
        # ticket out of dispatch while its terminal-state move is retried.
        self._pending_escalations: dict[str, int] = {}
        # Initiative B — strong references for fire-and-forget tasks
        # (worker-exit cleanup, retry firing, escalations). The event loop
        # keeps only weak references to tasks, so an unreferenced task can
        # be garbage-collected mid-flight and its exception vanishes with
        # it. `_spawn_supervised` is the only sanctioned way to fire one.
        self._background_tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # dispatch-state views (initiative A). Read-only aliases so the many
    # legacy read sites (and tests) keep working while DispatchState owns
    # the collections; mutations should go through its methods.
    # ------------------------------------------------------------------

    @property
    def _running(self) -> dict[str, RunningEntry]:
        return self._dispatch_state.running

    @property
    def _claimed(self) -> set[str]:
        return self._dispatch_state.claimed

    @property
    def _retry(self) -> dict[str, RetryEntry]:
        return self._dispatch_state.retry

    @property
    def _completed(self) -> set[str]:
        return self._dispatch_state.completed

    @property
    def _persisted_retry_attempts(self) -> dict[str, int]:
        return self._dispatch_state.persisted_retry_attempts

    @property
    def _turn_budget_exhausted(self) -> set[str]:
        return self._dispatch_state.turn_budget_exhausted

    # ------------------------------------------------------------------
    # supervised background tasks (initiative B)
    # ------------------------------------------------------------------

    def _spawn_supervised(
        self, coro: Coroutine[Any, Any, None], *, name: str
    ) -> asyncio.Task[None]:
        """Fire-and-forget with a strong reference and loud failure.

        The event loop keeps only weak references to tasks; a bare
        `create_task` whose result nobody holds can be garbage-collected
        mid-flight, and any exception it raised vanishes with it. Every
        orchestrator fire-and-forget goes through here so the task is
        pinned until done, failures land in the log, and `stop()` can
        drain the set before closing shared resources.
        """
        loop = self._loop
        task = (
            loop.create_task(coro, name=name)
            if loop is not None
            else asyncio.create_task(coro, name=name)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)
        return task

    def _on_background_task_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error(
                "background_task_failed",
                task_name=task.get_name(),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _drain_background_tasks(self) -> None:
        """Give in-flight cleanup a bounded window, then cancel stragglers."""
        pending = [task for task in self._background_tasks if not task.done()]
        if not pending:
            return
        _done, still_pending = await asyncio.wait(
            pending, timeout=STOP_BACKGROUND_TASKS_TIMEOUT_S
        )
        for task in still_pending:
            log.warning(
                "background_task_cancelled_on_stop",
                task_name=task.get_name(),
            )
            task.cancel()

    # ------------------------------------------------------------------
    # public accessors for API / TUI layers
    # ------------------------------------------------------------------

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    @property
    def stats(self) -> StatsStore | None:
        return self._stats

    def _registry_guard(self, op: str, fn: Callable[[], Any], default: Any) -> Any:
        """Run one registry op; a broken registry degrades, never raises.

        The lease is a secondary guard on top of the in-process `_running`/
        `_claimed` sets, so registry failures fail-open (callers get
        `default`) and surface through health() instead of killing the tick.
        """
        try:
            result = fn()
        except Exception as exc:
            self._registry_error_count += 1
            self._last_registry_error = f"{op}: {exc}"
            log.error("run_registry_error", op=op, error=str(exc))
            return default
        self._last_registry_error = None
        return result

    def _ensure_run_registry(self, cfg: ServiceConfig) -> None:
        path = registry_path_for_workflow(cfg.workflow_path)
        if self._run_registry is not None and self._run_registry.path == path:
            return
        if self._run_registry is not None:
            self._run_registry.close()
        try:
            self._run_registry = RunRegistry(path)
        except Exception as exc:
            self._run_registry = None
            self._registry_error_count += 1
            self._last_registry_error = f"open: {exc}"
            log.error("run_registry_open_failed", path=str(path), error=str(exc))
            return
        registry = self._run_registry
        reclaimed = self._registry_guard(
            "reclaim_dead_owner", registry.reclaim_dead_owner_leases, []
        )
        if reclaimed:
            log.info(
                "run_leases_reclaimed_dead_owner",
                count=len(reclaimed),
                identifiers=[r.identifier for r in reclaimed],
                path=str(path),
            )
        expired = self._registry_guard("expire_stale", registry.expire_stale, 0)
        if expired:
            log.info("run_leases_expired_on_start", count=expired, path=str(path))
        flags = self._registry_guard("list_issue_flags", registry.list_issue_flags, [])
        self._rehydrate_issue_flags(flags)

    def recent_runs(
        self, issue_id: str | None = None, limit: int = 50
    ) -> tuple[list[dict[str, Any]], str | None]:
        registry = self._run_registry
        if registry is None:
            cfg = self._workflow_state.current()
            if cfg is not None:
                self._ensure_run_registry(cfg)
                registry = self._run_registry
        if registry is None:
            return [], "run registry unavailable"

        rows = self._registry_guard(
            "recent_runs",
            lambda: registry.recent_runs(issue_id=issue_id, limit=limit),
            None,
        )
        if rows is None:
            return [], self._last_registry_error
        return [_run_record_payload(row) for row in rows], None

    def _rehydrate_issue_flags(self, flags: list[Any]) -> None:
        self._persisted_retry_attempts.clear()
        for flag in flags:
            issue_id = flag.issue_id
            if flag.budget_exhausted:
                self._turn_budget_exhausted.add(issue_id)
            if flag.paused:
                self._paused_issue_ids.add(issue_id)
                if flag.pause_reason:
                    self._pause_reasons[issue_id] = str(flag.pause_reason)
            if flag.retry_attempt is not None:
                self._persisted_retry_attempts[issue_id] = int(flag.retry_attempt)
                debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
                debug.current_retry_attempt = int(flag.retry_attempt)
                debug.current_attempt_kind = "retry"

    def _set_issue_flags(self, issue_id: str, **flags: Any) -> None:
        registry = self._run_registry
        if registry is None:
            return
        self._registry_guard(
            "set_issue_flags",
            lambda: registry.set_issue_flags(issue_id, **flags),
            None,
        )

    def _clear_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: bool = False,
        budget_exhausted: bool = False,
        paused: bool = False,
    ) -> None:
        registry = self._run_registry
        if registry is None:
            return
        self._registry_guard(
            "clear_issue_flags",
            lambda: registry.clear_issue_flags(
                issue_id,
                retry_attempt=retry_attempt,
                budget_exhausted=budget_exhausted,
                paused=paused,
            ),
            None,
        )

    def _mark_budget_exhausted(self, issue_id: str) -> None:
        self._turn_budget_exhausted.add(issue_id)
        self._persisted_retry_attempts.pop(issue_id, None)
        self._set_issue_flags(issue_id, budget_exhausted=True, retry_attempt=None)

    def _has_active_run_lease(self, issue_id: str) -> bool:
        if self._run_registry is None:
            return False
        registry = self._run_registry
        return bool(
            self._registry_guard(
                "has_active_lease", lambda: registry.has_active_lease(issue_id), False
            )
        )

    def _heartbeat_run_lease(
        self,
        issue_id: str,
        entry: RunningEntry,
        *,
        progress: datetime | None = None,
        backend_agent_pid: int | None = None,
    ) -> bool:
        """Refresh the entry's lease; returns False only on a real conflict.

        A missed heartbeat means the row is no longer active — either the
        lease TTL lapsed (e.g. a blocked tick) or a peer replaced it. A
        healthy worker should not keep running leaseless, so try to take a
        fresh lease; only an actual conflicting holder returns False.
        """
        registry = self._run_registry
        if registry is None or not entry.run_id:
            return True
        ok = self._registry_guard(
            "heartbeat",
            lambda: registry.heartbeat(
                issue_id=issue_id,
                run_id=entry.run_id,
                progress_at=progress,
                backend_agent_pid=backend_agent_pid or entry.codex_app_server_pid,
            ),
            True,
        )
        if ok:
            return True
        if entry.lease_lost:
            return False
        new_run_id = self._registry_guard(
            "reacquire",
            lambda: registry.acquire_run(
                entry.issue,
                workspace_path=entry.workspace_path,
                attempt=entry.retry_attempt,
                attempt_kind="reacquired",
                agent_kind=entry.agent_kind,
            ),
            "",
        )
        if new_run_id == "":
            # Registry error mid-reacquire: keep the worker; health is
            # already flagged degraded by the guard.
            return True
        if new_run_id:
            entry.run_id = new_run_id
            log.warning(
                "run_lease_reacquired",
                issue_id=issue_id,
                issue_identifier=entry.issue.identifier,
                run_id=new_run_id,
            )
            return True
        entry.lease_lost = True
        log.error(
            "run_lease_conflict",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
        )
        return False

    def _heartbeat_running_leases(self) -> None:
        """Per-tick lease refresh; a conflicting holder stops our worker.

        Cancelling stamps `cancelled_at`, so the existing two-stage
        reconcile (cancel -> force-eject after grace) owns the cleanup if
        the worker is stuck on a non-cancellable await.
        """
        for issue_id, entry in list(self._running.items()):
            if self._heartbeat_run_lease(issue_id, entry):
                continue
            task = entry.worker_task
            if task is not None and not task.done() and entry.cancelled_at is None:
                log.error(
                    "worker_cancelled_lease_conflict",
                    issue_id=issue_id,
                    issue_identifier=entry.issue.identifier,
                )
                task.cancel()
                entry.cancelled_at = datetime.now(timezone.utc)

    def _finish_run_lease(
        self, issue_id: str, entry: RunningEntry, status: str
    ) -> None:
        registry = self._run_registry
        if registry is None or not entry.run_id:
            return
        self._registry_guard(
            "complete_run",
            lambda: registry.complete_run(
                issue_id=issue_id,
                run_id=entry.run_id,
                status=status,
            ),
            None,
        )

    def _try_acquire_run_lease(
        self,
        *,
        issue: Issue,
        workspace_path: Path,
        attempt: int | None,
        attempt_kind: str,
        agent_kind: str,
    ) -> str | None:
        registry = self._run_registry
        if registry is None:
            return ""
        run_id = self._registry_guard(
            "acquire_run",
            lambda: registry.acquire_run(
                issue,
                workspace_path=workspace_path,
                attempt=attempt,
                attempt_kind=attempt_kind,
                agent_kind=agent_kind,
            ),
            "",
        )
        if run_id == "":
            # Registry error: dispatch proceeds leaseless (same as
            # registry-disabled) and health reports degraded.
            return ""
        if run_id:
            return run_id
        log.info(
            "dispatch_lease_held",
            issue_id=issue.id,
            issue_identifier=issue.identifier,
        )
        return None

    # ------------------------------------------------------------------
    # public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        cfg = self._workflow_state.current()
        if cfg is None:
            cfg, err = self._workflow_state.reload()
            if err is not None or cfg is None:
                raise err or SymphonyError("workflow not loaded")
        validate_for_dispatch(cfg)
        # Surface the workflow dir to every subprocess spawned afterwards
        # (hooks and agent backends inherit via os.environ). WORKFLOW.md
        # authors can then reference it from `claude.command` etc., e.g.
        # `--add-dir "$SYMPHONY_WORKFLOW_DIR/kanban"` so Claude Code accepts
        # writes through the host-board junction installed by after_create.
        import os as _os
        _os.environ["SYMPHONY_WORKFLOW_DIR"] = str(cfg.workflow_path.parent)
        self._workspace_manager = WorkspaceManager(
            cfg.workspace_root,
            cfg.hooks,
            workflow_dir=cfg.workflow_path.parent,
            board_root=cfg.tracker.board_root,
            reuse_policy=cfg.workspace_reuse_policy,
            hook_env=_branch_hook_env(cfg),
        )
        self._load_token_ema(cfg)
        self._load_done_count(cfg)
        self._stats = stats_store_for(
            cfg.workflow_path.parent / ".symphony" / "stats.jsonl"
        )
        self._ensure_run_registry(cfg)
        await self._startup_terminal_cleanup(cfg)
        self._spawn_tick_loop()

    def _spawn_tick_loop(self) -> None:
        self._tick_task = asyncio.create_task(self._tick_loop(), name="symphony-tick")
        self._tick_task.add_done_callback(self._on_tick_task_done)

    def _on_tick_task_done(self, task: asyncio.Task[None]) -> None:
        """R1 — the tick loop must not die silently.

        The per-tick guard in `_tick_loop` catches `Exception`, so only a
        `BaseException` (or a bug in the loop scaffolding itself) lands
        here. Restart a bounded number of times; past the bound, stay dead
        but visibly so via health().
        """
        if task is not self._tick_task:
            # A stale callback from a superseded loop must not double-restart.
            return
        if self._stopping or task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        self._tick_error_count += 1
        self._last_tick_error = str(exc) or type(exc).__name__
        if self._tick_loop_restarts >= TICK_LOOP_MAX_RESTARTS:
            log.error(
                "tick_loop_dead",
                error=self._last_tick_error,
                error_type=type(exc).__name__,
                restarts=self._tick_loop_restarts,
            )
            return
        self._tick_loop_restarts += 1
        log.error(
            "tick_loop_restarted",
            error=self._last_tick_error,
            error_type=type(exc).__name__,
            restart=self._tick_loop_restarts,
        )
        self._spawn_tick_loop()

    async def stop(self) -> None:
        self._stopping = True
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except (asyncio.CancelledError, Exception):
                pass
        # Set every pause event so any worker blocked on `event.wait()`
        # wakes up and observes the upcoming cancel. Without this, a paused
        # worker would never reach the awaited `CancelledError` and
        # `stop()` would hang on `await worker_task`.
        for event in list(self._pause_events.values()):
            if not event.is_set():
                event.set()
        for entry in list(self._running.values()):
            if entry.worker_task is not None:
                entry.worker_task.cancel()
        for entry in list(self._retry.values()):
            entry.timer_handle.cancel()
        for entry in list(self._running.values()):
            if entry.worker_task is None:
                continue
            try:
                await entry.worker_task
            except (asyncio.CancelledError, Exception):
                pass
        # Worker exits above may have fired supervised cleanup tasks
        # (lease release, registry writes). Let them land before closing
        # the registry; cancel anything still stuck after the bound.
        await self._drain_background_tasks()
        self._running.clear()
        self._retry.clear()
        self._paused_issue_ids.clear()
        self._pause_reasons.clear()
        self._pause_events.clear()
        self._turn_budget_exhausted.clear()
        self._lease_blocked.clear()
        if self._run_registry is not None:
            self._run_registry.close()
            self._run_registry = None

    # ------------------------------------------------------------------
    # observers (§13)
    # ------------------------------------------------------------------

    def add_observer(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._observers.append(callback)

    async def _notify_observers(self) -> None:
        for cb in list(self._observers):
            try:
                await cb()
            except Exception as exc:
                log.warning("observer_failed", error=str(exc))

    # ------------------------------------------------------------------
    # snapshot / API surface (§13.3, §13.7)
    # ------------------------------------------------------------------

    def request_refresh(self) -> bool:
        """§13.7.2 POST /refresh — schedule an immediate tick."""
        if self._refresh_pending:
            return True  # coalesced
        self._refresh_pending = True
        self._tick_event.set()
        return False

    def iter_running_issues(self) -> tuple[Issue, ...]:
        """Return the issues currently owned by running workers."""
        return tuple(entry.issue for entry in self._running.values())

    def snapshot(self) -> dict[str, Any]:
        cfg = self._workflow_state.current()
        running_rows = [self._running_row(eid, entry) for eid, entry in self._running.items()]
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
                "seconds_running": round(self._totals.seconds_running + active_seconds, 1),
            },
            "rate_limits": self._latest_rate_limits,
            "workflow": {
                "default_agent_kind": cfg.agent.kind if cfg is not None else "",
                "branch_policy": self._branch_policy_snapshot(cfg),
            },
            "health": self._health_summary(),
        }

    def health(self) -> dict[str, Any]:
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
        status = "degraded" if degraded_reasons else ("starting" if last is None else "ok")
        return {
            "status": status,
            "degraded_reasons": degraded_reasons,
            "version": __version__,
            "generated_at": _utc_iso_z(),
            "workflow_path": str(self._workflow_state.path),
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

    def _health_summary(self) -> dict[str, Any]:
        full = self.health()
        return {
            "status": full["status"],
            "degraded_reasons": full["degraded_reasons"],
            "tick_alive": full["tick"]["alive"],
            "last_tick_completed_at": full["tick"]["last_completed_at"],
        }

    def _branch_policy_snapshot(self, cfg: ServiceConfig | None) -> dict[str, Any]:
        if cfg is None:
            return {
                "feature_branch_pattern": "symphony/<ID>",
                "base_branch": "current branch",
                "merge_target_branch": "current branch",
                "merge_timing": "after Learn, before Done",
                "auto_merge_enabled": False,
            }
        base = cfg.agent.feature_base_branch or "current branch"
        target = cfg.agent.auto_merge_target_branch or base
        return {
            "feature_branch_pattern": "symphony/<ID>",
            "base_branch": base,
            "merge_target_branch": target,
            "merge_timing": "after Learn, before Done",
            "auto_merge_enabled": bool(cfg.agent.auto_merge_on_done),
        }

    def issue_snapshot(self, identifier: str) -> dict[str, Any] | None:
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
                        "path": str(debug.last_workspace) if debug.last_workspace else None
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

    def issue_attention(self, issue: Issue) -> dict[str, str | None] | None:
        if self._issue_is_terminal(issue):
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

    def _issue_is_terminal(self, issue: Issue) -> bool:
        state = normalize_state(issue.state)
        cfg = self._workflow_state.current()
        if cfg is not None:
            return state in {normalize_state(s) for s in cfg.tracker.terminal_states}
        return state in {"done", "cancelled", "canceled", "blocked", "archive"}

    def _stalled_attention(
        self, entry: RunningEntry
    ) -> dict[str, str | None] | None:
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

    def _retry_attention(self, entry: RetryEntry) -> dict[str, str | None]:
        reason = entry.error or f"{entry.kind} attempt {entry.attempt} scheduled"
        return _attention_signal(
            "retry_scheduled",
            "Retry scheduled",
            reason,
            "info",
            due_at=_from_monotonic_to_iso(entry.due_at_ms),
        )

    def _running_row(self, issue_id: str, entry: RunningEntry) -> dict[str, Any]:
        debug = self._issue_debug.get(issue_id, _IssueDebug())
        total_turn_count = debug.completed_turn_count + entry.turn_count
        return {
            "issue_id": issue_id,
            "issue_identifier": entry.issue.identifier,
            "state": entry.issue.state,
            "agent_kind": self._entry_agent_kind(entry),
            "session_id": entry.session_id,
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

    def _entry_agent_kind(self, entry: RunningEntry) -> str:
        if entry.agent_kind:
            return entry.agent_kind
        requested = _requested_agent_kind(entry.issue)
        if requested is not None:
            return requested
        cfg = self._workflow_state.current()
        return cfg.agent.kind if cfg is not None else ""

    # ------------------------------------------------------------------
    # operator-driven pause / resume
    # ------------------------------------------------------------------

    def is_paused(self, issue_id: str) -> bool:
        return issue_id in self._paused_issue_ids

    def pause_worker(self, issue_id: str, reason: str | None = None) -> bool:
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

    def resume_worker(self, issue_id: str) -> bool:
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

    def find_running_issue_id(self, identifier: str) -> str | None:
        """Resolve a human-readable identifier (e.g. `OLV-002`) to issue.id.

        Used by the HTTP API so callers can target tickets without knowing
        the tracker's internal id.
        """
        for issue_id, entry in self._running.items():
            if entry.issue.identifier == identifier:
                return issue_id
        return None

    def find_resumable_issue_id(self, identifier: str) -> str | None:
        """Resolve an identifier for resume across running and held retries."""
        issue_id = self.find_running_issue_id(identifier)
        if issue_id is not None:
            return issue_id
        for issue_id, retry in self._retry.items():
            if retry.identifier == identifier:
                return issue_id
        return None

    async def skip_learn(self, identifier: str) -> tuple[bool, str]:
        """Move an idle Learn ticket to Human Review with an audit note."""
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
        if normalize_state(issue.state) != "learn":
            return False, f"only Learn tickets can be skipped (state={issue.state})"
        if self.find_running_issue_id(identifier) is not None:
            return False, f"{identifier} started running; retry after it stops"

        await asyncio.to_thread(
            self._tracker_call_append_note,
            cfg,
            issue,
            "Learn Skipped",
            "Operator skipped wiki write-back from the Learn lane.",
        )
        await asyncio.to_thread(
            self._tracker_call_update_state,
            cfg,
            issue,
            "Human Review",
        )
        self.request_refresh()
        return True, f"moved {identifier} to Human Review"

    def _retry_row(self, entry: RetryEntry) -> dict[str, Any]:
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

    def _done_count_path(self, cfg: ServiceConfig) -> Path:
        """On-disk location for the persisted Done counter."""
        return cfg.workflow_path.parent / ".symphony" / "done_count.json"

    def _load_done_count(self, cfg: ServiceConfig) -> None:
        """Restore the Done counter across orchestrator restarts.

        Without persistence, every restart resets `_done_count` to 0 and
        the C5 wiki-sweep cadence skips indefinitely on a frequently
        restarted backend. Malformed payloads degrade to 0 rather than
        crash startup.
        """
        path = self._done_count_path(cfg)
        try:
            if not path.exists():
                return
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("done_count_load_failed", path=str(path), error=str(exc))
            return
        if isinstance(raw, dict):
            value = raw.get("done_count")
            if isinstance(value, int) and value >= 0:
                self._done_count = value

    def _persist_done_count(self, cfg: ServiceConfig) -> None:
        """Best-effort flush; mirrors `_persist_token_ema`."""
        path = self._done_count_path(cfg)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"done_count": self._done_count}, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            log.warning("done_count_persist_failed", path=str(path), error=str(exc))

    def _maybe_run_wiki_sweep(self, cfg: ServiceConfig, *, identifier: str) -> None:
        """C5 — bump the Done counter and run wiki-sweep every Nth time.

        Called from the two Done-transition sites (`_on_worker_exit` and
        the reconcile-driven path). `sweep_every_n: 0` disables the
        auto-sweep entirely. The sweep is intentionally synchronous and
        best-effort: it runs in-process for simplicity (the typical wiki
        is small), failures only log a warning, and never block the
        Done transition. The counter is persisted after every Done so
        sweep cadence survives orchestrator restarts.
        """
        every = cfg.wiki.sweep_every_n
        if every <= 0:
            return
        self._done_count += 1
        self._persist_done_count(cfg)
        if self._done_count % every != 0:
            return
        root = cfg.wiki.root
        if root is None:
            return
        try:
            report = _wiki_sweep_run(root, dry_run=False)
        except Exception as exc:
            log.warning(
                "wiki_sweep_failed",
                identifier=identifier,
                root=str(root),
                error=str(exc),
            )
            return
        log.info(
            "wiki_sweep_run",
            identifier=identifier,
            done_count=self._done_count,
            sweep_every_n=every,
            root=str(report.root) if report.root is not None else "",
            duplicates=len(report.duplicates),
            orphans=len(report.orphans),
            missing_files=len(report.missing_files),
            stale=len(report.stale_entries),
            mutations=len(report.mutations),
            clean=report.is_clean(),
        )

    async def _after_done_then_remove_per_policy(
        self,
        cfg: "ServiceConfig",
        path: Path,
        *,
        identifier: str,
        title: str,
        debug_target: "_IssueDebug | None",
    ) -> None:
        """Fire `after_done` hook and remove the workspace per failure policy.

        Default policy `warn`: hook failure logs and the workspace is
        removed anyway (legacy behaviour — a failed hook can look like a
        clean Done). Policy `block`: hook failure preserves the workspace
        and records `last_error` on the debug entry so the operator can
        investigate before the worktree is reaped. Pair `block` with a
        production-critical `after_done` script (deploy, host-apply).
        """
        if self._workspace_manager is None:
            return
        ok = await self._workspace_manager.after_done_best_effort(
            path, identifier=identifier, title=title
        )
        if not ok and cfg.agent.after_done_failure_policy == "block":
            log.warning(
                "after_done_block_workspace_preserved",
                identifier=identifier,
                path=str(path),
            )
            if debug_target is not None:
                debug_target.last_error = (
                    "after_done failed; workspace preserved (policy=block) "
                    "— operator action required"
                )
            return
        await self._workspace_manager.remove(path)

    async def _block_done_ticket_for_merge_gate(
        self,
        cfg: "ServiceConfig",
        issue: Issue,
        workspace_path: Path,
        *,
        result: AutoMergeResult,
        debug_target: "_IssueDebug | None",
    ) -> None:
        branch = f"symphony/{issue.identifier}"
        target = cfg.agent.auto_merge_target_branch or "(current branch)"
        detail = result.detail.strip()
        note_body = (
            f"Symphony could not merge `{branch}` into `{target}` after this "
            "ticket reached `Done`, so the ticket was moved to `Blocked` to "
            "prevent dependents from running against an incomplete target branch.\n\n"
            f"- status: `{result.status}`\n"
            f"- workspace preserved: `{workspace_path}`"
        )
        if detail:
            note_body = f"{note_body}\n- detail: {detail[:1000]}"
        if debug_target is not None:
            debug_target.last_error = (
                f"auto_merge failed ({result.status}); moved to Blocked; "
                "workspace preserved"
            )
        try:
            await asyncio.to_thread(
                self._tracker_call_update_state,
                cfg,
                issue,
                "Blocked",
            )
            await asyncio.to_thread(
                self._tracker_call_append_note,
                cfg,
                issue,
                "Merge Gate Failed",
                note_body,
            )
            log.warning(
                "auto_merge_gate_blocked_ticket",
                identifier=issue.identifier,
                branch=branch,
                target=target,
                status=result.status,
                path=str(workspace_path),
            )
        except Exception as exc:
            log.warning(
                "auto_merge_gate_block_persist_failed",
                identifier=issue.identifier,
                branch=branch,
                target=target,
                status=result.status,
                error=str(exc),
                path=str(workspace_path),
            )

    async def _auto_merge_done_gate_or_block(
        self,
        cfg: "ServiceConfig",
        issue: Issue,
        workspace_path: Path,
        *,
        debug_target: "_IssueDebug | None",
    ) -> bool:
        if not cfg.agent.auto_merge_on_done:
            return True
        result = await _pkg.auto_merge_on_done_best_effort(
            workflow_dir=cfg.workflow_path.parent,
            branch=f"symphony/{issue.identifier}",
            identifier=issue.identifier,
            title=issue.title,
            target_branch=cfg.agent.auto_merge_target_branch,
            exclude_paths=cfg.agent.auto_merge_exclude_paths,
            capture_untracked=cfg.agent.auto_merge_capture_untracked,
        )
        if result is None or result.ok:
            return True
        await self._block_done_ticket_for_merge_gate(
            cfg,
            issue,
            workspace_path,
            result=result,
            debug_target=debug_target,
        )
        return False

    # ------------------------------------------------------------------
    # tick loop (§16.2)
    # ------------------------------------------------------------------

    async def _tick_loop(self) -> None:
        # Fire an immediate tick.
        while not self._stopping:
            try:
                await self._on_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # R1 — one bad tick degrades the tick, never the loop. The
                # counters feed health(); the bounded pause keeps a hot
                # failure (e.g. a refresh-spammed tick event) from spinning.
                self._tick_error_count += 1
                self._consecutive_tick_failures += 1
                self._last_tick_error = str(exc) or type(exc).__name__
                log.error(
                    "tick_failed",
                    error=self._last_tick_error,
                    error_type=type(exc).__name__,
                    consecutive=self._consecutive_tick_failures,
                )
                backoff_s = min(
                    2.0 ** (self._consecutive_tick_failures - 1),
                    TICK_FAILURE_BACKOFF_MAX_S,
                )
                await asyncio.sleep(backoff_s)
            else:
                self._consecutive_tick_failures = 0
                self._last_tick_completed_at = datetime.now(timezone.utc)
            cfg = self._workflow_state.current()
            poll_ms = cfg.poll_interval_ms if cfg is not None else 30_000
            try:
                await asyncio.wait_for(self._tick_event.wait(), timeout=poll_ms / 1000.0)
            except asyncio.TimeoutError:
                pass
            self._tick_event.clear()
            self._refresh_pending = False

    async def _on_tick(self) -> None:
        cfg, err = self._workflow_state.reload()
        if err is not None and cfg is None:
            cfg = self._workflow_state.current()
            if cfg is None:
                log.error("workflow_unavailable", error=str(err))
                await self._notify_observers()
                return
            log.warning("workflow_reload_failed", error=str(err))
        assert cfg is not None
        # Apply hot-reloadable settings.
        if self._workspace_manager is not None and self._workspace_manager.root != cfg.workspace_root.resolve():
            log.info("workspace_root_changed", new=str(cfg.workspace_root))
            self._workspace_manager = WorkspaceManager(
                cfg.workspace_root,
                cfg.hooks,
                workflow_dir=cfg.workflow_path.parent,
                board_root=cfg.tracker.board_root,
                reuse_policy=cfg.workspace_reuse_policy,
                hook_env=_branch_hook_env(cfg),
            )
        elif self._workspace_manager is not None:
            self._workspace_manager.update_hooks(
                cfg.hooks,
                workflow_dir=cfg.workflow_path.parent,
                board_root=cfg.tracker.board_root,
            )
            self._workspace_manager.update_reuse_policy(cfg.workspace_reuse_policy)
            self._workspace_manager.update_hook_env(_branch_hook_env(cfg))
        self._ensure_run_registry(cfg)
        self._heartbeat_running_leases()
        if self._run_registry is not None:
            registry = self._run_registry
            expired = self._registry_guard("expire_stale", registry.expire_stale, 0)
            if expired:
                log.info("run_leases_expired", count=expired)

        await self._reconcile_running(cfg)
        # G1 — drop sticky locks for tickets no longer in flight. `_claimed`
        # gathers ids on every dispatch path that wants to skip a ticket on
        # the *current* tick (conflict_blocked, hit_max_turns,
        # token/turn-budget exhaustion). Without this prune those locks
        # outlive the situation that set them: a ticket the operator moves
        # back to Todo after fixing the conflict stays invisible to dispatch
        # for the rest of the session. Keeping `_claimed` aligned with
        # `_running ∪ _retry` lets the next tick re-evaluate eligibility
        # against the live tracker state — Blocked tickets stay skipped via
        # `_eligible`'s active-state check; recovered tickets dispatch.
        in_flight_ids = self._in_flight_ids()
        stale_claimed = self._dispatch_state.prune_claims_not_in(in_flight_ids)
        if stale_claimed:
            log.info(
                "stale_claimed_pruned",
                ids=sorted(stale_claimed),
            )
            # G3 — record the moment each id left `_claimed`. The dispatch
            # sort uses this to bump candidates whose wait age crossed
            # `WAIT_AGE_BUMP_MIN` ahead of registration FIFO, so a ticket
            # that spent 45 min in conflict isn't starved behind unrelated
            # numbered tickets that only just appeared.
            now_release = datetime.now(timezone.utc)
            for stale_id in stale_claimed:
                self._claim_released_at[stale_id] = now_release
        try:
            validate_for_dispatch(cfg)
        except SymphonyError as exc:
            log.error("dispatch_validation_failed", error=str(exc))
            await self._notify_observers()
            return

        # Fetch candidates.
        try:
            candidates = await self._fetch_candidates(cfg)
        except Exception as exc:
            self._consecutive_candidate_fetch_failures += 1
            log.warning(
                "candidate_fetch_failed",
                error=str(exc),
                consecutive=self._consecutive_candidate_fetch_failures,
            )
            await self._notify_observers()
            return
        self._consecutive_candidate_fetch_failures = 0

        for issue in self._sort_with_wait_age_bump(candidates, cfg):
            if await self._auto_triage_todo_if_actionable(issue, cfg):
                continue
            if self._available_slots(cfg) <= 0:
                break
            if not self._should_dispatch(issue, cfg):
                continue
            # C1 — system-level pre-check. An overlap with any in-flight
            # ticket's `## Touched Files` would race two workers against
            # the same paths. Move the candidate to Blocked instead of
            # claiming the slot; the agent prompt no longer carries this
            # check itself (workflow-v0.5.2 § C1).
            conflict = self._conflict_blocker(issue)
            if conflict is not None:
                other_identifier, overlap = conflict
                await self._block_ticket_for_conflict(
                    cfg, issue, other_identifier, overlap
                )
                continue
            persisted_attempt = self._persisted_retry_attempts.get(issue.id)
            self._dispatch(
                issue,
                cfg,
                attempt=persisted_attempt,
                attempt_kind="retry" if persisted_attempt is not None else None,
            )

        now_monotonic = time.monotonic()
        if (
            self._last_archive_sweep_monotonic is None
            or now_monotonic - self._last_archive_sweep_monotonic
            >= ARCHIVE_SWEEP_INTERVAL_SEC
        ):
            self._last_archive_sweep_monotonic = now_monotonic
            await self._archive_sweep(cfg)

        await self._notify_observers()

    def _sort_with_wait_age_bump(
        self, candidates: list[Issue], cfg: ServiceConfig
    ) -> list[Issue]:
        """G3 — promote candidates whose recovered wait age crossed the
        threshold ahead of registration-order FIFO. Candidates with no
        `_claim_released_at` entry, or one inside the threshold, keep
        their FIFO order. Among promoted candidates, oldest release
        wins so the most-starved ticket dispatches first.
        """
        if not self._claim_released_at:
            return _sort_for_dispatch_fifo(candidates, cfg)
        now = datetime.now(timezone.utc)
        bumped: list[Issue] = []
        normal: list[Issue] = []
        for issue in candidates:
            released_at = self._claim_released_at.get(issue.id)
            if released_at is None:
                normal.append(issue)
                continue
            wait_minutes = (now - released_at).total_seconds() / 60.0
            if wait_minutes >= WAIT_AGE_BUMP_MIN:
                bumped.append(issue)
            else:
                normal.append(issue)
        bumped.sort(
            key=lambda i: self._claim_released_at.get(i.id) or now
        )
        return bumped + _sort_for_dispatch_fifo(normal, cfg)

    async def _archive_sweep(self, cfg: ServiceConfig) -> None:
        """Auto-archive terminal-state issues older than `archive_after_days`.

        Runs once per tick. Disabled when `archive_after_days <= 0`. Failures
        are logged and swallowed — one stale issue should not break the tick.
        """
        if cfg.tracker.archive_after_days <= 0:
            return
        try:
            terminal_issues = await asyncio.to_thread(
                self._tracker_call_terminal_issues, cfg
            )
        except Exception as exc:
            log.warning("archive_sweep_fetch_failed", error=str(exc))
            return
        stale = select_archivable(
            terminal_issues,
            terminal_states=cfg.tracker.terminal_states,
            archive_state=cfg.tracker.archive_state,
            archive_after_days=cfg.tracker.archive_after_days,
        )
        for issue in stale:
            try:
                await asyncio.to_thread(
                    self._tracker_call_update_state,
                    cfg,
                    issue,
                    cfg.tracker.archive_state,
                )
                log.info(
                    "archive_sweep_moved",
                    identifier=issue.identifier,
                    target=cfg.tracker.archive_state,
                )
            except Exception as exc:
                log.warning(
                    "archive_sweep_update_failed",
                    identifier=issue.identifier,
                    error=str(exc),
                )

    def _tracker_call_update_state(
        self, cfg: ServiceConfig, issue: Issue, target_state: str
    ) -> None:
        client = build_tracker_client(cfg)
        try:
            client.update_state(issue, target_state)
        finally:
            client.close()
        self._record_stats_transition(issue.identifier, issue.state, target_state)
        # Notifications fire after the tracker write succeeds. If the write
        # raised, we never reach here — operators see the failure in logs
        # instead of a misleading "moved to X" Slack ping. Lenient by
        # design: dispatch_notification swallows network errors.
        _notify_state_transition(cfg, issue, target_state)

    @staticmethod
    def _tracker_call_append_note(
        cfg: ServiceConfig, issue: Issue, heading: str, body: str
    ) -> None:
        client = build_tracker_client(cfg)
        try:
            append_note = getattr(client, "append_note", None)
            if append_note is not None:
                append_note(issue, heading, body)
        finally:
            client.close()

    @staticmethod
    def _tracker_call_fetch_issue_full_by_id(
        cfg: ServiceConfig, identifier: str
    ) -> Issue | None:
        client = build_tracker_client(cfg)
        try:
            return client.fetch_issue_full_by_id(identifier)
        finally:
            client.close()

    # ------------------------------------------------------------------
    # candidate selection (§8.2)
    # ------------------------------------------------------------------

    def _should_dispatch(self, issue: Issue, cfg: ServiceConfig) -> bool:
        """§8.2 — eligibility for the poll-tick dispatch path."""
        return self._eligible(issue, cfg, owning_retry=False)

    async def _auto_triage_todo_if_actionable(
        self, issue: Issue, cfg: ServiceConfig
    ) -> bool:
        if not _is_auto_triage_todo_candidate(issue, cfg):
            return False
        try:
            await asyncio.to_thread(
                self._tracker_call_append_note,
                cfg,
                issue,
                "Triage",
                AUTO_TRIAGE_NOTE,
            )
            await asyncio.to_thread(
                self._tracker_call_update_state,
                cfg,
                issue,
                AUTO_TRIAGE_TARGET_STATE,
            )
        except Exception as exc:
            log.warning(
                "auto_triage_todo_failed",
                identifier=issue.identifier,
                error=str(exc),
            )
            self._record_tracker_error(issue.id, exc)
            return False
        self._clear_tracker_error(issue.id)
        log.info(
            "auto_triage_todo",
            identifier=issue.identifier,
            target=AUTO_TRIAGE_TARGET_STATE,
        )
        return True

    def _eligible(
        self, issue: Issue, cfg: ServiceConfig, *, owning_retry: bool
    ) -> bool:
        """Shared eligibility logic.

        `owning_retry=True` is set by the retry handler — it already owns the
        issue's claim (§7.1: `Claimed = Running or RetryQueued`), so the
        `_claimed`/`_running` self-membership checks would otherwise create a
        false-negative loop where the retry timer keeps rescheduling itself.
        """
        if issue.id in self._running:
            return False
        if self._has_active_run_lease(issue.id):
            self._lease_blocked[issue.id] = (
                "another active run lease exists for this issue"
            )
            return False
        self._lease_blocked.pop(issue.id, None)
        if not owning_retry and issue.id in self._claimed:
            return False
        # Paused tickets hold their slot but never start a fresh worker
        # until the operator resumes. Without this, a worker that exits
        # (turn_error, max_turns, reconcile cancel, …) would silently
        # re-dispatch via `_on_retry_timer` and look like an auto-unpause.
        if issue.id in self._paused_issue_ids:
            return False
        if issue.id in self._turn_budget_exhausted:
            return False
        if issue.id in self._terminal_persist_pending:
            return False
        active = {s.lower() for s in cfg.tracker.active_states}
        terminal = {s.lower() for s in cfg.tracker.terminal_states}
        state = normalize_state(issue.state)
        if state in terminal or state not in active:
            return False
        if not (issue.id and issue.identifier and issue.title and issue.state):
            return False
        requested_agent = _requested_agent_kind(issue)
        if requested_agent is not None and requested_agent not in SUPPORTED_AGENT_KINDS:
            log.warning(
                "ticket_agent_kind_unsupported",
                issue_id=issue.id,
                identifier=issue.identifier,
                agent_kind=requested_agent,
                supported=sorted(SUPPORTED_AGENT_KINDS),
            )
            return False
        # Per-state limit (§8.3).
        per_state_cap = cfg.agent.max_concurrent_agents_by_state.get(state)
        if per_state_cap is not None:
            current_in_state = sum(
                1
                for entry in self._running.values()
                if normalize_state(entry.issue.state) == state
            )
            if current_in_state >= per_state_cap:
                return False
        # Blockers apply to every active state; downstream work must wait
        # if an upstream dependency regresses or is unknown.
        if issue.blocked_by:
            for blocker in issue.blocked_by:
                if not _blocker_dependency_is_resolved(blocker.state, cfg):
                    return False
        return True

    def _available_slots(self, cfg: ServiceConfig) -> int:
        # The retry-counts-against-the-budget rule lives on DispatchState
        # (single owner of slot math) — see its docstring for the OLV-005
        # double-start war story.
        return self._dispatch_state.available_slots(cfg.agent.max_concurrent_agents)

    # ------------------------------------------------------------------
    # C1 — system-level conflict pre-check
    # ------------------------------------------------------------------

    def _touched_files_for(self, issue: Issue) -> set[str]:
        """Return the `## Touched Files` paths declared on a ticket body.

        Parses the issue's markdown description (set by every tracker
        adapter on candidate fetch). Returns an empty set when the
        section is missing or contains no bullet rows. Tolerant of
        malformed bullets — anything we can't recognise is skipped.
        """
        return _parse_touched_files(issue.description)

    def _conflict_blocker(
        self, candidate: Issue
    ) -> tuple[str, set[str]] | None:
        """Return `(other_identifier, overlapping_paths)` when claiming
        ``candidate`` would conflict with an in-flight ticket.

        "In-flight" = currently in `_running` OR pending retry. Iterates
        both, intersects each touched-file set against the candidate, and
        returns the first overlap found (stable order: running before
        retry, then insertion order within each).
        """
        candidate_files = self._touched_files_for(candidate)
        if not candidate_files:
            return None
        for other_id, entry in self._running.items():
            if other_id == candidate.id:
                continue
            other_files = self._touched_files_for(entry.issue)
            overlap = candidate_files & other_files
            if overlap:
                return entry.issue.identifier, overlap
        for other_id, retry_entry in self._retry.items():
            if other_id == candidate.id:
                continue
            # Retry entries don't carry the full Issue. Look up the
            # last-known body via running history when present; the
            # common case (retry of an exited ticket) leaves no body to
            # inspect, and the retry path re-evaluates on its own tick.
            running_entry = self._running.get(other_id)
            if running_entry is None:
                continue
            other_files = self._touched_files_for(running_entry.issue)
            overlap = candidate_files & other_files
            if overlap:
                return retry_entry.identifier, overlap
        return None

    async def _block_ticket_for_conflict(
        self,
        cfg: ServiceConfig,
        candidate: Issue,
        other_identifier: str,
        overlap: set[str],
    ) -> None:
        """Move ``candidate`` to ``Blocked`` and append a `## Conflict` note.

        Lenient: tracker failures only log a warning. The in-memory
        `_claimed` set still gets the candidate so the same dispatch loop
        doesn't immediately retry it inside the same tick. The G1 prune at
        `_on_tick` start drops this id on the next tick once the worker
        that triggered the conflict has exited, so the candidate can
        re-enter the dispatch loop the moment the operator (or auto-merge)
        moves it back to an active state.
        """
        sorted_overlap = sorted(overlap)
        note_body = (
            f"Conflicts with `{other_identifier}` on overlapping "
            f"`## Touched Files`:\n"
            + "\n".join(f"- `{p}`" for p in sorted_overlap)
        )
        try:
            await asyncio.to_thread(
                self._tracker_call_append_note,
                cfg,
                candidate,
                "Conflict",
                note_body,
            )
        except Exception as exc:
            log.warning(
                "conflict_note_failed",
                issue_id=candidate.id,
                identifier=candidate.identifier,
                error=str(exc),
            )
        try:
            await asyncio.to_thread(
                self._tracker_call_update_state,
                cfg,
                candidate,
                "Blocked",
            )
        except Exception as exc:
            log.warning(
                "conflict_block_failed",
                issue_id=candidate.id,
                identifier=candidate.identifier,
                error=str(exc),
            )
        # Keep the candidate out of this tick's dispatch loop even if the
        # tracker mutation didn't land — the in-memory claim clears on
        # the next reconcile if Blocked is terminal in the workflow.
        self._claimed.add(candidate.id)
        log.info(
            "conflict_blocked",
            issue_id=candidate.id,
            identifier=candidate.identifier,
            other=other_identifier,
            overlap=sorted_overlap,
        )

    # ------------------------------------------------------------------
    # C3 — adaptive token-budget EMA
    # ------------------------------------------------------------------

    def _token_ema_path(self, cfg: ServiceConfig) -> Path:
        """Return the on-disk location for the persisted EMA snapshot."""
        return cfg.workflow_path.parent / ".symphony" / "token_ema.json"

    def _load_token_ema(self, cfg: ServiceConfig) -> None:
        """Load `_token_ema` from disk on `start()`. Missing file = empty.

        Idempotent: a second `start()` (e.g. reload) overwrites in-memory
        EMA with the latest disk snapshot. Malformed payloads degrade to
        empty rather than crash startup.
        """
        path = self._token_ema_path(cfg)
        try:
            if not path.exists():
                self._token_ema = {}
                self._token_ema_loaded = True
                return
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "token_ema_load_failed",
                path=str(path),
                error=str(exc),
            )
            self._token_ema = {}
            self._token_ema_loaded = True
            return
        ema: dict[str, float] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if not isinstance(key, str):
                    continue
                try:
                    ema[key.lower()] = float(value)
                except (TypeError, ValueError):
                    continue
        self._token_ema = ema
        self._token_ema_loaded = True

    def _persist_token_ema(self, cfg: ServiceConfig) -> None:
        """Best-effort flush to disk via tmp+rename. Failures only log."""
        path = self._token_ema_path(cfg)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._token_ema, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            log.warning(
                "token_ema_persist_failed",
                path=str(path),
                error=str(exc),
            )

    def _update_token_ema(
        self, state: str, total_tokens: int, cfg: ServiceConfig | None
    ) -> None:
        """Fold ``total_tokens`` into the rolling EMA for ``state``.

        Standard EMA recurrence: ``ema_new = α·sample + (1-α)·ema_prev``.
        Unseen states start at zero, so a first sample lands at α·sample.
        Persists every update so the budget survives mid-turn crashes.
        """
        if total_tokens <= 0:
            return
        key = (state or "").lower()
        if not key:
            return
        prev = self._token_ema.get(key, 0.0)
        self._token_ema[key] = (
            _TOKEN_EMA_ALPHA * float(total_tokens)
            + (1.0 - _TOKEN_EMA_ALPHA) * prev
        )
        if cfg is not None:
            self._persist_token_ema(cfg)

    def _token_ema_for_state(self, state: str) -> int:
        """Rounded EMA for a state. 0 when unseen."""
        key = (state or "").lower()
        return int(round(self._token_ema.get(key, 0.0)))

    def _token_budget_for_state(
        self, cfg: ServiceConfig, state: str
    ) -> int:
        """Hard cap from `agent.max_total_tokens_by_state` w/ fallback."""
        key = (state or "").lower()
        by_state = cfg.agent.max_total_tokens_by_state
        cap = by_state.get(key)
        if cap is None and key == "learn":
            cap = by_state.get("learning")
        if cap is None and key == "learning":
            cap = by_state.get("learn")
        return cap if cap is not None else cfg.agent.max_total_tokens

    def _max_state_turns_for_state(
        self, cfg: ServiceConfig, state: str
    ) -> int:
        """Same-state turn cap from per-state config w/ global fallback."""
        key = (state or "").lower()
        by_state = cfg.agent.max_state_turns_by_state
        cap = by_state.get(key)
        if cap is None and key == "learn":
            cap = by_state.get("learning")
        if cap is None and key == "learning":
            cap = by_state.get("learn")
        return cap if cap is not None else cfg.agent.max_state_turns

    def _token_attention_threshold_for_state(
        self, cfg: ServiceConfig, state: str
    ) -> int:
        """Attention-only threshold from explicit per-state config."""
        key = (state or "").lower()
        by_state = cfg.agent.token_attention_threshold_by_state
        threshold = by_state.get(key)
        if threshold is None and key == "learn":
            threshold = by_state.get("learning")
        if threshold is None and key == "learning":
            threshold = by_state.get("learn")
        return threshold or 0

    def _ticket_prompt_path(
        self, cfg: ServiceConfig, issue: Issue
    ) -> str | None:
        if cfg.tracker.kind != "file":
            return None
        tracker = getattr(self, "_tracker", None)
        find_path = getattr(tracker, "find_path", None)
        if find_path is None:
            return None
        path = find_path(issue.identifier)
        return str(path) if path is not None else None

    def _record_token_attention_for_turn(
        self, entry: RunningEntry, cfg: ServiceConfig | None
    ) -> int:
        turn_total = max(
            entry.codex_total_tokens - entry.token_attention_total_tokens,
            0,
        )
        entry.token_attention_total_tokens = entry.codex_total_tokens
        debug = self._issue_debug.setdefault(entry.issue.id, _IssueDebug())
        state = entry.state_at_turn_start or entry.issue.state
        if entry.current_turn_message.strip() and turn_total == 0:
            debug.token_attention = _attention_signal(
                "token_telemetry_suspect",
                "Token telemetry",
                (
                    "productive turn reported zero total tokens; "
                    "backend telemetry may be incomplete"
                ),
                "warning",
            )
            log.warning(
                "token_telemetry_suspect",
                issue_id=entry.issue.id,
                identifier=entry.issue.identifier,
                state=state,
                turn_total_tokens=turn_total,
            )
            return turn_total
        threshold = (
            self._token_attention_threshold_for_state(cfg, state)
            if cfg is not None
            else 0
        )
        if threshold > 0 and turn_total > threshold:
            debug.token_attention = _attention_signal(
                "token_attention_threshold",
                "Token threshold",
                (
                    f"turn used {turn_total}/{threshold} total tokens "
                    f"in {state}"
                ),
                "warning",
            )
            log.warning(
                "token_attention_threshold_exceeded",
                issue_id=entry.issue.id,
                identifier=entry.issue.identifier,
                state=state,
                turn_total_tokens=turn_total,
                threshold=threshold,
            )
            return turn_total
        debug.token_attention = None
        return turn_total

    # ------------------------------------------------------------------
    # A2-orch + C3 — backend subprocess env injection
    # ------------------------------------------------------------------

    def _apply_dispatch_env(
        self,
        *,
        issue: Issue,
        cfg: ServiceConfig,
        is_rewind: bool,
    ) -> None:
        """Set per-dispatch env vars consumed by the backend subprocess.

        Always sets:
          * ``SYMPHONY_TOKEN_EMA`` — rolling EMA of total tokens for the
            current state (rounded int), 0 when unseen.
          * ``SYMPHONY_TOKEN_BUDGET`` — hard cap for the current state
            (max_total_tokens_by_state with fallback to max_total_tokens).

        On rewind dispatches also sets:
          * ``SYMPHONY_REWIND_SCOPE`` — JSON list of finding rows parsed
            from the latest applicable failure section (`## Review Findings`,
            `## QA Failure`, or `## Contract Failure`). Empty list when
            parsing fails (the env var is informational; an empty list
            signals "rewind, no machine-readable scope" without unsetting).

        On forward dispatches the rewind scope env var is UNSET so a
        previous-turn value can't bleed across.

        Backends inherit `os.environ`, so this mutates process-global
        state. Concurrent dispatches in the same tick are serialised by
        the orchestrator's single event loop, and each backend spawns
        its subprocess before the next dispatch lands.
        """
        ema_value = self._token_ema_for_state(issue.state)
        budget_value = self._token_budget_for_state(cfg, issue.state)
        os.environ["SYMPHONY_TOKEN_EMA"] = str(ema_value)
        os.environ["SYMPHONY_TOKEN_BUDGET"] = str(budget_value)
        if is_rewind:
            rows = _parse_findings_rows(issue.description)
            try:
                payload = json.dumps(rows, ensure_ascii=False)
            except (TypeError, ValueError):
                payload = "[]"
            os.environ["SYMPHONY_REWIND_SCOPE"] = payload
        else:
            os.environ.pop("SYMPHONY_REWIND_SCOPE", None)

    # ------------------------------------------------------------------
    # dispatch (§16.4)
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        issue: Issue,
        cfg: ServiceConfig,
        *,
        attempt: int | None,
        attempt_kind: str | None = None,
    ) -> None:
        self._dispatch_state.cancel_pending_retry(issue.id)

        workspace_path = (
            self._workspace_manager.path_for(issue.identifier)
            if self._workspace_manager
            else Path("/")
        )
        resolved_attempt_kind = attempt_kind or (
            "retry" if attempt is not None else "initial"
        )
        agent_kind = _requested_agent_kind(issue) or cfg.agent.kind
        run_id = self._try_acquire_run_lease(
            issue=issue,
            workspace_path=workspace_path,
            attempt=attempt,
            attempt_kind=resolved_attempt_kind,
            agent_kind=agent_kind,
        )
        if run_id is None:
            return
        entry = RunningEntry(
            issue=issue,
            started_at=datetime.now(timezone.utc),
            retry_attempt=attempt,
            worker_task=None,
            workspace_path=workspace_path,
            attempt_kind=resolved_attempt_kind,
            agent_kind=agent_kind,
            run_id=run_id,
        )
        self._dispatch_state.begin_run(issue.id, entry)
        try:
            worker_task = asyncio.create_task(
                self._run_agent_attempt(issue, attempt, cfg),
                name=f"symphony-worker-{issue.identifier}",
            )
        except Exception:
            self._dispatch_state.abort_run(issue.id)
            self._finish_run_lease(issue.id, entry, "dispatch_failed")
            raise
        entry.worker_task = worker_task
        worker_task.add_done_callback(
            lambda task, issue_id=issue.id: self._on_worker_task_done(issue_id, task)
        )
        debug = self._issue_debug.setdefault(issue.id, _IssueDebug())
        if attempt is not None:
            debug.restart_count += 1
        debug.current_attempt_kind = entry.attempt_kind
        log.info(
            "dispatch",
            issue_id=issue.id,
            issue_identifier=issue.identifier,
            attempt=attempt,
            agent_kind=entry.agent_kind,
        )
        # Persist the resolved backend onto the ticket so downstream
        # consumers (board UIs, audits, Done-state history) can see who
        # ran which ticket without inferring from logs. Idempotent —
        # adapter preserves any existing override.
        try:
            self._tracker_call_record_agent_kind(cfg, issue.identifier, entry.agent_kind)
        except Exception as exc:
            log.warning(
                "record_agent_kind_failed",
                issue_id=issue.id,
                identifier=issue.identifier,
                agent_kind=entry.agent_kind,
                error=str(exc),
            )

    def _on_worker_task_done(self, issue_id: str, task: asyncio.Task[None]) -> None:
        """Clean a registered worker whose coroutine never ran its cleanup.

        If a task is cancelled before its first scheduling slice, Python never
        enters the coroutine body, which means `_run_agent_attempt`'s `finally`
        cannot call `_on_worker_exit`. The usual path pops `_running` before
        this callback fires; a remaining entry means the slot would otherwise
        leak forever.

        The registered entry MUST belong to `task` itself. `_on_worker_exit`
        yields once at `_notify_observers`, and the 1s continuation retry
        timer can fire inside that yield to install a fresh entry under the
        same key. A stale callback that pops it would log a phantom
        `worker_task_finished_without_cleanup` and eject the live worker.
        """
        entry = self._dispatch_state.entry_owned_by(issue_id, task)
        if entry is None:
            return
        if entry.exit_started_at is not None:
            log.info(
                "worker_task_done_after_exit_started",
                issue_id=issue_id,
                task_name=task.get_name(),
                exit_started_at=entry.exit_started_at.isoformat(),
            )
            return
        exc_repr: str | None = None
        if task.cancelled():
            reason = "worker_task_cancelled_before_start"
            error = "asyncio task was cancelled before worker cleanup ran"
        else:
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                reason = "worker_task_cancelled_before_start"
                error = "asyncio task was cancelled before worker cleanup ran"
                exc = None
            else:
                reason = "worker_task_finished_without_cleanup"
                error = str(exc) if exc is not None else "worker task completed without exit cleanup"
            if exc is not None:
                exc_repr = f"{type(exc).__name__}: {exc!r}"
        # Diagnostic fields for hunting the leftover path that leaves an
        # entry in `_running` after the worker task is `done`. If this
        # branch ever fires, these surface (a) which coroutine the task
        # was running, (b) whether the entry was actually populated, and
        # (c) how far the worker got — enough to localize the missing
        # cleanup in a single repro.
        coro = task.get_coro()
        log.error(
            "worker_task_done_without_cleanup",
            issue_id=issue_id,
            reason=reason,
            error=error,
            task_name=task.get_name(),
            coro_qualname=getattr(coro, "__qualname__", repr(coro)),
            task_done=task.done(),
            task_cancelled=task.cancelled(),
            exc_repr=exc_repr,
            entry_started_at=entry.started_at.isoformat(),
            entry_turn_count=entry.turn_count,
            entry_workspace=str(entry.workspace_path),
            entry_cancelled_at=(
                entry.cancelled_at.isoformat() if entry.cancelled_at else None
            ),
        )
        self._spawn_supervised(
            self._on_worker_exit(issue_id, reason, error),
            name=f"symphony-worker-exit-{issue_id}",
        )

    # ------------------------------------------------------------------
    # worker (§16.5)
    # ------------------------------------------------------------------

    async def _run_agent_attempt(
        self, issue: Issue, attempt: int | None, cfg: ServiceConfig
    ) -> None:
        running_issue_id = issue.id
        outcome: str = "normal"
        error: str | None = None
        try:
            cfg = _config_for_issue_agent(cfg, issue)
            running = self._running.get(running_issue_id)
            if running is not None:
                running.agent_kind = cfg.agent.kind
            assert self._workspace_manager is not None
            workspace = await self._workspace_manager.create_or_reuse(issue.identifier)
            running = self._running.get(running_issue_id)
            if running is None:
                # Slot was reclaimed externally between dispatch and the
                # first await completing. Surface the orphan path instead
                # of crashing on `KeyError(running_issue_id)` — that crash
                # was the source of the worker_task_finished_without_cleanup
                # cascade observed on OLV-002.
                outcome = "orphaned"
                error = "running entry vanished before workspace bind"
                log.warning(
                    "worker_running_entry_vanished",
                    issue_id=running_issue_id,
                    site="workspace_bind",
                )
                return
            running.workspace_path = workspace.path
            try:
                await self._workspace_manager.before_run(workspace.path)
            except Exception as exc:
                outcome = "before_run_error"
                error = str(exc)
                return

            tools = []
            if cfg.tracker.kind == "linear" and cfg.agent.kind == "codex":
                tools.append(linear_graphql_tool())

            client = _pkg.build_backend(
                BackendInit(
                    cfg=cfg,
                    cwd=workspace.path,
                    workspace_root=cfg.workspace_root,
                    on_event=lambda ev, issue_id=running_issue_id: self._on_codex_event(
                        issue_id, ev
                    ),
                    client_tools=tools,
                )
            )
            # Expose the live backend to `_on_codex_event` so the stall-progress
            # predicate routes through `client.is_progress_event(...)`.
            running.client = client
            after_run_pending = False
            # Initial dispatch is always forward (no rewind); the env
            # mutation MUST land before `client.start()` because the
            # backend subprocess inherits os.environ at fork time.
            self._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=False)
            try:
                await client.start()
                await client.initialize()

                turn_number = 1
                # `cfg.tui.language` is the operator-chosen language for
                # both TUI chrome AND artefact docs. Resolution already
                # honours `SYMPHONY_LANG` (build_service_config call).
                doc_language = cfg.tui.language
                # Skill files are read off-loop; dispatch shares the event
                # loop with every other running worker.
                skill_context = await asyncio.to_thread(
                    render_skill_block, cfg.workflow_path.parent, issue.skills
                )
                first_prompt, _ = build_first_turn_prompt(
                    prompt_template=cfg.prompt_template_for_state(issue.state),
                    issue=issue,
                    attempt=attempt,
                    language=doc_language,
                    max_turns=cfg.agent.max_turns,
                    max_attempts=cfg.agent.max_attempts,
                    auto_merge_on_done=cfg.agent.auto_merge_on_done,
                    token_ema=self._token_ema_for_state(issue.state),
                    token_budget=self._token_budget_for_state(cfg, issue.state),
                    rewind_scope=None,
                    compact_issue_context=cfg.agent.compact_issue_context,
                    full_ticket_path=self._ticket_prompt_path(cfg, issue),
                    extra_context=skill_context,
                )
                await client.start_session(
                    initial_prompt=first_prompt,
                    issue_title=f"{issue.identifier}: {issue.title}",
                )

                # Track which kanban state the backend is currently
                # operating on. When the issue moves to a new state mid-run
                # we tear the backend down and rebuild it so the next phase
                # starts with a fresh context — shared knowledge flows only
                # through the markdown artefacts under
                # `docs/<identifier>/<stage>/` plus the ticket body.
                prev_phase_state = normalize_state(issue.state)
                # Canonical-cased mirror of `prev_phase_state`. Trackers
                # like Linear and Jira match state names case-sensitively
                # on writes, so a contract-failure rewind needs the
                # original casing rather than the lowercased form.
                prev_phase_state_raw = issue.state or ""

                while True:
                    # Operator pause gate — `pause_worker` clears the event,
                    # `resume_worker` sets it. Honoured at the turn boundary
                    # so we never tear down a turn the model is mid-way
                    # through. On resume, re-fetch issue state because the
                    # operator may have moved the ticket while it was held.
                    pause_event = self._pause_events.get(running_issue_id)
                    if pause_event is not None and not pause_event.is_set():
                        log.info(
                            "worker_paused",
                            issue_id=running_issue_id,
                            identifier=issue.identifier,
                            turn=turn_number,
                        )
                        await pause_event.wait()
                        log.info(
                            "worker_resumed",
                            issue_id=running_issue_id,
                            identifier=issue.identifier,
                            turn=turn_number,
                        )
                        refreshed = await self._refresh_issue_state(
                            cfg, running_issue_id
                        )
                        if refreshed is not None:
                            issue = refreshed
                            running_entry = self._running.get(running_issue_id)
                            if running_entry is not None:
                                running_entry.issue = issue

                    current_state = normalize_state(issue.state)
                    debug = self._issue_debug.setdefault(
                        running_issue_id, _IssueDebug()
                    )
                    if (
                        cfg.agent.max_total_turns > 0
                        and debug.completed_turn_count + turn_number
                        > cfg.agent.max_total_turns
                    ):
                        log.warning(
                            "worker_total_turn_budget_boundary",
                            issue_id=running_issue_id,
                            issue_identifier=issue.identifier,
                            completed_turns=debug.completed_turn_count,
                            next_turn=turn_number,
                            max_total_turns=cfg.agent.max_total_turns,
                        )
                        break
                    is_phase_transition = (
                        turn_number > 1 and current_state != prev_phase_state
                    )

                    if is_phase_transition:
                        try:
                            is_rewind = _is_rewind_transition(
                                prev_phase_state, current_state
                            )
                            # v0.6.7 — contract validator. When the agent
                            # moved forward (not a rewind), check that
                            # the producing stage actually wrote the
                            # sections its prompt promised. On failure:
                            # write the tracker state back to the
                            # producing stage, append a ## Contract
                            # Failure note, and treat the situation as
                            # a forced rewind so the rebuild + budget
                            # bookkeeping below still apply.
                            if not is_rewind:
                                if prev_phase_state in {
                                    "in progress",
                                    "verify",
                                    "learn",
                                    "done",
                                }:
                                    # IMPORTANT: contract eval reads
                                    # `issue.description`, so we MUST use
                                    # the full-body refresh — not the
                                    # minimal `_refresh_issue_state`, which
                                    # returns description=None for every
                                    # tracker adapter and would falsely
                                    # fail every forward transition. See
                                    # tests/test_orchestrator_contract_
                                    # integration.py for the regression
                                    # the v0.6.7 release surfaced.
                                    refreshed_for_contract = (
                                        await self._refresh_issue_full(
                                            cfg, running_issue_id
                                        )
                                    )
                                    if refreshed_for_contract is not None:
                                        issue = refreshed_for_contract
                                        running_entry = self._running.get(
                                            running_issue_id
                                        )
                                        if running_entry is not None:
                                            running_entry.issue = issue
                                        current_state = normalize_state(issue.state)
                                contract = evaluate_contract(
                                    producing_state=prev_phase_state,
                                    ticket_body=issue.description or "",
                                    identifier=issue.identifier,
                                    docs_root=workspace.path / "docs",
                                )
                                if not contract.passed:
                                    log.warning(
                                        "stage_contract_failed",
                                        issue_id=issue.id,
                                        identifier=issue.identifier,
                                        producing_state=prev_phase_state,
                                        advanced_to=current_state,
                                        missing=contract.missing,
                                    )
                                    await asyncio.to_thread(
                                        self._tracker_call_append_note,
                                        cfg,
                                        issue,
                                        contract.note_heading,
                                        contract.note_body,
                                    )
                                    await asyncio.to_thread(
                                        self._tracker_call_update_state,
                                        cfg,
                                        issue,
                                        prev_phase_state_raw
                                        or prev_phase_state,
                                    )
                                    # Pull the freshly-rewound body so the
                                    # next backend rebuild's first prompt
                                    # sees the ## Contract Failure note we
                                    # just appended (full-body fetch — see
                                    # the comment above the preflight
                                    # refresh for why minimal would erase
                                    # description).
                                    refreshed = await self._refresh_issue_full(
                                        cfg, running_issue_id
                                    )
                                    if refreshed is not None:
                                        issue = refreshed
                                    issue = replace(
                                        issue,
                                        state=(
                                            prev_phase_state_raw
                                            or prev_phase_state
                                        ),
                                    )
                                    running_entry = self._running.get(
                                        running_issue_id
                                    )
                                    if running_entry is not None:
                                        running_entry.issue = issue
                                    current_state = normalize_state(issue.state)
                                    is_rewind = True
                                elif contract.warnings:
                                    # Soft S2 advisories (e.g. a non-passing AC
                                    # Scorecard row): surface as a ticket note
                                    # without rewinding so the pipeline proceeds.
                                    log.warning(
                                        "stage_contract_warn",
                                        issue_id=issue.id,
                                        identifier=issue.identifier,
                                        producing_state=prev_phase_state,
                                        advanced_to=current_state,
                                        warnings=contract.warnings,
                                    )
                                    await asyncio.to_thread(
                                        self._tracker_call_append_note,
                                        cfg,
                                        issue,
                                        "Contract Warning",
                                        contract.warning_note.split("\n", 1)[1],
                                    )
                            if is_rewind:
                                debug = self._issue_debug.setdefault(
                                    running_issue_id, _IssueDebug()
                                )
                                debug.rewind_count += 1
                                if (
                                    cfg.agent.max_attempts > 0
                                    and debug.rewind_count > cfg.agent.max_attempts
                                ):
                                    await asyncio.to_thread(
                                        self._tracker_call_update_state,
                                        cfg,
                                        issue,
                                        "Blocked",
                                    )
                                    issue = replace(issue, state="Blocked")
                                    running_entry = self._running.get(
                                        running_issue_id
                                    )
                                    if running_entry is not None:
                                        running_entry.issue = issue
                                    log.warning(
                                        "rewind_budget_exceeded",
                                        issue_id=issue.id,
                                        identifier=issue.identifier,
                                        from_state=prev_phase_state,
                                        to_state=current_state,
                                        rewind_count=debug.rewind_count,
                                        max_attempts=cfg.agent.max_attempts,
                                    )
                                    break
                            client, first_prompt = await self._rebuild_backend_for_phase(
                                issue=issue,
                                running_issue_id=running_issue_id,
                                cfg=cfg,
                                workspace_path=workspace.path,
                                attempt=attempt,
                                doc_language=doc_language,
                                old_client=client,
                                is_rewind=is_rewind,
                            )
                            running_entry = self._running.get(running_issue_id)
                            if running_entry is not None:
                                # New backend instance — refresh the
                                # `_on_codex_event` reference so the stall
                                # predicate keeps routing to the live driver.
                                running_entry.client = client
                                running_entry.thread_id = None
                                running_entry.session_id = None
                                running_entry.turn_id = None
                                # New backend session reports absolute token
                                # totals from 0; the high-water marks below
                                # MUST reset or `_apply_token_totals` computes
                                # `max(new - old_high, 0) = 0` and silently
                                # drops every token from the new phase until
                                # the cumulative count overtakes the old mark.
                                # Cumulative `codex_*_tokens` are NOT reset;
                                # state-local totals reset so
                                # max_total_tokens_by_state is measured per
                                # stage, not against ticket lifetime usage.
                                running_entry.last_reported_input_tokens = 0
                                running_entry.last_reported_cache_input_tokens = 0
                                running_entry.last_reported_output_tokens = 0
                                running_entry.last_reported_total_tokens = 0
                                running_entry.codex_state_input_tokens = 0
                                running_entry.codex_state_cache_input_tokens = 0
                                running_entry.codex_state_output_tokens = 0
                                running_entry.codex_state_total_tokens = 0
                                # Per-stage EMA window restarts with the
                                # new state so first-turn cost in the new
                                # stage isn't inflated by the prior
                                # stage's cumulative total.
                                running_entry.last_ema_state_total_tokens = 0
                                running_entry.hit_token_budget = False
                                running_entry.token_budget_cap = 0
                                debug.state_turn_state = current_state
                                debug.state_turn_count = 0
                            log.info(
                                "worker_phase_transition",
                                issue_id=issue.id,
                                identifier=issue.identifier,
                                from_state=prev_phase_state,
                                to_state=current_state,
                                turn=turn_number,
                                attempt=attempt,
                                is_rewind=is_rewind,
                                workspace=str(workspace.path),
                            )
                            self._record_stats_transition(
                                issue.identifier, prev_phase_state, current_state
                            )
                        except Exception as exc:
                            outcome = "phase_transition_error"
                            error = str(exc)
                            return

                    is_continuation = turn_number > 1 and not is_phase_transition
                    if is_continuation:
                        debug = self._issue_debug.setdefault(running_issue_id, _IssueDebug())
                        prompt = build_continuation_prompt(
                            language=doc_language,
                            turn_number=debug.completed_turn_count + turn_number,
                            max_turns=cfg.agent.max_total_turns,
                        )
                    else:
                        prompt = first_prompt

                    running = self._running.get(running_issue_id)
                    if running is None:
                        outcome = "orphaned"
                        error = "running entry vanished before turn start"
                        log.warning(
                            "worker_running_entry_vanished",
                            issue_id=running_issue_id,
                            site="turn_start",
                        )
                        return
                    running.turn_count = turn_number
                    # Capture the state THIS turn is starting in. C3 EMA
                    # samples need the source state, not the destination
                    # the agent flips to mid-turn — without this, every
                    # stage's tokens get attributed to the next stage.
                    running.state_at_turn_start = (
                        running.issue.state or ""
                    ).lower()
                    # Symmetry with worker_turn_completed — a single line per
                    # turn-start so multi-turn runs (especially slow ones
                    # like gemini -p where a single turn can take 60-90s)
                    # don't look stuck between turns.
                    log.info(
                        "worker_turn_started",
                        issue_id=running_issue_id,
                        identifier=running.issue.identifier,
                        turn=turn_number,
                        max_turns=cfg.agent.max_turns,
                        is_continuation=is_continuation,
                    )
                    if turn_number > 1:
                        try:
                            await self._workspace_manager.before_run(workspace.path)
                        except Exception as exc:
                            outcome = "before_run_error"
                            error = str(exc)
                            return
                    after_run_pending = True
                    try:
                        await client.run_turn(prompt=prompt, is_continuation=is_continuation)
                    except (TurnTimeout, TurnFailed, TurnCancelled, TurnInputRequired) as exc:
                        outcome = "turn_error"
                        error = str(exc)
                        return

                    # Synchronous log on the worker's hot path — the
                    # listener-side `agent_turn_completed` log fires from
                    # `_on_codex_event` via the EVENT_TURN_COMPLETED emit,
                    # but reconcile can cancel the worker between the emit
                    # and the listener running, swallowing the visibility
                    # signal. Logging here guarantees one line per
                    # successful turn even when reconcile races us.
                    running_entry = self._running.get(running_issue_id)
                    if running_entry is not None:
                        log.info(
                            "worker_turn_completed",
                            issue_id=running_issue_id,
                            identifier=running_entry.issue.identifier,
                            turn=turn_number,
                            input_tokens=running_entry.codex_input_tokens,
                            cache_input_tokens=running_entry.codex_cache_input_tokens,
                            output_tokens=running_entry.codex_output_tokens,
                            total_tokens=running_entry.codex_total_tokens,
                        )

                    await self._workspace_manager.after_run_best_effort(workspace.path)
                    after_run_pending = False

                    # Record the state the backend just operated on so the
                    # next iteration can detect a phase transition against
                    # the freshly refreshed state below.
                    prev_phase_state = current_state
                    prev_phase_state_raw = (
                        running.issue.state if running is not None else issue.state
                    ) or ""

                    # Refresh issue state.
                    refreshed = await self._refresh_issue_state(cfg, running_issue_id)
                    if refreshed is None:
                        outcome = "issue_state_refresh_failed"
                        error = "could not refresh issue state"
                        return
                    issue = refreshed
                    running = self._running.get(running_issue_id)
                    if running is None:
                        outcome = "orphaned"
                        error = "running entry vanished after issue refresh"
                        log.warning(
                            "worker_running_entry_vanished",
                            issue_id=running_issue_id,
                            site="post_refresh",
                        )
                        return
                    running.issue = issue
                    state = normalize_state(issue.state)
                    active = {s.lower() for s in cfg.tracker.active_states}
                    if state not in active:
                        break
                    state_turn_count = _update_state_turn_counter(debug, state)
                    max_state_turns = self._max_state_turns_for_state(cfg, state)
                    if (
                        max_state_turns > 0
                        and state_turn_count >= max_state_turns
                    ):
                        running.hit_no_stage_change = True
                        log.warning(
                            "no_stage_change_watchdog",
                            issue_id=running_issue_id,
                            issue_identifier=running.issue.identifier,
                            state=running.issue.state,
                            state_turn_count=state_turn_count,
                            effective_max_state_turns=max_state_turns,
                            global_max_state_turns=cfg.agent.max_state_turns,
                        )
                        break
                    if turn_number >= cfg.agent.max_turns:
                        # Per-attempt ceiling reached without a terminal
                        # transition. Mark explicitly so `_on_worker_exit`
                        # doesn't auto-schedule a continuation — the ticket
                        # waits for operator action instead of looping
                        # silently against the ceiling.
                        running.hit_max_turns = True
                        log.warning(
                            "worker_max_turns_exhausted",
                            issue_id=running_issue_id,
                            issue_identifier=running.issue.identifier,
                            turns=turn_number,
                            max_turns=cfg.agent.max_turns,
                        )
                        break
                    turn_number += 1
            finally:
                # Defensive: a phase transition may have left `client`
                # pointing to a half-initialized backend, or to one whose
                # earlier `stop()` already failed. Either way, exiting the
                # worker without after_run_best_effort would leak workspace
                # state, so swallow stop() errors here too.
                try:
                    await client.stop()
                except Exception as stop_exc:
                    log.warning(
                        "worker_final_stop_failed",
                        issue_id=issue.id,
                        identifier=issue.identifier,
                        error=str(stop_exc),
                    )
                if after_run_pending:
                    await self._workspace_manager.after_run_best_effort(workspace.path)
        except SymphonyError as exc:
            outcome = "error"
            error = str(exc)
        except Exception as exc:
            outcome = "error"
            error = str(exc)
            log.error(
                "worker_unhandled_error",
                issue_id=running_issue_id,
                error=str(exc),
                exc_type=type(exc).__name__,
                traceback=traceback.format_exc(),
            )
        finally:
            # Diagnostic marker — pairs with `worker_task_done_without_cleanup`
            # to localize the path that leaves entries in `_running`. If
            # this line is missing from the log right before that error,
            # the outer finally never ran (Python contract violation =
            # interpreter shutdown / OS-level kill). If it IS present,
            # the bypass is inside `_on_worker_exit` itself.
            log.info(
                "worker_finally_entered",
                issue_id=running_issue_id,
                outcome=outcome,
                error=error,
            )
            entry = self._running.get(running_issue_id)
            if entry is not None:
                entry.exit_started_at = datetime.now(timezone.utc)
            await asyncio.shield(
                self._on_worker_exit(running_issue_id, outcome, error)
            )

    async def _rebuild_backend_for_phase(
        self,
        *,
        issue: Issue,
        running_issue_id: str,
        cfg: ServiceConfig,
        workspace_path: Path,
        attempt: int | None,
        doc_language: str,
        old_client: AgentBackend,
        is_rewind: bool,
    ) -> tuple[AgentBackend, str]:
        """Tear down `old_client` and rebuild a fresh-context backend.

        Returns `(new_client, new_first_prompt)` so the worker loop can
        rebind both. The caller is responsible for resetting bookkeeping
        on `RunningEntry` (session_id, token high-water marks, etc.) —
        keeping that here would couple this helper to the running-state
        dict and hurt testability.
        """
        # Defensive: a failing old-stop must not block the transition.
        # The new client we are about to build replaces the reference, so
        # any stuck resources in the old backend are someone else's
        # problem (the listener-side reaper or the OS).
        try:
            await old_client.stop()
        except Exception as stop_exc:
            log.warning(
                "phase_transition_old_stop_failed",
                issue_id=issue.id,
                identifier=issue.identifier,
                error=str(stop_exc),
            )
        tools: list[Any] = []
        if cfg.tracker.kind == "linear" and cfg.agent.kind == "codex":
            tools.append(linear_graphql_tool())
        new_client = _pkg.build_backend(
            BackendInit(
                cfg=cfg,
                cwd=workspace_path,
                workspace_root=cfg.workspace_root,
                on_event=lambda ev, issue_id=running_issue_id: self._on_codex_event(
                    issue_id, ev
                ),
                client_tools=tools,
            )
        )
        # Reset per-dispatch env BEFORE the new backend's subprocess spawns.
        # Forward phase transitions unset SYMPHONY_REWIND_SCOPE; rewinds
        # set it to the JSON of the latest finding rows.
        self._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=is_rewind)
        try:
            await new_client.start()
            await new_client.initialize()
            skill_context = await asyncio.to_thread(
                render_skill_block, cfg.workflow_path.parent, issue.skills
            )
            first_prompt, _ = build_first_turn_prompt(
                prompt_template=cfg.prompt_template_for_state(issue.state),
                issue=issue,
                attempt=attempt,
                language=doc_language,
                max_turns=cfg.agent.max_turns,
                max_attempts=cfg.agent.max_attempts,
                is_rewind=is_rewind,
                auto_merge_on_done=cfg.agent.auto_merge_on_done,
                token_ema=self._token_ema_for_state(issue.state),
                token_budget=self._token_budget_for_state(cfg, issue.state),
                rewind_scope=(
                    _parse_findings_rows(issue.description) if is_rewind else None
                ),
                compact_issue_context=cfg.agent.compact_issue_context,
                full_ticket_path=self._ticket_prompt_path(cfg, issue),
                extra_context=skill_context,
            )
            await new_client.start_session(
                initial_prompt=first_prompt,
                issue_title=f"{issue.identifier}: {issue.title}",
            )
        except BaseException:
            try:
                await new_client.stop()
            except Exception as stop_exc:
                log.warning(
                    "phase_transition_new_stop_failed",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    error=str(stop_exc),
                )
            raise
        return new_client, first_prompt

    async def _refresh_issue_state(
        self, cfg: ServiceConfig, issue_id: str
    ) -> Issue | None:
        try:
            results = await asyncio.to_thread(
                self._tracker_call_states_by_ids, cfg, [issue_id]
            )
        except Exception as exc:
            log.warning("issue_state_refresh_failed", issue_id=issue_id, error=str(exc))
            self._record_tracker_error(issue_id, exc)
            return None
        for issue in results:
            if issue.id == issue_id:
                self._clear_tracker_error(issue_id)
                return issue
        return None

    async def _refresh_issue_full(
        self, cfg: ServiceConfig, issue_id: str
    ) -> Issue | None:
        """Refresh an issue with its full body (description) from the tracker.

        `_refresh_issue_state` returns the *minimal* Issue payload — fast
        but strips description. The stage-contract validator (v0.6.7+)
        needs the live body to evaluate required-section presence, so the
        forward-transition path uses this helper instead. Returns None on
        transport failure or missing id; callers must keep the prior
        in-memory issue in that case (do NOT replace it with None).
        """
        try:
            issue = await asyncio.to_thread(
                self._tracker_call_full_by_id, cfg, issue_id
            )
            self._clear_tracker_error(issue_id)
            return issue
        except Exception as exc:
            log.warning(
                "issue_full_refresh_failed", issue_id=issue_id, error=str(exc)
            )
            self._record_tracker_error(issue_id, exc)
            return None

    async def _persist_budget_exhausted_state(
        self,
        *,
        cfg: ServiceConfig,
        entry: RunningEntry,
        issue_id: str,
        target_state: str,
        budget_kind: str,
        state_turn_limit: int | None = None,
    ) -> bool:
        if not target_state:
            return False
        if budget_kind == "tokens":
            budget_detail = (
                f"({entry.codex_state_total_tokens}/"
                f"{entry.token_budget_cap or cfg.agent.max_total_tokens})"
            )
        elif budget_kind == "max_turns":
            budget_detail = f"(max_turns={cfg.agent.max_turns}/attempt)"
        elif budget_kind == "empty_response_loop":
            budget_detail = (
                f"(consecutive_empty_turns={entry.consecutive_empty_turns}, "
                f"threshold={EMPTY_TURN_LOOP_THRESHOLD})"
            )
        elif budget_kind == "no_stage_change":
            debug = self._issue_debug.get(issue_id)
            count = debug.state_turn_count if debug is not None else 0
            state_name = entry.issue.state
            if not state_name and debug is not None:
                state_name = debug.state_turn_state
            limit = (
                state_turn_limit
                if state_turn_limit is not None
                else self._max_state_turns_for_state(cfg, state_name)
            )
            budget_detail = (
                f"(state_turns={count}, "
                f"effective_max_state_turns={limit})"
            )
        else:
            budget_detail = f"(max_total_turns={cfg.agent.max_total_turns})"
        note_body = (
            f"{budget_kind} budget exceeded {budget_detail} while state stayed "
            f"{entry.issue.state}. Symphony moved this ticket to {target_state} "
            f"to prevent automatic re-dispatch."
        )
        try:
            await asyncio.to_thread(
                self._tracker_call_update_state,
                cfg,
                entry.issue,
                target_state,
            )
            await asyncio.to_thread(
                self._tracker_call_append_note,
                cfg,
                entry.issue,
                "Budget Exceeded",
                note_body,
            )
            log.info(
                "budget_exhausted_persisted",
                issue_id=issue_id,
                issue_identifier=entry.issue.identifier,
                target_state=target_state,
                budget_kind=budget_kind,
            )
            self._clear_tracker_error(issue_id)
            return True
        except Exception as persist_exc:
            # Lenient: the in-memory guard still prevents another dispatch in
            # this process; the log explains why restart persistence failed.
            log.warning(
                "budget_exhausted_persist_failed",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                target_state=target_state,
                budget_kind=budget_kind,
                error=str(persist_exc),
            )
            self._record_tracker_error(issue_id, persist_exc)
            return False

    async def _persist_no_stage_change_handoff(
        self,
        *,
        cfg: ServiceConfig,
        entry: RunningEntry,
        issue_id: str,
        target_state: str,
        turn_count: int,
        state_name: str,
    ) -> bool:
        note_body = (
            f"Symphony stopped this worker: no stage change after {turn_count} "
            f"turns in {state_name}. "
            f"The workflow is configured to hand off to {target_state}, so "
            "Symphony moved the ticket there for the next stage."
        )
        try:
            await asyncio.to_thread(
                self._tracker_call_update_state,
                cfg,
                entry.issue,
                target_state,
            )
            await asyncio.to_thread(
                self._tracker_call_append_note,
                cfg,
                entry.issue,
                "Stage Watchdog Handoff",
                note_body,
            )
            self._clear_tracker_error(issue_id)
            return True
        except Exception as exc:
            log.warning(
                "no_stage_change_handoff_failed",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                target_state=target_state,
                error=str(exc),
            )
            self._record_tracker_error(issue_id, exc)
            return False

    # ------------------------------------------------------------------
    # codex events
    # ------------------------------------------------------------------

    @staticmethod
    def _token_cap_for_entry(cfg: ServiceConfig | None, entry: RunningEntry) -> int:
        if cfg is None:
            return 0
        state = normalize_state(entry.issue.state)
        by_state = cfg.agent.max_total_tokens_by_state
        cap = by_state.get(state)
        if cap is None and state == "learn":
            cap = by_state.get("learning")
        if cap is None and state == "learning":
            cap = by_state.get("learn")
        return cap if cap is not None else cfg.agent.max_total_tokens

    @staticmethod
    def _preview_from_payload(payload: dict[str, Any]) -> str:
        for key in ("message", "lastMessage", "text", "summary"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        item = payload.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type") or "").lower()
            text = item.get("text") or item.get("message")
            if isinstance(text, str) and text.strip():
                return text.strip()
            name = item.get("name") or item.get("tool") or item.get("command")
            args = item.get("arguments")
            command = ""
            if isinstance(args, dict):
                raw_cmd = args.get("cmd") or args.get("command")
                if isinstance(raw_cmd, str):
                    command = raw_cmd.strip()
            if name and ("tool" in item_type or command):
                suffix = f" {command}" if command else ""
                return f"tool: {name}{suffix}".strip()
        return ""

    async def _on_codex_event(self, issue_id: str, event: dict[str, Any]) -> None:
        entry = self._running.get(issue_id)
        if entry is None:
            return
        ev_name = str(event.get("event") or "")
        entry.last_codex_event = ev_name
        ts_text = event.get("timestamp")
        if isinstance(ts_text, str):
            try:
                entry.last_codex_timestamp = datetime.fromisoformat(
                    ts_text.replace("Z", "+00:00")
                )
            except ValueError:
                entry.last_codex_timestamp = datetime.now(timezone.utc)
        else:
            entry.last_codex_timestamp = datetime.now(timezone.utc)
        pid = event.get("codex_app_server_pid") or event.get("agent_pid")
        if isinstance(pid, int):
            entry.codex_app_server_pid = pid
            self._heartbeat_run_lease(issue_id, entry, backend_agent_pid=pid)
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            msg = self._preview_from_payload(payload)
            if msg:
                entry.last_codex_message = msg[:400]
                # G2 — per-turn buffer tracks any preview that arrived during
                # this turn. Cleared on EVENT_TURN_COMPLETED after the
                # empty-loop check so the next turn starts fresh.
                entry.current_turn_message = msg[:400]
            if ev_name == EVENT_APPROVAL_DENIED:
                command = str(payload.get("command") or "")
                reason = str(payload.get("reason") or "approval denied")
                debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
                if command:
                    debug.last_error = f"approval denied: {reason} ({command})"
                else:
                    debug.last_error = f"approval denied: {reason}"
                log.warning(
                    "approval_denied",
                    issue_id=issue_id,
                    identifier=entry.issue.identifier,
                    command=command,
                    reason=reason,
                )
        # Token deltas (§13.5).
        usage = event.get("usage") or {}
        delta_out = 0
        if isinstance(usage, dict):
            _, delta_out = self._apply_token_totals(entry, usage)
        # Hard token-budget cap. Catches the runaway-reasoning case the
        # stall predicate can't see: codex completes each turn but
        # accumulates 1.6M tokens per turn (history re-send) and burns
        # through dozens of megatokens before max_turns ends the attempt.
        # 0 = disabled (legacy default). On breach: cancel the worker and
        # record the reason so the operator finds out without log-diving.
        cfg = self._workflow_state.current()
        cap = self._token_cap_for_entry(cfg, entry)
        if (
            cap > 0
            and entry.cancelled_at is None
            and entry.codex_state_total_tokens >= cap
        ):
            log.warning(
                "token_budget_exceeded",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                state_total_tokens=entry.codex_state_total_tokens,
                total_tokens=entry.codex_total_tokens,
                cap=cap,
                state=entry.issue.state,
            )
            debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
            debug.last_error = (
                f"token budget exceeded "
                f"({entry.codex_state_total_tokens}/{cap} in {entry.issue.state}) "
                "— worker cancelled"
            )
            entry.hit_token_budget = True
            entry.token_budget_cap = cap
            if entry.worker_task is not None:
                entry.worker_task.cancel()
            entry.cancelled_at = datetime.now(timezone.utc)
        # Progress predicate — see RunningEntry.last_progress_timestamp.
        # `EVENT_OTHER_MESSAGE` is a catch-all that the claude backend fires
        # for both `assistant` (real model output) and `user` (tool_result
        # echo) stream-json messages. Treating every one as progress lets
        # the 5-min stall threshold get reset by tool_result echoes alone,
        # so a turn that produces no model output for 18 min still looks
        # alive. Filter: lifecycle events count, OUTPUT token movement
        # counts, and `EVENT_OTHER_MESSAGE` counts only when the payload's
        # `type` is `assistant` (matches claude_code stream-json shape;
        # harmless for other backends that don't set `type`).
        #
        # NOTE on `delta_out` (not `delta_total`): codex app-server attaches
        # `_latest_usage` to every emitted event, including catch-all
        # `EVENT_OTHER_MESSAGE` frames between turns. Codex inflates
        # `input_tokens` by re-sending conversation history each turn, so
        # `delta_total > 0` is true even when the model has produced no
        # output — that masked a real 18-turn / 30M-token reasoning loop
        # (IB-006, 2026-05-16). `output_tokens` only advances when the
        # model actually emits content, which is the signal we need.
        is_progress = ev_name != EVENT_OTHER_MESSAGE
        if not is_progress and isinstance(payload, dict):
            # Delegate the catch-all OTHER_MESSAGE filter to the backend so
            # per-driver echo shapes (claude stream-json `user`/tool_result
            # frames, codex preview items, future backends with their own
            # keepalive types) live next to the code that knows their wire
            # protocol. Claude and codex both narrow to `type=="assistant"`;
            # pi/gemini inherit the conservative `BaseAgentBackend` default
            # of always-True. When the backend reference isn't published
            # yet (e.g. unit tests poking `_on_codex_event` directly without
            # a build_backend call), apply the historical inline filter so
            # existing invariants hold.
            backend = entry.client
            if backend is None:
                is_progress = payload.get("type") == "assistant"
            else:
                is_progress = backend.is_progress_event(payload)
        if delta_out > 0:
            is_progress = True
        if is_progress:
            entry.last_progress_timestamp = entry.last_codex_timestamp
            self._heartbeat_run_lease(
                issue_id,
                entry,
                progress=entry.last_progress_timestamp,
            )
        # Rate limits.
        rl = event.get("rate_limits")
        if isinstance(rl, dict):
            self._latest_rate_limits = rl
        # Update session id when known. The backend reports a single session
        # identifier; this orchestrator stores it as `thread_id` for legacy
        # snapshot-shape stability and mirrors it as `session_id`. Codex
        # additionally exposes per-turn ids; when present they suffix the
        # session id so consumers can distinguish turns. Non-Codex backends
        # never set `turn_id`, so the suffix is silently skipped for them.
        if ev_name == EVENT_SESSION_STARTED:
            sid = (
                payload.get("session_id")
                or payload.get("thread_id")
                or payload.get("threadId")
            ) if isinstance(payload, dict) else None
            if sid:
                entry.thread_id = str(sid)
                entry.session_id = entry.thread_id
            log.info(
                "agent_session_started",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                session_id=entry.session_id,
            )
        if ev_name == EVENT_TURN_COMPLETED:
            turn_id = payload.get("turnId") or payload.get("turn_id")
            if turn_id and entry.thread_id:
                entry.turn_id = str(turn_id)
                entry.session_id = f"{entry.thread_id}-{entry.turn_id}"
            log.info(
                "agent_turn_completed",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                turn=entry.turn_count,
                input_tokens=entry.codex_input_tokens,
                cache_input_tokens=entry.codex_cache_input_tokens,
                output_tokens=entry.codex_output_tokens,
                total_tokens=entry.codex_total_tokens,
                last_message=(entry.last_codex_message or "")[:160],
            )
            self._record_token_attention_for_turn(entry, cfg)
            self._record_stats_turn(entry)
            # C3 — adaptive token budget. Sample = per-turn state-local
            # total tokens. `_update_token_ema` no-ops on non-positive
            # samples, so a turn with zero token movement (e.g. an event
            # that fires before any usage is reported) is skipped
            # silently rather than dragging the EMA toward zero.
            turn_sample = max(
                entry.codex_state_total_tokens
                - entry.last_ema_state_total_tokens,
                0,
            )
            if turn_sample > 0:
                # Prefer the source state (captured at worker_turn_started)
                # so a stage that flipped the ticket mid-turn still has its
                # cost attributed correctly. Fall back to current state for
                # event-injection unit tests that bypass turn_started.
                target_state = (
                    entry.state_at_turn_start or entry.issue.state
                )
                self._update_token_ema(
                    target_state, turn_sample, cfg
                )
                entry.last_ema_state_total_tokens = (
                    entry.codex_state_total_tokens
                )
            # G2 — empty-response loop guard. A turn whose `current_turn_message`
            # stayed empty produced no fresh preview text. Counter resets on a
            # turn with real preview; crossing the threshold cancels the worker
            # and persists via the existing budget-exhausted plumbing.
            if entry.current_turn_message.strip():
                entry.consecutive_empty_turns = 0
            else:
                entry.consecutive_empty_turns += 1
            entry.current_turn_message = ""
            if (
                entry.consecutive_empty_turns >= EMPTY_TURN_LOOP_THRESHOLD
                and entry.cancelled_at is None
            ):
                log.warning(
                    "empty_response_loop",
                    issue_id=issue_id,
                    identifier=entry.issue.identifier,
                    consecutive_empty_turns=entry.consecutive_empty_turns,
                    threshold=EMPTY_TURN_LOOP_THRESHOLD,
                )
                debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
                debug.last_error = (
                    f"empty_response_loop after "
                    f"{entry.consecutive_empty_turns} consecutive empty turns"
                )
                if cfg is not None:
                    await self._persist_budget_exhausted_state(
                        cfg=cfg,
                        entry=entry,
                        issue_id=issue_id,
                        target_state=cfg.agent.budget_exhausted_state,
                        budget_kind="empty_response_loop",
                    )
                # G2 — auto-pause the ticket so dispatch + retry both refuse to
                # restart it even when `budget_exhausted_state` is unset (the
                # persist branch above is a no-op then). The pause survives
                # worker exit via `_paused_issue_ids` and is the same gate the
                # operator's manual pause uses, so the operator's existing
                # resume_worker() path lifts it. Without this, an unconfigured
                # budget_exhausted_state lets the loop re-dispatch immediately
                # on the next tick (verified live on olive-clone 2026-05-20).
                if issue_id not in self._paused_issue_ids:
                    pause_reason = (
                        f"empty_response_loop: {entry.consecutive_empty_turns} "
                        f"consecutive empty turns "
                        f"(threshold {EMPTY_TURN_LOOP_THRESHOLD}); resume via "
                        "resume_worker after inspecting the ticket"
                    )
                    self._paused_issue_ids.add(issue_id)
                    self._pause_reasons[issue_id] = pause_reason
                    self._set_issue_flags(
                        issue_id,
                        paused=True,
                        pause_reason=pause_reason,
                    )
                    pause_event = self._pause_events.get(issue_id)
                    if pause_event is None:
                        pause_event = asyncio.Event()
                        self._pause_events[issue_id] = pause_event
                    pause_event.clear()
                    log.info(
                        "empty_response_loop_auto_paused",
                        issue_id=issue_id,
                        identifier=entry.issue.identifier,
                    )
                if entry.worker_task is not None:
                    entry.worker_task.cancel()
                entry.cancelled_at = datetime.now(timezone.utc)
        if ev_name == EVENT_TURN_FAILED:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            stderr_tail = payload.get("stderr_tail") if isinstance(payload, dict) else None
            log.warning(
                "agent_turn_failed",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                turn=entry.turn_count,
                reason=str(reason) if reason else "",
                stderr_tail=stderr_tail if isinstance(stderr_tail, list) else None,
            )
        if ev_name == EVENT_COMPACTION:
            phase = payload.get("phase") if isinstance(payload, dict) else None
            log.info(
                "agent_compaction",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                phase=str(phase) if phase else "",
                reason=str(payload.get("reason") or "")
                if isinstance(payload, dict) else "",
                tokens_before=payload.get("tokens_before")
                if isinstance(payload, dict) else None,
            )
        if ev_name == EVENT_AGENT_RETRY:
            phase = payload.get("phase") if isinstance(payload, dict) else None
            log.info(
                "agent_internal_retry",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                phase=str(phase) if phase else "",
                attempt=payload.get("attempt") if isinstance(payload, dict) else None,
                error=str(payload.get("error") or payload.get("final_error") or "")
                if isinstance(payload, dict) else "",
            )

        # Track recent events.
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.recent_events.append(
            {
                "at": ts_text or _utc_iso_z(),
                "event": ev_name,
                "message": entry.last_codex_message,
            }
        )
        if len(debug.recent_events) > 50:
            debug.recent_events = debug.recent_events[-50:]

    def _apply_token_totals(
        self, entry: RunningEntry, totals: dict[str, Any]
    ) -> tuple[int, int]:
        in_tok = int(totals.get("input_tokens") or 0)
        cache_tok = int(totals.get("cache_input_tokens") or 0)
        out_tok = int(totals.get("output_tokens") or 0)
        tot_tok = int(totals.get("total_tokens") or (in_tok + cache_tok + out_tok))
        # §13.5 — track deltas from last reported absolute totals.
        delta_in = max(in_tok - entry.last_reported_input_tokens, 0)
        delta_cache = max(cache_tok - entry.last_reported_cache_input_tokens, 0)
        delta_out = max(out_tok - entry.last_reported_output_tokens, 0)
        delta_total = max(tot_tok - entry.last_reported_total_tokens, 0)
        entry.last_reported_input_tokens = in_tok
        entry.last_reported_cache_input_tokens = cache_tok
        entry.last_reported_output_tokens = out_tok
        entry.last_reported_total_tokens = tot_tok
        entry.codex_input_tokens += delta_in
        entry.codex_cache_input_tokens += delta_cache
        entry.codex_output_tokens += delta_out
        entry.codex_total_tokens += delta_total
        entry.codex_state_input_tokens += delta_in
        entry.codex_state_cache_input_tokens += delta_cache
        entry.codex_state_output_tokens += delta_out
        entry.codex_state_total_tokens += delta_total
        self._totals.input_tokens += delta_in
        self._totals.cache_input_tokens += delta_cache
        self._totals.output_tokens += delta_out
        self._totals.total_tokens += delta_total
        return delta_total, delta_out

    # ------------------------------------------------------------------
    # run-stats recording (stats.jsonl — feeds the stats page / TUI screen)
    # ------------------------------------------------------------------

    def _record_stats_turn(self, entry: RunningEntry) -> None:
        if self._stats is None:
            return
        delta_in = entry.codex_input_tokens - entry.stats_input_tokens
        delta_cache = entry.codex_cache_input_tokens - entry.stats_cache_input_tokens
        delta_out = entry.codex_output_tokens - entry.stats_output_tokens
        delta_total = entry.codex_total_tokens - entry.stats_total_tokens
        entry.stats_input_tokens = entry.codex_input_tokens
        entry.stats_cache_input_tokens = entry.codex_cache_input_tokens
        entry.stats_output_tokens = entry.codex_output_tokens
        entry.stats_total_tokens = entry.codex_total_tokens
        self._stats.record_turn(
            issue=entry.issue.identifier,
            state=entry.state_at_turn_start or normalize_state(entry.issue.state),
            agent=self._entry_agent_kind(entry),
            input_tokens=max(delta_in, 0),
            cache_tokens=max(delta_cache, 0),
            output_tokens=max(delta_out, 0),
            total_tokens=max(delta_total, 0),
        )

    def _record_stats_transition(
        self, identifier: str, from_state: str, to_state: str
    ) -> None:
        if self._stats is None:
            return
        self._stats.record_transition(
            issue=identifier,
            from_state=normalize_state(from_state),
            to_state=normalize_state(to_state),
        )

    # ------------------------------------------------------------------
    # worker exit handling (§16.6)
    # ------------------------------------------------------------------

    async def _on_worker_exit(self, issue_id: str, reason: str, error: str | None) -> None:
        # Treat the whole exit handler as in-flight. From the moment a worker
        # leaves `_running` until its terminal-state persist (or retry enqueue)
        # finishes, the ticket must stay ineligible: the `await`s inside the
        # body (auto-commit, the async budget persist) each yield to a poll tick
        # that would otherwise prune the in-tick `_claimed` lock and re-dispatch
        # the still-active ticket. See docs/improvements/
        # dispatch-double-dispatch-race-2026-06-28.md.
        self._terminal_persist_pending.add(issue_id)
        try:
            await self._on_worker_exit_impl(issue_id, reason, error)
        finally:
            self._terminal_persist_pending.discard(issue_id)

    async def _on_worker_exit_impl(
        self, issue_id: str, reason: str, error: str | None
    ) -> None:
        # INFO-level entry marker — pairs with `worker_finally_entered`.
        # If `worker_finally_entered` is in the log but this is missing,
        # the outer finally's `await self._on_worker_exit(...)` was
        # cancelled before the coroutine body started executing.
        log.info(
            "worker_exit_entered",
            issue_id=issue_id,
            reason=reason,
            running_keys_before_pop=list(self._running.keys()),
        )
        entry = self._running.pop(issue_id, None)
        # G3 — clear any stale wait-age bonus once the worker exits. The
        # next entry into `_claimed` (conflict, budget, etc.) will record
        # a fresh release timestamp, so leaving the old one behind would
        # falsely promote the ticket on its next candidate-list appearance.
        self._claim_released_at.pop(issue_id, None)
        # The wakeup event is per-worker — pop it so a fresh worker (if
        # any) starts with a clean gate. `_paused_issue_ids` is per-issue
        # and is intentionally preserved: it's what lets `_eligible`
        # refuse to re-dispatch a ticket the operator chose to hold.
        pause_event = self._pause_events.pop(issue_id, None)
        if pause_event is not None and not pause_event.is_set():
            # Unblock anything still awaiting the event so the worker's
            # cancellation path can run to completion.
            pause_event.set()
        log.info(
            "worker_exit_pop",
            issue_id=issue_id,
            reason=reason,
            popped=entry is not None,
            running_keys_after_pop=list(self._running.keys()),
        )
        if entry is None:
            return
        self._finish_run_lease(issue_id, entry, reason)
        elapsed = (datetime.now(timezone.utc) - entry.started_at).total_seconds()
        self._totals.seconds_running += elapsed
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.last_workspace = entry.workspace_path
        debug.last_error = error
        debug.completed_turn_count += entry.turn_count
        if self._stats is not None:
            self._stats.record_run_end(
                issue=entry.issue.identifier,
                state=normalize_state(entry.issue.state),
                agent=self._entry_agent_kind(entry),
                outcome=reason,
                turns=entry.turn_count,
                seconds=elapsed,
            )

        if reason == "normal":
            cfg = self._workflow_state.current()
            self._persisted_retry_attempts.pop(issue_id, None)
            self._clear_issue_flags(issue_id, retry_attempt=True)
            if entry.hit_token_budget:
                if cfg is not None:
                    before_state = normalize_state(entry.issue.state)
                    refreshed = await self._refresh_issue_state(cfg, issue_id)
                    if refreshed is not None:
                        entry.issue = refreshed
                    after_state = normalize_state(entry.issue.state)
                    if refreshed is not None and after_state != before_state:
                        log.info(
                            "token_budget_stage_advanced",
                            issue_id=issue_id,
                            issue_identifier=entry.issue.identifier,
                            from_state=before_state,
                            to_state=after_state,
                        )
                    else:
                        self._mark_budget_exhausted(issue_id)
                        self._claimed.add(issue_id)
                        cap = entry.token_budget_cap or self._token_cap_for_entry(
                            cfg, entry
                        )
                        debug.last_error = (
                            f"max_total_tokens reached "
                            f"({entry.codex_state_total_tokens}/{cap} "
                            f"in {entry.issue.state}); "
                            f"state still {entry.issue.state}"
                        )
                        log.warning(
                            "worker_token_budget_exhausted",
                            issue_id=issue_id,
                            issue_identifier=entry.issue.identifier,
                            state_total_tokens=entry.codex_state_total_tokens,
                            total_tokens=entry.codex_total_tokens,
                            max_total_tokens=cap,
                            state=entry.issue.state,
                        )
                        await self._persist_budget_exhausted_state(
                            cfg=cfg,
                            entry=entry,
                            issue_id=issue_id,
                            target_state=cfg.agent.budget_exhausted_state,
                            budget_kind="tokens",
                        )
                        return
                else:
                    self._mark_budget_exhausted(issue_id)
                    self._claimed.add(issue_id)
                    debug.last_error = (
                        "max_total_tokens reached; workflow config unavailable"
                    )
                    return

            if entry.hit_no_stage_change:
                count = debug.state_turn_count
                state_name = entry.issue.state or debug.state_turn_state
                action = cfg.agent.no_stage_change_action if cfg is not None else "block"
                if cfg is not None and action != "block":
                    persisted = await self._persist_no_stage_change_handoff(
                        cfg=cfg,
                        entry=entry,
                        issue_id=issue_id,
                        target_state=action,
                        turn_count=count,
                        state_name=state_name,
                    )
                    if persisted:
                        entry.issue = replace(entry.issue, state=action)
                    debug.last_error = (
                        f"no stage change after {count} turns in {state_name}; "
                        f"moved to {action}"
                    )
                    return
                self._claimed.add(issue_id)
                target_state = (
                    cfg.agent.budget_exhausted_state if cfg is not None else ""
                )
                if cfg is not None and target_state:
                    state_turn_limit = self._max_state_turns_for_state(
                        cfg, state_name
                    )
                    persisted = await self._persist_budget_exhausted_state(
                        cfg=cfg,
                        entry=entry,
                        issue_id=issue_id,
                        target_state=target_state,
                        budget_kind="no_stage_change",
                        state_turn_limit=state_turn_limit,
                    )
                    if persisted:
                        entry.issue = replace(entry.issue, state=target_state)
                pause_reason = (
                    f"no stage change after {count} turns in {state_name} - "
                    "operator action required"
                )
                debug.last_error = pause_reason
                self._paused_issue_ids.add(issue_id)
                self._pause_reasons[issue_id] = pause_reason
                self._set_issue_flags(
                    issue_id,
                    paused=True,
                    pause_reason=pause_reason,
                )
                return

            max_total_turns = cfg.agent.max_total_turns if cfg is not None else 60
            if debug.completed_turn_count >= max_total_turns:
                self._mark_budget_exhausted(issue_id)
                self._claimed.add(issue_id)
                debug.last_error = (
                    f"max_total_turns reached "
                    f"({debug.completed_turn_count}/{max_total_turns})"
                )
                log.warning(
                    "worker_total_turn_budget_exhausted",
                    issue_id=issue_id,
                    issue_identifier=entry.issue.identifier,
                    total_turns=debug.completed_turn_count,
                    max_total_turns=max_total_turns,
                )
                # Persistence: in-memory `_turn_budget_exhausted` clears on
                # service restart, so without an explicit transition the
                # same ticket runs again next boot. When the operator opted
                # in via `agent.budget_exhausted_state`, write the new
                # state through the tracker so the decision survives
                # restart and reaches anyone reviewing the board.
                target_state = (
                    cfg.agent.budget_exhausted_state if cfg is not None else ""
                )
                if target_state and cfg is not None:
                    await self._persist_budget_exhausted_state(
                        cfg=cfg,
                        entry=entry,
                        issue_id=issue_id,
                        target_state=target_state,
                        budget_kind="turns",
                    )
                return
            self._completed.add(issue_id)
            cleanup_started = entry.workspace_cleanup_started
            if (
                cfg is not None
                and cfg.agent.auto_commit_on_done
                and not cleanup_started
            ):
                # Snapshot whatever the agent left in the worktree, even if
                # the ticket isn't strictly at Done. The worker stopped
                # cleanly (`reason == "normal"`); any subsequent reconcile or
                # operator cleanup would `git worktree remove --force` and
                # discard uncommitted work otherwise. Lenient — failures only
                # warn; a missed snapshot must not block the queue.
                await _pkg.commit_workspace_on_done(
                    entry.workspace_path,
                    identifier=entry.issue.identifier,
                    title=entry.issue.title,
                    exit_reason=reason,
                    state=entry.issue.state,
                )
            # When the worker ran the ticket all the way to Done, the
            # reconcile path that normally fires after_done/auto_merge/remove
            # will *not* fire here: this entry was just popped from
            # `_running` and `_reconcile_running` only iterates entries it
            # finds there. Run the same terminal-state post-processing
            # inline so a clean win produces the same artefacts as a
            # reconcile-driven termination.
            is_done = (entry.issue.state or "").strip().lower() == "done"
            terminal_states = (
                {normalize_state(s) for s in cfg.tracker.terminal_states}
                if cfg is not None
                else set()
            )
            is_terminal = normalize_state(entry.issue.state) in terminal_states
            if cleanup_started:
                pass
            elif is_done and cfg is not None and self._workspace_manager is not None:
                merge_ok = await self._auto_merge_done_gate_or_block(
                    cfg,
                    entry.issue,
                    entry.workspace_path,
                    debug_target=debug,
                )
                if merge_ok:
                    await self._after_done_then_remove_per_policy(
                        cfg,
                        entry.workspace_path,
                        identifier=entry.issue.identifier,
                        title=entry.issue.title,
                        debug_target=debug,
                    )
                    # C5 — count this Done and run wiki-sweep if the cadence
                    # configured by `wiki.sweep_every_n` is up. Failures are
                    # absorbed inside the helper so we never block the
                    # Done transition on a wiki housekeeping nudge.
                    self._maybe_run_wiki_sweep(
                        cfg, identifier=entry.issue.identifier
                    )
                # Don't schedule a continuation — a Done ticket has nothing
                # to continue. Skip straight to the worker_exit emit below.
            elif not is_terminal and not entry.hit_max_turns:
                self._schedule_retry(
                    issue_id,
                    identifier=entry.issue.identifier,
                    attempt=1,
                    delay_ms=CONTINUATION_RETRY_DELAY_MS,
                    error=None,
                    kind="continuation",
                )
            elif entry.hit_max_turns:
                # `max_turns` exhausted without a terminal transition: stop
                # auto-continuation and, when the workflow exposes a Blocked
                # terminal state, persist that state so the web/TUI boards do
                # not look idle while the ticket is actually operator-blocked.
                self._claimed.add(issue_id)
                attempt_cap = cfg.agent.max_turns if cfg is not None else 0
                target_state = (
                    _max_turns_exhausted_target_state(cfg) if cfg is not None else ""
                )
                persisted = False
                if cfg is not None and target_state:
                    persisted = await self._persist_budget_exhausted_state(
                        cfg=cfg,
                        entry=entry,
                        issue_id=issue_id,
                        target_state=target_state,
                        budget_kind="max_turns",
                    )
                    if persisted:
                        entry.issue = replace(entry.issue, state=target_state)
                suffix = (
                    f"; moved to {target_state}"
                    if persisted
                    else " — operator action required"
                )
                debug.last_error = f"max_turns reached ({attempt_cap}/attempt){suffix}"
        else:
            failure_reason = f"{reason}: {error}" if error else reason
            pause_reason = _worker_error_pause_reason(reason, error)
            debug.last_error = pause_reason
            self._paused_issue_ids.add(issue_id)
            self._pause_reasons[issue_id] = pause_reason
            self._set_issue_flags(
                issue_id,
                paused=True,
                pause_reason=pause_reason,
            )
            log.warning(
                "worker_error_auto_paused",
                issue_id=issue_id,
                issue_identifier=entry.issue.identifier,
                reason=reason,
                error=error,
                pause_reason=pause_reason,
            )
            next_attempt = (entry.retry_attempt or 0) + 1
            cfg = self._workflow_state.current()
            cap = cfg.agent.max_retry_backoff_ms if cfg is not None else 300_000
            delay_ms = min(RETRY_BASE_MS * (2 ** (next_attempt - 1)), cap)
            self._schedule_retry(
                issue_id,
                identifier=entry.issue.identifier,
                attempt=next_attempt,
                delay_ms=delay_ms,
                error=_clean_board_error_message(failure_reason),
                kind="retry",
            )
        log.info(
            "worker_exit",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
            reason=reason,
            error=error,
        )
        await self._notify_observers()

    def _force_eject_zombie(
        self, issue_id: str, entry: RunningEntry, cfg: ServiceConfig
    ) -> None:
        """Forcibly free a worker slot when cancellation didn't propagate.

        Pops the entry from `_running` / `_claimed` and queues a backoff
        retry. The original `worker_task` stays cancelled — if it ever
        unblocks, its `finally` chain hits `_on_worker_exit`, which is a
        no-op on a missing entry, so this is race-safe.
        """
        self._running.pop(issue_id, None)
        self._claimed.discard(issue_id)
        if entry.codex_app_server_pid is not None:
            killed = kill_process_group(entry.codex_app_server_pid)
            log.warning(
                "force_eject_killed_process_group",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                pid=entry.codex_app_server_pid,
                killed=killed,
            )
        self._finish_run_lease(issue_id, entry, "force_ejected_zombie")
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
        )
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.last_workspace = entry.workspace_path
        debug.last_error = "force_ejected_zombie"

    # ------------------------------------------------------------------
    # retry handling (§16.6)
    # ------------------------------------------------------------------

    def _schedule_retry(
        self,
        issue_id: str,
        *,
        identifier: str,
        attempt: int,
        delay_ms: int,
        error: str | None,
        kind: str | None = None,
    ) -> None:
        if self._loop is None:
            return
        # v0.6.7 — cap auto-retries scheduled after a failure (kind
        # other than "continuation"). When the cap is set (>0) and we
        # would otherwise schedule attempt N where N exceeds the cap,
        # escalate the ticket to a terminal state instead so the
        # operator gets a board-level signal rather than a silent
        # retry storm. Continuations (turn-to-turn / stage-to-stage
        # success rescheduling) are exempt — they aren't failure
        # retries and reset the attempt counter to 1.
        cfg = self._workflow_state.current()
        max_retries = cfg.agent.max_retries if cfg is not None else 0
        retry_kind = kind or ("continuation" if error is None else "retry")
        if (
            max_retries > 0
            and retry_kind != "continuation"
            and attempt > max_retries
        ):
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
            return
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
                kind=kind or ("continuation" if error is None else "retry"),
            ),
        )
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.current_retry_attempt = attempt
        debug.current_attempt_kind = self._retry[issue_id].kind
        if self._retry[issue_id].kind == "continuation":
            self._persisted_retry_attempts.pop(issue_id, None)
            self._clear_issue_flags(issue_id, retry_attempt=True)
        else:
            self._persisted_retry_attempts[issue_id] = attempt
            self._set_issue_flags(issue_id, retry_attempt=attempt)

    def _in_flight_ids(self) -> set[str]:
        """Issue ids the G1 claim-prune must treat as legitimately claimed."""
        return (
            self._dispatch_state.in_flight_ids()
            | self._terminal_persist_pending
            | set(self._pending_escalations)
        )

    async def _escalate_max_retries(
        self,
        *,
        issue_id: str,
        identifier: str,
        attempt: int,
        error: str | None,
    ) -> None:
        """Move a ticket whose retry budget is exhausted to a terminal state.

        Surfaces a board-level ``## Escalation`` note and updates the
        tracker state to ``Blocked`` (or whichever configured terminal
        state mentions ``block``/``human``). The ticket no longer cycles
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
        target_state = ""
        for terminal in cfg.tracker.terminal_states:
            if "block" in terminal.lower() or "human" in terminal.lower():
                target_state = terminal
                break
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

    async def _on_retry_timer(self, issue_id: str) -> None:
        retry = self._retry.pop(issue_id, None)
        if retry is None:
            return
        # Paused tickets re-park the retry on a fixed short hold without
        # consuming a retry attempt. `resume_worker` cancels the timer
        # and re-fires this coroutine, so unpause is immediate. Without
        # this we'd reach `_eligible` → "not eligible at retry time" and
        # silently burn through the backoff schedule.
        if issue_id in self._paused_issue_ids:
            paused_error = (
                self._pause_reasons.get(issue_id)
                or retry.error
                or "paused"
            )
            self._schedule_retry(
                issue_id,
                identifier=retry.identifier,
                attempt=retry.attempt,
                delay_ms=PAUSED_RETRY_HOLD_MS,
                error=paused_error,
                kind=retry.kind,
            )
            return
        cfg = self._workflow_state.current()
        if cfg is None:
            self._claimed.discard(issue_id)
            self._paused_issue_ids.discard(issue_id)
            self._pause_reasons.pop(issue_id, None)
            self._persisted_retry_attempts.pop(issue_id, None)
            self._clear_issue_flags(issue_id, retry_attempt=True, paused=True)
            return
        try:
            candidates = await self._fetch_candidates(cfg)
        except Exception as exc:
            self._schedule_retry(
                issue_id,
                identifier=retry.identifier,
                attempt=retry.attempt + 1,
                delay_ms=min(
                    RETRY_BASE_MS * (2 ** retry.attempt), cfg.agent.max_retry_backoff_ms
                ),
                error=f"retry poll failed: {exc}",
            )
            return
        match = next((i for i in candidates if i.id == issue_id), None)
        if match is None:
            self._claimed.discard(issue_id)
            # Ticket left the orchestrator's view (terminal, archived,
            # filtered out by workflow change); drop any pause flag so
            # we don't leak it across the resurrection of the same id.
            self._paused_issue_ids.discard(issue_id)
            self._pause_reasons.pop(issue_id, None)
            self._persisted_retry_attempts.pop(issue_id, None)
            self._clear_issue_flags(issue_id, retry_attempt=True, paused=True)
            log.info("retry_release", issue_id=issue_id, identifier=retry.identifier)
            return
        if not self._eligible(match, cfg, owning_retry=True):
            self._schedule_retry(
                issue_id,
                identifier=match.identifier,
                attempt=retry.attempt + 1,
                delay_ms=min(
                    RETRY_BASE_MS * (2 ** retry.attempt), cfg.agent.max_retry_backoff_ms
                ),
                error="not eligible at retry time",
            )
            return
        if self._available_slots(cfg) == 0:
            self._schedule_retry(
                issue_id,
                identifier=match.identifier,
                attempt=retry.attempt + 1,
                delay_ms=min(
                    RETRY_BASE_MS * (2 ** retry.attempt), cfg.agent.max_retry_backoff_ms
                ),
                error="no available orchestrator slots",
            )
            return
        self._dispatch(match, cfg, attempt=retry.attempt, attempt_kind=retry.kind)

    # ------------------------------------------------------------------
    # reconciliation (§16.3)
    # ------------------------------------------------------------------

    async def _reconcile_running(self, cfg: ServiceConfig) -> None:
        # Part A: stall detection + force-eject of zombie workers.
        _, _, stall_timeout_ms = cfg.backend_timeouts()
        for issue_id, entry in list(self._running.items()):
            self._heartbeat_run_lease(issue_id, entry)
        if stall_timeout_ms > 0:
            now = datetime.now(timezone.utc)
            for issue_id, entry in list(self._running.items()):
                # Paused workers are intentionally idle — operator chose to
                # hold them. Treating that idleness as a stall would defeat
                # the whole feature: a pause would trip cancel + force-eject
                # within the stall window. Skip stall checks while paused;
                # the moment the operator resumes, the next turn re-enters
                # the normal progress-timestamp loop.
                if self.is_paused(issue_id):
                    continue
                # Worker that already received a stall-cancel: if it didn't
                # exit within the grace window, force-eject so its slot
                # doesn't leak. The cancel is still in flight; if the worker
                # eventually wakes, `_on_worker_exit` no-ops on a missing
                # entry.
                if entry.cancelled_at is not None:
                    since_cancel = (now - entry.cancelled_at).total_seconds()
                    if since_cancel > STALL_FORCE_EJECT_GRACE_S:
                        log.error(
                            "stalled_worker_force_ejected",
                            issue_id=issue_id,
                            identifier=entry.issue.identifier,
                            elapsed_since_cancel_s=round(since_cancel, 1),
                        )
                        self._force_eject_zombie(issue_id, entry, cfg)
                    continue
                # Use last_progress_timestamp (real model/lifecycle activity)
                # rather than last_codex_timestamp (any byte from the backend),
                # so claude API tool_result echoes / stream keepalive don't
                # keep resetting the stall clock. Until real progress exists,
                # measure from started_at; backend keepalives are UI activity,
                # not proof that the turn is advancing.
                seen = entry.last_progress_timestamp or entry.started_at
                elapsed_ms = (now - seen).total_seconds() * 1000
                if elapsed_ms > stall_timeout_ms:
                    log.warning(
                        "stalled_session",
                        issue_id=issue_id,
                        identifier=entry.issue.identifier,
                        elapsed_ms=int(elapsed_ms),
                    )
                    if entry.worker_task is not None:
                        entry.worker_task.cancel()
                    entry.cancelled_at = now
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
        self,
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
        if state in terminal:
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
            last_seen = entry.last_codex_timestamp
            age = (now - last_seen).total_seconds() if last_seen else None
            if age is not None and age < recent_grace_s:
                # Active worker — let it exit on its own.
                log.info(
                    "reconcile_skip_active_worker",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    state=issue.state,
                    last_event_age_s=round(age, 1),
                )
                return
            if entry.exit_started_at is not None:
                log.info(
                    "reconcile_skip_exiting_worker",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    state=issue.state,
                    exit_started_at=entry.exit_started_at.isoformat(),
                )
                return
            log.info(
                "reconcile_terminate_terminal",
                issue_id=issue.id,
                identifier=issue.identifier,
                state=issue.state,
                last_event_age_s=round(age, 1) if age is not None else None,
            )
            if entry.worker_task is not None:
                entry.worker_task.cancel()
            if self._workspace_manager is not None:
                entry.workspace_cleanup_started = True
                if cfg.agent.auto_commit_on_done:
                    # Snapshot before remove — `git worktree remove
                    # --force` would otherwise discard whatever the
                    # agent left uncommitted in the worktree.
                    await _pkg.commit_workspace_on_done(
                        entry.workspace_path,
                        identifier=entry.issue.identifier,
                        title=entry.issue.title,
                        exit_reason="reconcile_terminate_terminal",
                        state=issue.state,
                    )
                if (issue.state or "").strip().lower() == "done":
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
                        self._maybe_run_wiki_sweep(
                            cfg, identifier=entry.issue.identifier
                        )
                else:
                    # Non-Done terminal state (e.g. Cancelled, Blocked):
                    # no after_done hook, just reap the workspace.
                    await self._workspace_manager.remove(entry.workspace_path)
        elif state in active:
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
                if cfg.agent.auto_commit_on_done:
                    await _pkg.commit_workspace_on_done(
                        entry.workspace_path,
                        identifier=entry.issue.identifier,
                        title=entry.issue.title,
                        exit_reason="reconcile_terminate_inactive",
                        state=issue.state,
                    )
                await self._workspace_manager.remove(entry.workspace_path)

    # ------------------------------------------------------------------
    # tracker access
    # ------------------------------------------------------------------

    async def _fetch_candidates(self, cfg: ServiceConfig) -> list[Issue]:
        return await asyncio.to_thread(self._tracker_call_candidates, cfg)

    def _record_tracker_error(self, issue_id: str, exc: Exception | str) -> None:
        message = str(exc) or type(exc).__name__
        message = " ".join(message.split())
        if len(message) > 500:
            message = message[-500:]
        self._issue_debug.setdefault(issue_id, _IssueDebug()).tracker_error = message

    def _clear_tracker_error(self, issue_id: str) -> None:
        debug = self._issue_debug.get(issue_id)
        if debug is not None:
            debug.tracker_error = None

    @staticmethod
    def _tracker_call_candidates(cfg: ServiceConfig) -> list[Issue]:
        client = build_tracker_client(cfg)
        try:
            return client.fetch_candidate_issues()
        finally:
            client.close()

    @staticmethod
    def _tracker_call_states_by_ids(cfg: ServiceConfig, ids: list[str]) -> list[Issue]:
        client = build_tracker_client(cfg)
        try:
            return client.fetch_issue_states_by_ids(ids)
        finally:
            client.close()

    @staticmethod
    def _tracker_call_full_by_id(
        cfg: ServiceConfig, issue_id: str
    ) -> Issue | None:
        """Single-issue fetch with full body — used by contract validation."""
        client = build_tracker_client(cfg)
        try:
            return client.fetch_issue_full_by_id(issue_id)
        finally:
            client.close()

    @staticmethod
    def _tracker_call_terminal_issues(cfg: ServiceConfig) -> list[Issue]:
        client = build_tracker_client(cfg)
        try:
            return client.fetch_issues_by_states(cfg.tracker.terminal_states)
        finally:
            client.close()

    @staticmethod
    def _tracker_call_record_agent_kind(
        cfg: ServiceConfig, identifier: str, agent_kind: str
    ) -> None:
        """Best-effort: persist the resolved backend onto the ticket.

        Adapters that don't implement ``record_agent_kind`` (e.g. Linear,
        where the field has no remote analogue) are silently skipped.
        """
        client = build_tracker_client(cfg)
        try:
            record = getattr(client, "record_agent_kind", None)
            if record is None:
                return
            record(identifier, agent_kind)
        finally:
            client.close()

    # ------------------------------------------------------------------
    # startup cleanup (§8.6)
    # ------------------------------------------------------------------

    async def _startup_terminal_cleanup(self, cfg: ServiceConfig) -> None:
        try:
            terminals = await asyncio.to_thread(self._tracker_call_terminal_issues, cfg)
        except Exception as exc:
            log.warning("startup_terminal_fetch_failed", error=str(exc))
            return
        if self._workspace_manager is None:
            return
        for issue in terminals:
            path = self._workspace_manager.path_for(issue.identifier)
            if path.exists():
                is_done = (issue.state or "").strip().lower() == "done"
                if is_done:
                    branch = f"symphony/{issue.identifier}"
                    already_merged = False
                    if cfg.agent.auto_merge_on_done:
                        already_merged = await _branch_already_merged_into_target(
                            cfg.workflow_path.parent,
                            branch=branch,
                            target_branch=cfg.agent.auto_merge_target_branch,
                        )
                    if already_merged:
                        log.info(
                            "startup_terminal_cleanup_skipped_already_merged",
                            identifier=issue.identifier,
                            branch=branch,
                            target=cfg.agent.auto_merge_target_branch or "HEAD",
                            path=str(path),
                        )
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
                    await _pkg.commit_workspace_on_done(
                        path,
                        identifier=issue.identifier,
                        title=issue.title,
                        exit_reason="startup_terminal_cleanup",
                        state=issue.state,
                    )
                await self._workspace_manager.remove(path)
