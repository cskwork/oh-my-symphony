"""Backend contract suite — one Testcase Superclass every adapter must pass.

Initiative C of docs/improvements/architecture-improvement-plan-2026-07-05.md.

Asserts the `AgentBackend` lifecycle contract documented in
`symphony/backends/__init__.py`:

    start -> initialize -> start_session -> run_turn* -> stop

and the MUST-emit normalized events: `session_started` before the first
turn outcome, `turn_started` with the live child pid immediately after every
per-turn spawn, `turn_completed` / `turn_failed` per turn outcome, plus the
shared event envelope every adapter emits through `_emit`.

Each concrete adapter subclasses `PerTurnBackendContract` and only supplies
its canned CLI output (Meszaros, Testcase Superclass). A new adapter that
cannot pass this suite must not ship; an upstream schema drift that breaks
parsing (cf. the opencode `run --format json` incident) turns these tests
red instead of silently emptying responses.

Codex is deliberately absent from the per-turn lifecycle matrix: it is the
second lifecycle family (persistent app-server, JSON-RPC over stdio) and keeps
its own suite in `test_backends*.py`. This module checks its protocol and one
live persistent-process event without changing its spawn/reaping contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import symphony.backends.claude_code as claude_module
import symphony.backends.codex as codex_module
import symphony.backends.per_turn as per_turn_module
from symphony.backends import (
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    EVENT_TURN_STARTED,
    AgentBackend,
    BackendInit,
    build_backend,
)
from symphony.errors import ResponseError, TurnFailed
from symphony.orchestrator import Orchestrator
from symphony.utils.git_sandbox import GIT_ROOTS_ENV_VAR
from tests.test_backends import (
    _BlockingStream,
    _FakeSubprocess,
    _install_subprocess_double,
    _make_cfg,
)

ALL_KINDS = (
    "codex",
    "claude",
    "gemini",
    "agy",
    "kiro",
    "opencode",
    "pi",
    "prime-agent",
)

# Keys of the normalized event envelope every backend's `_emit` produces.
EVENT_ENVELOPE_KEYS = {
    "event",
    "timestamp",
    "payload",
    "usage",
    "rate_limits",
    "agent_pid",
}


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_backend_kind_satisfies_protocol(kind: str, tmp_path: Path) -> None:
    cfg = _make_cfg(kind, workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    backend = build_backend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_async_noop)
    )
    assert isinstance(backend, AgentBackend)


async def _async_noop(event: dict[str, Any]) -> None:
    del event


@pytest.mark.asyncio
async def test_codex_live_event_exposes_agent_pid(tmp_path: Path) -> None:
    """The persistent Codex lifecycle must publish its owned process group too."""
    cfg = _make_cfg("codex", workspace_root=tmp_path)
    cwd = tmp_path / "ws"
    cwd.mkdir()
    events: list[dict[str, Any]] = []

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)

    backend = codex_module.CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=on_event)
    )
    backend._process = _FakeSubprocess()  # type: ignore[assignment]

    await backend._handle_notification(
        {
            "method": codex_module.NOTIF_ITEM_COMPLETED,
            "params": {"item": {"type": "agentMessage", "text": "working"}},
        }
    )

    assert events[-1]["agent_pid"] == _FakeSubprocess.pid


class PerTurnBackendContract:
    """Contract every per-turn CLI adapter must satisfy identically.

    Subclasses provide `kind`, the module whose subprocess machinery gets
    doubled, and the canned stdout of one successful CLI turn.
    """

    kind: str
    module: Any
    canonical_message: str | None = None

    def success_processes(self) -> list[_FakeSubprocess]:
        raise NotImplementedError

    def failure_process(self) -> _FakeSubprocess:
        return _FakeSubprocess(stdout_blob=b"", stderr_blob=b"boom\n", returncode=1)

    def _make_backend(
        self, tmp_path: Path, events: list[dict[str, Any]]
    ) -> AgentBackend:
        cfg = _make_cfg(self.kind, workspace_root=tmp_path)
        cwd = tmp_path / "ws"
        cwd.mkdir(exist_ok=True)

        async def on_event(event: dict[str, Any]) -> None:
            events.append(event)

        return build_backend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=on_event)
        )

    async def test_full_lifecycle_emits_contract_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict[str, Any]] = []
        _install_subprocess_double(monkeypatch, self.module, self.success_processes())
        backend = self._make_backend(tmp_path, events)

        await backend.start()
        info = await backend.initialize()
        assert isinstance(info, dict)
        session_id = await backend.start_session(
            initial_prompt="hi", issue_title="Contract"
        )
        assert isinstance(session_id, str) and session_id
        result = await backend.run_turn(prompt="do the thing", is_continuation=False)
        await backend.stop()

        assert result.status == EVENT_TURN_COMPLETED
        assert result.turn_id
        names = [event["event"] for event in events]
        assert EVENT_SESSION_STARTED in names
        assert EVENT_TURN_COMPLETED in names
        assert names.index(EVENT_SESSION_STARTED) < names.index(EVENT_TURN_COMPLETED)
        turn_completed = next(
            event for event in events if event["event"] == EVENT_TURN_COMPLETED
        )
        assert turn_completed["agent_pid"] == _FakeSubprocess.pid
        for event in events:
            assert EVENT_ENVELOPE_KEYS <= event.keys()
            assert isinstance(event["payload"], dict)

    async def test_turn_spawn_events_publish_distinct_pids_immediately(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict[str, Any]] = []
        processes = [_FakeSubprocess(), _FakeSubprocess()]
        processes[0].pid = 11111
        processes[1].pid = 22222
        for process in processes:
            process.stdout = _BlockingStream()
            process.stderr = _BlockingStream()
        _install_subprocess_double(monkeypatch, self.module, processes.copy())
        backend = self._make_backend(tmp_path, events)

        for index, expected_pid in enumerate((11111, 22222), start=1):
            task = asyncio.create_task(
                backend.run_turn(prompt=f"turn {index}", is_continuation=index > 1)
            )
            try:
                for _ in range(100):
                    spawn_events = [
                        event
                        for event in events
                        if event["event"] == EVENT_TURN_STARTED
                    ]
                    if len(spawn_events) == index:
                        break
                    await asyncio.sleep(0.001)
                assert len(spawn_events) == index, (
                    "turn_started must publish the live child pid before output"
                )
                assert spawn_events[-1]["agent_pid"] == expected_pid
                assert EVENT_TURN_COMPLETED not in [
                    event["event"] for event in events
                ]
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        assert [event["agent_pid"] for event in spawn_events] == [11111, 22222]

    async def test_productive_completion_exposes_canonical_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if self.canonical_message is None:
            pytest.skip("backend is outside the AF-05 preview contract")
        events: list[dict[str, Any]] = []
        _install_subprocess_double(monkeypatch, self.module, self.success_processes())
        backend = self._make_backend(tmp_path, events)

        await backend.start_session(initial_prompt="hi", issue_title="Contract")
        await backend.run_turn(prompt="do the thing", is_continuation=False)

        completed = [event for event in events if event["event"] == EVENT_TURN_COMPLETED]
        payload = completed[-1]["payload"]
        assert payload["message"] == self.canonical_message
        assert Orchestrator._preview_from_payload(payload) == self.canonical_message

    async def test_zero_exit_whitespace_stdout_is_a_failed_turn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict[str, Any]] = []
        _install_subprocess_double(
            monkeypatch,
            self.module,
            [_FakeSubprocess(stdout_blob=b" \n\t", returncode=0)],
        )
        backend = self._make_backend(tmp_path, events)

        await backend.start_session(initial_prompt="hi", issue_title="Contract")
        with pytest.raises(TurnFailed):
            await backend.run_turn(prompt="do the thing", is_continuation=False)

        names = [event["event"] for event in events]
        assert EVENT_TURN_FAILED in names
        assert EVENT_TURN_COMPLETED not in names

    async def test_nonzero_exit_emits_turn_failed_and_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict[str, Any]] = []
        _install_subprocess_double(monkeypatch, self.module, [self.failure_process()])
        backend = self._make_backend(tmp_path, events)

        await backend.start_session(initial_prompt="hi", issue_title="Contract")
        with pytest.raises(TurnFailed):
            await backend.run_turn(prompt="do the thing", is_continuation=False)

        names = [event["event"] for event in events]
        assert EVENT_TURN_FAILED in names
        assert EVENT_TURN_COMPLETED not in names

    async def test_stop_is_idempotent_and_closes_backend(
        self, tmp_path: Path
    ) -> None:
        events: list[dict[str, Any]] = []
        backend = self._make_backend(tmp_path, events)

        await backend.stop()
        await backend.stop()
        with pytest.raises(ResponseError):
            await backend.run_turn(prompt="late", is_continuation=False)


class TestClaudeBackendContract(PerTurnBackendContract):
    kind = "claude"
    module = per_turn_module
    canonical_message = "done"

    def success_processes(self) -> list[_FakeSubprocess]:
        return [
            _FakeSubprocess(
                stdout_lines=[
                    b'{"type":"system","subtype":"init","session_id":"claude-c1"}\n',
                    b'{"type":"assistant","message":{"content":['
                    b'{"type":"tool_use","name":"Edit"},'
                    b'{"type":"text","text":"done"}]}}\n',
                    b'{"type":"result","subtype":"success","is_error":false,'
                    b'"result":"","session_id":"claude-c1","usage":{}}\n',
                ]
            )
        ]


class TestGeminiBackendContract(PerTurnBackendContract):
    kind = "gemini"
    module = per_turn_module
    canonical_message = "done"

    def success_processes(self) -> list[_FakeSubprocess]:
        return [
            _FakeSubprocess(
                stdout_blob=b'{"session_id":"gem-c1","response":"done","stats":{}}'
            )
        ]


class TestAgyBackendContract(PerTurnBackendContract):
    kind = "agy"
    module = per_turn_module
    canonical_message = "done"

    def success_processes(self) -> list[_FakeSubprocess]:
        return [_FakeSubprocess(stdout_blob=b"done")]


class TestKiroBackendContract(PerTurnBackendContract):
    kind = "kiro"
    module = per_turn_module
    canonical_message = "done"

    def success_processes(self) -> list[_FakeSubprocess]:
        return [_FakeSubprocess(stdout_blob=b"done")]


class TestOpenCodeBackendContract(PerTurnBackendContract):
    kind = "opencode"
    module = per_turn_module

    def success_processes(self) -> list[_FakeSubprocess]:
        return [
            _FakeSubprocess(
                stdout_blob=(
                    b'{"type":"session.updated","session":{"id":"oc-c1"}}\n'
                    b'{"type":"message","message":"done",'
                    b'"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}\n'
                )
            )
        ]


class TestPiBackendContract(PerTurnBackendContract):
    kind = "pi"
    module = per_turn_module

    def success_processes(self) -> list[_FakeSubprocess]:
        return [
            _FakeSubprocess(
                stdout_lines=[
                    b'{"type":"session","version":3,"id":"pi-c1"}\n',
                    b'{"type":"agent_end","messages":[]}\n',
                ]
            )
        ]


class TestPrimeAgentBackendContract(PerTurnBackendContract):
    kind = "prime-agent"
    module = per_turn_module

    def success_processes(self) -> list[_FakeSubprocess]:
        return [
            _FakeSubprocess(
                stdout_lines=[
                    b'{"type":"session","version":3,"id":"prime-c1"}\n',
                    b'{"type":"message_end","message":{"role":"user",'
                    b'"content":[{"type":"text","text":"prompt"}]}}\n',
                    b'{"type":"message_end","message":{"role":"assistant",'
                    b'"content":[{"type":"text","text":"done"}]}}\n',
                    b'{"type":"agent_end","messages":[{"role":"assistant",'
                    b'"content":[{"type":"text","text":"done"}],'
                    b'"stopReason":"stop"}]}\n',
                ]
            )
        ]


# ---------------------------------------------------------------------------
# git-root grant — every agent kind, not just the two Symphony can flag-inject
#
# The default workspace is a linked git worktree, which puts the object
# database outside the workspace directory. Whatever sandbox an agent CLI
# applies, Symphony must hand it the paths a delivery commit needs; every
# backend does that through the environment, and codex/claude additionally on
# the command line. A backend that silently skips this reintroduces the
# `failed to insert into database` block that stalls a board.
# ---------------------------------------------------------------------------

# Every per-turn adapter (claude and the pi family included) spawns and
# reaps through the `per_turn` skeleton, so that is where the subprocess
# names get doubled; codex keeps its own persistent-process machinery.
_SPAWN_MODULES = {
    "codex": codex_module,
    "claude": per_turn_module,
    "gemini": per_turn_module,
    "agy": per_turn_module,
    "kiro": per_turn_module,
    "opencode": per_turn_module,
    "pi": per_turn_module,
    "prime-agent": per_turn_module,
}


def _worktree_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Host repo + workspace-root + a real linked worktree used as agent cwd."""
    env = {
        "HOME": str(tmp_path),
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": os.environ.get("PATH", ""),
    }

    def git(cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(cwd), env=env, check=True, capture_output=True
        )

    host = tmp_path / "host"
    host.mkdir()
    git(host, "init", "-q", "-b", "main")
    (host / "seed.txt").write_text("seed")
    git(host, "add", "seed.txt")
    git(host, "commit", "-qm", "seed")
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    cwd = workspace_root / "ws"
    git(host, "worktree", "add", "-q", str(cwd), "-b", "symphony/T-1")
    return host, workspace_root, cwd


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.asyncio
async def test_every_backend_grants_the_object_database_of_a_worktree(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git CLI required")
    host, workspace_root, cwd = _worktree_workspace(tmp_path)
    captured: dict[str, dict[str, str]] = {}
    module = _SPAWN_MODULES[kind]

    async def fake_create_subprocess_exec(*args: Any, **kwargs: Any):
        del args
        captured["env"] = dict(kwargs.get("env") or {})
        return _FakeSubprocess(stdout_blob=b"", stderr_blob=b"", returncode=0)

    async def fake_safe_proc_wait(proc: Any, *, timeout: Any = None) -> Any:
        del timeout
        return proc.returncode

    monkeypatch.setattr(
        module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(module, "safe_proc_wait", fake_safe_proc_wait, raising=False)

    cfg = _make_cfg(kind, workspace_root=workspace_root)
    backend = build_backend(
        BackendInit(
            cfg=cfg, cwd=cwd, workspace_root=workspace_root, on_event=_async_noop
        )
    )
    await backend.start()
    if kind != "codex":
        # Per-turn backends spawn inside run_turn; the canned empty stdout
        # makes parsing fail, which is irrelevant — the env is already
        # captured by then.
        with contextlib.suppress(Exception):
            await backend.run_turn(prompt="do the thing", is_continuation=False)
    with contextlib.suppress(Exception):
        await backend.stop()

    assert "env" in captured, f"{kind} never spawned a subprocess"
    granted = captured["env"].get(GIT_ROOTS_ENV_VAR, "").split(os.pathsep)
    common_dir = str((host / ".git").resolve())
    gitdir = str((host / ".git" / "worktrees" / "ws").resolve())
    assert common_dir in granted, f"{kind} did not grant the object database"
    assert gitdir in granted, f"{kind} did not grant the worktree admin dir"


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.asyncio
async def test_no_backend_grants_extra_roots_for_a_plain_repo_workspace(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace that owns its `.git` needs nothing extra — stay quiet."""
    if shutil.which("git") is None:
        pytest.skip("git CLI required")
    workspace_root = tmp_path / "workspaces"
    cwd = workspace_root / "ws"
    cwd.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(cwd),
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        check=True,
        capture_output=True,
    )
    captured: dict[str, dict[str, str]] = {}
    module = _SPAWN_MODULES[kind]

    async def fake_create_subprocess_exec(*args: Any, **kwargs: Any):
        del args
        captured["env"] = dict(kwargs.get("env") or {})
        return _FakeSubprocess(stdout_blob=b"", stderr_blob=b"", returncode=0)

    async def fake_safe_proc_wait(proc: Any, *, timeout: Any = None) -> Any:
        del timeout
        return proc.returncode

    monkeypatch.setattr(
        module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(module, "safe_proc_wait", fake_safe_proc_wait, raising=False)

    cfg = _make_cfg(kind, workspace_root=workspace_root)
    backend = build_backend(
        BackendInit(
            cfg=cfg, cwd=cwd, workspace_root=workspace_root, on_event=_async_noop
        )
    )
    await backend.start()
    if kind != "codex":
        with contextlib.suppress(Exception):
            await backend.run_turn(prompt="do the thing", is_continuation=False)
    with contextlib.suppress(Exception):
        await backend.stop()

    assert captured["env"].get(GIT_ROOTS_ENV_VAR, "") == ""


# ---------------------------------------------------------------------------
# Review §4.3 — transient stream errors must retry, not pause
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        "claude stream unreadable: 20 consecutive malformed lines",
        "codex stream unreadable: 20 consecutive malformed lines",
        "pi stream unreadable: 20 consecutive malformed lines",
        "claude exited with no result event (rc=1)",
    ],
)
def test_transient_stream_errors_are_retryable(error: str) -> None:
    from symphony.orchestrator.core import _is_retryable_worker_error

    assert _is_retryable_worker_error("claude", "worker_exit", error)


def test_unmatched_crashes_still_pause_for_inspection() -> None:
    """A blanket retry-all would mask real crashes — keep the pause path."""
    from symphony.orchestrator.core import _is_retryable_worker_error

    assert not _is_retryable_worker_error(
        "claude", "worker_exit", "TypeError: NoneType is not subscriptable"
    )


def test_retry_markers_match_the_strings_backends_actually_emit() -> None:
    """Drift guard: the marker list and the backend messages are one contract."""
    import symphony.backends.pi as pi_module
    from symphony.orchestrator.core import _RETRYABLE_WORKER_ERROR_MARKERS

    # claude and pi now emit the stream-unreadable message from the shared
    # streaming base in per_turn, so that module is part of the contract.
    sources = "\n".join(
        Path(mod.__file__).read_text(encoding="utf-8")
        for mod in (claude_module, codex_module, pi_module, per_turn_module)
    )
    for marker in ("stream unreadable", "no result event"):
        assert marker in _RETRYABLE_WORKER_ERROR_MARKERS
        assert marker in sources, (
            f"marker {marker!r} no longer appears in any backend — the retry "
            "list and the backends have drifted apart"
        )


# ---------------------------------------------------------------------------
# GitHub #29 — per-dispatch env travels in BackendInit, not os.environ
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.asyncio
async def test_every_backend_overlays_backend_init_env_on_spawn(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`BackendInit.env` wins over the inherited process environment."""
    if shutil.which("git") is None:
        pytest.skip("git CLI required")
    workspace_root = tmp_path / "workspaces"
    cwd = workspace_root / "ws"
    cwd.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(cwd),
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        check=True,
        capture_output=True,
    )
    # A stale value from an earlier dispatch must not leak through.
    monkeypatch.setenv("SYMPHONY_TOKEN_BUDGET", "stale")
    monkeypatch.setenv("SYMPHONY_REWIND_SCOPE", "[]")
    captured: dict[str, dict[str, str]] = {}
    module = _SPAWN_MODULES[kind]

    async def fake_create_subprocess_exec(*args: Any, **kwargs: Any):
        del args
        captured["env"] = dict(kwargs.get("env") or {})
        return _FakeSubprocess(stdout_blob=b"", stderr_blob=b"", returncode=0)

    async def fake_safe_proc_wait(proc: Any, *, timeout: Any = None) -> Any:
        del timeout
        return proc.returncode

    monkeypatch.setattr(
        module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(module, "safe_proc_wait", fake_safe_proc_wait, raising=False)

    cfg = _make_cfg(kind, workspace_root=workspace_root)
    backend = build_backend(
        BackendInit(
            cfg=cfg,
            cwd=cwd,
            workspace_root=workspace_root,
            on_event=_async_noop,
            env={"SYMPHONY_TOKEN_BUDGET": "4321", "SYMPHONY_TOKEN_EMA": "10"},
        )
    )
    await backend.start()
    if kind != "codex":
        with contextlib.suppress(Exception):
            await backend.run_turn(prompt="do the thing", is_continuation=False)
    with contextlib.suppress(Exception):
        await backend.stop()

    assert "env" in captured, f"{kind} never spawned a subprocess"
    assert captured["env"].get("SYMPHONY_TOKEN_BUDGET") == "4321"
    assert captured["env"].get("SYMPHONY_TOKEN_EMA") == "10"
    # Inherited keys the dispatch did not set stay inherited: the overlay is
    # additive, it does not replace the environment.
    assert captured["env"].get("SYMPHONY_REWIND_SCOPE") == "[]"
    assert captured["env"].get("PATH") == os.environ.get("PATH")
