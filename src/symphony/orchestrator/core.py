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
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Coroutine, TypeVar, cast

from .. import __version__
from .._shell import kill_process_group, process_group_exists, process_identity
from ..artifacts import ArtifactRecord, ArtifactStore, format_bytes
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
    redact_session_id,
)
from ..backends import build_backend
from ..chat import cfg_for_mode
from ..utils import git_inspect
from ..utils.archive import select_archivable
from ..backends.codex import linear_graphql_tool
from ..errors import (
    SymphonyError,
    TurnFailed,
    TurnInputRequired,
    TurnTimeout,
    TurnCancelled,
)
from ..continuous_improvement import (
    AgentTask,
    FileLease,
    any_mode_due,
    ImprovementRunner,
    Lease,
    default_improvement_runner,
    lease_path_for,
)
from ..issue import BlockerRef, Issue, normalize_state
from ..logging import get_logger
from ..prompt import build_continuation_prompt, build_first_turn_prompt
from ..runtime_safety import ensure_workflow_repo_is_safe
from ..service_identity import SERVICE_INSTANCE_ENV, normalize_service_instance_id
from ..skills import render_skill_block
from ..stats import StatsStore, stats_store_for
from ..trackers import TrackerClient, build_tracker_client
from ..utils.wiki_sweep import sweep as _wiki_sweep_run
from ..workflow import (
    DEFAULT_TERMINAL_STATES,
    ServiceConfig,
    SUPPORTED_AGENT_KINDS,
    SYMPHONY_BRANCH_PREFIX,
    WorkflowState,
    validate_for_dispatch,
)
from ..utils.auto_merge import AutoMergeResult, auto_merge_on_done_best_effort
from ..utils.git_sandbox import SANDBOX_WRITE_DENIED, classify_history_failure
from ..workspace import (
    HISTORY_PUSH_FAILED,
    HistoryGateResult,
    WorkspaceManager,
    commit_workspace_on_done,
    finalize_delivery_history,
    verify_branch_history,
)
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
from .release_contracts import (
    ReleaseValidationResult,
    release_workspace_target_errors,
    resolve_target_release_identity,
    validate_release_contract,
)
from .release_cycle import (
    ReleaseCycleService,
    ReleaseCycleWriteResult as _ReleaseCycleWriteResult,
    has_active_verify_lane as _has_active_release_verify_lane,
    has_release_finalizer_lane as _has_release_finalizer_lane,
    initial_release_gate_fingerprint as _initial_release_gate_fingerprint,
    is_release_success_state as _is_release_success_state,
    is_release_evidence_issue as _is_release_evidence_issue,
    is_release_finalizer as _is_release_finalizer,
    release_failure_target_state as _release_failure_target_state,
    release_ticket_version_token as _release_ticket_version_token,
    release_verifier_state as _release_verifier_state,
)
from .dispatch_state import DispatchState
from .entries import RetryEntry, RunningEntry, _CodexTotals, _IssueDebug
from .executors import LegacyStageExecutor, TicketExecutor, TicketRunContext
from .helpers import (
    _branch_hook_env,
    _branch_already_merged_into_target,
    _config_for_issue_agent,
    resolve_symphony_cli,
    _from_monotonic_to_iso,
    _is_auto_triage_todo_candidate,
    _is_rewind_transition,
    _human_review_target_state,
    _max_turns_exhausted_target_state,
    _rewind_budget_target_state,
    _notify_state_transition,
    _requested_agent_kind,
    _sort_for_dispatch_fifo,
    _task_debug,
    _to_iso,
    _utc_iso_z,
)
from .parsing import _parse_findings_rows, _parse_touched_files
from .scheduler import (
    MAX_DEPENDENCY_EDGES,
    MAX_DEPENDENCY_NODES,
    DependencyAnalysis,
    analyze_dependencies,
    sort_candidates,
)
from .run_registry import (
    ContinuationCheckpoint,
    ReleaseEvidenceIdentity,
    ReleaseGate,
    RunRecord,
    RunRegistry,
    registry_path_for_workflow,
)


# Initiative D — the former ``_pkg.<name>`` parent-package indirection is
# gone. Collaborators (``build_backend``, ``commit_workspace_on_done``,
# ``auto_merge_on_done_best_effort``) are imported directly and looked up
# from this module's globals at call time, so tests patch
# ``symphony.orchestrator.core.<name>`` — the consumer's reference.
# ``build_backend`` is additionally constructor-injectable.

log = get_logger()

DEFAULT_IMPROVEMENT_RUN_TIMEOUT_S = 3600.0

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FAILED_BLOCKER_TERMINAL_STATES = {
    "archive",
    "archived",
    "blocked",
    "cancelled",
    "canceled",
    "duplicate",
    "human review",
}
_RETRYABLE_WORKER_ERROR_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "temporarily overloaded",
    "overloaded",
    "service unavailable",
    "connection error",
    "network error",
    "connection reset",
    "connection timed out",
    "try again later",
    # Transient backend stream faults (acceptance finding 3). The strings
    # below are emitted verbatim by the backends; `test_backend_contract.py`
    # pins them so the two lists cannot drift apart.
    "stream unreadable",  # claude_code.py / codex.py / pi.py
    "no result event",  # claude_code.py — rc!=0 with no result frame
)


class _EligibilityDisposition(str, Enum):
    READY = "ready"
    WAIT_SLOT = "wait_slot"
    WAIT_NON_SLOT = "wait_non_slot"
    REJECT = "reject"


@dataclass(frozen=True)
class _EligibilityDecision:
    disposition: _EligibilityDisposition
    code: str
    reason: str


@dataclass(frozen=True)
class _RunLeaseAcquisition:
    """One new lease and an optional predecessor checkpoint."""

    run_id: str
    continued_from_run_id: str = ""
    checkpoint: ContinuationCheckpoint | None = None


@dataclass(frozen=True)
class _ReleaseDispatchAuthority:
    """Monotonic release role resolved before a worker lease is acquired."""

    issue: Issue
    gate: ReleaseGate | None = None
    app_release: bool = False
    cycle_verifier: bool = False
    finalizer: bool = False


@dataclass(frozen=True)
class _AgentPhaseState:
    issue: Issue
    cfg: ServiceConfig
    client: AgentBackend
    first_prompt: str
    current_state: str
    known_app_release: bool


@dataclass(frozen=True)
class _AgentPhaseTransition:
    state: _AgentPhaseState
    is_rewind: bool


class _ReleaseTransitionAuthorityLost(SymphonyError):
    """A stale release worker must exit without mutating its replacement."""


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


# The one path a continuous-improvement agent turn may write in the host
# worktree. Mirrors `continuous_improvement.AGENT_OUTPUT_DIR`.
CI_AGENT_OUTPUT_PREFIX = ".symphony/continuous-improvement/proposals/"


