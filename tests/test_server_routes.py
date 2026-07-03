"""HTTP route shape for `symphony.server.build_app`.

`build_app(orchestrator)` exposes the JSON API the TUI / board-viewer /
operators consume. The existing test_board_viewer covers the viewer's
own server. This file pins the orchestrator-side route contract:

  * GET  /                         -> text hint
  * GET  /api/v1/state             -> orchestrator.snapshot()
  * GET  /api/v1/refresh           -> 405 method_not_allowed
  * POST /api/v1/refresh           -> 202 {queued, coalesced, ...}
  * GET  /api/v1/{identifier}      -> orchestrator.issue_snapshot()
  * POST /api/v1/{identifier}/pause   -> 200 {paused: true}
  * POST /api/v1/{identifier}/resume  -> 200 {paused: false}
  * GET  /api/v1/_debug/tasks      -> {tasks: [...]}

Drives the aiohttp Application through `aiohttp.test_utils` directly so
this works without the optional `pytest-aiohttp` plugin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, cast

import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony.orchestrator import Orchestrator
from symphony.server import build_app


@dataclass
class _StubOrchestrator:
    """Minimal stub honoring the contract `build_app` reads from."""

    snapshot_payload: dict[str, Any] = field(
        default_factory=lambda: {"lanes": [], "running": []}
    )
    issue_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    running_ids: dict[str, str] = field(default_factory=dict)
    retry_ids: dict[str, str] = field(default_factory=dict)
    paused_ids: set[str] = field(default_factory=set)
    refresh_calls: int = 0

    def snapshot(self) -> dict[str, Any]:
        return self.snapshot_payload

    def issue_snapshot(self, identifier: str) -> dict[str, Any] | None:
        return self.issue_payloads.get(identifier)

    def request_refresh(self) -> bool:
        coalesced = self.refresh_calls > 0
        self.refresh_calls += 1
        return coalesced

    def find_running_issue_id(self, identifier: str) -> str | None:
        return self.running_ids.get(identifier)

    def find_resumable_issue_id(self, identifier: str) -> str | None:
        return self.running_ids.get(identifier) or self.retry_ids.get(identifier)

    def is_paused(self, issue_id: str) -> bool:
        return issue_id in self.paused_ids

    def pause_worker(self, issue_id: str) -> bool:
        already = issue_id in self.paused_ids
        self.paused_ids.add(issue_id)
        return not already

    def resume_worker(self, issue_id: str) -> bool:
        if issue_id in self.paused_ids:
            self.paused_ids.discard(issue_id)
            return True
        return False


def _make_app_with_stub() -> tuple[Any, _StubOrchestrator]:
    orch = _StubOrchestrator()
    orch.snapshot_payload = {
        "lanes": [{"name": "Todo", "issues": []}],
        "running": [],
        "version": "test",
    }
    orch.issue_payloads = {
        "MT-1": {"id": "iss-1", "identifier": "MT-1", "state": "Todo"}
    }
    orch.running_ids = {"MT-1": "iss-1"}
    # build_app types its parameter as Orchestrator; at runtime it only
    # uses the method protocol we mirror in `_StubOrchestrator`.
    app = build_app(cast(Orchestrator, orch))
    return app, orch


@pytest_asyncio.fixture
async def client() -> AsyncIterator[TestClient]:
    app, _ = _make_app_with_stub()
    server = TestServer(app)
    cli = TestClient(server)
    await cli.start_server()
    try:
        yield cli
    finally:
        await cli.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def test_root_serves_web_app(client: TestClient) -> None:
    resp = await client.get("/")
    body = await resp.text()
    if resp.status == 200:
        # Packaged SPA present — index.html served.
        assert resp.headers["content-type"].startswith("text/html")
        assert "<html" in body.lower()
    else:
        # Assets missing (e.g. partial install) degrades to a clear 503.
        assert resp.status == 503
        assert "assets missing" in body


async def test_state_route_returns_orchestrator_snapshot(client: TestClient) -> None:
    resp = await client.get("/api/v1/state")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["version"] == "test"
    assert payload["lanes"] == [{"name": "Todo", "issues": []}]


async def test_refresh_get_returns_405_with_error_envelope(client: TestClient) -> None:
    resp = await client.get("/api/v1/refresh")
    assert resp.status == 405
    payload = await resp.json()
    assert payload["error"]["code"] == "method_not_allowed"


async def test_refresh_post_returns_202_with_queued_envelope(
    client: TestClient,
) -> None:
    resp = await client.post("/api/v1/refresh")
    assert resp.status == 202
    payload = await resp.json()
    assert payload["queued"] is True
    assert payload["coalesced"] is False
    assert "requested_at" in payload
    assert payload["operations"] == ["poll", "reconcile"]


async def test_refresh_post_marks_coalesced_on_second_call(
    client: TestClient,
) -> None:
    await client.post("/api/v1/refresh")
    resp = await client.post("/api/v1/refresh")
    payload = await resp.json()
    assert payload["coalesced"] is True


async def test_refresh_post_with_invalid_json_returns_400(
    client: TestClient,
) -> None:
    resp = await client.post(
        "/api/v1/refresh",
        data="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    payload = await resp.json()
    assert payload["error"]["code"] == "invalid_json"


async def test_refresh_post_with_empty_body_succeeds(client: TestClient) -> None:
    resp = await client.post("/api/v1/refresh", data="")
    assert resp.status == 202


async def test_issue_route_returns_snapshot_when_present(client: TestClient) -> None:
    resp = await client.get("/api/v1/MT-1")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["identifier"] == "MT-1"
    assert payload["state"] == "Todo"


async def test_issue_route_returns_404_when_absent(client: TestClient) -> None:
    resp = await client.get("/api/v1/UNKNOWN-99")
    assert resp.status == 404
    payload = await resp.json()
    assert payload["error"]["code"] == "issue_not_found"


async def test_pause_route_returns_404_when_not_running(client: TestClient) -> None:
    resp = await client.post("/api/v1/UNKNOWN-99/pause")
    assert resp.status == 404
    payload = await resp.json()
    assert payload["error"]["code"] == "issue_not_running"


async def test_pause_route_pauses_running_worker(client: TestClient) -> None:
    resp = await client.post("/api/v1/MT-1/pause")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["issue_identifier"] == "MT-1"
    assert payload["issue_id"] == "iss-1"
    assert payload["paused"] is True
    assert payload["changed"] is True
    assert payload["already_paused"] is False


async def test_pause_then_pause_again_reports_already_paused(
    client: TestClient,
) -> None:
    await client.post("/api/v1/MT-1/pause")
    resp = await client.post("/api/v1/MT-1/pause")
    payload = await resp.json()
    assert payload["paused"] is True
    assert payload["changed"] is False
    assert payload["already_paused"] is True


async def test_resume_route_releases_paused_worker(client: TestClient) -> None:
    await client.post("/api/v1/MT-1/pause")
    resp = await client.post("/api/v1/MT-1/resume")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["paused"] is False
    assert payload["changed"] is True


async def test_resume_route_releases_paused_retry_worker() -> None:
    app, orch = _make_app_with_stub()
    orch.running_ids = {}
    orch.retry_ids = {"MT-1": "iss-1"}
    orch.paused_ids.add("iss-1")
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post("/api/v1/MT-1/resume")
        assert resp.status == 200
        payload = await resp.json()
        assert payload["issue_identifier"] == "MT-1"
        assert payload["issue_id"] == "iss-1"
        assert payload["paused"] is False
        assert payload["changed"] is True
        assert "iss-1" not in orch.paused_ids
    finally:
        await client.close()


async def test_resume_route_returns_404_for_unknown_identifier(
    client: TestClient,
) -> None:
    resp = await client.post("/api/v1/UNKNOWN-99/resume")
    assert resp.status == 404
    payload = await resp.json()
    assert payload["error"]["code"] == "issue_not_resumable"


async def test_debug_tasks_route_returns_list(client: TestClient) -> None:
    resp = await client.get("/api/v1/_debug/tasks")
    assert resp.status == 200
    payload = await resp.json()
    assert "tasks" in payload
    assert isinstance(payload["tasks"], list)
    assert len(payload["tasks"]) >= 1
    sample = payload["tasks"][0]
    assert set(sample.keys()) >= {"name", "done", "cancelled", "coro_repr", "stack"}
