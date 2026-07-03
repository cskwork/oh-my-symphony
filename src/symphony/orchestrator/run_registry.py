"""SQLite-backed run registry for crash-safe dispatch leases."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..issue import Issue


DEFAULT_LEASE_TTL = timedelta(minutes=5)

# Bound how long a locked database can stall a caller. Registry ops run
# inline on the event loop (sqlite connections are thread-affine), so this
# is the worst-case tick delay a contended WAL database can inflict.
SQLITE_BUSY_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    issue_id: str
    identifier: str
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


@dataclass(frozen=True)
class IssueFlags:
    issue_id: str
    retry_attempt: int | None
    budget_exhausted: bool
    paused: bool
    updated_at: datetime


_UNSET = object()


def registry_path_for_workflow(workflow_path: str | Path) -> Path:
    return Path(workflow_path).expanduser().resolve().parent / ".symphony" / "state.db"


def clamp_run_history_limit(limit: int) -> int:
    return max(1, min(int(limit), 200))


def _pid_alive(pid: int) -> bool:
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
        self._ensure_schema()

    @property
    def path(self) -> Path:
        return self._path

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
                    owner_pid, owner_boot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, NULL, ?, ?)
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
                ),
            )
            conn.execute("COMMIT")
            return run_id
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def heartbeat(
        self,
        *,
        issue_id: str,
        run_id: str,
        now: datetime | None = None,
        progress_at: datetime | None = None,
    ) -> bool:
        now = _utc(now)
        expires = now + self._lease_ttl
        progress = _utc(progress_at) if progress_at is not None else None
        if progress is None:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (_iso(now), _iso(expires), issue_id, run_id)
        else:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?, last_progress_at = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (_iso(now), _iso(expires), _iso(progress), issue_id, run_id)
        cur = self._connect().execute(sql, args)
        return cur.rowcount > 0

    def complete_run(
        self,
        *,
        issue_id: str,
        run_id: str,
        status: str,
        now: datetime | None = None,
    ) -> bool:
        now = _utc(now)
        cur = self._connect().execute(
            """
            UPDATE runs
            SET status = ?, updated_at = ?, completed_at = ?, lease_expires_at = NULL
            WHERE issue_id = ? AND run_id = ?
            """,
            (status, _iso(now), _iso(now), issue_id, run_id),
        )
        return cur.rowcount > 0

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
            WHERE status = 'active' AND lease_expires_at > ?
            ORDER BY started_at, run_id
            """,
            (_iso(now),),
        )
        return [_record(row) for row in rows.fetchall()]

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
        Reclaimed rows get status 'orphaned' (distinct from TTL 'expired')
        so a later recovery pass can decide about their workspaces.
        """
        now = _utc(now)
        alive = pid_alive or _pid_alive
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE status = 'active' AND lease_expires_at > ?
                ORDER BY started_at, run_id
                """,
                (_iso(now),),
            ).fetchall()
            reclaimed: list[RunRecord] = []
            for row in rows:
                if row["owner_boot_id"] == self._boot_id:
                    continue
                pid = row["owner_pid"]
                if pid is not None and alive(int(pid)):
                    continue
                conn.execute(
                    """
                    UPDATE runs
                    SET status = 'orphaned', updated_at = ?, completed_at = ?
                    WHERE run_id = ?
                    """,
                    (_iso(now), _iso(now), row["run_id"]),
                )
                reclaimed.append(_record(row))
            conn.execute("COMMIT")
            return reclaimed
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
        self, issue_id: str | None = None, limit: int = 50
    ) -> list[RunRecord]:
        """Return newest run rows, clamping limit into [1, 200]."""
        limit = clamp_run_history_limit(limit)
        if issue_id:
            rows = self._connect().execute(
                """
                SELECT * FROM runs
                WHERE issue_id = ? OR identifier = ?
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (issue_id, issue_id, limit),
            ).fetchall()
        else:
            rows = self._connect().execute(
                """
                SELECT * FROM runs
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_record(row) for row in rows]

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
        if retry_attempt is not _UNSET:
            next_retry_attempt = retry_attempt  # type: ignore[assignment]
        if budget_exhausted is not _UNSET:
            next_budget_exhausted = bool(budget_exhausted)
        if paused is not _UNSET:
            next_paused = bool(paused)
        self._write_issue_flags(
            issue_id,
            retry_attempt=next_retry_attempt,
            budget_exhausted=next_budget_exhausted,
            paused=next_paused,
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
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL,
                identifier TEXT NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt INTEGER,
                attempt_kind TEXT NOT NULL,
                agent_kind TEXT NOT NULL,
                workspace_path TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                lease_expires_at TEXT,
                last_progress_at TEXT,
                completed_at TEXT,
                owner_pid INTEGER,
                owner_boot_id TEXT
            )
            """
        )
        # Migrate pre-owner databases in place; NULL owners read as
        # "unknown, presumed dead" in reclaim_dead_owner_leases.
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "owner_pid" not in existing:
            conn.execute("ALTER TABLE runs ADD COLUMN owner_pid INTEGER")
        if "owner_boot_id" not in existing:
            conn.execute("ALTER TABLE runs ADD COLUMN owner_boot_id TEXT")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runs_issue_status_lease
            ON runs(issue_id, status, lease_expires_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS issue_flags (
                issue_id TEXT PRIMARY KEY,
                retry_attempt INTEGER,
                budget_exhausted INTEGER NOT NULL DEFAULT 0,
                paused INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )

    def _write_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: int | None,
        budget_exhausted: bool,
        paused: bool,
        now: datetime | None,
    ) -> None:
        conn = self._connect()
        if retry_attempt is None and not budget_exhausted and not paused:
            conn.execute("DELETE FROM issue_flags WHERE issue_id = ?", (issue_id,))
            return
        updated_at = _iso(_utc(now))
        conn.execute(
            """
            INSERT INTO issue_flags (
                issue_id, retry_attempt, budget_exhausted, paused, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET
                retry_attempt = excluded.retry_attempt,
                budget_exhausted = excluded.budget_exhausted,
                paused = excluded.paused,
                updated_at = excluded.updated_at
            """,
            (
                issue_id,
                retry_attempt,
                1 if budget_exhausted else 0,
                1 if paused else 0,
                updated_at,
            ),
        )

    def _active_issue_locked(
        self, issue_id: str, now: datetime
    ) -> sqlite3.Row | None:
        return self._connect().execute(
            """
            SELECT * FROM runs
            WHERE issue_id = ?
              AND status = 'active'
              AND lease_expires_at > ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (issue_id, _iso(now)),
        ).fetchone()

    def _expire_stale_locked(self, now: datetime) -> int:
        cur = self._connect().execute(
            """
            UPDATE runs
            SET status = 'expired', updated_at = ?, completed_at = ?
            WHERE status = 'active'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (_iso(now), _iso(now), _iso(now)),
        )
        return cur.rowcount


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
    return RunRecord(
        run_id=str(row["run_id"]),
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
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
    )


def _issue_flags(row: sqlite3.Row) -> IssueFlags:
    return IssueFlags(
        issue_id=str(row["issue_id"]),
        retry_attempt=(
            int(row["retry_attempt"]) if row["retry_attempt"] is not None else None
        ),
        budget_exhausted=bool(row["budget_exhausted"]),
        paused=bool(row["paused"]),
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
    )
