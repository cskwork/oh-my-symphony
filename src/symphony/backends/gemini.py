"""Gemini CLI backend.

Drives `gemini -p "" --yolo` once per turn. Current Gemini CLI releases expose
plain stdout rather than Symphony-friendly JSON/session flags, so Symphony
mints and keeps a local session UUID for telemetry while treating each Gemini
CLI invocation as a one-shot turn. If an older/custom command returns JSON, the
backend still parses its response/stats best-effort.
"""

from __future__ import annotations

import json
from typing import Any

from . import (
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    BackendInit,
    TurnResult,
)
from .per_turn import PerTurnCliBackend, _has_shell_flag


class GeminiBackend(PerTurnCliBackend):
    """One subprocess per turn; parses plain text or best-effort JSON output."""

    def __init__(self, init: BackendInit) -> None:
        cfg = init.cfg.gemini
        super().__init__(
            init, agent_name="gemini", turn_timeout_ms=cfg.turn_timeout_ms
        )
        self._gemini = cfg

    # ------------------------------------------------------------------
    # per-turn hooks
    # ------------------------------------------------------------------

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        del prompt, is_continuation  # prompt travels via stdin; no resume flag
        cmd = self._gemini.command
        if _has_shell_flag(cmd, "-y", "--yolo"):
            return cmd
        return f"{cmd} --yolo"

    async def _complete_turn(self, stdout_text: str, rc: int) -> TurnResult:
        parsed = self._parse_json_output(stdout_text)
        if parsed is not None:
            sid = parsed.get("session_id")
            if isinstance(sid, str) and sid:
                old_sid = self._session_id
                self._session_id = sid
                if sid != old_sid:
                    await self._emit(
                        EVENT_SESSION_STARTED,
                        {"session_id": sid, "thread_id": sid},
                    )
            response = parsed.get("response")
            last_message = response if isinstance(response, str) else stdout_text
            stats = parsed.get("stats") if isinstance(parsed.get("stats"), dict) else {}
            self._update_usage_from_stats(stats)
        else:
            last_message = stdout_text
            stats = {}
        payload = {
            "message": last_message,
            "result": last_message,
            "response": last_message,
            "session_id": self._session_id,
            "stats": stats,
            "exit_code": rc,
        }
        await self._emit(EVENT_TURN_COMPLETED, payload)
        return TurnResult(
            status=EVENT_TURN_COMPLETED,
            turn_id=self._session_id,
            last_message=last_message[:400],
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _parse_json_output(self, text: str) -> dict[str, Any] | None:
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _update_usage_from_stats(self, stats: Any) -> None:
        if not isinstance(stats, dict):
            return
        models = stats.get("models")
        if not isinstance(models, dict):
            return
        input_tokens = 0
        output_tokens = 0
        for model in models.values():
            if not isinstance(model, dict):
                continue
            tokens = model.get("tokens")
            if not isinstance(tokens, dict):
                continue
            input_tokens += int(tokens.get("input") or 0)
            input_tokens += int(tokens.get("cached") or 0)
            output_tokens += int(tokens.get("candidates") or 0)
            output_tokens += int(tokens.get("thoughts") or 0)
            output_tokens += int(tokens.get("tool") or 0)
        self._latest_usage["input_tokens"] += input_tokens
        self._latest_usage["output_tokens"] += output_tokens
        self._latest_usage["total_tokens"] += input_tokens + output_tokens
