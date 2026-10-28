"""Tick body, dispatch eligibility, token budgets, and worker dispatch for ``Orchestrator``.

Extracted from ``core.Orchestrator`` (LR-3 lean-core split). Each method is a
module-level function whose first parameter is ``self: Orchestrator``;
``core.Orchestrator`` binds it as a class attribute, so ``self.<name>(...)``
calls, ``Orchestrator.<name>`` patches, and behaviour are unchanged.
Collaborators that tests monkeypatch on ``symphony.orchestrator.core`` are read
through the late-bound ``_core()`` handle at call time, never bound here.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..utils.atomic_json import state_file_name, write_json_atomic
from ..errors import SymphonyError
from ..factory.wayfinder import factory_proof_commands
from ..issue import BlockerRef, Issue, normalize_state
from ..skills import render_skill_block, select_skills_for_stage
from ..workflow.verify import verify_config
from ..workflow import ServiceConfig, selectable_agent_kinds
from ..workspace import WorkspaceManager
from .constants import ARCHIVE_SWEEP_INTERVAL_SEC, WAIT_AGE_BUMP_MIN, _TOKEN_EMA_ALPHA
from .factory_contracts import factory_browser_required, factory_verification_text
from .entries import RunningEntry, _IssueDebug
from .executors import LegacyStageExecutor, TicketExecutor, TicketRunContext
from .helpers import (
    _branch_hook_env,
    _canonical_state_label,
    _from_monotonic_to_iso,
    _is_rewind_transition,
    _is_successful_terminal_state,
    _requested_agent_kind,
    _utc_iso_z,
)
from .parsing import _parse_findings_rows, _parse_touched_files
from .scheduler import MAX_DEPENDENCY_EDGES, DependencyAnalysis, sort_candidates
from .run_registry import ReleaseGate
from .core_support import (
    _EligibilityDecision,
    _EligibilityDisposition,
    _attention_signal,
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


async def _on_tick(self: Orchestrator) -> None:
    if self._schedule_snapshot.get("generated_at"):
        self._schedule_snapshot["stale"] = True
        self._schedule_snapshot["reason"] = "scheduler_pass_incomplete"
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
    if (
        self._workspace_manager is not None
        and self._workspace_manager.root != cfg.workspace_root.resolve()
    ):
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
    await self._ensure_run_registry_async(cfg)
    self._heartbeat_running_leases()
    if self._run_registry is not None:
        registry = self._run_registry
        await self._reclaim_dead_owner_runs_async(registry)
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
        _core().validate_for_dispatch(cfg)
    except SymphonyError as exc:
        log.error("dispatch_validation_failed", error=str(exc))
        await self._notify_observers()
        return

    await self._auto_normalize_legacy_human_review_done(cfg)
    # Validate terminal FIX outcomes before fetching active candidates. A
    # source whose dependency only looks Done must never dispatch first.
    try:
        await self._auto_reopen_sources_from_resolved_rcas(cfg)
    except Exception as exc:
        log.warning("blocked_fix_safety_sweep_failed", error=str(exc))
        await self._notify_observers()
        return

    # Fetch candidates.
    try:
        candidates = await self._fetch_candidates(cfg)
    except Exception as exc:
        self._consecutive_candidate_fetch_failures += 1
        if self._schedule_snapshot.get("generated_at"):
            self._schedule_snapshot["stale"] = True
            self._schedule_snapshot["reason"] = "candidate_fetch_failed"
        log.warning(
            "candidate_fetch_failed",
            error=str(exc),
            consecutive=self._consecutive_candidate_fetch_failures,
        )
        await self._notify_observers()
        return
    self._consecutive_candidate_fetch_failures = 0

    for issue in candidates:
        self._blocked_rca_source_ids.discard(issue.id)
        self._history_recovery_attempted.discard(issue.id)

    dependency_edge_count = sum(len(issue.blocked_by) for issue in candidates)
    dependency_graph_within_bounds = (
        len(candidates) <= _core().MAX_DEPENDENCY_NODES
        and dependency_edge_count <= MAX_DEPENDENCY_EDGES
    )
    if not dependency_graph_within_bounds:
        log.error(
            "dependency_graph_too_large",
            nodes=len(candidates),
            edges=dependency_edge_count,
            policy=cfg.agent.scheduling_policy,
        )
        if cfg.agent.scheduling_policy == "dag":
            self._schedule_snapshot = {
                "schema_version": 1,
                "available": False,
                "reason": "schedule_graph_too_large",
                "generated_at": _utc_iso_z(),
                "stale": False,
                "policy": "dag",
                "policy_order": (
                    "starvation, priority, critical_path, registration"
                ),
                "slots": {},
                "entries": [],
            }
            await self._notify_observers()
            return
    if (
        not dependency_graph_within_bounds
        and cfg.agent.scheduling_policy == "fifo"
    ):
        slots_before = self._available_slots(cfg)
        await self._dispatch_fifo_without_schedule_projection(candidates, cfg)
        self._schedule_snapshot = {
            "schema_version": 1,
            "available": False,
            "reason": "schedule_graph_too_large",
            "generated_at": _utc_iso_z(),
            "stale": False,
            "policy": "fifo",
            "policy_order": "starvation, registration",
            "slots": {
                "running": len(self._running),
                "maximum": cfg.agent.max_concurrent_agents,
                "available_before": slots_before,
                "available_after": self._available_slots(cfg),
            },
            "entries": [],
        }
        if self._available_slots(cfg) > 0:
            await self._auto_recover_blocked_sources(cfg)
        now_monotonic = time.monotonic()
        if (
            self._last_archive_sweep_monotonic is None
            or now_monotonic - self._last_archive_sweep_monotonic
            >= ARCHIVE_SWEEP_INTERVAL_SEC
        ):
            self._last_archive_sweep_monotonic = now_monotonic
            await self._archive_sweep(cfg)
        if (
            self._last_artifact_sweep_monotonic is None
            or now_monotonic - self._last_artifact_sweep_monotonic
            >= ARCHIVE_SWEEP_INTERVAL_SEC
        ):
            self._last_artifact_sweep_monotonic = now_monotonic
            await self._artifact_sweep(cfg)
        self._maybe_schedule_needs_sweep(cfg)
        self._maybe_schedule_continuous_improvement(cfg)
        await self._notify_observers()
        return

    dependency_analysis = (
        await asyncio.to_thread(_core().analyze_dependencies, candidates)
        if dependency_graph_within_bounds
        and (
            cfg.agent.scheduling_policy == "dag"
            or cfg.tracker.kind == "file"
        )
        else DependencyAnalysis({}, {})
    )
    ordered_candidates = self._sort_with_wait_age_bump(
        candidates, cfg, analysis=dependency_analysis
    )
    path_lengths = dependency_analysis.critical_path_lengths
    waves = dependency_analysis.waves
    schedule_entries: list[dict[str, Any]] = []
    ready_rank = 0
    slots_before = self._available_slots(cfg)
    evaluated_at = datetime.now(timezone.utc)
    mutating_scan_open = True
    for scan_position, issue in enumerate(ordered_candidates, start=1):
        released_at = self._claim_released_at.get(issue.id)
        starvation_promoted = bool(
            released_at is not None
            and (evaluated_at - released_at).total_seconds() / 60.0
            >= WAIT_AGE_BUMP_MIN
        )
        entry: dict[str, Any] = {
            "issue_id": issue.id,
            "identifier": issue.identifier,
            "request": (issue.request or "").strip() or None,
            "state": issue.state,
            "evaluated_state": issue.state,
            "evaluated_updated_at": (
                issue.updated_at.isoformat() if issue.updated_at else None
            ),
            "evaluated_priority": issue.priority,
            "evaluated_request": (issue.request or "").strip() or None,
            "evaluated_blocked_by": [
                {
                    "id": blocker.id,
                    "identifier": blocker.identifier,
                    "state": blocker.state,
                }
                for blocker in issue.blocked_by
            ],
            "priority": issue.priority,
            "critical_path_length": path_lengths.get(issue.id, 0),
            "wave": waves.get(issue.id, 0),
            "scan_position": scan_position,
            "queue_rank": None,
            "starvation_promoted": starvation_promoted,
            "status": "waiting",
            "code": "not_evaluated",
            "reason": "not evaluated",
            "dispatch_outcome": None,
            "retry": None,
        }
        if dependency_graph_within_bounds:
            schedule_entries.append(entry)

        if mutating_scan_open and await self._auto_triage_todo_if_actionable(
            issue, cfg
        ):
            entry.update(
                status="waiting",
                code="auto_triage",
                reason="ticket was advanced by automatic triage",
                dispatch_outcome="state_changed",
            )
            continue
        running = self._running.get(issue.id)
        if running is not None:
            entry.update(
                status="running",
                code="running",
                reason="worker is running",
                dispatch_outcome="running",
            )
            if mutating_scan_open and self._available_slots(cfg) <= 0:
                mutating_scan_open = False
            continue
        retry = self._retry.get(issue.id)
        if retry is not None:
            entry.update(
                status="retrying",
                code="retry_scheduled",
                reason="owned by the retry timer",
                retry={
                    "attempt": retry.attempt,
                    "kind": retry.kind,
                    "due_at": _from_monotonic_to_iso(retry.due_at_ms),
                    "holds_slot": retry.holds_slot,
                },
            )
            if mutating_scan_open and self._available_slots(cfg) <= 0:
                mutating_scan_open = False
            continue

        if mutating_scan_open and self._available_slots(cfg) <= 0:
            # Preserve the legacy loop's first capacity break. Remaining
            # rows are projected without tracker writes or dispatch checks.
            mutating_scan_open = False

        decision = self._eligibility_decision(
            issue,
            cfg,
            owning_retry=False,
            include_global_slots=False,
        )
        entry.update(
            status=(
                "ready"
                if decision.disposition is _EligibilityDisposition.READY
                else (
                    "waiting"
                    if decision.disposition
                    in {
                        _EligibilityDisposition.WAIT_SLOT,
                        _EligibilityDisposition.WAIT_NON_SLOT,
                    }
                    else "needs_action"
                )
            ),
            code=decision.code,
            reason=decision.reason,
        )
        if decision.disposition is not _EligibilityDisposition.READY:
            continue

        if await self._auto_promote_factory_ready(issue, cfg):
            entry.update(
                status="waiting",
                code="factory_ready_promotion",
                reason="factory dependency gate advanced to Build",
                dispatch_outcome="state_changed",
            )
            continue

        ready_rank += 1
        entry["queue_rank"] = ready_rank
        if not mutating_scan_open or self._available_slots(cfg) <= 0:
            mutating_scan_open = False
            entry.update(
                status="ready",
                code="waiting_global_capacity",
                reason="ready; waiting for an orchestrator slot",
                dispatch_outcome="queued",
            )
            continue

        if await self._hold_reopened_ticket_over_budget(issue, cfg):
            entry.update(
                status="needs_action",
                code="reopen_budget",
                reason="ticket exceeded agent.max_reopens and was parked",
                dispatch_outcome="state_changed",
            )
            continue

        # C1 — this final pre-dispatch check can still invalidate a
        # forecast because touched-file ownership changes with live runs.
        conflict = self._conflict_blocker(issue)
        if conflict is not None:
            other_identifier, overlap = conflict
            await self._block_ticket_for_conflict(
                cfg, issue, other_identifier, overlap
            )
            entry.update(
                status="needs_action",
                code="refused_conflict",
                reason=f"touched files overlap with {other_identifier}",
                dispatch_outcome="blocked",
            )
            continue
        if await self._hold_release_ticket_without_component(issue, cfg):
            entry.update(
                status="needs_action",
                code="refused_dispatch_authority",
                reason="app release contracts are not installed; ticket was parked",
                dispatch_outcome="blocked",
            )
            continue
        persisted_attempt = self._persisted_retry_attempts.get(issue.id)
        started = self._dispatch(
            issue,
            cfg,
            attempt=persisted_attempt,
            attempt_kind="retry" if persisted_attempt is not None else None,
        )
        entry.update(
            status="running" if started else "needs_action",
            code="dispatched" if started else "refused_dispatch_authority",
            reason=(
                "selected and dispatched"
                if started
                else "selected but final dispatch authority refused the run"
            ),
            dispatch_outcome="started" if started else "refused",
        )

    self._schedule_snapshot = {
        "schema_version": 1,
        "available": (
            cfg.tracker.kind == "file" and dependency_graph_within_bounds
        ),
        "reason": (
            "unsupported_tracker"
            if cfg.tracker.kind != "file"
            else (
                None
                if dependency_graph_within_bounds
                else "schedule_graph_too_large"
            )
        ),
        "generated_at": _utc_iso_z(),
        "stale": False,
        "policy": cfg.agent.scheduling_policy,
        "policy_order": (
            "starvation, priority, critical_path, registration"
            if cfg.agent.scheduling_policy == "dag"
            else "starvation, registration"
        ),
        "slots": {
            "running": len(self._running),
            "maximum": cfg.agent.max_concurrent_agents,
            "available_before": slots_before,
            "available_after": self._available_slots(cfg),
        },
        "entries": schedule_entries if cfg.tracker.kind == "file" else [],
    }

    if self._available_slots(cfg) > 0:
        await self._auto_recover_blocked_sources(cfg)

    now_monotonic = time.monotonic()
    if (
        self._last_archive_sweep_monotonic is None
        or now_monotonic - self._last_archive_sweep_monotonic
        >= ARCHIVE_SWEEP_INTERVAL_SEC
    ):
        self._last_archive_sweep_monotonic = now_monotonic
        await self._archive_sweep(cfg)
    if (
        self._last_artifact_sweep_monotonic is None
        or now_monotonic - self._last_artifact_sweep_monotonic
        >= ARCHIVE_SWEEP_INTERVAL_SEC
    ):
        self._last_artifact_sweep_monotonic = now_monotonic
        await self._artifact_sweep(cfg)

    self._maybe_schedule_needs_sweep(cfg)

    # Continuous-improvement heartbeat (plan §4). Cheap, non-blocking:
    # evaluates due-math + guards and, at most, fires one background task.
    self._maybe_schedule_continuous_improvement(cfg)

    await self._notify_observers()


async def _dispatch_fifo_without_schedule_projection(
    self: Orchestrator, candidates: list[Issue], cfg: ServiceConfig
) -> None:
    """Preserve the legacy bounded loop when explanation limits are exceeded."""

    empty_analysis = DependencyAnalysis({}, {})
    for issue in self._sort_with_wait_age_bump(
        candidates, cfg, analysis=empty_analysis
    ):
        if await self._auto_triage_todo_if_actionable(issue, cfg):
            continue
        if self._is_factory_profile(cfg):
            decision = self._eligibility_decision(
                issue, cfg, owning_retry=False, include_global_slots=False
            )
            if decision.disposition is not _EligibilityDisposition.READY:
                continue
            if await self._auto_promote_factory_ready(issue, cfg):
                continue
        if self._available_slots(cfg) <= 0:
            break
        if not self._should_dispatch(issue, cfg):
            continue
        if await self._hold_reopened_ticket_over_budget(issue, cfg):
            continue
        conflict = self._conflict_blocker(issue)
        if conflict is not None:
            other_identifier, overlap = conflict
            await self._block_ticket_for_conflict(
                cfg, issue, other_identifier, overlap
            )
            continue
        if await self._hold_release_ticket_without_component(issue, cfg):
            continue
        persisted_attempt = self._persisted_retry_attempts.get(issue.id)
        self._dispatch(
            issue,
            cfg,
            attempt=persisted_attempt,
            attempt_kind=("retry" if persisted_attempt is not None else None),
        )


async def _auto_promote_factory_ready(
    self: Orchestrator, issue: Issue, cfg: ServiceConfig
) -> bool:
    """Advance an eligible dependency gate without starting an agent."""
    if not self._is_factory_profile(cfg) or normalize_state(issue.state) != "ready":
        return False
    try:
        await asyncio.to_thread(
            self._tracker_call_update_state, cfg, issue, "Build"
        )
        self._clear_tracker_error(issue.id)
    except Exception as exc:
        self._record_tracker_error(issue.id, exc)
        log.warning(
            "factory_ready_promotion_failed",
            identifier=issue.identifier,
            error=str(exc),
        )
    return True


def _sort_with_wait_age_bump(
    self: Orchestrator,
    candidates: list[Issue],
    cfg: ServiceConfig,
    *,
    analysis: DependencyAnalysis | None = None,
) -> list[Issue]:
    """G3 — promote candidates whose recovered wait age crossed the
        threshold ahead of registration-order FIFO. Candidates with no
        `_claim_released_at` entry, or one inside the threshold, keep
        their FIFO order. Among promoted candidates, oldest release
        wins so the most-starved ticket dispatches first.
        """
    if not self._claim_released_at:
        return sort_candidates(
            candidates, cfg.agent.scheduling_policy, analysis=analysis
        )
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
    bumped.sort(key=lambda i: self._claim_released_at.get(i.id) or now)
    return bumped + sort_candidates(
        normal, cfg.agent.scheduling_policy, analysis=analysis
    )


# ------------------------------------------------------------------
# candidate selection (§8.2)
# ------------------------------------------------------------------

def _should_dispatch(self: Orchestrator, issue: Issue, cfg: ServiceConfig) -> bool:
    """§8.2 — eligibility for the poll-tick dispatch path."""
    return self._eligible(issue, cfg, owning_retry=False)


def _eligible(
    self: Orchestrator, issue: Issue, cfg: ServiceConfig, *, owning_retry: bool
) -> bool:
    """Compatibility seam for callers that only need a yes/no answer."""
    return (
        self._eligibility_decision(
            issue, cfg, owning_retry=owning_retry
        ).disposition
        is _EligibilityDisposition.READY
    )


def _eligibility_ownership_decision(
    self: Orchestrator, issue: Issue, cfg: ServiceConfig, *, owning_retry: bool
) -> _EligibilityDecision | None:
    if issue.id in self._running:
        return _EligibilityDecision(
            _EligibilityDisposition.REJECT,
            "running",
            "duplicate running ownership",
        )
    ci_task = self._improvement_task
    if (
        cfg.continuous_improvement.require_idle_board
        and ci_task is not None
        and not ci_task.done()
    ):
        return _EligibilityDecision(
            _EligibilityDisposition.WAIT_NON_SLOT,
            "continuous_improvement",
            "continuous improvement is using the idle board",
        )
    if self._has_active_run_lease(issue.id):
        reason = "another active run lease exists for this issue"
        self._lease_blocked[issue.id] = reason
        return _EligibilityDecision(
            _EligibilityDisposition.WAIT_NON_SLOT, "leased_elsewhere", reason
        )
    self._lease_blocked.pop(issue.id, None)
    registry = self._run_registry
    if registry is None and self._last_registry_error is not None:
        return _EligibilityDecision(
            _EligibilityDisposition.WAIT_NON_SLOT,
            "registry_unavailable",
            "release evidence authority registry is unavailable",
        )
    if registry is not None:
        unavailable = object()
        identity = self._registry_guard(
            "release_evidence_identity_by_issue_id",
            lambda: registry.get_release_evidence_identity_by_issue_id(issue.id),
            unavailable,
        )
        if identity is unavailable:
            return _EligibilityDecision(
                _EligibilityDisposition.WAIT_NON_SLOT,
                "registry_unavailable",
                "release evidence authority could not be read",
            )
        if identity is not None and identity.retired:
            return _EligibilityDecision(
                _EligibilityDisposition.REJECT,
                "historical_release_verifier",
                "historical release verifier is evidence-only",
            )
    if not owning_retry and issue.id in self._claimed:
        return _EligibilityDecision(
            _EligibilityDisposition.REJECT,
            "claimed",
            "issue already has a claim",
        )
    if issue.id in self._paused_issue_ids:
        return _EligibilityDecision(
            _EligibilityDisposition.WAIT_SLOT,
            "paused",
            self._pause_reasons.get(issue.id, "paused"),
        )
    if issue.id in self._turn_budget_exhausted:
        return _EligibilityDecision(
            _EligibilityDisposition.REJECT,
            "budget_exhausted",
            "turn budget exhausted",
        )
    if issue.id in self._terminal_persist_pending:
        return _EligibilityDecision(
            _EligibilityDisposition.REJECT,
            "finalizing",
            "finalization already owns issue",
        )
    return None


def _eligibility_contract_decision(
    self: Orchestrator, issue: Issue, cfg: ServiceConfig
) -> _EligibilityDecision | None:
    active = {s.lower() for s in cfg.tracker.active_states}
    terminal = {s.lower() for s in cfg.tracker.terminal_states}
    state = normalize_state(issue.state)
    if state in terminal or state not in active:
        return _EligibilityDecision(
            _EligibilityDisposition.REJECT,
            "inactive",
            f"inactive state: {issue.state}",
        )
    if not (issue.id and issue.identifier and issue.title and issue.state):
        return _EligibilityDecision(
            _EligibilityDisposition.REJECT,
            "incomplete_identity",
            "incomplete issue identity",
        )
    requested_agent = _requested_agent_kind(issue)
    kinds = selectable_agent_kinds(cfg.agent.profiles)
    if requested_agent is not None and requested_agent not in kinds:
        log.warning(
            "ticket_agent_kind_unsupported",
            issue_id=issue.id,
            identifier=issue.identifier,
            agent_kind=requested_agent,
            supported=sorted(kinds),
        )
        return _EligibilityDecision(
            _EligibilityDisposition.REJECT,
            "unsupported_agent",
            f"unsupported agent kind: {requested_agent}; "
            f"supported: {sorted(kinds)}",
        )
    return None


def _eligibility_contention_decision(
    self: Orchestrator,
    issue: Issue,
    cfg: ServiceConfig,
    *,
    include_global_slots: bool = True,
) -> _EligibilityDecision | None:
    state = normalize_state(issue.state)
    per_state_cap = cfg.agent.max_concurrent_agents_by_state.get(state)
    if per_state_cap is not None:
        current_in_state = sum(
            1
            for entry in self._running.values()
            if normalize_state(entry.issue.state) == state
        )
        if current_in_state >= per_state_cap:
            return _EligibilityDecision(
                _EligibilityDisposition.WAIT_NON_SLOT,
                "waiting_state_capacity",
                f"per-state capacity reached for {issue.state}",
            )
    for blocker in issue.blocked_by:
        if self._blocker_is_in_flight(blocker):
            return _EligibilityDecision(
                _EligibilityDisposition.WAIT_NON_SLOT,
                "waiting_dependency",
                f"blocker still in flight: {blocker.identifier}",
            )
        if not _blocker_dependency_is_resolved(blocker.state, cfg):
            return _EligibilityDecision(
                _EligibilityDisposition.WAIT_NON_SLOT,
                "waiting_dependency",
                f"blocker unresolved: {blocker.identifier}",
            )
    if include_global_slots and self._available_slots(cfg) == 0:
        return _EligibilityDecision(
            _EligibilityDisposition.WAIT_NON_SLOT,
            "waiting_global_capacity",
            "no available orchestrator slots",
        )
    return None


def _eligibility_decision(
    self: Orchestrator,
    issue: Issue,
    cfg: ServiceConfig,
    *,
    owning_retry: bool,
    include_global_slots: bool = True,
) -> _EligibilityDecision:
    decision = self._eligibility_ownership_decision(
        issue, cfg, owning_retry=owning_retry
    )
    if decision is None:
        decision = self._eligibility_contract_decision(issue, cfg)
    if decision is None:
        decision = self._eligibility_contention_decision(
            issue, cfg, include_global_slots=include_global_slots
        )
    return decision or _EligibilityDecision(
        _EligibilityDisposition.READY, "ready", "eligible"
    )


def _blocker_is_in_flight(self: Orchestrator, blocker: BlockerRef) -> bool:
    """Keep dependents idle until the upstream run is fully finalized."""
    keys = {key for key in (blocker.id, blocker.identifier) if key}
    if keys & self._in_flight_ids():
        return True
    return any(entry.issue.identifier in keys for entry in self._running.values())


def _available_slots(self: Orchestrator, cfg: ServiceConfig) -> int:
    # The retry-counts-against-the-budget rule lives on DispatchState
    # (single owner of slot math) — see its docstring for the OLV-005
    # double-start war story.
    return self._dispatch_state.available_slots(cfg.agent.max_concurrent_agents)


# ------------------------------------------------------------------
# C1 — system-level conflict pre-check
# ------------------------------------------------------------------

def _touched_files_for(self: Orchestrator, issue: Issue) -> set[str]:
    """Return the `## Touched Files` paths declared on a ticket body.

        Parses the issue's markdown description (set by every tracker
        adapter on candidate fetch). Returns an empty set when the
        section is missing or contains no bullet rows. Tolerant of
        malformed bullets — anything we can't recognise is skipped.
        """
    return _parse_touched_files(issue.description)


def _conflict_blocker(self: Orchestrator, candidate: Issue) -> tuple[str, set[str]] | None:
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
        # A retry entry carries a snapshot of the exited ticket's
        # `## Touched Files` taken when the retry was scheduled (#28);
        # prefer the live body when the ticket is still running.
        running_entry = self._running.get(other_id)
        other_files = (
            self._touched_files_for(running_entry.issue)
            if running_entry is not None
            else set(retry_entry.touched_files)
        )
        overlap = candidate_files & other_files
        if overlap:
            return retry_entry.identifier, overlap
    return None


async def _block_ticket_for_conflict(
    self: Orchestrator,
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
        f"`## Touched Files`:\n" + "\n".join(f"- `{p}`" for p in sorted_overlap)
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

def _token_ema_path(self: Orchestrator, cfg: ServiceConfig) -> Path:
    """Return the on-disk location for the persisted EMA snapshot."""
    return (
        cfg.workflow_path.parent
        / ".symphony"
        / state_file_name(cfg.workflow_path, "token_ema")
    )


def _load_token_ema(self: Orchestrator, cfg: ServiceConfig) -> None:
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


def _persist_token_ema(self: Orchestrator, cfg: ServiceConfig) -> None:
    """Best-effort flush to disk via tmp+rename. Failures only log."""
    path = self._token_ema_path(cfg)
    try:
        write_json_atomic(path, self._token_ema)
    except OSError as exc:
        log.warning(
            "token_ema_persist_failed",
            path=str(path),
            error=str(exc),
        )


def _update_token_ema(
    self: Orchestrator, state: str, total_tokens: int, cfg: ServiceConfig | None
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
        _TOKEN_EMA_ALPHA * float(total_tokens) + (1.0 - _TOKEN_EMA_ALPHA) * prev
    )
    if cfg is not None:
        self._persist_token_ema(cfg)


def _token_ema_for_state(self: Orchestrator, state: str) -> int:
    """Rounded EMA for a state. 0 when unseen."""
    key = (state or "").lower()
    return int(round(self._token_ema.get(key, 0.0)))


def _token_budget_for_state(self: Orchestrator, cfg: ServiceConfig, state: str) -> int:
    """Hard cap from `agent.max_total_tokens_by_state` w/ fallback."""
    key = (state or "").lower()
    by_state = cfg.agent.max_total_tokens_by_state
    cap = by_state.get(key)
    if cap is None and key == "learn":
        cap = by_state.get("learning")
    if cap is None and key == "learning":
        cap = by_state.get("learn")
    return cap if cap is not None else cfg.agent.max_total_tokens


def _max_state_turns_for_state(self: Orchestrator, cfg: ServiceConfig, state: str) -> int:
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
    self: Orchestrator, cfg: ServiceConfig, state: str
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
    self: Orchestrator, cfg: ServiceConfig, issue: Issue, workspace_path: Path | None = None
) -> str | None:
    if cfg.tracker.kind != "file":
        return None
    if self._is_factory_profile(cfg) and workspace_path is not None:
        host_board = cfg.tracker.board_root
        if host_board is None:
            return None
        local_path = workspace_path / "kanban" / f"{issue.identifier}.md"
        if (
            local_path.parent.resolve() == host_board.resolve()
            and local_path.is_file()
        ):
            return str(local_path.relative_to(workspace_path))
        host_path = host_board / f"{issue.identifier}.md"
        return str(host_path) if host_path.is_file() else None
    tracker = getattr(self, "_tracker", None)
    find_path = getattr(tracker, "find_path", None)
    if find_path is None:
        return None
    path = find_path(issue.identifier)
    return str(path) if path is not None else None


def _is_factory_profile(cfg: ServiceConfig) -> bool:
    return (
        str(cfg.raw.get("contract_profile", "advanced")).strip().lower()
        == "factory"
    )


def _render_stage_skills(
    self: Orchestrator, cfg: ServiceConfig, issue: Issue, workspace_path: Path
) -> str:
    factory = self._is_factory_profile(cfg)
    return render_skill_block(
        cfg.workflow_path.parent,
        select_skills_for_stage(
            str(cfg.raw.get("contract_profile", "advanced")),
            issue.state,
            issue.skills,
        ),
        runtime_dir=workspace_path if factory else None,
        inline_body=not factory,
    )


def _start_factory_verify_phase(
    self: Orchestrator, cfg: ServiceConfig, issue: Issue, entry: RunningEntry
) -> None:
    entry.factory_verification_baseline = None
    entry.factory_verify_completed = False
    entry.factory_verify_started_at = 0.0
    if self._is_factory_profile(cfg):
        entry.factory_browser_required = (
            entry.factory_browser_required
            or factory_browser_required(issue.description or "", issue.skills)
        )
    if self._is_factory_profile(cfg) and entry.factory_declared_proof is None:
        entry.factory_declared_proof = tuple(
            factory_proof_commands(issue.description or "")
        )
    if self._is_factory_profile(cfg) and normalize_state(issue.state) == "verify":
        entry.factory_verification_baseline = factory_verification_text(
            issue.description or ""
        )
        entry.factory_verify_started_at = datetime.now(timezone.utc).timestamp()


async def _enforce_factory_exit_contract(
    self: Orchestrator, cfg: ServiceConfig, entry: RunningEntry
) -> bool:
    """Use the same gate when a worker exit/reconcile skips the phase loop."""
    producing = normalize_state(entry.state_at_turn_start)
    if not (
        (self._is_factory_profile(cfg) and producing in {"build", "verify"})
        or producing in verify_config(cfg).commands
    ):
        return True
    latest = await self._refresh_issue_full(cfg, entry.issue.id)
    if latest is not None:
        entry.issue = latest
    current = normalize_state(entry.issue.state)
    if (
        not self._is_factory_profile(cfg)
        and current not in {normalize_state(state) for state in cfg.tracker.active_states}
        and not _is_successful_terminal_state(current)
    ):
        return True
    if current == producing or _is_rewind_transition(
        producing, current, cfg.tracker.active_states
    ):
        return True
    outcome = await self._enforce_stage_contract(
        cfg=cfg,
        running_issue_id=entry.issue.id,
        issue=entry.issue,
        producing_state=producing,
        producing_state_raw=_canonical_state_label(cfg, producing),
        current_state=current,
        workspace_path=entry.workspace_path,
        known_app_release=entry.known_app_release,
        running_entry=entry,
    )
    entry.issue = outcome.issue
    return not outcome.rewound


def _record_token_attention_for_turn(
    self: Orchestrator, entry: RunningEntry, cfg: ServiceConfig | None
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
            (f"turn used {turn_total}/{threshold} total tokens in {state}"),
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

def _dispatch_env_for(
    self: Orchestrator,
    *,
    issue: Issue,
    cfg: ServiceConfig,
    is_rewind: bool,
) -> dict[str, str]:
    """Return the per-dispatch env vars for the backend subprocess.

        Always includes:
          * ``SYMPHONY_TOKEN_EMA`` — rolling EMA of total tokens for the
            current state (rounded int), 0 when unseen.
          * ``SYMPHONY_TOKEN_BUDGET`` — hard cap for the current state
            (max_total_tokens_by_state with fallback to max_total_tokens).

        Rewind dispatches also include:
          * ``SYMPHONY_REWIND_SCOPE`` — JSON list of finding rows parsed
            from the latest applicable failure section (`## Review Findings`,
            `## QA Failure`, or `## Contract Failure`). Empty list when
            parsing fails (the env var is informational; an empty list
            signals "rewind, no machine-readable scope").

        Forward dispatches omit the rewind scope so a previous-turn value
        cannot bleed across. The mapping travels in ``BackendInit.env`` and
        is overlaid on the inherited environment at spawn time; nothing here
        touches ``os.environ`` (GitHub issue #29).
        """
    ema_value = self._token_ema_for_state(issue.state)
    budget_value = self._token_budget_for_state(cfg, issue.state)
    env = {
        "SYMPHONY_TOKEN_EMA": str(ema_value),
        "SYMPHONY_TOKEN_BUDGET": str(budget_value),
    }
    if is_rewind:
        rows = _parse_findings_rows(issue.description)
        try:
            payload = json.dumps(rows, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = "[]"
        env["SYMPHONY_REWIND_SCOPE"] = payload
    return env


# ------------------------------------------------------------------
# dispatch (§16.4)
# ------------------------------------------------------------------

def _executor_for(self: Orchestrator, cfg: ServiceConfig) -> TicketExecutor:
    """Pick the execution strategy for one dispatch."""
    return LegacyStageExecutor(self)


def _dispatch(
    self: Orchestrator,
    issue: Issue,
    cfg: ServiceConfig,
    *,
    attempt: int | None,
    attempt_kind: str | None = None,
) -> bool:
    try:
        release_authority = self._prepare_release_dispatch(issue, cfg)
    except SymphonyError as exc:
        log.error(
            "release_dispatch_refused",
            issue_id=issue.id,
            identifier=issue.identifier,
            tracker_kind=cfg.tracker.kind,
            error=str(exc),
        )
        return False
    issue = release_authority.issue
    self._dispatch_state.cancel_pending_retry(issue.id)

    workspace_path = (
        self._workspace_manager.path_for(issue.identifier)
        if self._workspace_manager
        else Path("/")
    )
    resolved_attempt_kind = attempt_kind or (
        "retry" if attempt is not None else "initial"
    )
    agent_kind = cfg.agent.kind_for_state(issue.state, _requested_agent_kind(issue))
    acquisition = self._try_acquire_run_lease(
        cfg=cfg,
        issue=issue,
        workspace_path=workspace_path,
        attempt=attempt,
        attempt_kind=resolved_attempt_kind,
        agent_kind=agent_kind,
        release_required=(
            release_authority.gate is not None
            or release_authority.app_release
            or release_authority.finalizer
        ),
    )
    if acquisition is None:
        return False
    run_id = acquisition.run_id
    if acquisition.checkpoint is not None:
        resolved_attempt_kind = "recovery"
    if release_authority.gate is not None:
        try:
            gate = release_authority.gate
            if release_authority.finalizer:
                bound = bool(
                    self._release_registry_call(
                        cfg,
                        "bind_release_finalizer_run",
                        lambda registry: registry.bind_release_finalizer_run(
                            gate=gate,
                            finalizer_issue_id=issue.id,
                            finalizer_run_id=run_id,
                        ),
                    )
                )
                current_gate = cast(
                    ReleaseGate | None,
                    self._release_registry_call(
                        cfg,
                        "read_bound_finalizer_gate",
                        lambda registry: registry.get_release_gate(
                            gate.finalizer_identifier
                        ),
                    ),
                )
                if not bound or current_gate is None:
                    raise SymphonyError(
                        "application release finalizer lease could not be "
                        "bound to the approved gate"
                    )
                issue = self._guard_release_finalizer(
                    cfg=cfg,
                    issue=issue,
                    gate=current_gate,
                    expected_run_id=run_id,
                    require_run_authority=True,
                )
            elif gate.status == "pending":
                bound = bool(
                    self._release_registry_call(
                        cfg,
                        "bind_release_verifier_run",
                        lambda registry: registry.bind_release_verifier_run(
                            gate=gate,
                            verifier_run_id=run_id,
                        ),
                    )
                )
                current_gate = cast(
                    ReleaseGate | None,
                    self._release_registry_call(
                        cfg,
                        "read_bound_verifier_gate",
                        lambda registry: registry.get_release_gate_for_verifier(
                            issue.identifier
                        ),
                    ),
                )
                if not bound or current_gate is None:
                    raise SymphonyError(
                        "application release verifier lease could not be bound "
                        "to the pending gate"
                    )
            else:
                current_gate = cast(
                    ReleaseGate | None,
                    self._release_registry_call(
                        cfg,
                        "reread_release_gate_after_lease",
                        lambda registry: registry.get_release_gate_for_verifier(
                            issue.identifier
                        ),
                    ),
                )
                if current_gate is None:
                    raise SymphonyError(
                        "application release verifier authority disappeared "
                        "after lease acquisition"
                    )
            if (
                current_gate.verifier_issue_id != gate.verifier_issue_id
                or current_gate.verifier_identifier != gate.verifier_identifier
                or current_gate.expected_contract_sha256
                != gate.expected_contract_sha256
                or current_gate.cycle_fingerprint != gate.cycle_fingerprint
            ):
                raise SymphonyError(
                    "application release gate changed during lease acquisition"
                )
            release_authority = replace(
                release_authority,
                issue=issue,
                gate=current_gate,
            )
        except Exception as exc:
            try:
                self._release_registry_call(
                    cfg,
                    "release_failed_acquired_run",
                    lambda registry: registry.complete_run(
                        issue_id=issue.id,
                        run_id=run_id,
                        status="release_authority_lost",
                    ),
                )
            except Exception:
                pass
            log.error(
                "release_dispatch_refused_after_lease",
                issue_id=issue.id,
                identifier=issue.identifier,
                error=str(exc),
            )
            return False
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=attempt,
        worker_task=None,
        workspace_path=workspace_path,
        attempt_kind=resolved_attempt_kind,
        agent_kind=agent_kind,
        run_id=run_id,
        continued_from_run_id=acquisition.continued_from_run_id,
        continuation_checkpoint=acquisition.checkpoint,
        known_app_release=release_authority.app_release,
        known_release_cycle_verifier=release_authority.cycle_verifier,
        known_app_release_finalizer=release_authority.finalizer,
        release_gate_finalizer=(
            release_authority.gate.finalizer_identifier
            if release_authority.gate is not None
            else ""
        ),
        release_gate_expected_contract_sha256=(
            release_authority.gate.expected_contract_sha256
            if release_authority.gate is not None
            else ""
        ),
        release_gate_cycle_fingerprint=(
            release_authority.gate.cycle_fingerprint
            if release_authority.gate is not None
            else ""
        ),
        release_gate_generation=(
            release_authority.gate.generation
            if release_authority.gate is not None
            else ""
        ),
        release_finalizer_rewind_state=(
            issue.state if release_authority.finalizer else ""
        ),
        release_authority_resolved=True,
    )
    self._dispatch_state.begin_run(issue.id, entry)
    # Execution mode is chosen once, here, and fixed for the run's
    # lifetime. Deciding later would let a mid-run config reload switch
    # a ticket between the stage loop and a DAG.
    executor = self._executor_for(cfg)
    run_context = TicketRunContext(
        issue=issue,
        attempt=attempt,
        cfg=cfg,
        run_id=run_id,
        workspace_path=workspace_path,
        agent_kind=agent_kind,
        attempt_kind=resolved_attempt_kind,
    )
    try:
        worker_task = asyncio.create_task(
            executor.execute(run_context),
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
    self._append_run_event(entry, "run_started", {"state": issue.state})
    debug = self._issue_debug.setdefault(issue.id, _IssueDebug())
    if acquisition.checkpoint is not None:
        debug.completed_turn_count = max(
            debug.completed_turn_count, acquisition.checkpoint.turn
        )
        debug.restart_count += 1
    elif attempt is not None:
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
    # adapter preserves any existing override. Skipped when
    # `agent.stage_kinds` routing is active: the stamp is read back as
    # a per-ticket pin on later dispatches, which would freeze the
    # first stage's backend and defeat per-state routing.
    try:
        if cfg.agent.stage_kinds or issue.planning_ref is not None:
            # Routed boards and PM-managed tickets must keep inherited
            # backend selection out of the authoritative pin.  PM handoff
            # already persists an explicit task override when one exists;
            # writing the resolved workflow backend here would mutate an
            # inherited task after its dispatch permit was reserved.
            # The audit-only field remains visible without changing scope.
            self._tracker_call_record_last_agent_kind(
                cfg, issue.identifier, entry.agent_kind
            )
        else:
            self._tracker_call_record_agent_kind(
                cfg, issue.identifier, entry.agent_kind
            )
    except Exception as exc:
        log.warning(
            "record_agent_kind_failed",
            issue_id=issue.id,
            identifier=issue.identifier,
            agent_kind=entry.agent_kind,
            error=str(exc),
        )
    return True


def _on_worker_task_done(self: Orchestrator, issue_id: str, task: asyncio.Task[None]) -> None:
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

        AF-01 secondary defect: `task.exception()` is fetched unconditionally
        up front, before the `entry is None` early return. A raising
        `_on_worker_exit_impl` pops the entry and then fails, so by the time
        this callback runs there is nothing left to find via
        `entry_owned_by` even though the task truly ended errored — without
        retrieving it here, that exception is silently dropped and surfaces
        only as asyncio's "Task exception was never retrieved" warning.
        """
    cancelled_before_start = task.cancelled()
    exc: BaseException | None = None
    if not cancelled_before_start:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            cancelled_before_start = True

    entry = self._dispatch_state.entry_owned_by(issue_id, task)
    if entry is None:
        if exc is not None:
            log.error(
                "worker_task_errored_after_cleanup",
                issue_id=issue_id,
                task_name=task.get_name(),
                error=str(exc),
                exc_type=type(exc).__name__,
            )
        return
    if entry.exit_started_at is not None:
        log.info(
            "worker_task_done_after_exit_started",
            issue_id=issue_id,
            task_name=task.get_name(),
            exit_started_at=entry.exit_started_at.isoformat(),
        )
        return
    if cancelled_before_start and self._stopping:
        reason = "shutdown_interrupted"
        error = None
    elif cancelled_before_start:
        reason = "worker_task_cancelled_before_start"
        error = "asyncio task was cancelled before worker cleanup ran"
    else:
        reason = "worker_task_finished_without_cleanup"
        error = (
            str(exc)
            if exc is not None
            else "worker task completed without exit cleanup"
        )
    exc_repr = f"{type(exc).__name__}: {exc!r}" if exc is not None else None
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
        self._on_worker_exit(issue_id, reason, error, owning_task=task),
        name=f"symphony-worker-exit-{issue_id}",
    )
