"""REST + WebSocket contract for the operator chat (`/api/v1/chat/*`)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony import chat as chat_module
from symphony.backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_STARTED,
    BackendInit,
    TurnResult,
)
from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState

WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Doing]
  terminal_states: [Done, Archive]

agent:
  kind: claude
---

You are working on {{ issue.identifier }}.
"""

CONFIRMATION_TOKEN = "c" * 64


class _StubOrchestrator:
    """Chat routes never touch the orchestrator; handlers here are lazy."""

    def __init__(self, workflow_state: WorkflowState) -> None:
        self._workflow_state = workflow_state

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": "2026-08-06T00:00:00Z",
            "counts": {"running": 0, "retrying": 0},
            "running": [],
            "retrying": [],
            "codex_totals": {},
            "rate_limits": None,
        }

    def find_running_issue_id(self, _identifier: str) -> str | None:
        return None

    def request_refresh(self) -> bool:
        return False


class _FakeBackend:
    def __init__(self, init: BackendInit) -> None:
        self.init = init
        self.turns: list[str] = []
        self.stopped = False
        self.gate: asyncio.Event | None = None
        self.other_frames: list[dict[str, Any]] | None = None
        self.terminal_payload: dict[str, Any] | None = None
        self.next_initialize_entered: asyncio.Event | None = None
        self.next_initialize_gate: asyncio.Event | None = None
        self.initialize_entered: asyncio.Event | None = None
        self.initialize_gate: asyncio.Event | None = None

    async def start(self) -> None:
        pass

    async def initialize(self) -> dict[str, Any]:
        if self.initialize_entered is not None:
            self.initialize_entered.set()
        if self.initialize_gate is not None:
            await self.initialize_gate.wait()
        return {}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        return "pending"

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        self.turns.append(prompt)
        if self.gate is not None:
            await self.gate.wait()
        await self._emit(EVENT_TURN_STARTED, {})
        frames = self.other_frames
        if frames is None:
            frames = [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "thinking"}]},
                }
            ]
        for frame in frames:
            await self._emit(EVENT_OTHER_MESSAGE, frame)
        await self._emit(
            EVENT_TURN_COMPLETED,
            self.terminal_payload or {"message": "the answer"},
        )
        return TurnResult(
            status=EVENT_TURN_COMPLETED, turn_id="t", last_message="the answer"
        )

    async def stop(self) -> None:
        self.stopped = True

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await self.init.on_event(
            {
                "event": event,
                "timestamp": "2026-08-06T00:00:00Z",
                "payload": payload,
                "usage": {},
                "rate_limits": None,
                "agent_pid": 123,
            }
        )

    @property
    def session_id(self) -> str | None:
        return "sess-1"

    @property
    def pid(self) -> int | None:
        return 123

    @property
    def latest_usage(self) -> dict[str, int]:
        return {}

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    def is_progress_event(self, _event: dict[str, Any]) -> bool:
        return True


@pytest.fixture()
def fake_backends(monkeypatch: pytest.MonkeyPatch) -> list[_FakeBackend]:
    built: list[_FakeBackend] = []

    def _build(init: BackendInit) -> _FakeBackend:
        backend = _FakeBackend(init)
        if built:
            previous = built[-1]
            backend.initialize_entered = previous.next_initialize_entered
            backend.initialize_gate = previous.next_initialize_gate
            previous.next_initialize_entered = None
            previous.next_initialize_gate = None
        built.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", _build)
    return built


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    (tmp_path / "kanban").mkdir()
    return tmp_path


@pytest_asyncio.fixture()
async def client(
    board_dir: Path, fake_backends: list[_FakeBackend]
) -> AsyncIterator[TestClient]:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    try:
        yield cli
    finally:
        await cli.close()


async def _receive_types_until(ws: Any, terminal: str, limit: int = 20) -> list[str]:
    types: list[str] = []
    for _ in range(limit):
        frame = await asyncio.wait_for(ws.receive_json(), timeout=5)
        types.append(frame["type"])
        if frame["type"] == terminal:
            break
    return types


