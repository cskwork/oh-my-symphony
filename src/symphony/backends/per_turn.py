"""Template-Method skeleton for the per-turn (spawn-per-turn) CLI family.

Initiative C of docs/improvements/architecture-improvement-plan-2026-07-05.md.

Symphony's backends fall into two lifecycle families:

- **per-turn** (this module): one subprocess per worker turn — spawn,
  feed the prompt, collect stdout, reap via ``safe_proc_wait``, emit
  normalized events. plain-CLI (agy/kiro), gemini, and opencode share
  this skeleton; each adapter only supplies the tool-specific steps.
  claude and the pi family are the *streaming* members of this family
  (``JsonlStreamBackend``): same spawn/reap/emit skeleton, but stdout is
  parsed one JSON line at a time while the child runs.
- **persistent app-server** (``codex.py``): one long-running JSON-RPC
  process for the whole session. Deliberately NOT forced into this base.

The skeleton owns the concurrency-sensitive parts (bounded collect,
cancellation reap, closed-flag races) so adapters cannot drift apart on
them; ``tests/test_backend_contract.py`` pins the shared behaviour.
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

from .._shell import resolve_bash, safe_proc_wait, terminate_process_tree
from ..errors import PortExit, ResponseError, TurnFailed, TurnTimeout
from ..logging import get_logger
from ..utils.git_sandbox import git_roots_env
from ..workspace import validate_agent_cwd
from . import (
    EVENT_MALFORMED,
    EVENT_SESSION_STARTED,
    EVENT_TURN_FAILED,
    EVENT_TURN_STARTED,
    MALFORMED_LINE_LIMIT,
    POST_STREAM_REAP_TIMEOUT_S,
    BackendInit,
    BaseAgentBackend,
    EventCallback,
    TurnResult,
    _is_valid_session_id,
    redact_session_id,
)


log = get_logger()

# StreamReader line-buffer limit for the subprocess pipes; shared by the
# per-turn family, claude, pi, and codex. The asyncio default of 64 KiB
# overflows on stream-json / JSON-mode events whose `result` text,
# `message_update`, or tool-result payload exceeds that on a single line,
# raising `LimitOverrunError: Separator is found, but chunk is longer
# than limit` and dropping the rest of the stream. Codex upstream §10.1
# caps lines at 10 MB; matches the former per-file copies.
MAX_LINE_BYTES = 10 * 1024 * 1024

# Placeholder ``start_session`` answer for CLIs that mint the session id
# inside their first output stream (claude, pi family); the real id arrives
# mid-stream and triggers ``session_started``.
PENDING_SESSION_ID = "pending"


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stderr_tail_blob(tail: "deque[str]") -> str:
    """Compact stderr tail for failure messages (≤400 chars, keeps the end)."""
    if not tail:
        return ""
    joined = " | ".join(tail)
    return joined if len(joined) <= 400 else joined[-400:]


async def _reap_process(proc: asyncio.subprocess.Process) -> int | None:
    """Tear down a process group or surface ambiguous cleanup.

    Returns the exit status ``terminate_process_tree`` observed (``None``
    when the transport already recorded it on ``proc.returncode``).
    """
    result = await terminate_process_tree(proc)
    if result is None and proc.returncode is None:
        raise RuntimeError("backend process cleanup could not be confirmed")
    return result


async def _emit_event(
    on_event: EventCallback,
    event: str,
    payload: dict[str, Any],
    *,
    usage: dict[str, int],
    rate_limits: dict[str, Any] | None = None,
    agent_pid: int | None = None,
    redact_session: str | None = None,
) -> None:
    """Deliver the normalized backend-event envelope; never raises.

    The per-turn family, claude, pi, and codex backends each carried a
    near-identical copy of this; the envelope shape (event / timestamp /
    payload / usage / rate_limits / agent_pid, plus the swallow-exception
    contract) is pinned here once. ``redact_session=None`` disables
    session redaction — an identity in ``redact_session_id`` — which is
    how the codex backend (no session redaction) uses it.
    """
    ev_payload = redact_session_id(
        payload if isinstance(payload, dict) else {"data": payload},
        redact_session,
    )
    try:
        await on_event(
            {
                "event": event,
                "timestamp": _utc_iso(),
                "payload": ev_payload,
                "usage": dict(usage),
                "rate_limits": dict(rate_limits) if rate_limits else None,
                "agent_pid": agent_pid,
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
      - ``_drive_turn``: everything after the prompt is written — collect,
        gate on exit status, complete. ``JsonlStreamBackend`` swaps in a
        live line parser here; bulk-read adapters keep the default.
      - ``_git_roots_env``: the git-dir grant placed in the child's env.
      - ``session_id`` property, ``start_session``, ``is_progress_event``.
    """

    def __init__(
        self, init: BackendInit, *, agent_name: str, turn_timeout_ms: int
    ) -> None:
        validate_agent_cwd(init.cwd, init.workspace_root)
        self._agent_name = agent_name
        self._turn_timeout_ms = turn_timeout_ms
        self._cwd = init.cwd
        self._extra_env = dict(init.env)
        self._on_event = init.on_event
        self._on_process_started = init.on_process_started
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
        if self._on_process_started is not None:
            self._on_process_started(proc.pid)
        self._active_proc = proc
        # `stop()` may have flipped `_closed` while we awaited spawn — the
        # process is orphaned because `stop()` only inspects `_active_proc`
        # and we hadn't published yet. Reap and bail.
        if self._closed:
            await self._reap(proc)
            self._active_proc = None
            raise ResponseError("backend closed during spawn")
        watchers = self._start_watchers(proc)
        try:
            await self._emit(EVENT_TURN_STARTED, {})
            if stdin_payload is not None:
                await self._write_prompt(proc, stdin_payload)
            return await self._drive_turn(proc)
        except asyncio.CancelledError:
            await self._reap(proc)
            raise
        finally:
            for watcher in watchers:
                watcher.cancel()
            if proc.returncode is not None:
                self._active_proc = None

    async def _drive_turn(self, proc: asyncio.subprocess.Process) -> TurnResult:
        """Collect the child's output and turn it into a ``TurnResult``.

        Bulk-read adapters keep this default: gather stdout/stderr/exit
        under the turn timeout, fail on a non-zero exit or empty stdout,
        then hand the text to ``_complete_turn``.
        """
        stdout, stderr, rc = await self._collect(proc)
        self._capture_stderr(stderr or b"")
        if rc != 0:
            await self._fail_turn(rc)
        stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
        if not stdout_text:
            reason = f"{self._agent_name} exited successfully with empty stdout"
            await self._emit(
                EVENT_TURN_FAILED,
                {
                    "reason": reason,
                    "exit_code": rc,
                    "stderr_tail": list(self._stderr_tail),
                },
            )
            raise TurnFailed(reason)
        return await self._complete_turn(stdout_text, rc)

    # ------------------------------------------------------------------
    # skeleton steps
    # ------------------------------------------------------------------

    def _git_roots_env(self) -> dict[str, str]:
        """Env fragment granting the git dirs a sandbox scoped to ``cwd``
        would miss. Claude widens the scan to the workspace root."""
        return git_roots_env(self._cwd)

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
                env={**os.environ, **self._git_roots_env(), **self._extra_env},
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
        session_id = getattr(self, "_opencode_session_id", None)
        text = redact_session_id(text, session_id)
        for line in text.splitlines():
            if line:
                self._stderr_tail.append(line)

    def _stderr_blob(self) -> str:
        return _stderr_tail_blob(self._stderr_tail)

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        """Tear down a process group or surface ambiguous cleanup."""
        await _reap_process(proc)

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await _emit_event(
            self._on_event,
            event,
            payload,
            usage=self._latest_usage,
            agent_pid=self.pid,
            redact_session=None
            if event == EVENT_SESSION_STARTED
            else getattr(self, "_opencode_session_id", None),
        )


