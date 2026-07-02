from __future__ import annotations

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