async def test_chat_session_crud(client: TestClient) -> None:
    resp = await client.get("/api/v1/chat/session")
    assert resp.status == 200
    assert (await resp.json())["active"] is False

    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 201
    payload = await resp.json()
    assert payload["active"] is True
    assert payload["mode"] == "qa"
    assert payload["agent_kind"] == "claude"

    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "chat_session_exists"

    resp = await client.patch("/api/v1/chat/session", json={"mode": "edit"})
    assert resp.status == 200
    patched = await resp.json()
    assert patched["mode"] == "edit"
    assert patched["context_preserved"] is True  # claude resumes

    resp = await client.delete("/api/v1/chat/session")
    assert resp.status == 200
    resp = await client.delete("/api/v1/chat/session")
    assert resp.status == 404
    resp = await client.patch("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 404

    resp = await client.post("/api/v1/chat/session", json={"mode": "nope"})
    assert resp.status == 400


async def test_chat_message_validation_and_busy(
    client: TestClient, fake_backends: list[_FakeBackend]
) -> None:
    resp = await client.post("/api/v1/chat/message", json={"text": "hi"})
    assert resp.status == 404

    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 201
    backend = fake_backends[0]
    backend.gate = asyncio.Event()

    resp = await client.post("/api/v1/chat/message", json={"text": ""})
    assert resp.status == 400

    resp = await client.post("/api/v1/chat/message", json={"text": "slow"})
    assert resp.status == 202

    resp = await client.post("/api/v1/chat/message", json={"text": "too soon"})
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "chat_busy"

    backend.gate.set()

    # Non-JSON mutations are rejected by the API guard.
    resp = await client.post(
        "/api/v1/chat/message",
        data="text=hi",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status == 415


async def test_chat_message_without_backend_returns_conflict_without_user_row(
    client: TestClient,
) -> None:
    from symphony import webapi

    created = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    session_id = (await created.json())["session_id"]
    manager = client.server.app[webapi.CHAT_MANAGER_KEY]
    session = manager.session(session_id)
    assert session is not None
    assert session.backend is not None
    await session.backend.stop()
    session.backend = None
    before = [row.as_dict() for row in session.transcript]

    response = await client.post(
        f"/api/v1/chat/sessions/{session_id}/message",
        json={"text": "preserve this draft"},
    )

    assert response.status == 409
    assert (await response.json())["error"]["code"] == "chat_backend_unavailable"
    assert [row.as_dict() for row in session.transcript] == before
    assert session.turn_count == 0


async def test_chat_numeric_project_choice_uses_server_owned_registration(
    board_dir: Path,
    fake_backends: list[_FakeBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from symphony import webapi

    calls: list[tuple[str, Path]] = []

    def create_project(
        _registry: Any,
        *,
        name: str,
        path: Path,
        expected_target: Any | None = None,
    ) -> Any:
        assert expected_target is not None and expected_target.repo == path
        calls.append((name, path))
        if name == "Broken":
            raise RuntimeError("simulated project setup failure")
        return SimpleNamespace(
            id="todo-app",
            name=name,
            git_repo=str(path),
            workflow=str(path / "WORKFLOW.md"),
            host="127.0.0.1",
            port=10000,
        )

    monkeypatch.setattr(webapi, "_create_or_adopt_registered_project", create_project)
    monkeypatch.setattr(webapi, "ProjectRegistry", lambda: object())
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    try:
        created = await cli.post(
            "/api/v1/chat/sessions",
            json={"mode": "edit", "confirmation_token": CONFIRMATION_TOKEN},
        )
        assert created.status == 201
        session_id = (await created.json())["session_id"]
        target = board_dir.parent / "todo-app"
        fake_backends[-1].other_frames = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                    "text": (
                        "1. Create a separate Todo app.\n"
                        "<symphony-project-setup>"
                        + json.dumps(
                            {
                                "choice": 1,
                                "name": "Todo App",
                                "path": str(target),
                            }
                        )
                        + "</symphony-project-setup>"
                    ),
                        }
                    ]
                },
            }
        ]
        response = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/message", json={"text": "offer"}
        )
        assert response.status == 202
        manager = app[webapi.CHAT_MANAGER_KEY]
        session = manager.session(session_id)
        assert session is not None and session.turn_task is not None
        await session.turn_task
        snapshot = await (await cli.get(f"/api/v1/chat/sessions/{session_id}")).json()
        [action] = snapshot["project_setup_actions"]
        assert action["choice"] == 1
        assert action["operation"] == "create"
        assert "symphony-project-setup" not in " ".join(
            row["text"] for row in snapshot["transcript_tail"]
        )

        denied = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/message",
            json={"text": "1"},
            headers={"Origin": "http://evil.example"},
        )
        assert denied.status == 403
        assert calls == []

        missing_capability = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/message", json={"text": "1"}
        )
        assert missing_capability.status == 403
        assert calls == []

        selected = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/message",
            json={"text": "1"},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert selected.status == 200
        result = await selected.json()
        assert result["action"]["status"] == "succeeded"
        assert result["action"]["project"]["id"] == "todo-app"
        # Duplicate delivery stays on the server-owned action, never a second
        # backend turn or project setup.
        duplicate = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/project-setup/"
            f"{action['action_id']}/select",
            json={},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert duplicate.status == 200
        assert calls == [("Todo App", target)]

        broken_target = board_dir.parent / "broken"
        fake_backends[-1].other_frames = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<symphony-project-setup>"
                                + json.dumps(
                                    {
                                        "choice": 2,
                                        "name": "Broken",
                                        "path": str(broken_target),
                                    }
                                )
                                + "</symphony-project-setup>"
                            ),
                        }
                    ]
                },
            }
        ]
        response = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/message", json={"text": "offer broken"}
        )
        assert response.status == 202
        assert session.turn_task is not None
        await session.turn_task
        actions = (await (await cli.get(f"/api/v1/chat/sessions/{session_id}")).json())[
            "project_setup_actions"
        ]
        broken = next(action for action in actions if action["choice"] == 2)
        failed = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/project-setup/{broken['action_id']}/select",
            json={},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert failed.status == 409
        failed_body = await failed.json()
        assert failed_body["error"]["code"] == "project_setup_failed"
        assert failed_body["action"]["status"] == "failed"
        assert fake_backends[-1].turns[-2].endswith("offer")
        assert fake_backends[-1].turns[-1].endswith("offer broken")
    finally:
        await cli.close()


