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
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from collections import deque
from typing import Any

from .._shell import resolve_bash, safe_proc_wait, terminate_process_tree
from ..errors import (
    PortExit,
    ResponseError,
    TurnFailed,
    TurnTimeout,
)
from ..logging import get_logger
from ..utils.git_sandbox import git_roots_env
from ..workspace import validate_agent_cwd
from . import (
    EVENT_AGENT_RETRY,
    EVENT_COMPACTION,
    EVENT_MALFORMED,
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    EVENT_TURN_STARTED,
    MALFORMED_LINE_LIMIT,
    POST_STREAM_REAP_TIMEOUT_S,
    BackendInit,
    BaseAgentBackend,
    TurnResult,
    _is_valid_session_id,
    redact_session_id,
)
from .per_turn import (
    MAX_LINE_BYTES,
    _emit_event,
    _reap_process,
    _stderr_tail_blob,
)


log = get_logger()

PENDING_SESSION_ID = "pending"

# Model-output lifecycle events. Everything else on the stream (session
# header, tool_execution_* echoes, keepalives) must not reset the
# orchestrator's stall clock — mirrors the claude backend's
# assistant-frames-only predicate.
_PROGRESS_EVENT_TYPES = frozenset(
    {"message_start", "message_update", "message_end", "turn_start", "turn_end"}
)


