"""SPEC §7, §8, §16 — Orchestrator class (state machine + worker driver).

The orchestrator is the single authority for scheduling state. All worker
outcomes are reported back through asyncio queues and converted into
explicit state transitions (§7.0).

Concurrency model:
- One asyncio event loop owns mutation of `running`, `claimed`, and
  `retry_attempts`. Workers run as tasks; tracker calls run in a thread
  executor; codex events arrive via async callbacks routed through a queue.

Collaborators — ``build_backend``, ``commit_workspace_on_done``, and
``auto_merge_on_done_best_effort`` — are imported directly and called
through this module's globals, so tests patch
``symphony.orchestrator.core.<name>`` (the consumer's reference);
``build_backend`` can also be constructor-injected.

Layout (LR-3 lean-core split): this module keeps construction, the
dispatch-state views, supervised background tasks, start/stop, observers,
and the tick loop. The remaining methods live as module-level functions in
``run_leases``, ``release_seam``, ``api_views``, ``sweeps``,
``blocked_recovery``, ``tracker_bridge``, ``dispatch``, ``agent_phase``, and
``retry_reconcile`` and are bound onto ``Orchestrator`` as class attributes
at the end of the class body. Those modules read monkeypatch seams through
``_core()`` at call time, so ``symphony.orchestrator.core.<name>`` stays the
patch target for the names re-exported below.
"""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine

from ..artifacts import ArtifactStore
from ..backends import AgentBackend, BackendInit, build_backend
from ..errors import SymphonyError
from ..continuous_improvement import (
    ImprovementRunner,
    Lease,
    default_improvement_runner,
)
from ..issue import Issue
from ..logging import get_logger
from ..runtime_safety import ensure_workflow_repo_is_safe
from ..service_identity import SERVICE_INSTANCE_ENV, normalize_service_instance_id
from ..stats import StatsStore, stats_store_for
from ..trackers import TrackerClient
from ..workflow import ServiceConfig, WorkflowState, validate_for_dispatch
from ..workspace import WorkspaceManager
from .constants import (
    STALL_FORCE_EJECT_GRACE_S,
    STOP_BACKGROUND_TASKS_TIMEOUT_S,
    TICK_FAILURE_BACKOFF_MAX_S,
    TICK_LOOP_MAX_RESTARTS,
)
from .dispatch_state import DispatchState
from .entries import RetryEntry, RunningEntry, _CodexTotals, _IssueDebug
from .helpers import _branch_hook_env, resolve_symphony_cli
from .run_registry import RunRegistry
from .core_support import _normalize_agent_pid
from . import (
    agent_phase,
    api_views,
    blocked_recovery,
    dispatch,
    release_seam,
    retry_reconcile,
    run_leases,
    sweeps,
    tracker_bridge,
)