async def test_chat_ws_streams_turn_events(client: TestClient) -> None:
    ws = await client.ws_connect("/api/v1/chat/ws")
    hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
    assert hello["type"] == "hello"
    assert hello["snapshot"]["active"] is False

    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 201
    frame = await asyncio.wait_for(ws.receive_json(), timeout=5)
    assert frame["type"] == "session_status"

    resp = await client.post("/api/v1/chat/message", json={"text": "explain"})
    assert resp.status == 202
    types = await _receive_types_until(ws, "turn_completed")
    assert types[0] == "user_message"
    assert "turn_started" in types
    assert "agent_message" in types
    assert types[-1] == "turn_completed"
    await ws.close()


async def test_chat_ws_streams_prime_agent_snapshots_and_final_message(
    client: TestClient, fake_backends: list[_FakeBackend]
) -> None:
    resp = await client.post(
        "/api/v1/chat/sessions", json={"mode": "qa", "agent_kind": "prime-agent"}
    )
    session_id = (await resp.json())["session_id"]
    backend = fake_backends[-1]
    backend.other_frames = [
        {
            "type": "message_update",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {"type": "text", "text": "Hel"},
                ],
            },
        },
        {
            "type": "message_update",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private reasoning grows"},
                    {"type": "text", "text": "Hello"},
                ],
            },
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
            },
        },
    ]
    backend.terminal_payload = {
        "type": "agent_end",
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
            }
        ],
    }

    ws = await client.ws_connect(f"/api/v1/chat/ws?session={session_id}")
    await asyncio.wait_for(ws.receive_json(), timeout=5)  # hello
    await client.post(
        f"/api/v1/chat/sessions/{session_id}/message", json={"text": "stream"}
    )
    frames: list[dict[str, Any]] = []
    while True:
        frame = await asyncio.wait_for(ws.receive_json(), timeout=5)
        frames.append(frame)
        if frame["type"] == "turn_completed":
            break

    snapshots = [frame for frame in frames if frame["type"] == "agent_snapshot"]
    assert [frame["text"] for frame in snapshots] == ["Hel", "Hello"]
    assert all(frame["seq"] is None for frame in snapshots)
    assert [frame["text"] for frame in frames if frame["type"] == "agent_message"] == [
        "Hello"
    ]
    assert "private reasoning" not in json.dumps(frames)

    snapshot = await (await client.get(f"/api/v1/chat/sessions/{session_id}")).json()
    transcript = snapshot["transcript_tail"]
    assert "agent_snapshot" not in [row["type"] for row in transcript]
    assert [row["text"] for row in transcript if row["type"] == "agent_message"] == [
        "Hello"
    ]
    await ws.close()


async def test_chat_sessions_plural_crud_and_singular_alias(
    client: TestClient,
) -> None:
    resp = await client.get("/api/v1/chat/sessions")
    assert resp.status == 200
    listing = await resp.json()
    assert listing["sessions"] == [] and listing["resumable"] == []
    assert listing["max_sessions"] >= 1
    assert listing["default_agent_kind"] == "claude"
    assert "prime-agent" in listing["supported_agent_kinds"]

    resp = await client.post("/api/v1/chat/sessions", json={"mode": "qa"})
    assert resp.status == 201
    first = (await resp.json())["session_id"]
    resp = await client.post("/api/v1/chat/sessions", json={"mode": "edit"})
    assert resp.status == 201
    second = (await resp.json())["session_id"]

    listing = await (await client.get("/api/v1/chat/sessions")).json()
    assert [s["session_id"] for s in listing["sessions"]] == [first, second]
    assert listing["active_id"] == second

    # The singular alias still means "one chat session at a time".
    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "chat_session_exists"
    # ...and points at the active one.
    assert (await (await client.get("/api/v1/chat/session")).json())[
        "session_id"
    ] == second

    resp = await client.post(
        f"/api/v1/chat/sessions/{first}/message", json={"text": "hello"}
    )
    assert resp.status == 202
    resp = await client.patch(f"/api/v1/chat/sessions/{first}", json={"mode": "edit"})
    assert resp.status == 200
    assert (await resp.json())["mode"] == "edit"

    resp = await client.get(f"/api/v1/chat/sessions/{first}")
    assert resp.status == 200
    assert (await resp.json())["turn_count"] == 1

    resp = await client.delete(f"/api/v1/chat/sessions/{first}")
    assert resp.status == 200
    resp = await client.get(f"/api/v1/chat/sessions/{first}")
    assert resp.status == 404
    listing = await (await client.get("/api/v1/chat/sessions")).json()
    assert [e["session_id"] for e in listing["resumable"]] == [first]


