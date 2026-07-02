from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from symphony.issue import Issue
from symphony.orchestrator.run_registry import RunRegistry


def _issue(identifier: str = "MT-1") -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="",
        priority=None,
        state="In Progress",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_run_registry_active_lease_blocks_second_claim(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(seconds=60))
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()

    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )

    assert run_id
    assert (
        registry.acquire_run(
            issue,
            workspace_path=tmp_path / "ws" / issue.identifier,
            attempt=None,
            attempt_kind="initial",
            agent_kind="codex",
            now=now + timedelta(seconds=1),
        )
        is None
    )
    assert registry.has_active_lease(issue.id, now=now + timedelta(seconds=1)) is True


def test_run_registry_expires_stale_lease_before_reclaim(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(seconds=30))
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    workspace = tmp_path / "ws" / issue.identifier

    first_run = registry.acquire_run(
        issue,
        workspace_path=workspace,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )

    assert first_run
    assert registry.expire_stale(now=now + timedelta(seconds=31)) == 1
    second_run = registry.acquire_run(
        issue,
        workspace_path=workspace,
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
        now=now + timedelta(seconds=32),
    )
    assert second_run
    assert second_run != first_run
    assert registry.get_run(first_run).status == "expired"
    assert registry.get_run(second_run).status == "active"


def test_run_registry_survives_reopen_and_releases_completed_run(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    workspace = tmp_path / "ws" / issue.identifier

    registry = RunRegistry(path, lease_ttl=timedelta(seconds=60))
    run_id = registry.acquire_run(
        issue,
        workspace_path=workspace,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    registry.close()

    reopened = RunRegistry(path, lease_ttl=timedelta(seconds=60))
    assert reopened.has_active_lease(issue.id, now=now + timedelta(seconds=1)) is True

    reopened.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="normal",
        now=now + timedelta(seconds=2),
    )

    assert reopened.has_active_lease(issue.id, now=now + timedelta(seconds=3)) is False
    assert reopened.get_run(run_id).status == "normal"


def test_run_registry_reclaims_dead_owner_lease_before_ttl(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()

    crashed = RunRegistry(
        path, lease_ttl=timedelta(minutes=5), owner_pid=4242, boot_id="crashed-boot"
    )
    run_id = crashed.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    crashed.close()

    fresh = RunRegistry(path, lease_ttl=timedelta(minutes=5), boot_id="fresh-boot")
    # Owner process still alive -> honor the lease until TTL.
    assert (
        fresh.reclaim_dead_owner_leases(
            now=now + timedelta(seconds=1), pid_alive=lambda _pid: True
        )
        == []
    )
    assert fresh.has_active_lease(issue.id, now=now + timedelta(seconds=1)) is True
    # Owner process dead -> reclaim immediately, well before TTL.
    reclaimed = fresh.reclaim_dead_owner_leases(
        now=now + timedelta(seconds=2), pid_alive=lambda _pid: False
    )
    assert [r.run_id for r in reclaimed] == [run_id]
    assert fresh.has_active_lease(issue.id, now=now + timedelta(seconds=3)) is False
    assert fresh.get_run(run_id).status == "orphaned"


def test_run_registry_reclaim_skips_own_boot(tmp_path: Path) -> None:
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    issue = _issue()
    registry = RunRegistry(
        tmp_path / "state.db",
        lease_ttl=timedelta(minutes=5),
        owner_pid=999_999,
        boot_id="my-boot",
    )
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    assert (
        registry.reclaim_dead_owner_leases(
            now=now + timedelta(seconds=1), pid_alive=lambda _pid: False
        )
        == []
    )
    assert registry.has_active_lease(issue.id, now=now + timedelta(seconds=1)) is True


def test_run_registry_migrates_legacy_schema_and_reclaims_null_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE runs (
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
    future = (now + timedelta(minutes=5)).isoformat()
    conn.execute(
        """
        INSERT INTO runs VALUES (
            'legacy-run', 'id-MT-1', 'MT-1', 'MT-1 title', 'In Progress',
            NULL, 'initial', 'codex', '/tmp/ws', 'active', ?, ?, ?, ?, NULL
        )
        """,
        (now.isoformat(), now.isoformat(), future, now.isoformat()),
    )
    conn.commit()
    conn.close()

    registry = RunRegistry(path, lease_ttl=timedelta(minutes=5), boot_id="fresh")
    reclaimed = registry.reclaim_dead_owner_leases(
        now=now + timedelta(seconds=1), pid_alive=lambda _pid: False
    )
    assert [r.run_id for r in reclaimed] == ["legacy-run"]
    assert registry.has_active_lease("id-MT-1", now=now + timedelta(seconds=2)) is False
