"""Lifecycle hardening tests for backend subprocess handling."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import symphony._shell as shell_module
import symphony.backends.claude_code as claude_module
import symphony.backends.per_turn as per_turn_module
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
    taskkills: list[int] = []

    async def fake_safe_proc_wait(process, *, timeout=None):
        waits.append(timeout)
        return None if len(waits) == 1 else -9

    monkeypatch.setattr(shell_module, "safe_proc_wait", fake_safe_proc_wait)
    monkeypatch.setattr(
        shell_module,
        "_signal_process_group",
        lambda pid, sig: signals.append((pid, sig)) or True,
    )
    if sys.platform == "win32":
        # Keep the unit test from taskkill-ing an unrelated real pid.
        monkeypatch.setattr(
            shell_module,
            "_taskkill_tree",
            lambda pid, *, force=True: taskkills.append(pid) or True,
        )

    rc = await shell_module.terminate_process_tree(proc)

    assert rc == -9
    assert waits == [2.0, 5.0]
    if sys.platform == "win32":
        # taskkill /T /F goes first; because the (faked) taskkill did not
        # reap the process, the terminate -> kill fallback ladder runs.
        assert taskkills == [proc.pid]
        assert signals == []
        assert proc.terminated is True
        assert proc.killed is True
    else:
        assert signals == [(proc.pid, signal.SIGTERM), (proc.pid, signal.SIGKILL)]
        assert proc.terminated is False
        assert proc.killed is False


def test_kill_process_group_uses_platform_native_tree_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[tuple[int, ...]] = []

    if sys.platform == "win32":
        def fake_taskkill(pid: int, *, force: bool = True) -> bool:
            killed.append((pid, force))
            return True

        monkeypatch.setattr(shell_module, "_taskkill_tree", fake_taskkill)
        assert shell_module.kill_process_group(4242) is True
        assert killed == [(4242, True)]
    else:
        monkeypatch.setattr(
            shell_module,
            "_signal_process_group",
            lambda pid, sig: killed.append((pid, sig)) or True,
        )
        assert shell_module.kill_process_group(4242) is True
        assert killed == [(4242, signal.SIGKILL)]


def test_kill_process_group_skips_kill_on_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded pid whose fingerprint changed must never be signalled.

    The OS reuses pids; killing on a stale recorded pid would take down an
    unrelated process (on win32, a whole unrelated tree via taskkill /T).
    The identity gate runs before any platform dispatch, so this test is
    platform-independent.
    """
    signalled: list[int] = []
    if sys.platform == "win32":
        monkeypatch.setattr(
            shell_module,
            "_taskkill_tree",
            lambda pid, *, force=True: signalled.append(pid) or True,
        )
    else:
        monkeypatch.setattr(
            shell_module,
            "_signal_process_group",
            lambda pid, sig: signalled.append(pid) or True,
        )
    monkeypatch.setattr(
        shell_module, "process_identity", lambda pid: "live-fingerprint"
    )

    killed = shell_module.kill_process_group(4242, identity="recorded-fingerprint")

    assert killed is False
    assert signalled == []


def test_kill_process_group_proceeds_on_identity_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching fingerprint proves the recorded incarnation still lives."""
    signalled: list[int] = []
    if sys.platform == "win32":
        monkeypatch.setattr(
            shell_module,
            "_taskkill_tree",
            lambda pid, *, force=True: signalled.append(pid) or True,
        )
    else:
        monkeypatch.setattr(
            shell_module,
            "_signal_process_group",
            lambda pid, sig: signalled.append(pid) or True,
        )
    monkeypatch.setattr(
        shell_module, "process_identity", lambda pid: "matching-fingerprint"
    )

    killed = shell_module.kill_process_group(4242, identity="matching-fingerprint")

    assert killed is True
    assert signalled == [4242]


async def _wait_for_file(path: Path, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path.name}")


@pytest.mark.asyncio
async def test_terminate_process_tree_kills_real_grandchild(tmp_path: Path) -> None:
    """Regression: the agent CLI is a grandchild of the spawned wrapper.

    Backends spawn ``bash -lc <agent cli>``, so the real workload runs one
    level below the process Symphony holds. Terminating only the direct
    child (the Windows bug this guards against) leaves the grandchild
    running. Uses a file handshake instead of pid-liveness APIs so the
    same assertions hold on Windows and on the POSIX CI runners.
    """
    (tmp_path / "tree_child.py").write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "grandchild = subprocess.Popen(\n"
        "    [sys.executable, str(Path(__file__).with_name('tree_grandchild.py'))]\n"
        ")\n"
        "print(grandchild.pid, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    (tmp_path / "tree_grandchild.py").write_text(
        "import time\n"
        "from pathlib import Path\n"
        "here = Path(__file__).parent\n"
        "(here / 'grandchild_alive').write_text('x', encoding='utf-8')\n"
        "while not (here / 'grandchild_stop').exists():\n"
        "    time.sleep(0.05)\n"
        "(here / 'grandchild_exited').write_text('x', encoding='utf-8')\n",
        encoding="utf-8",
    )
    spawn_kwargs: dict[str, Any] = {}
    if os.name == "posix":
        # Mirror the backends: the child leads its own process group so a
        # group kill can never reach the test runner.
        spawn_kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(tmp_path / "tree_child.py"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **spawn_kwargs,
    )
    grandchild_pid: int | None = None
    try:
        assert proc.stdout is not None  # spawned with stdout=PIPE above
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        grandchild_pid = int(line.strip())
        # Positive control: the grandchild must actually be running before
        # we terminate the tree, or the assertions below prove nothing.
        await _wait_for_file(tmp_path / "grandchild_alive", timeout_s=15)

        rc = await asyncio.wait_for(
            shell_module.terminate_process_tree(proc), timeout=30
        )
        assert rc is not None or proc.returncode is not None

        # If the grandchild survived the tree kill it notices this stop
        # file within ~0.05s and writes its exit marker — a generous 3s
        # window with no marker proves it was taken down with the tree.
        (tmp_path / "grandchild_stop").write_text("x", encoding="utf-8")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            assert not (tmp_path / "grandchild_exited").exists(), (
                f"grandchild pid {grandchild_pid} survived tree termination"
            )
            await asyncio.sleep(0.1)
    finally:
        # Self-service exit first: a surviving grandchild stops on its own
        # even if the hard kills below somehow miss it.
        with contextlib.suppress(OSError):
            (tmp_path / "grandchild_stop").write_text("x", encoding="utf-8")
        for pid in (proc.pid, grandchild_pid):
            if pid is None:
                continue
            with contextlib.suppress(OSError):
                if sys.platform == "win32":
                    shell_module._taskkill_tree(pid)
                else:
                    os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await shell_module.safe_proc_wait(proc, timeout=1.0)


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
    # claude's _reap helper routes through per_turn._reap_process, which
    # resolves terminate_process_tree in per_turn's namespace.
    monkeypatch.setattr(
        per_turn_module,
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
