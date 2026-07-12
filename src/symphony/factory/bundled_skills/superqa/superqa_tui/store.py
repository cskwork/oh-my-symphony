"""SQLite store: per-site variables (accounts etc.), settings, run history.

DB lives in ~/.superqa/superqa.db - never inside the skill repo, never committed.
Keys matching SECRET_PATTERN are stored flagged as secret and masked in reports.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from .scenario import superqa_home

SECRET_PATTERN = re.compile(r"(pass|pw|secret|token|pin|credential)", re.IGNORECASE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vars (
    site TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    secret INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (site, key)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario TEXT NOT NULL,
    site TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'running',
    passed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    effects INTEGER NOT NULL DEFAULT 0,
    report_path TEXT,
    summary TEXT
);
"""


class Store:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (superqa_home() / "superqa.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(runs)")}
        if "summary" not in cols:
            self._conn.execute("ALTER TABLE runs ADD COLUMN summary TEXT")

    def close(self) -> None:
        self._conn.close()

    # ---- vars -------------------------------------------------------------
    def set_var(self, site: str, key: str, value: str, secret: bool | None = None) -> None:
        if secret is None:
            secret = bool(SECRET_PATTERN.search(key))
        self._conn.execute(
            "INSERT INTO vars(site,key,value,secret,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(site,key) DO UPDATE SET value=excluded.value, "
            "secret=excluded.secret, updated_at=excluded.updated_at",
            (site, key, value, int(secret), time.time()),
        )
        self._conn.commit()

    def get_var(self, site: str, key: str) -> str | None:
        for scope in (site, "*"):
            row = self._conn.execute(
                "SELECT value FROM vars WHERE site=? AND key=?", (scope, key)
            ).fetchone()
            if row:
                return row["value"]
        return None

    def delete_var(self, site: str, key: str) -> None:
        self._conn.execute("DELETE FROM vars WHERE site=? AND key=?", (site, key))
        self._conn.commit()

    def list_vars(self, site: str | None = None) -> list[dict]:
        q = "SELECT site,key,value,secret FROM vars"
        args: tuple = ()
        if site:
            q += " WHERE site=? OR site='*'"
            args = (site,)
        rows = self._conn.execute(q + " ORDER BY site,key", args).fetchall()
        return [dict(r) for r in rows]

    def secret_values(self) -> list[str]:
        rows = self._conn.execute("SELECT value FROM vars WHERE secret=1").fetchall()
        return [r["value"] for r in rows if r["value"]]

    def substitute(self, site: str, text: str) -> str:
        """Replace {{key}} placeholders with stored values (site scope then '*')."""
        def repl(m: re.Match) -> str:
            v = self.get_var(site, m.group(1).strip())
            return v if v is not None else m.group(0)
        return re.sub(r"\{\{\s*([\w.\-가-힣]+)\s*\}\}", repl, text)

    # ---- settings ----------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    # ---- runs ---------------------------------------------------------------
    def start_run(self, scenario: str, site: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs(scenario,site,started_at) VALUES(?,?,?)",
            (scenario, site, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, status: str, passed: int, failed: int,
                   effects: int, report_path: str | None,
                   summary: str | None = None) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at=?, status=?, passed=?, failed=?, "
            "effects=?, report_path=?, summary=? WHERE id=?",
            (time.time(), status, passed, failed, effects, report_path, summary, run_id),
        )
        self._conn.commit()

    def recent_runs(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_run(self, scenario: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE scenario=? AND report_path IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (scenario,),
        ).fetchone()
        return dict(row) if row else None

    def previous_run(self, scenario: str, before_id: int) -> dict | None:
        """Latest finished run of the same scenario before the given run id."""
        row = self._conn.execute(
            "SELECT * FROM runs WHERE scenario=? AND id<? AND finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (scenario, before_id),
        ).fetchone()
        return dict(row) if row else None
