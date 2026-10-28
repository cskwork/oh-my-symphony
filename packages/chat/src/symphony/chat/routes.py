"""Routes: chat sessions + WebSocket."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import WSCloseCode, WSMsgType, web

from symphony.chat import ChatManager
from symphony.errors import (
    ChatBackendUnavailableError,
    ChatBusyError,
    ChatIntentActionError,
    ChatIntentAuthorizationError,
    ChatNoSessionError,
    ChatProjectActionError,
    ChatProjectAuthorizationError,
    ChatSessionExistsError,
)
from symphony.orchestrator import Orchestrator
from symphony.projects import ProjectRegistry
from symphony.webapi.context import _Ctx
from symphony.webapi.project_routes import (
    _create_or_adopt_registered_project,
    _project_mutation_error,
)
from symphony.webapi.security import (
    _MAX_CHAT_MESSAGE,
    _json_error,
    _origin_is_trusted,
    _read_json,
    _wrap,
)
from symphony.webapi.validation import (
    _check_agent_kind,
    _check_budget,
    _check_chat_confirmation_token,
    _check_chat_session_id,
    _check_intent_action_id,
    _check_project_setup_action_id,
)
from symphony.workflow.mutate import WorkflowMutationError

CHAT_MANAGER_KEY: web.AppKey[ChatManager] = web.AppKey("symphony.chat", ChatManager)

# ---------------------------------------------------------------------------
# routes: operator chat (REST mutations + one-way WebSocket stream)
# ---------------------------------------------------------------------------


def register_chat_routes(
    app: web.Application, ctx: _Ctx, orchestrator: Orchestrator
) -> None:
    # request_refresh lets board tickets the chat agent files in edit mode
    # dispatch on the next tick instead of waiting out the poll interval.
    project_registry = ProjectRegistry()
    manager = ChatManager(
        ctx.config,
        request_refresh=orchestrator.request_refresh,
        project_creator=lambda name, path, *, expected_target, preset="default": (
            _create_or_adopt_registered_project(
                project_registry,
                name=name,
                path=path,
                expected_target=expected_target,
                # Only a non-default preset travels, so seams that predate
                # the option (tests, forks) keep their signature.
                **({"preset": preset} if preset != "default" else {}),
            )
        ),
    )
    app[CHAT_MANAGER_KEY] = manager
    websockets: set[web.WebSocketResponse] = set()

    # The singular `/chat/session` routes predate multi-session support and
    # stay as an alias for "the active session"; everything below shares one
    # implementation so the two shapes cannot drift.

    async def _start(body: dict[str, Any]) -> web.Response:
        mode = body.get("mode", "qa")
        if not isinstance(mode, str):
            raise WorkflowMutationError("mode must be a string")
        agent_kind = None
        if body.get("agent_kind") not in (None, ""):
            agent_kind = _check_agent_kind(body.get("agent_kind"), ctx.config())
        max_turns = _check_budget(body.get("max_turns"), "max_turns")
        max_tokens = _check_budget(body.get("max_tokens"), "max_tokens")
        confirmation_token = _check_chat_confirmation_token(
            body.get("confirmation_token")
        )
        try:
            snapshot = await manager.start_session(
                mode,
                agent_kind,
                max_turns=max_turns,
                max_tokens=max_tokens,
                confirmation_token=confirmation_token,
            )
        except ChatSessionExistsError as exc:
            return _json_error(409, exc.code, exc.message)
        return web.json_response(snapshot, status=201)

    async def _set_mode(body: dict[str, Any], session_id: str | None) -> web.Response:
        mode = body.get("mode")
        if not isinstance(mode, str):
            raise WorkflowMutationError("mode must be a string")
        try:
            result = await manager.set_mode(mode, session_id)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatBusyError as exc:
            return _json_error(409, exc.code, exc.message)
        return web.json_response(result)

    async def _stop(session_id: str | None, forget: bool = False) -> web.Response:
        try:
            await manager.stop_session(session_id, forget=forget)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        return web.json_response({"stopped": True, "forgotten": forget})

    async def _confirm_project_setup(
        request: web.Request, session_id: str | None, action_id: str
    ) -> web.Response:
        # This crosses from chat into global registry/Git mutation. Preserve the
        # same loopback + same-origin boundary as the Project management form.
        denied = _project_mutation_error(request)
        if denied is not None:
            return denied
        try:
            action = await manager.confirm_project_setup(
                action_id,
                session_id,
                confirmation_token=request.headers.get("X-Symphony-Chat-Confirmation"),
            )
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatProjectAuthorizationError as exc:
            return _json_error(403, exc.code, exc.message)
        except ChatProjectActionError as exc:
            status = (
                404 if exc.message.startswith("unknown project setup action") else 409
            )
            return _json_error(status, exc.code, exc.message)
        if action["status"] == "failed":
            return web.json_response(
                {
                    "error": {
                        "code": "project_setup_failed",
                        "message": str(action.get("error") or "project setup failed"),
                    },
                    "action": action,
                },
                status=409,
            )
        return web.json_response({"action": action})

    async def _confirm_intent(
        request: web.Request, session_id: str | None, action_id: str
    ) -> web.Response:
        # Approval writes the board (a ticket) and `.sdlc/work/<slug>/`, both
        # inside the project the chat already serves; the browser-held
        # confirmation capability is the authority, as for project setup.
        try:
            action = await manager.confirm_intent(
                action_id,
                session_id,
                confirmation_token=request.headers.get("X-Symphony-Chat-Confirmation"),
            )
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatIntentAuthorizationError as exc:
            return _json_error(403, exc.code, exc.message)
        except ChatIntentActionError as exc:
            status = 404 if exc.message.startswith("unknown intent action") else 409
            return _json_error(status, exc.code, exc.message)
        if action["status"] == "failed":
            return web.json_response(
                {
                    "error": {
                        "code": "intent_approval_failed",
                        "message": str(action.get("error") or "intent approval failed"),
                    },
                    "action": action,
                },
                status=409,
            )
        return web.json_response({"action": action})

    async def _send(
        request: web.Request, body: dict[str, Any], session_id: str | None
    ) -> web.Response:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise WorkflowMutationError("text is required")
        if len(text) > _MAX_CHAT_MESSAGE:
            raise WorkflowMutationError(
                f"text too long (max {_MAX_CHAT_MESSAGE} chars)"
            )
        try:
            selected = manager.project_setup_for_choice(text, session_id)
            intent = manager.intent_for_reply(text, session_id)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        # A bare numeric response is an action only when it matches a live,
        # server-issued choice. All other text remains ordinary conversation.
        if selected is not None:
            return await _confirm_project_setup(request, session_id, selected.action_id)
        # Likewise a bare ``approve`` (or ``approve <slug>``) is the intent
        # gate only when exactly one live proposal matches.
        if intent is not None:
            return await _confirm_intent(request, session_id, intent.action_id)
        try:
            snapshot = await manager.send_message(text, session_id)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatBackendUnavailableError as exc:
            return _json_error(409, exc.code, exc.message)
        except ChatBusyError as exc:
            return _json_error(409, exc.code, exc.message)
        return web.json_response(snapshot, status=202)

    async def handle_chat_session_get(_request: web.Request) -> web.Response:
        return web.json_response(manager.snapshot())

    async def handle_chat_session_post(request: web.Request) -> web.Response:
        body = await _read_json(request)
        if manager.live_count:
            return _json_error(
                409,
                "chat_session_exists",
                "a chat session is already active; stop it first or use "
                "/api/v1/chat/sessions",
            )
        return await _start(body)

    async def handle_chat_session_patch(request: web.Request) -> web.Response:
        return await _set_mode(await _read_json(request), None)

    async def handle_chat_session_delete(_request: web.Request) -> web.Response:
        return await _stop(None)

    async def handle_chat_message_post(request: web.Request) -> web.Response:
        return await _send(request, await _read_json(request), None)

    async def handle_chat_sessions_get(_request: web.Request) -> web.Response:
        return web.json_response(manager.list_sessions())

    async def handle_chat_sessions_post(request: web.Request) -> web.Response:
        return await _start(await _read_json(request))

    async def handle_chat_session_detail(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        snapshot = manager.snapshot(session_id)
        if not snapshot.get("active"):
            return _json_error(
                404, "chat_no_session", f"no live chat session {session_id!r}"
            )
        return web.json_response(snapshot)

    async def handle_chat_session_id_patch(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        return await _set_mode(await _read_json(request), session_id)

    async def handle_chat_session_id_delete(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        forget = request.query.get("forget", "").lower() in {"1", "true", "yes"}
        return await _stop(session_id, forget=forget)

    async def handle_chat_session_id_message(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        return await _send(request, await _read_json(request), session_id)

    async def handle_chat_intent_approve(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        action_id = _check_intent_action_id(request.match_info["action_id"])
        body = await _read_json(request)
        if body:
            return _json_error(400, "invalid_body", "intent approval takes no fields")
        return await _confirm_intent(request, session_id, action_id)

    async def handle_chat_project_setup_select(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        action_id = _check_project_setup_action_id(request.match_info["action_id"])
        body = await _read_json(request)
        if body:
            return _json_error(
                400, "invalid_body", "project setup selection takes no fields"
            )
        return await _confirm_project_setup(request, session_id, action_id)

    async def handle_chat_session_reattach(request: web.Request) -> web.Response:
        session_id = _check_chat_session_id(request.match_info["session_id"])
        body = await _read_json(request)
        if set(body) - {"confirmation_token"}:
            raise WorkflowMutationError("reattach accepts only confirmation_token")
        token = _check_chat_confirmation_token(body.get("confirmation_token"))
        try:
            snapshot = await manager.reattach(session_id, confirmation_token=token)
        except ChatNoSessionError as exc:
            return _json_error(404, exc.code, exc.message)
        except ChatSessionExistsError as exc:
            return _json_error(409, exc.code, exc.message)
        return web.json_response(snapshot)

    def _origin_allowed(request: web.Request) -> bool:
        # Browsers do not apply CORS to WebSocket upgrades; without this an
        # arbitrary web page could stream the operator's transcript.
        return _origin_is_trusted(request, request.headers.get("Origin") or "")

    async def _pump(
        queue: asyncio.Queue[dict[str, Any] | None], ws: web.WebSocketResponse
    ) -> None:
        while True:
            row = await queue.get()
            try:
                if row is None:
                    await ws.close(code=WSCloseCode.GOING_AWAY, message=b"shutdown")
                    return
                await ws.send_json(row)
            except (ConnectionResetError, RuntimeError):
                return

    async def handle_chat_ws(request: web.Request) -> web.StreamResponse:
        if not _origin_allowed(request):
            return _json_error(
                403, "forbidden_origin", "cross-origin websocket rejected"
            )
        raw_focus = (request.query.get("session") or "").strip()
        focus = _check_chat_session_id(raw_focus) if raw_focus else None
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        websockets.add(ws)
        queue = manager.subscribe(focus)
        pump = asyncio.create_task(_pump(queue, ws))
        try:
            await ws.send_json(
                {
                    "type": "hello",
                    "snapshot": manager.snapshot(focus),
                    "sessions": manager.list_sessions(),
                }
            )
            # Server->client stream, except for one client message: which
            # session's token deltas this socket wants. Draining also
            # detects disconnect.
            async for message in ws:
                if message.type is not WSMsgType.TEXT:
                    continue
                try:
                    frame = json.loads(message.data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(frame, dict) or frame.get("type") != "focus":
                    continue
                requested = frame.get("session_id")
                if requested in (None, ""):
                    manager.set_focus(queue, None)
                    continue
                try:
                    manager.set_focus(queue, _check_chat_session_id(requested))
                except WorkflowMutationError:
                    continue
        finally:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):
                pass
            manager.unsubscribe(queue)
            websockets.discard(ws)
        return ws

    async def _close_chat(_app: web.Application) -> None:
        # Close sockets first: an open WS otherwise holds runner.cleanup()
        # until shutdown_timeout expires.
        for ws in list(websockets):
            try:
                await ws.close(code=WSCloseCode.GOING_AWAY, message=b"shutdown")
            except Exception:
                pass
        websockets.clear()
        await manager.close()

    app.on_shutdown.append(_close_chat)

    app.router.add_get("/api/v1/chat/session", _wrap(handle_chat_session_get))
    app.router.add_post("/api/v1/chat/session", _wrap(handle_chat_session_post))
    app.router.add_patch("/api/v1/chat/session", _wrap(handle_chat_session_patch))
    app.router.add_delete("/api/v1/chat/session", _wrap(handle_chat_session_delete))
    app.router.add_post("/api/v1/chat/message", _wrap(handle_chat_message_post))
    app.router.add_get("/api/v1/chat/sessions", _wrap(handle_chat_sessions_get))
    app.router.add_post("/api/v1/chat/sessions", _wrap(handle_chat_sessions_post))
    app.router.add_get(
        "/api/v1/chat/sessions/{session_id}", _wrap(handle_chat_session_detail)
    )
    app.router.add_patch(
        "/api/v1/chat/sessions/{session_id}", _wrap(handle_chat_session_id_patch)
    )
    app.router.add_delete(
        "/api/v1/chat/sessions/{session_id}", _wrap(handle_chat_session_id_delete)
    )
    app.router.add_post(
        "/api/v1/chat/sessions/{session_id}/message",
        _wrap(handle_chat_session_id_message),
    )
    app.router.add_post(
        "/api/v1/chat/sessions/{session_id}/project-setup/{action_id}/select",
        _wrap(handle_chat_project_setup_select),
    )
    app.router.add_post(
        "/api/v1/chat/sessions/{session_id}/intent/{action_id}/approve",
        _wrap(handle_chat_intent_approve),
    )
    app.router.add_post(
        "/api/v1/chat/sessions/{session_id}/reattach",
        _wrap(handle_chat_session_reattach),
    )
    app.router.add_get("/api/v1/chat/ws", handle_chat_ws)
