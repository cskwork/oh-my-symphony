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


def test_run_registry_persists_issue_flags_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)
    registry = RunRegistry(path)

    registry.set_issue_flags(
        "id-MT-1",
        retry_attempt=3,
        budget_exhausted=True,
        paused=True,
        now=now,
    )
    registry.close()

    reopened = RunRegistry(path)
    flags = reopened.get_issue_flags("id-MT-1")

    assert flags is not None
    assert flags.issue_id == "id-MT-1"
    assert flags.retry_attempt == 3
    assert flags.budget_exhausted is True
    assert flags.paused is True
    assert flags.updated_at == now


def test_run_registry_clears_issue_flags_independently(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    registry.set_issue_flags(
        "id-MT-1", retry_attempt=2, budget_exhausted=True, paused=True
    )

    registry.clear_issue_flags("id-MT-1", retry_attempt=True, paused=True)
    flags = registry.get_issue_flags("id-MT-1")

    assert flags is not None
    assert flags.retry_attempt is None
    assert flags.budget_exhausted is True
    assert flags.paused is False

    registry.clear_issue_flags("id-MT-1", budget_exhausted=True)
    assert registry.get_issue_flags("id-MT-1") is None


def test_recent_runs_empty(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")

    assert registry.recent_runs() == []


def test_recent_runs_orders_newest_first_and_filters_issue(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    first = _issue("MT-1")
    second = _issue("MT-2")
    now = datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc)

    run_1 = registry.acquire_run(
        first,
        workspace_path=tmp_path / "ws" / first.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_1
    registry.complete_run(
        issue_id=first.id,
        run_id=run_1,
        status="normal",
        now=now + timedelta(seconds=1),
    )
    run_2 = registry.acquire_run(
        second,
        workspace_path=tmp_path / "ws" / second.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="claude",
        now=now + timedelta(seconds=2),
    )
    assert run_2
    registry.complete_run(
        issue_id=second.id,
        run_id=run_2,
        status="force_ejected_zombie",
        now=now + timedelta(seconds=3),
    )
    run_3 = registry.acquire_run(
        first,
        workspace_path=tmp_path / "ws" / first.identifier,
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
        now=now + timedelta(seconds=4),
    )
    assert run_3

    recent = registry.recent_runs()
    assert [r.run_id for r in recent] == [run_3, run_2, run_1]
    assert recent[1].status == "force_ejected_zombie"
    assert recent[1].attempt_kind == "initial"
    assert recent[1].agent_kind == "claude"
    assert recent[1].completed_at == now + timedelta(seconds=3)

    filtered = registry.recent_runs(issue_id=first.id)
    assert [r.run_id for r in filtered] == [run_3, run_1]

    identifier_filtered = registry.recent_runs(issue_id=first.identifier)
    assert [r.run_id for r in identifier_filtered] == [run_3, run_1]


def test_recent_runs_limit_clamped(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    now = datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc)
    for index in range(3):
        issue = _issue(f"MT-{index}")
        run_id = registry.acquire_run(
            issue,
            workspace_path=tmp_path / "ws" / issue.identifier,
            attempt=None,
            attempt_kind="initial",
            agent_kind="codex",
            now=now + timedelta(seconds=index),
        )
        assert run_id
        registry.complete_run(
            issue_id=issue.id,
            run_id=run_id,
            status="normal",
            now=now + timedelta(seconds=index, milliseconds=500),
        )

    assert len(registry.recent_runs(limit=0)) == 1
    assert len(registry.recent_runs(limit=-10)) == 1
    assert len(registry.recent_runs(limit=500)) == 3
