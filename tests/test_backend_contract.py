"""Backend contract suite — one Testcase Superclass every adapter must pass.

Initiative C of docs/improvements/architecture-improvement-plan-2026-07-05.md.

Asserts the `AgentBackend` lifecycle contract documented in
`symphony/backends/__init__.py`:

    start -> initialize -> start_session -> run_turn* -> stop

and the MUST-emit normalized events: `session_started` before the first
turn outcome, `turn_completed` / `turn_failed` per turn outcome, plus the
shared event envelope every adapter emits through `_emit`.

Each concrete adapter subclasses `PerTurnBackendContract` and only supplies
its canned CLI output (Meszaros, Testcase Superclass). A new adapter that
cannot pass this suite must not ship; an upstream schema drift that breaks
parsing (cf. the opencode `run --format json` incident) turns these tests
red instead of silently emptying responses.

Codex is deliberately absent from the lifecycle matrix: it is the second
lifecycle family (persistent app-server, JSON-RPC over stdio) and keeps its
own suite in `test_backends*.py`. It still appears in the protocol check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import symphony.backends.claude_code as claude_module
import symphony.backends.gemini as gemini_module
import symphony.backends.opencode as opencode_module
import symphony.backends.pi as pi_module
import symphony.backends.plain_cli as plain_cli_module
from symphony.backends import (
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    AgentBackend,
    BackendInit,
    build_backend,
)
from symphony.errors import ResponseError, TurnFailed
from tests.test_backends import (
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


class PerTurnBackendContract:
    """Contract every per-turn CLI adapter must satisfy identically.

    Subclasses provide `kind`, the module whose subprocess machinery gets
    doubled, and the canned stdout of one successful CLI turn.
    """

    kind: str
    module: Any

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
        for event in events:
            assert EVENT_ENVELOPE_KEYS <= event.keys()
            assert isinstance(event["payload"], dict)

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

    def success_processes(self) -> list[_FakeSubprocess]:
        return [
            _FakeSubprocess(
                stdout_lines=[
                    b'{"type":"system","subtype":"init","session_id":"claude-c1"}\n',
                    b'{"type":"result","subtype":"success","is_error":false,'
                    b'"result":"done","session_id":"claude-c1","usage":{}}\n',
                ]
            )
        ]


class TestGeminiBackendContract(PerTurnBackendContract):
    kind = "gemini"
    module = gemini_module

    def success_processes(self) -> list[_FakeSubprocess]:
        return [
            _FakeSubprocess(
                stdout_blob=b'{"session_id":"gem-c1","response":"done","stats":{}}'
            )
        ]


class TestAgyBackendContract(PerTurnBackendContract):
    kind = "agy"
    module = plain_cli_module

    def success_processes(self) -> list[_FakeSubprocess]:
        return [_FakeSubprocess(stdout_blob=b"done")]


class TestKiroBackendContract(PerTurnBackendContract):
    kind = "kiro"
    module = plain_cli_module

    def success_processes(self) -> list[_FakeSubprocess]:
        return [_FakeSubprocess(stdout_blob=b"done")]


class TestOpenCodeBackendContract(PerTurnBackendContract):
    kind = "opencode"
    module = opencode_module

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
