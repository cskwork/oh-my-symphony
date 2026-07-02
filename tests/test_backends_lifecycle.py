"""Lifecycle hardening tests for backend subprocess handling."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

import pytest

import symphony._shell as shell_module
import symphony.backends.claude_code as claude_module
from symphony.backends import (
    EVENT_TURN_COMPLETED,
    MALFORMED_LINE_LIMIT,
    POST_STREAM_REAP_TIMEOUT_S,
    BackendInit,
)
from symphony.backends.claude_code import ClaudeCodeBackend
from symphony.backends.codex import CodexAppServerBackend
from symphony.backends.gemini import GeminiBackend
from symphony.backends.pi import PiBackend
from symphony.errors import CodexNotFound, PortExit, TurnFailed
from tests.test_backends import (
    _FakeProcess,
    _FakeStdin,
    _FakeStream,
    _make_cfg,
    _noop_event,
)


class _PipeProcess(_FakeProcess):
    def __init__(
        self,
        *,
        stdout_lines: list[bytes] | None = None,
        stderr_lines: list[bytes] | None = None,
        returncode: int | None = None,
    ) -> None:
        super().__init__()
        self.stdin = _FakeStdin()
        self.stdout = _FakeStream(lines=stdout_lines or [])
        self.stderr = None if stderr_lines is None else _FakeStream(lines=stderr_lines)
        self.returncode = returncode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "backend_cls", "method", "expected_exc"),
    [
        ("codex", CodexAppServerBackend, "start", CodexNotFound),
        ("claude", ClaudeCodeBackend, "run_turn", PortExit),
        ("gemini", GeminiBackend, "run_turn", PortExit),
        ("pi", PiBackend, "run_turn", PortExit),
    ],
)
async def test_backend_spawns_own_process_group_on_posix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    backend_cls: type,
    method: str,
    expected_exc: type[BaseException],
) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        del args
        captured.append(kwargs)
        raise FileNotFoundError("missing bash")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    cfg = _make_cfg(kind, workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = backend_cls(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )

    with pytest.raises(expected_exc):
        if method == "start":
            await backend.start()
        else:
            await backend.run_turn(prompt="hi", is_continuation=False)

    assert captured
    assert captured[0]["start_new_session"] is (os.name == "posix")


@pytest.mark.asyncio
async def test_terminate_process_tree_escalates_sigterm_to_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProcess()
    waits: list[float | None] = []
    signals: list[tuple[int, int]] = []

    async def fake_safe_proc_wait(process, *, timeout=None):
        waits.append(timeout)
        return None if len(waits) == 1 else -signal.SIGKILL

    monkeypatch.setattr(shell_module, "safe_proc_wait", fake_safe_proc_wait)
    monkeypatch.setattr(
        shell_module,
        "_signal_process_group",
        lambda pid, sig: signals.append((pid, sig)) or True,
    )

    rc = await shell_module.terminate_process_tree(proc)

    assert rc == -signal.SIGKILL
    assert signals == [(proc.pid, signal.SIGTERM), (proc.pid, signal.SIGKILL)]
    assert waits == [2.0, 5.0]


@pytest.mark.asyncio
async def test_terminate_process_tree_skips_already_dead_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProcess()
    proc.returncode = 0
    waits: list[object] = []
    signals: list[object] = []
    monkeypatch.setattr(
        shell_module,
        "safe_proc_wait",
        lambda *args, **kwargs: waits.append(args),
    )
    monkeypatch.setattr(
        shell_module,
        "_signal_process_group",
        lambda *args, **kwargs: signals.append(args),
    )

    rc = await shell_module.terminate_process_tree(proc)

    assert rc == 0
    assert waits == []
    assert signals == []


@pytest.mark.asyncio
async def test_codex_stdout_eof_fails_completion_waiter_promptly(tmp_path: Path) -> None:
    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    backend._process = _PipeProcess(  # type: ignore[assignment]
        stdout_lines=[b""],
        returncode=1,
    )
    waiter = backend._arm_completion_waiter()

    await backend._stdout_reader()

    with pytest.raises(TurnFailed, match="closed stdout"):
        await waiter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "backend_cls", "valid_line"),
    [
        ("claude", ClaudeCodeBackend, b'{"type":"assistant","message":{}}\n'),
        ("pi", PiBackend, b'{"type":"message_start"}\n'),
    ],
)
async def test_stream_malformed_streak_sets_corrupt_after_limit(
    tmp_path: Path, kind: str, backend_cls: type, valid_line: bytes
) -> None:
    del valid_line
    cfg = _make_cfg(kind, workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = backend_cls(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    proc = _PipeProcess(stdout_lines=[b"{bad json\n"] * MALFORMED_LINE_LIMIT)

    await backend._consume_stream(proc)  # type: ignore[attr-defined]

    assert backend._stream_corrupt is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "backend_cls", "valid_line"),
    [
        ("claude", ClaudeCodeBackend, b'{"type":"assistant","message":{}}\n'),
        ("pi", PiBackend, b'{"type":"message_start"}\n'),
    ],
)
async def test_stream_malformed_streak_resets_after_valid_line(
    tmp_path: Path, kind: str, backend_cls: type, valid_line: bytes
) -> None:
    cfg = _make_cfg(kind, workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = backend_cls(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )
    proc = _PipeProcess(
        stdout_lines=([b"{bad json\n"] * (MALFORMED_LINE_LIMIT - 1))
        + [valid_line]
        + ([b"{still bad\n"] * (MALFORMED_LINE_LIMIT - 1))
    )

    await backend._consume_stream(proc)  # type: ignore[attr-defined]

    assert backend._stream_corrupt is None


@pytest.mark.asyncio
async def test_claude_bounded_post_stream_reap_terminates_lingering_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[dict] = []

    def on_event(event: dict) -> "asyncio.Future[None]":
        events.append(event)
        fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()
        fut.set_result(None)
        return fut

    proc = _PipeProcess(
        stdout_lines=[
            (
                b'{"type":"result","subtype":"success","result":"ok",'
                b'"session_id":"s1","usage":{}}\n'
            )
        ],
        stderr_lines=[],
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return proc

    waits: list[float | None] = []
    terminated: list[int] = []

    async def fake_safe_proc_wait(process, *, timeout=None):
        waits.append(timeout)
        return None

    async def fake_terminate_process_tree(process):
        terminated.append(process.pid)
        process.returncode = 0
        return 0

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(claude_module, "safe_proc_wait", fake_safe_proc_wait)
    monkeypatch.setattr(
        claude_module,
        "terminate_process_tree",
        fake_terminate_process_tree,
    )

    cfg = _make_cfg("claude", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = ClaudeCodeBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=on_event)
    )

    result = await backend.run_turn(prompt="hi", is_continuation=False)

    assert result.status == EVENT_TURN_COMPLETED
    assert waits == [POST_STREAM_REAP_TIMEOUT_S]
    assert terminated == [proc.pid]
