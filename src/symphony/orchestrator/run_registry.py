"""SQLite-backed run registry for crash-safe dispatch leases."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..issue import Issue


DEFAULT_LEASE_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    issue_id: str
    identifier: str
    status: str
    workspace_path: Path
    lease_expires_at: datetime | None
    last_progress_at: datetime | None


class RunRegistry:
    """Persist one active dispatch lease per issue in `.symphony/state.db`."""

    def __init__(self, path: Path, lease_ttl: timedelta = DEFAULT_LEASE_TTL) -> None:
        self._path = path
        self._lease_ttl = lease_ttl
        self._conn: sqlite3.Connection | None = None
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
                    updated_at, lease_expires_at, last_progress_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, NULL)
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

    def get_run(self, run_id: str) -> RunRecord:
        row = self._connect().execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _record(row)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
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
                completed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runs_issue_status_lease
            ON runs(issue_id, status, lease_expires_at)
            """
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
    return RunRecord(
        run_id=str(row["run_id"]),
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        status=str(row["status"]),
        workspace_path=Path(str(row["workspace_path"])),
        lease_expires_at=_parse(row["lease_expires_at"]),
        last_progress_at=_parse(row["last_progress_at"]),
    )
