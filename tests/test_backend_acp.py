"""`format: acp` profiles — the Agent Client Protocol backend against a fake agent."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from symphony.backends import (
    EVENT_APPROVAL_AUTO_APPROVED,
    EVENT_APPROVAL_DENIED,
    EVENT_MALFORMED,
    EVENT_NOTIFICATION,
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_TURN_CANCELLED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    EVENT_TURN_STARTED,
    BackendInit,
    build_backend,
)
from symphony.backends.acp import AcpBackend
from symphony.backends.plain_cli import PlainCliBackend
from symphony.errors import ResponseError, ResponseTimeout, TurnFailed, TurnTimeout
from symphony.workflow import ServiceConfig, build_service_config, parse_workflow_text

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")

FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


def _cfg(tmp_path: Path, frontmatter: str) -> ServiceConfig:
    text = "---\n" + textwrap.dedent(frontmatter).lstrip() + "---\nbody\n"
    return build_service_config(parse_workflow_text(text, tmp_path / "WORKFLOW.md"))


def _acp_cfg(tmp_path: Path, mode: str = "normal", *, extra: str = "", **timeouts: int) -> ServiceConfig:
    command = shlex.join([sys.executable, str(FAKE_AGENT), mode, *extra.split()])
    timeout_lines = "".join(f"      {key}: {value}\n" for key, value in timeouts.items())
    frontmatter = (
        "tracker: {kind: file, board_root: ./board}\n"
        "agent:\n"
        "  kind: fake-acp\n"
        "  profiles:\n"
        "    fake-acp:\n"
        f"      command: {json.dumps(command)}\n"
        "      format: acp\n" + timeout_lines
    )
    return _cfg(tmp_path, frontmatter)


class _Harness:
    def __init__(self, tmp_path: Path, cfg: ServiceConfig) -> None:
        self.events: list[dict[str, Any]] = []
        self.log = tmp_path / "acp-received.jsonl"
        self.child_pid_file = tmp_path / "child.pid"
        cwd = tmp_path / "ws"
        cwd.mkdir(exist_ok=True)
        self.cwd = cwd
        backend = build_backend(
            BackendInit(
                cfg=cfg,
                cwd=cwd,
                workspace_root=tmp_path,
                on_event=self._on_event,
                env={
                    # Keep the operator's login profile out of `bash -lc`.
                    "HOME": str(tmp_path),
                    "FAKE_ACP_LOG": str(self.log),
                    "FAKE_ACP_CHILD_PID": str(self.child_pid_file),
                },
            )
        )
        assert isinstance(backend, AcpBackend)
        self.backend = backend

    async def _on_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [event["event"] for event in self.events]

    def payloads(self, name: str) -> list[dict[str, Any]]:
        return [event["payload"] for event in self.events if event["event"] == name]

    def received(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    async def open(self) -> str:
        await self.backend.start()
        await self.backend.initialize()
        return await self.backend.start_session(initial_prompt="p", issue_title="T")


@pytest.fixture
async def harness_factory(tmp_path: Path):
    made: list[_Harness] = []

    def make(cfg: ServiceConfig) -> _Harness:
        harness = _Harness(tmp_path, cfg)
        made.append(harness)
        return harness

    yield make
    for harness in made:
        await harness.backend.stop()


def _group_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_handshake_streams_chunks_into_final_message(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path))

    sid = await h.open()
    result = await h.backend.run_turn(prompt="hello", is_continuation=False)

    init, new, prompt = h.received()[:3]
    assert init["method"] == "initialize"
    assert init["params"]["protocolVersion"] == 1
    assert init["params"]["clientCapabilities"] == {
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    }
    assert new["method"] == "session/new"
    assert new["params"] == {"cwd": str(h.cwd), "mcpServers": []}
    assert prompt["params"] == {"sessionId": sid, "prompt": [{"type": "text", "text": "hello"}]}
    assert result.status == EVENT_TURN_COMPLETED
    assert result.turn_id == sid
    assert result.last_message.startswith("turn 1 pid ")
    assert result.last_message.endswith("echo hello")
    completed = h.payloads(EVENT_TURN_COMPLETED)[-1]
    assert completed["message"] == result.last_message
    assert completed["stop_reason"] == "end_turn"
    names = h.names()
    assert names.index(EVENT_SESSION_STARTED) < names.index(EVENT_TURN_STARTED)
    assert names.index(EVENT_TURN_STARTED) < names.index(EVENT_TURN_COMPLETED)


async def test_updates_map_to_normalized_events_and_unknown_is_ignored(
    tmp_path: Path, harness_factory
) -> None:
    h = harness_factory(_acp_cfg(tmp_path))
    await h.open()

    await h.backend.run_turn(prompt="x", is_continuation=False)

    others = h.payloads(EVENT_OTHER_MESSAGE)
    types = [payload.get("type") for payload in others]
    assert "agent_thought" in types
    assert "agent_delta" in types
    tool = next(p for p in others if p.get("type") == "tool_call")
    assert tool == {
        "type": "tool_call", "tool_call_id": "c1", "title": "Read file",
        "kind": "read", "status": "pending",
    }
    unknown = [p for p in others if p.get("type") == "acp_update"]
    assert {"type": "acp_update", "session_update": "brand_new_kind_from_the_future"} in unknown
    usage = next(p for p in others if p.get("type") == "usage")
    assert usage == {
        "type": "usage", "context_used_tokens": 1200, "context_size_tokens": 200000,
        "cost": {"amount": 0.01, "currency": "USD"},
    }
    # Stall clock: model/tool activity is progress, telemetry and unknown are not.
    assert h.backend.is_progress_event({"type": "agent_delta"})
    assert h.backend.is_progress_event({"type": "tool_call_update"})
    assert not h.backend.is_progress_event({"type": "usage"})
    assert not h.backend.is_progress_event({"type": "acp_update"})


async def test_permission_auto_approved_and_unadvertised_methods_rejected(
    tmp_path: Path, harness_factory
) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "permission"))
    await h.open()

    result = await h.backend.run_turn(prompt="edit", is_continuation=False)

    # allow_once preferred over allow_always; destructive command rejected;
    # fs/read_text_file was never advertised -> method not found.
    assert result.last_message == "perm=yes;danger=no;fs=-32601"
    approved = h.payloads(EVENT_APPROVAL_AUTO_APPROVED)
    assert approved == [
        {"method": "session/request_permission", "option": "yes", "tool": "Edit file"}
    ]
    denied = h.payloads(EVENT_APPROVAL_DENIED)
    assert denied[0]["command"] == "rm -rf /"


async def test_error_response_fails_turn(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "error"))
    await h.open()

    with pytest.raises(TurnFailed, match="model exploded"):
        await h.backend.run_turn(prompt="x", is_continuation=False)

    assert "model exploded" in h.payloads(EVENT_TURN_FAILED)[-1]["reason"]


async def test_non_end_turn_stop_reason_fails_turn(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "refusal"))
    await h.open()

    with pytest.raises(TurnFailed, match="refusal"):
        await h.backend.run_turn(prompt="x", is_continuation=False)


async def test_sparse_malformed_lines_are_tolerated(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "malformed"))
    await h.open()

    result = await h.backend.run_turn(prompt="ok", is_continuation=False)

    assert result.last_message.endswith("echo ok")
    assert [p["raw"] for p in h.payloads(EVENT_MALFORMED)] == ["this is not json", "{broken"]


async def test_garbage_stream_fails_turn(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "garbage"))
    await h.open()

    with pytest.raises(TurnFailed, match="stream unreadable"):
        await h.backend.run_turn(prompt="x", is_continuation=False)


async def test_silent_agent_hits_read_timeout(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "silent-init", read_timeout_ms=300))
    await h.backend.start()

    with pytest.raises(ResponseTimeout):
        await h.backend.initialize()


async def test_stalled_turn_times_out_and_kills_process_group(
    tmp_path: Path, harness_factory
) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "hang", turn_timeout_ms=1500))
    await h.open()
    agent_pid = h.backend.pid
    assert agent_pid is not None

    with pytest.raises(TurnTimeout):
        await h.backend.run_turn(prompt="x", is_continuation=False)

    child_pid = int(h.child_pid_file.read_text())
    assert not _group_alive(child_pid)
    assert not _group_alive(agent_pid)
    assert h.backend.pid is None
    assert any(m.get("method") == "session/cancel" for m in h.received())
    assert h.payloads(EVENT_TURN_FAILED)[-1]["reason"] == "turn_timeout"


@pytest.mark.parametrize("mode", ["hang", "hang-cancel"])
async def test_cancel_sends_session_cancel_and_kills_group(
    tmp_path: Path, harness_factory, mode: str
) -> None:
    h = harness_factory(_acp_cfg(tmp_path, mode))
    sid = await h.open()
    agent_pid = h.backend.pid
    assert agent_pid is not None
    task = asyncio.create_task(h.backend.run_turn(prompt="x", is_continuation=False))
    for _ in range(200):
        if h.child_pid_file.exists() and h.child_pid_file.read_text():
            break
        await asyncio.sleep(0.02)
    child_pid = int(h.child_pid_file.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    cancels = [m for m in h.received() if m.get("method") == "session/cancel"]
    assert cancels == [{"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid}}]
    assert not _group_alive(child_pid)
    assert not _group_alive(agent_pid)
    assert EVENT_TURN_CANCELLED in h.names()


async def test_continuation_reuses_live_session(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path))
    sid = await h.open()
    pid = h.backend.pid

    first = await h.backend.run_turn(prompt="one", is_continuation=False)
    second = await h.backend.run_turn(prompt="two", is_continuation=True)

    assert first.last_message.startswith(f"turn 1 pid {pid} ")
    assert second.last_message.startswith(f"turn 2 pid {pid} ")
    prompts = [m for m in h.received() if m.get("method") == "session/prompt"]
    assert [m["params"]["sessionId"] for m in prompts] == [sid, sid]
    assert [m.get("method") for m in h.received()].count("session/new") == 1


async def test_dead_agent_resumes_session_when_advertised(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "die-after-turn", extra="--resume"))
    sid = await h.open()
    first_pid = h.backend.pid
    await h.backend.run_turn(prompt="one", is_continuation=False)
    for _ in range(200):
        if not h.backend._process_alive():
            break
        await asyncio.sleep(0.02)

    second = await h.backend.run_turn(prompt="two", is_continuation=True)

    assert h.backend.pid != first_pid
    assert h.backend.session_id == sid
    resume = [m for m in h.received() if m.get("method") == "session/resume"]
    assert resume[0]["params"]["sessionId"] == sid
    assert second.last_message.endswith("echo two")
    assert any(p.get("type") == "acp_session_resumed" for p in h.payloads(EVENT_NOTIFICATION))


async def test_dead_agent_without_resume_starts_fresh_session_and_says_so(
    tmp_path: Path, harness_factory
) -> None:
    h = harness_factory(_acp_cfg(tmp_path, "die-after-turn"))
    sid = await h.open()
    await h.backend.run_turn(prompt="one", is_continuation=False)
    for _ in range(200):
        if not h.backend._process_alive():
            break
        await asyncio.sleep(0.02)

    await h.backend.run_turn(prompt="two", is_continuation=True)

    assert h.backend.session_id != sid
    assert "session/resume" not in [m.get("method") for m in h.received()]
    notes = h.payloads(EVENT_NOTIFICATION)
    assert any(p.get("type") == "acp_session_restarted" for p in notes)


async def test_resume_session_unsupported_returns_false(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path))
    await h.backend.start()
    await h.backend.initialize()

    assert await h.backend.resume_session("sess-old") is False


async def test_stop_kills_agent_and_refuses_more_turns(tmp_path: Path, harness_factory) -> None:
    h = harness_factory(_acp_cfg(tmp_path))
    await h.open()
    pid = h.backend.pid
    assert pid is not None

    await h.backend.stop()

    assert not _group_alive(pid)
    with pytest.raises(ResponseError):
        await h.backend.run_turn(prompt="x", is_continuation=True)


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------


def test_acp_profile_validates_and_is_selectable(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        """
        tracker: {kind: file, board_root: ./board, active_states: [Todo, Verify]}
        agent:
          kind: gem
          stage_kinds: {Verify: kiro-acp}
          profiles:
            gem: {command: gemini --acp, format: acp, turn_timeout_ms: 5000}
            kiro-acp: {command: kiro-cli acp --trust-all-tools, format: acp}
        """,
    )

    assert cfg.agent.profiles["gem"].format == "acp"
    assert cfg.agent.kind == "gem"
    assert cfg.agent.kind_for_state("Verify") == "kiro-acp"
    assert cfg.backend_timeouts()[0] == 5000


def test_build_backend_routes_by_profile_format(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()

    def build(fmt: str) -> object:
        cfg = _cfg(
            tmp_path,
            f"""
            tracker: {{kind: file, board_root: ./board}}
            agent:
              kind: mini
              profiles:
                mini: {{command: mini-cli, format: {fmt}}}
            """,
        )
        return build_backend(
            BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop)
        )

    assert type(build("acp")) is AcpBackend
    assert type(build("text")) is PlainCliBackend


async def _noop(event: dict[str, Any]) -> None:
    del event
