"""HTTP surface of the intent gate: approve route and the ``approve`` reply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from aiohttp.test_utils import TestClient, TestServer

from symphony import chat as chat_module
from symphony import webapi
from symphony.intent import IntentAction
from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import ServiceConfig, WorkflowState

from tests.test_chat_intent import INTENT_BODY
from tests.test_webapi_chat import (  # noqa: F401  (fixtures re-exported on purpose)
    CONFIRMATION_TOKEN,
    _FakeBackend,
    _StubOrchestrator,
    board_dir,
    fake_backends,
)


def _intent_frame(slug: str = "todo-app") -> dict[str, Any]:
    marker = "<symphony-intent>" + json.dumps(
        {"slug": slug, "title": "Build a Todo app", "track": "full", "intent": INTENT_BODY}
    ) + "</symphony-intent>"
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Proposal follows.\n" + marker}]},
    }


async def test_intent_approve_route_and_reply(
    board_dir: Path, fake_backends: list[_FakeBackend], monkeypatch: Any
) -> None:
    filed: list[str] = []

    def filer(cfg: ServiceConfig, action: IntentAction, *, approved_at: str, session_id: str):
        filed.append(action.slug)
        return {"identifier": "REQ-1", "state": "Todo", "request": action.slug}

    monkeypatch.setattr(chat_module, "file_intent_request", filer)
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    try:
        created = await cli.post(
            "/api/v1/chat/sessions",
            json={"mode": "qa", "confirmation_token": CONFIRMATION_TOKEN},
        )
        assert created.status == 201
        session_id = (await created.json())["session_id"]
        fake_backends[-1].other_frames = [_intent_frame()]
        response = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/message", json={"text": "build a todo app"}
        )
        assert response.status == 202
        manager = app[webapi.CHAT_MANAGER_KEY]
        session = manager.session(session_id)
        assert session is not None and session.turn_task is not None
        await session.turn_task

        snapshot = await (await cli.get(f"/api/v1/chat/sessions/{session_id}")).json()
        [action] = snapshot["intent_actions"]
        assert action["slug"] == "todo-app" and action["status"] == "pending"
        assert "symphony-intent" not in " ".join(
            row["text"] for row in snapshot["transcript_tail"]
        )

        # Unknown id -> 404; missing capability -> 403; nothing filed.
        missing = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/intent/intent-{'0' * 32}/approve",
            json={},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert missing.status == 404
        forbidden = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/intent/{action['action_id']}/approve",
            json={},
        )
        assert forbidden.status == 403
        assert filed == []

        # A bare "approve" reply is the gate, not a backend turn.
        turns_before = len(fake_backends[-1].turns)
        approved = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/message",
            json={"text": "approve"},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert approved.status == 200
        body = await approved.json()
        assert body["action"]["status"] == "approved"
        assert body["action"]["ticket"]["identifier"] == "REQ-1"
        assert filed == ["todo-app"]
        assert len(fake_backends[-1].turns) == turns_before

        # Re-approving via the route is idempotent.
        again = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/intent/{action['action_id']}/approve",
            json={},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert again.status == 200 and filed == ["todo-app"]

        # With no live proposal, "approve" is ordinary conversation again.
        fake_backends[-1].other_frames = None
        plain = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/message",
            json={"text": "approve"},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert plain.status == 202
        assert session.turn_task is not None
        await session.turn_task
        assert fake_backends[-1].turns[-1].endswith("approve")
        assert "approved intent 'todo-app'" in fake_backends[-1].turns[-1]
    finally:
        await cli.close()


async def test_intent_approve_route_reports_failure(
    board_dir: Path, fake_backends: list[_FakeBackend], monkeypatch: Any
) -> None:
    def filer(cfg: ServiceConfig, action: IntentAction, *, approved_at: str, session_id: str):
        raise RuntimeError("no free request id")

    monkeypatch.setattr(chat_module, "file_intent_request", filer)
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
        session_id = (await created.json())["session_id"]
        fake_backends[-1].other_frames = [_intent_frame("broken")]
        await cli.post(f"/api/v1/chat/sessions/{session_id}/message", json={"text": "go"})
        session = app[webapi.CHAT_MANAGER_KEY].session(session_id)
        assert session is not None and session.turn_task is not None
        await session.turn_task
        [action] = (await (await cli.get(f"/api/v1/chat/sessions/{session_id}")).json())[
            "intent_actions"
        ]
        failed = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/intent/{action['action_id']}/approve",
            json={},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert failed.status == 409
        body = await failed.json()
        assert body["error"]["code"] == "intent_approval_failed"
        assert body["action"]["status"] == "failed"
        assert "no free request id" in body["action"]["error"]
        bad_body = await cli.post(
            f"/api/v1/chat/sessions/{session_id}/intent/{action['action_id']}/approve",
            json={"force": True},
            headers={"X-Symphony-Chat-Confirmation": CONFIRMATION_TOKEN},
        )
        assert bad_body.status == 400
    finally:
        await cli.close()