async def test_chat_stop_waits_for_gated_mode_rebuild(
    client: TestClient, fake_backends: list[_FakeBackend]
) -> None:
    created = await client.post("/api/v1/chat/sessions", json={"mode": "qa"})
    session_id = (await created.json())["session_id"]
    entered = asyncio.Event()
    release = asyncio.Event()
    fake_backends[0].next_initialize_entered = entered
    fake_backends[0].next_initialize_gate = release

    changing = asyncio.create_task(
        client.patch(
            f"/api/v1/chat/sessions/{session_id}",
            json={"mode": "edit"},
        )
    )
    await entered.wait()
    stopping = asyncio.create_task(client.delete(f"/api/v1/chat/sessions/{session_id}"))
    await asyncio.sleep(0)
    try:
        assert stopping.done() is False
    finally:
        release.set()

    changed = await changing
    stopped = await stopping
    assert changed.status == 200
    assert stopped.status == 200
    listing = await (await client.get("/api/v1/chat/sessions")).json()
    assert listing["sessions"] == []
    assert [row["session_id"] for row in listing["resumable"]] == [session_id]
    assert all(backend.stopped for backend in fake_backends)


async def test_chat_session_id_is_validated(client: TestClient) -> None:
    resp = await client.get("/api/v1/chat/sessions/..%2F..%2Fetc%2Fpasswd")
    assert resp.status == 400
    resp = await client.post("/api/v1/chat/sessions/nope/reattach", json={})
    assert resp.status == 400
    resp = await client.post(
        "/api/v1/chat/sessions/20260806-000000-abcdef/reattach", json={}
    )
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "chat_no_session"


async def test_chat_reattach_restores_a_stopped_session(
    client: TestClient,
) -> None:
    resp = await client.post("/api/v1/chat/sessions", json={"mode": "qa"})
    session_id = (await resp.json())["session_id"]
    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/message", json={"text": "remember"}
    )
    assert resp.status == 202
    await client.delete(f"/api/v1/chat/sessions/{session_id}")

    resp = await client.post(f"/api/v1/chat/sessions/{session_id}/reattach", json={})
    assert resp.status == 200
    payload = await resp.json()
    assert payload["active"] is True
    assert "remember" in [m["text"] for m in payload["transcript_tail"]]

    # Forgetting drops it from the resumable list.
    await client.delete(f"/api/v1/chat/sessions/{session_id}?forget=true")
    listing = await (await client.get("/api/v1/chat/sessions")).json()
    assert listing["resumable"] == []


async def test_chat_ws_tags_frames_and_accepts_focus(client: TestClient) -> None:
    resp = await client.post("/api/v1/chat/sessions", json={"mode": "qa"})
    session_id = (await resp.json())["session_id"]
    ws = await client.ws_connect(f"/api/v1/chat/ws?session={session_id}")
    hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
    assert hello["type"] == "hello"
    assert hello["snapshot"]["session_id"] == session_id
    assert [s["session_id"] for s in hello["sessions"]["sessions"]] == [session_id]

    await ws.send_json({"type": "focus", "session_id": session_id})
    await client.post(
        f"/api/v1/chat/sessions/{session_id}/message", json={"text": "explain"}
    )
    frame = await asyncio.wait_for(ws.receive_json(), timeout=5)
    assert frame["type"] == "user_message"
    assert frame["session_id"] == session_id
    await ws.close()


async def test_chat_ws_rejects_cross_origin(client: TestClient) -> None:
    with pytest.raises(aiohttp.WSServerHandshakeError):
        await client.ws_connect(
            "/api/v1/chat/ws", headers={"Origin": "http://evil.example"}
        )


async def test_shutdown_stops_chat_backend(
    board_dir: Path, fake_backends: list[_FakeBackend]
) -> None:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    resp = await cli.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 201
    ws = await cli.ws_connect("/api/v1/chat/ws")
    await asyncio.wait_for(ws.receive_json(), timeout=5)  # hello

    await cli.close()  # fires app.on_shutdown

    assert fake_backends[0].stopped is True