class JsonlStreamBackend(PerTurnCliBackend):
    """Per-turn CLI whose stdout is one JSON event per line, parsed live.

    claude (``--output-format stream-json``) and the pi family
    (``--mode json``) share this shape: the CLI mints the session id inside
    the stream, emits intermediate frames worth forwarding as they arrive,
    and ends with one terminal event. Two things differ from the bulk-read
    skeleton and are pinned by ``tests/test_backends_lifecycle.py``:

    - the turn timeout bounds only the stream; once stdout closes the child
      gets a bounded reap (``POST_STREAM_REAP_TIMEOUT_S``) so a lingering
      grandchild cannot hang the turn on an untimed wait;
    - the exit status is interpreted by the adapter together with the
      terminal event instead of failing the turn outright.

    Subclasses MUST implement:
      - ``_command_for_turn`` (append ``_resume_args`` for the session flag),
      - ``_handle_stream_event``: consume one decoded frame; return it when
        it is the terminal event, ``None`` otherwise,
      - ``_complete_stream_turn``: interpret terminal event + exit status and
        emit ``turn_completed`` / ``turn_failed``.

    Class attributes:
      - ``_resume_flag``: CLI flag that re-enters a captured session.
      - ``_stderr_settle_s``: seconds to let stderr drain after stdout
        closes; ``0`` cancels the drain task immediately.
    """

    _resume_flag = "--resume"
    _stderr_settle_s: float = 0.0

    def __init__(
        self, init: BackendInit, *, agent_name: str, turn_timeout_ms: int
    ) -> None:
        super().__init__(init, agent_name=agent_name, turn_timeout_ms=turn_timeout_ms)
        self._resume_on_next_turn = False
        self._expected_resume_session_id: str | None = None
        self._resume_session_confirmed = False
        self._last_message: str = ""
        # Last bad line when MALFORMED_LINE_LIMIT consecutive lines failed
        # to parse; _drive_turn turns it into a precise TurnFailed.
        self._stream_corrupt: str | None = None

    # ------------------------------------------------------------------
    # subclass hooks
    # ------------------------------------------------------------------

    async def _handle_stream_event(
        self, msg: dict[str, Any]
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    async def _complete_stream_turn(
        self,
        proc: asyncio.subprocess.Process,
        terminal: dict[str, Any] | None,
        rc: int | None,
    ) -> TurnResult:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # AgentBackend lifecycle
    # ------------------------------------------------------------------

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        # The CLI mints the session id inside the first stream (claude: the
        # `system/init` event; pi: the `session` header line). Return a
        # placeholder; the real id reaches `_observe_session_id` mid-stream
        # and triggers `session_started`.
        del initial_prompt, issue_title
        return PENDING_SESSION_ID

    async def resume_session(self, session_id: str) -> bool:
        """Select an exact prior session for the next per-turn process."""
        if self._closed or not _is_valid_session_id(session_id):
            return False
        self._session_id = session_id
        self._expected_resume_session_id = session_id
        self._resume_session_confirmed = False
        self._resume_on_next_turn = True
        return True

    def _resume_args(self, *, is_continuation: bool, resume_across_turns: bool) -> str:
        """`` <flag> <id>`` when this turn re-enters the captured session."""
        should_resume = self._resume_on_next_turn or (
            is_continuation and resume_across_turns
        )
        self._resume_on_next_turn = False
        sid = self._session_id
        if should_resume and sid and sid != PENDING_SESSION_ID:
            return f" {self._resume_flag} {shlex.quote(sid)}"
        return ""

    async def _drive_turn(self, proc: asyncio.subprocess.Process) -> TurnResult:
        try:
            terminal = await asyncio.wait_for(
                self._consume_stream(proc), timeout=self._turn_timeout_ms / 1000.0
            )
        except asyncio.TimeoutError as exc:
            await self._reap(proc)
            await self._emit(EVENT_TURN_FAILED, {"reason": "turn_timeout"})
            raise TurnTimeout(f"{self._agent_name} turn timed out") from exc

        rc = await self._reap_after_stream(proc)
        if self._stderr_settle_s > 0:
            # The drain task stops with stdout; collect any stderr lines
            # written just before process exit without hanging on a child
            # that leaves stderr open.
            try:
                await asyncio.wait_for(
                    self._drain_stderr(proc), timeout=self._stderr_settle_s
                )
            except asyncio.TimeoutError:
                pass
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
        return await self._complete_stream_turn(proc, terminal, rc)

    async def _reap_after_stream(
        self, proc: asyncio.subprocess.Process
    ) -> int | None:
        """Bounded reap once stdout closed; never an untimed wait."""
        rc = await safe_proc_wait(proc, timeout=POST_STREAM_REAP_TIMEOUT_S)
        if rc is None and proc.returncode is None:
            # stdout closed but the process lingers — reap the tree
            # instead of hanging the turn on an unbounded wait.
            rc = await _reap_process(proc)
        return rc if rc is not None else proc.returncode

    # ------------------------------------------------------------------
    # JSONL parsing
    # ------------------------------------------------------------------

    async def _consume_stream(
        self, proc: asyncio.subprocess.Process
    ) -> dict[str, Any] | None:
        """Read JSON lines; return the terminal event or None."""
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
                event = await self._handle_stream_event(msg)
                if event is not None:
                    terminal = event
        finally:
            await self._settle_stderr(stderr_task)
        return terminal

    async def _settle_stderr(self, stderr_task: "asyncio.Task[None]") -> None:
        if self._stderr_settle_s > 0:
            # Give a closed stdout a brief chance to flush stderr
            # diagnostics; cancelling immediately can lose the auth/network
            # error that explains a non-zero exit. Do not wait indefinitely
            # if a child keeps stderr open after stdout closes.
            try:
                await asyncio.wait_for(stderr_task, timeout=self._stderr_settle_s)
                return
            except asyncio.TimeoutError:
                pass
            except (asyncio.CancelledError, Exception):
                return
        stderr_task.cancel()
        try:
            await stderr_task
        except (asyncio.CancelledError, Exception):
            pass

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

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await _emit_event(
            self._on_event,
            event,
            payload,
            usage=self._latest_usage,
            rate_limits=self.latest_rate_limits,
            agent_pid=self.pid,
            redact_session=None if event == EVENT_SESSION_STARTED else self._session_id,
        )
