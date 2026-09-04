"""Claude Code CLI backend.

Drives `claude -p --output-format stream-json --verbose` once per turn. The
underlying CLI does not have a persistent app-server; sessions are tracked by
ID, so this backend spawns a fresh subprocess each turn and uses
`--resume <session-id>` for continuity.

Stream-JSON event shape (one line of JSON per event):

  {"type":"system","subtype":"init","session_id":"...","model":"...",...}
  {"type":"assistant","message":{...,"usage":{...}},"session_id":"..."}
  {"type":"user","message":{"content":[{"type":"tool_result",...}]},...}
  {"type":"result","subtype":"success","is_error":false,
   "usage":{"input_tokens":N,"output_tokens":N,...},"result":"...",
   "total_cost_usd":N,"session_id":"...","duration_ms":N,...}

Errors surface as `result` events with `is_error:true` and a `subtype`
indicating the failure mode (e.g. `error_max_turns`).

The spawn / prompt / stream / reap lifecycle lives in
``per_turn.JsonlStreamBackend``; this module only supplies the claude
command line, the stream-json frame handling, and the result interpretation.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any, Sequence

from ..errors import TurnFailed
from ..logging import get_logger
from ..utils.git_sandbox import GIT_ROOTS_ENV_VAR, git_roots_outside
from . import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    TurnResult,
)
from .per_turn import JsonlStreamBackend


log = get_logger()


def _inject_add_dirs(command: str, dirs: Sequence[str]) -> str:
    """Inject ``--add-dir <path>`` right after a literal ``claude`` token.

    Injecting after the token rather than appending keeps pipelines and
    redirections in operator-authored commands intact. Non-``claude``
    commands (wrapper scripts) are returned unchanged; they get the same
    list through :data:`GIT_ROOTS_ENV_VAR` instead.
    """
    if not dirs:
        return command
    stripped = command.lstrip()
    if not (stripped == "claude" or stripped.startswith(("claude ", "claude\t"))):
        return command
    leading_ws = command[: len(command) - len(stripped)]
    rest = stripped[len("claude") :]
    flags = " ".join(f"--add-dir {shlex.quote(d)}" for d in dirs)
    return f"{leading_ws}claude {flags}{rest}"


class ClaudeCodeBackend(JsonlStreamBackend):
    """One subprocess per turn; speaks Claude Code stream-json."""

    _resume_flag = "--resume"

    def __init__(self, init: BackendInit) -> None:
        cfg = init.cfg.claude
        super().__init__(init, agent_name="claude", turn_timeout_ms=cfg.turn_timeout_ms)
        self._claude = cfg
        # Resolved once: the worktree layout cannot change mid-run, and
        # run_turn spawns a fresh subprocess every turn.
        self._git_roots = git_roots_outside(init.cwd, init.workspace_root)
        if self._git_roots:
            log.info("claude_git_roots_granted", roots=self._git_roots)
        self._latest_usage = {
            "input_tokens": 0,
            "cache_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._latest_rate_limits: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # AgentBackend lifecycle
    # ------------------------------------------------------------------

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return dict(self._latest_rate_limits) if self._latest_rate_limits is not None else None

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        """Only count claude `assistant` stream-json frames as progress.

        The catch-all `EVENT_OTHER_MESSAGE` path also carries `user` frames
        whose payload is a `tool_result` echo. Resetting the stall timer
        on every echo masked a real 18-minute model stall in OLV-002
        (commit 499e787). Restrict the predicate to real assistant output;
        the orchestrator additionally treats lifecycle events and OUTPUT
        token deltas as progress so this filter only narrows the catch-all
        OTHER_MESSAGE bucket.
        """
        return event.get("type") == "assistant"

    async def initialize(self) -> dict[str, Any]:
        return {"agent": "claude_code"}

    # ------------------------------------------------------------------
    # per-turn hooks
    # ------------------------------------------------------------------

    def _git_roots_env(self) -> dict[str, str]:
        if not self._git_roots:
            return {}
        return {GIT_ROOTS_ENV_VAR: os.pathsep.join(self._git_roots)}

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        del prompt  # travels via stdin
        cmd = _inject_add_dirs(self._claude.command, self._git_roots)
        return cmd + self._resume_args(
            is_continuation=is_continuation,
            resume_across_turns=self._claude.resume_across_turns,
        )

    async def _handle_stream_event(
        self, msg: dict[str, Any]
    ) -> dict[str, Any] | None:
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "init":
            sid = msg.get("session_id")
            if isinstance(sid, str) and sid:
                await self._observe_session_id(sid)
        elif kind == "assistant":
            # Mid-stream `usage` deltas are ignored; the terminal
            # `result` event is the source of truth for accumulation.
            last_text = _extract_text(msg.get("message") or {})
            if last_text:
                self._last_message = last_text[:400]
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        elif kind == "user":
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        elif kind == "result":
            self._update_usage_absolute(msg.get("usage") or {})
            sid = msg.get("session_id")
            if isinstance(sid, str) and sid:
                await self._observe_session_id(sid)
            return msg
        else:
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        return None

    async def _complete_stream_turn(
        self,
        proc: asyncio.subprocess.Process,
        terminal: dict[str, Any] | None,
        rc: int | None,
    ) -> TurnResult:
        # Claude reports failure through the `result` event, not the exit
        # status; the status only decorates the no-result diagnostic.
        del rc
        if terminal is None:
            # Stream ended without a `result` event — treat as failure.
            stderr_blob = self._stderr_blob()
            err_msg = (
                f"claude exited with no result event (rc={proc.returncode})"
                + (f"; stderr: {stderr_blob}" if stderr_blob else "")
            )
            await self._emit(
                EVENT_TURN_FAILED,
                {"reason": err_msg, "stderr_tail": list(self._stderr_tail)},
            )
            raise TurnFailed(err_msg)

        if _is_error_result(terminal):
            reason = _error_result_message(terminal)
            payload = {
                **terminal,
                "reason": reason,
                "stderr_tail": list(self._stderr_tail),
            }
            await self._emit(EVENT_TURN_FAILED, payload)
            raise TurnFailed(reason)

        await self._require_resume_confirmation()

        message = str(terminal.get("result") or "").strip() or self._last_message
        self._last_message = message[:400]
        await self._emit(
            EVENT_TURN_COMPLETED,
            {**terminal, "message": message},
        )
        return TurnResult(
            status=EVENT_TURN_COMPLETED,
            turn_id=str(terminal.get("session_id") or self._session_id or ""),
            last_message=self._last_message,
        )

    def _update_usage_absolute(self, usage: dict[str, Any]) -> None:
        # Each `result` event reports usage for that one turn — accumulate.
        if not isinstance(usage, dict):
            return
        in_t = int(usage.get("input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_create = int(usage.get("cache_creation_input_tokens") or 0)
        out_t = int(usage.get("output_tokens") or 0)
        cache_t = cache_read + cache_create
        self._latest_usage["input_tokens"] += in_t
        self._latest_usage["cache_input_tokens"] += cache_t
        self._latest_usage["output_tokens"] += out_t
        self._latest_usage["total_tokens"] += in_t + cache_t + out_t


def _extract_text(message: dict[str, Any]) -> str:
    """Pull the last text block out of a Claude assistant message."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    for block in reversed(content):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return ""


def _is_error_result(event: dict[str, Any]) -> bool:
    subtype = str(event.get("subtype") or "").lower()
    value = event.get("is_error")
    if isinstance(value, bool):
        if value:
            return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
    elif value:
        return True

    if subtype.startswith("error"):
        return True
    if subtype == "success":
        return False
    return False


def _error_result_message(event: dict[str, Any]) -> str:
    """Return a human-actionable reason for Claude result failures."""
    for key in ("error", "message", "result", "api_error_message", "api_error_status"):
        value = event.get(key)
        if isinstance(value, dict):
            text = _extract_text(value)
        else:
            text = str(value or "").strip()
        if text and text.lower() != "success":
            return text

    subtype = str(event.get("subtype") or "").strip()
    if subtype.lower().startswith("error"):
        return subtype
    return "claude turn failed"
