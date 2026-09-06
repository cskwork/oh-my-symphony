"""Auth + loopback gates for the web API (`symphony.webapi`, `symphony.server`).

Covers the optional `SYMPHONY_API_TOKEN` bearer gate on the whole `/api/`
surface — including the chat-WebSocket `?token=` handshake exception that
keeps the shipped SPA usable in token mode — the loopback-only
`/api/v1/_debug/tasks` route, and the guarantee that unhandled server
errors never echo internal exception text to clients.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

import symphony.server as server_mod
from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState
from symphony.webapi import (
    API_TOKEN_ENV,
    TRUSTED_ORIGINS_ENV,
    _request_has_valid_bearer,
    _request_has_valid_ws_query_token,
    _request_is_loopback,
)

# The chat WS handshake builds its `hello` frame from the workflow config,
# so even auth-only tests need a loadable WORKFLOW.md behind the stub.
AUTH_WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Doing]
  terminal_states: [Done, Archive]

agent:
  kind: claude
---
body
"""
SERVICE_CAPABILITY = "a" * 43


class _StubOrchestrator:
    """Just enough orchestrator surface for the routes these tests hit."""

    def __init__(
        self,
        workflow_state: WorkflowState,
        *,
        service_instance_id: str | None = None,
    ) -> None:
        self._workflow_state = workflow_state
        self._service_instance_id = service_instance_id

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    @property
    def service_instance_id(self) -> str | None:
        return self._service_instance_id

    def snapshot(self) -> dict[str, Any]:
        return {"lanes": [], "running": [], "version": "test"}

    def request_refresh(self) -> bool:
        return False

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service_instance_id": self._service_instance_id,
        }


async def _start_client(
    tmp_path: Path, *, service_instance_id: str | None
) -> TestClient:
    # build_app types its parameter as Orchestrator; at runtime it only
    # uses the method protocol we mirror in `_StubOrchestrator`.
    (tmp_path / "WORKFLOW.md").write_text(AUTH_WORKFLOW_TEXT, encoding="utf-8")
    (tmp_path / "kanban").mkdir()
    state = WorkflowState(tmp_path / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(
        cast(
            Orchestrator,
            _StubOrchestrator(state, service_instance_id=service_instance_id),
        )
    )
    server = TestServer(app)
    cli = TestClient(server)
    await cli.start_server()
    return cli


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[TestClient]:
    cli = await _start_client(tmp_path, service_instance_id=SERVICE_CAPABILITY)
    try:
        yield cli
    finally:
        await cli.close()


# ---------------------------------------------------------------------------
# SYMPHONY_API_TOKEN bearer gate
# ---------------------------------------------------------------------------


async def test_token_unset_allows_requests_without_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Backward compat: no env var, no Authorization header — loopback
    # default stays frictionless.
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    resp = await client.get("/api/v1/state")
    assert resp.status == 200
    assert (await resp.json())["version"] == "test"


async def test_token_blank_behaves_as_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "   ")
    resp = await client.get("/api/v1/state")
    assert resp.status == 200


