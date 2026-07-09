"""Template-Method skeleton for the per-turn (spawn-per-turn) CLI family.

Initiative C of docs/improvements/architecture-improvement-plan-2026-07-05.md.

Symphony's backends fall into two lifecycle families:

- **per-turn** (this module): one subprocess per worker turn — spawn,
  feed the prompt, collect stdout, reap via ``safe_proc_wait``, emit
  normalized events. plain-CLI (agy/kiro), gemini, and opencode share
  this skeleton; each adapter only supplies the tool-specific steps.
- **persistent app-server** (``codex.py``): one long-running JSON-RPC
  process for the whole session. Deliberately NOT forced into this base.

The skeleton owns the concurrency-sensitive parts (bounded collect,
cancellation reap, closed-flag races) so adapters cannot drift apart on
them; ``tests/test_backend_contract.py`` pins the shared behaviour.
"""

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
    EVENT_TURN_FAILED,
    EVENT_TURN_STARTED,
    BackendInit,
    BaseAgentBackend,
    TurnResult,
)


log = get_logger()

# StreamReader buffer limit for the subprocess pipes; matches codex.py.
MAX_LINE_BYTES = 10 * 1024 * 1024


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _has_shell_flag(command: str, *flags: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    return any(part in flags for part in parts)


class PerTurnCliBackend(BaseAgentBackend):
    """Spawn -> feed prompt -> collect -> reap -> emit, once per turn.

    Subclasses MUST implement:
      - ``_command_for_turn``: build the shell command for one turn.
      - ``_complete_turn``: parse collected stdout, emit ``turn_completed``,
        return the ``TurnResult``.

    Subclasses MAY override:
      - ``_stdin_payload``: text piped to the child's stdin; return ``None``
        when the prompt travels in the command line (stdin -> /dev/null).
      - ``_read_stdout``: collect stdout incrementally when the CLI exposes
        useful streaming frames; returned bytes still feed ``_complete_turn``.
      - ``_start_watchers``: extra per-turn side tasks (e.g. opencode's
        heartbeats); the skeleton cancels them when the turn ends.
      - ``session_id`` property, ``start_session``, ``is_progress_event``.
    """

    def __init__(
        self, init: BackendInit, *, agent_name: str, turn_timeout_ms: int
    ) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._agent_name = agent_name
        self._turn_timeout_ms = turn_timeout_ms
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
    # subclass hooks
    # ------------------------------------------------------------------

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        raise NotImplementedError

    async def _complete_turn(self, stdout_text: str, rc: int) -> TurnResult:
        raise NotImplementedError

    def _stdin_payload(self, prompt: str) -> str | None:
        return prompt

    async def _read_stdout(self, stream: asyncio.StreamReader) -> bytes:
        return await stream.read()

    def _start_watchers(
        self, proc: asyncio.subprocess.Process
    ) -> list["asyncio.Task[None]"]:
        del proc
        return []

    # ------------------------------------------------------------------
    # AgentBackend lifecycle
    # ------------------------------------------------------------------

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        """Bulk-read CLIs emit nothing mid-turn, so no event counts as
        progress — the stall clock runs from turn boundaries, bounded by
        the per-turn timeout. Adapters with mid-turn signals (opencode
        heartbeats) override this."""
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
        command = self._command_for_turn(
            prompt=prompt, is_continuation=is_continuation
        )
        stdin_payload = self._stdin_payload(prompt)
        proc = await self._spawn(command, pipe_stdin=stdin_payload is not None)
        # `stop()` may have flipped `_closed` while we awaited spawn — the
        # process is orphaned because `stop()` only inspects `_active_proc`
        # and we hadn't published yet. Reap and bail.
        if self._closed:
            await self._reap(proc)
            raise ResponseError("backend closed during spawn")
        self._active_proc = proc
        watchers = self._start_watchers(proc)
        try:
            await self._emit(EVENT_TURN_STARTED, {})
            if stdin_payload is not None:
                await self._write_prompt(proc, stdin_payload)
            stdout, stderr, rc = await self._collect(proc)
            self._capture_stderr(stderr or b"")
            if rc != 0:
                await self._fail_turn(rc)
            stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
            return await self._complete_turn(stdout_text, rc)
        except asyncio.CancelledError:
            await self._reap(proc)
            raise
        finally:
            for watcher in watchers:
                watcher.cancel()
            self._active_proc = None

    # ------------------------------------------------------------------
    # skeleton steps
    # ------------------------------------------------------------------

    async def _spawn(
        self, command: str, *, pipe_stdin: bool
    ) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                command,
                cwd=str(self._cwd),
                stdin=asyncio.subprocess.PIPE
                if pipe_stdin
                else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
                limit=MAX_LINE_BYTES,
                # Own process group so terminate/kill reaches the agent CLI
                # behind the bash wrapper (POSIX only).
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
            raise PortExit(
                f"{self._agent_name} stdin closed", error=str(exc)
            ) from exc

    async def _collect(
        self, proc: asyncio.subprocess.Process
    ) -> tuple[bytes, bytes, int]:
        assert proc.stdout is not None and proc.stderr is not None
        stdout_task = asyncio.create_task(self._read_stdout(proc.stdout))
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
        """Best-effort process-group teardown; mirrors `stop()`."""
        await terminate_process_tree(proc)

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            await self._on_event(
                {
                    "event": event,
                    "timestamp": _utc_iso(),
                    "payload": payload
                    if isinstance(payload, dict)
                    else {"data": payload},
                    "usage": dict(self._latest_usage),
                    "rate_limits": None,
                    "agent_pid": self.pid,
                }
            )
        except Exception as exc:
            log.warning("event_callback_failed", error=str(exc))
