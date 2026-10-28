"""Agent Client Protocol (ACP) backend driven by an `AgentProfileConfig`.

Any CLI that speaks ACP (https://agentclientprotocol.com, protocol v1) runs
through this one class when its `agent.profiles` entry sets `format: acp`.
ACP is JSON-RPC 2.0, one message per line over stdio:

    initialize -> session/new -> session/prompt* (agent streams session/update)

One agent process and one ACP session live for the whole worker run;
continuation turns send another `session/prompt` on the same session. The
client advertises no fs/terminal capabilities, so the agent works in the
workspace with its own tools and every client method it might call is
rejected with a JSON-RPC error, except `session/request_permission`, which
is answered unattended.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from typing import Any

from .. import __version__
from .._shell import (
    process_identity,
    resolve_bash,
    safe_proc_wait,
    terminate_process_tree,
)
from .._windows_backend import spawn_backend
from ..errors import (
    ConfigValidationError,
    PortExit,
    ResponseError,
    ResponseTimeout,
    TurnCancelled,
    TurnFailed,
    TurnTimeout,
)
from ..logging import get_logger
from ..utils.git_sandbox import git_roots_env
from ..workspace import validate_agent_cwd
from . import (
    EVENT_APPROVAL_AUTO_APPROVED,
    EVENT_APPROVAL_DENIED,
    EVENT_MALFORMED,
    EVENT_NOTIFICATION,
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_TURN_CANCELLED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    EVENT_TURN_STARTED,
    MALFORMED_LINE_LIMIT,
    POST_STREAM_REAP_TIMEOUT_S,
    BackendInit,
    BaseAgentBackend,
    TurnResult,
    _is_valid_session_id,
)
from .approval_policy import dangerous_command_reason
from .per_turn import MAX_LINE_BYTES, _emit_event, _stderr_tail_blob


log = get_logger()

ACP_PROTOCOL_VERSION = 1

METHOD_INITIALIZE = "initialize"
METHOD_SESSION_NEW = "session/new"
METHOD_SESSION_LOAD = "session/load"
METHOD_SESSION_RESUME = "session/resume"
METHOD_SESSION_PROMPT = "session/prompt"
METHOD_SESSION_CANCEL = "session/cancel"
METHOD_SESSION_UPDATE = "session/update"
METHOD_REQUEST_PERMISSION = "session/request_permission"

JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_AUTH_REQUIRED = -32000

# How long a cancelled turn may take to answer `session/cancel` with
# `stopReason: cancelled` before the process group is terminated anyway.
CANCEL_GRACE_S = 2.0
# Bound on re-probing a group whose leader already exited (see `_terminate`).
EXITED_GROUP_SETTLE_S = 5.0

_ASSISTANT_MESSAGE_PREVIEW_CAP = 1000

# `OTHER_MESSAGE` payload types that reflect real model/tool activity and so
# reset the orchestrator's stall clock. usage/mode/command updates and
# unknown kinds are telemetry, not progress.
_PROGRESS_TYPES = frozenset(
    {"agent_delta", "agent_thought", "tool_call", "tool_call_update", "plan"}
)


class AcpBackend(BaseAgentBackend):
    """One long-lived ACP agent process per worker run."""

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        name = init.cfg.agent.kind
        profile = init.cfg.agent_profile(name)
        if profile is None or profile.format != "acp":
            raise ConfigValidationError(
                f"no ACP agent profile named {name!r}", value=name
            )
        self._profile = profile
        self._agent_name = profile.name
        self._cwd = init.cwd
        self._extra_env = dict(init.env)
        self._on_event = init.on_event
        self._on_process_started = init.on_process_started
        self._on_process_identified = init.on_process_identified
        self._process_launch_guard = init.process_launch_guard
        self._require_durable_process = init._require_durable_process
        self._process: asyncio.subprocess.Process | None = None
        self._process_identity: str | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._closed = False
        self._cleanup_unconfirmed = False
        self._stream_corrupt: str | None = None
        self._agent_capabilities: dict[str, Any] = {}
        self._session_id: str | None = None
        # session/load replays history as session/update before answering;
        # those frames are old turns and must not reach the current turn.
        self._replaying = False
        self._turn_text: list[str] = []
        self._latest_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

    # ------------------------------------------------------------------
    # AgentBackend lifecycle
    # ------------------------------------------------------------------

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") in _PROGRESS_TYPES

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def latest_usage(self) -> dict[str, int]:
        return dict(self._latest_usage)

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    async def start(self) -> None:
        if self._closed:
            raise ResponseError("backend is closed")
        if self._cleanup_unconfirmed:
            raise ResponseError("prior backend process cleanup is not confirmed")
        command = self._profile.command
        env = {**os.environ, **git_roots_env(self._cwd), **self._extra_env}
        self._stream_corrupt = None
        try:
            async with self._process_launch_guard() as publish_started:
                if os.name == "nt":
                    self._process, self._process_identity = await spawn_backend(
                        [resolve_bash(), "-lc", command], cwd=str(self._cwd), env=env,
                        limit=MAX_LINE_BYTES, pipe_stdin=True,
                        on_identified=self._on_process_identified,
                        on_started=self._on_process_started, publish_started=publish_started,
                        require_durable=self._require_durable_process,
                        track_process=self._track_windows_process,
                    )
                else:
                    self._process = await asyncio.create_subprocess_exec(
                        resolve_bash(),
                        "-lc",
                        command,
                        cwd=str(self._cwd),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                        limit=MAX_LINE_BYTES,
                        # Own process group so terminate/kill reaches the
                        # agent CLI behind the bash wrapper.
                        start_new_session=True,
                    )
                    self._process_identity = process_identity(self._process.pid)
                    if self._on_process_identified is not None:
                        self._on_process_identified(
                            self._process.pid, self._process_identity
                        )
                    elif self._on_process_started is not None:
                        self._on_process_started(self._process.pid)
                    await publish_started(self._process.pid)
        except FileNotFoundError as exc:
            raise PortExit("bash not available", error=str(exc)) from exc
        except BaseException as launch_exc:
            if self._process is not None:
                try:
                    cleanup = await terminate_process_tree(
                        self._process, identity=self._process_identity
                    )
                except BaseException:
                    self._cleanup_unconfirmed = True
                    raise
                if cleanup is None:
                    self._cleanup_unconfirmed = True
                    if isinstance(launch_exc, asyncio.CancelledError):
                        raise launch_exc
                    raise RuntimeError(
                        "backend process cleanup could not be confirmed"
                    ) from launch_exc
                self._process = None
                self._process_identity = None
            raise
        assert self._process is not None
        self._reader_task = asyncio.create_task(self._stdout_reader(self._process))
        self._stderr_task = asyncio.create_task(self._stderr_reader(self._process))

    def _track_windows_process(self, proc: asyncio.subprocess.Process, identity: str) -> None:
        self._process, self._process_identity = proc, identity

    async def initialize(self) -> dict[str, Any]:
        result = await self._request(
            METHOD_INITIALIZE,
            {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                # Nothing advertised: the agent edits the worktree with its
                # own tools, and any fs/terminal call is rejected.
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "symphony", "version": __version__},
            },
        )
        caps = result.get("agentCapabilities")
        self._agent_capabilities = caps if isinstance(caps, dict) else {}
        return result

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        # The prompt travels in the first `session/prompt`.
        del initial_prompt, issue_title
        result = await self._request(
            METHOD_SESSION_NEW, {"cwd": str(self._cwd), "mcpServers": []}
        )
        session_id = result.get("sessionId")
        if not _is_valid_session_id(session_id):
            raise ResponseError(
                f"{self._agent_name} returned no sessionId from session/new",
                method=METHOD_SESSION_NEW,
            )
        assert isinstance(session_id, str)
        self._session_id = session_id
        await self._emit(
            EVENT_SESSION_STARTED, {"session_id": session_id, "thread_id": session_id}
        )
        return session_id

    async def resume_session(self, session_id: str) -> bool:
        """Reattach an exact ACP session when the agent advertises it.

        Prefers `session/resume` (no history replay) over `session/load`.
        Any RPC failure returns False so the caller starts a fresh session.
        """
        if self._closed or not _is_valid_session_id(session_id):
            return False
        method = self._resume_method()
        if method is None:
            return False
        params = {"sessionId": session_id, "cwd": str(self._cwd), "mcpServers": []}
        self._replaying = method == METHOD_SESSION_LOAD
        try:
            await self._request(method, params)
        except (ResponseError, ResponseTimeout, PortExit):
            return False
        finally:
            self._replaying = False
        self._session_id = session_id
        await self._emit(
            EVENT_SESSION_STARTED, {"session_id": session_id, "thread_id": session_id}
        )
        return True

    def _resume_method(self) -> str | None:
        session_caps = self._agent_capabilities.get("sessionCapabilities")
        if isinstance(session_caps, dict) and isinstance(
            session_caps.get("resume"), dict
        ):
            return METHOD_SESSION_RESUME
        if self._agent_capabilities.get("loadSession") is True:
            return METHOD_SESSION_LOAD
        return None

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        del is_continuation  # the live session already carries the context
        if self._closed:
            raise ResponseError("backend is closed")
        if self._cleanup_unconfirmed:
            raise ResponseError("prior backend process cleanup is not confirmed")
        if self._session_id is None:
            raise ResponseError("run_turn called before session started")
        if not self._process_alive():
            await self._restart_session()
        self._turn_text = []
        await self._emit(EVENT_TURN_STARTED, {})
        params = {
            "sessionId": self._session_id,
            "prompt": [{"type": "text", "text": prompt}],
        }
        msg_id, future = await self._send_request(METHOD_SESSION_PROMPT, params)
        try:
            response = await asyncio.wait_for(
                asyncio.shield(future), timeout=self._profile.turn_timeout_ms / 1000.0
            )
        except asyncio.TimeoutError as exc:
            await self._abort_turn(future)
            await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
            raise TurnTimeout(f"{self._agent_name} turn timed out") from exc
        except asyncio.CancelledError:
            await self._abort_turn(future)
            await self._emit(EVENT_TURN_CANCELLED, {"reason": "cancelled"})
            raise
        except PortExit as exc:
            reason = f"{self._agent_name} exited mid-turn: {exc}"
            stderr = _stderr_tail_blob(self._stderr_tail)
            if stderr:
                reason += f"; stderr: {stderr}"
            await self._emit(
                EVENT_TURN_FAILED,
                {"reason": reason, "stderr_tail": list(self._stderr_tail)},
            )
            raise TurnFailed(reason) from exc
        finally:
            self._pending.pop(msg_id, None)
        return await self._complete_turn(response)

    async def stop(self) -> None:
        self._closed = True
        cleanup_unconfirmed = False
        if self._process is not None:
            result = await self._terminate(self._process)
            cleanup_unconfirmed = result is None
            if not cleanup_unconfirmed:
                self._process = None
                self._process_identity = None
        await self._cancel_readers()
        if cleanup_unconfirmed:
            self._cleanup_unconfirmed = True
            raise RuntimeError("backend process cleanup could not be confirmed")

    # ------------------------------------------------------------------
    # turn helpers
    # ------------------------------------------------------------------

    async def _complete_turn(self, response: dict[str, Any]) -> TurnResult:
        error = response.get("error")
        if error is not None:
            message = (
                str(error.get("message") or "rpc error")
                if isinstance(error, dict)
                else str(error)
            )
            reason = f"{self._agent_name} session/prompt failed: {message}"
            await self._emit(EVENT_TURN_FAILED, {"reason": reason, "error": error})
            raise TurnFailed(reason)
        result = response.get("result")
        stop_reason = result.get("stopReason") if isinstance(result, dict) else None
        text = "".join(self._turn_text).strip()
        if stop_reason == "cancelled":
            await self._emit(EVENT_TURN_CANCELLED, {"stop_reason": stop_reason})
            raise TurnCancelled(f"{self._agent_name} turn cancelled")
        if stop_reason != "end_turn":
            # max_tokens / max_turn_requests / refusal / unknown: the agent
            # stopped before finishing, so the turn is not a success.
            reason = f"{self._agent_name} stopped with stopReason={stop_reason!r}"
            await self._emit(
                EVENT_TURN_FAILED,
                {"reason": reason, "stop_reason": stop_reason, "message": text},
            )
            raise TurnFailed(reason)
        await self._emit(
            EVENT_TURN_COMPLETED,
            {
                "message": text,
                "result": text,
                "response": text,
                "stop_reason": stop_reason,
                "session_id": self._session_id,
            },
        )
        preview = text
        if len(preview) > _ASSISTANT_MESSAGE_PREVIEW_CAP:
            preview = preview[:_ASSISTANT_MESSAGE_PREVIEW_CAP] + "…"
        return TurnResult(
            status=EVENT_TURN_COMPLETED, turn_id=self._session_id, last_message=preview
        )

    async def _abort_turn(self, future: asyncio.Future[dict[str, Any]]) -> None:
        """`session/cancel`, a short grace, then confirm the group is gone.

        The grace only lets the agent flush its `cancelled` reply; the group
        is terminated either way so no agent keeps working after Symphony
        gave up on the turn. Unconfirmed cleanup blocks further turns.
        """
        if self._session_id is not None and self._process_alive():
            try:
                await self._write(
                    {
                        "jsonrpc": "2.0",
                        "method": METHOD_SESSION_CANCEL,
                        "params": {"sessionId": self._session_id},
                    }
                )
                await asyncio.wait_for(asyncio.shield(future), timeout=CANCEL_GRACE_S)
            except (asyncio.TimeoutError, OSError, PortExit, ResponseError):
                pass
        process = self._process
        if process is None:
            return
        try:
            result = await self._terminate(process)
        except BaseException:
            self._cleanup_unconfirmed = True
            raise
        if result is None:
            self._cleanup_unconfirmed = True
            raise RuntimeError("backend process cleanup could not be confirmed")
        self._process = None
        self._process_identity = None
        await self._cancel_readers()

    async def _terminate(self, process: asyncio.subprocess.Process) -> int | None:
        """Confirm the agent's process group is gone; None when unconfirmed."""
        if process.returncode is None and (
            self._reader_task is None or self._reader_task.done()
        ):
            # stdout already closed: the agent is exiting on its own. Reap it
            # first so the group probe does not race a half-exited leader.
            await safe_proc_wait(process, timeout=POST_STREAM_REAP_TIMEOUT_S)
        result = await terminate_process_tree(process, identity=self._process_identity)
        deadline = time.monotonic() + EXITED_GROUP_SETTLE_S
        while result is None and process.returncode is not None:
            # The leader is reaped but a just-exited member was still visible
            # to killpg and not to ps (macOS lists dying processes under
            # pgid 0). Re-probe briefly: success still needs killpg to find
            # no member at all, so live processes stay unconfirmed.
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)
            result = await terminate_process_tree(
                process, identity=self._process_identity
            )
        return result

    def _process_alive(self) -> bool:
        return (
            self._process is not None
            and self._process.returncode is None
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def _restart_session(self) -> None:
        """Relaunch a dead agent between turns; resume the session if possible."""
        previous = self._session_id
        if self._process is not None:
            result = await self._terminate(self._process)
            if result is None:
                self._cleanup_unconfirmed = True
                raise ResponseError("prior backend process cleanup is not confirmed")
            self._process = None
            self._process_identity = None
        await self._cancel_readers()
        await self.start()
        await self.initialize()
        if previous is not None and await self.resume_session(previous):
            await self._emit(
                EVENT_NOTIFICATION,
                {"type": "acp_session_resumed", "message": "agent restarted; session resumed"},
            )
            return
        await self.start_session(initial_prompt="", issue_title=None)
        await self._emit(
            EVENT_NOTIFICATION,
            {
                "type": "acp_session_restarted",
                "message": (
                    "agent process exited between turns and cannot resume "
                    "sessions; continuing in a fresh ACP session without "
                    "prior turn context"
                ),
            },
        )

    async def _cancel_readers(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._reader_task = None
        self._stderr_task = None

    # ------------------------------------------------------------------
    # JSON-RPC over stdio
    # ------------------------------------------------------------------

    async def _send_request(
        self, method: str, params: dict[str, Any]
    ) -> tuple[int, asyncio.Future[dict[str, Any]]]:
        if self._process is None or self._process.stdin is None:
            raise ResponseError("subprocess not started")
        msg_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._write(
                {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
            )
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(msg_id, None)
            raise PortExit(f"{self._agent_name} stdin closed", error=str(exc)) from exc
        return msg_id, future

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Synchronous request bounded by the profile's `read_timeout_ms`."""
        if self._closed:
            raise ResponseError("backend is closed")
        msg_id, future = await self._send_request(method, params)
        try:
            response = await asyncio.wait_for(
                future, timeout=self._profile.read_timeout_ms / 1000.0
            )
        except asyncio.TimeoutError as exc:
            raise ResponseTimeout(
                f"{self._agent_name} did not answer {method}", method=method
            ) from exc
        finally:
            self._pending.pop(msg_id, None)
        error = response.get("error")
        if error is not None:
            message = (
                str(error.get("message") or "rpc error")
                if isinstance(error, dict)
                else str(error)
            )
            if isinstance(error, dict) and error.get("code") == JSONRPC_AUTH_REQUIRED:
                message += (
                    f" (log in to {self._agent_name} interactively first; "
                    "Symphony does not run ACP authentication)"
                )
            raise ResponseError(message, method=method)
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def _write(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise ResponseError("subprocess not started")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self._process.stdin.write((body + "\n").encode("utf-8"))
        await self._process.stdin.drain()

    async def _stdout_reader(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        malformed_streak = 0
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("acp_stdout_read_error", agent=self._agent_name, error=str(exc))
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    await self._emit(EVENT_MALFORMED, {"raw": text[:500]})
                    malformed_streak += 1
                    if malformed_streak >= MALFORMED_LINE_LIMIT:
                        self._stream_corrupt = text
                        break
                    continue
                malformed_streak = 0
                if not isinstance(msg, dict):
                    await self._emit(EVENT_MALFORMED, {"raw": text[:500]})
                    continue
                await self._dispatch(msg)
        finally:
            if self._stream_corrupt is not None:
                reason = (
                    f"{self._agent_name} stream unreadable: {MALFORMED_LINE_LIMIT} "
                    f"consecutive malformed lines (last: {self._stream_corrupt[:200]!r})"
                )
            else:
                reason = f"{self._agent_name} closed stdout (rc={process.returncode})"
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(PortExit(reason))

    async def _stderr_reader(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while True:
            try:
                line = await process.stderr.readline()
            except asyncio.CancelledError:
                raise
            except Exception:
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_tail.append(text)
            log.debug("acp_stderr", agent=self._agent_name, line=text)

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        if "id" in msg and not method:
            fut = self._pending.get(msg["id"]) if isinstance(msg["id"], int) else None
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        if not isinstance(method, str):
            await self._emit(EVENT_MALFORMED, {"raw": json.dumps(msg)[:500]})
            return
        params = msg.get("params")
        params = params if isinstance(params, dict) else {}
        if "id" in msg:
            await self._handle_agent_request(msg["id"], method, params)
            return
        if method == METHOD_SESSION_UPDATE:
            if not self._replaying:
                await self._handle_update(params)
            return
        await self._emit(EVENT_OTHER_MESSAGE, {"type": "acp_notification", "method": method})

    async def _handle_agent_request(
        self, msg_id: Any, method: str, params: dict[str, Any]
    ) -> None:
        if method == METHOD_REQUEST_PERMISSION:
            result = await self._answer_permission(params)
            response: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        else:
            # fs/*, terminal/* and anything else were never advertised.
            await self._emit(
                EVENT_OTHER_MESSAGE, {"type": "acp_rejected_request", "method": method}
            )
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": JSONRPC_METHOD_NOT_FOUND,
                    "message": f"client method not supported: {method}",
                },
            }
        try:
            await self._write(response)
        except (OSError, ResponseError) as exc:
            log.warning("acp_response_write_failed", method=method, error=str(exc))

    async def _answer_permission(self, params: dict[str, Any]) -> dict[str, Any]:
        options = params.get("options")
        options = [opt for opt in options if isinstance(opt, dict)] if isinstance(options, list) else []
        tool_call = params.get("toolCall")
        tool_call = tool_call if isinstance(tool_call, dict) else {}
        title = str(tool_call.get("title") or "")[:400]
        command = _tool_call_command(tool_call)
        denial = dangerous_command_reason(command) if command else None
        if denial is not None:
            chosen = _pick_option(options, ("reject_once", "reject_always"))
            await self._emit(
                EVENT_APPROVAL_DENIED,
                {"method": METHOD_REQUEST_PERMISSION, "command": command[:400], "reason": denial},
            )
        else:
            chosen = _pick_option(options, ("allow_once", "allow_always"))
            if chosen is None:
                await self._emit(
                    EVENT_APPROVAL_DENIED,
                    {
                        "method": METHOD_REQUEST_PERMISSION,
                        "tool": title,
                        "reason": "no allow option offered",
                    },
                )
            else:
                payload = {"method": METHOD_REQUEST_PERMISSION, "option": chosen}
                if command:
                    payload["command"] = command[:400]
                elif title:
                    payload["tool"] = title
                await self._emit(EVENT_APPROVAL_AUTO_APPROVED, payload)
        if chosen is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": chosen}}

    async def _handle_update(self, params: dict[str, Any]) -> None:
        update = params.get("update")
        if not isinstance(update, dict):
            await self._emit(EVENT_OTHER_MESSAGE, {"type": "acp_update", "update": params})
            return
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = _content_text(update.get("content"))
            if text:
                self._turn_text.append(text)
                await self._emit(EVENT_OTHER_MESSAGE, {"type": "agent_delta", "text": text})
            return
        if kind == "agent_thought_chunk":
            text = _content_text(update.get("content"))
            await self._emit(EVENT_OTHER_MESSAGE, {"type": "agent_thought", "text": text})
            return
        if kind in ("tool_call", "tool_call_update"):
            await self._emit(
                EVENT_OTHER_MESSAGE,
                {
                    "type": kind,
                    "tool_call_id": update.get("toolCallId"),
                    "title": update.get("title"),
                    "kind": update.get("kind"),
                    "status": update.get("status"),
                },
            )
            return
        if kind == "plan":
            await self._emit(
                EVENT_OTHER_MESSAGE, {"type": "plan", "entries": update.get("entries")}
            )
            return
        if kind == "usage_update":
            payload: dict[str, Any] = {
                "type": "usage",
                "context_used_tokens": update.get("used"),
                "context_size_tokens": update.get("size"),
            }
            cost = update.get("cost")
            if isinstance(cost, dict):
                payload["cost"] = {
                    "amount": cost.get("amount"),
                    "currency": cost.get("currency"),
                }
            await self._emit(EVENT_OTHER_MESSAGE, payload)
            return
        await self._emit(
            EVENT_OTHER_MESSAGE, {"type": "acp_update", "session_update": str(kind)[:100]}
        )

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await _emit_event(
            self._on_event,
            event,
            payload,
            usage=self._latest_usage,
            agent_pid=self.pid,
        )


def _pick_option(options: list[dict[str, Any]], kinds: tuple[str, ...]) -> str | None:
    for kind in kinds:
        for option in options:
            option_id = option.get("optionId")
            if option.get("kind") == kind and isinstance(option_id, str):
                return option_id
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, dict) and content.get("type") == "text":
        text = content.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _tool_call_command(tool_call: dict[str, Any]) -> str:
    """Best-effort shell command behind an `execute` permission request."""
    raw = tool_call.get("rawInput")
    if isinstance(raw, dict):
        command = raw.get("command") or raw.get("cmd")
        if isinstance(command, str):
            return command
        if isinstance(command, list):
            return " ".join(str(part) for part in command)
    if tool_call.get("kind") == "execute":
        title = tool_call.get("title")
        return title if isinstance(title, str) else ""
    return ""