# Names that tests monkeypatch as ``symphony.orchestrator.core.<name>``, that
# the extracted method modules read through ``_core()`` at call time, or that
# callers import from here. The redundant ``x as x`` form marks the re-export.
import time as time
from .._shell import (
    kill_process_group as kill_process_group,
    process_group_exists as process_group_exists,
    process_identity as process_identity,
)
from ..trackers import (
    build_tracker_client as build_tracker_client,
)
from ..utils.auto_merge import (
    auto_merge_on_done_best_effort as auto_merge_on_done_best_effort,
)
from ..workspace import (
    commit_workspace_on_done as commit_workspace_on_done,
    finalize_delivery_history as finalize_delivery_history,
    verify_branch_history as verify_branch_history,
)
from .helpers import (
    _branch_already_merged_into_target as _branch_already_merged_into_target,
    _notify_state_transition as _notify_state_transition,
)
from .scheduler import (
    MAX_DEPENDENCY_NODES as MAX_DEPENDENCY_NODES,
    analyze_dependencies as analyze_dependencies,
)
from .sweeps import (
    CI_AGENT_OUTPUT_PREFIX as CI_AGENT_OUTPUT_PREFIX,
)
from .core_support import (
    _ANSI_ESCAPE_RE as _ANSI_ESCAPE_RE,
    _BLOCKED_RCA_HEADING_RE as _BLOCKED_RCA_HEADING_RE,
    _BLOCKED_RCA_RESOLVED_HEADING_RE as _BLOCKED_RCA_RESOLVED_HEADING_RE,
    _CONTROL_CHAR_RE as _CONTROL_CHAR_RE,
    _EligibilityDecision as _EligibilityDecision,
    _EligibilityDisposition as _EligibilityDisposition,
    _FAILED_BLOCKER_TERMINAL_STATES as _FAILED_BLOCKER_TERMINAL_STATES,
    _QUOTA_WORKER_ERROR_MARKERS as _QUOTA_WORKER_ERROR_MARKERS,
    _REOPEN_APPROVED_RE as _REOPEN_APPROVED_RE,
    _RETRYABLE_WORKER_ERROR_MARKERS as _RETRYABLE_WORKER_ERROR_MARKERS,
    _attention_signal as _attention_signal,
    _blocked_rca_already_requested as _blocked_rca_already_requested,
    _blocker_dependency_is_resolved as _blocker_dependency_is_resolved,
    _clean_board_error_message as _clean_board_error_message,
    _has_app_release_label as _has_app_release_label,
    _has_retryable_worker_marker as _has_retryable_worker_marker,
    _is_opencode_sigterm_retry as _is_opencode_sigterm_retry,
    _is_quota_worker_error as _is_quota_worker_error,
    _is_retryable_worker_error as _is_retryable_worker_error,
    _successful_blocker_terminal_states as _successful_blocker_terminal_states,
    _update_state_turn_counter as _update_state_turn_counter,
    _worker_error_pause_reason as _worker_error_pause_reason,
    _worktree_status_snapshot as _worktree_status_snapshot,
)
from .release_seam import (
    _ReleaseDispatchAuthority as _ReleaseDispatchAuthority,
)
from .agent_phase import (
    _AgentPhaseState as _AgentPhaseState,
    _AgentPhaseTransition as _AgentPhaseTransition,
    _BOARD_HEALTH_LANES as _BOARD_HEALTH_LANES,
    _StageContractOutcome as _StageContractOutcome,
    _backend_agent_pid as _backend_agent_pid,
)
from .blocked_recovery import (
    _BLOCKED_RCA_HOST_RESOLVED_HEADING_RE as _BLOCKED_RCA_HOST_RESOLVED_HEADING_RE,
    _BLOCKED_RCA_OPERATOR_BLOCKER_RE as _BLOCKED_RCA_OPERATOR_BLOCKER_RE,
    _BLOCKED_RCA_SOURCE_IDENTIFIER_RE as _BLOCKED_RCA_SOURCE_IDENTIFIER_RE,
    _BLOCKED_RCA_TITLE_RE as _BLOCKED_RCA_TITLE_RE,
    _HISTORY_FAILURE_HEADING_RE as _HISTORY_FAILURE_HEADING_RE,
    _HISTORY_RECOVERY_HEADING_RE as _HISTORY_RECOVERY_HEADING_RE,
    _HUMAN_REVIEW_COMPLETION_RE as _HUMAN_REVIEW_COMPLETION_RE,
    _HUMAN_REVIEW_CONFIRM_DONE_RE as _HUMAN_REVIEW_CONFIRM_DONE_RE,
    _HUMAN_REVIEW_DO_NOT_CONFIRM_RE as _HUMAN_REVIEW_DO_NOT_CONFIRM_RE,
    _HUMAN_REVIEW_INTERVENTION_RE as _HUMAN_REVIEW_INTERVENTION_RE,
    _HUMAN_REVIEW_MERGE_FAILURE_RE as _HUMAN_REVIEW_MERGE_FAILURE_RE,
    _NEXT_H2_RE as _NEXT_H2_RE,
    _blocked_rca_current_episode_resolved as _blocked_rca_current_episode_resolved,
    _blocked_rca_description as _blocked_rca_description,
    _blocked_rca_identifier_prefix as _blocked_rca_identifier_prefix,
    _blocked_rca_labels as _blocked_rca_labels,
    _blocked_rca_requires_operator_intervention as _blocked_rca_requires_operator_intervention,
    _blocked_rca_source_identifier as _blocked_rca_source_identifier,
    _blocked_rca_work_state as _blocked_rca_work_state,
    _blocked_source_reopen_state as _blocked_source_reopen_state,
    _human_review_done_state as _human_review_done_state,
    _human_review_requires_operator_intervention as _human_review_requires_operator_intervention,
    _is_blocked_fix_ticket as _is_blocked_fix_ticket,
    _is_blocked_rca_ticket as _is_blocked_rca_ticket,
    _legacy_human_review_is_done as _legacy_human_review_is_done,
    _looks_like_blocked_rca_ticket as _looks_like_blocked_rca_ticket,
    _markdown_section as _markdown_section,
)
from .tracker_bridge import (
    _HTTPX_CLIENT_CLOSED_MARKERS as _HTTPX_CLIENT_CLOSED_MARKERS,
    _T as _T,
    _TrackerClientUnavailable as _TrackerClientUnavailable,
    _is_closed_client_runtime_error as _is_closed_client_runtime_error,
)
from .run_leases import (
    _RunLeaseAcquisition as _RunLeaseAcquisition,
    _is_retryable_auto_pause_reason as _is_retryable_auto_pause_reason,
    _reclaim_kill_confirm as _reclaim_kill_confirm,
    _run_record_payload as _run_record_payload,
)


