"""SQLite-backed run registry for crash-safe dispatch leases."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, cast, overload

from .._shell import process_identity
from ..issue import Issue
from .diagnostics import (
    MAX_DIAGNOSTIC_COUNTER,
    MAX_EVENTS_PER_RUN,
    MAX_RUNS_WITH_DIAGNOSTIC_EVENTS,
    event_payload_json,
    redact_text,
)
from .migrations import LATEST_SCHEMA_VERSION, apply_migrations, current_schema_version


DEFAULT_LEASE_TTL = timedelta(minutes=5)

# Bound how long a locked database can stall a caller. Registry ops run
# inline on the event loop (sqlite connections are thread-affine), so this
# is the worst-case tick delay a contended WAL database can inflict.
SQLITE_BUSY_TIMEOUT_S = 5.0

# Checkpoints contain only the opaque backend session handle and the completed
# workflow boundary needed for recovery. Reject rather than truncate: a
# truncated session id could resume the wrong conversation or silently fall
# back to a fresh one.
MAX_RESUME_SESSION_ID_BYTES = 4_096
MAX_RESUME_SESSION_ID_CHARS = 512
MAX_CHECKPOINT_STATE_BYTES = 256


@dataclass(frozen=True)
class ContinuationCheckpoint:
    """Bounded private value handed only to the backend continuation path."""

    resume_session_id: str
    state: str
    turn: int
    checkpointed_at: datetime

    def __post_init__(self) -> None:
        _validate_checkpoint_fields(
            resume_session_id=self.resume_session_id,
            state=self.state,
            turn=self.turn,
        )
        if not isinstance(self.checkpointed_at, datetime):
            raise ValueError("checkpointed_at must be a datetime")
        object.__setattr__(self, "checkpointed_at", _utc(self.checkpointed_at))


@dataclass(frozen=True)
class ContinuationAcquisition:
    """A new run lease plus the exact predecessor boundary it consumed."""

    run_id: str
    continued_from_run_id: str
    checkpoint: ContinuationCheckpoint


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    issue_id: str
    identifier: str
    title: str
    state: str
    status: str
    workspace_path: Path
    lease_expires_at: datetime | None
    last_progress_at: datetime | None
    attempt: int | None = None
    attempt_kind: str = ""
    agent_kind: str = ""
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    owner_pid: int | None = None
    owner_boot_id: str | None = None
    backend_agent_pid: int | None = None
    backend_process_identity: str | None = None
    input_tokens: int | None = None
    cache_input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    failure_class: str | None = None
    failure_message: str | None = None
    branch_name: str | None = None
    commit_sha: str | None = None
    continued_from_run_id: str | None = None
    checkpoint_state: str | None = None
    checkpoint_turn: int | None = None
    checkpointed_at: datetime | None = None


@dataclass(frozen=True)
class IssueFlags:
    issue_id: str
    retry_attempt: int | None
    budget_exhausted: bool
    paused: bool
    pause_reason: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ReleaseGate:
    """Host-owned pending/approved identity for one release finalizer."""

    finalizer_identifier: str
    verifier_issue_id: str
    verifier_identifier: str
    expected_contract_sha256: str
    cycle_fingerprint: str
    approved_fingerprint: str | None
    status: str
    target_branch: str | None
    approved_target_sha: str | None
    verifier_run_id: str | None
    updated_at: datetime
    finalizer_run_id: str | None = None
    generation: str = ""
    finalizer_completed_at: datetime | None = None
    finalizer_completion_token: str | None = None


@dataclass(frozen=True)
class ReleaseEvidenceIdentity:
    """Append-only host identity for an evidence-only release ticket."""

    issue_id: str
    identifier: str
    finalizer_identifier: str
    role: str
    cycle_generation: str
    retired: bool
    recorded_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ReleaseCycleItem:
    """Host-owned identity for one repair or fresh verifier board ticket."""

    finalizer_identifier: str
    cycle_fingerprint: str
    item_role: str
    item_key: str
    issue_id: str
    identifier: str
    recorded_at: datetime
    updated_at: datetime


_UNSET = object()


def registry_path_for_workflow(workflow_path: str | Path) -> Path:
    return Path(workflow_path).expanduser().resolve().parent / ".symphony" / "state.db"


def clamp_run_history_limit(limit: int) -> int:
    return max(1, min(int(limit), 200))


def _pid_alive_win32(pid: int) -> bool:
    """Windows liveness probe: OpenProcess + GetExitCodeProcess.

    ``os.kill(pid, 0)`` cannot answer this question on Windows — every
    outcome lands in the generic ``OSError`` bucket, which the POSIX
    mapping below reads as "alive". Probe the process object directly:

    - ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` fails with
      ``ERROR_INVALID_PARAMETER`` (87) for a pid the kernel no longer
      knows → dead. ``ERROR_ACCESS_DENIED`` (5) means the process
      exists but belongs to another context → conservatively alive
      (the safe direction for lease honoring is to wait out the TTL).
    - ``GetExitCodeProcess == STILL_ACTIVE (259)`` → alive; any other
      exit code means the process has terminated and only an open
      handle keeps the pid resolvable → dead.

    The theoretical "process legitimately exited with code 259" case
    reads as alive; the lease then lapses via TTL — acceptable for a
    best-effort liveness probe. Handles are always closed; the access
    right is query-only, so no privilege escalation is involved.
    """
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    # These attributes are only defined by ctypes on Windows. Resolve them
    # dynamically so Linux Pyright can analyze this guarded helper; if an
    # unusual Windows runtime lacks either API, conservatively honor the lease.
    win_dll = getattr(ctypes, "WinDLL", None)
    get_last_error = getattr(ctypes, "get_last_error", None)
    if win_dll is None or get_last_error is None:
        return True
    kernel32 = win_dll("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, ctypes.c_ulong(pid)
    )
    if not handle:
        return get_last_error() == ERROR_ACCESS_DENIED
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            # Cannot prove dead — honor the lease until TTL.
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        return _pid_alive_win32(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # PermissionError and friends: the pid exists but isn't ours.
        # Unknown errors default to "alive" — the safe direction is to
        # honor the lease until its TTL rather than double-dispatch.
        return True
    return True


class RunRegistry:
    """Persist one active dispatch lease per issue in `.symphony/state.db`."""

    def __init__(
        self,
        path: Path,
        lease_ttl: timedelta = DEFAULT_LEASE_TTL,
        *,
        owner_pid: int | None = None,
        boot_id: str | None = None,
    ) -> None:
        self._path = path
        self._lease_ttl = lease_ttl
        self._conn: sqlite3.Connection | None = None
        # Owner identity lets a restarted process distinguish "a dead
        # process's leftover lease" (reclaim now) from "a live peer's lease"
        # (honor until TTL). The boot id is unique per registry instance so
        # our own live leases are never self-reclaimed.
        self._owner_pid = owner_pid if owner_pid is not None else os.getpid()
        self._boot_id = boot_id or uuid.uuid4().hex
        self._applied_migrations: list[int] = []
        self._ensure_schema()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def applied_migrations(self) -> tuple[int, ...]:
        """Schema versions this instance applied at open time (doctor uses it)."""
        return tuple(self._applied_migrations)

    def schema_version(self) -> int:
        return current_schema_version(self._connect())

    def schema_is_current(self) -> bool:
        return self.schema_version() >= LATEST_SCHEMA_VERSION

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def acquire_run(
        self,
        issue: Issue,
        *,
        workspace_path: Path,
        attempt: int | None,
        attempt_kind: str,
        agent_kind: str,
        now: datetime | None = None,
    ) -> str | None:
        now = _utc(now)
        expires = now + self._lease_ttl
        run_id = uuid.uuid4().hex
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            if self._active_issue_locked(issue.id, now) is not None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, issue_id, identifier, title, state, attempt,
                    attempt_kind, agent_kind, workspace_path, status, started_at,
                    updated_at, lease_expires_at, last_progress_at, completed_at,
                    owner_pid, owner_boot_id, branch_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    run_id,
                    issue.id,
                    issue.identifier,
                    issue.title,
                    issue.state,
                    attempt,
                    attempt_kind,
                    agent_kind,
                    str(workspace_path),
                    _iso(now),
                    _iso(now),
                    _iso(expires),
                    _iso(now),
                    self._owner_pid,
                    self._boot_id,
                    f"symphony/{issue.identifier}",
                ),
            )
            self._append_attempt_event_best_effort_locked(
                run_id=run_id,
                event_type="run_acquired",
                payload={
                    "attempt": attempt,
                    "attempt_kind": attempt_kind,
                    "agent_kind": agent_kind,
                    "state": issue.state,
                },
                now=now,
            )
            conn.execute("COMMIT")
            return run_id
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def latest_continuation_source(
        self,
        *,
        issue_id: str,
        agent_kind: str,
        state: str,
        issue_updated_at: datetime | None = None,
    ) -> str | None:
        """Return the newest eligible, unconsumed recovery source.

        This is intentionally only a discovery hint. Acquisition repeats all
        predicates under ``BEGIN IMMEDIATE`` so a stale answer is harmless.
        """
        row = self._connect().execute(
            """
            SELECT source.*
            FROM runs AS source
            WHERE source.issue_id = ?
              AND source.agent_kind = ?
              AND source.state = ?
              AND source.checkpoint_state = ?
              AND ((source.status = 'orphaned' AND source.failure_class = 'orphaned')
                   OR source.status = 'shutdown_interrupted')
              AND source.backend_agent_pid IS NULL
              AND source.completed_at IS NOT NULL
              AND source.lease_expires_at IS NULL
              AND source.resume_session_id IS NOT NULL
              AND length(CAST(source.resume_session_id AS BLOB)) BETWEEN 1 AND ?
              AND source.checkpoint_turn BETWEEN 1 AND ?
              AND source.checkpointed_at IS NOT NULL
              AND source.checkpointed_at >= ?
              AND NOT EXISTS (
                  SELECT 1 FROM runs AS successor
                  WHERE successor.continued_from_run_id = source.run_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM runs AS newer
                  WHERE newer.issue_id = source.issue_id
                    AND newer.rowid > source.rowid
              )
            ORDER BY source.checkpointed_at DESC, source.rowid DESC
            LIMIT 1
            """,
            (
                issue_id,
                agent_kind,
                state,
                state,
                MAX_RESUME_SESSION_ID_BYTES,
                MAX_DIAGNOSTIC_COUNTER,
                _iso(issue_updated_at) if issue_updated_at is not None else "",
            ),
        ).fetchone()
        if row is None:
            return None
        try:
            checkpointed_at = _parse(str(row["checkpointed_at"]))
            if checkpointed_at is None:
                return None
            ContinuationCheckpoint(
                resume_session_id=str(row["resume_session_id"]),
                state=str(row["checkpoint_state"]),
                turn=int(row["checkpoint_turn"]),
                checkpointed_at=checkpointed_at,
            )
        except (TypeError, ValueError, OverflowError):
            return None
        return str(row["run_id"])

    def acquire_continuation_run(
        self,
        issue: Issue,
        *,
        continued_from_run_id: str,
        workspace_path: Path,
        attempt: int | None,
        attempt_kind: str,
        agent_kind: str,
        now: datetime | None = None,
    ) -> ContinuationAcquisition | None:
        """Atomically consume one safe checkpoint into a new active run.

        Only kill-confirmed orphan rows and gracefully stopped rows whose
        backend pid was cleared are eligible. The explicit predecessor id and
        exact issue/identifier/agent/state tuple prevent a stale discovery
        result from resuming into a different dispatch.
        """
        now = _utc(now)
        expires = now + self._lease_ttl
        run_id = uuid.uuid4().hex
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Preserve ordinary lease compatibility, while never selecting an
            # active/reclaiming predecessor as a continuation source.
            self._expire_stale_locked(now)
            if self._active_issue_locked(issue.id, now) is not None:
                conn.execute("COMMIT")
                return None
            source = conn.execute(
                """
                SELECT * FROM runs AS source
                WHERE source.run_id = ?
                  AND source.issue_id = ?
                  AND source.identifier = ?
                  AND source.agent_kind = ?
                  AND source.state = ?
                  AND source.checkpoint_state = ?
                  AND ((source.status = 'orphaned' AND source.failure_class = 'orphaned')
                   OR source.status = 'shutdown_interrupted')
                  AND source.backend_agent_pid IS NULL
                  AND source.completed_at IS NOT NULL
                  AND source.lease_expires_at IS NULL
                  AND source.resume_session_id IS NOT NULL
                  AND source.checkpoint_turn IS NOT NULL
                  AND source.checkpointed_at IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM runs AS successor
                      WHERE successor.continued_from_run_id = source.run_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM runs AS newer
                      WHERE newer.issue_id = source.issue_id
                        AND newer.rowid > source.rowid
                  )
                LIMIT 1
                """,
                (
                    continued_from_run_id,
                    issue.id,
                    issue.identifier,
                    agent_kind,
                    issue.state,
                    issue.state,
                ),
            ).fetchone()
            if source is None:
                conn.execute("COMMIT")
                return None
            try:
                checkpointed_at = _parse(str(source["checkpointed_at"]))
                if checkpointed_at is None:
                    conn.execute("ROLLBACK")
                    return None
                checkpoint = ContinuationCheckpoint(
                    resume_session_id=str(source["resume_session_id"]),
                    state=str(source["checkpoint_state"]),
                    turn=int(source["checkpoint_turn"]),
                    checkpointed_at=checkpointed_at,
                )
                if issue.updated_at is not None and _utc(issue.updated_at) > checkpointed_at:
                    conn.execute("ROLLBACK")
                    return None
            except (TypeError, ValueError, OverflowError):
                # Corrupt or manually edited recovery material never degrades
                # into a fresh-looking continuation lease.
                conn.execute("ROLLBACK")
                return None
            try:
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, issue_id, identifier, title, state, attempt,
                        attempt_kind, agent_kind, workspace_path, status,
                        started_at, updated_at, lease_expires_at,
                        last_progress_at, completed_at, owner_pid,
                        owner_boot_id, branch_name, resume_session_id,
                        checkpoint_state, checkpoint_turn, checkpointed_at,
                        continued_from_run_id
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, NULL,
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        run_id,
                        issue.id,
                        issue.identifier,
                        issue.title,
                        issue.state,
                        attempt,
                        attempt_kind,
                        agent_kind,
                        str(workspace_path),
                        _iso(now),
                        _iso(now),
                        _iso(expires),
                        _iso(now),
                        self._owner_pid,
                        self._boot_id,
                        f"symphony/{issue.identifier}",
                        checkpoint.resume_session_id,
                        checkpoint.state,
                        checkpoint.turn,
                        _iso(checkpoint.checkpointed_at),
                        continued_from_run_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # The partial unique index is the final cross-process
                # one-successor fence if discovery raced. Other integrity
                # failures are registry errors and must not look like "no
                # eligible checkpoint" to a caller that may fall back fresh.
                if "runs.continued_from_run_id" not in str(exc):
                    raise
                conn.execute("ROLLBACK")
                return None
            self._append_attempt_event_best_effort_locked(
                run_id=run_id,
                event_type="run_acquired",
                payload={
                    "attempt": attempt,
                    "attempt_kind": attempt_kind,
                    "agent_kind": agent_kind,
                    "state": issue.state,
                },
                now=now,
            )
            conn.execute("COMMIT")
            return ContinuationAcquisition(
                run_id=run_id,
                continued_from_run_id=continued_from_run_id,
                checkpoint=checkpoint,
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    def checkpoint_completed_turn(
        self,
        *,
        issue_id: str,
        run_id: str,
        resume_session_id: str,
        state: str,
        turn: int,
        now: datetime | None = None,
    ) -> bool:
        """Persist the newest fully completed turn for this exact run owner."""
        _validate_checkpoint_fields(
            resume_session_id=resume_session_id,
            state=state,
            turn=turn,
        )
        now = _utc(now)
        cur = self._connect().execute(
            """
            UPDATE runs
            SET resume_session_id = ?, checkpoint_state = ?,
                checkpoint_turn = ?, checkpointed_at = ?, state = ?,
                updated_at = ?
            WHERE issue_id = ?
              AND run_id = ?
              AND status = 'active'
              AND owner_pid = ?
              AND owner_boot_id = ?
              AND (checkpoint_turn IS NULL OR checkpoint_turn < ?)
            """,
            (
                resume_session_id,
                state,
                turn,
                _iso(now),
                state,
                _iso(now),
                issue_id,
                run_id,
                self._owner_pid,
                self._boot_id,
                turn,
            ),
        )
        return cur.rowcount == 1

    def heartbeat(
        self,
        *,
        issue_id: str,
        run_id: str,
        now: datetime | None = None,
        progress_at: datetime | None = None,
        backend_agent_pid: int | None = None,
    ) -> bool:
        now = _utc(now)
        expires = now + self._lease_ttl
        progress = _utc(progress_at) if progress_at is not None else None
        conn = self._connect()
        backend_identity: str | None = None
        if backend_agent_pid is not None:
            existing = conn.execute(
                """
                SELECT backend_agent_pid, backend_process_identity
                FROM runs WHERE issue_id = ? AND run_id = ? AND status = 'active'
                """,
                (issue_id, run_id),
            ).fetchone()
            if (
                existing is not None
                and existing["backend_agent_pid"] == backend_agent_pid
                and existing["backend_process_identity"]
            ):
                backend_identity = str(existing["backend_process_identity"])
            else:
                backend_identity = process_identity(backend_agent_pid)
        if progress is None and backend_agent_pid is None:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (_iso(now), _iso(expires), issue_id, run_id)
        elif progress is None:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?, backend_agent_pid = ?,
                    backend_process_identity = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (
                _iso(now),
                _iso(expires),
                backend_agent_pid,
                backend_identity,
                issue_id,
                run_id,
            )
        elif backend_agent_pid is None:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?, last_progress_at = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (_iso(now), _iso(expires), _iso(progress), issue_id, run_id)
        else:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?, last_progress_at = ?,
                    backend_agent_pid = ?, backend_process_identity = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (
                _iso(now),
                _iso(expires),
                _iso(progress),
                backend_agent_pid,
                backend_identity,
                issue_id,
                run_id,
            )
        cur = conn.execute(sql, args)
        return cur.rowcount > 0


    def clear_backend_agent_pid(
        self,
        *,
        issue_id: str,
        run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Clear process ownership without overloading heartbeat(None)."""
        now = _utc(now)
        expires = now + self._lease_ttl
        cur = self._connect().execute(
            """
            UPDATE runs
            SET updated_at = ?, lease_expires_at = ?, backend_agent_pid = NULL,
                backend_process_identity = NULL
            WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """,
            (_iso(now), _iso(expires), issue_id, run_id),
        )
        return cur.rowcount > 0

    def complete_run(
        self,
        *,
        issue_id: str,
        run_id: str,
        status: str,
        now: datetime | None = None,
        state: str | None = None,
        input_tokens: int | None = None,
        cache_input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        failure_class: str | None = None,
        failure_message: str | None = None,
        commit_sha: str | None = None,
    ) -> bool:
        """Complete a run while preserving source compatibility for old callers.

        The terminal status and lease release are authoritative. Explorer
        summary fields and the completion event are best-effort inside a
        savepoint, so malformed or unavailable telemetry cannot strand a run.
        """
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = conn.execute(
                "SELECT * FROM runs WHERE issue_id = ? AND run_id = ?",
                (issue_id, run_id),
            ).fetchone()
            if current is None:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                """
                UPDATE runs
                SET status = ?, updated_at = ?, completed_at = ?,
                    lease_expires_at = NULL
                WHERE issue_id = ? AND run_id = ?
                """,
                (status, _iso(now), _iso(now), issue_id, run_id),
            )
            try:
                conn.execute("SAVEPOINT run_diagnostic")
            except Exception:
                conn.execute("COMMIT")
                return True
            try:
                values = {
                    "state": state if state is not None else str(current["state"]),
                    "input_tokens": _optional_nonnegative(
                        input_tokens, current["input_tokens"]
                    ),
                    "cache_input_tokens": _optional_nonnegative(
                        cache_input_tokens, current["cache_input_tokens"]
                    ),
                    "output_tokens": _optional_nonnegative(
                        output_tokens, current["output_tokens"]
                    ),
                    "total_tokens": _optional_nonnegative(
                        total_tokens, current["total_tokens"]
                    ),
                    "failure_class": (
                        redact_text(failure_class, 256) if failure_class else None
                    ),
                    "failure_message": (
                        redact_text(failure_message) if failure_message else None
                    ),
                    "commit_sha": _valid_sha(commit_sha) or current["commit_sha"],
                }
                conn.execute(
                    """
                    UPDATE runs
                    SET state = ?, input_tokens = ?, cache_input_tokens = ?,
                        output_tokens = ?, total_tokens = ?, failure_class = ?,
                        failure_message = ?, commit_sha = ?
                    WHERE issue_id = ? AND run_id = ?
                    """,
                    (
                        values["state"],
                        values["input_tokens"],
                        values["cache_input_tokens"],
                        values["output_tokens"],
                        values["total_tokens"],
                        values["failure_class"],
                        values["failure_message"],
                        values["commit_sha"],
                        issue_id,
                        run_id,
                    ),
                )
                self._append_attempt_event_locked(
                    run_id=run_id,
                    event_type="run_completed",
                    payload={"status": status, **values},
                    now=now,
                )
                self._prune_terminal_diagnostics_locked()
            except Exception:
                conn.execute("ROLLBACK TO run_diagnostic")
            finally:
                conn.execute("RELEASE run_diagnostic")
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def append_attempt_event(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Append one bounded event without waiting behind authoritative writers."""
        now = _utc(now)
        conn = self._connect()
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        prior_busy_timeout_ms = int(row[0]) if row is not None else 0
        began = False
        try:
            # This method is observational and runs on the asyncio control
            # thread. Drop on cross-process contention instead of delaying
            # worker events or authoritative lease heartbeats for up to 5s.
            conn.execute("PRAGMA busy_timeout = 0")
            conn.execute("BEGIN IMMEDIATE")
            began = True
            owned = conn.execute(
                """
                SELECT status, owner_pid, owner_boot_id
                FROM runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if (
                owned is None
                or str(owned["status"]) != "active"
                or owned["owner_pid"] != self._owner_pid
                or owned["owner_boot_id"] != self._boot_id
            ):
                conn.execute("ROLLBACK")
                began = False
                return False
            normalized_json = event_payload_json(event_type, payload)
            self._append_attempt_event_locked(
                run_id=run_id,
                event_type=event_type,
                payload_json=normalized_json,
                now=now,
            )
            if event_type in {"turn_completed", "workspace_updated"}:
                normalized = json.loads(normalized_json)
                assignments: list[str] = []
                values: list[Any] = []
                for field in (
                    "input_tokens",
                    "cache_input_tokens",
                    "output_tokens",
                    "total_tokens",
                ):
                    if field in normalized:
                        assignments.append(f"{field} = ?")
                        values.append(normalized[field])
                if normalized.get("commit_sha"):
                    assignments.append("commit_sha = ?")
                    values.append(normalized["commit_sha"])
                if assignments:
                    values.append(run_id)
                    conn.execute(
                        f"UPDATE runs SET {', '.join(assignments)} WHERE run_id = ?",
                        values,
                    )
            conn.execute("COMMIT")
            began = False
            return True
        except Exception:
            if began and conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute(f"PRAGMA busy_timeout = {prior_busy_timeout_ms}")

    def _append_attempt_event_locked(
        self,
        *,
        run_id: str,
        event_type: str,
        now: datetime,
        payload: Mapping[str, Any] | None = None,
        payload_json: str | None = None,
    ) -> None:
        encoded = payload_json if payload_json is not None else event_payload_json(event_type, payload)
        conn = self._connect()
        conn.execute(
            "INSERT INTO attempt_events(run_id, event_type, created_at, payload_json) VALUES (?, ?, ?, ?)",
            (run_id, event_type, _iso(now), encoded),
        )
        conn.execute(
            """
            DELETE FROM attempt_events
            WHERE run_id = ? AND event_id NOT IN (
                SELECT event_id FROM attempt_events
                WHERE run_id = ? ORDER BY event_id DESC LIMIT ?
            )
            """,
            (run_id, run_id, MAX_EVENTS_PER_RUN),
        )

    def _append_attempt_event_best_effort_locked(
        self,
        *,
        run_id: str,
        event_type: str,
        now: datetime,
        payload: Mapping[str, Any] | None = None,
        prune_terminal: bool = False,
    ) -> None:
        """Fence optional telemetry so it cannot roll back an owning transaction."""
        conn = self._connect()
        try:
            conn.execute("SAVEPOINT run_diagnostic")
        except Exception:
            return
        try:
            self._append_attempt_event_locked(
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                now=now,
            )
            if prune_terminal:
                self._prune_terminal_diagnostics_locked()
        except Exception:
            conn.execute("ROLLBACK TO run_diagnostic")
        finally:
            conn.execute("RELEASE run_diagnostic")

    def _prune_terminal_diagnostics_locked(self) -> None:
        """Keep events/excerpts only for the newest bounded terminal run set."""
        conn = self._connect()
        stale = """
            SELECT run_id FROM runs
            WHERE status NOT IN ('active', 'reclaiming')
            ORDER BY completed_at DESC, rowid DESC
            LIMIT -1 OFFSET ?
        """
        conn.execute(
            f"DELETE FROM attempt_events WHERE run_id IN ({stale})",
            (MAX_RUNS_WITH_DIAGNOSTIC_EVENTS,),
        )
        conn.execute(
            f"UPDATE runs SET failure_message = NULL WHERE run_id IN ({stale})",
            (MAX_RUNS_WITH_DIAGNOSTIC_EVENTS,),
        )

    def has_active_lease(self, issue_id: str, now: datetime | None = None) -> bool:
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            found = self._active_issue_locked(issue_id, now) is not None
            conn.execute("COMMIT")
            return found
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def active_leases(self, now: datetime | None = None) -> list[RunRecord]:
        now = _utc(now)
        self.expire_stale(now=now)
        rows = self._connect().execute(
            """
            SELECT * FROM runs
            WHERE status = 'reclaiming'
               OR (status = 'active' AND lease_expires_at > ?)
            ORDER BY started_at, run_id
            """,
            (_iso(now),),
        )
        return [_record(row) for row in rows.fetchall()]

    def bind_release_verifier_run(
        self,
        *,
        gate: ReleaseGate,
        verifier_run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Bind one exact active run to the current pending verifier cycle."""
        if gate.status != "pending":
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            active = self._active_issue_locked(gate.verifier_issue_id, now)
            if (
                active is None
                or str(active["run_id"]) != verifier_run_id
                or str(active["identifier"]) != gate.verifier_identifier
                or str(active["status"]) != "active"
            ):
                conn.execute("ROLLBACK")
                return False
            current = conn.execute(
                """
                SELECT verifier_run_id, updated_at FROM release_gates
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND status = 'pending'
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                ),
            ).fetchone()
            if current is None:
                conn.execute("ROLLBACK")
                return False
            if str(active["started_at"]) < str(current["updated_at"]):
                conn.execute("ROLLBACK")
                return False
            bound_run_id = current["verifier_run_id"]
            if bound_run_id and str(bound_run_id) != verifier_run_id:
                prior = conn.execute(
                    "SELECT status, lease_expires_at FROM runs WHERE run_id = ?",
                    (str(bound_run_id),),
                ).fetchone()
                if prior is not None and (
                    str(prior["status"]) == "reclaiming"
                    or (
                        str(prior["status"]) == "active"
                        and prior["lease_expires_at"] is not None
                        and str(prior["lease_expires_at"]) > _iso(now)
                    )
                ):
                    conn.execute("ROLLBACK")
                    return False
            cur = conn.execute(
                """
                UPDATE release_gates SET verifier_run_id = ?
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND status = 'pending'
                """,
                (
                    verifier_run_id,
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                ),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def bind_release_finalizer_run(
        self,
        *,
        gate: ReleaseGate,
        finalizer_issue_id: str,
        finalizer_run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Bind an exact active finalizer lease to the approved gate cycle."""
        if gate.status != "approved":
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            active = self._active_issue_locked(finalizer_issue_id, now)
            if (
                active is None
                or str(active["run_id"]) != finalizer_run_id
                or str(active["identifier"]) != gate.finalizer_identifier
                or str(active["status"]) != "active"
            ):
                conn.execute("ROLLBACK")
                return False
            if self._active_issue_locked(gate.verifier_issue_id, now) is not None:
                conn.execute("ROLLBACK")
                return False
            current = conn.execute(
                """
                SELECT updated_at, finalizer_run_id, finalizer_completed_at
                FROM release_gates
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND approved_fingerprint = ?
                  AND target_branch = ?
                  AND approved_target_sha = ?
                  AND verifier_run_id = ?
                  AND status = 'approved'
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.approved_fingerprint,
                    gate.target_branch,
                    gate.approved_target_sha,
                    gate.verifier_run_id,
                ),
            ).fetchone()
            if current is None or str(active["started_at"]) < str(
                current["updated_at"]
            ):
                conn.execute("ROLLBACK")
                return False
            prior_finalizer_run_id = current["finalizer_run_id"]
            if (
                current["finalizer_completed_at"] is not None
                or (
                    prior_finalizer_run_id is not None
                    and str(prior_finalizer_run_id) != finalizer_run_id
                    and self._run_is_live_locked(
                        str(prior_finalizer_run_id), now
                    )
                )
            ):
                conn.execute("ROLLBACK")
                return False
            cur = conn.execute(
                """
                UPDATE release_gates SET finalizer_run_id = ?
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND approved_fingerprint = ?
                  AND target_branch = ?
                  AND approved_target_sha = ?
                  AND verifier_run_id = ?
                  AND status = 'approved'
                  AND finalizer_completed_at IS NULL
                """,
                (
                    finalizer_run_id,
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.approved_fingerprint,
                    gate.target_branch,
                    gate.approved_target_sha,
                    gate.verifier_run_id,
                ),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def release_finalizer_run_is_authorized(
        self,
        *,
        gate: ReleaseGate,
        finalizer_issue_id: str,
        allow_active: bool = True,
        now: datetime | None = None,
    ) -> bool:
        """Check current gate and exact bound run in one read transaction."""
        if gate.status != "approved" or not gate.finalizer_run_id:
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            row = conn.execute(
                """
                SELECT 1
                FROM release_gates AS gate
                JOIN runs AS run ON run.run_id = gate.finalizer_run_id
                WHERE gate.finalizer_identifier = ?
                  AND gate.verifier_issue_id = ?
                  AND gate.verifier_identifier = ?
                  AND gate.expected_contract_sha256 = ?
                  AND gate.cycle_fingerprint = ?
                  AND gate.generation = ?
                  AND gate.approved_fingerprint = ?
                  AND gate.target_branch = ?
                  AND gate.approved_target_sha = ?
                  AND gate.verifier_run_id = ?
                  AND gate.finalizer_run_id = ?
                  AND gate.status = 'approved'
                  AND (
                    (gate.finalizer_completed_at IS NULL AND ? IS NULL)
                    OR gate.finalizer_completed_at = ?
                  )
                  AND (
                    (gate.finalizer_completion_token IS NULL AND ? IS NULL)
                    OR gate.finalizer_completion_token = ?
                  )
                  AND run.issue_id = ?
                  AND run.identifier = gate.finalizer_identifier
                  AND (
                    (? = 1 AND run.status = 'active' AND run.lease_expires_at > ?)
                    OR (
                        run.status = 'normal'
                        AND run.completed_at IS NOT NULL
                        AND gate.finalizer_completed_at IS NOT NULL
                        AND gate.finalizer_completion_token IS NOT NULL
                    )
                  )
                LIMIT 1
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.approved_fingerprint,
                    gate.target_branch,
                    gate.approved_target_sha,
                    gate.verifier_run_id,
                    gate.finalizer_run_id,
                    (
                        _iso(gate.finalizer_completed_at)
                        if gate.finalizer_completed_at is not None
                        else None
                    ),
                    (
                        _iso(gate.finalizer_completed_at)
                        if gate.finalizer_completed_at is not None
                        else None
                    ),
                    gate.finalizer_completion_token,
                    gate.finalizer_completion_token,
                    finalizer_issue_id,
                    1 if allow_active else 0,
                    _iso(now),
                ),
            ).fetchone()
            conn.execute("COMMIT")
            return row is not None
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def release_verifier_run_is_authorized(
        self,
        *,
        gate: ReleaseGate,
        verifier_issue_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Check the exact current gate generation and active verifier run."""
        if not gate.verifier_run_id:
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            row = conn.execute(
                """
                SELECT 1
                FROM release_gates AS gate
                JOIN runs AS run ON run.run_id = gate.verifier_run_id
                WHERE gate.finalizer_identifier = ?
                  AND gate.verifier_issue_id = ?
                  AND gate.verifier_identifier = ?
                  AND gate.expected_contract_sha256 = ?
                  AND gate.cycle_fingerprint = ?
                  AND gate.generation = ?
                  AND gate.verifier_run_id = ?
                  AND gate.status IN ('pending', 'approved')
                  AND run.issue_id = ?
                  AND run.identifier = gate.verifier_identifier
                  AND run.status = 'active'
                  AND run.lease_expires_at > ?
                LIMIT 1
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.verifier_run_id,
                    verifier_issue_id,
                    _iso(now),
                ),
            ).fetchone()
            conn.execute("COMMIT")
            return row is not None
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def mark_release_finalizer_completed(
        self,
        *,
        gate: ReleaseGate,
        finalizer_issue_id: str,
        completion_token: str,
        now: datetime | None = None,
    ) -> bool:
        """Persist terminal delivery proof for the exact active finalizer run."""
        if (
            gate.status != "approved"
            or not gate.finalizer_run_id
            or not completion_token.strip()
        ):
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            active = self._active_issue_locked(finalizer_issue_id, now)
            if (
                active is None
                or str(active["run_id"]) != gate.finalizer_run_id
                or str(active["identifier"]) != gate.finalizer_identifier
                or str(active["status"]) != "active"
                or self._active_issue_locked(gate.verifier_issue_id, now) is not None
            ):
                conn.execute("ROLLBACK")
                return False
            cur = conn.execute(
                """
                UPDATE release_gates
                SET finalizer_completed_at = COALESCE(finalizer_completed_at, ?),
                    finalizer_completion_token = COALESCE(
                        finalizer_completion_token, ?
                    )
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND approved_fingerprint = ?
                  AND target_branch = ?
                  AND approved_target_sha = ?
                  AND verifier_run_id = ?
                  AND finalizer_run_id = ?
                  AND status = 'approved'
                """,
                (
                    _iso(now),
                    completion_token,
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.approved_fingerprint,
                    gate.target_branch,
                    gate.approved_target_sha,
                    gate.verifier_run_id,
                    gate.finalizer_run_id,
                ),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def replace_pending_release_gate(
        self,
        gate: ReleaseGate,
        *,
        now: datetime | None = None,
        invalidating_finalizer_run_id: str | None = None,
    ) -> ReleaseGate:
        """Atomically invalidate approval and create a new cycle generation.

        A live finalizer normally fences replacement.  The one exception is
        the exact finalizer run that discovered its own approval became stale;
        it may invalidate that cycle before releasing its lease.  The caller
        must provide that bound run id, which is checked under the same write
        transaction as the replacement.
        """
        if gate.status != "pending":
            raise ValueError("replacement release gate must have pending status")
        now = _utc(now)
        generation = uuid.uuid4().hex
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            prior = conn.execute(
                """
                SELECT verifier_issue_id, verifier_identifier, verifier_run_id,
                       generation, finalizer_run_id
                FROM release_gates WHERE finalizer_identifier = ?
                """,
                (gate.finalizer_identifier,),
            ).fetchone()
            if prior is not None:
                self._expire_stale_locked(now)
                prior_verifier_run_id = prior["verifier_run_id"]
                active_verifier = self._active_issue_locked(
                    str(prior["verifier_issue_id"]), now
                )
                if active_verifier is not None and (
                    prior_verifier_run_id is None
                    or str(active_verifier["run_id"])
                    != str(prior_verifier_run_id)
                ):
                    raise RuntimeError(
                        "release verifier cleanup or foreign lease must finish "
                        "before replacing its gate"
                    )
                prior_finalizer_run_id = prior["finalizer_run_id"]
                active_finalizer = self._active_identifier_locked(
                    gate.finalizer_identifier, now
                )
                if invalidating_finalizer_run_id is not None:
                    if (
                        prior_finalizer_run_id is None
                        or str(prior_finalizer_run_id)
                        != invalidating_finalizer_run_id
                        or active_finalizer is None
                        or str(active_finalizer["run_id"])
                        != invalidating_finalizer_run_id
                    ):
                        raise RuntimeError(
                            "only the exact active release finalizer may invalidate "
                            "its gate"
                        )
                elif active_finalizer is not None:
                    raise RuntimeError(
                        "active release finalizer must finish before replacing its gate"
                    )
                conn.execute(
                    """
                    INSERT INTO release_evidence_issues (
                        issue_id, identifier, finalizer_identifier, role,
                        cycle_generation, retired, recorded_at, updated_at
                    ) VALUES (?, ?, ?, 'verifier', ?, 1, ?, ?)
                    ON CONFLICT(issue_id) DO UPDATE SET
                        identifier = excluded.identifier,
                        finalizer_identifier = excluded.finalizer_identifier,
                        role = 'verifier',
                        cycle_generation = excluded.cycle_generation,
                        retired = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(prior["verifier_issue_id"]),
                        str(prior["verifier_identifier"]),
                        gate.finalizer_identifier,
                        str(prior["generation"] or ""),
                        _iso(now),
                        _iso(now),
                    ),
                )
            conn.execute(
                """
                INSERT INTO release_gates (
                    finalizer_identifier, verifier_issue_id, verifier_identifier,
                    expected_contract_sha256, cycle_fingerprint,
                    approved_fingerprint, status, target_branch,
                    approved_target_sha, verifier_run_id, finalizer_run_id,
                    generation, finalizer_completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, 'pending', NULL, NULL, NULL, NULL,
                          ?, NULL, ?)
                ON CONFLICT(finalizer_identifier) DO UPDATE SET
                    verifier_issue_id = excluded.verifier_issue_id,
                    verifier_identifier = excluded.verifier_identifier,
                    expected_contract_sha256 = excluded.expected_contract_sha256,
                    cycle_fingerprint = excluded.cycle_fingerprint,
                    approved_fingerprint = NULL,
                    status = 'pending',
                    target_branch = NULL,
                    approved_target_sha = NULL,
                    verifier_run_id = NULL,
                    finalizer_run_id = NULL,
                    generation = excluded.generation,
                    finalizer_completed_at = NULL,
                    finalizer_completion_token = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    generation,
                    _iso(now),
                ),
            )
            conn.execute(
                """
                INSERT INTO release_evidence_issues (
                    issue_id, identifier, finalizer_identifier, role,
                    cycle_generation, retired, recorded_at, updated_at
                ) VALUES (?, ?, ?, 'verifier', ?, 0, ?, ?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    identifier = excluded.identifier,
                    finalizer_identifier = excluded.finalizer_identifier,
                    role = 'verifier',
                    cycle_generation = excluded.cycle_generation,
                    retired = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.finalizer_identifier,
                    generation,
                    _iso(now),
                    _iso(now),
                ),
            )
            row = conn.execute(
                "SELECT * FROM release_gates WHERE finalizer_identifier = ?",
                (gate.finalizer_identifier,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if row is None:
            raise RuntimeError("pending release gate disappeared after replacement")
        return _release_gate(row)

    def approve_release_gate(
        self,
        *,
        finalizer_identifier: str,
        verifier_issue_id: str,
        verifier_identifier: str,
        expected_contract_sha256: str,
        expected_cycle_fingerprint: str,
        expected_generation: str,
        approved_fingerprint: str,
        target_branch: str,
        target_sha: str,
        verifier_run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Approve the exact pending tuple and exact active run atomically."""
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            active = self._active_issue_locked(verifier_issue_id, now)
            if (
                active is None
                or str(active["run_id"]) != verifier_run_id
                or str(active["identifier"]) != verifier_identifier
                or str(active["status"]) != "active"
            ):
                conn.execute("ROLLBACK")
                return False
            pending = conn.execute(
                """
                SELECT updated_at, verifier_run_id FROM release_gates
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND status = 'pending'
                """,
                (
                    finalizer_identifier,
                    verifier_issue_id,
                    verifier_identifier,
                    expected_contract_sha256,
                    expected_cycle_fingerprint,
                    expected_generation,
                ),
            ).fetchone()
            if (
                pending is None
                or str(pending["verifier_run_id"] or "") != verifier_run_id
                or str(active["started_at"]) < str(pending["updated_at"])
            ):
                conn.execute("ROLLBACK")
                return False
            cur = conn.execute(
                """
                UPDATE release_gates
                SET status = 'approved', target_branch = ?, approved_target_sha = ?,
                    approved_fingerprint = ?, verifier_run_id = ?,
                    finalizer_run_id = NULL, updated_at = ?
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND verifier_run_id = ?
                  AND status = 'pending'
                """,
                (
                    target_branch,
                    target_sha,
                    approved_fingerprint,
                    verifier_run_id,
                    _iso(now),
                    finalizer_identifier,
                    verifier_issue_id,
                    verifier_identifier,
                    expected_contract_sha256,
                    expected_cycle_fingerprint,
                    expected_generation,
                    verifier_run_id,
                ),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_release_gate(
        self, finalizer_identifier: str
    ) -> ReleaseGate | None:
        row = self._connect().execute(
            "SELECT * FROM release_gates WHERE finalizer_identifier = ?",
            (finalizer_identifier,),
        ).fetchone()
        return _release_gate(row) if row is not None else None

    def get_release_gate_for_verifier(
        self, verifier_identifier: str
    ) -> ReleaseGate | None:
        row = self._connect().execute(
            "SELECT * FROM release_gates WHERE verifier_identifier = ?",
            (verifier_identifier,),
        ).fetchone()
        return _release_gate(row) if row is not None else None

    def get_release_evidence_identity(
        self, identifier: str
    ) -> ReleaseEvidenceIdentity | None:
        row = self._connect().execute(
            "SELECT * FROM release_evidence_issues WHERE identifier = ?",
            (identifier,),
        ).fetchone()
        return _release_evidence_identity(row) if row is not None else None

    def get_release_evidence_identity_by_issue_id(
        self, issue_id: str
    ) -> ReleaseEvidenceIdentity | None:
        """Return one evidence identity by its stable tracker issue id."""
        row = self._connect().execute(
            "SELECT * FROM release_evidence_issues WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
        return _release_evidence_identity(row) if row is not None else None

    def get_release_cycle_item(
        self,
        *,
        finalizer_identifier: str,
        cycle_fingerprint: str,
        item_role: str,
        item_key: str,
    ) -> ReleaseCycleItem | None:
        """Return the host-owned ticket identity for one lifecycle item."""
        row = self._connect().execute(
            """
            SELECT * FROM release_cycle_items
            WHERE finalizer_identifier = ?
              AND cycle_fingerprint = ?
              AND item_role = ?
              AND item_key = ?
            """,
            (
                finalizer_identifier,
                cycle_fingerprint,
                item_role,
                item_key,
            ),
        ).fetchone()
        return _release_cycle_item(row) if row is not None else None

    def record_release_cycle_item(
        self,
        *,
        finalizer_identifier: str,
        cycle_fingerprint: str,
        item_role: str,
        item_key: str,
        issue: Issue,
        now: datetime | None = None,
    ) -> ReleaseCycleItem:
        """Bind one logical release-cycle item to exactly one board ticket.

        The mapping is immutable. A retry may record the same tuple again,
        but it may never redirect a repair/verifier key to a different ticket
        or reuse one ticket for a different logical item.
        """
        return self._record_release_cycle_item_identity(
            finalizer_identifier=finalizer_identifier,
            cycle_fingerprint=cycle_fingerprint,
            item_role=item_role,
            item_key=item_key,
            issue_id=issue.id,
            identifier=issue.identifier,
            now=now,
        )

    def reserve_release_cycle_item(
        self,
        *,
        finalizer_identifier: str,
        cycle_fingerprint: str,
        item_role: str,
        item_key: str,
        identifier: str,
        now: datetime | None = None,
    ) -> ReleaseCycleItem:
        """Durably reserve a deterministic local-board ticket before create.

        File-board creation and SQLite cannot share one transaction.  By
        writing the exact host-derived identifier first, a crash after the
        board write can be resumed without trusting worker-editable labels or
        allocating a duplicate ticket.  File-board issue ids equal their
        identifiers, so the final readback can only confirm this same tuple.
        """
        return self._record_release_cycle_item_identity(
            finalizer_identifier=finalizer_identifier,
            cycle_fingerprint=cycle_fingerprint,
            item_role=item_role,
            item_key=item_key,
            issue_id=identifier,
            identifier=identifier,
            now=now,
        )

    def _record_release_cycle_item_identity(
        self,
        *,
        finalizer_identifier: str,
        cycle_fingerprint: str,
        item_role: str,
        item_key: str,
        issue_id: str,
        identifier: str,
        now: datetime | None,
    ) -> ReleaseCycleItem:
        if item_role not in {"repair", "verifier"}:
            raise ValueError("release cycle item role must be repair or verifier")
        if not all(
            value.strip()
            for value in (
                finalizer_identifier,
                cycle_fingerprint,
                item_key,
                issue_id,
                identifier,
            )
        ):
            raise ValueError("release cycle item identity fields must be non-empty")
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                """
                SELECT * FROM release_cycle_items
                WHERE finalizer_identifier = ?
                  AND cycle_fingerprint = ?
                  AND item_role = ?
                  AND item_key = ?
                """,
                (
                    finalizer_identifier,
                    cycle_fingerprint,
                    item_role,
                    item_key,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["issue_id"]) != issue_id
                    or str(existing["identifier"]) != identifier
                ):
                    raise RuntimeError(
                        "release cycle item is already bound to a different ticket"
                    )
                conn.execute(
                    """
                    UPDATE release_cycle_items SET updated_at = ?
                    WHERE finalizer_identifier = ?
                      AND cycle_fingerprint = ?
                      AND item_role = ?
                      AND item_key = ?
                    """,
                    (
                        _iso(now),
                        finalizer_identifier,
                        cycle_fingerprint,
                        item_role,
                        item_key,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO release_cycle_items (
                        finalizer_identifier, cycle_fingerprint, item_role,
                        item_key, issue_id, identifier, recorded_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        finalizer_identifier,
                        cycle_fingerprint,
                        item_role,
                        item_key,
                        issue_id,
                        identifier,
                        _iso(now),
                        _iso(now),
                    ),
                )
            row = conn.execute(
                """
                SELECT * FROM release_cycle_items
                WHERE finalizer_identifier = ?
                  AND cycle_fingerprint = ?
                  AND item_role = ?
                  AND item_key = ?
                """,
                (
                    finalizer_identifier,
                    cycle_fingerprint,
                    item_role,
                    item_key,
                ),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if row is None:
            raise RuntimeError("release cycle item disappeared after recording")
        return _release_cycle_item(row)

    def has_release_authority(self) -> bool:
        row = self._connect().execute(
            """
            SELECT 1 FROM release_gates
            UNION ALL
            SELECT 1 FROM release_evidence_issues
            UNION ALL
            SELECT 1 FROM release_cycle_items
            LIMIT 1
            """
        ).fetchone()
        return row is not None

    def invalidate_release_gate(self, finalizer_identifier: str) -> bool:
        cur = self._connect().execute(
            "DELETE FROM release_gates WHERE finalizer_identifier = ?",
            (finalizer_identifier,),
        )
        return cur.rowcount > 0

    def expire_stale(self, now: datetime | None = None) -> int:
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            count = self._expire_stale_locked(now)
            conn.execute("COMMIT")
            return count
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def reclaim_dead_owner_leases(
        self,
        now: datetime | None = None,
        *,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> list[RunRecord]:
        """Free unexpired leases whose owner process is gone.

        A crashed process's last heartbeat can push `lease_expires_at` up to
        a full TTL into the future; without this pass a restart cannot
        re-dispatch the interrupted ticket for minutes. Leases owned by this
        registry instance (same boot id) or by a live pid are left alone —
        the safe direction for pid reuse is to wait out the TTL.
        Rows first enter `reclaiming`, which remains lease-blocking while the
        caller performs OS cleanup outside this transaction. Existing
        `reclaiming` rows are returned again so a crash between claim, kill,
        and finalize is retry-safe.
        """
        now = _utc(now)
        alive = pid_alive or _pid_alive
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE status IN ('reclaiming', 'active')
                ORDER BY started_at, run_id
                """
            ).fetchall()
            reclaimed: list[RunRecord] = []
            for row in rows:
                if row["status"] == "reclaiming":
                    reclaimed.append(_record(row))
                    continue
                if row["owner_boot_id"] == self._boot_id:
                    continue
                pid = row["owner_pid"]
                lease_expires_at = _parse(row["lease_expires_at"])
                lease_expired = (
                    lease_expires_at is not None and lease_expires_at <= now
                )
                if pid is not None and alive(int(pid)) and not lease_expired:
                    continue
                conn.execute(
                    """
                    UPDATE runs
                    SET status = 'reclaiming', updated_at = ?, completed_at = NULL
                    WHERE run_id = ?
                    """,
                    (_iso(now), row["run_id"]),
                )
                claimed = conn.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (row["run_id"],)
                ).fetchone()
                if claimed is not None:
                    reclaimed.append(_record(claimed))
            conn.execute("COMMIT")
            return reclaimed
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def finalize_reclaimed_lease(
        self, run_id: str, now: datetime | None = None
    ) -> bool:
        """Release a reclaim fence after external process cleanup completes."""
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                """
                UPDATE runs
                SET status = 'orphaned', updated_at = ?, completed_at = ?,
                    lease_expires_at = NULL, backend_agent_pid = NULL,
                    backend_process_identity = NULL, failure_class = 'orphaned'
                WHERE run_id = ? AND status = 'reclaiming'
                """,
                (_iso(now), _iso(now), run_id),
            )
            if cur.rowcount:
                self._append_attempt_event_best_effort_locked(
                    run_id=run_id,
                    event_type="run_completed",
                    payload={"status": "orphaned", "failure_class": "orphaned"},
                    now=now,
                    prune_terminal=True,
                )
            conn.execute("COMMIT")
            return cur.rowcount > 0
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_run(self, run_id: str) -> RunRecord:
        row = self._connect().execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _record(row)

    def recent_runs(
        self,
        issue_id: str | None = None,
        limit: int = 50,
        *,
        query: str | None = None,
        status: str | None = None,
        agent: str | None = None,
    ) -> list[RunRecord]:
        """Return newest rows with optional, backwards-compatible filters."""
        limit = clamp_run_history_limit(limit)
        clauses: list[str] = []
        params: list[Any] = []
        if issue_id:
            clauses.append("(issue_id = ? OR identifier = ?)")
            params.extend((issue_id, issue_id))
        if query:
            escaped = _like_pattern(query)
            clauses.append(
                "(issue_id LIKE ? ESCAPE '\\' OR identifier LIKE ? ESCAPE '\\' "
                "OR title LIKE ? ESCAPE '\\' OR agent_kind LIKE ? ESCAPE '\\' "
                "OR status LIKE ? ESCAPE '\\')"
            )
            params.extend((escaped, escaped, escaped, escaped, escaped))
        if status:
            clauses.append("status = ?")
            params.append(status)
        if agent:
            clauses.append("agent_kind = ?")
            params.append(agent)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._connect().execute(
            f"SELECT * FROM runs {where} ORDER BY rowid DESC LIMIT ?",
            params,
        ).fetchall()
        return [_record(row) for row in rows]

    def run_events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            """
            SELECT event_id, event_type, created_at, payload_json
            FROM attempt_events WHERE run_id = ? ORDER BY event_id
            """,
            (run_id,),
        ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "event_type": str(row["event_type"]),
                "created_at": str(row["created_at"]),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in rows
        ]

    def run_detail(self, run_id: str) -> dict[str, Any]:
        """Read summary and events from one SQLite snapshot."""
        conn = self._connect()
        conn.execute("BEGIN")
        try:
            record = self.get_run(run_id)
            events = self.run_events(run_id)
            conn.execute("COMMIT")
            return {"run": _run_summary(record), "events": events}
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def diagnostic_json(self, run_id: str) -> dict[str, Any]:
        detail = self.run_detail(run_id)
        return {"schema_version": 1, **detail}

    def get_issue_flags(self, issue_id: str) -> IssueFlags | None:
        row = self._connect().execute(
            "SELECT * FROM issue_flags WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
        return _issue_flags(row) if row is not None else None

    def list_issue_flags(self) -> list[IssueFlags]:
        rows = self._connect().execute(
            "SELECT * FROM issue_flags ORDER BY issue_id"
        ).fetchall()
        return [_issue_flags(row) for row in rows]

    def set_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: int | None | object = _UNSET,
        budget_exhausted: bool | object = _UNSET,
        paused: bool | object = _UNSET,
        pause_reason: str | None | object = _UNSET,
        now: datetime | None = None,
    ) -> None:
        existing = self.get_issue_flags(issue_id)
        next_retry_attempt = (
            existing.retry_attempt if existing is not None else None
        )
        next_budget_exhausted = (
            existing.budget_exhausted if existing is not None else False
        )
        next_paused = existing.paused if existing is not None else False
        next_pause_reason = existing.pause_reason if existing is not None else None
        if retry_attempt is not _UNSET:
            next_retry_attempt = cast("int | None", retry_attempt)
        if budget_exhausted is not _UNSET:
            next_budget_exhausted = bool(budget_exhausted)
        if paused is not _UNSET:
            next_paused = bool(paused)
        if pause_reason is not _UNSET:
            next_pause_reason = cast("str | None", pause_reason)
        elif not next_paused:
            next_pause_reason = None
        self._write_issue_flags(
            issue_id,
            retry_attempt=next_retry_attempt,
            budget_exhausted=next_budget_exhausted,
            paused=next_paused,
            pause_reason=next_pause_reason,
            now=now,
        )

    def clear_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: bool = False,
        budget_exhausted: bool = False,
        paused: bool = False,
        now: datetime | None = None,
    ) -> None:
        existing = self.get_issue_flags(issue_id)
        if existing is None:
            return
        self._write_issue_flags(
            issue_id,
            retry_attempt=None if retry_attempt else existing.retry_attempt,
            budget_exhausted=False if budget_exhausted else existing.budget_exhausted,
            paused=False if paused else existing.paused,
            pause_reason=None if paused else existing.pause_reason,
            now=now,
        )

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self._path, timeout=SQLITE_BUSY_TIMEOUT_S, isolation_level=None
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _ensure_schema(self) -> None:
        """Bring the database up to the latest schema version.

        The DDL itself lives in `migrations.py`; this stays a one-liner so
        there is exactly one place that decides what the schema looks like.
        """
        self._applied_migrations = apply_migrations(self._connect(), self._path)

    def _write_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: int | None,
        budget_exhausted: bool,
        paused: bool,
        pause_reason: str | None,
        now: datetime | None,
    ) -> None:
        conn = self._connect()
        if not paused:
            pause_reason = None
        if retry_attempt is None and not budget_exhausted and not paused:
            conn.execute("DELETE FROM issue_flags WHERE issue_id = ?", (issue_id,))
            return
        updated_at = _iso(_utc(now))
        conn.execute(
            """
            INSERT INTO issue_flags (
                issue_id, retry_attempt, budget_exhausted, paused,
                pause_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET
                retry_attempt = excluded.retry_attempt,
                budget_exhausted = excluded.budget_exhausted,
                paused = excluded.paused,
                pause_reason = excluded.pause_reason,
                updated_at = excluded.updated_at
            """,
            (
                issue_id,
                retry_attempt,
                1 if budget_exhausted else 0,
                1 if paused else 0,
                pause_reason,
                updated_at,
            ),
        )

    def _run_is_live_locked(self, run_id: str, now: datetime) -> bool:
        row = self._connect().execute(
            "SELECT status, lease_expires_at FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return False
        return str(row["status"]) == "reclaiming" or (
            str(row["status"]) == "active"
            and row["lease_expires_at"] is not None
            and str(row["lease_expires_at"]) > _iso(now)
        )

    def _active_issue_locked(
        self, issue_id: str, now: datetime
    ) -> sqlite3.Row | None:
        return self._connect().execute(
            """
            SELECT * FROM runs
            WHERE issue_id = ?
              AND (
                  status = 'reclaiming'
                  OR (status = 'active' AND lease_expires_at > ?)
              )
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (issue_id, _iso(now)),
        ).fetchone()

    def _active_identifier_locked(
        self, identifier: str, now: datetime
    ) -> sqlite3.Row | None:
        return self._connect().execute(
            """
            SELECT * FROM runs
            WHERE identifier = ?
              AND (
                  status = 'reclaiming'
                  OR (status = 'active' AND lease_expires_at > ?)
              )
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (identifier, _iso(now)),
        ).fetchone()

    def _expire_stale_locked(self, now: datetime) -> int:
        conn = self._connect()
        expired = conn.execute(
            """
            SELECT run_id FROM runs
            WHERE status = 'active'
              AND backend_agent_pid IS NULL
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            ORDER BY rowid
            """,
            (_iso(now),),
        ).fetchall()
        cur = conn.execute(
            """
            UPDATE runs
            SET status = 'expired', updated_at = ?, completed_at = ?,
                lease_expires_at = NULL, failure_class = 'lease_expired'
            WHERE status = 'active'
              AND backend_agent_pid IS NULL
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (_iso(now), _iso(now), _iso(now)),
        )
        for index, row in enumerate(expired):
            self._append_attempt_event_best_effort_locked(
                run_id=str(row["run_id"]),
                event_type="run_completed",
                payload={
                    "status": "expired",
                    "failure_class": "lease_expired",
                },
                now=now,
                prune_terminal=index == len(expired) - 1,
            )
        return cur.rowcount


def _validate_checkpoint_fields(
    *, resume_session_id: str, state: str, turn: int
) -> None:
    if not isinstance(resume_session_id, str) or not resume_session_id.strip():
        raise ValueError("resume_session_id must be a non-empty string")
    if (
        len(resume_session_id) > MAX_RESUME_SESSION_ID_CHARS
        or len(resume_session_id.encode("utf-8")) > MAX_RESUME_SESSION_ID_BYTES
    ):
        raise ValueError("resume_session_id exceeds the private checkpoint bound")
    if not all(char.isprintable() for char in resume_session_id):
        raise ValueError("resume_session_id contains control characters")
    if not isinstance(state, str) or not state.strip():
        raise ValueError("checkpoint state must be a non-empty string")
    if len(state.encode("utf-8")) > MAX_CHECKPOINT_STATE_BYTES:
        raise ValueError("checkpoint state exceeds the checkpoint bound")
    if isinstance(turn, bool) or not isinstance(turn, int):
        raise ValueError("checkpoint turn must be an integer")
    if turn < 1 or turn > MAX_DIAGNOSTIC_COUNTER:
        raise ValueError("checkpoint turn is outside the supported range")


def _optional_nonnegative(value: int | None, fallback: Any = None) -> int | None:
    if value is None and fallback is None:
        return None
    return _nonnegative(value, fallback)


def _nonnegative(value: int | None, fallback: Any = 0) -> int:
    candidate = fallback if value is None else value
    try:
        return min(max(int(candidate or 0), 0), MAX_DIAGNOSTIC_COUNTER)
    except (TypeError, ValueError):
        return 0


def _valid_sha(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip().lower()
    if 4 <= len(candidate) <= 64 and all(char in "0123456789abcdef" for char in candidate):
        return candidate
    return None


def _like_pattern(value: str) -> str:
    escaped = value.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _run_summary(record: RunRecord) -> dict[str, Any]:
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
        "started_at": _iso(record.started_at),
        "updated_at": _iso(record.updated_at),
        "completed_at": _iso(record.completed_at),
        "workspace_path": str(record.workspace_path),
        "branch_name": record.branch_name or f"symphony/{record.identifier}",
        "commit_sha": record.commit_sha,
        "continued_from_run_id": record.continued_from_run_id,
        "checkpoint": (
            {
                "state": record.checkpoint_state,
                "turn": record.checkpoint_turn,
                "checkpointed_at": _iso(record.checkpointed_at),
            }
            if record.checkpoint_state is not None
            and record.checkpoint_turn is not None
            and record.checkpointed_at is not None
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


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@overload
def _iso(value: datetime) -> str: ...


@overload
def _iso(value: None) -> None: ...


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _record(row: sqlite3.Row) -> RunRecord:
    owner_pid = row["owner_pid"]
    backend_agent_pid = row["backend_agent_pid"]
    return RunRecord(
        run_id=str(row["run_id"]),
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        title=str(row["title"]),
        state=str(row["state"]),
        status=str(row["status"]),
        workspace_path=Path(str(row["workspace_path"])),
        lease_expires_at=_parse(row["lease_expires_at"]),
        last_progress_at=_parse(row["last_progress_at"]),
        attempt=int(row["attempt"]) if row["attempt"] is not None else None,
        attempt_kind=str(row["attempt_kind"]),
        agent_kind=str(row["agent_kind"]),
        started_at=_parse(row["started_at"]),
        updated_at=_parse(row["updated_at"]),
        completed_at=_parse(row["completed_at"]),
        owner_pid=int(owner_pid) if owner_pid is not None else None,
        owner_boot_id=row["owner_boot_id"],
        backend_agent_pid=(
            int(backend_agent_pid) if backend_agent_pid is not None else None
        ),
        backend_process_identity=(
            str(row["backend_process_identity"])
            if row["backend_process_identity"]
            else None
        ),
        input_tokens=(int(row["input_tokens"]) if row["input_tokens"] is not None else None),
        cache_input_tokens=(int(row["cache_input_tokens"]) if row["cache_input_tokens"] is not None else None),
        output_tokens=(int(row["output_tokens"]) if row["output_tokens"] is not None else None),
        total_tokens=(int(row["total_tokens"]) if row["total_tokens"] is not None else None),
        failure_class=str(row["failure_class"]) if row["failure_class"] else None,
        failure_message=str(row["failure_message"]) if row["failure_message"] else None,
        branch_name=str(row["branch_name"]) if row["branch_name"] else None,
        commit_sha=str(row["commit_sha"]) if row["commit_sha"] else None,
        continued_from_run_id=(
            str(row["continued_from_run_id"])
            if row["continued_from_run_id"]
            else None
        ),
        checkpoint_state=(
            str(row["checkpoint_state"]) if row["checkpoint_state"] else None
        ),
        checkpoint_turn=(
            int(row["checkpoint_turn"])
            if row["checkpoint_turn"] is not None
            else None
        ),
        checkpointed_at=_parse(row["checkpointed_at"]),
    )


def _issue_flags(row: sqlite3.Row) -> IssueFlags:
    return IssueFlags(
        issue_id=str(row["issue_id"]),
        retry_attempt=(
            int(row["retry_attempt"]) if row["retry_attempt"] is not None else None
        ),
        budget_exhausted=bool(row["budget_exhausted"]),
        paused=bool(row["paused"]),
        pause_reason=row["pause_reason"],
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
    )


def _release_gate(row: sqlite3.Row) -> ReleaseGate:
    return ReleaseGate(
        finalizer_identifier=str(row["finalizer_identifier"]),
        verifier_issue_id=str(row["verifier_issue_id"]),
        verifier_identifier=str(row["verifier_identifier"]),
        expected_contract_sha256=str(row["expected_contract_sha256"]),
        cycle_fingerprint=str(row["cycle_fingerprint"]),
        approved_fingerprint=(
            str(row["approved_fingerprint"])
            if row["approved_fingerprint"]
            else None
        ),
        status=str(row["status"]),
        target_branch=(str(row["target_branch"]) if row["target_branch"] else None),
        approved_target_sha=(
            str(row["approved_target_sha"])
            if row["approved_target_sha"]
            else None
        ),
        verifier_run_id=(
            str(row["verifier_run_id"]) if row["verifier_run_id"] else None
        ),
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
        finalizer_run_id=(
            str(row["finalizer_run_id"]) if row["finalizer_run_id"] else None
        ),
        generation=str(row["generation"] or ""),
        finalizer_completed_at=_parse(row["finalizer_completed_at"]),
        finalizer_completion_token=(
            str(row["finalizer_completion_token"])
            if row["finalizer_completion_token"]
            else None
        ),
    )


def _release_evidence_identity(row: sqlite3.Row) -> ReleaseEvidenceIdentity:
    return ReleaseEvidenceIdentity(
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        finalizer_identifier=str(row["finalizer_identifier"]),
        role=str(row["role"]),
        cycle_generation=str(row["cycle_generation"]),
        retired=bool(row["retired"]),
        recorded_at=_parse(row["recorded_at"]) or datetime.now(timezone.utc),
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
    )


def _release_cycle_item(row: sqlite3.Row) -> ReleaseCycleItem:
    return ReleaseCycleItem(
        finalizer_identifier=str(row["finalizer_identifier"]),
        cycle_fingerprint=str(row["cycle_fingerprint"]),
        item_role=str(row["item_role"]),
        item_key=str(row["item_key"]),
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        recorded_at=_parse(row["recorded_at"]) or datetime.now(timezone.utc),
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
    )
