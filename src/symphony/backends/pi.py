"""Pi coding-agent CLI backend.

Drives `pi --mode json -p ""` once per turn. Pi (https://pi.dev) auto-saves
sessions under `~/.pi/agent/sessions/<cwd-hash>/`; multi-turn continuity
re-enters the captured session id with `--session <id>`.

JSON mode emits one event per line on stdout. The first line is the session
header, followed by `AgentSessionEvent` items as the run progresses:

  {"type":"session","version":3,"id":"<uuid>","timestamp":"...","cwd":"..."}
  {"type":"agent_start", ...}
  {"type":"turn_start", ...}
  {"type":"message_start", ...}
  {"type":"message_update", ...}
  {"type":"message_end","message":{...,"usage":{...},"stopReason":"stop"}}
  {"type":"tool_execution_start", ...}
  {"type":"tool_execution_end","toolName":"...","isError":false,"result":...}
  {"type":"turn_end","message":AssistantMessage,"toolResults":[...]}
  {"type":"agent_end","messages":[AssistantMessage, ...]}

Terminal event: `agent_end`. Errors surface either as
`message.stopReason == "error"` with `errorMessage`, or as `tool_execution_end`
with `isError: true`. Process-level fatals go to stderr.

Usage shape inside an `AssistantMessage`:
  {"input": N, "output": N, "cacheRead": N, "cacheWrite": N,
   "totalTokens": N, "cost": {...}}

We accumulate (input + cacheRead + cacheWrite) into `input_tokens` so totals
remain comparable with the Codex / Claude buckets, where token counts (not
billed cost) are the unit.

The spawn / prompt / stream / reap lifecycle lives in
``per_turn.JsonlStreamBackend``; this module supplies the pi command line,
the JSONL frame handling, and the terminal-event / exit-status
interpretation. ``prime_agent.py`` subclasses it with a different config
section and resume flag.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..errors import TurnFailed
from ..logging import get_logger
from ..workflow import PiConfig, PrimeAgentConfig
from . import (
    EVENT_AGENT_RETRY,
    EVENT_COMPACTION,
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    TurnResult,
)
from .per_turn import JsonlStreamBackend


log = get_logger()

# Model-output lifecycle events. Everything else on the stream (session
# header, tool_execution_* echoes, keepalives) must not reset the
# orchestrator's stall clock — mirrors the claude backend's
# assistant-frames-only predicate.
_PROGRESS_EVENT_TYPES = frozenset(
    {"message_start", "message_update", "message_end", "turn_start", "turn_end"}
)


class PiBackend(JsonlStreamBackend):
    """One subprocess per turn; speaks pi --mode json JSONL."""

    _agent_name = "pi"

    # Subclass override point: the CLI flag used for session resume.
    # Pi uses ``--session <id>``; PrimeAgent uses ``--resume <id>``.
    _resume_flag = "--session"

    # Let stderr settle briefly after stdout closes so a TurnFailed can
    # carry the actual reason (auth error, network, ratelimit, ...) up to
    # the orchestrator instead of the opaque "no agent_end event" string.
    _stderr_settle_s = 0.1

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") in _PROGRESS_EVENT_TYPES

    def __init__(self, init: BackendInit) -> None:
        cfg = self._turn_config(init)
        super().__init__(
            init, agent_name=self._agent_name, turn_timeout_ms=cfg.turn_timeout_ms
        )
        self._pi = cfg

    @staticmethod
    def _turn_config(init: BackendInit) -> PiConfig | PrimeAgentConfig:
        """Config section this backend drives; PrimeAgent points elsewhere."""
        return init.cfg.pi

    # ------------------------------------------------------------------
    # per-turn hooks
    # ------------------------------------------------------------------

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        # Pi documents stdin as appended to the `-p` argument. Since the
        # default command is `pi --mode json -p ""`, the prompt arrives
        # entirely through stdin.
        del prompt
        # Both the assistant preview and stderr diagnostics belong to this
        # subprocess. Clear them before every turn so a missing terminal event
        # cannot make a later empty turn look successful (or repeat stale
        # diagnostics from an earlier process).
        self._last_message = ""
        self._stderr_tail.clear()
        return self._pi.command + self._resume_args(
            is_continuation=is_continuation,
            resume_across_turns=self._pi.resume_across_turns,
        )

    async def _handle_stream_event(
        self, msg: dict[str, Any]
    ) -> dict[str, Any] | None:
        kind = msg.get("type")
        if kind == "session":
            sid = msg.get("id")
            if isinstance(sid, str) and sid:
                await self._observe_session_id(sid)
        elif kind == "message_end":
            message = msg.get("message") or {}
            if isinstance(message, dict):
                self._update_usage(message.get("usage") or {})
                if message.get("role") == "assistant":
                    last_text = _extract_text(message)
                    if last_text:
                        self._last_message = last_text[:400]
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        elif kind == "turn_end":
            # `turn_end` carries the same AssistantMessage as the
            # paired `message_end`; usage is already accumulated, so
            # only refresh the last-message preview if missing.
            message = msg.get("message") or {}
            if (
                isinstance(message, dict)
                and message.get("role") == "assistant"
                and not self._last_message
            ):
                last_text = _extract_text(message)
                if last_text:
                    self._last_message = last_text[:400]
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        elif kind == "agent_end":
            return msg
        elif kind == "compaction_start":
            # Pi auto-compacts when the conversation approaches the
            # model's context window (or on `/compact`). Surface as a
            # normalized event so symphony can log it once at INFO
            # — a sudden token drop on the next turn would otherwise
            # be unattributable.
            await self._emit(
                EVENT_COMPACTION,
                {
                    "phase": "start",
                    "reason": msg.get("reason"),
                },
            )
        elif kind == "compaction_end":
            result = msg.get("result") or {}
            payload = {
                "phase": "end",
                "reason": msg.get("reason"),
                "aborted": bool(msg.get("aborted")),
                "will_retry": bool(msg.get("willRetry")),
            }
            if isinstance(result, dict):
                # Best-effort: surface tokensBefore from the
                # CompactionEntry summary if pi includes it.
                for src, dst in (
                    ("tokensBefore", "tokens_before"),
                    ("firstKeptEntryId", "first_kept_entry_id"),
                ):
                    if src in result:
                        payload[dst] = result[src]
            err = msg.get("errorMessage")
            if isinstance(err, str) and err:
                payload["error"] = err
            await self._emit(EVENT_COMPACTION, payload)
        elif kind == "auto_retry_start":
            await self._emit(
                EVENT_AGENT_RETRY,
                {
                    "phase": "start",
                    "attempt": msg.get("attempt"),
                    "max_attempts": msg.get("maxAttempts"),
                    "delay_ms": msg.get("delayMs"),
                    "error": msg.get("errorMessage"),
                },
            )
        elif kind == "auto_retry_end":
            await self._emit(
                EVENT_AGENT_RETRY,
                {
                    "phase": "end",
                    "attempt": msg.get("attempt"),
                    "success": bool(msg.get("success")),
                    "final_error": msg.get("finalError"),
                },
            )
        else:
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        return None

    async def _complete_stream_turn(
        self,
        proc: asyncio.subprocess.Process,
        terminal: dict[str, Any] | None,
        rc: int | None,
    ) -> TurnResult:
        del proc
        if terminal is not None:
            await self._require_resume_confirmation()
            terminal_message = _extract_last_assistant_message(
                terminal.get("messages")
            )
            if terminal_message:
                self._last_message = terminal_message[:400]

            failure_reason = _extract_failure_reason(
                terminal, agent_name=self._agent_name
            )
            if failure_reason is not None:
                stderr_blob = self._stderr_blob()
                if stderr_blob:
                    failure_reason = f"{failure_reason}; stderr: {stderr_blob}"
                payload = {
                    "reason": failure_reason,
                    "stderr_tail": list(self._stderr_tail),
                    **terminal,
                }
                await self._emit(EVENT_TURN_FAILED, payload)
                raise TurnFailed(failure_reason)

            # A clean terminal event is not success if the CLI itself
            # reports a non-zero process status.
            if rc not in (None, 0):
                await self._raise_nonzero_exit(rc)

            await self._emit(EVENT_TURN_COMPLETED, terminal)
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=self._session_id,
                last_message=self._last_message,
            )

        # Graceful exit with assistant output but no explicit agent_end:
        # prime-agent's -p mode sometimes closes stdout before flushing
        # the terminal event. Only rc=0 plus text from this turn qualifies;
        # stale output from a previous turn was cleared in _command_for_turn.
        if rc not in (None, 0):
            await self._raise_nonzero_exit(rc)
        if rc == 0 and self._last_message:
            await self._require_resume_confirmation()
            synthetic = {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": self._last_message}
                        ],
                        "stopReason": "stop",
                    }
                ],
                "message": self._last_message,
            }
            log.info(
                f"{self._agent_name}_agent_end_missing_ok",
                reason="rc=0 with assistant output, no agent_end event",
            )
            await self._emit(EVENT_TURN_COMPLETED, synthetic)
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=self._session_id,
                last_message=self._last_message,
            )

        stderr_blob = self._stderr_blob()
        err_msg = (
            f"{self._agent_name} exited with no agent_end event (rc={rc})"
            + (f"; stderr: {stderr_blob}" if stderr_blob else "")
        )
        await self._emit(
            EVENT_TURN_FAILED,
            {"reason": err_msg, "stderr_tail": list(self._stderr_tail)},
        )
        raise TurnFailed(err_msg)

    async def _raise_nonzero_exit(self, rc: int) -> None:
        """Turn a non-zero CLI status into a backend-specific failure."""
        stderr_blob = self._stderr_blob()
        err_msg = f"{self._agent_name} exited with code {rc}"
        if stderr_blob:
            err_msg += f"; stderr: {stderr_blob}"
        await self._emit(
            EVENT_TURN_FAILED,
            {
                "reason": err_msg,
                "exit_code": rc,
                "stderr": stderr_blob,
                "stderr_tail": list(self._stderr_tail),
            },
        )
        raise TurnFailed(err_msg)

    def _update_usage(self, usage: dict[str, Any]) -> None:
        """Accumulate Pi's per-message Usage into the running totals."""
        if not isinstance(usage, dict):
            return
        in_t = int(usage.get("input") or 0)
        cache_read = int(usage.get("cacheRead") or 0)
        cache_write = int(usage.get("cacheWrite") or 0)
        out_t = int(usage.get("output") or 0)
        # Mirror the Claude backend: cache reads/writes count toward input
        # tokens so the totals stay unit-comparable across backends.
        billed_in = in_t + cache_read + cache_write
        self._latest_usage["input_tokens"] += billed_in
        self._latest_usage["output_tokens"] += out_t
        self._latest_usage["total_tokens"] += billed_in + out_t


def _extract_text(message: dict[str, Any]) -> str:
    """Pull the last text block out of a Pi AssistantMessage."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    return text
    text = message.get("text")
    if isinstance(text, str):
        return text
    return ""


def _extract_last_assistant_message(messages: object) -> str:
    """Return the last textual assistant message from an agent_end list."""
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        text = _extract_text(message)
        if text:
            return text
    return ""


def _extract_failure_reason(
    terminal: dict[str, Any], agent_name: str = "pi"
) -> str | None:
    """Return a non-empty failure reason if the terminal `agent_end` event
    indicates the run ended in error; otherwise None.

    Pi's `agent_end` carries `messages: AssistantMessage[]`; an erroring run
    leaves the final message with `stopReason == "error"` and an
    `errorMessage` field. `aborted` is also treated as a failure so the
    orchestrator can surface it as a turn failure rather than silently
    succeeding.
    """
    messages = terminal.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    if not isinstance(last, dict):
        return None
    stop_reason = last.get("stopReason")
    if stop_reason in ("error", "aborted"):
        err = last.get("errorMessage")
        if isinstance(err, str) and err:
            return err
        return f"{agent_name} turn ended with stopReason={stop_reason!r}"
    return None
