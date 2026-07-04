"""OpenCode CLI backend.

Drives `opencode run --format json --auto [message..]` once per turn.
OpenCode owns the real session id; Symphony starts with a local worker id and
switches to the OpenCode id if it appears in JSON events. Continuation turns
only pass `--session <id>` after that handoff.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
import uuid
from collections import deque
from typing import Any, Iterable

from .._shell import resolve_bash, safe_proc_wait, terminate_process_tree
from ..errors import PortExit, ResponseError, TurnFailed, TurnTimeout
from ..logging import get_logger
from ..workspace import validate_agent_cwd
from . import (
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    BaseAgentBackend,
    TurnResult,
)


log = get_logger()

MAX_LINE_BYTES = 10 * 1024 * 1024
HEARTBEAT_INTERVAL_S = 30.0
TOKEN_KEYS = {
    "cached",
    "cache_input_tokens",
    "cache_read",
    "cacheRead",
    "cache_write",
    "cacheWrite",
    "candidates",
    "completion",
    "completion_tokens",
    "input",
    "input_tokens",
    "output",
    "output_tokens",
    "prompt",
    "prompt_tokens",
    "thoughts",
    "tool",
    "total",
    "total_tokens",
}


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class OpenCodeBackend(BaseAgentBackend):
    """One subprocess per turn; parses OpenCode raw JSON events."""

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") == "opencode_heartbeat"

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._opencode = init.cfg.opencode
        self._cwd = init.cwd
        self._on_event = init.on_event
        self._session_id: str | None = None
        self._opencode_session_id: str | None = None
        self._closed = False
        self._active_proc: asyncio.subprocess.Process | None = None
        self._latest_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._stderr_tail: deque[str] = deque(maxlen=20)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._active_proc
        if proc is not None and proc.returncode is None:
            await terminate_process_tree(proc)

    @property
    def session_id(self) -> str | None:
        return self._opencode_session_id or self._session_id

    @property
    def pid(self) -> int | None:
        return self._active_proc.pid if self._active_proc is not None else None

    @property
    def latest_usage(self) -> dict[str, int]:
        return dict(self._latest_usage)

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    async def initialize(self) -> dict[str, Any]:
        return {"agent": "opencode"}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        del initial_prompt, issue_title
        self._session_id = str(uuid.uuid4())
        await self._emit(
            EVENT_SESSION_STARTED,
            {"session_id": self._session_id, "thread_id": self._session_id},
        )
        return self._session_id

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        if self._closed:
            raise ResponseError("backend is closed")

        command = self._command_for_turn(prompt=prompt, is_continuation=is_continuation)
        try:
            proc = await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                command,
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
                limit=MAX_LINE_BYTES,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as exc:
            raise PortExit("bash not available", error=str(exc)) from exc

        if self._closed:
            await self._reap(proc)
            raise ResponseError("backend closed during spawn")
        self._active_proc = proc
        heartbeat_task = asyncio.create_task(self._emit_heartbeats(proc))
        try:
            timeout_s = self._opencode.turn_timeout_ms / 1000.0
            assert proc.stdout is not None and proc.stderr is not None
            stdout_task = asyncio.create_task(proc.stdout.read())
            stderr_task = asyncio.create_task(proc.stderr.read())
            try:
                stdout, stderr, safe_rc = await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, safe_proc_wait(proc)),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError as exc:
                stdout_task.cancel()
                stderr_task.cancel()
                await self._reap(proc)
                await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
                raise TurnTimeout("opencode turn timed out") from exc

            rc = safe_rc if safe_rc is not None else (proc.returncode or 0)
            self._capture_stderr(stderr or b"")
            if rc != 0:
                err_msg = self._stderr_blob()
                payload = {
                    "reason": f"opencode exit {rc}"
                    + (f"; stderr: {err_msg}" if err_msg else ""),
                    "stderr_tail": list(self._stderr_tail),
                    "stderr": err_msg,
                }
                await self._emit(EVENT_TURN_FAILED, payload)
                raise TurnFailed(err_msg or f"opencode failed with exit {rc}")

            result_text = (stdout or b"").decode("utf-8", errors="replace").strip()
            events = self._decode_events(result_text)
            response = self._response_from_events(events) if events else result_text
            new_sid = self._session_id_from_events(events)
            if new_sid and new_sid != self._opencode_session_id:
                self._opencode_session_id = new_sid
                await self._emit(
                    EVENT_SESSION_STARTED,
                    {"session_id": new_sid, "thread_id": new_sid},
                )
            self._update_usage_from_events(events)
            # `message` key feeds _preview_from_payload -> current_turn_message so a
            # productive turn resets the G2 empty-loop counter (opencode delivers
            # text only at turn end).
            payload = {
                "message": response,
                "result": response,
                "response": response,
                "session_id": self.session_id,
                "events": events,
                "exit_code": rc,
            }
            await self._emit(EVENT_TURN_COMPLETED, payload)
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=self.session_id,
                last_message=response[:400],
            )
        except asyncio.CancelledError:
            await self._reap(proc)
            raise
        finally:
            heartbeat_task.cancel()
            self._active_proc = None

    async def _emit_heartbeats(self, proc: asyncio.subprocess.Process) -> None:
        # OpenCode emits no JSON until the per-turn subprocess exits; liveness
        # keeps the shared stall detector from cancelling healthy long turns.
        while proc.returncode is None:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            if proc.returncode is not None:
                return
            await self._emit(
                EVENT_OTHER_MESSAGE,
                {"type": "opencode_heartbeat", "pid": proc.pid},
            )

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        cmd = self._opencode.command
        if (
            is_continuation
            and self._opencode.resume_across_turns
            and self._opencode_session_id
        ):
            cmd = f"{cmd} --session {shlex.quote(self._opencode_session_id)}"
        return f"{cmd} {shlex.quote(prompt)}"

    def _decode_events(self, text: str) -> list[dict[str, Any]]:
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return self._decode_json_lines(text)
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return []

    def _decode_json_lines(self, text: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        return events

    def _session_id_from_events(self, events: Iterable[dict[str, Any]]) -> str | None:
        for event in events:
            session_id = _extract_session_id(event)
            if session_id:
                return session_id
        return None

    def _response_from_events(self, events: Iterable[dict[str, Any]]) -> str:
        parts = [self._text_from_event(event) for event in events]
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _text_from_event(event: dict[str, Any]) -> str:
        # opencode `run --format json` streams JSONL frames shaped as
        # {"type": ..., "sessionID": ..., "part": {...}} (src/cli/cmd/run.ts
        # `emit`). Assistant prose is carried by type=="text" frames under
        # part.text; tool_use / step_start frames carry no user-facing prose.
        # Reading part.text is what keeps `response` non-empty so the G2
        # empty-loop guard sees a productive turn. Fall back to the flat-key
        # scan for the pre-JSONL raw shape and non-opencode payloads.
        if isinstance(event, dict) and event.get("type") == "text":
            part = event.get("part")
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return _extract_text(event)

    def _update_usage_from_events(self, events: Iterable[dict[str, Any]]) -> None:
        for event in events:
            for usage in _usage_dicts(event):
                self._apply_usage(usage)

    def _apply_usage(self, usage: dict[str, Any]) -> None:
        input_tokens = _int_value(usage, "input_tokens", "prompt_tokens", "input", "prompt")
        input_tokens += _int_value(
            usage,
            "cache_input_tokens",
            "cache_read",
            "cacheRead",
            "cache_write",
            "cacheWrite",
            "cached",
        )
        output_tokens = _int_value(
            usage,
            "output_tokens",
            "completion_tokens",
            "output",
            "completion",
            "candidates",
            "thoughts",
            "tool",
        )
        total_tokens = _int_value(usage, "total_tokens", "total")
        if total_tokens == 0 and (input_tokens or output_tokens):
            total_tokens = input_tokens + output_tokens
        self._latest_usage["input_tokens"] += input_tokens
        self._latest_usage["output_tokens"] += output_tokens
        self._latest_usage["total_tokens"] += total_tokens

    def _capture_stderr(self, stderr: bytes) -> None:
        text = stderr.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line:
                self._stderr_tail.append(line)

    def _stderr_blob(self) -> str:
        if not self._stderr_tail:
            return ""
        joined = " | ".join(self._stderr_tail)
        return joined if len(joined) <= 400 else joined[-400:]

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        await terminate_process_tree(proc)

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            await self._on_event(
                {
                    "event": event,
                    "timestamp": _utc_iso(),
                    "payload": payload if isinstance(payload, dict) else {"data": payload},
                    "usage": dict(self._latest_usage),
                    "rate_limits": None,
                    "agent_pid": self.pid,
                }
            )
        except Exception as exc:
            log.warning("event_callback_failed", error=str(exc))


def _extract_session_id(event: dict[str, Any]) -> str | None:
    for key in ("session_id", "sessionId", "sessionID"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("session", "data"):
        nested = event.get(key)
        if isinstance(nested, dict):
            value = _extract_session_id(nested)
            if value:
                return value
            nested_id = nested.get("id")
            if isinstance(nested_id, str) and nested_id:
                return nested_id
    return None


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part for part in (_extract_text(item) for item in value) if part)
    if not isinstance(value, dict):
        return ""
    for key in ("response", "result", "message", "text", "content", "output"):
        extracted = _extract_text(value.get(key))
        if extracted:
            return extracted
    return _extract_text(value.get("data"))


def _usage_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _usage_dicts(item)
        return
    if not isinstance(value, dict):
        return
    if TOKEN_KEYS.intersection(value.keys()):
        yield value
    for key in ("usage", "tokens", "stats", "cost"):
        yield from _usage_dicts(value.get(key))


def _int_value(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            continue
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            continue
        if ivalue:
            return ivalue
    return 0
