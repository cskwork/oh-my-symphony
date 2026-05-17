"""Gemini CLI backend.

Drives `gemini -p "" --output-format json` once per turn. Symphony mints a
session UUID at `start_session`; turn 1 passes it via `--session-id`, and
same-state continuation turns resume it via `--resume`. The orchestrator
rebuilds backends on phase transitions, so Explore → Plan and other state
changes naturally get a fresh Gemini session.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
import uuid
from collections import deque
from typing import Any

from .._shell import resolve_bash, safe_proc_wait
from ..errors import (
    PortExit,
    ResponseError,
    TurnFailed,
    TurnTimeout,
)
from ..logging import get_logger
from ..workspace import validate_agent_cwd
from . import (
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    BaseAgentBackend,
    TurnResult,
)


log = get_logger()

# StreamReader line-buffer limit for the subprocess pipes. Gemini currently
# reads stdout with `.read()` (no line limit applies) but we set this for
# consistency with the other backends and as a safety net for future
# refactors. Matches codex.py.
MAX_LINE_BYTES = 10 * 1024 * 1024


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class GeminiBackend(BaseAgentBackend):
    """One subprocess per turn; parses Gemini JSON output."""

    def __init__(self, init: BackendInit) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._gemini = init.cfg.gemini
        self._cwd = init.cwd
        self._on_event = init.on_event
        self._session_id: str | None = None
        self._closed = False
        self._active_proc: asyncio.subprocess.Process | None = None
        self._latest_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._stderr_tail: deque[str] = deque(maxlen=20)

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
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            rc = await safe_proc_wait(proc, timeout=2.0)
            if rc is None and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await safe_proc_wait(proc)

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
        return None

    async def initialize(self) -> dict[str, Any]:
        return {"agent": "gemini"}

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

        try:
            proc = await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                self._command_for_turn(is_continuation=is_continuation),
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
                limit=MAX_LINE_BYTES,
            )
        except FileNotFoundError as exc:
            raise PortExit("bash not available", error=str(exc)) from exc

        # `stop()` may have flipped `_closed` while we awaited spawn — reap
        # the orphaned process and bail.
        if self._closed:
            await self._reap(proc)
            raise ResponseError("backend closed during spawn")
        self._active_proc = proc
        try:
            assert proc.stdin is not None and proc.stdout is not None
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise PortExit("gemini stdin closed", error=str(exc)) from exc

            timeout_s = self._gemini.turn_timeout_ms / 1000.0
            assert proc.stdout is not None and proc.stderr is not None
            stdout_task = asyncio.create_task(proc.stdout.read())
            stderr_task = asyncio.create_task(proc.stderr.read())
            try:
                stdout, stderr, safe_rc = await asyncio.wait_for(
                    asyncio.gather(
                        stdout_task,
                        stderr_task,
                        safe_proc_wait(proc),
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError as exc:
                stdout_task.cancel()
                stderr_task.cancel()
                await self._reap(proc)
                await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
                raise TurnTimeout("gemini turn timed out") from exc

            rc = safe_rc if safe_rc is not None else (proc.returncode or 0)
            self._capture_stderr(stderr or b"")
            if rc != 0:
                err_msg = self._stderr_blob()
                # Standardize on `stderr_tail` (list[str]) so orchestrator /
                # operator grep handles every backend the same way; keep the
                # legacy `stderr` key for back-compat with anything that read
                # the previous shape.
                payload = {
                    "reason": f"gemini exit {rc}" + (f"; stderr: {err_msg}" if err_msg else ""),
                    "stderr_tail": list(self._stderr_tail),
                    "stderr": err_msg,
                }
                await self._emit(EVENT_TURN_FAILED, payload)
                raise TurnFailed(err_msg or f"gemini failed with exit {rc}")

            result_text = (stdout or b"").decode("utf-8", errors="replace").strip()
            try:
                parsed = json.loads(result_text)
            except json.JSONDecodeError as exc:
                payload = {
                    "reason": "gemini emitted malformed JSON",
                    "stdout": result_text[:400],
                    "stderr_tail": list(self._stderr_tail),
                }
                await self._emit(EVENT_TURN_FAILED, payload)
                raise TurnFailed("gemini emitted malformed JSON") from exc
            if not isinstance(parsed, dict):
                payload = {
                    "reason": "gemini JSON output was not an object",
                    "stdout": result_text[:400],
                    "stderr_tail": list(self._stderr_tail),
                }
                await self._emit(EVENT_TURN_FAILED, payload)
                raise TurnFailed("gemini JSON output was not an object")

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
            last_message = response if isinstance(response, str) else ""
            self._update_usage_from_stats(parsed.get("stats"))
            payload = {
                "result": last_message,
                "response": last_message,
                "session_id": self._session_id,
                "stats": parsed.get("stats") if isinstance(parsed.get("stats"), dict) else {},
                "exit_code": rc,
            }
            await self._emit(EVENT_TURN_COMPLETED, payload)
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=self._session_id,
                last_message=last_message[:400],
            )
        finally:
            self._active_proc = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _command_for_turn(self, *, is_continuation: bool) -> str:
        cmd = f"{self._gemini.command} --skip-trust --output-format json"
        if not self._session_id:
            return cmd
        if is_continuation:
            if self._gemini.resume_across_turns:
                return f"{cmd} --resume {shlex.quote(self._session_id)}"
            return cmd
        return f"{cmd} --session-id {shlex.quote(self._session_id)}"

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

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        """Best-effort terminate→wait→kill ladder; mirrors `stop()`."""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        rc = await safe_proc_wait(proc, timeout=2.0)
        if rc is None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await safe_proc_wait(proc)

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
