"""Pure module-level helpers used by the Orchestrator state machine.

Everything in this module is stateless: it takes plain values in and
returns plain values out. Time conversions, sort order, hook env
building, and the ticket-level dispatch eligibility predicates all
live here so the `Orchestrator` class body stays focused on the
asyncio orchestration itself.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import ConfigValidationError
from ..issue import Issue, normalize_state, sort_for_dispatch
from ..logging import get_logger
from ..notifications import NotificationEvent, dispatch_notification
from ..workflow import ServiceConfig, SUPPORTED_AGENT_KINDS
from .constants import (
    _AUTO_TRIAGE_ACCEPTANCE_RE,
    _AUTO_TRIAGE_TRIAGE_RE,
    _REWIND_TRANSITIONS,
)


log = get_logger()


def _is_rewind_transition(prev_state: str, current_state: str) -> bool:
    """True when a phase transition is moving backwards in the pipeline.

    Verify and Learn are the only 4-stage pipeline rewinds. Contract
    failures still force a rewind at the caller even when the state pair
    itself is not in this static map.
    """
    return (prev_state, current_state) in _REWIND_TRANSITIONS


def _branch_hook_env(cfg: ServiceConfig) -> dict[str, str]:
    """Env consumed by the default worktree hook when creating a feature branch."""
    return {
        "SYMPHONY_FEATURE_BASE_BRANCH": cfg.agent.feature_base_branch or "",
        "SYMPHONY_MERGE_TARGET_BRANCH": cfg.agent.auto_merge_target_branch or "",
    }


async def _branch_already_merged_into_target(
    workflow_dir: Path, *, branch: str, target_branch: str
) -> bool:
    """True when `branch` is already contained by the merge target.

    Startup cleanup uses this before it snapshots lingering Done workspaces:
    if an operator has already merged the branch into the target, a restart
    must not create a fresh commit on the old feature branch and re-open the
    merge gate.
    """
    target = (target_branch or "HEAD").strip() or "HEAD"

    def _check() -> bool:
        verify_branch = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=str(workflow_dir),
            capture_output=True,
            check=False,
        )
        if verify_branch.returncode != 0:
            return False
        verify_target = subprocess.run(
            ["git", "rev-parse", "--verify", target],
            cwd=str(workflow_dir),
            capture_output=True,
            check=False,
        )
        if verify_target.returncode != 0:
            return False
        merged = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch, target],
            cwd=str(workflow_dir),
            capture_output=True,
            check=False,
        )
        return merged.returncode == 0

    try:
        return await asyncio.to_thread(_check)
    except Exception:
        return False


def _requested_agent_kind(issue: Issue) -> str | None:
    if not issue.agent_kind:
        return None
    kind = issue.agent_kind.strip().lower()
    return kind or None


def _is_auto_triage_todo_candidate(issue: Issue, cfg: ServiceConfig) -> bool:
    if not cfg.agent.auto_triage_actionable_todo:
        return False
    if cfg.tracker.kind != "file":
        return False
    if normalize_state(issue.state) != "todo":
        return False
    if not any(normalize_state(s) == "in progress" for s in cfg.tracker.active_states):
        return False
    if issue.blocked_by:
        return False
    if any(label.strip().lower() == "bug" for label in issue.labels):
        return False
    description = issue.description or ""
    if not description.strip():
        return False
    if _AUTO_TRIAGE_TRIAGE_RE.search(description):
        return False
    return bool(_AUTO_TRIAGE_ACCEPTANCE_RE.search(description))


def _config_for_issue_agent(cfg: ServiceConfig, issue: Issue) -> ServiceConfig:
    """Return a per-worker config with the ticket's backend override applied."""
    kind = _requested_agent_kind(issue)
    if kind is None or kind == cfg.agent.kind:
        return cfg
    if kind not in SUPPORTED_AGENT_KINDS:
        raise ConfigValidationError(
            f"ticket agent.kind must be one of {sorted(SUPPORTED_AGENT_KINDS)}",
            value=kind,
            issue=issue.identifier,
        )
    return replace(cfg, agent=replace(cfg.agent, kind=kind))


def _utc_iso_z() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_monotonic_to_iso(due_at_ms: float) -> str:
    """Best-effort: project monotonic time onto wall clock for display."""
    loop = asyncio.get_event_loop()
    now_mono = loop.time() * 1000.0
    delta_seconds = max((due_at_ms - now_mono) / 1000.0, 0.0)
    target = datetime.now(timezone.utc).timestamp() + delta_seconds
    return datetime.fromtimestamp(target, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _max_turns_exhausted_target_state(cfg: ServiceConfig) -> str:
    if cfg.agent.budget_exhausted_state:
        return cfg.agent.budget_exhausted_state
    for state in cfg.tracker.terminal_states:
        if normalize_state(state) == "blocked":
            return state
    return ""


def _notify_state_transition(
    cfg: ServiceConfig, issue: Issue, target_state: str
) -> None:
    """Fire-and-forget Slack (or future channel) ping for one transition.

    Lives at module scope so the static ``_tracker_call_update_state`` can
    call it without an instance reference. Errors from the dispatcher are
    already swallowed; this wrapper only guards the lookup itself so a
    malformed config or a hot reload-mid-transition can't take down the
    tracker write path.
    """
    if not cfg.notifications.has_any():
        return
    try:
        event = NotificationEvent(
            identifier=issue.identifier,
            title=issue.title,
            prev_state=issue.state,
            next_state=target_state,
            workflow=cfg.workflow_path.parent.name,
        )
        dispatch_notification(cfg.notifications, event)
    except Exception as exc:
        log.warning(
            "notification_emit_failed",
            identifier=issue.identifier,
            target=target_state,
            error=str(exc),
        )


def _task_debug(task: asyncio.Task[Any] | None) -> dict[str, Any] | None:
    if task is None:
        return None
    stack = [
        f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"
        for frame in task.get_stack()
    ]
    return {
        "name": task.get_name(),
        "done": task.done(),
        "cancelled": task.cancelled() if task.done() else False,
        "coro_repr": repr(task.get_coro()),
        "stack": stack,
    }


def _sort_for_dispatch_fifo(issues: list[Issue], cfg: ServiceConfig) -> list[Issue]:
    """Sort dispatch candidates by stable ticket registration order."""
    del cfg  # Reserved for future tracker-specific ordering knobs.
    return sort_for_dispatch(issues)
