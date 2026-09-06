"""Pure module-level helpers used by the Orchestrator state machine.

Everything in this module is stateless: it takes plain values in and
returns plain values out. Time conversions, sort order, hook env
building, and the ticket-level dispatch eligibility predicates all
live here so the `Orchestrator` class body stays focused on the
asyncio orchestration itself.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import ConfigValidationError
from ..issue import Issue, normalize_state, sort_for_dispatch
from ..logging import get_logger
from ..notifications import NotificationEvent, dispatch_notification
from ..ticket_markdown import parse_body_dependency_ids
from ..workflow import ServiceConfig, SUPPORTED_AGENT_KINDS
from .constants import (
    _AUTO_TRIAGE_ACCEPTANCE_RE,
    _AUTO_TRIAGE_TRIAGE_RE,
    _REWIND_TRANSITIONS,
)


log = get_logger()


def _is_rewind_transition(
    prev_state: str,
    current_state: str,
    active_states: tuple[str, ...] | None = None,
) -> bool:
    """True when a transition moves backwards in configured active order."""
    previous = normalize_state(prev_state)
    current = normalize_state(current_state)
    if active_states is None:
        return (previous, current) in _REWIND_TRANSITIONS
    order = [normalize_state(state) for state in active_states]
    try:
        return order.index(previous) > order.index(current)
    except ValueError:
        return False


def _canonical_state_label(cfg: ServiceConfig, normalized: str) -> str:
    """Return the workflow's own spelling of a normalized state ("" if unknown)."""
    for label in (*cfg.tracker.active_states, *cfg.tracker.terminal_states):
        if normalize_state(label) == normalized:
            return label
    return ""


def _branch_hook_env(cfg: ServiceConfig) -> dict[str, str]:
    """Env consumed by the default worktree hook when creating a feature branch.

    `SYMPHONY_BOARD_ROOT` / `SYMPHONY_BOARD_ROOT_NAME` let the setup hook link
    the *configured* board root back into the workspace. Hooks that hardcode
    `kanban` silently give the worker no board on any other `board_root`,
    which the orchestrator sees only as an endless re-dispatch loop.
    """
    env = {
        "SYMPHONY_FEATURE_BASE_BRANCH": cfg.agent.feature_base_branch or "",
        "SYMPHONY_MERGE_TARGET_BRANCH": cfg.agent.auto_merge_target_branch or "",
    }
    name = board_root_name_for_hooks(cfg)
    root = cfg.tracker.board_root
    if root is not None:
        env["SYMPHONY_BOARD_ROOT"] = str(root.resolve())
    if name is not None:
        env["SYMPHONY_BOARD_ROOT_NAME"] = name
    return env


def resolve_symphony_cli() -> str:
    """Absolute path to the `symphony` CLI a dispatched worker can run.

    F-19: every stage prompt and the chat preamble now *require*
    `symphony board new`, but Symphony is typically installed in a venv and
    launched by absolute path (`.venv/bin/symphony`) or by `sys.executable -m`.
    The spawned agent inherits the orchestrator's PATH, which need not contain
    that venv's `bin`. Exporting the resolved path as `SYMPHONY_CLI` lets the
    prompts say `${SYMPHONY_CLI:-symphony} board new ...` and work either way.
    """
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is not None and argv0.name in {"symphony", "symphony.exe"}:
        resolved = argv0.resolve()
        if resolved.is_file():
            return str(resolved)
    sibling = Path(sys.executable).parent / (
        "symphony.exe" if sys.platform == "win32" else "symphony"
    )
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("symphony")
    if found:
        return str(Path(found).resolve())
    # Last resort: the module entry point through this interpreter. Callers
    # interpolate it unquoted (`${SYMPHONY_CLI:-symphony} board ...`), so a
    # multi-word value still forms a valid command line.
    return f"{sys.executable} -m symphony.cli.main"


def board_root_name_for_hooks(cfg: ServiceConfig) -> str | None:
    """Board root path relative to the workflow dir, or None when outside it.

    Only a board that lives *inside* the workflow directory can be linked
    into a worktree workspace by relative name; an out-of-tree board root is
    reached by absolute path and needs no link.
    """
    root = cfg.tracker.board_root
    if root is None:
        return None
    workflow_dir = cfg.workflow_path.parent.resolve()
    try:
        relative = root.resolve().relative_to(workflow_dir)
    except ValueError:
        return None
    text = relative.as_posix()
    return text or None


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
    if parse_body_dependency_ids(description):
        return False
    if _AUTO_TRIAGE_TRIAGE_RE.search(description):
        return False
    return bool(_AUTO_TRIAGE_ACCEPTANCE_RE.search(description))


def _config_for_issue_agent(cfg: ServiceConfig, issue: Issue) -> ServiceConfig:
    """Return a per-worker config with the ticket's backend override applied.

    Precedence: per-ticket `agent_kind` pin > `agent.stage_kinds` entry for
    the ticket's current state > workflow-level `agent.kind`.
    """
    pin = _requested_agent_kind(issue)
    if pin is not None and pin not in SUPPORTED_AGENT_KINDS:
        raise ConfigValidationError(
            f"ticket agent.kind must be one of {sorted(SUPPORTED_AGENT_KINDS)}",
            value=pin,
            issue=issue.identifier,
        )
    kind = cfg.agent.kind_for_state(issue.state, pin)
    if kind == cfg.agent.kind:
        return cfg
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


def _terminal_state_matching(cfg: ServiceConfig, *keywords: str) -> str:
    """First terminal lane whose normalized name contains any keyword.

    F-32: `"Human Review"` and `"Blocked"` used to be hardcoded transition
    targets, so a fully customized board (explicitly allowed) got a state
    string its tracker does not know. Resolve through the configured lanes
    and let the caller refuse cleanly when none matches.
    """
    normalized = [(state, normalize_state(state)) for state in cfg.tracker.terminal_states]
    for keyword in keywords:
        for state, low in normalized:
            if keyword in low:
                return state
    return ""


def _human_review_target_state(cfg: ServiceConfig) -> str:
    """Lane for operator attention: `human`-ish, else `block`-ish, else the
    first terminal lane. Empty when the board declares no terminal lane."""
    resolved = _terminal_state_matching(cfg, "human", "review", "block")
    if resolved:
        return resolved
    return cfg.tracker.terminal_states[0] if cfg.tracker.terminal_states else ""


def _rewind_budget_target_state(cfg: ServiceConfig) -> str:
    """Lane for a ticket that exhausted its rewind budget."""
    resolved = _terminal_state_matching(cfg, "block", "human")
    if resolved:
        return resolved
    return cfg.tracker.terminal_states[0] if cfg.tracker.terminal_states else ""


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