# Initiative D — the former ``_pkg.<name>`` parent-package indirection is
# gone. Collaborators (``build_backend``, ``commit_workspace_on_done``,
# ``auto_merge_on_done_best_effort``) are imported directly and looked up
# from this module's globals at call time, so tests patch
# ``symphony.orchestrator.core.<name>`` — the consumer's reference.
# ``build_backend`` is additionally constructor-injectable.

log = get_logger()

DEFAULT_IMPROVEMENT_RUN_TIMEOUT_S = 3600.0


def _initial_improvement_status() -> dict[str, Any]:
    """Runtime half of the heartbeat status. Config-derived fields
    (enabled/interval_ms/max_turns/agent_kind) are refreshed each tick from
    the live snapshot; here we seed neutral defaults so the web API can read
    a status even before the first tick.
    """
    return {
        "enabled": False,
        "interval_ms": 0,
        "max_turns": 0,
        "agent_kind": "",
        "modes": [],
        "in_flight": False,
        "current_phase": None,
        "last_started_at": None,
        "last_finished_at": None,
        "last_result": None,
        "last_error": None,
        "tickets_created": 0,
        "skipped_reason": None,
        "last_verified_branch": None,
        "last_verified_sha": None,
        "last_mode_results": [],
        "last_request_id": None,
    }


