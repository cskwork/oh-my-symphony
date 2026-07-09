"""OpenCode CLI backend.

Drives `opencode run --format json --auto [message..]` once per turn.
OpenCode owns the real session id; Symphony starts with a local worker id and
switches to the OpenCode id if it appears in JSON events. Continuation turns
only pass `--session <id>` after that handoff.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from collections import Counter
from typing import Any, Iterable

from . import (
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    BackendInit,
    TurnResult,
)
from .per_turn import PerTurnCliBackend


HEARTBEAT_INTERVAL_S = 30.0
STREAM_READ_CHUNK_BYTES = 64 * 1024
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
    "reasoning",
    "thoughts",
    "tool",
    "total",
    "totalTokens",
    "total_tokens",
}


class OpenCodeBackend(PerTurnCliBackend):
    """One subprocess per turn; parses OpenCode raw JSON events."""

    def __init__(self, init: BackendInit) -> None:
        cfg = init.cfg.opencode
        super().__init__(
            init, agent_name="opencode", turn_timeout_ms=cfg.turn_timeout_ms
        )
        self._opencode = cfg
        self._opencode_session_id: str | None = None
        self._streamed_event_counts: Counter[str] = Counter()

    # ------------------------------------------------------------------
    # per-turn hooks
    # ------------------------------------------------------------------

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") in {"opencode_heartbeat", "opencode_usage"}

    @property
    def session_id(self) -> str | None:
        return self._opencode_session_id or self._session_id

    def _stdin_payload(self, prompt: str) -> str | None:
        del prompt  # travels in the command line
        return None

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        cmd = self._opencode.command
        if (
            is_continuation
            and self._opencode.resume_across_turns
            and self._opencode_session_id
        ):
            cmd = f"{cmd} --session {shlex.quote(self._opencode_session_id)}"
        return f"{cmd} {shlex.quote(prompt)}"

    def _start_watchers(
        self, proc: asyncio.subprocess.Process
    ) -> list["asyncio.Task[None]"]:
        return [asyncio.create_task(self._emit_heartbeats(proc))]

    async def _read_stdout(self, stream: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        pending = bytearray()
        self._streamed_event_counts.clear()
        while chunk := await stream.read(STREAM_READ_CHUNK_BYTES):
            chunks.append(chunk)
            pending.extend(chunk)
            start = 0
            while (end := pending.find(b"\n", start)) >= 0:
                await self._publish_stream_frame(bytes(pending[start : end + 1]))
                start = end + 1
            if start:
                del pending[:start]
        if pending:
            await self._publish_stream_frame(bytes(pending))
        return b"".join(chunks)

    async def _publish_stream_frame(self, frame: bytes) -> None:
        text = frame.decode("utf-8", errors="replace")
        for event in self._decode_events(text):
            await self._publish_stream_event(event)
            self._streamed_event_counts[_event_fingerprint(event)] += 1

    async def _publish_stream_event(self, event: dict[str, Any]) -> None:
        new_sid = _extract_session_id(event)
        if new_sid and new_sid != self._opencode_session_id:
            self._opencode_session_id = new_sid
            await self._emit(
                EVENT_SESSION_STARTED,
                {"session_id": new_sid, "thread_id": new_sid},
            )
        previous_usage = self.latest_usage
        self._update_usage_from_events((event,))
        if self.latest_usage != previous_usage:
            await self._emit(EVENT_OTHER_MESSAGE, {"type": "opencode_usage"})

    async def _complete_turn(self, stdout_text: str, rc: int) -> TurnResult:
        events = self._decode_events(stdout_text)
        streamed = self._streamed_event_counts.copy()
        for event in events:
            fingerprint = _event_fingerprint(event)
            if streamed[fingerprint]:
                streamed[fingerprint] -= 1
            else:
                await self._publish_stream_event(event)
        self._streamed_event_counts.clear()
        response = self._response_from_events(events) if events else stdout_text
        # `message` key feeds _preview_from_payload -> current_turn_message so a
        # productive turn resets the G2 empty-loop counter. Streaming frames
        # are retained so the final response can still be assembled here.
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

    async def _emit_heartbeats(self, proc: asyncio.subprocess.Process) -> None:
        # Usage frames mark progress; heartbeats cover quiet model/tool periods.
        while proc.returncode is None:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            if proc.returncode is not None:
                return
            await self._emit(
                EVENT_OTHER_MESSAGE,
                {"type": "opencode_heartbeat", "pid": proc.pid},
            )

    # ------------------------------------------------------------------
    # JSON event decoding
    # ------------------------------------------------------------------

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
        explicit_cache_tokens = _int_value(
            usage,
            "cache_input_tokens",
            "cached",
        )
        if explicit_cache_tokens:
            input_tokens += explicit_cache_tokens
        else:
            input_tokens += _sum_int_values(
                usage,
                "cache_read",
                "cacheRead",
                "cache_write",
                "cacheWrite",
            )
            cache = usage.get("cache")
            if isinstance(cache, dict):
                input_tokens += _sum_int_values(
                    cache,
                    "read",
                    "write",
                    "cache_read",
                    "cache_write",
                    "cacheRead",
                    "cacheWrite",
                )
        output_tokens = _int_value(
            usage,
            "output_tokens",
            "completion_tokens",
            "output",
            "completion",
            "reasoning",
            "candidates",
            "thoughts",
            "tool",
        )
        total_tokens = _int_value(usage, "total_tokens", "totalTokens", "total")
        if total_tokens == 0 and (input_tokens or output_tokens):
            total_tokens = input_tokens + output_tokens
        self._latest_usage["input_tokens"] += input_tokens
        self._latest_usage["output_tokens"] += output_tokens
        self._latest_usage["total_tokens"] += total_tokens


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


def _event_fingerprint(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
    for key in ("usage", "tokens", "stats", "cost", "part", "info"):
        yield from _usage_dicts(value.get(key))


def _int_value(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            continue
        if ivalue:
            return ivalue
    return 0


def _sum_int_values(data: dict[str, Any], *keys: str) -> int:
    total = 0
    for key in keys:
        value = data.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            continue
        if ivalue > 0:
            total += ivalue
    return total