async def test_token_set_gates_get_requests(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")

    no_header = await client.get("/api/v1/state")
    assert no_header.status == 401
    assert (await no_header.json())["error"]["code"] == "unauthorized"

    wrong = await client.get(
        "/api/v1/state", headers={"Authorization": "Bearer wrong-token"}
    )
    assert wrong.status == 401
    assert (await wrong.json())["error"]["code"] == "unauthorized"

    right = await client.get(
        "/api/v1/state", headers={"Authorization": "Bearer sekrit-token"}
    )
    assert right.status == 200


async def test_token_set_gates_mutation_routes_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")

    blocked = await client.post("/api/v1/refresh", json={})
    assert blocked.status == 401

    allowed = await client.post(
        "/api/v1/refresh", json={}, headers={"Authorization": "Bearer sekrit-token"}
    )
    assert allowed.status == 202


async def test_tokenized_health_accepts_only_exact_service_instance_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    header = "X-Symphony-Service-Instance"

    missing = await client.get("/api/v1/health")
    assert missing.status == 401

    wrong = await client.get("/api/v1/health", headers={header: "b" * 43})
    assert wrong.status == 401

    health = await client.get(
        "/api/v1/health", headers={header: SERVICE_CAPABILITY}
    )
    assert health.status == 200
    assert (await health.json())["service_instance_id"] == SERVICE_CAPABILITY

    other_route = await client.get(
        "/api/v1/state", headers={header: SERVICE_CAPABILITY}
    )
    assert other_route.status == 401


async def test_tokenized_health_rejects_short_foreground_instance_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    short_id_client = await _start_client(
        tmp_path, service_instance_id="instance-a"
    )
    try:
        response = await short_id_client.get(
            "/api/v1/health",
            headers={"X-Symphony-Service-Instance": "instance-a"},
        )
    finally:
        await short_id_client.close()

    assert response.status == 401


async def test_token_not_required_outside_api_paths(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Static assets / landing page must stay reachable without the token.
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    resp = await client.get("/")
    assert resp.status in {200, 503}  # SPA present or assets-missing hint
    assert resp.status != 401


def test_request_has_valid_bearer_requires_exact_match() -> None:
    def req(header: str) -> Any:
        return SimpleNamespace(headers={"Authorization": header})

    token = "tok-123"
    assert _request_has_valid_bearer(req("Bearer tok-123"), token)
    # Scheme is case-insensitive RFC 7235; token must match exactly.
    assert _request_has_valid_bearer(req("bearer tok-123"), token)
    assert not _request_has_valid_bearer(req("Bearer tok-12"), token)
    assert not _request_has_valid_bearer(req("Bearer tok-123 extra"), token)
    assert not _request_has_valid_bearer(req("Basic tok-123"), token)
    assert not _request_has_valid_bearer(req(""), token)
    # Non-ASCII input must compare (and fail) instead of raising.
    assert not _request_has_valid_bearer(req("Bearer passwörd"), token)


# ---------------------------------------------------------------------------
# SYMPHONY_API_TOKEN × chat WebSocket handshake
# ---------------------------------------------------------------------------


async def test_token_ws_handshake_accepts_query_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Browsers cannot set headers on a WS upgrade, so the chat socket is
    the one route where `?token=` authenticates. A valid token completes
    the handshake and streams the `hello` frame."""
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    ws = await client.ws_connect("/api/v1/chat/ws?token=sekrit-token")
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
        assert hello["type"] == "hello"
    finally:
        await ws.close()


async def test_token_ws_handshake_accepts_bearer_header_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    ws = await client.ws_connect(
        "/api/v1/chat/ws", headers={"Authorization": "Bearer sekrit-token"}
    )
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
        assert hello["type"] == "hello"
    finally:
        await ws.close()


async def test_token_ws_handshake_rejects_missing_or_wrong_query_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    with pytest.raises(aiohttp.WSServerHandshakeError) as missing:
        await client.ws_connect("/api/v1/chat/ws")
    assert missing.value.status == 401

    with pytest.raises(aiohttp.WSServerHandshakeError) as wrong:
        await client.ws_connect("/api/v1/chat/ws?token=not-the-token")
    assert wrong.value.status == 401


async def test_token_query_param_rejected_on_plain_routes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The query-param exception is WS-only: a `?token=` on a normal GET
    must stay a 401 (query strings are the URL part most likely to end
    up in access logs)."""
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    resp = await client.get("/api/v1/state", params={"token": "sekrit-token"})
    assert resp.status == 401
    assert (await resp.json())["error"]["code"] == "unauthorized"


def test_request_has_valid_ws_query_token_requires_exact_match() -> None:
    def req(query: dict[str, str]) -> Any:
        return SimpleNamespace(query=query)

    assert _request_has_valid_ws_query_token(req({"token": "tok-123"}), "tok-123")
    assert not _request_has_valid_ws_query_token(req({"token": "tok-12"}), "tok-123")
    assert not _request_has_valid_ws_query_token(req({}), "tok-123")
    assert not _request_has_valid_ws_query_token(req({"token": ""}), "tok-123")
    # Non-ASCII input must compare (and fail) instead of raising.
    assert not _request_has_valid_ws_query_token(req({"token": "passwörd"}), "tok-123")


# ---------------------------------------------------------------------------
# JSON content type on every mutation (CSRF: no simple-form submissions)
# ---------------------------------------------------------------------------


async def test_bodyless_browser_mutations_require_json_content_type(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    # An HTML form can submit an empty POST without a CORS preflight; any
    # browser provenance header must trigger the 415 even with no body.
    for headers in (
        {"Origin": "http://evil.example"},
        {"Referer": "http://evil.example/page"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Mode": "no-cors"},
    ):
        resp = await client.post("/api/v1/refresh", headers=headers)
        assert resp.status == 415, headers
        assert (await resp.json())["error"]["code"] == "unsupported_media_type"
    resp = await client.delete("/api/v1/issues/X-1", headers={"Origin": "http://evil.example"})
    assert resp.status == 415
    resp = await client.post(
        "/api/v1/refresh", json={}, headers={"Origin": "http://127.0.0.1"}
    )
    assert resp.status == 202


async def test_bodyless_cli_mutations_still_work_without_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`curl -X POST .../refresh` from a shell has no Origin / Sec-Fetch
    headers, cannot be a CSRF vector, and must keep working (0.22 contract)."""
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    resp = await client.post("/api/v1/refresh", skip_auto_headers=("Content-Type",))
    assert resp.status == 202
    # A body that is not JSON is still rejected regardless of provenance.
    resp = await client.post(
        "/api/v1/refresh", data="x=1", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert resp.status == 415


# ---------------------------------------------------------------------------
# /api/v1/_debug/tasks loopback gate
# ---------------------------------------------------------------------------


async def test_debug_tasks_allowed_from_loopback_peer(
    client: TestClient,
) -> None:
    # TestClient connects over 127.0.0.1 — a real loopback peer.
    resp = await client.get("/api/v1/_debug/tasks")
    assert resp.status == 200
    payload = await resp.json()
    assert isinstance(payload["tasks"], list)


async def test_debug_tasks_rejects_non_loopback_peer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a request whose remote peer is off-machine (e.g. served
    # behind a LAN bind) by forcing the shared loopback predicate off.
    monkeypatch.setattr(server_mod, "_request_is_loopback", lambda _request: False)
    resp = await client.get("/api/v1/_debug/tasks")
    assert resp.status == 403
    payload = await resp.json()
    assert payload["error"]["code"] == "debug_tasks_local_only"


def test_shared_loopback_predicate_rejects_non_loopback_remote() -> None:
    def req(remote: str) -> Any:
        return SimpleNamespace(remote=remote, app={}, headers={})

    assert _request_is_loopback(req("127.0.0.1"))
    assert not _request_is_loopback(req("203.0.113.9"))


def test_shared_loopback_predicate_reads_forwarded_client_behind_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def req(remote: str, **headers: str) -> Any:
        # Real requests carry a case-insensitive multidict; hyphenate here.
        return SimpleNamespace(
            remote=remote,
            app={},
            headers=CIMultiDict(
                {key.replace("_", "-"): value for key, value in headers.items()}
            ),
        )

    # No trusted origins: a direct loopback caller could forge the header,
    # so it is ignored and the TCP peer alone decides.
    monkeypatch.delenv(TRUSTED_ORIGINS_ENV, raising=False)
    assert _request_is_loopback(req("127.0.0.1", X_Forwarded_For="203.0.113.9"))

    # Reverse-proxy deployment declared: the proxy is the loopback peer and
    # the real client is whatever it forwarded.
    monkeypatch.setenv(TRUSTED_ORIGINS_ENV, "https://symphony.example.com")
    assert _request_is_loopback(req("127.0.0.1"))
    assert _request_is_loopback(req("127.0.0.1", X_Forwarded_For="127.0.0.1"))
    assert _request_is_loopback(req("127.0.0.1", X_Forwarded_For="::1, 10.0.0.2"))
    assert _request_is_loopback(req("127.0.0.1", Forwarded="for=127.0.0.1;proto=https"))
    assert _request_is_loopback(req("127.0.0.1", Forwarded='for="[::1]:4711"'))
    assert not _request_is_loopback(req("127.0.0.1", X_Forwarded_For="203.0.113.9"))
    assert not _request_is_loopback(req("127.0.0.1", X_Forwarded_For="203.0.113.9, 127.0.0.1"))
    assert not _request_is_loopback(req("127.0.0.1", Forwarded="for=203.0.113.9"))
    assert not _request_is_loopback(req("127.0.0.1", X_Forwarded_For="not-an-ip"))
    assert not _request_is_loopback(req("127.0.0.1", Forwarded="proto=https"))
    # A non-loopback peer never gets to vouch for itself via headers.
    assert not _request_is_loopback(req("203.0.113.9", X_Forwarded_For="127.0.0.1"))


async def test_debug_tasks_rejects_forwarded_non_loopback_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = {"X-Forwarded-For": "203.0.113.9"}
    monkeypatch.delenv(TRUSTED_ORIGINS_ENV, raising=False)
    assert (await client.get("/api/v1/_debug/tasks", headers=headers)).status == 200

    monkeypatch.setenv(TRUSTED_ORIGINS_ENV, "https://symphony.example.com")
    resp = await client.get("/api/v1/_debug/tasks", headers=headers)
    assert resp.status == 403
    assert (await resp.json())["error"]["code"] == "debug_tasks_local_only"
    assert (await client.get("/api/v1/_debug/tasks")).status == 200


# ---------------------------------------------------------------------------
# 500 handler must not echo exception text
# ---------------------------------------------------------------------------


async def test_unhandled_error_returns_generic_message() -> None:
    from symphony.webapi import _wrap

    async def boom(_request: web.Request) -> web.Response:
        raise RuntimeError("SECRET internal detail: C:\\operator\\board.sqlite")

    app = web.Application()
    app.router.add_get("/api/v1/boom", _wrap(boom))
    server = TestServer(app)
    cli = TestClient(server)
    await cli.start_server()
    try:
        resp = await cli.get("/api/v1/boom")
        assert resp.status == 500
        payload = await resp.json()
        assert payload["error"]["code"] == "internal_error"
        assert payload["error"]["message"] == "internal server error"
        assert "SECRET" not in json.dumps(payload)
    finally:
        await cli.close()