class Orchestrator:
    def __init__(
        self,
        workflow_state: WorkflowState,
        *,
        build_backend: Callable[[BackendInit], AgentBackend] | None = None,
        improvement_runner: ImprovementRunner | None = None,
        improvement_lease: Lease | None = None,
    ) -> None:
        self._service_instance_id = normalize_service_instance_id(
            os.environ.pop(SERVICE_INSTANCE_ENV, None)
        )
        self._workflow_state = workflow_state
        # Initiative D — backend factory via constructor injection. None
        # falls back to the module-level `build_backend`, looked up at call
        # time in `_build_agent_backend` so tests may also monkeypatch
        # `symphony.orchestrator.core.build_backend`.
        self._build_backend_override = build_backend
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
        self._needs_sweep_task: asyncio.Task[None] | None = None
        self._lease_blocked: dict[str, str] = {}
        self._blocked_rca_source_ids: set[str] = set()
        # Serialize the board check + create sequence.  The in-memory source
        # set is only a cache; concurrent manual/automatic recovery requests
        # must re-check the persisted board before either creates an RCA.
        self._blocked_rca_creation_lock = asyncio.Lock()
        self._manual_completion_lock = asyncio.Lock()
        # A worker's post-turn path and the reconciliation loop can observe
        # the same Verify transition before either has finished persisting
        # release authority. Keep one lock per live RunningEntry so the
        # validator, GREEN CAS, and RED repair lifecycle are one serialized
        # host decision for that verifier run.
        self._app_release_transition_locks: dict[
            str, tuple[RunningEntry, asyncio.Lock]
        ] = {}
        # Tickets the host already re-checked for a sandbox-denied history
        # write. Bounds the extra description fetch to one per ticket per
        # process instead of one per sweep.
        self._history_recovery_attempted: set[str] = set()
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
        self._schedule_snapshot: dict[str, Any] = {
            "schema_version": 1,
            "available": False,
            "reason": "not_evaluated",
            "entries": [],
        }
        self._totals = _CodexTotals()
        self._latest_rate_limits: dict[str, Any] | None = None
        self._issue_debug: dict[str, _IssueDebug] = {}
        self._workspace_manager: WorkspaceManager | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._tick_event = asyncio.Event()
        self._stopping = False
        # One cached tracker client per orchestrator lifetime for the
        # `_tracker_call_*` bridge helpers below — the Jira/Linear adapters
        # wrap an httpx connection pool per client, so building one per
        # call threw away connection reuse on every poll tick. Built
        # lazily on first use, closed in `stop()`. Guarded by a lock
        # because the bridges run on `asyncio.to_thread` worker threads.
        # The lock is reentrant: checkout delegates may re-enter guarded
        # helpers on the same thread.
        self._tracker_client: TrackerClient | None = None
        self._tracker_client_cfg: ServiceConfig | None = None
        self._tracker_client_closed = False
        # `closing` covers the window while stop() is closing the pool:
        # new checkouts fail fast instead of rebuilding into a pool that
        # is about to disappear (or leaking a fresh client past stop()).
        self._tracker_client_closing = False
        self._tracker_client_close_race_logged = False
        self._tracker_client_lock = threading.RLock()
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
        # Ticket artifact store (`.symphony/artifacts/`). Bound in start()
        # alongside `self._stats`; stays None when `artifacts.enabled: false`.
        self._artifact_store: ArtifactStore | None = None
        self._last_artifact_sweep_monotonic: float | None = None
        self._run_registry: RunRegistry | None = None
        self._run_registry_initialized = False
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

        # Continuous-improvement heartbeat (plan §4). Default-off, config
        # re-read per tick from the workflow snapshot. Its own asyncio task
        # — never a worker slot. Due-math uses the monotonic clock so a
        # wall-clock jump can't wedge it. Runner + lease are injectable so
        # tests never spawn real subprocesses or touch a shared lockfile.
        # Agent-driven improvement modes need a real backend turn. The CI
        # module must stay orchestrator-free, so the capability is injected:
        # binding it as a keyword partial keeps the 3-positional
        # `ImprovementRunner` signature every injected test fake implements.
        self._improvement_runner: ImprovementRunner = improvement_runner or partial(
            default_improvement_runner,
            agent_runner=self._run_improvement_agent,
        )
        self._improvement_lease = improvement_lease
        self._improvement_task: asyncio.Task[None] | None = None
        self._improvement_run_timeout_s: float = DEFAULT_IMPROVEMENT_RUN_TIMEOUT_S
        self._last_improvement_monotonic: float | None = None
        self._next_improvement_due_monotonic: float | None = None
        self._improvement_turns_used: int = 0
        self._improvement_cap_warned: bool = False
        self._improvement_status: dict[str, Any] = _initial_improvement_status()

    def _pm_process_launch_guard(
        self, running_issue_id: str, issue: Issue, cfg: ServiceConfig,
        workspace_path: Path | None = None,
    ):
        from ..backends import noop_process_launch_guard

        running = self._running.get(running_issue_id)
        permit = running.pm_dispatch_permit if running is not None else None
        registry = self._run_registry
        if permit is None or registry is None:
            return noop_process_launch_guard
        assert running is not None
        # Only a PM dispatch permit reaches here, so the optional PM package is installed.
        from ..pm.authority import PmAuthority  # pyright: ignore[reportMissingImports]

        authority = PmAuthority(
            registry, cfg.workflow_path.parent / ".symphony" / "pm-locks",
            successful_states={
                state for state in cfg.tracker.terminal_states
                if state.casefold() not in {"cancelled", "blocked", cfg.tracker.archive_state.casefold()}
            },
        )

        async def load_issue() -> Issue | None:
            return await asyncio.to_thread(
                self._tracker_call_fetch_issue_full_by_id,
                cfg,
                issue.identifier,
            )

        return authority.launch_guard(
            permit, load_issue, running.run_id, workspace_path=workspace_path
        )

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
    def _persisted_retry_attempts(self) -> dict[str, int]:
        return self._dispatch_state.persisted_retry_attempts

    @property
    def _turn_budget_exhausted(self) -> set[str]:
        return self._dispatch_state.turn_budget_exhausted

    def _build_agent_backend(self, init: BackendInit) -> AgentBackend:
        """Resolve the backend factory: injected > module global (patchable)."""
        factory = self._build_backend_override
        if factory is None:
            factory = build_backend
        return factory(init)

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

    async def _drain_worker_tasks(self) -> None:
        """Wait one eject-grace window, then abandon cancellation resisters."""
        workers = {
            issue_id: entry
            for issue_id, entry in self._running.items()
            if entry.worker_task is not None
        }
        if not workers:
            return
        done, pending = await asyncio.wait(
            [entry.worker_task for entry in workers.values() if entry.worker_task],
            timeout=STALL_FORCE_EJECT_GRACE_S,
        )
        for task in done:
            try:
                task.result()
            except (asyncio.CancelledError, Exception):
                pass
        for issue_id, entry in workers.items():
            task = entry.worker_task
            if task not in pending:
                continue
            cleanup_confirmed = await self._confirm_entry_backend_cleanup_async(
                issue_id, entry
            )
            if entry.run_id and self._run_registry is not None:
                entry.backend_cleanup_unconfirmed = not cleanup_confirmed
            agent_pgid = _normalize_agent_pid(entry.agent_pgid)
            log.warning(
                "worker_task_abandoned_on_stop",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                agent_kind=self._entry_agent_kind(entry),
                pid=agent_pgid,
                cleanup_confirmed=cleanup_confirmed,
            )
            self._finish_run_lease(issue_id, entry, "shutdown_abandoned")

    # ------------------------------------------------------------------
    # public accessors for API / TUI layers
    # ------------------------------------------------------------------

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    @property
    def service_instance_id(self) -> str | None:
        """Per-launch managed-service capability, absent for foreground runs."""
        return self._service_instance_id

    @property
    def stats(self) -> StatsStore | None:
        return self._stats

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
        ensure_workflow_repo_is_safe(cfg.workflow_path)
        validate_for_dispatch(cfg)
        # Surface the workflow dir to every subprocess spawned afterwards
        # (hooks and agent backends inherit via os.environ). WORKFLOW.md
        # authors can then reference it from `claude.command` etc., e.g.
        # `--add-dir "$SYMPHONY_WORKFLOW_DIR/kanban"` so Claude Code accepts
        # writes through the host-board junction installed by after_create.
        os.environ["SYMPHONY_WORKFLOW_DIR"] = str(cfg.workflow_path.parent)
        # The board-tool protocol in the stage prompts and the chat preamble
        # requires the `symphony` CLI; a venv install is not necessarily on
        # the worker's PATH. Prompts reference `${SYMPHONY_CLI:-symphony}`.
        os.environ["SYMPHONY_CLI"] = resolve_symphony_cli()
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
        if cfg.artifacts.enabled:
            self._artifact_store = ArtifactStore(
                cfg.workflow_path.parent / ".symphony" / "artifacts",
                max_file_bytes=cfg.artifacts.max_file_mb * 1024 * 1024,
                max_ticket_bytes=cfg.artifacts.max_ticket_mb * 1024 * 1024,
            )
            self._ensure_artifact_dir_git_excluded(cfg)
        await self._ensure_run_registry_async(cfg)
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
        await self._drain_worker_tasks()
        # Worker exits above may have fired supervised cleanup tasks
        # (lease release, registry writes). Let them land before closing
        # the registry; cancel anything still stuck after the bound.
        await self._drain_background_tasks()
        self._running.clear()
        self._app_release_transition_locks.clear()
        self._retry.clear()
        self._paused_issue_ids.clear()
        self._pause_reasons.clear()
        self._pause_events.clear()
        self._turn_budget_exhausted.clear()
        self._lease_blocked.clear()
        self._blocked_rca_source_ids.clear()
        self._history_recovery_attempted.clear()
        self._issue_debug.clear()
        if self._run_registry is not None:
            self._run_registry.close()
            self._run_registry = None
        # Close the per-lifetime tracker client AFTER the drains above so
        # in-flight `asyncio.to_thread` bridges finish first. Bridges that
        # still slip through afterwards raise a clean retryable
        # `TrackerClientUnavailable` (see `_invoke_shared_tracker_client`)
        # instead of touching the closed pool or rebuilding past shutdown.
        with self._tracker_client_lock:
            # New checkouts must fail fast while the pool is closing; a
            # rebuild here would race the close and leak a fresh client.
            self._tracker_client_closing = True
            if self._tracker_client is not None:
                self._tracker_client.close()
                self._tracker_client = None
                self._tracker_client_cfg = None
                self._tracker_client_closed = True

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
                await asyncio.wait_for(
                    self._tick_event.wait(), timeout=poll_ms / 1000.0
                )
            except asyncio.TimeoutError:
                pass
            self._tick_event.clear()
            self._refresh_pending = False

    # -- run_leases.py --------------------------------------------------
    _registry_guard = run_leases._registry_guard
    _open_run_registry = run_leases._open_run_registry
    _finish_run_registry_open = run_leases._finish_run_registry_open
    _ensure_run_registry = run_leases._ensure_run_registry
    _ensure_run_registry_async = run_leases._ensure_run_registry_async
    _ensure_run_registry_open_only = run_leases._ensure_run_registry_open_only
    _reclaim_dead_owner_runs = run_leases._reclaim_dead_owner_runs
    _reclaim_dead_owner_runs_async = run_leases._reclaim_dead_owner_runs_async
    _reap_and_finalize_reclaimed_run = run_leases._reap_and_finalize_reclaimed_run
    _reap_and_finalize_reclaimed_run_async = run_leases._reap_and_finalize_reclaimed_run_async
    recent_runs = run_leases.recent_runs
    run_detail = run_leases.run_detail
    run_diagnostic = run_leases.run_diagnostic
    _append_run_event = run_leases._append_run_event
    _rehydrate_issue_flags = run_leases._rehydrate_issue_flags
    _set_issue_flags = run_leases._set_issue_flags
    _clear_issue_flags = run_leases._clear_issue_flags
    _mark_budget_exhausted = run_leases._mark_budget_exhausted
    _has_active_run_lease = run_leases._has_active_run_lease
    _heartbeat_run_lease = run_leases._heartbeat_run_lease
    _agent_pid_identity = run_leases._agent_pid_identity
    _sync_backend_agent_pid = run_leases._sync_backend_agent_pid
    _heartbeat_running_leases = run_leases._heartbeat_running_leases
    _finish_run_lease = run_leases._finish_run_lease
    _entry_backend_cleanup_record = run_leases._entry_backend_cleanup_record
    _confirm_entry_backend_cleanup = run_leases._confirm_entry_backend_cleanup
    _confirm_entry_backend_cleanup_async = run_leases._confirm_entry_backend_cleanup_async
    _try_acquire_run_lease = run_leases._try_acquire_run_lease

    # -- release_seam.py (optional app release contracts) ----------------
    # Each `authority_method` delegate runs `symphony.release.authority` when
    # the package is installed and fails closed without it.
    _release_registry_required = release_seam._release_registry_required
    _release_registry_call = release_seam._release_registry_call
    _hold_release_ticket_without_component = release_seam._hold_release_ticket_without_component
    _pending_release_gate = staticmethod(release_seam.authority_method("_pending_release_gate"))
    _persist_pending_release_gate = release_seam.authority_method("_persist_pending_release_gate")
    _create_initial_release_gate = release_seam.authority_method("_create_initial_release_gate")
    _reopen_stale_release_gate = release_seam.authority_method("_reopen_stale_release_gate")
    _guard_release_finalizer = release_seam.authority_method("_guard_release_finalizer")
    _mark_release_finalizer_completed = release_seam.authority_method("_mark_release_finalizer_completed")
    _invalidate_release_finalizer_version = release_seam.authority_method("_invalidate_release_finalizer_version")
    _guard_release_finalizer_with_version = release_seam.authority_method("_guard_release_finalizer_with_version")
    _require_running_release_authority = release_seam.authority_method("_require_running_release_authority")
    _require_release_transition_verifier_authority = release_seam.authority_method("_require_release_transition_verifier_authority")
    _prepare_release_dispatch = release_seam.authority_method("_prepare_release_dispatch")
    _release_verifier_handoff_is_durable = release_seam.authority_method("_release_verifier_handoff_is_durable")
    _rewind_app_release_transition = release_seam.authority_method("_rewind_app_release_transition")
    _app_release_transition_lock = release_seam.authority_method("_app_release_transition_lock")
    _enforce_app_release_transition = release_seam.authority_method("_enforce_app_release_transition")
    _enforce_app_release_transition_guarded = release_seam.authority_method("_enforce_app_release_transition_guarded")
    _enforce_app_release_transition_inner = release_seam.authority_method("_enforce_app_release_transition_inner")
    _startup_release_terminal_guard = release_seam.authority_method("_startup_release_terminal_guard")
    _tracker_call_reconcile_release_cycle = staticmethod(release_seam.authority_method("_tracker_call_reconcile_release_cycle"))

    # -- api_views.py ---------------------------------------------------
    confirm_done = api_views.confirm_done
    snapshot = api_views.snapshot
    schedule_snapshot = api_views.schedule_snapshot
    dependency_state_resolved = api_views.dependency_state_resolved
    health = api_views.health
    _health_summary = api_views._health_summary
    _branch_policy_snapshot = api_views._branch_policy_snapshot
    issue_snapshot = api_views.issue_snapshot
    issue_budget_snapshot = api_views.issue_budget_snapshot
    issue_attention = api_views.issue_attention
    _issue_is_terminal = api_views._issue_is_terminal
    _stalled_attention = api_views._stalled_attention
    _retry_attention = api_views._retry_attention
    _running_row = api_views._running_row
    _entry_agent_kind = api_views._entry_agent_kind
    is_paused = api_views.is_paused
    pause_worker = api_views.pause_worker
    resume_worker = api_views.resume_worker
    find_running_issue_id = api_views.find_running_issue_id
    find_resumable_issue_id = api_views.find_resumable_issue_id
    skip_document = api_views.skip_document
    _retry_row = api_views._retry_row

    # -- sweeps.py ------------------------------------------------------
    _done_count_path = sweeps._done_count_path
    _load_done_count = sweeps._load_done_count
    _persist_done_count = sweeps._persist_done_count
    _maybe_run_wiki_sweep = sweeps._maybe_run_wiki_sweep
    _artifact_commit_excludes = staticmethod(sweeps._artifact_commit_excludes)
    _prompt_artifacts_dir = sweeps._prompt_artifacts_dir
    _collect_ticket_artifacts = sweeps._collect_ticket_artifacts
    _artifact_section_body = sweeps._artifact_section_body
    _tracker_call_upsert_artifacts_section = staticmethod(sweeps._tracker_call_upsert_artifacts_section)
    _artifact_sweep = sweeps._artifact_sweep
    _tracker_call_retained_identifiers = staticmethod(sweeps._tracker_call_retained_identifiers)
    _ensure_artifact_dir_git_excluded = sweeps._ensure_artifact_dir_git_excluded
    _archive_sweep = sweeps._archive_sweep
    _maybe_schedule_continuous_improvement = sweeps._maybe_schedule_continuous_improvement
    _maybe_schedule_needs_sweep = sweeps._maybe_schedule_needs_sweep
    _run_continuous_improvement = sweeps._run_continuous_improvement
    _run_improvement_agent = sweeps._run_improvement_agent
    _enforce_improvement_write_contract = sweeps._enforce_improvement_write_contract
    _report_improvement_phase = sweeps._report_improvement_phase
    _on_improvement_task_done = sweeps._on_improvement_task_done
    reset_continuous_improvement_turns = sweeps.reset_continuous_improvement_turns
    continuous_improvement_status = sweeps.continuous_improvement_status
    _improvement_next_due_iso = sweeps._improvement_next_due_iso

    # -- blocked_recovery.py --------------------------------------------
    _after_done_then_remove_per_policy = blocked_recovery._after_done_then_remove_per_policy
    _preserve_history_failure = blocked_recovery._preserve_history_failure
    _block_done_ticket_for_merge_gate = blocked_recovery._block_done_ticket_for_merge_gate
    _auto_merge_done_gate_or_block = blocked_recovery._auto_merge_done_gate_or_block
    _resolve_delivered_factory_blockers = blocked_recovery._resolve_delivered_factory_blockers
    _auto_normalize_legacy_human_review_done = blocked_recovery._auto_normalize_legacy_human_review_done
    _tracker_call_link_blocked_fix = staticmethod(blocked_recovery._tracker_call_link_blocked_fix)
    _tracker_call_active_rca_for_source = staticmethod(blocked_recovery._tracker_call_active_rca_for_source)
    _tracker_call_create_blocked_rca_issue = staticmethod(blocked_recovery._tracker_call_create_blocked_rca_issue)
    recover_blocked_issue = blocked_recovery.recover_blocked_issue
    _recover_blocked_history_gate = blocked_recovery._recover_blocked_history_gate
    _open_blocked_rca_for_issue = blocked_recovery._open_blocked_rca_for_issue
    _auto_recover_blocked_sources = blocked_recovery._auto_recover_blocked_sources
    _resolved_blocked_rca_issue = blocked_recovery._resolved_blocked_rca_issue
    _source_issue_for_blocked_rca = blocked_recovery._source_issue_for_blocked_rca
    _reopen_source_for_resolved_rca = blocked_recovery._reopen_source_for_resolved_rca
    _move_blocked_fix_out_of_done = blocked_recovery._move_blocked_fix_out_of_done
    _hold_unproven_blocked_fix = blocked_recovery._hold_unproven_blocked_fix
    _auto_reopen_sources_from_resolved_rcas = blocked_recovery._auto_reopen_sources_from_resolved_rcas
    _prior_done_run_count = blocked_recovery._prior_done_run_count
    _hold_reopened_ticket_over_budget = blocked_recovery._hold_reopened_ticket_over_budget
    _auto_triage_todo_if_actionable = blocked_recovery._auto_triage_todo_if_actionable

    # -- tracker_bridge.py ----------------------------------------------
    _tracker_call_update_state = tracker_bridge._tracker_call_update_state
    _tracker_call_append_note = staticmethod(tracker_bridge._tracker_call_append_note)
    _tracker_call_fetch_issue_full_by_id = staticmethod(tracker_bridge._tracker_call_fetch_issue_full_by_id)
    _fetch_candidates = tracker_bridge._fetch_candidates
    _record_tracker_error = tracker_bridge._record_tracker_error
    _clear_tracker_error = tracker_bridge._clear_tracker_error
    _shared_tracker_client = tracker_bridge._shared_tracker_client
    _invoke_shared_tracker_client = tracker_bridge._invoke_shared_tracker_client
    _log_tracker_close_race = tracker_bridge._log_tracker_close_race
    _tracker_call_candidates = tracker_bridge._tracker_call_candidates
    _tracker_call_states_by_ids = tracker_bridge._tracker_call_states_by_ids
    _tracker_call_full_by_id = tracker_bridge._tracker_call_full_by_id
    _tracker_call_terminal_issues = tracker_bridge._tracker_call_terminal_issues
    _tracker_call_record_agent_kind = tracker_bridge._tracker_call_record_agent_kind
    _pin_fallback_agent_kind = tracker_bridge._pin_fallback_agent_kind
    _next_fallback_agent_kind = tracker_bridge._next_fallback_agent_kind
    _tracker_call_record_last_agent_kind = tracker_bridge._tracker_call_record_last_agent_kind

    # -- dispatch.py ----------------------------------------------------
    _on_tick = dispatch._on_tick
    _dispatch_fifo_without_schedule_projection = dispatch._dispatch_fifo_without_schedule_projection
    _auto_promote_factory_ready = dispatch._auto_promote_factory_ready
    _sort_with_wait_age_bump = dispatch._sort_with_wait_age_bump
    _should_dispatch = dispatch._should_dispatch
    _eligible = dispatch._eligible
    _eligibility_ownership_decision = dispatch._eligibility_ownership_decision
    _eligibility_contract_decision = dispatch._eligibility_contract_decision
    _eligibility_contention_decision = dispatch._eligibility_contention_decision
    _eligibility_decision = dispatch._eligibility_decision
    _blocker_is_in_flight = dispatch._blocker_is_in_flight
    _available_slots = dispatch._available_slots
    _touched_files_for = dispatch._touched_files_for
    _conflict_blocker = dispatch._conflict_blocker
    _block_ticket_for_conflict = dispatch._block_ticket_for_conflict
    _token_ema_path = dispatch._token_ema_path
    _load_token_ema = dispatch._load_token_ema
    _persist_token_ema = dispatch._persist_token_ema
    _update_token_ema = dispatch._update_token_ema
    _token_ema_for_state = dispatch._token_ema_for_state
    _token_budget_for_state = dispatch._token_budget_for_state
    _max_state_turns_for_state = dispatch._max_state_turns_for_state
    _token_attention_threshold_for_state = dispatch._token_attention_threshold_for_state
    _ticket_prompt_path = dispatch._ticket_prompt_path
    _is_factory_profile = staticmethod(dispatch._is_factory_profile)
    _render_stage_skills = dispatch._render_stage_skills
    _start_factory_verify_phase = dispatch._start_factory_verify_phase
    _enforce_factory_exit_contract = dispatch._enforce_factory_exit_contract
    _record_token_attention_for_turn = dispatch._record_token_attention_for_turn
    _dispatch_env_for = dispatch._dispatch_env_for
    _executor_for = dispatch._executor_for
    _dispatch = dispatch._dispatch
    _on_worker_task_done = dispatch._on_worker_task_done

    # -- agent_phase.py -------------------------------------------------
    run_legacy_stage_loop = agent_phase.run_legacy_stage_loop
    _run_agent_attempt = agent_phase._run_agent_attempt
    _transition_agent_phase = agent_phase._transition_agent_phase
    _enforce_stage_contract = agent_phase._enforce_stage_contract
    _reset_phase_accounting = staticmethod(agent_phase._reset_phase_accounting)
    _rebuild_backend_for_phase = agent_phase._rebuild_backend_for_phase
    _refresh_issue_state = agent_phase._refresh_issue_state
    _refresh_issue_full = agent_phase._refresh_issue_full
    _persist_budget_exhausted_state = agent_phase._persist_budget_exhausted_state
    _escalate_empty_response_loop = agent_phase._escalate_empty_response_loop
    _token_cap_for_entry = staticmethod(agent_phase._token_cap_for_entry)
    _preview_from_payload = staticmethod(agent_phase._preview_from_payload)
    _on_codex_event = agent_phase._on_codex_event
    _apply_token_totals = agent_phase._apply_token_totals
    _record_stats_turn = agent_phase._record_stats_turn
    _record_stats_transition = agent_phase._record_stats_transition
    _record_stats_gate = agent_phase._record_stats_gate
    _board_health_wanted = agent_phase._board_health_wanted
    _board_health_for_prompt = agent_phase._board_health_for_prompt

    # -- retry_reconcile.py ---------------------------------------------
    _on_worker_exit = retry_reconcile._on_worker_exit
    _on_worker_exit_impl = retry_reconcile._on_worker_exit_impl
    _force_eject_zombie = retry_reconcile._force_eject_zombie
    _schedule_retry = retry_reconcile._schedule_retry
    _retry_cap_exceeded = retry_reconcile._retry_cap_exceeded
    _install_retry = retry_reconcile._install_retry
    _in_flight_ids = retry_reconcile._in_flight_ids
    _escalate_max_retries = retry_reconcile._escalate_max_retries
    _on_retry_timer = retry_reconcile._on_retry_timer
    _repark_paused_retry = retry_reconcile._repark_paused_retry
    _process_retry = retry_reconcile._process_retry
    _handle_retry_decision = retry_reconcile._handle_retry_decision
    _repark_retry = retry_reconcile._repark_retry
    _release_retry_ownership = retry_reconcile._release_retry_ownership
    _reconcile_stall_state = retry_reconcile._reconcile_stall_state
    _stall_timeout_ms_for_entry = retry_reconcile._stall_timeout_ms_for_entry
    _reconcile_running = retry_reconcile._reconcile_running
    _reconcile_one = retry_reconcile._reconcile_one
    _startup_terminal_cleanup = retry_reconcile._startup_terminal_cleanup

    # Deprecated alias — the lane was renamed Learn -> Document; external
    # callers using the old method name keep working.
    skip_learn = skip_document
