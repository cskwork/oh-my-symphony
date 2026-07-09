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
from pathlib import Path
from typing import Any

import pytest

import symphony.backends.claude_code as claude_module
import symphony.backends.codex as codex_module
import symphony.backends.per_turn as per_turn_module
import symphony.backends.pi as pi_module
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
from tests.test_backends import (
    _BlockingStream,
    _FakeSubprocess,
    _install_subprocess_double,
    _make_cfg,
)

ALL_KINDS = ("codex", "claude", "gemini", "agy", "kiro", "opencode", "pi")

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
    module = claude_module
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
    module = pi_module

    def success_processes(self) -> list[_FakeSubprocess]:
        return [
            _FakeSubprocess(
                stdout_lines=[
                    b'{"type":"session","version":3,"id":"pi-c1"}\n',
                    b'{"type":"agent_end","messages":[]}\n',
                ]
            )
        ]