class PiBackend(BaseAgentBackend):
    """One subprocess per turn; speaks pi --mode json JSONL."""

    _agent_name = "pi"

    # Subclass override point: the CLI flag used for session resume.
    # Pi uses ``--session <id>``; PrimeAgent uses ``--resume <id>``.
    _resume_flag = "--session"

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        return event.get("type") in _PROGRESS_EVENT_TYPES

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._pi = init.cfg.pi
        self._cwd = init.cwd
        self._on_event = init.on_event
        self._on_process_started = init.on_process_started
        self._session_id: str | None = None
        self._resume_on_next_turn = False
        self._expected_resume_session_id: str | None = None
        self._resume_session_confirmed = False
        self._closed = False
        self._active_proc: asyncio.subprocess.Process | None = None
        self._latest_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._last_message: str = ""
        # Bounded ring buffer of stderr lines so a TurnFailed exception can
        # carry the actual reason (auth error, network, ratelimit, ...) up to
        # the orchestrator instead of the opaque "no agent_end event" string.
        self._stderr_tail: deque[str] = deque(maxlen=20)
        # Last bad line when MALFORMED_LINE_LIMIT consecutive lines failed
        # to parse; run_turn turns it into a precise TurnFailed.
        self._stream_corrupt: str | None = None

    # ------------------------------------------------------------------
    # AgentBackend lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._active_proc
        if proc is not None and proc.returncode is None:
            result = await terminate_process_tree(proc)
            if result is None and proc.returncode is None:
                raise RuntimeError("backend process cleanup could not be confirmed")

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def pid(self) -> int | None:
        return self._active_proc.pid if self._active_proc is not None else None

    @property
    def latest_usage(self) -> dict[str, int]:
        return dict(self._latest_usage)

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        # Pi does not surface rate-limit telemetry in the JSON stream.
        return None

    async def initialize(self) -> dict[str, Any]:
        return {"agent": self._agent_name}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        # Pi mints a session id when the first `pi --mode json` invocation
        # writes the `session` header line. Return a placeholder; the real id
        # arrives in `_consume_stream` and triggers `session_started`.
        del initial_prompt, issue_title
        return PENDING_SESSION_ID

    async def resume_session(self, session_id: str) -> bool:
        """Select an exact Pi-family session for the next CLI process."""
        if self._closed or not _is_valid_session_id(session_id):
            return False
        self._session_id = session_id
        self._expected_resume_session_id = session_id
        self._resume_session_confirmed = False
        self._resume_on_next_turn = True
        return True

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        if self._closed:
            raise ResponseError("backend is closed")

        # Both the assistant preview and stderr diagnostics belong to this
        # subprocess. Clear them before every turn so a missing terminal event
        # cannot make a later empty turn look successful (or repeat stale
        # diagnostics from an earlier process).
        self._last_message = ""
        self._stderr_tail.clear()

        cmd = self._pi.command
        if self._session_id and self._session_id != PENDING_SESSION_ID and (
            self._resume_on_next_turn
            or (is_continuation and self._pi.resume_across_turns)
        ):
            cmd = f"{cmd} {self._resume_flag} {shlex.quote(self._session_id)}"

        try:
            proc = await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                cmd,
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **git_roots_env(self._cwd)},
                limit=MAX_LINE_BYTES,
                # Own process group so terminate/kill reaches the agent CLI
                # behind the bash wrapper (POSIX only).
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as exc:
            raise PortExit("bash not available", error=str(exc)) from exc
        self._resume_on_next_turn = False

        if self._on_process_started is not None:
            self._on_process_started(proc.pid)
        self._active_proc = proc
        # `stop()` may have flipped `_closed` while we awaited spawn — reap
        # the orphaned process and bail.
        if self._closed:
            await self._reap(proc)
            self._active_proc = None
            raise ResponseError("backend closed during spawn")
        try:
            await self._emit(EVENT_TURN_STARTED, {})
            assert proc.stdin is not None and proc.stdout is not None
            try:
                # Pi documents stdin as appended to the `-p` argument. Since
                # the default command is `pi --mode json -p ""`, the prompt
                # arrives entirely through stdin.
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise PortExit(
                    f"{self._agent_name} stdin closed", error=str(exc)
                ) from exc

            timeout_s = self._pi.turn_timeout_ms / 1000.0
            try:
                terminal = await asyncio.wait_for(
                    self._consume_stream(proc), timeout=timeout_s
                )
            except asyncio.TimeoutError as exc:
                await self._reap(proc)
                await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
                raise TurnTimeout(f"{self._agent_name} turn timed out") from exc

            safe_rc = await safe_proc_wait(proc, timeout=POST_STREAM_REAP_TIMEOUT_S)
            if safe_rc is None and proc.returncode is None:
                # stdout closed but the process lingers — reap the tree
                # instead of hanging the turn on an unbounded wait.
                safe_rc = await terminate_process_tree(proc)
                if safe_rc is None and proc.returncode is None:
                    raise RuntimeError(
                        "backend process cleanup could not be confirmed"
                    )
            # The stream reader is cancelled when stdout closes, so collect
            # any stderr lines written just before process exit after the
            # bounded reap. This keeps process-level diagnostics actionable,
            # without hanging on a child that leaves stderr open.
            try:
                await asyncio.wait_for(self._drain_stderr(proc), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            rc = safe_rc if safe_rc is not None else proc.returncode
            if self._stream_corrupt is not None:
                err_msg = (
                    f"{self._agent_name} stream unreadable: "
                    f"{MALFORMED_LINE_LIMIT} consecutive "
                    f"malformed lines (last: {self._stream_corrupt[:200]!r})"
                )
                await self._emit(
                    EVENT_TURN_FAILED,
                    {"reason": err_msg, "stderr_tail": list(self._stderr_tail)},
                )
                raise TurnFailed(err_msg)

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
            # stale output from a previous turn was cleared above.
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
        except asyncio.CancelledError:
            await self._reap(proc)
            raise
        finally:
            if proc.returncode is not None:
                self._active_proc = None

    # ------------------------------------------------------------------
    # JSONL parsing
    # ------------------------------------------------------------------

    async def _consume_stream(
        self, proc: asyncio.subprocess.Process
    ) -> dict[str, Any] | None:
        """Read JSONL events; return the terminal `agent_end` event or None."""
        assert proc.stdout is not None
        terminal: dict[str, Any] | None = None
        self._stream_corrupt = None
        malformed_streak = 0
        stderr_task = asyncio.create_task(self._drain_stderr(proc))
        try:
            while True:
                try:
                    line = await proc.stdout.readline()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error(
                        f"{self._agent_name}_stdout_read_error", error=str(exc)
                    )
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
                    continue
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
                    terminal = msg
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
        finally:
            # Give a closed stdout a brief chance to flush stderr diagnostics;
            # cancelling immediately can lose the auth/network error that
            # explains a non-zero exit. Do not wait indefinitely if a child
            # keeps stderr open after stdout closes.
            try:
                await asyncio.wait_for(stderr_task, timeout=0.1)
            except asyncio.TimeoutError:
                stderr_task.cancel()
                try:
                    await stderr_task
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass
        return terminal

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        while True:
            try:
                line = await proc.stderr.readline()
            except (asyncio.CancelledError, Exception):
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            text = redact_session_id(text, self._session_id)
            if text:
                self._stderr_tail.append(text)
            log.debug(f"{self._agent_name}_stderr", line=text)

    def _stderr_blob(self) -> str:
        """Compact stderr tail for inclusion in failure messages (≤400 chars)."""
        return _stderr_tail_blob(self._stderr_tail)

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

    async def _require_resume_confirmation(self) -> None:
        if self._expected_resume_session_id is None:
            return
        if not self._resume_session_confirmed:
            reason = (
                f"{self._agent_name} did not confirm the requested recovered session"
            )
            await self._emit(EVENT_TURN_FAILED, {"reason": reason})
            raise TurnFailed(reason)
        self._expected_resume_session_id = None
        self._resume_session_confirmed = False

    async def _observe_session_id(self, session_id: str) -> None:
        expected = self._expected_resume_session_id
        if expected is not None:
            if session_id != expected:
                reason = f"{self._agent_name} returned a different recovered session"
                await self._emit(EVENT_TURN_FAILED, {"reason": reason})
                raise TurnFailed(reason)
            self._resume_session_confirmed = True
        if session_id != self._session_id:
            self._session_id = session_id
            await self._emit(
                EVENT_SESSION_STARTED,
                {"session_id": session_id, "thread_id": session_id},
            )

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        """Tear down a process group or surface ambiguous cleanup."""
        await _reap_process(proc)

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

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await _emit_event(
            self._on_event,
            event,
            payload,
            usage=self._latest_usage,
            agent_pid=self.pid,
            redact_session=None if event == EVENT_SESSION_STARTED else self._session_id,
        )


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
