"""Shared one-shot plain-stdout CLI backend helpers."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
import uuid
from collections import deque
from typing import Any

from .._shell import resolve_bash, safe_proc_wait, terminate_process_tree
from ..errors import PortExit, ResponseError, TurnFailed, TurnTimeout
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
MAX_LINE_BYTES = 10 * 1024 * 1024


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class PlainCliBackend(BaseAgentBackend):
    """One subprocess per turn; treats stdout as the final assistant message."""

    def __init__(
        self,
        init: BackendInit,
        *,
        agent_name: str,
        command: str,
        turn_timeout_ms: int,
        resume_across_turns: bool,
        unattended_flags: tuple[str, ...] = (),
        continuation_flag: str | None = None,
    ) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._agent_name = agent_name
        self._command = command
        self._turn_timeout_ms = turn_timeout_ms
        self._resume_across_turns = resume_across_turns
        self._unattended_flags = unattended_flags
        self._continuation_flag = continuation_flag
        self._cwd = init.cwd
        self._on_event = init.on_event
        self._session_id: str | None = None
        self._closed = False
        self._active_proc: asyncio.subprocess.Process | None = None
        self._latest_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._stderr_tail: deque[str] = deque(maxlen=20)

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        del event
        return False

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
        return {"agent": self._agent_name}

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
        proc = await self._spawn(is_continuation=is_continuation)
        if self._closed:
            await self._reap(proc)
            raise ResponseError("backend closed during spawn")
        self._active_proc = proc
        try:
            await self._write_prompt(proc, prompt)
            stdout, stderr, rc = await self._collect(proc)
            self._capture_stderr(stderr or b"")
            if rc != 0:
                await self._fail_turn(rc)
            last_message = (stdout or b"").decode("utf-8", errors="replace").strip()
            await self._emit(
                EVENT_TURN_COMPLETED,
                {
                    "result": last_message,
                    "response": last_message,
                    "session_id": self._session_id,
                    "stats": {},
                    "exit_code": rc,
                },
            )
            return TurnResult(
                status=EVENT_TURN_COMPLETED,
                turn_id=self._session_id,
                last_message=last_message[:400],
            )
        except asyncio.CancelledError:
            await self._reap(proc)
            raise
        finally:
            self._active_proc = None

    async def _spawn(
        self, *, is_continuation: bool
    ) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                self._command_for_turn(is_continuation=is_continuation),
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
                limit=MAX_LINE_BYTES,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as exc:
            raise PortExit("bash not available", error=str(exc)) from exc

    async def _write_prompt(
        self, proc: asyncio.subprocess.Process, prompt: str
    ) -> None:
        assert proc.stdin is not None
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise PortExit(f"{self._agent_name} stdin closed", error=str(exc)) from exc

    async def _collect(
        self, proc: asyncio.subprocess.Process
    ) -> tuple[bytes, bytes, int]:
        assert proc.stdout is not None and proc.stderr is not None
        stdout_task = asyncio.create_task(proc.stdout.read())
        stderr_task = asyncio.create_task(proc.stderr.read())
        try:
            stdout, stderr, safe_rc = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, safe_proc_wait(proc)),
                timeout=self._turn_timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError as exc:
            stdout_task.cancel()
            stderr_task.cancel()
            await self._reap(proc)
            await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
            raise TurnTimeout(f"{self._agent_name} turn timed out") from exc
        rc = safe_rc if safe_rc is not None else (proc.returncode or 0)
        return stdout, stderr, rc

    async def _fail_turn(self, rc: int) -> None:
        err_msg = self._stderr_blob()
        payload = {
            "reason": f"{self._agent_name} exit {rc}"
            + (f"; stderr: {err_msg}" if err_msg else ""),
            "stderr_tail": list(self._stderr_tail),
            "stderr": err_msg,
        }
        await self._emit(EVENT_TURN_FAILED, payload)
        raise TurnFailed(err_msg or f"{self._agent_name} failed with exit {rc}")

    def _command_for_turn(self, *, is_continuation: bool) -> str:
        cmd = self._command
        flags = list(self._unattended_flags)
        if (
            is_continuation
            and self._resume_across_turns
            and self._continuation_flag is not None
        ):
            flags.append(self._continuation_flag)
        for flag in flags:
            if not _has_shell_flag(cmd, flag):
                cmd = f"{cmd} {flag}"
        return cmd

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


def _has_shell_flag(command: str, *flags: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return any(part in flags for part in parts)
