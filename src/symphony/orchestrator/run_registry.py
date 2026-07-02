"""SQLite-backed run registry for crash-safe dispatch leases."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

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
    owner_pid: int | None = None
    owner_boot_id: str | None = None


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
        owner_pid=int(owner_pid) if owner_pid is not None else None,
        owner_boot_id=row["owner_boot_id"],
    )
