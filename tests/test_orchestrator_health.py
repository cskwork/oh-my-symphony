"""R1/R3/A1 — tick-loop supervision, lease hardening, health surface."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

import symphony.orchestrator.core as core_mod
from symphony.aidt_worktree.runtime import AidtWorktreeHealth
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator
from symphony.orchestrator.constants import TICK_LOOP_MAX_RESTARTS
from symphony.orchestrator.entries import RunningEntry
from symphony.orchestrator.run_registry import RunRegistry
from symphony.server import build_app
from symphony.workflow import WorkflowState


def _orch() -> Orchestrator:
    return Orchestrator(WorkflowState(Path("/tmp/no.md")))


def _issue(identifier: str = "MT-1") -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="",
        priority=2,
        state="In Progress",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# ----------------------------------------------------------------------
# R1 — tick-loop supervision
# ----------------------------------------------------------------------


async def test_tick_loop_survives_on_tick_exception(monkeypatch) -> None:
    orch = _orch()
    calls = {"n": 0}

    async def _boom_then_ok() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(orch, "_on_tick", _boom_then_ok)
    monkeypatch.setattr(
        orch._workflow_state, "current", lambda: SimpleNamespace(poll_interval_ms=10)
    )
    monkeypatch.setattr(core_mod, "TICK_FAILURE_BACKOFF_MAX_S", 0.01)

    orch._spawn_tick_loop()
    try:
        for _ in range(400):
            orch._tick_event.set()
            await asyncio.sleep(0.005)
            if calls["n"] >= 3:
                break
        assert calls["n"] >= 3, "loop must keep ticking after a failed tick"
        assert orch._tick_task is not None and not orch._tick_task.done()
        health = orch.health()
        assert health["tick"]["error_count"] == 1
        assert health["tick"]["consecutive_failures"] == 0
        assert health["tick"]["last_completed_at"] is not None
        assert health["status"] == "ok"
    finally:
        orch._stopping = True
        assert orch._tick_task is not None
        orch._tick_task.cancel()
        try:
            await orch._tick_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_tick_loop_done_callback_restarts_bounded(monkeypatch) -> None:
    orch = _orch()

    class _Fatal(BaseException):
        """Escapes the per-tick Exception guard on purpose."""

    starts = {"n": 0}

    async def _fatal() -> None:
        starts["n"] += 1
        raise _Fatal("kill the loop")

    monkeypatch.setattr(orch, "_on_tick", _fatal)
    orch._spawn_tick_loop()

    for _ in range(400):
        await asyncio.sleep(0.005)
        assert orch._tick_task is not None
        if orch._tick_loop_restarts >= TICK_LOOP_MAX_RESTARTS and orch._tick_task.done():
            break
    assert orch._tick_loop_restarts == TICK_LOOP_MAX_RESTARTS
    assert starts["n"] == 1 + TICK_LOOP_MAX_RESTARTS
    health = orch.health()
    assert health["tick"]["alive"] is False
    assert health["status"] == "degraded"
    assert "tick_loop_dead" in health["degraded_reasons"]


def test_health_degraded_after_consecutive_tick_failures() -> None:
    orch = _orch()
    assert orch.health()["status"] == "starting"
    orch._consecutive_tick_failures = 3
    health = orch.health()
    assert health["status"] == "degraded"
    assert "tick_failures" in health["degraded_reasons"]


def test_health_reports_starting_before_first_tick() -> None:
    orch = _orch()

    health = orch.health()

    assert health["status"] == "starting"
    assert health["tick"]["last_completed_at"] is None
    assert health["workflow_path"] == "/tmp/no.md"


def test_snapshot_includes_health_summary() -> None:
    orch = _orch()
    snap = orch.snapshot()
    assert snap["health"]["status"] == "starting"
    assert snap["health"]["degraded_reasons"] == []


# ----------------------------------------------------------------------
# R3 — lease hardening
# ----------------------------------------------------------------------


def test_registry_error_degrades_instead_of_raising() -> None:
    orch = _orch()

    class _Broken:
        def has_active_lease(self, issue_id: str) -> bool:
            raise sqlite3.OperationalError("database is locked")

    orch._run_registry = _Broken()  # type: ignore[assignment]
    assert orch._has_active_run_lease("id-MT-1") is False
    health = orch.health()
    assert health["run_registry"]["error_count"] == 1
    assert "run_registry_error" in health["degraded_reasons"]


async def test_heartbeat_lease_lost_reacquires(tmp_path: Path) -> None:
    orch = _orch()
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(minutes=5))
    orch._run_registry = registry
    issue = _issue()
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,
        workspace_path=tmp_path / "ws",
        agent_kind="codex",
        run_id="stale-run-id",
    )

    assert orch._heartbeat_run_lease(issue.id, entry) is True
    assert entry.run_id and entry.run_id != "stale-run-id"
    assert registry.has_active_lease(issue.id) is True
    assert entry.lease_lost is False


async def test_heartbeat_lease_conflict_flags_entry(tmp_path: Path) -> None:
    orch = _orch()
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(minutes=5))
    orch._run_registry = registry
    issue = _issue()
    foreign = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert foreign
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,
        workspace_path=tmp_path / "ws",
        agent_kind="codex",
        run_id="stale-run-id",
    )

    assert orch._heartbeat_run_lease(issue.id, entry) is False
    assert entry.lease_lost is True


async def test_lease_conflict_cancels_running_worker(tmp_path: Path) -> None:
    orch = _orch()
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(minutes=5))
    orch._run_registry = registry
    issue = _issue()
    foreign = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert foreign

    async def _parked() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(_parked())
    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=task,
        workspace_path=tmp_path / "ws",
        agent_kind="codex",
        run_id="stale-run-id",
    )
    orch._running[issue.id] = entry

    orch._heartbeat_running_leases()

    assert entry.cancelled_at is not None
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled()


# ----------------------------------------------------------------------
# A1 — health endpoint
# ----------------------------------------------------------------------


async def test_health_endpoint_returns_status() -> None:
    orch = _orch()
    app = build_app(orch)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/api/v1/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "starting"
        assert data["workflow_path"] == "/tmp/no.md"
        assert data["version"]
        assert data["tick"]["alive"] is False
        assert data["counts"] == {"running": 0, "retrying": 0}
        assert data["run_registry"]["enabled"] is False
    finally:
        await client.close()


def test_worktree_degraded_and_fatal_health_add_one_bounded_reason() -> None:
    hostile = "TOP-SECRET-/private/repository.git"

    class _Runtime:
        def __init__(self) -> None:
            self.status = "degraded"

        def health_snapshot(self) -> AidtWorktreeHealth:
            return AidtWorktreeHealth(
                True,
                self.status,  # type: ignore[arg-type]
                "a" * 64,
                3,
                2,
                1,
                1,
                "scope_changed",
                "A20-1--viewer-api",
                "2026-07-21T00:00:00Z",
            )

    orch = _orch()
    runtime = _Runtime()
    runtime.hostile = hostile  # type: ignore[attr-defined]
    orch._aidt_worktree_runtime = runtime  # type: ignore[attr-defined]

    for status in ("degraded", "fatal"):
        runtime.status = status
        health = orch.health()
        rendered = repr(health)

        assert health["aidt_worktree"]["status"] == status
        assert health["degraded_reasons"].count("aidt_worktree_failure") == 1
        assert hostile not in rendered
        assert set(health["aidt_worktree"]) == {
            "enabled",
            "status",
            "workflow_generation",
            "create_count",
            "resume_count",
            "failure_count",
            "consecutive_failures",
            "last_category",
            "last_ref",
            "last_success_at",
        }