def _worktree_status_snapshot(cwd: Path) -> dict[str, str] | None:
    """`path -> porcelain status code` for a worktree, or None when not git.

    Untracked files are listed individually (`-uall`) so a new file in a
    previously-clean directory is visible as its own path.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-uall", "-z"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    snapshot: dict[str, str] = {}
    for record in result.stdout.split("\0"):
        if len(record) < 4:
            continue
        code, path = record[:2], record[3:]
        if path:
            snapshot[path] = code
    return snapshot


def _clean_board_error_message(message: str) -> str:
    without_ansi = _ANSI_ESCAPE_RE.sub("", message)
    without_controls = _CONTROL_CHAR_RE.sub("", without_ansi)
    return " ".join(without_controls.split())


def _worker_error_pause_reason(reason: str, error: str | None) -> str:
    detail = f"{reason}: {error}" if error else reason
    clean = _clean_board_error_message(detail)
    return f"worker error: {clean}; paused for operator inspection"


def _has_retryable_worker_marker(clean_error: str) -> bool:
    return any(marker in clean_error for marker in _RETRYABLE_WORKER_ERROR_MARKERS)


def _is_opencode_sigterm_retry(agent_kind: str, clean_error: str) -> bool:
    if "exit -15" not in clean_error:
        return False
    return agent_kind == "opencode" or "opencode" in clean_error


def _is_retryable_worker_error(agent_kind: str, reason: str, error: str | None) -> bool:
    detail = f"{reason}: {error}" if error else reason
    clean = _clean_board_error_message(detail).lower()
    if _has_retryable_worker_marker(clean):
        return True
    # Live OpenCode throttling can surface as SIGTERM with no stderr.
    return _is_opencode_sigterm_retry(agent_kind, clean)


def _is_retryable_auto_pause_reason(pause_reason: str | None) -> bool:
    if not pause_reason:
        return False
    clean = _clean_board_error_message(pause_reason).lower()
    if "worker error:" not in clean or "paused for operator inspection" not in clean:
        return False
    return _has_retryable_worker_marker(clean) or _is_opencode_sigterm_retry("", clean)


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


def _normalize_agent_pid(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _reclaim_kill_confirm(record: RunRecord) -> str | None:
    """Prove-and-kill one reclaimed run's recorded backend process.

    Pure OS work — identity probe, process-group kill, and the bounded
    gone-confirm poll — with no registry access, so callers can run it
    inside ``asyncio.to_thread`` without breaking the registry's
    thread-affine SQLite connection. Returns the outcome label
    ("killed" / "not_found" / "identity_mismatch"), or ``None`` when the
    incarnation could not be proven and the caller must NOT finalize.
    """
    pid = record.backend_agent_pid
    if pid is not None and pid <= 0:
        log.warning(
            "reclaim_invalid_orphan_agent_pid",
            issue_id=record.issue_id,
            identifier=record.identifier,
            pid=pid,
        )
        return None
    if pid is None:
        return "no_backend_pid"
    expected_identity = record.backend_process_identity
    current_identity = process_identity(pid)
    group_exists = process_group_exists(pid)
    if current_identity is None:
        if group_exists is not False:
            log.warning(
                "reclaim_backend_identity_ambiguous",
                issue_id=record.issue_id,
                identifier=record.identifier,
                pid=pid,
            )
            return None
        outcome = "not_found"
    elif expected_identity is None:
        # Pre-v8 or failed identity capture: never signal a reusable
        # numeric pid without proving it is the recorded incarnation.
        log.warning(
            "reclaim_backend_identity_missing",
            issue_id=record.issue_id,
            identifier=record.identifier,
            pid=pid,
        )
        return None
    elif current_identity != expected_identity:
        # The recorded process incarnation is gone and this pid was
        # reused. Do not signal the unrelated replacement.
        outcome = "identity_mismatch"
    else:
        try:
            killed = kill_process_group(pid)
        except Exception as exc:
            log.warning(
                "reclaim_killed_orphan_agent",
                issue_id=record.issue_id,
                identifier=record.identifier,
                pid=pid,
                outcome=f"error: {type(exc).__name__}",
            )
            return None
        if killed:
            deadline = time.monotonic() + 1.0
            confirmed_gone = process_group_exists(pid) is False
            while not confirmed_gone and time.monotonic() < deadline:
                time.sleep(0.05)
                confirmed_gone = process_group_exists(pid) is False
            if not confirmed_gone:
                log.warning(
                    "reclaim_killed_orphan_agent",
                    issue_id=record.issue_id,
                    identifier=record.identifier,
                    pid=pid,
                    outcome="unconfirmed",
                )
                return None
            outcome = "killed"
        elif process_group_exists(pid) is False:
            outcome = "not_found"
        else:
            log.warning(
                "reclaim_killed_orphan_agent",
                issue_id=record.issue_id,
                identifier=record.identifier,
                pid=pid,
                outcome="ambiguous",
            )
            return None
    log.warning(
        "reclaim_killed_orphan_agent",
        issue_id=record.issue_id,
        identifier=record.identifier,
        pid=pid,
        outcome=outcome,
    )
    return outcome


def _backend_agent_pid(backend: AgentBackend) -> int | None:
    return _normalize_agent_pid(getattr(backend, "pid", None))


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


def _run_record_payload(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "issue_id": record.issue_id,
        "identifier": record.identifier,
        "title": record.title,
        "state": record.state,
        "attempt": record.attempt,
        "attempt_kind": record.attempt_kind,
        "agent_kind": record.agent_kind,
        "status": record.status,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "workspace_path": str(record.workspace_path) if record.workspace_path else None,
        "branch_name": record.branch_name or f"{SYMPHONY_BRANCH_PREFIX}{record.identifier}",
        "commit_sha": record.commit_sha,
        "continued_from_run_id": record.continued_from_run_id,
        "checkpoint": (
            {
                "state": record.checkpoint_state,
                "turn": record.checkpoint_turn,
                "checkpointed_at": (
                    record.checkpointed_at.isoformat()
                    if record.checkpointed_at is not None
                    else None
                ),
            }
            if record.checkpoint_state is not None
            and record.checkpoint_turn is not None
            else None
        ),
        "tokens": {
            "input": record.input_tokens,
            "cache": record.cache_input_tokens,
            "output": record.output_tokens,
            "total": record.total_tokens,
        },
        "failure_class": record.failure_class,
        "failure_message": record.failure_message,
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


def _blocked_rca_work_state(cfg: ServiceConfig) -> str:
    for state in cfg.tracker.active_states:
        if normalize_state(state) == "in progress":
            return state
    return cfg.tracker.active_states[0] if cfg.tracker.active_states else "Todo"


def _blocked_source_reopen_state(cfg: ServiceConfig) -> str:
    for state in cfg.tracker.active_states:
        if normalize_state(state) == "todo":
            return state
    return cfg.tracker.active_states[0] if cfg.tracker.active_states else "Todo"


def _blocked_rca_labels(issue: Issue) -> list[str]:
    labels: list[str] = []
    for label in ("blocked-fix", f"source-{issue.identifier.lower()}"):
        normalized = re.sub(r"[^a-z0-9_.-]+", "-", label.lower()).strip("-")
        if normalized and normalized not in labels:
            labels.append(normalized)
    for label in issue.labels:
        if label not in labels:
            labels.append(label)
    return labels


def _has_app_release_label(issue: Issue) -> bool:
    return any(label.strip().lower() == "app-release" for label in issue.labels)


_APP_RELEASE_FILE_TRACKER_ONLY = (
    "app-release verifier execution requires tracker.kind=file; remote labeled "
    "tickets are refused before agent execution"
)


def _blocked_rca_identifier_prefix(issue: Issue) -> str:
    source = re.sub(r"[^A-Za-z0-9]+", "-", issue.identifier).strip("-")
    return f"FIX-{source or 'SOURCE'}"


_BLOCKED_RCA_HEADING_RE = re.compile(
    r"^##\s+Blocked\s+(?:Fix|RCA)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BLOCKED_RCA_RESOLVED_HEADING_RE = re.compile(
    r"^##\s+(?:Blocked\s+(?:Fix|RCA)\s+Resolved|(?:Fix|RCA)\s+Resolution)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BLOCKED_RCA_HOST_RESOLVED_HEADING_RE = re.compile(
    r"^##\s+Blocked\s+(?:Fix|RCA)\s+Resolved\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BLOCKED_RCA_SOURCE_IDENTIFIER_RE = re.compile(
    r"^-\s*Identifier:\s*`(?P<identifier>[^`]+)`\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BLOCKED_RCA_TITLE_RE = re.compile(
    r"^(?:RCA\s+unblock|Fix\s+and\s+unblock)\s+(?P<identifier>[^:]+):",
    re.IGNORECASE,
)
_BLOCKED_RCA_OPERATOR_BLOCKER_RE = re.compile(
    r"^##\s+((?:Fix|RCA)\s+Blocker|Operator\s+Action|Intervention\s+Required)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HUMAN_REVIEW_INTERVENTION_RE = re.compile(
    r"^(##\s+(Operator\s+Action|Intervention\s+Required|History\s+Failure|"
    r"Merge\s+Missing)|###\s+Intervention\s+Required)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HUMAN_REVIEW_DO_NOT_CONFIRM_RE = re.compile(
    r"^\s*`?Do\s+not\s+confirm\b",
    re.IGNORECASE | re.MULTILINE,
)
_HUMAN_REVIEW_CONFIRM_DONE_RE = re.compile(
    r"^\s*`?Confirm\s+Done`?\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HUMAN_REVIEW_COMPLETION_RE = re.compile(
    r"^##\s+(As-Is\s*->\s*To-Be\s+Report|Unblock\s+Note)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HUMAN_REVIEW_MERGE_FAILURE_RE = re.compile(
    r"^##\s+Merge\s+Failure\s*$",
    re.IGNORECASE | re.MULTILINE,
)


_HISTORY_FAILURE_HEADING_RE = re.compile(
    r"^##\s+History\s+Failure\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HISTORY_RECOVERY_HEADING_RE = re.compile(
    r"^##\s+History\s+Recovery\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_H2_RE = re.compile(r"^##\s+", re.MULTILINE)


def _markdown_section(description: str, heading_re: re.Pattern[str]) -> str:
    """Body under the first heading matching ``heading_re``, up to the next `## `.

    Scoping the read to one section matters for failure classification: a
    ticket description also carries QA logs and prior-run notes, and matching
    a phrase like `permission denied` anywhere in the body would mislabel
    unrelated tickets.
    """
    match = heading_re.search(description or "")
    if match is None:
        return ""
    rest = description[match.end() :]
    nxt = _NEXT_H2_RE.search(rest)
    return (rest[: nxt.start()] if nxt else rest).strip()


def _blocked_rca_already_requested(issue: Issue) -> bool:
    """Whether the current Blocked episode already requested an RCA.

    Resolution notes close the preceding request.  A source that later blocks
    again is a new episode and may open a new RCA, provided no RCA for that
    source is currently active on the board.
    """
    description = issue.description or ""
    requested = list(_BLOCKED_RCA_HEADING_RE.finditer(description))
    if not requested:
        return False
    resolved = list(_BLOCKED_RCA_RESOLVED_HEADING_RE.finditer(description))
    return not resolved or requested[-1].start() > resolved[-1].start()


def _is_blocked_rca_ticket(issue: Issue) -> bool:
    return any(
        label.lower() in {"blocked-fix", "blocked-rca"} for label in issue.labels
    )


def _is_blocked_fix_ticket(issue: Issue) -> bool:
    return any(label.lower() == "blocked-fix" for label in issue.labels)


def _looks_like_blocked_rca_ticket(issue: Issue) -> bool:
    if _is_blocked_rca_ticket(issue):
        return True
    return bool(_BLOCKED_RCA_TITLE_RE.match(issue.title or ""))


def _blocked_rca_source_identifier(issue: Issue) -> str | None:
    description = issue.description or ""
    match = _BLOCKED_RCA_SOURCE_IDENTIFIER_RE.search(description)
    if match is not None:
        return match.group("identifier").strip()
    match = _BLOCKED_RCA_TITLE_RE.match(issue.title or "")
    if match is not None:
        return match.group("identifier").strip()
    for label in issue.labels:
        normalized = label.lower()
        if normalized.startswith("source-"):
            return label[len("source-") :].strip()
    return None


def _blocked_rca_resolution_already_recorded(issue: Issue) -> bool:
    return bool(_BLOCKED_RCA_RESOLVED_HEADING_RE.search(issue.description or ""))


def _blocked_rca_current_episode_resolved(issue: Issue) -> bool:
    body = issue.description or ""
    resolutions = list(_BLOCKED_RCA_RESOLVED_HEADING_RE.finditer(body))
    if not resolutions:
        return False
    requests = list(_BLOCKED_RCA_HEADING_RE.finditer(body))
    return not requests or resolutions[-1].start() > requests[-1].start()


def _blocked_rca_requires_operator_intervention(issue: Issue) -> bool:
    body = issue.description or ""
    blockers = list(_BLOCKED_RCA_OPERATOR_BLOCKER_RE.finditer(body))
    if not blockers:
        return False
    resolutions = list(_BLOCKED_RCA_RESOLVED_HEADING_RE.finditer(body))
    return not resolutions or blockers[-1].start() > resolutions[-1].start()


def _human_review_done_state(cfg: ServiceConfig) -> str | None:
    for state in cfg.tracker.terminal_states:
        if normalize_state(state).strip() == "done":
            return state
    return None


def _human_review_requires_operator_intervention(issue: Issue) -> bool:
    body = issue.description or ""
    return bool(
        _blocked_rca_requires_operator_intervention(issue)
        or _HUMAN_REVIEW_INTERVENTION_RE.search(body)
        or _HUMAN_REVIEW_DO_NOT_CONFIRM_RE.search(body)
    )


def _legacy_human_review_is_done(issue: Issue) -> bool:
    if normalize_state(issue.state).strip() != "human review":
        return False
    if _looks_like_blocked_rca_ticket(issue):
        return False
    body = issue.description or ""
    if not body or _human_review_requires_operator_intervention(issue):
        return False
    if _HUMAN_REVIEW_MERGE_FAILURE_RE.search(body) and not re.search(
        r"^##\s+Unblock\s+Note\s*$",
        body,
        re.IGNORECASE | re.MULTILINE,
    ):
        return False
    return bool(
        _HUMAN_REVIEW_CONFIRM_DONE_RE.search(body)
        or _HUMAN_REVIEW_COMPLETION_RE.search(body)
    )


def _blocked_rca_description(
    issue: Issue,
    *,
    reopen_state: str,
) -> str:
    return (
        "## Goal\n\n"
        f"Make `{issue.identifier}` actionable again and remove the verified blocker "
        "so automated delivery can continue.\n\n"
        "## Source Ticket\n\n"
        f"- Identifier: `{issue.identifier}`\n"
        f"- Title: {issue.title}\n"
        f"- Current state: `{issue.state}`\n"
        f"- Reopen target after proven fix: `{reopen_state}`\n\n"
        "## Required Sequence\n\n"
        f"1. Read the source ticket `{issue.identifier}` first, including its title, "
        "request, description, acceptance criteria, and latest `## Blocker`, "
        "`## Budget Exceeded`, `## QA Failure`, `## Review Findings`, or "
        "legacy `## Blocked RCA` sections.\n"
        "2. Compare the requested outcome and acceptance criteria with the failure "
        "evidence. Ambiguity in the request or acceptance criteria is itself a blocker; "
        "do not merely restate it.\n"
        f"3. When instructions are vague, incomplete, or not testable, append "
        f"`## Clarified Request` to `{issue.identifier}` with the intended outcome, "
        "in-scope work, non-goals, constraints, assumptions, concrete, testable "
        "acceptance criteria, and exact verification commands or artifacts. Preserve "
        "the operator's intent; do not invent consequential product decisions.\n"
        "4. Resolve any code, configuration, environment, or process defect that is "
        "safe for an agent to change, then verify the fix with concrete commands or "
        "artifacts. If clarification is the only blocker, the improved source "
        "instructions are the fix.\n"
        f"5. Only after the blocker is resolved and the source ticket is actionable, "
        f"append `## Fix Resolution` to both `{issue.identifier}` and this fix ticket, "
        f"then move that source ticket to `{reopen_state}`. Do not move the fix ticket "
        "to Done before both updates are persisted.\n"
        "6. Do not skip the source ticket's normal workflow. Once it is back in Todo, "
        "it must pass through the configured Todo/In Progress/Verify/Document review "
        "path like any other ticket.\n"
        "7. If the blocker requires credentials, destructive operations, external "
        "approval, or a consequential product decision, leave the source ticket "
        "Blocked and append `## Fix Blocker` with the exact operator action or "
        "decision required.\n\n"
        "## Done Evidence\n\n"
        "- Root cause category and evidence.\n"
        "- Source instructions changed: yes/no, with the resulting acceptance criteria "
        "or the reason no clarification was needed.\n"
        "- Fix and verification evidence, or exact operator action required.\n"
        f"- Final state decision for `{issue.identifier}`.\n"
    )


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
        self._lease_blocked: dict[str, str] = {}
        self._blocked_rca_source_ids: set[str] = set()
        # Serialize the board check + create sequence.  The in-memory source
        # set is only a cache; concurrent manual/automatic recovery requests
        # must re-check the persisted board before either creates an RCA.
        self._blocked_rca_creation_lock = asyncio.Lock()
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
            killed: bool | None = None
            agent_pgid = _normalize_agent_pid(entry.agent_pgid)
            if agent_pgid is not None:
                try:
                    # win32 kill_process_group shells out to `taskkill /T`;
                    # keep that subprocess off the event loop so stop()
                    # drains never stall the service on a slow kill. The
                    # recorded identity fingerprint gates pid reuse.
                    killed = await asyncio.to_thread(
                        kill_process_group,
                        agent_pgid,
                        identity=self._agent_pid_identity(entry),
                    )
                except Exception as exc:
                    log.warning(
                        "worker_process_kill_failed_on_stop",
                        issue_id=issue_id,
                        agent_kind=self._entry_agent_kind(entry),
                        pid=agent_pgid,
                        error=str(exc),
                    )
            log.warning(
                "worker_task_abandoned_on_stop",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                agent_kind=self._entry_agent_kind(entry),
                pid=agent_pgid,
                killed=killed,
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

    def _open_run_registry(self, cfg: ServiceConfig) -> tuple[RunRegistry | None, bool]:
        """Open (or reuse) the run registry connection.

        Returns ``(registry, fresh)``. ``fresh`` is True only when a NEW
        connection was opened (callers run reclaim + open-maintenance
        then); an existing path-matched registry returns ``(registry,
        False)`` so maintenance is skipped, mirroring the historical
        early return. Open failures log, bump the error counters, and
        return ``(None, False)``.
        """
        self._run_registry_initialized = True
        path = registry_path_for_workflow(cfg.workflow_path)
        if self._run_registry is not None and self._run_registry.path == path:
            return self._run_registry, False
        if self._run_registry is not None:
            self._run_registry.close()
        try:
            self._run_registry = RunRegistry(path)
        except Exception as exc:
            self._run_registry = None
            self._registry_error_count += 1
            self._last_registry_error = f"open: {exc}"
            log.error("run_registry_open_failed", path=str(path), error=str(exc))
            return None, False
        return self._run_registry, True

    def _finish_run_registry_open(
        self, registry: RunRegistry, cfg: ServiceConfig, path: Path
    ) -> None:
        """Post-open maintenance: expire stale leases, rehydrate flags."""
        expired = self._registry_guard("expire_stale", registry.expire_stale, 0)
        if expired:
            log.info("run_leases_expired_on_start", count=expired, path=str(path))
        flags = self._registry_guard("list_issue_flags", registry.list_issue_flags, [])
        self._rehydrate_issue_flags(flags, cfg=cfg, registry=registry)

    def _ensure_run_registry(self, cfg: ServiceConfig) -> None:
        """Open the registry and reclaim dead-owner leases (blocking form).

        The per-record kill+confirm (taskkill / SIGKILL plus the bounded
        gone-confirm poll) runs inline on the calling thread, completing
        before this returns. Event-loop callers must use
        `_ensure_run_registry_async` instead so the OS kill never stalls
        the tick loop.
        """
        registry, fresh = self._open_run_registry(cfg)
        if not fresh or registry is None:
            return
        self._reclaim_dead_owner_runs(registry, path=registry.path)
        self._finish_run_registry_open(registry, cfg, registry.path)

    async def _ensure_run_registry_async(self, cfg: ServiceConfig) -> None:
        """Event-loop form of `_ensure_run_registry`.

        Identical semantics — open, reclaim dead owners (kill+confirm
        completed before return, 'reclaiming' fence observable mid-kill),
        expire, rehydrate — except each record's blocking kill+confirm
        runs in a worker thread, so the tick loop keeps servicing ticks
        while a slow taskkill drains.
        """
        registry, fresh = self._open_run_registry(cfg)
        if not fresh or registry is None:
            return
        await self._reclaim_dead_owner_runs_async(registry, path=registry.path)
        self._finish_run_registry_open(registry, cfg, registry.path)

    def _ensure_run_registry_open_only(self, cfg: ServiceConfig) -> None:
        """Open + maintenance for sync callers that must never block.

        Same as `_ensure_run_registry` minus the OS-level dead-owner
        reclaim: the release-gate fallback runs on the event loop and a
        taskkill there would stall ticks. Dead-owner leases are still
        honored (they block acquisition) and reclaimed by the next tick's
        off-loop pass, so correctness never depends on this side effect.
        """
        registry, fresh = self._open_run_registry(cfg)
        if not fresh or registry is None:
            return
        self._finish_run_registry_open(registry, cfg, registry.path)

    def _reclaim_dead_owner_runs(
        self, registry: RunRegistry, *, path: Path | None = None
    ) -> None:
        reclaimed = self._registry_guard(
            "reclaim_dead_owner", registry.reclaim_dead_owner_leases, []
        )
        if not reclaimed:
            return
        finalized = [
            record
            for record in reclaimed
            if self._reap_and_finalize_reclaimed_run(registry, record)
        ]
        log.info(
            "run_leases_reclaimed_dead_owner",
            count=len(finalized),
            pending_count=len(reclaimed) - len(finalized),
            identifiers=[record.identifier for record in finalized],
            path=str(path or registry.path),
        )

    async def _reclaim_dead_owner_runs_async(
        self, registry: RunRegistry, *, path: Path | None = None
    ) -> None:
        """`_reclaim_dead_owner_runs` with each kill+confirm off the loop."""
        reclaimed = self._registry_guard(
            "reclaim_dead_owner", registry.reclaim_dead_owner_leases, []
        )
        if not reclaimed:
            return
        finalized = []
        for record in reclaimed:
            if await self._reap_and_finalize_reclaimed_run_async(registry, record):
                finalized.append(record)
        log.info(
            "run_leases_reclaimed_dead_owner",
            count=len(finalized),
            pending_count=len(reclaimed) - len(finalized),
            identifiers=[record.identifier for record in finalized],
            path=str(path or registry.path),
        )

    def _release_registry_required(self, cfg: ServiceConfig) -> RunRegistry:
        """Return the release authority store or fail closed.

        Ordinary lease bookkeeping intentionally degrades when SQLite is
        unavailable. Application release authority cannot: a missing read
        must never turn a pending verifier or finalizer into a normal ticket.

        Opens without the OS-level reclaim (see
        `_ensure_run_registry_open_only`) — this fallback can run on the
        event loop, and dead-owner cleanup is covered by the per-tick
        off-loop reclaim pass.
        """
        self._ensure_run_registry_open_only(cfg)
        registry = self._run_registry
        if registry is None:
            raise SymphonyError(
                "application release authority registry is unavailable",
                workflow=str(cfg.workflow_path),
            )
        return registry

    def _release_registry_call(
        self,
        cfg: ServiceConfig,
        op: str,
        fn: Callable[[RunRegistry], Any],
    ) -> Any:
        registry = self._release_registry_required(cfg)
        try:
            result = fn(registry)
        except Exception as exc:
            self._registry_error_count += 1
            self._last_registry_error = f"release_{op}: {exc}"
            log.error("release_registry_error", op=op, error=str(exc))
            raise SymphonyError(
                "application release authority operation failed",
                operation=op,
                error=str(exc),
            ) from exc
        self._last_registry_error = None
        return result

    @staticmethod
    def _pending_release_gate(
        *, issue: Issue, finalizer: str, contract_sha256: str
    ) -> ReleaseGate:
        return ReleaseGate(
            finalizer_identifier=finalizer,
            verifier_issue_id=issue.id,
            verifier_identifier=issue.identifier,
            expected_contract_sha256=contract_sha256,
            cycle_fingerprint=_initial_release_gate_fingerprint(
                verifier_identifier=issue.identifier,
                finalizer_identifier=finalizer,
                contract_sha256=contract_sha256,
            ),
            approved_fingerprint=None,
            status="pending",
            target_branch=None,
            approved_target_sha=None,
            verifier_run_id=None,
            finalizer_run_id=None,
            finalizer_completed_at=None,
            finalizer_completion_token=None,
            updated_at=datetime.now(timezone.utc),
        )

    def _persist_pending_release_gate(
        self,
        *,
        cfg: ServiceConfig,
        gate: ReleaseGate,
        operation: str,
        invalidating_finalizer_run_id: str | None = None,
    ) -> ReleaseGate:
        """Replace approval with PENDING and prove the authoritative tuple."""
        written = cast(
            ReleaseGate,
            self._release_registry_call(
                cfg,
                operation,
                lambda registry: registry.replace_pending_release_gate(
                    gate,
                    invalidating_finalizer_run_id=(invalidating_finalizer_run_id),
                ),
            ),
        )
        persisted = cast(
            ReleaseGate | None,
            self._release_registry_call(
                cfg,
                f"{operation}_readback",
                lambda registry: registry.get_release_gate(gate.finalizer_identifier),
            ),
        )
        expected = (
            gate.finalizer_identifier,
            gate.verifier_issue_id,
            gate.verifier_identifier,
            gate.expected_contract_sha256,
            gate.cycle_fingerprint,
            written.generation,
            "pending",
        )
        actual = (
            (
                persisted.finalizer_identifier,
                persisted.verifier_issue_id,
                persisted.verifier_identifier,
                persisted.expected_contract_sha256,
                persisted.cycle_fingerprint,
                persisted.generation,
                persisted.status,
            )
            if persisted is not None
            else None
        )
        if actual != expected:
            raise SymphonyError(
                "application release authority was not durably persisted",
                verifier=gate.verifier_identifier,
                finalizer=gate.finalizer_identifier,
            )
        if not written.generation:
            raise SymphonyError(
                "application release authority lacks a durable cycle generation",
                verifier=gate.verifier_identifier,
            )
        return cast(ReleaseGate, persisted)

    def _create_initial_release_gate(
        self, cfg: ServiceConfig, issue: Issue
    ) -> tuple[Issue, ReleaseGate]:
        if not _has_active_release_verify_lane(cfg) or not _has_release_finalizer_lane(
            cfg
        ):
            raise SymphonyError(
                "app-release requires active Verify and finalizer lanes before dispatch"
            )
        identity = resolve_target_release_identity(
            repository_root=cfg.workflow_path.parent,
            configured_target_branch=cfg.agent.auto_merge_target_branch,
        )
        if identity.errors:
            raise SymphonyError(
                "cannot bind initial application release verifier",
                errors=list(identity.errors),
            )
        finalizer = self._tracker_call_fetch_issue_full_by_id(
            cfg, identity.finalizer_ticket
        )
        if finalizer is None or not _is_release_finalizer(finalizer):
            raise SymphonyError(
                "release contract finalizer is missing or lacks app-release-finalizer",
                verifier=issue.identifier,
                finalizer=identity.finalizer_ticket,
            )
        blocker_ids = {
            blocker.identifier or blocker.id
            for blocker in finalizer.blocked_by
            if blocker.identifier or blocker.id
        }
        if issue.identifier not in blocker_ids:
            raise SymphonyError(
                "release finalizer must be blocked by its verifier before dispatch",
                verifier=issue.identifier,
                finalizer=identity.finalizer_ticket,
            )
        gate = self._pending_release_gate(
            issue=issue,
            finalizer=identity.finalizer_ticket,
            contract_sha256=identity.contract_sha256,
        )
        persisted = self._persist_pending_release_gate(
            cfg=cfg,
            gate=gate,
            operation="replace_initial_pending_gate",
        )
        if normalize_state(finalizer.state) in {
            normalize_state(state) for state in cfg.tracker.terminal_states
        }:
            ReleaseCycleService(cfg).reopen_after_target_change(
                finalizer=finalizer,
                gate=persisted,
                expected_contract_sha256=persisted.expected_contract_sha256,
                reason="initial release verification has not yet been approved",
                finalizer_state=next(
                    (
                        state
                        for state in reversed(cfg.tracker.active_states)
                        if normalize_state(state) != "verify"
                    ),
                    None,
                ),
            )
        restored = ReleaseCycleService(cfg).restore_verifier_gate_labels(
            issue=issue,
            gate=persisted,
            verifier_state=_release_verifier_state(cfg),
        )
        return restored, persisted

    def _reopen_stale_release_gate(
        self,
        *,
        cfg: ServiceConfig,
        finalizer: Issue,
        gate: ReleaseGate,
        reason: str,
        current_contract_sha256: str = "",
        finalizer_state: str | None = None,
        invalidating_finalizer_run_id: str | None = None,
    ) -> None:
        expected_hash = current_contract_sha256 or gate.expected_contract_sha256
        pending = replace(
            gate,
            expected_contract_sha256=expected_hash,
            cycle_fingerprint=_initial_release_gate_fingerprint(
                verifier_identifier=gate.verifier_identifier,
                finalizer_identifier=gate.finalizer_identifier,
                contract_sha256=expected_hash,
            ),
            approved_fingerprint=None,
            status="pending",
            target_branch=None,
            approved_target_sha=None,
            verifier_run_id=None,
            updated_at=datetime.now(timezone.utc),
        )
        pending = self._persist_pending_release_gate(
            cfg=cfg,
            gate=pending,
            operation="invalidate_stale_approval",
            invalidating_finalizer_run_id=invalidating_finalizer_run_id,
        )
        ReleaseCycleService(cfg).reopen_after_target_change(
            finalizer=finalizer,
            gate=pending,
            expected_contract_sha256=expected_hash,
            reason=reason,
            finalizer_state=finalizer_state,
        )

    def _guard_release_finalizer(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        gate: ReleaseGate,
        rewind_state: str | None = None,
        expected_run_id: str | None = None,
        allow_active_run: bool = True,
        require_run_authority: bool = False,
    ) -> Issue:
        persisted_issue = self._tracker_call_fetch_issue_full_by_id(
            cfg, issue.identifier
        )
        if persisted_issue is None:
            raise SymphonyError(
                "application release finalizer could not be read",
                finalizer=issue.identifier,
            )
        issue = persisted_issue
        terminal_states = {
            normalize_state(state) for state in cfg.tracker.terminal_states
        }
        if normalize_state(
            issue.state
        ) in terminal_states and not _is_release_success_state(cfg, issue.state):
            raise SymphonyError(
                "application release finalizer is in a non-success terminal state",
                finalizer=issue.identifier,
                state=issue.state,
            )
        if gate.status != "approved":
            raise SymphonyError(
                "application release finalizer is waiting for GREEN verification",
                finalizer=issue.identifier,
                verifier=gate.verifier_identifier,
                status=gate.status,
            )
        blocker_ids = {
            blocker.identifier or blocker.id
            for blocker in issue.blocked_by
            if blocker.identifier or blocker.id
        }
        if gate.verifier_identifier not in blocker_ids:
            raise SymphonyError(
                "application release finalizer is not bound to its approved verifier",
                finalizer=issue.identifier,
                verifier=gate.verifier_identifier,
            )
        verifier = self._tracker_call_fetch_issue_full_by_id(
            cfg, gate.verifier_identifier
        )
        if (
            verifier is None
            or verifier.id != gate.verifier_issue_id
            or not _is_release_success_state(cfg, verifier.state)
        ):
            raise SymphonyError(
                "application release finalizer verifier is not successfully terminal",
                finalizer=issue.identifier,
                verifier=gate.verifier_identifier,
                verifier_state=verifier.state if verifier is not None else "missing",
            )
        peer_active = bool(
            self._release_registry_call(
                cfg,
                "check_verifier_lease",
                lambda registry: registry.has_active_lease(gate.verifier_issue_id),
            )
        )
        if peer_active:
            raise SymphonyError(
                "application release verifier is still in flight",
                verifier=gate.verifier_identifier,
            )
        if require_run_authority:
            if expected_run_id is not None and gate.finalizer_run_id != expected_run_id:
                raise SymphonyError(
                    "application release finalizer run is not bound to this gate cycle",
                    finalizer=issue.identifier,
                    expected_run_id=expected_run_id,
                    bound_run_id=gate.finalizer_run_id or "missing",
                )
            authorized = bool(
                self._release_registry_call(
                    cfg,
                    "check_finalizer_run_authority",
                    lambda registry: registry.release_finalizer_run_is_authorized(
                        gate=gate,
                        finalizer_issue_id=issue.id,
                        allow_active=allow_active_run,
                    ),
                )
            )
            if not authorized:
                raise SymphonyError(
                    "application release finalizer lacks exact run completion authority",
                    finalizer=issue.identifier,
                    run_id=gate.finalizer_run_id or "missing",
                )
        identity = resolve_target_release_identity(
            repository_root=cfg.workflow_path.parent,
            configured_target_branch=cfg.agent.auto_merge_target_branch,
        )
        mismatches = list(identity.errors)
        if gate.target_branch != identity.target_branch:
            mismatches.append("approved target branch changed")
        if gate.approved_target_sha != identity.target_sha:
            mismatches.append("approved target SHA changed")
        if gate.expected_contract_sha256 != identity.contract_sha256:
            mismatches.append("approved release contract changed")
        if gate.finalizer_identifier != identity.finalizer_ticket:
            mismatches.append("release contract finalizer changed")
        if gate.finalizer_completed_at is not None:
            completion_token = _release_ticket_version_token(cfg, issue.identifier)
            if not gate.finalizer_completion_token:
                mismatches.append(
                    "completed finalizer lacks a host-observed board transition token"
                )
            elif gate.finalizer_completion_token != completion_token:
                mismatches.append(
                    "completed finalizer board transition changed after delivery"
                )
        if mismatches:
            reason = "; ".join(dict.fromkeys(mismatches))
            self._reopen_stale_release_gate(
                cfg=cfg,
                finalizer=issue,
                gate=gate,
                reason=reason,
                current_contract_sha256=identity.contract_sha256,
                finalizer_state=rewind_state,
                invalidating_finalizer_run_id=(
                    expected_run_id if require_run_authority else None
                ),
            )
            raise SymphonyError(
                "application release approval became stale; fresh verification required",
                finalizer=issue.identifier,
                reason=reason,
            )
        return issue

    def _mark_release_finalizer_completed(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        gate: ReleaseGate,
        completion_token: str,
        rewind_state: str | None,
    ) -> ReleaseGate:
        current_issue = self._tracker_call_fetch_issue_full_by_id(cfg, issue.identifier)
        current_token = _release_ticket_version_token(cfg, issue.identifier)
        if (
            current_issue is None
            or current_issue.id != issue.id
            or not _is_release_success_state(cfg, current_issue.state)
            or current_token != completion_token
        ):
            self._invalidate_release_finalizer_version(
                cfg=cfg,
                issue=current_issue or issue,
                gate=gate,
                rewind_state=rewind_state,
                reason=(
                    "finalizer ticket changed between terminal approval and "
                    "completion persistence"
                ),
            )
            raise SymphonyError(
                "application release finalizer changed before completion proof",
                finalizer=issue.identifier,
            )
        completed = bool(
            self._release_registry_call(
                cfg,
                "mark_finalizer_completed",
                lambda registry: registry.mark_release_finalizer_completed(
                    gate=gate,
                    finalizer_issue_id=issue.id,
                    completion_token=completion_token,
                ),
            )
        )
        persisted = cast(
            ReleaseGate | None,
            self._release_registry_call(
                cfg,
                "read_finalizer_completion",
                lambda registry: registry.get_release_gate(gate.finalizer_identifier),
            ),
        )
        if (
            not completed
            or persisted is None
            or persisted.generation != gate.generation
            or persisted.finalizer_run_id != gate.finalizer_run_id
            or persisted.finalizer_completed_at is None
            or persisted.finalizer_completion_token != completion_token
        ):
            raise SymphonyError(
                "application release finalizer completion proof could not be persisted",
                finalizer=issue.identifier,
            )
        confirmed_issue = self._tracker_call_fetch_issue_full_by_id(
            cfg, issue.identifier
        )
        confirmed_token = _release_ticket_version_token(cfg, issue.identifier)
        if (
            confirmed_issue is None
            or confirmed_issue.id != issue.id
            or not _is_release_success_state(cfg, confirmed_issue.state)
            or confirmed_token != completion_token
        ):
            self._invalidate_release_finalizer_version(
                cfg=cfg,
                issue=confirmed_issue or issue,
                gate=persisted,
                rewind_state=rewind_state,
                reason=(
                    "finalizer ticket changed while completion proof was "
                    "being persisted"
                ),
            )
            raise SymphonyError(
                "application release finalizer changed during completion proof",
                finalizer=issue.identifier,
            )
        return persisted

    def _invalidate_release_finalizer_version(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        gate: ReleaseGate,
        rewind_state: str | None,
        reason: str,
    ) -> None:
        identity = resolve_target_release_identity(
            repository_root=cfg.workflow_path.parent,
            configured_target_branch=cfg.agent.auto_merge_target_branch,
        )
        self._reopen_stale_release_gate(
            cfg=cfg,
            finalizer=issue,
            gate=gate,
            reason=reason,
            current_contract_sha256=(
                identity.contract_sha256
                if not identity.errors
                else gate.expected_contract_sha256
            ),
            finalizer_state=rewind_state,
            invalidating_finalizer_run_id=gate.finalizer_run_id,
        )

    def _guard_release_finalizer_with_version(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        gate: ReleaseGate,
        rewind_state: str | None,
        expected_run_id: str | None,
        require_run_authority: bool,
    ) -> tuple[Issue, str]:
        """Guard one finalizer and pin the exact board version it approved."""
        before_token = _release_ticket_version_token(cfg, issue.identifier)
        guarded = self._guard_release_finalizer(
            cfg=cfg,
            issue=issue,
            gate=gate,
            rewind_state=rewind_state,
            expected_run_id=expected_run_id,
            require_run_authority=require_run_authority,
        )
        after_token = _release_ticket_version_token(cfg, guarded.identifier)
        if before_token != after_token:
            self._invalidate_release_finalizer_version(
                cfg=cfg,
                issue=guarded,
                gate=gate,
                rewind_state=rewind_state,
                reason=(
                    "finalizer ticket changed while terminal authority was "
                    "being checked"
                ),
            )
            raise SymphonyError(
                "application release finalizer changed during terminal guard",
                finalizer=guarded.identifier,
            )
        return guarded, after_token

    def _require_running_release_authority(
        self,
        *,
        cfg: ServiceConfig,
        entry: RunningEntry,
        workspace_path: Path | None = None,
    ) -> Issue:
        """Revalidate the exact host-owned gate generation for one live run."""
        if not (
            entry.known_app_release
            or entry.known_release_cycle_verifier
            or entry.known_app_release_finalizer
        ):
            return entry.issue
        if not entry.run_id or not entry.release_gate_finalizer:
            raise SymphonyError(
                "application release run lacks cached host authority",
                identifier=entry.issue.identifier,
            )
        gate = cast(
            ReleaseGate | None,
            self._release_registry_call(
                cfg,
                "revalidate_running_gate",
                lambda registry: registry.get_release_gate(
                    entry.release_gate_finalizer
                ),
            ),
        )
        if gate is None:
            raise SymphonyError(
                "application release gate disappeared during execution",
                identifier=entry.issue.identifier,
            )
        expected = (
            entry.release_gate_finalizer,
            entry.release_gate_expected_contract_sha256,
            entry.release_gate_cycle_fingerprint,
            entry.release_gate_generation,
        )
        actual = (
            gate.finalizer_identifier,
            gate.expected_contract_sha256,
            gate.cycle_fingerprint,
            gate.generation,
        )
        if actual != expected or not gate.generation:
            raise SymphonyError(
                "application release gate generation changed during execution",
                identifier=entry.issue.identifier,
            )
        if entry.known_app_release_finalizer:
            if gate.finalizer_run_id != entry.run_id:
                raise SymphonyError(
                    "application release finalizer lost its exact run binding",
                    finalizer=entry.issue.identifier,
                )
            guarded = self._guard_release_finalizer(
                cfg=cfg,
                issue=entry.issue,
                gate=gate,
                expected_run_id=entry.run_id,
                require_run_authority=True,
            )
            if workspace_path is not None:
                if not gate.approved_target_sha:
                    raise SymphonyError(
                        "application release finalizer has no approved target SHA"
                    )
                workspace_errors = release_workspace_target_errors(
                    workspace_root=workspace_path,
                    repository_root=cfg.workflow_path.parent,
                    target_sha=gate.approved_target_sha,
                    board_root=cfg.tracker.board_root,
                    allowed_roots=(PurePosixPath("docs") / entry.issue.identifier,),
                    role="finalizer",
                )
                if workspace_errors:
                    raise SymphonyError(
                        "application release finalizer workspace is stale",
                        errors=list(workspace_errors),
                    )
            return guarded
        if (
            gate.verifier_issue_id != entry.issue.id
            or gate.verifier_identifier != entry.issue.identifier
            or gate.verifier_run_id != entry.run_id
        ):
            raise SymphonyError(
                "application release verifier lost its exact run binding",
                verifier=entry.issue.identifier,
            )
        authorized = bool(
            self._release_registry_call(
                cfg,
                "check_verifier_run_authority",
                lambda registry: registry.release_verifier_run_is_authorized(
                    gate=gate,
                    verifier_issue_id=entry.issue.id,
                ),
            )
        )
        if not authorized:
            raise SymphonyError(
                "application release verifier run is no longer authorized",
                verifier=entry.issue.identifier,
            )
        return entry.issue

    def _require_release_transition_verifier_authority(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        entry: RunningEntry,
    ) -> None:
        """Fence transition writes behind this verifier's exact live run."""
        if not self._heartbeat_run_lease(issue.id, entry):
            raise _ReleaseTransitionAuthorityLost(
                "application release verifier lost its active run lease before "
                "transition enforcement",
                verifier=issue.identifier,
                run_id=entry.run_id,
            )
        try:
            self._require_running_release_authority(
                cfg=cfg,
                entry=entry,
            )
        except SymphonyError as exc:
            raise _ReleaseTransitionAuthorityLost(
                "application release verifier no longer owns the exact gate "
                "generation and role",
                verifier=issue.identifier,
                run_id=entry.run_id,
                reason=str(exc),
            ) from exc

    def _prepare_release_dispatch(
        self, issue: Issue, cfg: ServiceConfig
    ) -> _ReleaseDispatchAuthority:
        app_label = _has_app_release_label(issue)
        finalizer_label = _is_release_finalizer(issue)
        if cfg.tracker.kind != "file":
            if app_label or finalizer_label:
                raise SymphonyError(
                    _APP_RELEASE_FILE_TRACKER_ONLY,
                    tracker_kind=cfg.tracker.kind,
                )
            return _ReleaseDispatchAuthority(issue=issue)

        try:
            gate_for_verifier = cast(
                ReleaseGate | None,
                self._release_registry_call(
                    cfg,
                    "find_verifier_gate",
                    lambda registry: registry.get_release_gate_for_verifier(
                        issue.identifier
                    ),
                ),
            )
            gate_for_finalizer = cast(
                ReleaseGate | None,
                self._release_registry_call(
                    cfg,
                    "find_finalizer_gate",
                    lambda registry: registry.get_release_gate(issue.identifier),
                ),
            )
            evidence_identity = cast(
                ReleaseEvidenceIdentity | None,
                self._release_registry_call(
                    cfg,
                    "find_release_evidence_identity",
                    lambda registry: registry.get_release_evidence_identity(
                        issue.identifier
                    ),
                ),
            )
        except SymphonyError:
            identity = resolve_target_release_identity(
                repository_root=cfg.workflow_path.parent,
                configured_target_branch=cfg.agent.auto_merge_target_branch,
            )
            if app_label or finalizer_label or not identity.errors:
                raise
            return _ReleaseDispatchAuthority(issue=issue)

        if evidence_identity is not None and gate_for_verifier is None:
            if evidence_identity.retired:
                raise SymphonyError(
                    "historical release verifier is evidence-only and cannot be "
                    "redispatched",
                    verifier=issue.identifier,
                    finalizer=evidence_identity.finalizer_identifier,
                )
            raise SymphonyError(
                "current release verifier identity has no host-owned gate",
                verifier=issue.identifier,
                finalizer=evidence_identity.finalizer_identifier,
            )

        if (
            app_label
            or finalizer_label
            or gate_for_verifier is not None
            or gate_for_finalizer is not None
        ) and (
            not _has_active_release_verify_lane(cfg)
            or not _has_release_finalizer_lane(cfg)
        ):
            raise SymphonyError(
                "app-release requires active Verify and finalizer lanes before dispatch"
            )

        if gate_for_finalizer is not None or finalizer_label:
            if gate_for_finalizer is None:
                raise SymphonyError(
                    "application release finalizer has no host-owned authority",
                    finalizer=issue.identifier,
                )
            guarded = self._guard_release_finalizer(
                cfg=cfg,
                issue=issue,
                gate=gate_for_finalizer,
            )
            return _ReleaseDispatchAuthority(
                issue=guarded,
                gate=gate_for_finalizer,
                finalizer=True,
            )

        if gate_for_verifier is None and app_label:
            if normalize_state(issue.state) == "verify":
                issue, gate_for_verifier = self._create_initial_release_gate(cfg, issue)
            else:
                raise SymphonyError(
                    "app-release ticket outside Verify has no host-owned authority",
                    verifier=issue.identifier,
                    state=issue.state,
                )
        elif gate_for_verifier is not None:
            verifier_state: str | None = _release_verifier_state(cfg)
            if gate_for_verifier.status == "pending":
                pending_finalizer = self._tracker_call_fetch_issue_full_by_id(
                    cfg, gate_for_verifier.finalizer_identifier
                )
                if pending_finalizer is None:
                    raise SymphonyError(
                        "release finalizer disappeared while verifier was pending",
                        finalizer=gate_for_verifier.finalizer_identifier,
                    )
                if normalize_state(pending_finalizer.state) in {
                    normalize_state(state) for state in cfg.tracker.terminal_states
                }:
                    ReleaseCycleService(cfg).reopen_after_target_change(
                        finalizer=pending_finalizer,
                        gate=gate_for_verifier,
                        expected_contract_sha256=(
                            gate_for_verifier.expected_contract_sha256
                        ),
                        reason=(
                            "release finalizer became terminal before its verifier "
                            "received durable GREEN approval"
                        ),
                        finalizer_state=next(
                            (
                                state
                                for state in reversed(cfg.tracker.active_states)
                                if normalize_state(state) != "verify"
                            ),
                            None,
                        ),
                    )
            if gate_for_verifier.status == "approved" and normalize_state(
                issue.state
            ) in {normalize_state(state) for state in cfg.tracker.active_states}:
                finalizer = self._tracker_call_fetch_issue_full_by_id(
                    cfg, gate_for_verifier.finalizer_identifier
                )
                if finalizer is None:
                    raise SymphonyError(
                        "release finalizer disappeared while reopening verifier",
                        finalizer=gate_for_verifier.finalizer_identifier,
                    )
                self._reopen_stale_release_gate(
                    cfg=cfg,
                    finalizer=finalizer,
                    gate=gate_for_verifier,
                    reason=(
                        "approved verifier requires a new host-bound run after "
                        f"returning in active state {issue.state}"
                    ),
                    finalizer_state=next(
                        (
                            state
                            for state in reversed(cfg.tracker.active_states)
                            if normalize_state(state) != "verify"
                        ),
                        None,
                    ),
                )
                gate_for_verifier = cast(
                    ReleaseGate | None,
                    self._release_registry_call(
                        cfg,
                        "read_reopened_verifier_gate",
                        lambda registry: registry.get_release_gate_for_verifier(
                            issue.identifier
                        ),
                    ),
                )
                if gate_for_verifier is None:
                    raise SymphonyError(
                        "reopened release verifier authority disappeared",
                        verifier=issue.identifier,
                    )
            elif gate_for_verifier.status == "approved":
                verifier_state = None
            issue = ReleaseCycleService(cfg).restore_verifier_gate_labels(
                issue=issue,
                gate=gate_for_verifier,
                verifier_state=verifier_state,
            )

        app_release = app_label or gate_for_verifier is not None
        return _ReleaseDispatchAuthority(
            issue=issue,
            gate=gate_for_verifier,
            app_release=app_release,
            cycle_verifier=gate_for_verifier is not None,
        )

    def _reap_and_finalize_reclaimed_run(
        self, registry: RunRegistry, record: RunRecord
    ) -> bool:
        """Verify process incarnation, reap it, then release the SQLite fence.

        Blocking form for synchronous callers (tests, the sync ensure
        fallback): the OS-level kill+confirm runs inline on this thread.
        The registry finalize below stays on the caller's thread so the
        SQLite connection never crosses threads.
        """
        outcome = _reclaim_kill_confirm(record)
        if outcome is None:
            return False
        return bool(
            self._registry_guard(
                "finalize_reclaimed_lease",
                lambda: registry.finalize_reclaimed_lease(record.run_id),
                False,
            )
        )

    async def _reap_and_finalize_reclaimed_run_async(
        self, registry: RunRegistry, record: RunRecord
    ) -> bool:
        """Off-event-loop form of `_reap_and_finalize_reclaimed_run`.

        The blocking kill+confirm (taskkill + bounded gone-confirm poll)
        runs in a worker thread; the SQLite claim state and the finalize
        below stay on the event-loop thread, preserving the registry's
        thread-affine connection. The kill still completes before this
        coroutine returns, so the 'reclaiming' fence is observable
        mid-kill and finalized only afterwards.
        """
        outcome = await asyncio.to_thread(_reclaim_kill_confirm, record)
        if outcome is None:
            return False
        return bool(
            self._registry_guard(
                "finalize_reclaimed_lease",
                lambda: registry.finalize_reclaimed_lease(record.run_id),
                False,
            )
        )


    async def recent_runs(
        self,
        issue_id: str | None = None,
        limit: int = 50,
        *,
        query: str | None = None,
        status: str | None = None,
        agent: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        registry = self._run_registry
        if registry is None:
            cfg = self._workflow_state.current()
            if cfg is not None:
                # Async: the fallback open's dead-owner reclaim (taskkill)
                # must not block the web loop this handler runs on.
                await self._ensure_run_registry_async(cfg)
                registry = self._run_registry
        if registry is None:
            return [], "run registry unavailable"

        rows = self._registry_guard(
            "recent_runs",
            lambda: registry.recent_runs(
                issue_id=issue_id,
                limit=limit,
                query=query,
                status=status,
                agent=agent,
            ),
            None,
        )
        if rows is None:
            return [], self._last_registry_error
        return [_run_record_payload(row) for row in rows], None

    async def run_detail(self, run_id: str) -> tuple[dict[str, Any] | None, str | None]:
        registry = self._run_registry
        if registry is None:
            cfg = self._workflow_state.current()
            if cfg is not None:
                await self._ensure_run_registry_async(cfg)
                registry = self._run_registry
        if registry is None:
            return None, "run registry unavailable"
        try:
            return registry.run_detail(run_id), None
        except KeyError:
            return None, None
        except Exception as exc:
            self._registry_error_count += 1
            self._last_registry_error = f"run_detail: {exc}"
            return None, self._last_registry_error

    async def run_diagnostic(self, run_id: str) -> tuple[dict[str, Any] | None, str | None]:
        registry = self._run_registry
        if registry is None:
            cfg = self._workflow_state.current()
            if cfg is not None:
                await self._ensure_run_registry_async(cfg)
                registry = self._run_registry
        if registry is None:
            return None, "run registry unavailable"
        try:
            return registry.diagnostic_json(run_id), None
        except KeyError:
            return None, None
        except Exception as exc:
            self._registry_error_count += 1
            self._last_registry_error = f"run_diagnostic: {exc}"
            return None, self._last_registry_error

    def _append_run_event(
        self,
        entry: RunningEntry,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        registry = self._run_registry
        if registry is None or not entry.run_id:
            return
        self._registry_guard(
            "append_attempt_event",
            lambda: registry.append_attempt_event(
                run_id=entry.run_id,
                event_type=event_type,
                payload=payload,
            ),
            False,
        )

    def _release_verifier_handoff_is_durable(
        self,
        *,
        cfg: ServiceConfig,
        registry: RunRegistry,
        identity: ReleaseEvidenceIdentity,
    ) -> bool:
        """Prove that one retired verifier was replaced and durably relinked."""
        if (
            not identity.retired
            or identity.role != "verifier"
            or not identity.issue_id
            or not identity.identifier
            or not identity.finalizer_identifier
            or not identity.cycle_generation
            or registry.has_active_lease(identity.issue_id)
        ):
            return False
        gate = registry.get_release_gate(identity.finalizer_identifier)
        if (
            gate is None
            or not gate.generation
            or gate.generation == identity.cycle_generation
            or gate.verifier_issue_id == identity.issue_id
            or gate.verifier_identifier == identity.identifier
        ):
            return False
        current_identity = registry.get_release_evidence_identity_by_issue_id(
            gate.verifier_issue_id
        )
        if current_identity is None or (
            current_identity.issue_id,
            current_identity.identifier,
            current_identity.finalizer_identifier,
            current_identity.role,
            current_identity.cycle_generation,
            current_identity.retired,
        ) != (
            gate.verifier_issue_id,
            gate.verifier_identifier,
            gate.finalizer_identifier,
            "verifier",
            gate.generation,
            False,
        ):
            return False
        finalizer = self._tracker_call_fetch_issue_full_by_id(
            cfg, identity.finalizer_identifier
        )
        if finalizer is None or finalizer.identifier != identity.finalizer_identifier:
            return False
        blocker_identifiers = {
            blocker.identifier for blocker in finalizer.blocked_by if blocker.identifier
        }
        return (
            gate.verifier_identifier in blocker_identifiers
            and identity.identifier not in blocker_identifiers
        )

    def _rehydrate_issue_flags(
        self,
        flags: list[Any],
        *,
        cfg: ServiceConfig,
        registry: RunRegistry,
    ) -> None:
        self._persisted_retry_attempts.clear()
        for flag in flags:
            issue_id = flag.issue_id
            if flag.budget_exhausted:
                self._turn_budget_exhausted.add(issue_id)
            identity: ReleaseEvidenceIdentity | None = None
            handoff_complete = False
            if flag.retry_attempt is not None:
                try:
                    identity = registry.get_release_evidence_identity_by_issue_id(
                        issue_id
                    )
                    handoff_complete = bool(
                        identity is not None
                        and self._release_verifier_handoff_is_durable(
                            cfg=cfg,
                            registry=registry,
                            identity=identity,
                        )
                    )
                except Exception as exc:
                    # Recovery is an allow-list: unreadable authority keeps the
                    # existing retry/pause rather than forgiving it.
                    log.warning(
                        "historical_release_verifier_handoff_recovery_deferred",
                        issue_id=issue_id,
                        error=str(exc),
                    )
            if handoff_complete:
                assert identity is not None
                self._dispatch_state.cancel_pending_retry(issue_id)
                self._persisted_retry_attempts.pop(issue_id, None)
                self._paused_issue_ids.discard(issue_id)
                self._pause_reasons.pop(issue_id, None)
                self._clear_issue_flags(
                    issue_id,
                    retry_attempt=True,
                    paused=True,
                )
                log.info(
                    "historical_release_verifier_handoff_recovered",
                    issue_id=issue_id,
                    identifier=identity.identifier,
                    finalizer=identity.finalizer_identifier,
                    generation=identity.cycle_generation,
                )
                continue
            if flag.paused:
                pause_reason = str(flag.pause_reason) if flag.pause_reason else None
                if flag.retry_attempt is not None and _is_retryable_auto_pause_reason(
                    pause_reason
                ):
                    self._paused_issue_ids.discard(issue_id)
                    self._pause_reasons.pop(issue_id, None)
                    self._clear_issue_flags(issue_id, paused=True)
                    log.info(
                        "retryable_worker_pause_released",
                        issue_id=issue_id,
                        pause_reason=pause_reason,
                    )
                else:
                    self._paused_issue_ids.add(issue_id)
                    if pause_reason:
                        self._pause_reasons[issue_id] = pause_reason
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
        release_required = entry.release_authority_resolved and (
            entry.known_app_release
            or entry.known_release_cycle_verifier
            or entry.known_app_release_finalizer
        )
        current_cfg = self._workflow_state.current()
        continuation_authority_required = bool(
            current_cfg is not None and current_cfg.agent.crash_continuation
        )
        if registry is None or not entry.run_id:
            if release_required or (
                continuation_authority_required and self._run_registry_initialized
            ):
                entry.lease_lost = True
                log.error(
                    "run_lease_authority_unavailable",
                    issue_id=issue_id,
                    issue_identifier=entry.issue.identifier,
                    release_required=release_required,
                )
                return False
            return True
        if release_required:
            try:
                ok = registry.heartbeat(
                    issue_id=issue_id,
                    run_id=entry.run_id,
                    progress_at=progress,
                    backend_agent_pid=backend_agent_pid or entry.agent_pgid,
                )
            except Exception as exc:
                self._registry_error_count += 1
                self._last_registry_error = f"release_heartbeat: {exc}"
                entry.lease_lost = True
                log.error(
                    "release_registry_error",
                    op="heartbeat",
                    issue_id=issue_id,
                    error=str(exc),
                )
                return False
            self._last_registry_error = None
        elif continuation_authority_required:
            try:
                ok = registry.heartbeat(
                    issue_id=issue_id,
                    run_id=entry.run_id,
                    progress_at=progress,
                    backend_agent_pid=backend_agent_pid or entry.agent_pgid,
                )
            except Exception as exc:
                self._registry_error_count += 1
                self._last_registry_error = f"continuation_heartbeat: {exc}"
                entry.lease_lost = True
                log.error(
                    "continuation_registry_error",
                    op="heartbeat",
                    issue_id=issue_id,
                    error=str(exc),
                )
                return False
            self._last_registry_error = None
        else:
            ok = self._registry_guard(
                "heartbeat",
                lambda: registry.heartbeat(
                    issue_id=issue_id,
                    run_id=entry.run_id,
                    progress_at=progress,
                    backend_agent_pid=backend_agent_pid or entry.agent_pgid,
                ),
                True,
            )
        if ok:
            return True
        if entry.lease_lost:
            return False
        if release_required:
            entry.lease_lost = True
            log.error(
                "release_run_lease_lost",
                issue_id=issue_id,
                issue_identifier=entry.issue.identifier,
            )
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

    def _agent_pid_identity(self, entry: RunningEntry) -> str | None:
        """Return the identity fingerprint recorded with this entry's pid.

        The registry's heartbeat persists ``process_identity(pid)`` as
        ``backend_process_identity`` at the moment `_sync_backend_agent_pid`
        first records the pid, so the fingerprint survives restarts. None
        when no registry/run row backs the entry, or when the platform
        cannot prove identity (win32) — callers pass ``identity=None``
        through, which keeps the ungated kill-with-warn-once behavior.
        Lookup failures degrade to None rather than blocking a legitimate
        kill: the fingerprint is hardening, not authority.
        """
        registry = self._run_registry
        if registry is None or not entry.run_id:
            return None
        try:
            record = registry.get_run(entry.run_id)
        except KeyError:
            return None
        except Exception:
            return None
        return record.backend_process_identity

    def _sync_backend_agent_pid(
        self, issue_id: str, backend_agent_pid: int | None
    ) -> None:
        """Keep in-memory and persisted backend ownership in lockstep."""
        entry = self._running.get(issue_id)
        if entry is None:
            return
        entry.agent_pgid = backend_agent_pid
        if backend_agent_pid is not None:
            lease_ok = self._heartbeat_run_lease(
                issue_id,
                entry,
                backend_agent_pid=backend_agent_pid,
            )
            if not lease_ok:
                task = entry.worker_task
                if task is not None and not task.done() and entry.cancelled_at is None:
                    log.error(
                        "worker_cancelled_lease_conflict",
                        issue_id=issue_id,
                        issue_identifier=entry.issue.identifier,
                    )
                    task.cancel()
                    entry.cancelled_at = datetime.now(timezone.utc)
            return
        registry = self._run_registry
        if registry is None or not entry.run_id:
            return
        self._registry_guard(
            "clear_backend_agent_pid",
            lambda: registry.clear_backend_agent_pid(
                issue_id=issue_id,
                run_id=entry.run_id,
            ),
            True,
        )

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
        self,
        issue_id: str,
        entry: RunningEntry,
        status: str,
        error: str | None = None,
    ) -> None:
        registry = self._run_registry
        if registry is None or not entry.run_id:
            return
        if entry.backend_cleanup_unconfirmed:
            # Preserve the active owner/backend fence. The next service
            # instance must reclaim, kill/not-found the recorded process
            # group, and only then finalize the predecessor.
            log.warning(
                "run_lease_finish_deferred_for_backend_reap",
                issue_id=issue_id,
                issue_identifier=entry.issue.identifier,
                status=status,
                backend_agent_pid=entry.agent_pgid,
            )
            return
        self._registry_guard(
            "complete_run",
            lambda: registry.complete_run(
                issue_id=issue_id,
                run_id=entry.run_id,
                status=status,
                state=entry.issue.state,
                input_tokens=entry.codex_input_tokens,
                cache_input_tokens=entry.codex_cache_input_tokens,
                output_tokens=entry.codex_output_tokens,
                total_tokens=entry.codex_total_tokens,
                failure_class=(None if status == "normal" else status),
                failure_message=error,
            ),
            None,
        )

    def _try_acquire_run_lease(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        workspace_path: Path,
        attempt: int | None,
        attempt_kind: str,
        agent_kind: str,
        release_required: bool = False,
    ) -> _RunLeaseAcquisition | None:
        if release_required:
            try:
                run_id = self._release_registry_call(
                    cfg,
                    "acquire_release_run",
                    lambda registry: registry.acquire_run(
                        issue,
                        workspace_path=workspace_path,
                        attempt=attempt,
                        attempt_kind=attempt_kind,
                        agent_kind=agent_kind,
                    ),
                )
            except SymphonyError:
                return None
            if run_id:
                return _RunLeaseAcquisition(run_id=cast(str, run_id))
            log.info(
                "dispatch_lease_held",
                issue_id=issue.id,
                issue_identifier=issue.identifier,
            )
            return None
        registry = self._run_registry
        if registry is None:
            if cfg.agent.crash_continuation and self._run_registry_initialized:
                return None
            return _RunLeaseAcquisition(run_id="")

        if cfg.agent.crash_continuation:
            try:
                source_run_id = registry.latest_continuation_source(
                    issue_id=issue.id,
                    agent_kind=agent_kind,
                    state=issue.state,
                    issue_updated_at=issue.updated_at,
                )
                if source_run_id is not None:
                    recovered = registry.acquire_continuation_run(
                        issue,
                        continued_from_run_id=source_run_id,
                        workspace_path=workspace_path,
                        attempt=attempt,
                        attempt_kind="recovery",
                        agent_kind=agent_kind,
                    )
                    if recovered is None:
                        # Discovery raced or the source became ineligible. Do
                        # not degrade an authoritative continuation decision
                        # into a second fresh writer on this tick.
                        return None
                    self._last_registry_error = None
                    return _RunLeaseAcquisition(
                        run_id=recovered.run_id,
                        continued_from_run_id=recovered.continued_from_run_id,
                        checkpoint=recovered.checkpoint,
                    )
            except Exception as exc:
                # Session continuation is authority, not telemetry. A broken
                # read/claim fails closed even though ordinary lease
                # bookkeeping retains its historical fail-open behavior.
                self._registry_error_count += 1
                self._last_registry_error = f"acquire_continuation: {exc}"
                log.error(
                    "run_registry_error",
                    op="acquire_continuation",
                    error=str(exc),
                )
                return None

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
            if cfg.agent.crash_continuation:
                # Recovery authority cannot degrade into a leaseless writer.
                return None
            # Explicit opt-out preserves the historical fail-open lease path.
            return _RunLeaseAcquisition(run_id="")
        if run_id:
            return _RunLeaseAcquisition(run_id=run_id)
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

    def snapshot(self) -> dict[str, Any]:
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

    def schedule_snapshot(self) -> dict[str, Any]:
        """Immutable projection authored by the last completed scheduler pass."""

        return copy.deepcopy(self._schedule_snapshot)

    def dependency_state_resolved(self, state: str | None) -> bool:
        """Use the dispatcher's dependency-success contract for projections."""

        cfg = self._workflow_state.current()
        return _blocker_dependency_is_resolved(state, cfg)

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
            "merge_timing": "after Document, before Done",
            "auto_merge_enabled": bool(cfg.agent.auto_merge_on_done),
            "merge_delivery": (
                "upstream-publishing"
                if cfg.agent.auto_merge_push_target
                else "local-only"
            ),
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

    def issue_attention(self, issue: Issue) -> dict[str, str | None] | None:
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

    def _issue_is_terminal(self, issue: Issue) -> bool:
        state = normalize_state(issue.state)
        cfg = self._workflow_state.current()
        if cfg is not None:
            return state in {normalize_state(s) for s in cfg.tracker.terminal_states}
        return state in {"done", "cancelled", "canceled", "blocked", "archive"}

    def _stalled_attention(self, entry: RunningEntry) -> dict[str, str | None] | None:
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
        cfg = self._workflow_state.current()
        if cfg is None:
            return requested or ""
        return cfg.agent.kind_for_state(entry.issue.state, requested)

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

    async def skip_document(self, identifier: str) -> tuple[bool, str]:
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

    # Deprecated alias — the lane was renamed Learn -> Document; external
    # callers using the old method name keep working.
    skip_learn = skip_document

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

    async def _maybe_run_wiki_sweep(self, cfg: ServiceConfig, *, identifier: str) -> None:
        """C5 — bump the Done counter and run wiki-sweep every Nth time.

        Called (awaited) from the two Done-transition sites
        (`_on_worker_exit` and the reconcile-driven path). `sweep_every_n:
        0` disables the auto-sweep entirely. The sweep is best-effort and
        runs in-process for simplicity (the typical wiki is small), but
        off the event loop via `asyncio.to_thread` so a slow filesystem
        scan cannot stall ticks or delay shutdown. Failures only log a
        warning and never block the Done transition. The counter is
        persisted after every Done so sweep cadence survives
        orchestrator restarts.
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
            report = await asyncio.to_thread(_wiki_sweep_run, root, dry_run=False)
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
        branch = f"{SYMPHONY_BRANCH_PREFIX}{issue.identifier}"
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
        result = await auto_merge_on_done_best_effort(
            workflow_dir=cfg.workflow_path.parent,
            branch=f"{SYMPHONY_BRANCH_PREFIX}{issue.identifier}",
            identifier=issue.identifier,
            title=issue.title,
            target_branch=cfg.agent.auto_merge_target_branch,
            exclude_paths=cfg.agent.auto_merge_exclude_paths,
            capture_untracked=cfg.agent.auto_merge_capture_untracked,
            push_target=cfg.agent.auto_merge_push_target,
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
                await asyncio.wait_for(
                    self._tick_event.wait(), timeout=poll_ms / 1000.0
                )
            except asyncio.TimeoutError:
                pass
            self._tick_event.clear()
            self._refresh_pending = False

    async def _on_tick(self) -> None:
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
            validate_for_dispatch(cfg)
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
            len(candidates) <= MAX_DEPENDENCY_NODES
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
            self._maybe_schedule_continuous_improvement(cfg)
            await self._notify_observers()
            return

        dependency_analysis = (
            await asyncio.to_thread(analyze_dependencies, candidates)
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

        # Continuous-improvement heartbeat (plan §4). Cheap, non-blocking:
        # evaluates due-math + guards and, at most, fires one background task.
        self._maybe_schedule_continuous_improvement(cfg)

        await self._notify_observers()

    async def _dispatch_fifo_without_schedule_projection(
        self, candidates: list[Issue], cfg: ServiceConfig
    ) -> None:
        """Preserve the legacy bounded loop when explanation limits are exceeded."""

        empty_analysis = DependencyAnalysis({}, {})
        for issue in self._sort_with_wait_age_bump(
            candidates, cfg, analysis=empty_analysis
        ):
            if await self._auto_triage_todo_if_actionable(issue, cfg):
                continue
            if self._available_slots(cfg) <= 0:
                break
            if not self._should_dispatch(issue, cfg):
                continue
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
                attempt_kind=(
                    "retry" if persisted_attempt is not None else None
                ),
            )

    def _sort_with_wait_age_bump(
        self,
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

    @staticmethod
    def _artifact_commit_excludes(cfg: "ServiceConfig | None") -> tuple[str, ...]:
        """Pathspecs keeping collected deliverables out of the Done commit.

        Belt to `_ensure_artifact_dir_git_excluded`'s braces: that writes
        `.git/info/exclude` in the *workflow* repo, but a custom
        `after_create` hook can build the worktree in a different repo (the
        shipped monorepo template does), where the rule would not apply.
        This exclusion travels with the commit call, so it holds either way.
        """
        if cfg is None or not cfg.artifacts.enabled:
            return ()
        return (cfg.artifacts.dir,)

    def _prompt_artifacts_dir(self, cfg: ServiceConfig) -> str:
        """`artifacts.dir` for templates, or "" when collection is off.

        The empty string is what lets `{% if artifacts_dir %}` drop the
        instruction entirely — a disabled board must not tell workers to
        write into a directory nothing will ever read.
        """
        if not cfg.artifacts.enabled or self._artifact_store is None:
            return ""
        return cfg.artifacts.dir

    async def _collect_ticket_artifacts(
        self,
        cfg: ServiceConfig,
        *,
        identifier: str,
        workspace_path: Path,
        run_id: str,
        turn: int | None,
    ) -> None:
        """Copy new workspace deliverables into the host-owned store.

        Runs after every completed turn so a deliverable outlives the
        workspace (removed at Done) and is visible on the board while the
        run is still going. Idempotent by content hash, so re-scanning an
        unchanged directory writes nothing. Best-effort throughout: a
        failed collection must never fail the turn.
        """
        store = self._artifact_store
        if store is None:
            return
        try:
            result = await asyncio.to_thread(
                store.collect_from_workspace,
                workspace_path,
                identifier=identifier,
                magic_dir_name=cfg.artifacts.dir,
                run_id=run_id or None,
                turn=turn,
            )
        except Exception as exc:
            log.warning(
                "artifact_collect_failed", identifier=identifier, error=str(exc)
            )
            return
        for name, reason in result.skipped:
            if reason in ("duplicate", "hidden"):
                continue
            log.warning(
                "artifact_skipped", identifier=identifier, name=name, reason=reason
            )
        if not result.collected:
            return
        try:
            records = await asyncio.to_thread(store.list_for, identifier)
            await asyncio.to_thread(
                self._tracker_call_upsert_artifacts_section,
                cfg,
                identifier,
                self._artifact_section_body(cfg, identifier, records),
            )
        except Exception as exc:
            log.warning(
                "artifact_section_write_failed",
                identifier=identifier,
                error=str(exc),
            )

    def _artifact_section_body(
        self, cfg: ServiceConfig, identifier: str, records: list[ArtifactRecord]
    ) -> str:
        """Render the ticket's `## Artifacts` list.

        File-board tickets get a relative link that resolves in any local
        Markdown viewer; remote trackers (Jira, Linear) have no ticket file
        to be relative to, so they get the file name only. Today only the
        file board implements `upsert_artifacts_section`, so the remote
        rendering is written but not yet reachable — see
        `_tracker_call_upsert_artifacts_section`.
        """
        store = self._artifact_store
        if store is None:
            return ""
        is_file_board = (cfg.tracker.kind or "").strip().lower() == "file"
        board_root = cfg.tracker.board_root if is_file_board else None
        lines: list[str] = []
        for record in records:
            title = record.title or record.name
            size = format_bytes(record.byte_size)
            link: str | None = None
            if board_root is not None:
                try:
                    link = os.path.relpath(
                        store.root / identifier / "files" / record.name, board_root
                    )
                except (OSError, ValueError):
                    link = None
            # Angle brackets keep names with spaces a valid CommonMark link.
            head = (
                f"- [{title}](<{link}>) — `{record.name}` ({size})"
                if link
                else f"- {title} — `{record.name}` ({size})"
            )
            lines.append(head)
            if record.summary:
                lines.append(f"  - {record.summary}")
        return "\n".join(lines)

    @staticmethod
    def _tracker_call_upsert_artifacts_section(
        cfg: ServiceConfig, identifier: str, section_body: str
    ) -> None:
        client = build_tracker_client(cfg)
        try:
            upsert = getattr(client, "upsert_artifacts_section", None)
            if upsert is not None:
                upsert(identifier, section_body)
        finally:
            client.close()

    async def _artifact_sweep(self, cfg: ServiceConfig) -> None:
        """Drop artifact directories for tickets that left the board.

        Disabled when `artifacts.ttl_days <= 0`. Only directories whose
        ticket is no longer visible to the tracker AND untouched for the
        TTL are removed, so a live ticket never loses its deliverables.
        """
        store = self._artifact_store
        if store is None or cfg.artifacts.ttl_days <= 0:
            return
        try:
            known = await asyncio.to_thread(self._tracker_call_retained_identifiers, cfg)
        except Exception as exc:
            log.warning("artifact_sweep_fetch_failed", error=str(exc))
            return
        if not known:
            # None = tracker cannot enumerate; empty = it enumerated nothing.
            # A board directory that was renamed, unmounted, or lost to an I/O
            # error also globs to empty, and treating that as "every ticket is
            # gone" would delete artifacts nobody can regenerate. A genuinely
            # empty board has no artifacts to sweep either way.
            return
        try:
            await asyncio.to_thread(
                store.sweep,
                known_identifiers=known,
                ttl_days=cfg.artifacts.ttl_days,
            )
        except Exception as exc:
            log.warning("artifact_sweep_failed", error=str(exc))

    @staticmethod
    def _tracker_call_retained_identifiers(cfg: ServiceConfig) -> set[str] | None:
        """Tickets whose artifacts the sweep must keep: present, not archived.

        Returns None when the tracker cannot enumerate the whole board —
        the sweep then does nothing rather than risk deleting artifacts for
        tickets it simply could not see.
        """
        client = build_tracker_client(cfg)
        try:
            list_all = getattr(client, "list_all_identifiers", None)
            if list_all is None:
                return None
            known = {str(item) for item in list_all()}
            archive_state = (cfg.tracker.archive_state or "").strip()
            if archive_state:
                archived = client.fetch_issues_by_states([archive_state])
                known -= {issue.identifier for issue in archived}
            return known
        finally:
            client.close()

    def _ensure_artifact_dir_git_excluded(self, cfg: ServiceConfig) -> None:
        """Best-effort: keep the workspace artifact magic dir out of git.

        `commit_workspace_on_done` stages the whole worktree, so without an
        ignore rule every collected deliverable would land in the feature
        branch and merge into the target branch. `.git/info/exclude` lives
        in the shared common dir — one line covers the host checkout and
        every linked worktree — and stays local, unlike editing the user's
        checked-in .gitignore.
        """
        common_dir = git_inspect.git_common_dir(cfg.workflow_path.parent)
        if common_dir is None:
            # Custom `after_create` hooks may build worktrees in a different
            # repository (the shipped monorepo template does). Say so: without
            # the ignore rule `commit_workspace_on_done` stages deliverables
            # into the feature branch and the Done merge carries them.
            log.warning(
                "artifact_dir_git_exclude_skipped",
                reason="workflow dir is not inside a git repository",
                workflow_dir=str(cfg.workflow_path.parent),
            )
            return
        exclude_path = common_dir / "info" / "exclude"
        # Leading slash anchors to each working tree's root, where the magic
        # directory actually lives. An unanchored `output/` would match at any
        # depth and silently untrack an unrelated `web/output/` for good.
        pattern = f"/{cfg.artifacts.dir}/"
        try:
            existing = (
                exclude_path.read_text(encoding="utf-8")
                if exclude_path.exists()
                else ""
            )
            if pattern in existing.splitlines():
                return
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            with exclude_path.open("a", encoding="utf-8") as handle:
                if existing and not existing.endswith("\n"):
                    handle.write("\n")
                handle.write(f"{pattern}\n")
            log.info(
                "artifact_dir_git_excluded",
                exclude=str(exclude_path),
                pattern=pattern,
            )
        except OSError as exc:
            log.warning("artifact_dir_git_exclude_failed", error=str(exc))

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

    async def _auto_normalize_legacy_human_review_done(self, cfg: ServiceConfig) -> int:
        """Move legacy completion handoffs out of Human Review.

        Current prompts reserve Human Review for real manual intervention.
        Older file boards used it as the normal "Confirm Done" lane, which
        freezes dependencies once Human Review becomes intervention-only.
        """
        if cfg.tracker.kind != "file":
            return 0
        done_state = _human_review_done_state(cfg)
        if done_state is None:
            return 0
        try:
            terminal_issues = await asyncio.to_thread(
                self._tracker_call_terminal_issues, cfg
            )
        except Exception as exc:
            log.warning("human_review_normalize_fetch_failed", error=str(exc))
            return 0
        moved = 0
        for issue in terminal_issues:
            if not _legacy_human_review_is_done(issue):
                continue
            note_body = (
                "Moved this legacy `Human Review` card to `Done` because the "
                "current workflow reserves `Human Review` for critical/manual "
                "intervention, and this card contains completion evidence with "
                "no intervention marker."
            )
            try:
                await asyncio.to_thread(
                    self._tracker_call_append_note,
                    cfg,
                    issue,
                    "Human Review Normalized",
                    note_body,
                )
                await asyncio.to_thread(
                    self._tracker_call_update_state,
                    cfg,
                    issue,
                    done_state,
                )
            except Exception as exc:
                log.warning(
                    "human_review_normalize_failed",
                    identifier=issue.identifier,
                    error=str(exc),
                )
                self._record_tracker_error(issue.id, exc)
                continue
            self._clear_tracker_error(issue.id)
            moved += 1
            log.info(
                "human_review_normalized_done",
                identifier=issue.identifier,
                target=done_state,
            )
        return moved

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
    def _tracker_call_link_blocked_fix(
        cfg: ServiceConfig, source_issue: Issue, fix_identifier: str
    ) -> bool:
        client = build_tracker_client(cfg)
        try:
            update_fields = getattr(client, "update_fields", None)
            if not callable(update_fields):
                return False
            persisted = client.fetch_issue_full_by_id(source_issue.identifier)
            if persisted is None:
                return False
            current = [
                blocker.identifier or blocker.id
                for blocker in persisted.blocked_by
                if blocker.identifier or blocker.id
            ]
            if fix_identifier not in current:
                update_fields(
                    source_issue.identifier,
                    blocked_by=[*current, fix_identifier],
                )
            return True
        except Exception as exc:
            log.warning(
                "blocked_fix_dependency_link_failed",
                source_identifier=source_issue.identifier,
                fix_identifier=fix_identifier,
                error=str(exc),
            )
            return False
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

    @staticmethod
    def _tracker_call_reconcile_release_cycle(
        cfg: ServiceConfig,
        source_issue: Issue,
        validation: ReleaseValidationResult,
        source_agent_kind: str,
        *,
        before_finalizer_relink: Callable[[Issue], None] | None = None,
    ) -> _ReleaseCycleWriteResult:
        return ReleaseCycleService(cfg).reconcile(
            source_issue,
            validation,
            source_agent_kind,
            before_finalizer_relink=before_finalizer_relink,
        )

    @staticmethod
    def _tracker_call_set_agent_kind(
        cfg: ServiceConfig, identifier: str, agent_kind: str
    ) -> bool:
        client = build_tracker_client(cfg)
        try:
            update_fields = getattr(client, "update_fields", None)
            if update_fields is None:
                return False
            update_fields(identifier, agent_kind=agent_kind)
            return True
        finally:
            client.close()

    @staticmethod
    def _tracker_call_active_rca_for_source(
        cfg: ServiceConfig, source_identifier: str
    ) -> str | None:
        """Return the persisted active RCA for a source, if one exists."""
        client = build_tracker_client(cfg)
        try:
            # Blocked/Human Review RCA cards are still unresolved work even
            # when the workflow models those parking lanes as terminal. Only
            # completed/discarded RCA states stop suppressing duplicates.
            resolved_states = {"done", "archive", "cancelled"}
            scan_states = tuple(
                dict.fromkeys(
                    (*cfg.tracker.active_states, *cfg.tracker.terminal_states)
                )
            )
            unresolved_states = tuple(
                state
                for state in scan_states
                if normalize_state(state) not in resolved_states
            )
            issues = client.fetch_issues_by_states(unresolved_states)
        finally:
            client.close()
        source_key = source_identifier.casefold()
        for candidate in issues:
            if not _looks_like_blocked_rca_ticket(candidate):
                continue
            candidate_source = _blocked_rca_source_identifier(candidate)
            if candidate_source and candidate_source.casefold() == source_key:
                return candidate.identifier
        return None

    @staticmethod
    def _tracker_call_create_blocked_rca_issue(
        cfg: ServiceConfig,
        issue: Issue,
        rca_state: str,
        reopen_state: str,
        agent_kind: str,
    ) -> str | None:
        client = build_tracker_client(cfg)
        try:
            create_with_next_identifier = getattr(
                client, "create_with_next_identifier", None
            )
            if not callable(create_with_next_identifier):
                return None
            created = create_with_next_identifier(
                _blocked_rca_identifier_prefix(issue),
                title=f"Fix and unblock {issue.identifier}: {issue.title}",
                state=rca_state,
                priority=issue.priority,
                labels=_blocked_rca_labels(issue),
                description=_blocked_rca_description(
                    issue,
                    reopen_state=reopen_state,
                ),
                agent_kind=agent_kind,
            )
            if not isinstance(created, tuple) or not created:
                raise SymphonyError("tracker returned invalid created-ticket payload")
            return str(created[0])
        finally:
            client.close()

    # ------------------------------------------------------------------
    # candidate selection (§8.2)
    # ------------------------------------------------------------------

    def _should_dispatch(self, issue: Issue, cfg: ServiceConfig) -> bool:
        """§8.2 — eligibility for the poll-tick dispatch path."""
        return self._eligible(issue, cfg, owning_retry=False)

    async def recover_blocked_issue(
        self,
        identifier: str,
        *,
        target_state: str | None = None,
        agent_kind: str | None = None,
    ) -> tuple[bool, str, dict[str, str]]:
        """Open an RCA ticket for a Blocked issue without reopening it first."""
        cfg = self._workflow_state.current()
        if cfg is None:
            cfg, err = self._workflow_state.reload()
            if cfg is None:
                return False, f"workflow config unavailable: {err}", {}

        if self.find_running_issue_id(identifier) is not None:
            return False, f"{identifier} has a running worker; wait or pause first", {}

        issue = await asyncio.to_thread(
            self._tracker_call_fetch_issue_full_by_id, cfg, identifier
        )
        if issue is None:
            return False, f"unknown issue {identifier}", {}
        if normalize_state(issue.state) != "blocked":
            return (
                False,
                f"only Blocked tickets can be recovered (state={issue.state})",
                {},
            )
        if self.find_running_issue_id(identifier) is not None:
            return False, f"{identifier} started running; retry after it stops", {}

        return await self._open_blocked_rca_for_issue(
            cfg,
            issue,
            target_state=target_state,
            agent_kind=agent_kind,
            manual=True,
        )

    async def _flag_unpublished_history(
        self, cfg: ServiceConfig, issue: Issue, result: HistoryGateResult
    ) -> None:
        """Downgrade a card whose commit landed locally but never reached the remote.

        `Human Review` rather than `Blocked`: the work is committed and cannot
        be lost, so there is nothing for an RCA agent to root-cause — only a
        remote an operator has to settle. Blocking here would stall the queue
        over a publishing problem the pipeline already survived.
        """
        note = (
            "The delivery commit was recorded locally but Symphony could not "
            "verify it on the remote, so this card is not `Done` yet.\n\n"
            f"- branch: `{result.branch or '(unknown)'}`\n"
            f"- local commit: `{result.local_sha[:12] or 'none'}`\n"
            f"- remote tip: `{result.remote_sha[:12] or 'not found'}`\n"
            f"- classification: `{result.failure_kind}`\n\n"
            "Workspace preserved. Publish the branch and move the card to "
            "`Done`, or say why it should not be published.\n\n"
            f"```\n{result.detail[:1000]}\n```"
        )
        try:
            await asyncio.to_thread(
                self._tracker_call_append_note,
                cfg,
                issue,
                "History Not Published",
                note,
            )
            await asyncio.to_thread(
                self._tracker_call_update_state, cfg, issue, "Human Review"
            )
        except Exception as exc:
            log.warning(
                "history_gate_downgrade_failed",
                identifier=issue.identifier,
                branch=result.branch,
                error=str(exc),
            )
            return
        log.warning(
            "history_gate_unpublished",
            identifier=issue.identifier,
            branch=result.branch,
            local_sha=result.local_sha,
            remote_sha=result.remote_sha,
        )
        self.request_refresh()

    async def _recover_blocked_history_gate(
        self, cfg: ServiceConfig, issue: Issue
    ) -> bool:
        """Rescue a ticket blocked on a git write its sandbox never allowed.

        The agent runs the Final History Gate from inside a sandbox that may
        not reach the object database (``utils.git_sandbox``), so a finished
        ticket could self-park in ``Blocked`` on a housekeeping commit —
        while the host's own snapshot commit, running unsandboxed moments
        later, had already recorded the same work. Opening an RCA ticket for
        that is worse than useless: the RCA worker inherits the identical
        sandbox, blocks the same way, and cannot spawn a further RCA, which
        is where the board stops moving entirely.

        So before any RCA, the host re-checks the claim against real git
        state. Returns True when the ticket was moved and no RCA is needed.
        """
        description = issue.description or ""
        blocker = _markdown_section(description, _HISTORY_FAILURE_HEADING_RE)
        if not blocker or classify_history_failure(blocker) != SANDBOX_WRITE_DENIED:
            return False

        branch = f"{SYMPHONY_BRANCH_PREFIX}{issue.identifier}"
        # One rescue per ticket. A second identical block means the retry did
        # not help, and looping a ticket through the pipeline forever is a
        # worse failure mode than handing it to a human.
        already_recovered = bool(_HISTORY_RECOVERY_HEADING_RE.search(description))
        result = await verify_branch_history(
            cfg.workflow_path.parent,
            branch=branch,
            push=cfg.agent.auto_merge_push_target,
        )

        if not result.durable:
            log.info(
                "history_recovery_declined",
                identifier=issue.identifier,
                branch=branch,
                status=result.status,
            )
            return False

        published = (
            f"remote `{result.remote_sha[:12]}`"
            if result.remote_sha
            else "no upstream configured (local history only)"
        )
        note = (
            "Symphony re-checked this ticket's git history from the host repo, "
            "outside the agent sandbox that produced the failure.\n\n"
            f"- branch: `{branch}`\n"
            f"- local commit: `{result.local_sha[:12] or 'none'}`\n"
            f"- published: {published}\n"
            f"- host gate status: `{result.status}`\n\n"
            "The delivery record is in git history, so the reported "
            "`git add` failure was a sandbox permission limit, not lost work."
        )

        if result.status == HISTORY_PUSH_FAILED or already_recovered:
            reason = (
                "the remote tip could not be verified"
                if result.status == HISTORY_PUSH_FAILED
                else "this ticket was already rescued once and blocked again"
            )
            target = "Human Review"
            note = f"{note}\n\nMoved to `Human Review` because {reason}."
        else:
            target = _blocked_source_reopen_state(cfg)
            note = (
                f"{note}\n\nReopened into `{target}` for one automated retry; "
                "the history gate now runs on the host, so the same sandbox "
                "limit cannot block it again."
            )

        try:
            await asyncio.to_thread(
                self._tracker_call_append_note, cfg, issue, "History Recovery", note
            )
            await asyncio.to_thread(self._tracker_call_update_state, cfg, issue, target)
        except Exception as exc:
            log.warning(
                "history_recovery_persist_failed",
                identifier=issue.identifier,
                branch=branch,
                error=str(exc),
            )
            return False

        log.info(
            "history_recovery_applied",
            identifier=issue.identifier,
            branch=branch,
            status=result.status,
            target_state=target,
            local_sha=result.local_sha,
            remote_sha=result.remote_sha,
        )
        self.request_refresh()
        return True

    async def _open_blocked_rca_for_issue(
        self,
        cfg: ServiceConfig,
        issue: Issue,
        *,
        target_state: str | None = None,
        agent_kind: str | None = None,
        manual: bool = False,
    ) -> tuple[bool, str, dict[str, str]]:
        if _is_blocked_rca_ticket(issue):
            return False, f"{issue.identifier} is already a fix ticket", {}

        reopen_state = _blocked_source_reopen_state(cfg)
        rca_state = _blocked_rca_work_state(cfg)
        if target_state is not None:
            active_by_key = {
                normalize_state(state): state for state in cfg.tracker.active_states
            }
            requested_rca_state = active_by_key.get(normalize_state(target_state))
            if requested_rca_state is None:
                return (
                    False,
                    f"target_state must be one of active states: {list(cfg.tracker.active_states)}",
                    {},
                )
            rca_state = requested_rca_state

        requested_agent = (
            agent_kind or ""
        ).strip().lower() or cfg.agent.kind_for_state(rca_state, issue.agent_kind)
        if requested_agent not in SUPPORTED_AGENT_KINDS:
            requested_agent = cfg.agent.kind

        async with self._blocked_rca_creation_lock:
            # The board is authoritative. If a previous create succeeded but
            # its source edge/note did not, reconcile that partial write rather
            # than suppressing the retry or creating a second FIX ticket.
            active_rca = await asyncio.to_thread(
                self._tracker_call_active_rca_for_source,
                cfg,
                issue.identifier,
            )
            if active_rca is not None:
                await asyncio.to_thread(
                    self._tracker_call_link_blocked_fix,
                    cfg,
                    issue,
                    active_rca,
                )
                if not _blocked_rca_already_requested(issue):
                    try:
                        await asyncio.to_thread(
                            self._tracker_call_append_note,
                            cfg,
                            issue,
                            "Blocked Fix",
                            f"Fix ticket `{active_rca}` is already open. Symphony "
                            "reconciled the source dependency after an interrupted "
                            "recovery write; the source remains Blocked until the "
                            "fix is proven.",
                        )
                    except Exception as exc:
                        # Keep deduplication authoritative even if the source was
                        # concurrently removed or its note cannot yet be repaired.
                        log.warning(
                            "blocked_fix_source_note_reconcile_failed",
                            source_identifier=issue.identifier,
                            fix_identifier=active_rca,
                            error=str(exc),
                        )
                self._blocked_rca_source_ids.add(issue.id)
                return (
                    False,
                    f"blocked fix already opened for {issue.identifier}",
                    {},
                )
            if (
                _blocked_rca_already_requested(issue)
                or issue.id in self._blocked_rca_source_ids
            ):
                self._blocked_rca_source_ids.add(issue.id)
                return (
                    False,
                    f"blocked fix already opened for {issue.identifier}",
                    {},
                )
            rca_identifier = await asyncio.to_thread(
                self._tracker_call_create_blocked_rca_issue,
                cfg,
                issue,
                rca_state,
                reopen_state,
                requested_agent,
            )
            if rca_identifier is None:
                return (
                    False,
                    "blocked fix creation requires a tracker that can create tickets",
                    {},
                )

            body = (
                f"Fix ticket `{rca_identifier}` opened in `{rca_state}` for "
                f"`{requested_agent}` worker dispatch.\n\n"
                f"This source ticket remains `{issue.state}`. The fix worker must "
                "resolve and verify the root cause before moving this ticket back "
                f"to `{reopen_state}`. After that reopen, the source ticket still "
                "must pass the normal configured workflow. If the root cause cannot "
                "be resolved safely by an agent, the fix worker should leave this "
                "ticket Blocked with exact operator action."
            )
            if not manual:
                body = "Opened automatically by the orchestrator.\n\n" + body
            await asyncio.to_thread(
                self._tracker_call_link_blocked_fix,
                cfg,
                issue,
                rca_identifier,
            )
            await asyncio.to_thread(
                self._tracker_call_append_note,
                cfg,
                issue,
                "Blocked Fix",
                body,
            )
            # Only mark the in-process episode after its durable source note is
            # present. Partial creates are repaired by the active-board path.
            self._blocked_rca_source_ids.add(issue.id)

        self._record_stats_transition(rca_identifier, "", rca_state)
        self.request_refresh()
        return (
            True,
            f"{rca_identifier} opened to unblock {issue.identifier}; "
            f"{issue.identifier} remains {issue.state}",
            {
                "original_state": issue.state,
                "target_state": reopen_state,
                "source_reopen_state": reopen_state,
                "fix_identifier": rca_identifier,
                "fix_state": rca_state,
                # Deprecated response aliases retained for API compatibility.
                "rca_identifier": rca_identifier,
                "rca_state": rca_state,
                "agent_kind": requested_agent,
            },
        )

    async def _auto_recover_blocked_sources(self, cfg: ServiceConfig) -> int:
        if not cfg.agent.auto_recover_blocked:
            return 0
        slots = self._available_slots(cfg)
        if slots <= 0:
            return 0
        try:
            terminal_issues = await asyncio.to_thread(
                self._tracker_call_terminal_issues, cfg
            )
        except Exception as exc:
            log.warning("blocked_rca_fetch_failed", error=str(exc))
            return 0

        opened = 0
        for issue in _sort_for_dispatch_fifo(terminal_issues, cfg):
            if opened >= slots:
                break
            if normalize_state(issue.state) != "blocked":
                self._blocked_rca_source_ids.discard(issue.id)
                self._history_recovery_attempted.discard(issue.id)
                continue
            recovery_pending = issue.id not in self._history_recovery_attempted
            if not recovery_pending and _is_blocked_rca_ticket(issue):
                continue

            full_issue = issue
            if full_issue.description is None:
                fetched = await asyncio.to_thread(
                    self._tracker_call_fetch_issue_full_by_id,
                    cfg,
                    issue.id or issue.identifier,
                )
                if fetched is not None:
                    full_issue = fetched

            # Host-side rescue runs before any RCA, and deliberately also runs
            # for RCA tickets: an RCA blocked by the same sandbox limit is the
            # dead end, because it cannot open a further RCA of its own.
            if recovery_pending:
                self._history_recovery_attempted.add(issue.id)
                try:
                    if await self._recover_blocked_history_gate(cfg, full_issue):
                        self._clear_tracker_error(issue.id)
                        continue
                except Exception as exc:
                    log.warning(
                        "history_recovery_errored",
                        identifier=issue.identifier,
                        error=str(exc),
                    )
            if _is_blocked_rca_ticket(issue):
                continue

            try:
                changed, message, _details = await self._open_blocked_rca_for_issue(
                    cfg,
                    full_issue,
                    manual=False,
                )
            except Exception as exc:
                log.warning(
                    "blocked_rca_auto_failed",
                    identifier=issue.identifier,
                    error=str(exc),
                )
                self._record_tracker_error(issue.id, exc)
                continue
            if changed:
                self._clear_tracker_error(issue.id)
                opened += 1
                log.info(
                    "blocked_rca_auto_opened",
                    identifier=issue.identifier,
                    detail=message,
                )
            else:
                log.info(
                    "blocked_rca_auto_skipped",
                    identifier=issue.identifier,
                    reason=message,
                )
        return opened

    async def _resolved_blocked_rca_issue(
        self, cfg: ServiceConfig, issue: Issue
    ) -> Issue | None:
        if not _looks_like_blocked_rca_ticket(issue):
            return None
        if not _blocker_dependency_is_resolved(issue.state, cfg):
            return None
        if _is_blocked_rca_ticket(issue) and issue.description is not None:
            return issue
        fetched = await asyncio.to_thread(
            self._tracker_call_fetch_issue_full_by_id,
            cfg,
            issue.id or issue.identifier,
        )
        if fetched is None or not _looks_like_blocked_rca_ticket(fetched):
            return None
        return fetched

    async def _source_issue_for_blocked_rca(
        self,
        cfg: ServiceConfig,
        rca_issue: Issue,
        terminal_by_identifier: dict[str, Issue],
    ) -> Issue | None:
        source_identifier = _blocked_rca_source_identifier(rca_issue)
        if not source_identifier:
            log.warning(
                "blocked_rca_resolution_missing_source",
                identifier=rca_issue.identifier,
            )
            return None
        source_issue = terminal_by_identifier.get(source_identifier.casefold())
        if source_issue is not None and source_issue.description is not None:
            return source_issue
        fetched_source = await asyncio.to_thread(
            self._tracker_call_fetch_issue_full_by_id,
            cfg,
            source_identifier,
        )
        if fetched_source is None:
            log.warning(
                "blocked_rca_resolution_source_missing",
                rca_identifier=rca_issue.identifier,
                source_identifier=source_identifier,
            )
        return fetched_source

    async def _reopen_source_for_resolved_rca(
        self, cfg: ServiceConfig, rca_issue: Issue, source_issue: Issue
    ) -> bool:
        if normalize_state(source_issue.state) != "blocked":
            return False
        if _is_blocked_rca_ticket(source_issue):
            return False
        if _blocked_rca_requires_operator_intervention(
            rca_issue
        ) or _blocked_rca_requires_operator_intervention(source_issue):
            log.info(
                "blocked_rca_source_kept_blocked",
                rca_identifier=rca_issue.identifier,
                source_identifier=source_issue.identifier,
                reason="operator_intervention_required",
            )
            return False
        target_state = _blocked_source_reopen_state(cfg)
        body = (
            f"Fix ticket `{rca_issue.identifier}` reached `{rca_issue.state}`. "
            f"Symphony is moving `{source_issue.identifier}` back to "
            f"`{target_state}` so it can continue through the configured workflow."
        )
        try:
            if not _BLOCKED_RCA_HOST_RESOLVED_HEADING_RE.search(
                source_issue.description or ""
            ):
                await asyncio.to_thread(
                    self._tracker_call_append_note,
                    cfg,
                    source_issue,
                    "Blocked Fix Resolved",
                    body,
                )
            await asyncio.to_thread(
                self._tracker_call_update_state,
                cfg,
                source_issue,
                target_state,
            )
        except Exception as exc:
            log.warning(
                "blocked_rca_resolution_reopen_failed",
                rca_identifier=rca_issue.identifier,
                source_identifier=source_issue.identifier,
                error=str(exc),
            )
            self._record_tracker_error(source_issue.id, exc)
            return False
        # Keep the episode guard until the freshly reopened source appears in
        # the active candidate fetch, which prunes it. This prevents the same
        # tick's terminal snapshot from opening a second FIX.
        self._blocked_rca_source_ids.add(source_issue.id)
        self._clear_tracker_error(source_issue.id)
        log.info(
            "blocked_rca_source_reopened",
            rca_identifier=rca_issue.identifier,
            source_identifier=source_issue.identifier,
            target_state=target_state,
        )
        return True

    async def _move_blocked_fix_out_of_done(
        self,
        cfg: ServiceConfig,
        fix_issue: Issue,
        source_issue: Issue,
        *,
        reason: str,
        reason_code: str,
    ) -> bool:
        target_state = next(
            (
                state
                for state in cfg.tracker.terminal_states
                if normalize_state(state) == "human review"
            ),
            next(
                (
                    state
                    for state in cfg.tracker.terminal_states
                    if normalize_state(state) == "blocked"
                ),
                "Blocked",
            ),
        )
        body = (
            f"`{fix_issue.identifier}` cannot remain `Done` because {reason}. "
            f"Moved to `{target_state}`; the source ticket remains Blocked. "
            "Clarify the source request and acceptance criteria, verify the fix, "
            "then append `## Fix Resolution` before completing this ticket."
        )
        # Demotion is the safety boundary: perform it before best-effort notes,
        # and fail the whole tick closed if it cannot be persisted.
        try:
            await asyncio.to_thread(
                self._tracker_call_update_state,
                cfg,
                fix_issue,
                target_state,
            )
        except Exception as exc:
            log.warning(
                "blocked_fix_completion_hold_failed",
                fix_identifier=fix_issue.identifier,
                error=str(exc),
            )
            self._record_tracker_error(fix_issue.id, exc)
            raise

        if normalize_state(source_issue.state) != "blocked":
            try:
                await asyncio.to_thread(
                    self._tracker_call_update_state,
                    cfg,
                    source_issue,
                    "Blocked",
                )
            except Exception as exc:
                # The now-unresolved FIX dependency still prevents dispatch.
                log.warning(
                    "blocked_fix_source_reblock_failed",
                    fix_identifier=fix_issue.identifier,
                    source_identifier=source_issue.identifier,
                    error=str(exc),
                )
                self._record_tracker_error(source_issue.id, exc)

        try:
            await asyncio.to_thread(
                self._tracker_call_append_note,
                cfg,
                fix_issue,
                "Fix Completion Blocked",
                body,
            )
        except Exception as exc:
            log.warning(
                "blocked_fix_completion_note_failed",
                fix_identifier=fix_issue.identifier,
                error=str(exc),
            )

        self._clear_tracker_error(fix_issue.id)
        log.warning(
            "blocked_fix_completion_held",
            fix_identifier=fix_issue.identifier,
            target_state=target_state,
            reason=reason_code,
        )
        return True

    async def _hold_unproven_blocked_fix(
        self, cfg: ServiceConfig, fix_issue: Issue, source_issue: Issue
    ) -> bool:
        """Keep a new FIX ticket out of Done until both records prove recovery."""
        if not _is_blocked_fix_ticket(fix_issue):
            return False
        fix_has_resolution = _blocked_rca_current_episode_resolved(fix_issue)
        source_has_resolution = _blocked_rca_current_episode_resolved(source_issue)
        needs_operator = _blocked_rca_requires_operator_intervention(
            fix_issue
        ) or _blocked_rca_requires_operator_intervention(source_issue)
        if fix_has_resolution and source_has_resolution and not needs_operator:
            return False
        if needs_operator:
            reason = "it still requires operator action"
            reason_code = "operator_action"
        elif not fix_has_resolution:
            reason = "the FIX ticket has no current `## Fix Resolution`"
            reason_code = "missing_fix_resolution"
        else:
            reason = "the linked source has no current `## Fix Resolution`"
            reason_code = "missing_source_resolution"
        return await self._move_blocked_fix_out_of_done(
            cfg,
            fix_issue,
            source_issue,
            reason=reason,
            reason_code=reason_code,
        )

    async def _auto_reopen_sources_from_resolved_rcas(self, cfg: ServiceConfig) -> int:
        try:
            terminal_issues = await asyncio.to_thread(
                self._tracker_call_terminal_issues, cfg
            )
        except Exception as exc:
            log.warning("blocked_rca_resolution_fetch_failed", error=str(exc))
            raise
        terminal_by_identifier = {
            issue.identifier.casefold(): issue
            for issue in terminal_issues
            if issue.identifier
        }
        reopened = 0
        for issue in _sort_for_dispatch_fifo(terminal_issues, cfg):
            rca_issue = await self._resolved_blocked_rca_issue(cfg, issue)
            if rca_issue is None:
                continue
            source_issue = await self._source_issue_for_blocked_rca(
                cfg, rca_issue, terminal_by_identifier
            )
            if source_issue is None:
                continue
            if await self._hold_unproven_blocked_fix(cfg, rca_issue, source_issue):
                continue
            source_was_blocked = normalize_state(source_issue.state) == "blocked"
            if await self._reopen_source_for_resolved_rca(cfg, rca_issue, source_issue):
                reopened += 1
            elif source_was_blocked and _is_blocked_fix_ticket(rca_issue):
                await self._move_blocked_fix_out_of_done(
                    cfg,
                    rca_issue,
                    source_issue,
                    reason="Symphony could not persist the linked source reopen",
                    reason_code="source_reopen_failed",
                )
        return reopened

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
        """Compatibility seam for callers that only need a yes/no answer."""
        return (
            self._eligibility_decision(
                issue, cfg, owning_retry=owning_retry
            ).disposition
            is _EligibilityDisposition.READY
        )

    def _eligibility_ownership_decision(
        self, issue: Issue, cfg: ServiceConfig, *, owning_retry: bool
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
        self, issue: Issue, cfg: ServiceConfig
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
        if requested_agent is not None and requested_agent not in SUPPORTED_AGENT_KINDS:
            log.warning(
                "ticket_agent_kind_unsupported",
                issue_id=issue.id,
                identifier=issue.identifier,
                agent_kind=requested_agent,
                supported=sorted(SUPPORTED_AGENT_KINDS),
            )
            return _EligibilityDecision(
                _EligibilityDisposition.REJECT,
                "unsupported_agent",
                f"unsupported agent kind: {requested_agent}",
            )
        return None

    def _eligibility_contention_decision(
        self,
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
        self,
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

    def _blocker_is_in_flight(self, blocker: BlockerRef) -> bool:
        """Keep dependents idle until the upstream run is fully finalized."""
        keys = {key for key in (blocker.id, blocker.identifier) if key}
        if keys & self._in_flight_ids():
            return True
        return any(entry.issue.identifier in keys for entry in self._running.values())

    def _available_slots(self, cfg: ServiceConfig) -> int:
        # The retry-counts-against-the-budget rule lives on DispatchState
        # (single owner of slot math) — see its docstring for the OLV-005
        # double-start war story.
        return self._dispatch_state.available_slots(cfg.agent.max_concurrent_agents)

    # ------------------------------------------------------------------
    # continuous-improvement heartbeat (plan §4)
    # ------------------------------------------------------------------

    def _maybe_schedule_continuous_improvement(self, cfg: ServiceConfig) -> None:
        """Fire at most one heartbeat run when due. Called every tick.

        Non-blocking: the actual work runs in a supervised background task,
        never a worker slot. Config is read from the live snapshot each call
        so web-side toggles take effect without a restart.
        """
        ci = cfg.continuous_improvement
        # Cache config-derived fields even while disabled so the web-API
        # status reflects the current snapshot.
        self._improvement_status.update(
            enabled=ci.enabled,
            interval_ms=ci.interval_ms,
            max_turns=ci.max_turns,
            agent_kind=ci.agent_kind,
            modes=list(ci.resolved_modes()),
        )
        if not ci.enabled:
            return
        # One run at a time: a second tick while in flight is a no-op.
        if self._improvement_task is not None and not self._improvement_task.done():
            return

        now = time.monotonic()
        interval_s = max(ci.interval_ms, 0) / 1000.0
        if self._next_improvement_due_monotonic is None:
            # First observation while enabled: arm the next-due one interval
            # out so a fresh start doesn't fire immediately.
            self._next_improvement_due_monotonic = now + interval_s
            return
        if now < self._next_improvement_due_monotonic:
            return

        # Due. Turn budget (0 == unlimited).
        if ci.max_turns and self._improvement_turns_used >= ci.max_turns:
            self._improvement_status["skipped_reason"] = "max_turns_reached"
            if not self._improvement_cap_warned:
                log.warning(
                    "continuous_improvement_max_turns_reached",
                    turns_used=self._improvement_turns_used,
                    max_turns=ci.max_turns,
                )
                self._improvement_cap_warned = True
            return
        # Idle-board guard: postpone (don't consume the turn), re-check next
        # tick. Any live worker or retry-pending ticket counts as busy.
        if ci.require_idle_board and (
            self._running or self._retry or self._terminal_persist_pending
        ):
            self._improvement_status["skipped_reason"] = "board_busy"
            return
        # Per-mode cadence (`mode_interval_hours`): when every enabled mode is
        # still cooling down, re-arm the heartbeat without spending a turn.
        if not any_mode_due(cfg, cfg.workflow_path.parent):
            self._improvement_status["skipped_reason"] = "no_modes_due"
            self._next_improvement_due_monotonic = now + interval_s
            return

        self._improvement_status["skipped_reason"] = None
        self._last_improvement_monotonic = now
        self._next_improvement_due_monotonic = now + interval_s
        self._improvement_status.update(
            in_flight=True,
            current_phase="starting",
            last_started_at=_utc_iso_z(),
            last_result=None,
            last_error=None,
        )
        self._improvement_task = self._spawn_supervised(
            self._run_continuous_improvement(cfg),
            name="symphony-continuous-improvement",
        )
        self._improvement_task.add_done_callback(self._on_improvement_task_done)

    async def _run_continuous_improvement(self, cfg: ServiceConfig) -> None:
        """Acquire the cross-process lease, delegate to the runner, record.

        A run consumes a turn when it *completes* (success or failure). A
        lease-held postpone returns early and consumes nothing. Runner
        exceptions are caught here so the tick loop is never affected.
        """
        workflow_dir = cfg.workflow_path.parent
        lease = self._improvement_lease or FileLease(lease_path_for(workflow_dir))
        if not lease.acquire():
            # Another orchestrator holds the heartbeat; postpone silently.
            interval_s = max(cfg.continuous_improvement.interval_ms, 0) / 1000.0
            self._next_improvement_due_monotonic = time.monotonic() + interval_s
            self._improvement_status.update(
                in_flight=False, current_phase=None, skipped_reason="lease_held"
            )
            return

        consumed = False
        try:
            result = await asyncio.wait_for(
                self._improvement_runner(
                    cfg, workflow_dir, self._report_improvement_phase
                ),
                timeout=self._improvement_run_timeout_s,
            )
            self._improvement_status.update(
                last_result=result.status,
                skipped_reason=result.skipped_reason,
                tickets_created=result.tickets_created,
                last_verified_branch=result.verified_branch,
                last_verified_sha=result.verified_sha,
                last_mode_results=[
                    {
                        "mode": outcome.mode,
                        "status": outcome.status,
                        "summary": outcome.summary,
                        "ticket_ids": list(outcome.ticket_ids),
                    }
                    for outcome in result.modes
                ],
                last_request_id=result.request_id,
            )
            consumed = True
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._improvement_status.update(
                last_result="failed",
                last_error=(
                    "continuous improvement runner timed out after "
                    f"{self._improvement_run_timeout_s:g}s"
                ),
            )
            log.error(
                "continuous_improvement_run_failed",
                error="runner timed out",
                error_type="TimeoutError",
            )
            consumed = True
        except Exception as exc:  # noqa: BLE001 — runner failure must not kill the loop
            self._improvement_status.update(last_result="failed", last_error=str(exc))
            log.error(
                "continuous_improvement_run_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            consumed = True
        finally:
            lease.release()
            if consumed:
                # Reset-in-flight semantics: a reset zeroes the counter mid-run;
                # this in-flight run still counts its own increment on finish.
                self._improvement_turns_used += 1
                self._improvement_status["last_finished_at"] = _utc_iso_z()
            self._improvement_status.update(in_flight=False, current_phase=None)

    async def _run_improvement_agent(self, task: AgentTask) -> str:
        """Give an agent-driven improvement mode one backend turn.

        This is the orchestrator-side capability the continuous-improvement
        module is handed (it must not import the orchestrator). The turn runs
        against the host repo with `cwd == workspace_root == workflow_dir`,
        exactly like a chat turn, and outside the dispatch slot accounting —
        the heartbeat already holds the idle board. The backend stays in its
        normal editing mode so the agent can write its one proposal file.

        F-12: "read only except the proposal file" used to be prompt text
        only, in the *host* tree. It is now also mechanical — the working
        tree is snapshotted before and after the turn, and a write outside
        `.symphony/continuous-improvement/proposals/` discards the mode's
        proposals (the caller records `not_proven`) and logs the paths.
        """
        cfg = self._workflow_state.current()
        if cfg is None:
            raise SymphonyError("no workflow configuration loaded")
        ci = cfg.continuous_improvement
        agent_cfg, _ = cfg_for_mode(cfg, "edit", ci.agent_kind or cfg.agent.kind)

        async def _ignore_event(_event: dict[str, Any]) -> None:
            return None

        backend = self._build_agent_backend(
            BackendInit(
                cfg=agent_cfg,
                cwd=task.cwd,
                workspace_root=task.cwd,
                on_event=_ignore_event,
            )
        )
        before = await asyncio.to_thread(_worktree_status_snapshot, task.cwd)
        await backend.start()
        try:
            await backend.initialize()
            await backend.start_session(initial_prompt="", issue_title=None)
            result = await backend.run_turn(prompt=task.prompt, is_continuation=False)
        finally:
            await backend.stop()
        after = await asyncio.to_thread(_worktree_status_snapshot, task.cwd)
        self._enforce_improvement_write_contract(task, before, after)
        return result.last_message or ""

    def _enforce_improvement_write_contract(
        self,
        task: AgentTask,
        before: dict[str, str] | None,
        after: dict[str, str] | None,
    ) -> None:
        """Discard a CI agent turn that wrote outside its contract."""
        if before is None or after is None:
            # Not a git worktree (or git unavailable): nothing to compare
            # against. The prompt remains the only gate, as before.
            return
        allowed = CI_AGENT_OUTPUT_PREFIX
        offending = sorted(
            path
            for path, code in after.items()
            if before.get(path) != code and not path.startswith(allowed)
        )
        if not offending:
            return
        try:
            task.output_path.unlink()
        except OSError:
            pass
        log.error(
            "ci_agent_wrote_outside_contract",
            mode=task.mode,
            cwd=str(task.cwd),
            paths=offending[:20],
            path_count=len(offending),
        )
        raise SymphonyError(
            "continuous-improvement agent wrote outside its contract; "
            f"proposals discarded: {', '.join(offending[:5])}",
            mode=task.mode,
        )

    def _report_improvement_phase(self, phase: str) -> None:
        self._improvement_status["current_phase"] = phase

    def _on_improvement_task_done(self, task: asyncio.Task[None]) -> None:
        # Identity check: only clear the ref if it still points at this task,
        # so a task launched after this one is never dropped.
        if self._improvement_task is task:
            self._improvement_task = None

    def reset_continuous_improvement_turns(self) -> None:
        """Web-API hook: zero the turn counter, clear a max_turns skip."""
        self._improvement_turns_used = 0
        self._improvement_cap_warned = False
        if self._improvement_status.get("skipped_reason") == "max_turns_reached":
            self._improvement_status["skipped_reason"] = None

    def continuous_improvement_status(self) -> dict[str, Any]:
        """Web-API status snapshot. Merges cached config fields, the live
        turn counter, in-flight flag, and the next-due wall-clock estimate.
        """
        status = dict(self._improvement_status)
        cfg = self.workflow_state.current()
        if cfg is not None:
            ci = cfg.continuous_improvement
            status.update(
                enabled=ci.enabled,
                interval_ms=ci.interval_ms,
                max_turns=ci.max_turns,
                agent_kind=ci.agent_kind,
                modes=list(ci.resolved_modes()),
            )
        status["turns_used"] = self._improvement_turns_used
        status["in_flight"] = (
            self._improvement_task is not None and not self._improvement_task.done()
        )
        status["next_due_at"] = self._improvement_next_due_iso()
        return status

    def _improvement_next_due_iso(self) -> str | None:
        due = self._next_improvement_due_monotonic
        if due is None:
            return None
        delta = max(due - time.monotonic(), 0.0)
        return _to_iso(datetime.now(timezone.utc) + timedelta(seconds=delta))

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

    def _conflict_blocker(self, candidate: Issue) -> tuple[str, set[str]] | None:
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
            _TOKEN_EMA_ALPHA * float(total_tokens) + (1.0 - _TOKEN_EMA_ALPHA) * prev
        )
        if cfg is not None:
            self._persist_token_ema(cfg)

    def _token_ema_for_state(self, state: str) -> int:
        """Rounded EMA for a state. 0 when unseen."""
        key = (state or "").lower()
        return int(round(self._token_ema.get(key, 0.0)))

    def _token_budget_for_state(self, cfg: ServiceConfig, state: str) -> int:
        """Hard cap from `agent.max_total_tokens_by_state` w/ fallback."""
        key = (state or "").lower()
        by_state = cfg.agent.max_total_tokens_by_state
        cap = by_state.get(key)
        if cap is None and key == "learn":
            cap = by_state.get("learning")
        if cap is None and key == "learning":
            cap = by_state.get("learn")
        return cap if cap is not None else cfg.agent.max_total_tokens

    def _max_state_turns_for_state(self, cfg: ServiceConfig, state: str) -> int:
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

    def _ticket_prompt_path(self, cfg: ServiceConfig, issue: Issue) -> str | None:
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

    def _executor_for(self, cfg: ServiceConfig) -> TicketExecutor:
        """Pick the execution strategy for one dispatch."""
        return LegacyStageExecutor(self)

    def _dispatch(
        self,
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
            if cfg.agent.stage_kinds:
                # Routed board: the pin must stay empty, but the audit value
                # still belongs on the ticket (F-20).
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

    # ------------------------------------------------------------------
    # worker (§16.5)
    # ------------------------------------------------------------------

    async def run_legacy_stage_loop(
        self, issue: Issue, attempt: int | None, cfg: ServiceConfig
    ) -> None:
        """Public entry point for `LegacyStageExecutor`.

        A one-line forward so the execution-mode seam does not have to
        reach into a private method across modules.
        """
        await self._run_agent_attempt(issue, attempt, cfg)

    async def _run_agent_attempt(
        self, issue: Issue, attempt: int | None, cfg: ServiceConfig
    ) -> None:
        running_issue_id = issue.id
        outcome: str = "normal"
        error: str | None = None
        try:
            running = self._running.get(running_issue_id)
            if running is not None and not running.release_authority_resolved:
                try:
                    release_authority = self._prepare_release_dispatch(issue, cfg)
                except SymphonyError as exc:
                    outcome = "error"
                    error = str(exc)
                    log.error(
                        "release_execution_refused",
                        issue_id=issue.id,
                        identifier=issue.identifier,
                        tracker_kind=cfg.tracker.kind,
                        error=error,
                    )
                    return
                issue = release_authority.issue
                running.issue = issue
                running.known_app_release = release_authority.app_release
                running.known_release_cycle_verifier = release_authority.cycle_verifier
                running.known_app_release_finalizer = release_authority.finalizer
                if release_authority.gate is not None:
                    running.release_gate_finalizer = (
                        release_authority.gate.finalizer_identifier
                    )
                    running.release_gate_expected_contract_sha256 = (
                        release_authority.gate.expected_contract_sha256
                    )
                    running.release_gate_cycle_fingerprint = (
                        release_authority.gate.cycle_fingerprint
                    )
                    running.release_gate_generation = release_authority.gate.generation
                if release_authority.finalizer:
                    running.release_finalizer_rewind_state = issue.state
                running.release_authority_resolved = True
            # Keep the *unrouted* workflow config: `agent.stage_kinds` must be
            # re-resolved at every in-run phase transition, and re-resolving
            # against an already-routed cfg would pin the first lane's backend
            # for the whole dispatch (the normal Todo→…→Document path).
            base_cfg = cfg
            cfg = _config_for_issue_agent(base_cfg, issue)
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
            if (
                running.known_app_release
                or running.known_release_cycle_verifier
                or running.known_app_release_finalizer
            ):
                if not self._heartbeat_run_lease(running_issue_id, running):
                    outcome = "release_authority_error"
                    error = "application release lease was lost before workspace use"
                    return
                try:
                    running.issue = self._require_running_release_authority(
                        cfg=cfg,
                        entry=running,
                        workspace_path=workspace.path,
                    )
                    issue = running.issue
                except Exception as exc:
                    outcome = "release_authority_error"
                    error = str(exc)
                    return
            try:
                await self._workspace_manager.before_run(workspace.path)
            except Exception as exc:
                outcome = "before_run_error"
                error = str(exc)
                return

            tools = []
            if cfg.tracker.kind == "linear" and cfg.agent.kind == "codex":
                tools.append(linear_graphql_tool())

            client = self._build_agent_backend(
                BackendInit(
                    cfg=cfg,
                    cwd=workspace.path,
                    workspace_root=cfg.workspace_root,
                    on_event=lambda ev, issue_id=running_issue_id: self._on_codex_event(
                        issue_id, ev
                    ),
                    on_process_started=lambda pid, issue_id=running_issue_id: (
                        self._sync_backend_agent_pid(issue_id, pid)
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
                self._sync_backend_agent_pid(
                    running_issue_id, _backend_agent_pid(client)
                )
                try:
                    await client.start()
                finally:
                    self._sync_backend_agent_pid(
                        running_issue_id, _backend_agent_pid(client)
                    )
                await client.initialize()

                turn_number = 1
                debug = self._issue_debug.setdefault(running_issue_id, _IssueDebug())
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
                    turn_number=debug.completed_turn_count + turn_number,
                    max_turns=cfg.agent.max_total_turns,
                    max_attempts=cfg.agent.max_attempts,
                    auto_merge_on_done=cfg.agent.auto_merge_on_done,
                    token_ema=self._token_ema_for_state(issue.state),
                    token_budget=self._token_budget_for_state(cfg, issue.state),
                    rewind_scope=None,
                    compact_issue_context=cfg.agent.compact_issue_context,
                    full_ticket_path=self._ticket_prompt_path(cfg, issue),
                    artifacts_dir=self._prompt_artifacts_dir(cfg),
                    extra_context=skill_context,
                )
                resumed_checkpoint = False
                checkpoint = running.continuation_checkpoint
                if checkpoint is not None:
                    try:
                        resumed_checkpoint = await client.resume_session(
                            checkpoint.resume_session_id
                        )
                    except Exception as exc:
                        log.error(
                            "session_continuation_resume_error",
                            issue_id=running_issue_id,
                            issue_identifier=issue.identifier,
                            agent_kind=cfg.agent.kind,
                            error_type=type(exc).__name__,
                        )
                        raise SymphonyError(
                            "exact session continuation failed before turn start"
                        ) from None
                    running.recovery_session_resumed = resumed_checkpoint
                    if resumed_checkpoint:
                        running.resume_session_id = checkpoint.resume_session_id
                        self._append_run_event(running, "session_started", {})
                        log.info(
                            "session_continuation_resumed",
                            issue_id=running_issue_id,
                            issue_identifier=issue.identifier,
                            checkpoint_turn=checkpoint.turn,
                        )
                    else:
                        log.info(
                            "session_continuation_fresh_fallback",
                            issue_id=running_issue_id,
                            issue_identifier=issue.identifier,
                            checkpoint_turn=checkpoint.turn,
                            agent_kind=cfg.agent.kind,
                        )
                if not resumed_checkpoint:
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
                # Minimal state refreshes intentionally omit labels. Retain
                # the last full-body app-release signal until the next full
                # refresh so stage-contracts=off cannot erase the machine gate.
                running_entry = self._running.get(running_issue_id)
                known_app_release = (
                    running_entry.known_app_release
                    if running_entry is not None
                    else False
                ) or _has_app_release_label(issue)

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
                            transition = await self._transition_agent_phase(
                                phase_state=_AgentPhaseState(
                                    issue=issue,
                                    cfg=cfg,
                                    client=client,
                                    first_prompt=first_prompt,
                                    current_state=current_state,
                                    known_app_release=known_app_release,
                                ),
                                running_issue_id=running_issue_id,
                                base_cfg=base_cfg,
                                workspace_path=workspace.path,
                                attempt=attempt,
                                doc_language=doc_language,
                                producing_state=prev_phase_state,
                                producing_state_raw=prev_phase_state_raw,
                                turn_number=turn_number,
                            )
                            if transition is None:
                                break
                            phase_state = transition.state
                            issue = phase_state.issue
                            cfg = phase_state.cfg
                            client = phase_state.client
                            first_prompt = phase_state.first_prompt
                            current_state = phase_state.current_state
                            known_app_release = phase_state.known_app_release
                            is_rewind = transition.is_rewind
                            running_entry = self._running.get(running_issue_id)
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
                            if running_entry is not None:
                                self._append_run_event(
                                    running_entry,
                                    "phase_transition",
                                    {
                                        "from_state": prev_phase_state,
                                        "to_state": current_state,
                                        "turn": turn_number,
                                        "attempt": attempt,
                                        "is_rewind": is_rewind,
                                    },
                                )
                            self._record_stats_transition(
                                issue.identifier, prev_phase_state, current_state
                            )
                        except Exception as exc:
                            outcome = "phase_transition_error"
                            error = str(exc)
                            return

                    running_entry = self._running.get(running_issue_id)
                    if (
                        running_entry is not None
                        and running_entry.hit_empty_response_loop
                    ):
                        await self._escalate_empty_response_loop(
                            cfg=cfg,
                            entry=running_entry,
                            issue_id=running_issue_id,
                            cancel_worker=False,
                        )
                        break

                    is_continuation = (
                        (running.recovery_session_resumed and turn_number == 1)
                        or (turn_number > 1 and not is_phase_transition)
                    )
                    if is_continuation:
                        debug = self._issue_debug.setdefault(
                            running_issue_id, _IssueDebug()
                        )
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
                    if (
                        running.known_app_release
                        or running.known_release_cycle_verifier
                        or running.known_app_release_finalizer
                    ):
                        if not self._heartbeat_run_lease(running_issue_id, running):
                            outcome = "release_authority_error"
                            error = (
                                "application release lease was lost before agent turn"
                            )
                            return
                        try:
                            running.issue = self._require_running_release_authority(
                                cfg=cfg,
                                entry=running,
                            )
                            issue = running.issue
                        except Exception as exc:
                            outcome = "release_authority_error"
                            error = str(exc)
                            return
                    # Capture the state THIS turn is starting in. C3 EMA
                    # samples need the source state, not the destination
                    # the agent flips to mid-turn — without this, every
                    # stage's tokens get attributed to the next stage.
                    running.state_at_turn_start = (running.issue.state or "").lower()
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
                    self._append_run_event(
                        running,
                        "turn_started",
                        {
                            "turn": turn_number,
                            "state": running.issue.state,
                            "continuation": is_continuation,
                        },
                    )
                    if turn_number > 1:
                        try:
                            await self._workspace_manager.before_run(workspace.path)
                        except Exception as exc:
                            outcome = "before_run_error"
                            error = str(exc)
                            return
                    self._sync_backend_agent_pid(
                        running_issue_id, _backend_agent_pid(client)
                    )
                    after_run_pending = True
                    try:
                        await client.run_turn(
                            prompt=prompt, is_continuation=is_continuation
                        )
                    except (
                        TurnTimeout,
                        TurnFailed,
                        TurnCancelled,
                        TurnInputRequired,
                    ) as exc:
                        outcome = "turn_error"
                        error = str(
                            redact_session_id(str(exc), running.resume_session_id)
                        )
                        return
                    finally:
                        self._sync_backend_agent_pid(
                            running_issue_id, _backend_agent_pid(client)
                        )

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
                    # Collect before the next loop iteration evaluates the
                    # stage contract, so `artifacts.require_for_done` sees
                    # this turn's deliverables, and before Done removes the
                    # workspace they live in.
                    await self._collect_ticket_artifacts(
                        cfg,
                        identifier=issue.identifier,
                        workspace_path=workspace.path,
                        run_id=(
                            running_entry.run_id if running_entry is not None else ""
                        ),
                        turn=turn_number,
                    )
                    # The hook may commit or amend the turn's changes. Resolve
                    # HEAD only after it finishes so the explorer never reports
                    # the base/prior-turn commit as this turn's result.
                    commit_sha = None
                    if (workspace.path / ".git").exists():
                        commit_sha = await asyncio.to_thread(
                            git_inspect.resolve_commit, workspace.path, "HEAD"
                        )
                    if commit_sha:
                        running_entry = self._running.get(running_issue_id)
                        if running_entry is not None:
                            self._append_run_event(
                                running_entry,
                                "workspace_updated",
                                {"turn": turn_number, "commit_sha": commit_sha},
                            )

                    running_entry = self._running.get(running_issue_id)
                    registry = self._run_registry
                    if (
                        cfg.agent.crash_continuation
                        and registry is not None
                        and running_entry is not None
                        and running_entry.run_id
                        and running_entry.resume_session_id
                        and running_entry.last_completed_turn_event == turn_number
                        and not running_entry.known_app_release
                        and not running_entry.known_release_cycle_verifier
                        and not running_entry.known_app_release_finalizer
                    ):
                        checkpoint_turn = debug.completed_turn_count + turn_number
                        checkpoint_registry = cast(RunRegistry, registry)
                        checkpoint_run_id = running_entry.run_id
                        checkpoint_session_id = running_entry.resume_session_id
                        checkpoint_state = running_entry.issue.state
                        self._registry_guard(
                            "checkpoint_completed_turn",
                            lambda: checkpoint_registry.checkpoint_completed_turn(
                                issue_id=running_issue_id,
                                run_id=checkpoint_run_id,
                                resume_session_id=checkpoint_session_id,
                                state=checkpoint_state,
                                turn=checkpoint_turn,
                            ),
                            False,
                        )

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
                    release_rewound = False
                    if (
                        running.known_app_release_finalizer
                        and state != prev_phase_state
                    ):
                        try:
                            finalizer_identifier = (
                                running.release_gate_finalizer or issue.identifier
                            )
                            finalizer_gate = cast(
                                ReleaseGate | None,
                                self._release_registry_call(
                                    cfg,
                                    "read_finalizer_gate_after_turn",
                                    lambda registry: registry.get_release_gate(
                                        finalizer_identifier
                                    ),
                                ),
                            )
                            if finalizer_gate is None:
                                raise SymphonyError(
                                    "application release finalizer authority disappeared",
                                    finalizer=issue.identifier,
                                )
                            issue = self._guard_release_finalizer(
                                cfg=cfg,
                                issue=issue,
                                gate=finalizer_gate,
                                rewind_state=(prev_phase_state_raw or prev_phase_state),
                                expected_run_id=running.run_id,
                                require_run_authority=True,
                            )
                        except Exception as exc:
                            try:
                                issue = await self._rewind_app_release_transition(
                                    cfg=cfg,
                                    issue=issue,
                                    producing_state=(
                                        prev_phase_state_raw or prev_phase_state
                                    ),
                                    note_body=(
                                        "Final delivery was stopped because the "
                                        f"host-owned release approval is invalid: {exc}"
                                    ),
                                )
                                running.issue = issue
                            except Exception as rewind_exc:
                                log.error(
                                    "release_finalizer_rewind_failed",
                                    issue_id=issue.id,
                                    identifier=issue.identifier,
                                    gate_error=str(exc),
                                    rewind_error=str(rewind_exc),
                                )
                            outcome = "phase_transition_error"
                            error = str(exc)
                            return
                        if state in active:
                            running.release_finalizer_rewind_state = issue.state
                    if (
                        state != prev_phase_state
                        and prev_phase_state == "verify"
                        and not _is_rewind_transition(
                            prev_phase_state,
                            state,
                            cfg.tracker.active_states,
                        )
                    ):
                        try:
                            (
                                issue,
                                release_rewound,
                            ) = await self._enforce_app_release_transition(
                                cfg=cfg,
                                issue=issue,
                                workspace_path=workspace.path,
                                producing_state=(
                                    prev_phase_state_raw or prev_phase_state
                                ),
                                known_app_release=known_app_release,
                                running_entry=running,
                            )
                        except Exception as exc:
                            outcome = "phase_transition_error"
                            error = str(exc)
                            return
                        known_app_release = known_app_release or _has_app_release_label(
                            issue
                        )
                        running.issue = issue
                        state = normalize_state(issue.state)
                    if running.release_verifier_handoff_complete:
                        break
                    if release_rewound:
                        debug.rewind_count += 1
                        if (
                            cfg.agent.max_attempts > 0
                            and debug.rewind_count > cfg.agent.max_attempts
                        ):
                            rewind_target = _release_failure_target_state(cfg)
                            if rewind_target:
                                await asyncio.to_thread(
                                    self._tracker_call_update_state,
                                    cfg,
                                    issue,
                                    rewind_target,
                                )
                                issue = replace(issue, state=rewind_target)
                                running.issue = issue
                            else:
                                running.release_gate_exhausted = True
                            log.warning(
                                "rewind_budget_exceeded",
                                issue_id=issue.id,
                                identifier=issue.identifier,
                                from_state=prev_phase_state,
                                to_state=state,
                                rewind_count=debug.rewind_count,
                                max_attempts=cfg.agent.max_attempts,
                                target_state=rewind_target or "(none)",
                            )
                        break
                    if state not in active:
                        break
                    state_turn_count = _update_state_turn_counter(debug, state)
                    max_state_turns = self._max_state_turns_for_state(cfg, state)
                    if max_state_turns > 0 and state_turn_count >= max_state_turns:
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
                    running = self._running.get(running_issue_id)
                    if running is not None:
                        running.backend_cleanup_unconfirmed = True
                    log.warning(
                        "worker_final_stop_failed",
                        issue_id=issue.id,
                        identifier=issue.identifier,
                        error=str(stop_exc),
                    )
                else:
                    running = self._running.get(running_issue_id)
                    if running is not None and running.backend_cleanup_unconfirmed:
                        log.warning(
                            "worker_final_stop_cleanup_unconfirmed",
                            issue_id=issue.id,
                            identifier=issue.identifier,
                            pid=running.agent_pgid,
                        )
                    else:
                        self._sync_backend_agent_pid(running_issue_id, None)
                if after_run_pending:
                    await self._workspace_manager.after_run_best_effort(workspace.path)
                # Salvage deliverables written before an abnormal exit (turn
                # timeout, TurnFailed, stall eviction). The per-turn call runs
                # only on the success path, and the workspace is torn down at
                # Done, so without this the file is gone for good — worst
                # under `artifacts.require_for_done`, where the deliverable
                # turn is the long, timeout-prone one. Unshielded and
                # best-effort, exactly like the `after_run` hook above.
                entry_for_run = self._running.get(running_issue_id)
                await self._collect_ticket_artifacts(
                    cfg,
                    identifier=issue.identifier,
                    workspace_path=workspace.path,
                    run_id=entry_for_run.run_id if entry_for_run else "",
                    turn=None,  # salvage pass: the turn it came from is unknown
                )
        except asyncio.CancelledError:
            outcome = "shutdown_interrupted" if self._stopping else "cancelled"
            error = None
            raise
        except SymphonyError as exc:
            outcome = "error"
            running = self._running.get(running_issue_id)
            private_session_id = running.resume_session_id if running is not None else None
            error = str(redact_session_id(str(exc), private_session_id))
        except Exception as exc:
            outcome = "error"
            running = self._running.get(running_issue_id)
            private_session_id = running.resume_session_id if running is not None else None
            error = str(redact_session_id(str(exc), private_session_id))
            log.error(
                "worker_unhandled_error",
                issue_id=running_issue_id,
                error=error,
                exc_type=type(exc).__name__,
                traceback=str(
                    redact_session_id(traceback.format_exc(), private_session_id)
                ),
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
            # AF-01 — a force-ejected zombie's `finally` can run after a
            # retry already installed a fresh entry under this issue id
            # (the zombie task is never cancelled by force-eject, only its
            # bookkeeping is dropped). Only the task that actually owns the
            # current entry may stamp `exit_started_at` or enter
            # `_on_worker_exit`; a foreign owner must not touch either.
            # The handler keeps its own identity check as the single guard
            # around the eventual pop.
            # `entry.worker_task is None` counts as owned — many existing
            # tests drive this coroutine directly against a hand-installed
            # entry that never went through `_dispatch`.
            owning_task = asyncio.current_task()
            entry = self._running.get(running_issue_id)
            stale_entry = (
                entry is not None
                and owning_task is not None
                and self._dispatch_state.entry_foreign_to(running_issue_id, owning_task)
            )
            if stale_entry:
                log.warning(
                    "worker_finally_stale_entry",
                    issue_id=running_issue_id,
                    reason=outcome,
                )
            elif entry is not None:
                entry.exit_started_at = datetime.now(timezone.utc)
                await asyncio.shield(
                    self._on_worker_exit(
                        running_issue_id, outcome, error, owning_task=owning_task
                    )
                )

    async def _transition_agent_phase(
        self,
        *,
        phase_state: _AgentPhaseState,
        running_issue_id: str,
        base_cfg: ServiceConfig,
        workspace_path: Path,
        attempt: int | None,
        doc_language: str,
        producing_state: str,
        producing_state_raw: str,
        turn_number: int,
    ) -> _AgentPhaseTransition | None:
        issue = phase_state.issue
        cfg = phase_state.cfg
        client = phase_state.client
        first_prompt = phase_state.first_prompt
        current_state = phase_state.current_state
        known_app_release = phase_state.known_app_release
        debug = self._issue_debug.setdefault(running_issue_id, _IssueDebug())

        is_rewind = _is_rewind_transition(
            producing_state,
            current_state,
            cfg.tracker.active_states,
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
        if not is_rewind and cfg.agent.stage_contracts_enabled(
            cfg.tracker.active_states
        ):
            if producing_state in {
                "in progress",
                "verify",
                "document",
                # legacy lane name (pre-rename boards)
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
                refreshed_for_contract = await self._refresh_issue_full(
                    cfg, running_issue_id
                )
                if refreshed_for_contract is not None:
                    issue = refreshed_for_contract
                    known_app_release = known_app_release or _has_app_release_label(
                        issue
                    )
                    running_entry = self._running.get(running_issue_id)
                    if running_entry is not None:
                        running_entry.issue = issue
                    current_state = normalize_state(issue.state)
            contract = evaluate_contract(
                producing_state=producing_state,
                ticket_body=issue.description or "",
                identifier=issue.identifier,
                docs_root=workspace_path / "docs",
                artifact_store_root=(
                    self._artifact_store.root
                    if (
                        cfg.artifacts.require_for_done
                        and self._artifact_store is not None
                    )
                    else None
                ),
            )
            if not contract.passed:
                log.warning(
                    "stage_contract_failed",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    producing_state=producing_state,
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
                    producing_state_raw or producing_state,
                )
                # Pull the freshly-rewound body so the
                # next backend rebuild's first prompt
                # sees the ## Contract Failure note we
                # just appended (full-body fetch — see
                # the comment above the preflight
                # refresh for why minimal would erase
                # description).
                refreshed = await self._refresh_issue_full(cfg, running_issue_id)
                if refreshed is not None:
                    issue = refreshed
                issue = replace(
                    issue,
                    state=(producing_state_raw or producing_state),
                )
                running_entry = self._running.get(running_issue_id)
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
                    producing_state=producing_state,
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
            debug.rewind_count += 1
            if (
                cfg.agent.max_attempts > 0
                and debug.rewind_count > cfg.agent.max_attempts
            ):
                rewind_target = _rewind_budget_target_state(cfg)
                if rewind_target:
                    await asyncio.to_thread(
                        self._tracker_call_update_state,
                        cfg,
                        issue,
                        rewind_target,
                    )
                    issue = replace(issue, state=rewind_target)
                running_entry = self._running.get(running_issue_id)
                if running_entry is not None:
                    running_entry.issue = issue
                log.warning(
                    "rewind_budget_exceeded",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    from_state=producing_state,
                    to_state=current_state,
                    rewind_count=debug.rewind_count,
                    max_attempts=cfg.agent.max_attempts,
                    # F-32: a board with no block/human
                    # terminal lane keeps its state; the
                    # worker still stops.
                    target_state=rewind_target or "(none)",
                )
                return None
        running_entry = self._running.get(running_issue_id)
        if running_entry is not None:
            running_entry.consecutive_empty_turns = 0
            running_entry.hit_empty_response_loop = False
        # F-01: route the *new* lane's backend. The ticket
        # walks several states inside one dispatch, so the
        # kind must be re-resolved from the unrouted config
        # here — not reused from the lane we started in.
        phase_cfg = _config_for_issue_agent(base_cfg, issue)
        if phase_cfg.agent.kind != cfg.agent.kind:
            log.info(
                "stage_backend_rerouted",
                issue_id=issue.id,
                identifier=issue.identifier,
                from_state=producing_state,
                to_state=current_state,
                from_kind=cfg.agent.kind,
                to_kind=phase_cfg.agent.kind,
            )
        cfg = phase_cfg
        if running_entry is not None:
            running_entry.agent_kind = cfg.agent.kind
        client, first_prompt = await self._rebuild_backend_for_phase(
            issue=issue,
            running_issue_id=running_issue_id,
            cfg=cfg,
            workspace_path=workspace_path,
            attempt=attempt,
            doc_language=doc_language,
            old_client=client,
            is_rewind=is_rewind,
            turn_number=debug.completed_turn_count + turn_number,
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
            running_entry.resume_session_id = None
            running_entry.last_completed_turn_event = 0
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
        return _AgentPhaseTransition(
            state=_AgentPhaseState(
                issue=issue,
                cfg=cfg,
                client=client,
                first_prompt=first_prompt,
                current_state=current_state,
                known_app_release=known_app_release,
            ),
            is_rewind=is_rewind,
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
        turn_number: int,
    ) -> tuple[AgentBackend, str]:
        """Tear down `old_client` and rebuild a fresh-context backend.

        Returns `(new_client, new_first_prompt)` so the worker loop can
        rebind both. The caller is responsible for resetting bookkeeping
        on `RunningEntry` (session_id, token high-water marks, etc.) —
        keeping that here would couple this helper to the running-state
        dict and hurt testability.
        """
        try:
            await old_client.stop()
        except Exception as stop_exc:
            running = self._running.get(running_issue_id)
            if running is not None:
                running.backend_cleanup_unconfirmed = True
            log.warning(
                "phase_transition_old_stop_failed",
                issue_id=issue.id,
                identifier=issue.identifier,
                error=str(stop_exc),
            )
            raise
        else:
            self._sync_backend_agent_pid(running_issue_id, None)
        tools: list[Any] = []
        if cfg.tracker.kind == "linear" and cfg.agent.kind == "codex":
            tools.append(linear_graphql_tool())
        new_client = self._build_agent_backend(
            BackendInit(
                cfg=cfg,
                cwd=workspace_path,
                workspace_root=cfg.workspace_root,
                on_event=lambda ev, issue_id=running_issue_id: self._on_codex_event(
                    issue_id, ev
                ),
                on_process_started=lambda pid, issue_id=running_issue_id: (
                    self._sync_backend_agent_pid(issue_id, pid)
                ),
                client_tools=tools,
            )
        )
        # Reset per-dispatch env BEFORE the new backend's subprocess spawns.
        # Forward phase transitions unset SYMPHONY_REWIND_SCOPE; rewinds
        # set it to the JSON of the latest finding rows.
        self._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=is_rewind)
        try:
            self._sync_backend_agent_pid(
                running_issue_id, _backend_agent_pid(new_client)
            )
            try:
                await new_client.start()
            finally:
                self._sync_backend_agent_pid(
                    running_issue_id, _backend_agent_pid(new_client)
                )
            await new_client.initialize()
            skill_context = await asyncio.to_thread(
                render_skill_block, cfg.workflow_path.parent, issue.skills
            )
            first_prompt, _ = build_first_turn_prompt(
                prompt_template=cfg.prompt_template_for_state(issue.state),
                issue=issue,
                attempt=attempt,
                language=doc_language,
                turn_number=turn_number,
                max_turns=cfg.agent.max_total_turns,
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
                artifacts_dir=self._prompt_artifacts_dir(cfg),
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
                running = self._running.get(running_issue_id)
                if running is not None:
                    running.backend_cleanup_unconfirmed = True
                log.warning(
                    "phase_transition_new_stop_failed",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    error=str(stop_exc),
                )
            else:
                self._sync_backend_agent_pid(running_issue_id, None)
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
            log.warning("issue_full_refresh_failed", issue_id=issue_id, error=str(exc))
            self._record_tracker_error(issue_id, exc)
            return None

    async def _rewind_app_release_transition(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        producing_state: str,
        note_body: str,
    ) -> Issue:
        """Persist a release-gate rewind on the local file board."""
        return await asyncio.to_thread(
            ReleaseCycleService(cfg).rewind_transition,
            issue=issue,
            producing_state=producing_state,
            note_body=note_body,
        )

    def _app_release_transition_lock(self, issue_id: str) -> asyncio.Lock | None:
        """Return the lock owned by the current live run for ``issue_id``."""
        running = self._running.get(issue_id)
        if running is None:
            return None
        owned = self._app_release_transition_locks.get(issue_id)
        if owned is None or owned[0] is not running:
            lock = asyncio.Lock()
            self._app_release_transition_locks[issue_id] = (running, lock)
            return lock
        return owned[1]

    async def _enforce_app_release_transition(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        workspace_path: Path,
        producing_state: str,
        known_app_release: bool,
        running_entry: RunningEntry | None = None,
    ) -> tuple[Issue, bool]:
        """Fail closed around every post-transition release authority check."""
        authority_entry = running_entry or self._running.get(issue.id)
        transition_lock = (
            self._app_release_transition_lock(issue.id)
            if self._running.get(issue.id) is authority_entry
            else None
        )
        if transition_lock is not None:
            async with transition_lock:
                return await self._enforce_app_release_transition_guarded(
                    cfg=cfg,
                    issue=issue,
                    workspace_path=workspace_path,
                    producing_state=producing_state,
                    known_app_release=known_app_release,
                    running_entry=authority_entry,
                )
        return await self._enforce_app_release_transition_guarded(
            cfg=cfg,
            issue=issue,
            workspace_path=workspace_path,
            producing_state=producing_state,
            known_app_release=known_app_release,
            running_entry=authority_entry,
        )

    async def _enforce_app_release_transition_guarded(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        workspace_path: Path,
        producing_state: str,
        known_app_release: bool,
        running_entry: RunningEntry | None,
    ) -> tuple[Issue, bool]:
        """Run one serialized release decision and persist a safe rewind on error."""
        try:
            return await self._enforce_app_release_transition_inner(
                cfg=cfg,
                issue=issue,
                workspace_path=workspace_path,
                producing_state=producing_state,
                known_app_release=known_app_release,
                running_entry=running_entry,
            )
        except _ReleaseTransitionAuthorityLost:
            # The board and current gate now belong to a newer run. Rewinding
            # here would let the stale worker mutate (or retire) its peer's
            # release cycle, so ownership loss is a side-effect-free abort.
            raise
        except Exception as exc:
            try:
                rewound = await self._rewind_app_release_transition(
                    cfg=cfg,
                    issue=issue,
                    producing_state=producing_state,
                    note_body=(
                        "Release authority failed closed before delivery.\n\n"
                        f"Infrastructure error: {exc}"
                    ),
                )
            except Exception as rewind_exc:
                log.error(
                    "app_release_authority_rewind_failed",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    authority_error=str(exc),
                    rewind_error=str(rewind_exc),
                )
                raise SymphonyError(
                    "application release authority failed and its Verify rewind "
                    "could not be persisted",
                    authority_error=str(exc),
                    rewind_error=str(rewind_exc),
                ) from rewind_exc
            log.error(
                "app_release_authority_rewound",
                issue_id=issue.id,
                identifier=issue.identifier,
                error=str(exc),
            )
            return rewound, True

    async def _enforce_app_release_transition_inner(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        workspace_path: Path,
        producing_state: str,
        known_app_release: bool,
        running_entry: RunningEntry | None,
    ) -> tuple[Issue, bool]:
        """Gate a local-file-board transition out of Verify.

        Returns ``(issue, rewound)``. The full refresh and machine gate are
        independent of the prose stage-contract mode. Remote adapters do not
        expose the atomic create/update lifecycle API and are rejected before
        any release-gate write.
        """
        refreshed = await self._refresh_issue_full(cfg, issue.id)
        if refreshed is not None:
            issue = refreshed
        # Capture the entry before waiting for the per-run lock. Worker exit
        # may remove it from `_running` immediately after a peer persists the
        # decision, but the waiting caller still belongs to that exact run.
        running = running_entry or self._running.get(issue.id)
        enforce_bound_verifier_authority = bool(
            running is not None
            and running.release_authority_resolved
            and running.known_release_cycle_verifier
        )
        known_app_release = (
            known_app_release
            or (running.known_app_release if running is not None else False)
            or _has_app_release_label(issue)
        )
        if not known_app_release:
            return issue, False
        if cfg.tracker.kind != "file":
            raise SymphonyError(
                "app-release contracts require tracker.kind=file until an adapter "
                "provides atomic repair-cycle create/update support",
                tracker_kind=cfg.tracker.kind,
            )

        gate = cast(
            ReleaseGate | None,
            self._release_registry_call(
                cfg,
                "read_verifier_gate_for_transition",
                lambda registry: registry.get_release_gate_for_verifier(
                    issue.identifier
                ),
            ),
        )
        if gate is None:
            evidence_identity = cast(
                ReleaseEvidenceIdentity | None,
                self._release_registry_call(
                    cfg,
                    "read_retired_verifier_after_transition",
                    lambda registry: registry.get_release_evidence_identity(
                        issue.identifier
                    ),
                ),
            )
            if (
                running is not None
                and running.known_release_cycle_verifier
                and evidence_identity is not None
                and evidence_identity.retired
                and evidence_identity.issue_id == issue.id
                and evidence_identity.finalizer_identifier
                == running.release_gate_finalizer
                and evidence_identity.cycle_generation
                == running.release_gate_generation
            ):
                # A serialized peer already replaced this verifier with the
                # next PENDING cycle. The old issue is now immutable evidence;
                # recovering a gate for it would duplicate repairs/verifiers.
                log.info(
                    "app_release_red_transition_already_reconciled",
                    identifier=issue.identifier,
                    finalizer=evidence_identity.finalizer_identifier,
                    generation=evidence_identity.cycle_generation,
                )
                return issue, False
            if enforce_bound_verifier_authority:
                assert running is not None
                self._require_release_transition_verifier_authority(
                    cfg=cfg,
                    issue=issue,
                    entry=running,
                )
            # Defensive compatibility for a run that was already in flight
            # when the host upgraded. New dispatches always persist this row
            # before their lease is acquired.
            identity = resolve_target_release_identity(
                repository_root=cfg.workflow_path.parent,
                configured_target_branch=cfg.agent.auto_merge_target_branch,
            )
            if identity.errors:
                rewound = await self._rewind_app_release_transition(
                    cfg=cfg,
                    issue=issue,
                    producing_state=producing_state,
                    note_body=(
                        "Release validation could not establish host-owned "
                        "authority before the transition.\n\nEvidence errors:\n- "
                        + "\n- ".join(identity.errors)
                    ),
                )
                return rewound, True
            gate = self._persist_pending_release_gate(
                cfg=cfg,
                gate=self._pending_release_gate(
                    issue=issue,
                    finalizer=identity.finalizer_ticket,
                    contract_sha256=identity.contract_sha256,
                ),
                operation="recover_inflight_pending_gate",
            )
        elif enforce_bound_verifier_authority:
            assert running is not None
            self._require_release_transition_verifier_authority(
                cfg=cfg,
                issue=issue,
                entry=running,
            )
        if running is not None and not enforce_bound_verifier_authority:
            running.known_app_release = True
            running.known_release_cycle_verifier = True
            running.release_gate_finalizer = gate.finalizer_identifier
            running.release_gate_expected_contract_sha256 = (
                gate.expected_contract_sha256
            )
            running.release_gate_cycle_fingerprint = gate.cycle_fingerprint
            running.release_gate_generation = gate.generation

        validation = await asyncio.to_thread(
            validate_release_contract,
            workspace_root=workspace_path,
            repository_root=cfg.workflow_path.parent,
            verifier_ticket=issue.identifier,
            configured_target_branch=cfg.agent.auto_merge_target_branch,
            board_root=cfg.tracker.board_root,
        )
        if enforce_bound_verifier_authority:
            assert running is not None
            self._require_release_transition_verifier_authority(
                cfg=cfg,
                issue=issue,
                entry=running,
            )
        binding_errors: list[str] = []
        approved_for_current_run = (
            gate.status == "approved"
            and running is not None
            and bool(running.run_id)
            and gate.verifier_run_id == running.run_id
            and gate.approved_fingerprint == validation.fingerprint
            and gate.target_branch == validation.target_branch
            and gate.approved_target_sha == validation.target_sha
        )
        if gate.status != "pending" and not approved_for_current_run:
            binding_errors.append("release verifier authority is not pending")
        if gate.verifier_issue_id != issue.id:
            binding_errors.append("release verifier issue id does not match authority")
        if gate.verifier_identifier != issue.identifier:
            binding_errors.append(
                "release verifier identifier does not match authority"
            )
        if gate.expected_contract_sha256 != validation.contract_sha256:
            binding_errors.append(
                "host-owned expected contract hash does not match the current release contract"
            )
        if gate.finalizer_identifier != validation.finalizer_ticket:
            binding_errors.append(
                "host-owned finalizer binding does not match the release contract"
            )
        if binding_errors:
            if (
                gate.status == "pending"
                and validation.contract_sha256
                and gate.finalizer_identifier == validation.finalizer_ticket
                and gate.expected_contract_sha256 != validation.contract_sha256
            ):
                refreshed_pending = self._persist_pending_release_gate(
                    cfg=cfg,
                    gate=self._pending_release_gate(
                        issue=issue,
                        finalizer=gate.finalizer_identifier,
                        contract_sha256=validation.contract_sha256,
                    ),
                    operation="refresh_drifted_pending_release_contract",
                )
                ReleaseCycleService(cfg).restore_verifier_gate_labels(
                    issue=issue,
                    gate=refreshed_pending,
                    verifier_state=_release_verifier_state(cfg),
                )
                binding_errors.append(
                    "host authority was rebound to the new contract; a fresh "
                    "verifier run is required"
                )
            metadata = (
                f"\n\nContract SHA-256: "
                f"`{validation.contract_sha256 or '(unavailable)'}`\n"
                f"Target SHA: `{validation.target_sha or '(unavailable)'}`\n"
                f"Release fingerprint: `{validation.fingerprint}`"
            )
            note_text = validation.note_text
            if validation.evidence_errors:
                note_text += "\n- " + "\n- ".join(binding_errors)
            else:
                note_text = (
                    "Release validation did not pass.\n\nEvidence errors:\n- "
                    + "\n- ".join(binding_errors)
                )
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=producing_state,
                note_body=note_text + metadata,
            )
            return rewound, True
        if validation.passed:
            if approved_for_current_run:
                log.info(
                    "app_release_gate_already_approved",
                    identifier=issue.identifier,
                    target_branch=validation.target_branch,
                    target_sha=validation.target_sha,
                    contract_sha256=validation.contract_sha256,
                )
                return issue, False
            if running is None or not running.run_id:
                rewound = await self._rewind_app_release_transition(
                    cfg=cfg,
                    issue=issue,
                    producing_state=producing_state,
                    note_body=(
                        "Release validation passed, but no active host run lease "
                        "was available to bind the approval."
                    ),
                )
                return rewound, True
            approved = bool(
                self._release_registry_call(
                    cfg,
                    "approve_release_gate",
                    lambda registry: registry.approve_release_gate(
                        finalizer_identifier=gate.finalizer_identifier,
                        verifier_issue_id=gate.verifier_issue_id,
                        verifier_identifier=gate.verifier_identifier,
                        expected_contract_sha256=gate.expected_contract_sha256,
                        expected_cycle_fingerprint=gate.cycle_fingerprint,
                        expected_generation=gate.generation,
                        approved_fingerprint=validation.fingerprint,
                        target_branch=validation.target_branch,
                        target_sha=validation.target_sha,
                        verifier_run_id=running.run_id,
                    ),
                )
            )
            approved_gate = cast(
                ReleaseGate | None,
                self._release_registry_call(
                    cfg,
                    "read_approved_release_gate",
                    lambda registry: registry.get_release_gate(
                        gate.finalizer_identifier
                    ),
                ),
            )
            if (
                not approved
                or approved_gate is None
                or approved_gate.status != "approved"
                or approved_gate.approved_fingerprint != validation.fingerprint
                or approved_gate.approved_target_sha != validation.target_sha
                or approved_gate.target_branch != validation.target_branch
                or approved_gate.verifier_run_id != running.run_id
            ):
                rewound = await self._rewind_app_release_transition(
                    cfg=cfg,
                    issue=issue,
                    producing_state=producing_state,
                    note_body=(
                        "Release validation passed, but the host-owned GREEN "
                        "approval could not be durably persisted."
                    ),
                )
                return rewound, True
            log.info(
                "app_release_gate_passed",
                identifier=issue.identifier,
                target_branch=validation.target_branch,
                target_sha=validation.target_sha,
                contract_sha256=validation.contract_sha256,
            )
            return issue, False

        metadata = (
            f"\n\nContract SHA-256: `{validation.contract_sha256 or '(unavailable)'}`\n"
            f"Target SHA: `{validation.target_sha or '(unavailable)'}`\n"
            f"Release fingerprint: `{validation.fingerprint}`"
        )
        if validation.evidence_errors:
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=producing_state,
                note_body=validation.note_text + metadata,
            )
            return rewound, True

        registry_path = registry_path_for_workflow(cfg.workflow_path)

        def persist_fresh_pending_gate(verifier: Issue) -> None:
            pending = replace(
                self._pending_release_gate(
                    issue=verifier,
                    finalizer=validation.finalizer_ticket,
                    contract_sha256=validation.contract_sha256,
                ),
                cycle_fingerprint=validation.fingerprint,
            )
            registry = RunRegistry(registry_path)
            try:
                registry.replace_pending_release_gate(pending)
                persisted = registry.get_release_gate(validation.finalizer_ticket)
                expected = (
                    pending.finalizer_identifier,
                    pending.verifier_issue_id,
                    pending.verifier_identifier,
                    pending.expected_contract_sha256,
                    pending.cycle_fingerprint,
                    "pending",
                )
                actual = (
                    (
                        persisted.finalizer_identifier,
                        persisted.verifier_issue_id,
                        persisted.verifier_identifier,
                        persisted.expected_contract_sha256,
                        persisted.cycle_fingerprint,
                        persisted.status,
                    )
                    if persisted is not None
                    else None
                )
                if actual != expected:
                    raise SymphonyError(
                        "fresh release verifier authority was not persisted before relink",
                        verifier=verifier.identifier,
                        finalizer=validation.finalizer_ticket,
                    )
            finally:
                registry.close()

        lifecycle = await asyncio.to_thread(
            self._tracker_call_reconcile_release_cycle,
            cfg,
            issue,
            validation,
            issue.agent_kind or cfg.agent.kind,
            before_finalizer_relink=persist_fresh_pending_gate,
        )
        if not lifecycle.passed:
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=producing_state,
                note_body=(
                    validation.note_text
                    + metadata
                    + "\n\nRepair-cycle write failed closed: "
                    + lifecycle.error
                ),
            )
            return rewound, True

        if running is not None:
            retired_identity = cast(
                ReleaseEvidenceIdentity | None,
                self._release_registry_call(
                    cfg,
                    "read_completed_verifier_handoff",
                    lambda registry: registry.get_release_evidence_identity_by_issue_id(
                        issue.id
                    ),
                ),
            )
            if retired_identity is None or (
                retired_identity.issue_id,
                retired_identity.identifier,
                retired_identity.finalizer_identifier,
                retired_identity.role,
                retired_identity.cycle_generation,
                retired_identity.retired,
            ) != (
                issue.id,
                issue.identifier,
                running.release_gate_finalizer,
                "verifier",
                running.release_gate_generation,
                True,
            ):
                raise SymphonyError(
                    "completed release verifier handoff identity could not be proven",
                    verifier=issue.identifier,
                    finalizer=validation.finalizer_ticket,
                )
            running.release_verifier_handoff_complete = True

        log.warning(
            "app_release_repairs_created",
            identifier=issue.identifier,
            fingerprint=validation.fingerprint,
            repair_identifiers=lifecycle.repair_identifiers,
            verifier_identifier=lifecycle.verifier_identifier,
        )
        return issue, False

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
            budget_detail = f"(state_turns={count}, effective_max_state_turns={limit})"
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

    async def _escalate_empty_response_loop(
        self,
        *,
        cfg: ServiceConfig | None,
        entry: RunningEntry,
        issue_id: str,
        cancel_worker: bool,
    ) -> None:
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
        # persist branch above is a no-op then). The pause survives worker
        # exit via `_paused_issue_ids` and is the same gate the operator's
        # manual pause uses, so resume_worker() lifts it.
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
        entry.hit_empty_response_loop = False
        if cancel_worker and entry.worker_task is not None:
            entry.worker_task.cancel()
        entry.cancelled_at = datetime.now(timezone.utc)

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

        def assistant_message_preview(message: Any) -> str:
            """Extract text only from an assistant message.

            Pi and Prime wrap response text in a message object.  The same
            stream also contains user messages and tool results, so requiring
            an assistant role here keeps those echoes from looking like model
            output to the empty-turn guard.
            """
            if not isinstance(message, dict):
                return ""
            kind = str(message.get("role") or message.get("type") or "").lower()
            if kind != "assistant":
                return ""
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                for block in reversed(content):
                    if isinstance(block, dict):
                        text = block.get("text")
                    else:
                        text = block
                    if isinstance(text, str) and text.strip():
                        return text.strip()
            elif isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
            return ""

        # Pi/Prime `message_end` and `turn_end` payloads nest the assistant
        # message under `message`.
        message = payload.get("message")
        preview = assistant_message_preview(message)
        if preview:
            return preview

        # Their terminal `agent_end` payload carries all messages.  Walk
        # backwards so the preview reflects the final assistant response,
        # while skipping user messages and tool-result entries.
        if str(payload.get("type") or "").lower() == "agent_end":
            messages = payload.get("messages")
            if isinstance(messages, list):
                for message in reversed(messages):
                    preview = assistant_message_preview(message)
                    if preview:
                        return preview

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
        if ev_name != EVENT_SESSION_STARTED and entry.resume_session_id:
            sanitized = redact_session_id(event, entry.resume_session_id)
            if isinstance(sanitized, dict):
                event = sanitized
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
        raw_pid = (
            event.get("agent_pid")
            if "agent_pid" in event
            else event.get("codex_app_server_pid")
        )
        pid = _normalize_agent_pid(raw_pid)
        if pid is not None:
            self._sync_backend_agent_pid(issue_id, pid)
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
                self._append_run_event(
                    entry, "approval_denied", {"reason": reason}
                )
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
                (
                    payload.get("session_id")
                    or payload.get("thread_id")
                    or payload.get("threadId")
                )
                if isinstance(payload, dict)
                else None
            )
            if sid:
                entry.thread_id = str(sid)
                entry.session_id = entry.thread_id
                entry.resume_session_id = entry.thread_id
            log.info(
                "agent_session_started",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
            )
            self._append_run_event(entry, "session_started")
        if ev_name == EVENT_TURN_COMPLETED:
            entry.last_completed_turn_event = max(
                entry.last_completed_turn_event, entry.turn_count
            )
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
            self._append_run_event(
                entry,
                "turn_completed",
                {
                    "turn": entry.turn_count,
                    "input_tokens": entry.codex_input_tokens,
                    "cache_input_tokens": entry.codex_cache_input_tokens,
                    "output_tokens": entry.codex_output_tokens,
                    "total_tokens": entry.codex_total_tokens,
                },
            )
            self._record_token_attention_for_turn(entry, cfg)
            self._record_stats_turn(entry)
            # C3 — adaptive token budget. Sample = per-turn state-local
            # total tokens. `_update_token_ema` no-ops on non-positive
            # samples, so a turn with zero token movement (e.g. an event
            # that fires before any usage is reported) is skipped
            # silently rather than dragging the EMA toward zero.
            turn_sample = max(
                entry.codex_state_total_tokens - entry.last_ema_state_total_tokens,
                0,
            )
            if turn_sample > 0:
                # Prefer the source state (captured at worker_turn_started)
                # so a stage that flipped the ticket mid-turn still has its
                # cost attributed correctly. Fall back to current state for
                # event-injection unit tests that bypass turn_started.
                target_state = entry.state_at_turn_start or entry.issue.state
                self._update_token_ema(target_state, turn_sample, cfg)
                entry.last_ema_state_total_tokens = entry.codex_state_total_tokens
            # G2 — empty-response loop guard. A turn whose `current_turn_message`
            # stayed empty produced no fresh preview text. Counter resets on a
            # turn with real preview; crossing the threshold cancels the worker
            # and persists via the existing budget-exhausted plumbing.
            if entry.current_turn_message.strip():
                entry.consecutive_empty_turns = 0
                entry.hit_empty_response_loop = False
            else:
                entry.consecutive_empty_turns += 1
            entry.current_turn_message = ""
            if (
                entry.consecutive_empty_turns >= EMPTY_TURN_LOOP_THRESHOLD
                and entry.cancelled_at is None
            ):
                if entry.state_at_turn_start:
                    entry.hit_empty_response_loop = True
                    log.warning(
                        "empty_response_loop_pending",
                        issue_id=issue_id,
                        identifier=entry.issue.identifier,
                        consecutive_empty_turns=entry.consecutive_empty_turns,
                        threshold=EMPTY_TURN_LOOP_THRESHOLD,
                        state_at_turn_start=entry.state_at_turn_start,
                    )
                else:
                    await self._escalate_empty_response_loop(
                        cfg=cfg,
                        entry=entry,
                        issue_id=issue_id,
                        cancel_worker=True,
                    )
        if ev_name == EVENT_TURN_FAILED:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            stderr_tail = (
                payload.get("stderr_tail") if isinstance(payload, dict) else None
            )
            log.warning(
                "agent_turn_failed",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                turn=entry.turn_count,
                reason=str(reason) if reason else "",
                stderr_tail=stderr_tail if isinstance(stderr_tail, list) else None,
            )
            self._append_run_event(
                entry,
                "turn_failed",
                {
                    "turn": entry.turn_count,
                    "reason": reason,
                    "stderr_lines": stderr_tail,
                },
            )
        if ev_name == EVENT_COMPACTION:
            phase = payload.get("phase") if isinstance(payload, dict) else None
            compaction_reason = (
                payload.get("reason") if isinstance(payload, dict) else None
            )
            tokens_before = (
                payload.get("tokens_before") if isinstance(payload, dict) else None
            )
            log.info(
                "agent_compaction",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                phase=str(phase) if phase else "",
                reason=str(compaction_reason or ""),
                tokens_before=tokens_before,
            )
            self._append_run_event(
                entry,
                "compaction",
                {
                    "phase": phase,
                    "reason": compaction_reason,
                    "tokens_before": tokens_before,
                },
            )
        if ev_name == EVENT_AGENT_RETRY:
            phase = payload.get("phase") if isinstance(payload, dict) else None
            retry_attempt = payload.get("attempt") if isinstance(payload, dict) else None
            retry_error = (
                payload.get("error") or payload.get("final_error")
                if isinstance(payload, dict)
                else None
            )
            log.info(
                "agent_internal_retry",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                phase=str(phase) if phase else "",
                attempt=retry_attempt,
                error=str(retry_error or ""),
            )
            self._append_run_event(
                entry,
                "retry",
                {
                    "phase": phase,
                    "attempt": retry_attempt,
                    "error": retry_error,
                },
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

    async def _on_worker_exit(
        self,
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
        self,
        issue_id: str,
        reason: str,
        error: str | None,
        *,
        owning_task: asyncio.Task[None] | None = None,
        defer_lease_finish: bool = False,
    ) -> None:
        # AF-01 — identity gate before the pop. `owning_task` is only passed
        # by the two real callers (the worker's own `finally` and
        # `_on_worker_task_done`); direct-call test sites and other internal
        # callers omit it, which is treated as "no check" (pre-AF-01
        # behavior) rather than "owned by nobody" — many existing tests
        # exercise this method against entries with `worker_task=None`.
        if owning_task is not None and (
            self._running.get(issue_id) is None
            or self._dispatch_state.entry_foreign_to(issue_id, owning_task)
        ):
            log.warning(
                "worker_exit_stale_task",
                issue_id=issue_id,
                reason=reason,
            )
            return
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
        owned_transition = self._app_release_transition_locks.get(issue_id)
        if (
            entry is not None
            and owned_transition is not None
            and owned_transition[0] is entry
        ):
            self._app_release_transition_locks.pop(issue_id, None)
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
        if not defer_lease_finish:
            self._finish_run_lease(issue_id, entry, reason, error)
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
            if entry.known_app_release_finalizer and cfg is not None:
                refreshed_finalizer = await self._refresh_issue_full(cfg, issue_id)
                if refreshed_finalizer is not None:
                    entry.issue = refreshed_finalizer
                try:
                    finalizer_gate = cast(
                        ReleaseGate | None,
                        self._release_registry_call(
                            cfg,
                            "read_finalizer_gate_at_exit",
                            lambda registry: registry.get_release_gate(
                                entry.release_gate_finalizer or entry.issue.identifier
                            ),
                        ),
                    )
                    if finalizer_gate is None:
                        raise SymphonyError(
                            "application release finalizer authority disappeared",
                            finalizer=entry.issue.identifier,
                        )
                    entry.issue, completion_token = (
                        self._guard_release_finalizer_with_version(
                            cfg=cfg,
                            issue=entry.issue,
                            gate=finalizer_gate,
                            rewind_state=entry.release_finalizer_rewind_state or None,
                            expected_run_id=entry.run_id,
                            require_run_authority=True,
                        )
                    )
                    if _is_release_success_state(cfg, entry.issue.state):
                        finalizer_gate = self._mark_release_finalizer_completed(
                            cfg=cfg,
                            issue=entry.issue,
                            gate=finalizer_gate,
                            completion_token=completion_token,
                            rewind_state=(entry.release_finalizer_rewind_state or None),
                        )
                except Exception as exc:
                    try:
                        entry.issue = await self._rewind_app_release_transition(
                            cfg=cfg,
                            issue=entry.issue,
                            producing_state=(
                                entry.release_finalizer_rewind_state
                                or next(
                                    (
                                        state
                                        for state in reversed(cfg.tracker.active_states)
                                        if normalize_state(state) != "verify"
                                    ),
                                    _release_verifier_state(cfg),
                                )
                            ),
                            note_body=(
                                "Final delivery was stopped at worker exit because "
                                f"the release approval is invalid: {exc}"
                            ),
                        )
                    except Exception as rewind_exc:
                        log.error(
                            "release_finalizer_exit_rewind_failed",
                            issue_id=issue_id,
                            identifier=entry.issue.identifier,
                            gate_error=str(exc),
                            rewind_error=str(rewind_exc),
                        )
                    debug.last_error = str(exc)
                    log.warning(
                        "release_finalizer_exit_refused",
                        issue_id=issue_id,
                        identifier=entry.issue.identifier,
                        error=str(exc),
                    )
                    return
            if entry.release_verifier_handoff_complete:
                self._dispatch_state.cancel_pending_retry(issue_id)
                self._claimed.discard(issue_id)
                self._persisted_retry_attempts.pop(issue_id, None)
                self._clear_issue_flags(issue_id, retry_attempt=True)
                cleanup_started = entry.workspace_cleanup_started
                if (
                    cfg is not None
                    and cfg.agent.auto_commit_on_done
                    and not cleanup_started
                ):
                    await commit_workspace_on_done(
                        entry.workspace_path,
                        identifier=entry.issue.identifier,
                        title=entry.issue.title,
                        exit_reason=reason,
                        state=entry.issue.state,
                        extra_excludes=self._artifact_commit_excludes(cfg),
                    )
                if (
                    cfg is not None
                    and not cleanup_started
                    and self._workspace_manager is not None
                ):
                    await self._workspace_manager.remove(entry.workspace_path)
                log.info(
                    "release_verifier_handoff_completed",
                    issue_id=issue_id,
                    identifier=entry.issue.identifier,
                    finalizer=entry.release_gate_finalizer,
                    generation=entry.release_gate_generation,
                )
                log.info(
                    "worker_exit",
                    issue_id=issue_id,
                    issue_identifier=entry.issue.identifier,
                    reason=reason,
                    error=error,
                )
                await self._notify_observers()
                return
            self._persisted_retry_attempts.pop(issue_id, None)
            self._clear_issue_flags(issue_id, retry_attempt=True)
            if entry.release_gate_exhausted:
                pause_reason = (
                    "application release verification exhausted its rewind budget; "
                    "the verifier remains in Verify and requires operator action"
                )
                self._claimed.add(issue_id)
                self._paused_issue_ids.add(issue_id)
                self._pause_reasons[issue_id] = pause_reason
                self._set_issue_flags(
                    issue_id,
                    paused=True,
                    pause_reason=pause_reason,
                )
                debug.last_error = pause_reason
                log.warning(
                    "release_gate_rewind_budget_exhausted",
                    issue_id=issue_id,
                    issue_identifier=entry.issue.identifier,
                    state=entry.issue.state,
                )
                return
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
                action = (
                    cfg.agent.no_stage_change_action if cfg is not None else "block"
                )
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
                    state_turn_limit = self._max_state_turns_for_state(cfg, state_name)
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
            cleanup_started = entry.workspace_cleanup_started
            release_evidence_only = (
                entry.known_app_release
                or entry.known_release_cycle_verifier
                or entry.known_app_release_finalizer
            )
            # Final History Gate, host-side. The agent cannot be the one to
            # prove delivery: it runs sandboxed and may not reach the object
            # database at all (see `utils.git_sandbox`). The orchestrator is
            # unsandboxed, so it always records the branch locally. It pushes
            # and re-reads the remote tip only when
            # `agent.auto_merge_push_target` is true; local-only workflows
            # never publish the feature branch before the target merge.
            history_unpublished = False
            if (
                release_evidence_only
                and cfg is not None
                and cfg.agent.auto_commit_on_done
                and not cleanup_started
            ):
                await commit_workspace_on_done(
                    # Release evidence is an audit snapshot of an already
                    # host-authorized target; keep this path local-only even
                    # when normal tickets publish their history.
                    entry.workspace_path,
                    identifier=entry.issue.identifier,
                    title=entry.issue.title,
                    exit_reason=reason,
                    state=entry.issue.state,
                    extra_excludes=self._artifact_commit_excludes(cfg),
                )
            elif (
                cfg is not None
                and cfg.agent.auto_commit_on_done
                and not cleanup_started
                and normalize_state(entry.issue.state) in ("done", "human review")
            ):
                history = await finalize_delivery_history(
                    entry.workspace_path,
                    identifier=entry.issue.identifier,
                    title=entry.issue.title,
                    state=entry.issue.state,
                    push=cfg.agent.auto_merge_push_target,
                )
                if history.status == HISTORY_PUSH_FAILED:
                    history_unpublished = True
                    await self._flag_unpublished_history(cfg, entry.issue, history)
            elif (
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
                await commit_workspace_on_done(
                    entry.workspace_path,
                    identifier=entry.issue.identifier,
                    title=entry.issue.title,
                    exit_reason=reason,
                    state=entry.issue.state,
                    extra_excludes=self._artifact_commit_excludes(cfg),
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
            elif (
                release_evidence_only
                and is_terminal
                and cfg is not None
                and self._workspace_manager is not None
            ):
                # Release verifiers/finalizers prove an already-integrated
                # target. Their branch is snapshotted for audit, never merged
                # or delivered through `after_done`.
                if entry.known_app_release_finalizer:
                    try:
                        finalizer_gate = cast(
                            ReleaseGate | None,
                            self._release_registry_call(
                                cfg,
                                "finalizer_pre_cleanup_gate",
                                lambda registry: registry.get_release_gate(
                                    entry.release_gate_finalizer
                                    or entry.issue.identifier
                                ),
                            ),
                        )
                        if finalizer_gate is None:
                            raise SymphonyError(
                                "application release finalizer authority disappeared",
                                finalizer=entry.issue.identifier,
                            )
                        entry.issue = self._guard_release_finalizer(
                            cfg=cfg,
                            issue=entry.issue,
                            gate=finalizer_gate,
                            rewind_state=(entry.release_finalizer_rewind_state or None),
                            expected_run_id=entry.run_id,
                            require_run_authority=True,
                        )
                    except Exception as exc:
                        entry.issue = await self._rewind_app_release_transition(
                            cfg=cfg,
                            issue=entry.issue,
                            producing_state=(
                                entry.release_finalizer_rewind_state
                                or next(
                                    (
                                        state
                                        for state in reversed(cfg.tracker.active_states)
                                        if normalize_state(state) != "verify"
                                    ),
                                    _release_verifier_state(cfg),
                                )
                            ),
                            note_body=(
                                "Final delivery was stopped immediately before "
                                f"cleanup because the approval is invalid: {exc}"
                            ),
                        )
                        return
                await self._workspace_manager.remove(entry.workspace_path)
            elif history_unpublished:
                # Commit exists, remote does not have it. The card is now in
                # `Human Review`; keep the workspace so an operator can finish
                # the push by hand, and skip the Done post-processing that
                # would merge and reap it.
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
                    await self._maybe_run_wiki_sweep(
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
        elif reason == "shutdown_interrupted":
            # A managed stop is a recovery boundary, not a worker failure.
            # Do not persist pause/retry flags; the next service instance will
            # atomically claim the latest completed-turn checkpoint.
            debug.last_error = None
            log.info(
                "worker_shutdown_interrupted",
                issue_id=issue_id,
                issue_identifier=entry.issue.identifier,
            )
        else:
            failure_reason = f"{reason}: {error}" if error else reason
            cleaned_failure = _clean_board_error_message(failure_reason)
            if _is_retryable_worker_error(self._entry_agent_kind(entry), reason, error):
                debug.last_error = cleaned_failure
                log.warning(
                    "worker_error_retry_scheduled",
                    issue_id=issue_id,
                    issue_identifier=entry.issue.identifier,
                    reason=reason,
                    error=error,
                )
            else:
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
                error=cleaned_failure,
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
        self,
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
                agent_pgid = _normalize_agent_pid(entry.agent_pgid)
                if agent_pgid is not None and not skip_kill:
                    killed = kill_process_group(
                        agent_pgid, identity=self._agent_pid_identity(entry)
                    )
                    log.warning(
                        "force_eject_killed_process_group",
                        issue_id=issue_id,
                        identifier=entry.issue.identifier,
                        agent_kind=self._entry_agent_kind(entry),
                        pid=agent_pgid,
                        killed=killed,
                    )
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
        holds_slot: bool = True,
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
        )

    def _retry_cap_exceeded(
        self,
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
        self,
        *,
        issue_id: str,
        identifier: str,
        attempt: int,
        delay_ms: int,
        error: str | None,
        kind: str,
        holds_slot: bool,
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

    def _repark_paused_retry(self, retry: RetryEntry) -> None:
        error = self._pause_reasons.get(retry.issue_id) or retry.error or "paused"
        self._schedule_retry(
            retry.issue_id,
            identifier=retry.identifier,
            attempt=retry.attempt,
            delay_ms=PAUSED_RETRY_HOLD_MS,
            error=error,
            kind=retry.kind,
            holds_slot=True,
        )

    async def _process_retry(self, retry: RetryEntry, cfg: ServiceConfig) -> None:
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
        self,
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
        self,
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
        )

    def _release_retry_ownership(
        self, retry: RetryEntry, *, clear_pause: bool, reason: str
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
        self,
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
            if since_cancel > STALL_FORCE_EJECT_GRACE_S:
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
                agent_pgid = _normalize_agent_pid(entry.agent_pgid)
                kill_failure: Exception | None = None
                if agent_pgid is not None:
                    try:
                        killed = await asyncio.to_thread(
                            kill_process_group,
                            agent_pgid,
                            identity=self._agent_pid_identity(entry),
                        )
                        log.warning(
                            "force_eject_killed_process_group",
                            issue_id=issue_id,
                            identifier=entry.issue.identifier,
                            agent_kind=self._entry_agent_kind(entry),
                            pid=agent_pgid,
                            killed=killed,
                        )
                    except Exception as exc:
                        kill_failure = exc
                self._force_eject_zombie(
                    issue_id, entry, cfg, skip_kill=True
                )
                if kill_failure is not None:
                    raise kill_failure
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
        self, cfg: ServiceConfig, entry: RunningEntry
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

    async def _reconcile_running(self, cfg: ServiceConfig) -> None:
        # Part A: isolate each heartbeat/stall/eject lifecycle.
        now = datetime.now(timezone.utc)
        for issue_id, entry in list(self._running.items()):
            try:
                self._heartbeat_run_lease(issue_id, entry)
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
                        await commit_workspace_on_done(
                            entry.workspace_path,
                            identifier=entry.issue.identifier,
                            title=entry.issue.title,
                            exit_reason="reconcile_terminate_terminal",
                            state=issue.state,
                            extra_excludes=self._artifact_commit_excludes(cfg),
                        )
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
                        await commit_workspace_on_done(
                            entry.workspace_path,
                            identifier=entry.issue.identifier,
                            title=entry.issue.title,
                            exit_reason="reconcile_terminate_inactive",
                            state=issue.state,
                            extra_excludes=self._artifact_commit_excludes(cfg),
                        )
                    await self._workspace_manager.remove(entry.workspace_path)
                finally:
                    entry.workspace_cleanup_finished.set()

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

    def _shared_tracker_client(self, cfg: ServiceConfig) -> TrackerClient:
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
                client = build_tracker_client(cfg)
                self._tracker_client = client
                self._tracker_client_cfg = cfg
                self._tracker_client_closed = False
            return client

    def _invoke_shared_tracker_client(
        self, cfg: ServiceConfig, op: Callable[[TrackerClient], _T]
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

    def _log_tracker_close_race(self) -> None:
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

    def _tracker_call_candidates(self, cfg: ServiceConfig) -> list[Issue]:
        return self._invoke_shared_tracker_client(
            cfg, lambda client: client.fetch_candidate_issues()
        )

    def _tracker_call_states_by_ids(self, cfg: ServiceConfig, ids: list[str]) -> list[Issue]:
        return self._invoke_shared_tracker_client(
            cfg, lambda client: client.fetch_issue_states_by_ids(ids)
        )

    def _tracker_call_full_by_id(self, cfg: ServiceConfig, issue_id: str) -> Issue | None:
        """Single-issue fetch with full body — used by contract validation."""
        return self._invoke_shared_tracker_client(
            cfg, lambda client: client.fetch_issue_full_by_id(issue_id)
        )

    def _tracker_call_terminal_issues(self, cfg: ServiceConfig) -> list[Issue]:
        return self._invoke_shared_tracker_client(
            cfg,
            lambda client: client.fetch_issues_by_states(
                cfg.tracker.terminal_states
            ),
        )

    def _tracker_call_record_agent_kind(
        self, cfg: ServiceConfig, identifier: str, agent_kind: str
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

    def _tracker_call_record_last_agent_kind(
        self, cfg: ServiceConfig, identifier: str, agent_kind: str
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

    # ------------------------------------------------------------------
    # startup cleanup (§8.6)
    # ------------------------------------------------------------------

    async def _startup_release_terminal_guard(
        self,
        cfg: ServiceConfig,
        issue: Issue,
        *,
        owned_cleanup_run_id: str | None = None,
    ) -> tuple[Issue, bool, bool]:
        """Reconcile terminal release authority even when no workspace remains.

        Returns ``(issue, evidence_only, stopped)``. ``stopped`` means the
        ticket was rewound or registry authority could not be proven, so
        ordinary terminal cleanup must not continue.
        """
        if cfg.tracker.kind != "file":
            return issue, False, False
        try:
            verifier_gate = cast(
                ReleaseGate | None,
                self._release_registry_call(
                    cfg,
                    "startup_read_verifier_gate",
                    lambda registry: registry.get_release_gate_for_verifier(
                        issue.identifier
                    ),
                ),
            )
            finalizer_gate = cast(
                ReleaseGate | None,
                self._release_registry_call(
                    cfg,
                    "startup_read_finalizer_gate",
                    lambda registry: registry.get_release_gate(issue.identifier),
                ),
            )
            evidence_identity = cast(
                ReleaseEvidenceIdentity | None,
                self._release_registry_call(
                    cfg,
                    "startup_read_release_evidence_identity",
                    lambda registry: registry.get_release_evidence_identity(
                        issue.identifier
                    ),
                ),
            )
        except SymphonyError as exc:
            identity = resolve_target_release_identity(
                repository_root=cfg.workflow_path.parent,
                configured_target_branch=cfg.agent.auto_merge_target_branch,
            )
            if _is_release_evidence_issue(issue) or not identity.errors:
                log.error(
                    "startup_release_cleanup_refused",
                    identifier=issue.identifier,
                    error=str(exc),
                )
                return issue, True, True
            return issue, False, False

        evidence_only = (
            verifier_gate is not None
            or finalizer_gate is not None
            or evidence_identity is not None
            or _is_release_evidence_issue(issue)
        )
        if not evidence_only:
            return issue, False, False
        if owned_cleanup_run_id is not None:
            owns_cleanup = bool(
                self._release_registry_call(
                    cfg,
                    "startup_heartbeat_release_cleanup",
                    lambda registry: registry.heartbeat(
                        issue_id=issue.id,
                        run_id=owned_cleanup_run_id,
                    ),
                )
            )
            peer_active = not owns_cleanup
        else:
            peer_active = bool(
                self._release_registry_call(
                    cfg,
                    "startup_check_release_issue_lease",
                    lambda registry: registry.has_active_lease(issue.id),
                )
            )
        if peer_active:
            log.info(
                "startup_release_cleanup_skipped_live_peer",
                identifier=issue.identifier,
            )
            return issue, True, True
        finalizer_rewind_state = next(
            (
                state
                for state in reversed(cfg.tracker.active_states)
                if normalize_state(state) != "verify"
            ),
            _release_verifier_state(cfg),
        )
        if finalizer_gate is not None:
            try:
                guarded = self._guard_release_finalizer(
                    cfg=cfg,
                    issue=issue,
                    gate=finalizer_gate,
                    rewind_state=finalizer_rewind_state,
                    allow_active_run=False,
                    require_run_authority=True,
                )
            except SymphonyError as exc:
                rewound = await self._rewind_app_release_transition(
                    cfg=cfg,
                    issue=issue,
                    producing_state=finalizer_rewind_state,
                    note_body=(
                        "Startup stopped final delivery because the release "
                        f"approval is invalid: {exc}"
                    ),
                )
                return rewound, True, True
            return guarded, True, False
        if _is_release_finalizer(issue):
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=finalizer_rewind_state,
                note_body=(
                    "Startup found a terminal release finalizer without "
                    "host-owned approval."
                ),
            )
            return rewound, True, True
        if verifier_gate is not None:
            if verifier_gate.status == "approved" and _is_release_success_state(
                cfg, issue.state
            ):
                return issue, True, False
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=_release_verifier_state(cfg),
                note_body=(
                    "Startup found a terminal release verifier without a "
                    "durable GREEN approval in an explicit success state."
                ),
            )
            return rewound, True, True
        if evidence_identity is not None and evidence_identity.retired:
            return issue, True, False
        if evidence_identity is not None:
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=_release_verifier_state(cfg),
                note_body=(
                    "Startup found current release evidence whose host-owned "
                    "gate is missing."
                ),
            )
            return rewound, True, True
        if _has_app_release_label(issue):
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=_release_verifier_state(cfg),
                note_body=(
                    "Startup found terminal app-release evidence without "
                    "host-owned authority."
                ),
            )
            return rewound, True, True
        return issue, True, False

    async def _startup_terminal_cleanup(self, cfg: ServiceConfig) -> None:
        try:
            terminals = await asyncio.to_thread(self._tracker_call_terminal_issues, cfg)
        except Exception as exc:
            log.warning("startup_terminal_fetch_failed", error=str(exc))
            return
        if self._workspace_manager is None:
            return
        for issue in terminals:
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
                            await commit_workspace_on_done(
                                path,
                                identifier=issue.identifier,
                                title=issue.title,
                                exit_reason="startup_release_evidence_cleanup",
                                state=issue.state,
                            )
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
                if state == "blocked":
                    log.warning(
                        "startup_terminal_cleanup_preserved_blocked_workspace",
                        identifier=issue.identifier,
                        path=str(path),
                    )
                    continue
                if state == "done":
                    branch = f"{SYMPHONY_BRANCH_PREFIX}{issue.identifier}"
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
                    await commit_workspace_on_done(
                        path,
                        identifier=issue.identifier,
                        title=issue.title,
                        exit_reason="startup_terminal_cleanup",
                        state=issue.state,
                    )
                await self._workspace_manager.remove(path)
