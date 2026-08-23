"""Tests for the operator chat session manager (`symphony.chat`)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from symphony import chat as chat_module
from symphony.backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_STARTED,
    BackendInit,
    TurnResult,
)
from symphony.chat import (
    ChatManager,
    _board_preamble,
    _claude_command_for_mode,
    _summarize_claude_frame,
    _summarize_codex_frame,
    _summarize_pi_frame,
    _terminal_agent_message,
    cfg_for_mode,
)
from symphony.errors import (
    ChatBusyError,
    ChatBackendUnavailableError,
    ChatNoSessionError,
    ChatProjectActionError,
    ChatSessionExistsError,
    TurnTimeout,
)
from symphony.workflow import ServiceConfig, WorkflowState

CLAUDE_COMMAND = (
    "claude -p --output-format stream-json --verbose "
    '--permission-mode acceptEdits --add-dir "$SYMPHONY_WORKFLOW_DIR"'
)

WORKFLOW_TEXT = f"""---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Doing]
  terminal_states: [Done, Archive]

agent:
  kind: claude

claude:
  command: '{CLAUDE_COMMAND}'
---

You are working on {{{{ issue.identifier }}}}.
"""

CONFIRMATION_TOKEN = "c" * 64


def _setup_marker(payload: dict[str, Any]) -> str:
    """Render one project-setup marker with a properly encoded payload.

    Native Windows paths contain backslashes that are invalid as raw JSON
    escapes, so the payload must go through ``json.dumps`` instead of an
    f-string.
    """
    return (
        "<symphony-project-setup>" + json.dumps(payload) + "</symphony-project-setup>"
    )


def _cfg(tmp_path: Path) -> ServiceConfig:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    (tmp_path / "kanban").mkdir(exist_ok=True)
    state = WorkflowState(tmp_path / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    return cfg


class _FakeBackend:
    def __init__(self, init: BackendInit) -> None:
        self.init = init
        self.turns: list[tuple[str, bool]] = []
        self.stopped = False
        self.gate: asyncio.Event | None = None
        self.raise_on_turn: Exception | None = None
        self._session_id: str | None = None
        self.initialize_entered: asyncio.Event | None = None
        self.initialize_gate: asyncio.Event | None = None
        self.initialize_error: Exception | None = None

    async def start(self) -> None:
        pass

    async def initialize(self) -> dict[str, Any]:
        if self.initialize_entered is not None:
            self.initialize_entered.set()
        if self.initialize_gate is not None:
            await self.initialize_gate.wait()
        if self.initialize_error is not None:
            raise self.initialize_error
        return {}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        return "pending"

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        self.turns.append((prompt, is_continuation))
        if self.gate is not None:
            await self.gate.wait()
        if self.raise_on_turn is not None:
            raise self.raise_on_turn
        self._session_id = "sess-1"
        await self._emit(EVENT_TURN_STARTED, {})
        await self._emit(
            EVENT_OTHER_MESSAGE,
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"f": "a.py"}},
                        {"type": "text", "text": "reading the file"},
                    ]
                },
            },
        )
        await self._emit(EVENT_TURN_COMPLETED, {"message": f"answer {len(self.turns)}"})
        return TurnResult(
            status=EVENT_TURN_COMPLETED, turn_id="t", last_message="answer"
        )

    async def stop(self) -> None:
        self.stopped = True

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await self.init.on_event(
            {
                "event": event,
                "timestamp": "2026-08-06T00:00:00Z",
                "payload": payload,
                "usage": {"total_tokens": 7},
                "rate_limits": None,
                "agent_pid": 123,
            }
        )

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def pid(self) -> int | None:
        return 123

    @property
    def latest_usage(self) -> dict[str, int]:
        return {"total_tokens": 7}

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        return True


@pytest.fixture()
def fake_backends(monkeypatch: pytest.MonkeyPatch) -> list[_FakeBackend]:
    built: list[_FakeBackend] = []

    def _build(init: BackendInit) -> _FakeBackend:
        backend = _FakeBackend(init)
        built.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", _build)
    return built


async def _wait_turn(manager: ChatManager, session_id: str | None = None) -> None:
    session = (
        manager.active_session if session_id is None else manager.session(session_id)
    )
    assert session is not None and session.turn_task is not None
    await session.turn_task


# ---------------------------------------------------------------------------
# session lifecycle
# ---------------------------------------------------------------------------


async def test_start_session_caps_at_max_and_stop_clears(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    snapshot = await manager.start_session("qa")
    assert snapshot["active"] is True
    assert snapshot["mode"] == "qa"
    assert snapshot["agent_kind"] == "claude"
    assert snapshot["mode_enforced"] is True

    for _ in range(chat_module.MAX_SESSIONS - 1):
        await manager.start_session("qa")
    assert manager.live_count == chat_module.MAX_SESSIONS
    with pytest.raises(ChatSessionExistsError):
        await manager.start_session("qa")

    # Stopping one frees a slot; the newest live session becomes active.
    await manager.stop_session()
    assert manager.live_count == chat_module.MAX_SESSIONS - 1
    assert manager.snapshot()["active"] is True
    for _ in range(chat_module.MAX_SESSIONS - 1):
        await manager.stop_session()
    assert manager.snapshot() == {"active": False}
    assert all(b.stopped for b in fake_backends)
    with pytest.raises(ChatNoSessionError):
        await manager.stop_session()


async def test_backend_runs_in_workflow_dir_with_mode_command(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    init = fake_backends[0].init
    assert init.cwd == tmp_path
    assert init.workspace_root == tmp_path
    assert "--permission-mode plan" in init.cfg.claude.command
    assert "acceptEdits" not in init.cfg.claude.command
    await manager.stop_session()


async def test_send_message_preamble_and_continuation(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    backend = fake_backends[0]

    await manager.send_message("what does this repo do?")
    await _wait_turn(manager)
    prompt, is_continuation = backend.turns[0]
    assert prompt.endswith("what does this repo do?")
    assert "do not create, modify or delete" in prompt
    assert "Symphony kanban board at" in prompt
    assert "kanban" in prompt
    # Q&A mode describes tickets and defers filing to edit mode.
    assert "switch the chat to edit mode" in prompt
    # Board protocol: validated CLI with the board's actual states, never
    # hand-written ticket markdown.
    assert "${SYMPHONY_CLI:-symphony} board new" in prompt
    assert "Todo, Doing" in prompt
    assert "<IDENTIFIER>.md" not in prompt
    assert is_continuation is False

    await manager.send_message("second question")
    await _wait_turn(manager)
    prompt, is_continuation = backend.turns[1]
    assert prompt == "second question"
    assert is_continuation is True

    types = [m.type for m in manager.active_session.transcript]  # type: ignore[union-attr]
    assert "user_message" in types
    assert "turn_started" in types
    assert "agent_message" in types
    assert "tool_activity" in types
    assert "turn_completed" in types
    await manager.stop_session()


async def test_send_while_busy_raises(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    backend = fake_backends[0]
    backend.gate = asyncio.Event()

    await manager.send_message("slow one")
    await asyncio.sleep(0)  # let the turn task grab the lock
    assert manager.snapshot()["busy"] is True
    with pytest.raises(ChatBusyError):
        await manager.send_message("too soon")
    backend.gate.set()
    await _wait_turn(manager)
    await manager.stop_session()


async def test_send_without_backend_rejects_before_mutating_transcript(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    session = manager.active_session
    assert session is not None
    await session.backend.stop()  # type: ignore[union-attr]
    session.backend = None
    before = [(row.type, row.text) for row in session.transcript]

    with pytest.raises(ChatBackendUnavailableError, match="resume"):
        await manager.send_message("must not be accepted")

    assert session.turn_count == 0
    assert [(row.type, row.text) for row in session.transcript] == before
    assert session.turn_task is None
    await manager.stop_session()


async def test_mode_rebuild_blocks_send_until_backend_is_ready(
    tmp_path: Path,
    fake_backends: list[_FakeBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    session = manager.active_session
    assert session is not None
    before = (
        session.seq,
        session.turn_count,
        session.title,
        session.pending_preamble,
        session.pending_mode_notice,
        [(row.type, row.text) for row in session.transcript],
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    def build_replacement(init: BackendInit) -> _FakeBackend:
        backend = _FakeBackend(init)
        backend.initialize_entered = entered
        backend.initialize_gate = release
        fake_backends.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", build_replacement)
    changing = asyncio.create_task(manager.set_mode("edit"))
    await entered.wait()

    with pytest.raises(ChatBackendUnavailableError):
        await manager.send_message("keep this draft")

    assert (
        session.seq,
        session.turn_count,
        session.title,
        session.pending_preamble,
        session.pending_mode_notice,
        [(row.type, row.text) for row in session.transcript],
    ) == (
        before[0],
        before[1],
        before[2],
        True,
        False,
        before[5],
    )
    assert session.turn_task is None
    release.set()
    assert (await changing)["mode"] == "edit"

    await manager.send_message("retry after rebuild")
    await _wait_turn(manager)
    assert fake_backends[-1].turns[-1][0].endswith("retry after rebuild")
    await manager.stop_session()


async def test_stop_waits_for_mode_rebuild_then_stops_replacement(
    tmp_path: Path,
    fake_backends: list[_FakeBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    session_id = (await manager.start_session("qa"))["session_id"]
    entered = asyncio.Event()
    release = asyncio.Event()

    def build_replacement(init: BackendInit) -> _FakeBackend:
        backend = _FakeBackend(init)
        backend.initialize_entered = entered
        backend.initialize_gate = release
        fake_backends.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", build_replacement)
    changing = asyncio.create_task(manager.set_mode("edit", session_id))
    await entered.wait()
    stopping = asyncio.create_task(manager.stop_session(session_id))
    await asyncio.sleep(0)
    try:
        assert stopping.done() is False
    finally:
        release.set()
    assert (await changing)["mode"] == "edit"
    await stopping

    assert manager.session(session_id) is None
    assert all(backend.stopped for backend in fake_backends)


async def test_failed_mode_rebuild_moves_session_to_explicit_resume(
    tmp_path: Path,
    fake_backends: list[_FakeBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    session_id = (await manager.start_session("qa"))["session_id"]
    await manager.send_message("remember this")
    await _wait_turn(manager)

    def build_broken(init: BackendInit) -> _FakeBackend:
        backend = _FakeBackend(init)
        backend.initialize_error = RuntimeError("cannot initialize")
        fake_backends.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", build_broken)
    with pytest.raises(RuntimeError, match="cannot initialize"):
        await manager.set_mode("edit", session_id)

    assert manager.session(session_id) is None
    assert [row["session_id"] for row in manager.list_sessions()["resumable"]] == [
        session_id
    ]

    def build_ready(init: BackendInit) -> _FakeBackend:
        backend = _FakeBackend(init)
        fake_backends.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", build_ready)
    resumed = await manager.reattach(session_id)
    assert resumed["mode"] == "qa"
    assert any(
        row["type"] == "agent_message" and row["text"] == "answer 1"
        for row in resumed["transcript_tail"]
    )
    await manager.stop_session()


async def test_turn_failure_is_broadcast(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    fake_backends[0].raise_on_turn = TurnTimeout("turn timed out")

    await manager.send_message("hello")
    await _wait_turn(manager)
    session = manager.active_session
    assert session is not None
    failed = [m for m in session.transcript if m.type == "turn_failed"]
    assert failed and "turn_timeout" in failed[0].text
    # Session survives a failed turn.
    assert manager.snapshot()["active"] is True
    await manager.stop_session()


async def test_set_mode_rebuilds_with_resume_and_resets_continuation(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    await manager.send_message("q1")
    await _wait_turn(manager)

    result = await manager.set_mode("edit")
    assert result["mode"] == "edit"
    assert result["context_preserved"] is True
    assert fake_backends[0].stopped is True
    new_backend = fake_backends[1]
    assert "--permission-mode acceptEdits" in new_backend.init.cfg.claude.command
    assert "--resume sess-1" in new_backend.init.cfg.claude.command

    await manager.send_message("now edit something")
    await _wait_turn(manager)
    _, is_continuation = new_backend.turns[0]
    assert is_continuation is False  # fresh backend instance
    await manager.stop_session()


async def test_subscribers_receive_fanout_and_close_sends_sentinel(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    q1 = manager.subscribe()
    q2 = manager.subscribe()

    await manager.send_message("ping")
    await _wait_turn(manager)
    first = q1.get_nowait()
    assert first["type"] == "user_message"
    assert first["text"] == "ping"
    assert q2.get_nowait()["type"] == "user_message"

    await manager.close()
    # Drain until the shutdown sentinel arrives.
    sentinel_seen = False
    while not q1.empty():
        if q1.get_nowait() is None:
            sentinel_seen = True
    assert sentinel_seen


async def test_transcript_jsonl_written(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    snapshot = await manager.start_session("qa")
    await manager.send_message("persist me")
    await _wait_turn(manager)

    path = tmp_path / ".symphony" / "chat" / f"{snapshot['session_id']}.jsonl"
    for _ in range(50):
        if path.exists() and "persist me" in path.read_text(encoding="utf-8"):
            break
        await asyncio.sleep(0.05)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert any(r["type"] == "user_message" and r["text"] == "persist me" for r in rows)
    await manager.stop_session()


# ---------------------------------------------------------------------------
# mode config derivation
# ---------------------------------------------------------------------------


def test_claude_command_for_mode_strips_and_appends() -> None:
    qa = _claude_command_for_mode(CLAUDE_COMMAND, "qa")
    assert qa.count("--permission-mode") == 1
    assert "--permission-mode plan" in qa
    assert "acceptEdits" not in qa
    assert '--add-dir "$SYMPHONY_WORKFLOW_DIR"' in qa

    edit = _claude_command_for_mode("claude -p", "edit")
    assert edit == "claude -p --permission-mode acceptEdits"

    eq_form = _claude_command_for_mode(
        "claude -p --permission-mode=bypassPermissions --verbose", "qa"
    )
    assert "bypassPermissions" not in eq_form
    assert "--verbose" in eq_form

    resumed = _claude_command_for_mode("claude -p", "qa", "sess-9")
    assert resumed.endswith("--resume sess-9")


def test_cfg_for_mode_per_kind(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    claude_cfg, enforced = cfg_for_mode(cfg, "qa", "claude")
    assert enforced is True
    assert "--permission-mode plan" in claude_cfg.claude.command

    codex_qa, enforced = cfg_for_mode(cfg, "qa", "codex")
    assert enforced is True
    assert codex_qa.agent.kind == "codex"
    assert codex_qa.codex.thread_sandbox == "read-only"
    assert codex_qa.codex.turn_sandbox_policy == "read-only"

    codex_edit, enforced = cfg_for_mode(cfg, "edit", "codex")
    assert enforced is True
    assert codex_edit.codex.thread_sandbox == cfg.codex.thread_sandbox

    gemini_cfg, enforced = cfg_for_mode(cfg, "qa", "gemini")
    assert enforced is False
    assert gemini_cfg.agent.kind == "gemini"
    assert gemini_cfg.gemini == cfg.gemini


async def test_request_refresh_called_after_each_turn(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    calls: list[int] = []
    manager = ChatManager(lambda: cfg, request_refresh=lambda: calls.append(1))
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    await manager.send_message("file a ticket for the flaky test")
    await _wait_turn(manager)
    assert calls == [1]
    prompt, _ = fake_backends[0].turns[0]
    assert "Symphony kanban board at" in prompt
    assert "${SYMPHONY_CLI:-symphony} board new" in prompt
    await manager.stop_session()


# ---------------------------------------------------------------------------
# token streaming (claude --include-partial-messages)
# ---------------------------------------------------------------------------


def _delta_frame(text: str, *, delta_type: str = "text_delta") -> dict[str, Any]:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": delta_type, "text": text},
        },
    }


def test_summarize_claude_frame_extracts_text_deltas() -> None:
    assert _summarize_claude_frame(_delta_frame("Hel")) == [
        ("agent_delta", "Hel", {"index": 0})
    ]
    # Reasoning and half-built tool arguments must not reach the bubble.
    assert (
        _summarize_claude_frame(_delta_frame("hm", delta_type="thinking_delta")) == []
    )
    assert _summarize_claude_frame({"type": "stream_event", "event": {}}) == []
    assert _summarize_claude_frame(_delta_frame("")) == []


def test_summarize_codex_frame_omits_nonterminal_and_private_frames() -> None:
    assert _summarize_codex_frame(
        {"type": "agent_delta", "text": "Hel", "item_id": "private-item-id"}
    ) == [("agent_delta", "Hel", {})]
    private_items = [
        {
            "id": "reason-1",
            "type": "reasoning",
            "summary": [{"text": "private summary"}],
            "content": [{"text": "private reasoning"}],
        },
        {
            "id": "user-1",
            "type": "userMessage",
            "content": [{"text": "private user text"}],
        },
        {
            "id": "cmd-1",
            "type": "commandExecution",
            "command": "git status --short",
            "cwd": "/repo",
            "status": "inProgress",
            "aggregatedOutput": "private output",
        },
        {
            "id": "file-1",
            "type": "fileChange",
            "status": "inProgress",
            "changes": [{"path": "src/app.py", "diff": "private diff"}],
        },
        {
            "id": "mcp-1",
            "type": "mcpToolCall",
            "server": "filesystem",
            "tool": "read_file",
            "status": "inProgress",
            "arguments": {"path": "/private"},
        },
        {
            "id": "dynamic-1",
            "type": "dynamicToolCall",
            "tool": "lookup_ticket",
            "status": "inProgress",
            "arguments": {"id": "SECRET-1"},
        },
        {"id": "future-1", "type": "unknownFutureItem", "secret": "raw"},
        {"type": ["commandExecution"], "status": "completed", "command": "raw"},
    ]
    assert all(_summarize_codex_frame({"item": item}) == [] for item in private_items)


def test_summarize_codex_frame_normalizes_terminal_command_statuses_safely() -> None:
    expected_labels = {
        "completed": "command",
        "failed": "command failed",
        "declined": "command declined",
    }
    for status, label in expected_labels.items():
        normalized = _summarize_codex_frame(
            {
                "item": {
                    "id": f"cmd-{status}",
                    "type": "commandExecution",
                    "command": "deploy --api-key=sk-proj-1234567890abcdef",
                    "cwd": "/private/repo",
                    "status": status,
                    "aggregatedOutput": "raw command output",
                    "exitCode": 7,
                }
            }
        )
        assert normalized == [
            (
                "tool_activity",
                label,
                {"detail": "deploy --api-key=[REDACTED]"},
            )
        ]
        rendered = json.dumps(normalized)
        assert "cmd-" not in rendered
        assert "/private/repo" not in rendered
        assert "raw command output" not in rendered
        assert "1234567890abcdef" not in rendered

    long_detail = _summarize_codex_frame(
        {
            "item": {
                "type": "commandExecution",
                "status": "completed",
                "command": "x" * 500,
            }
        }
    )[0][2]["detail"]
    assert len(long_detail.encode("utf-8")) <= 200
    assert long_detail.endswith("…[truncated]")


def test_summarize_codex_frame_normalizes_file_mcp_and_dynamic_activity() -> None:
    frames = [
        (
            {
                "item": {
                    "id": "file-private-id",
                    "type": "fileChange",
                    "status": "completed",
                    "changes": [
                        {
                            "path": "src/app.py",
                            "kind": "update",
                            "diff": "+ password=private-file-secret",
                        },
                        {"path": "tests/test_app.py", "kind": "add", "diff": "+ raw"},
                    ],
                }
            },
            [
                (
                    "tool_activity",
                    "files changed",
                    {"detail": "src/app.py, tests/test_app.py"},
                )
            ],
        ),
        (
            {
                "item": {
                    "id": "mcp-private-id",
                    "type": "mcpToolCall",
                    "server": "filesystem",
                    "tool": "read_file",
                    "status": "failed",
                    "arguments": {"path": "/private/input"},
                    "result": {"content": "private MCP result"},
                    "error": "private MCP error",
                }
            },
            [
                (
                    "tool_activity",
                    "MCP tool failed",
                    {"detail": "filesystem/read_file"},
                )
            ],
        ),
        (
            {
                "item": {
                    "id": "dynamic-private-id",
                    "type": "dynamicToolCall",
                    "tool": "lookup_ticket",
                    "status": "completed",
                    "arguments": {"id": "SECRET-123"},
                    "contentItems": [
                        {"type": "inputText", "text": "private dynamic result"}
                    ],
                    "success": True,
                }
            },
            [
                (
                    "tool_activity",
                    "dynamic tool",
                    {"detail": "lookup_ticket"},
                )
            ],
        ),
    ]
    normalized = []
    for frame, expected in frames:
        activity = _summarize_codex_frame(frame)
        assert activity == expected
        normalized.extend(activity)

    rendered = json.dumps(normalized)
    for private_value in (
        "private-id",
        "private-file-secret",
        "/private/input",
        "private MCP result",
        "private MCP error",
        "SECRET-123",
        "private dynamic result",
    ):
        assert private_value not in rendered


def test_summarize_codex_frame_preserves_final_markdown_without_raw_item() -> None:
    markdown = "## Done\n\n- Updated `src/app.py`\n- Tests pass"
    normalized = _summarize_codex_frame(
        {
            "type": "assistant",
            "message": markdown,
            "item": {
                "id": "agent-private-id",
                "type": "agentMessage",
                "text": markdown,
                "raw": {"reasoning": "private chain of thought"},
            },
        }
    )
    assert normalized == [("agent_message", markdown, {"partial": True})]
    rendered = json.dumps(normalized)
    assert "agent-private-id" not in rendered
    assert "private chain of thought" not in rendered


def test_summarize_pi_frame_streams_only_visible_assistant_text() -> None:
    update = {
        "type": "message_update",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "private chain of thought"},
                {"type": "text", "text": "Hello"},
            ],
        },
    }
    assert _summarize_pi_frame(update) == [("agent_snapshot", "Hello", {})]
    assert (
        _summarize_pi_frame(
            {
                "type": "message_update",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "private"}],
                },
            }
        )
        == []
    )
    assert (
        _summarize_pi_frame(
            {"type": "message_start", "message": {"role": "assistant", "content": []}}
        )
        == []
    )


def test_summarize_pi_frame_finishes_one_persisted_agent_message() -> None:
    message = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "private"},
            {"type": "text", "text": "Final answer"},
        ],
    }
    assert _summarize_pi_frame({"type": "message_end", "message": message}) == [
        ("agent_message", "Final answer", {})
    ]
    # `turn_end` repeats message_end and must not duplicate the transcript row.
    assert _summarize_pi_frame({"type": "turn_end", "message": message}) == []


def test_terminal_agent_message_accepts_pi_agent_end_shape() -> None:
    assert (
        _terminal_agent_message(
            {
                "type": "agent_end",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "question"}]},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "private"},
                            {"type": "text", "text": "answer"},
                        ],
                    },
                ],
            }
        )
        == "answer"
    )


async def test_token_deltas_stream_without_touching_transcript(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    snapshot = await manager.start_session("qa")
    queue = manager.subscribe()
    backend = fake_backends[0]

    await manager.send_message("stream it")
    for chunk in ("Hel", "lo"):
        await backend._emit(EVENT_OTHER_MESSAGE, _delta_frame(chunk))
    await _wait_turn(manager)

    frames: list[dict[str, Any]] = []
    while not queue.empty():
        row = queue.get_nowait()
        if row is not None:
            frames.append(row)
    deltas = [f for f in frames if f["type"] == "agent_delta"]
    assert [f["text"] for f in deltas] == ["Hel", "lo"]
    # Ephemeral: no sequence number, no transcript row, no JSONL line.
    assert all(f["seq"] is None for f in deltas)
    session = manager.active_session
    assert session is not None
    assert "agent_delta" not in [m.type for m in session.transcript]

    path = tmp_path / ".symphony" / "chat" / f"{snapshot['session_id']}.jsonl"
    for _ in range(50):
        if path.exists() and "turn_completed" in path.read_text(encoding="utf-8"):
            break
        await asyncio.sleep(0.05)
    assert "agent_delta" not in path.read_text(encoding="utf-8")
    await manager.stop_session()


# ---------------------------------------------------------------------------
# multiple sessions + reattach
# ---------------------------------------------------------------------------


async def test_sessions_run_independently(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    first = (await manager.start_session("qa"))["session_id"]
    second = (
        await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    )["session_id"]
    assert first != second
    fake_backends[0].gate = asyncio.Event()

    # A blocked turn in the first session must not lock the second.
    await manager.send_message("slow", first)
    await asyncio.sleep(0)
    with pytest.raises(ChatBusyError):
        await manager.send_message("too soon", first)
    await manager.send_message("fast", second)
    await _wait_turn(manager, second)

    assert manager.snapshot(second)["turn_count"] == 1
    assert manager.snapshot(second)["busy"] is False
    # The first session's turn is counted (it is spending) but still running.
    assert manager.snapshot(first)["busy"] is True
    assert not any(
        m.type == "turn_completed"
        for m in manager.session(first).transcript  # type: ignore[union-attr]
    )
    # Transcripts do not bleed across sessions.
    assert not any(
        m.text == "fast"
        for m in manager.session(first).transcript  # type: ignore[union-attr]
    )

    listing = manager.list_sessions()
    assert [s["session_id"] for s in listing["sessions"]] == [first, second]
    assert listing["active_id"] == second
    assert listing["max_sessions"] == chat_module.MAX_SESSIONS
    assert listing["default_agent_kind"] == "claude"
    assert listing["supported_agent_kinds"] == sorted(chat_module.SUPPORTED_AGENT_KINDS)

    fake_backends[0].gate.set()
    await _wait_turn(manager, first)
    await manager.stop_session(second)
    await manager.stop_session(first)


async def test_deltas_only_reach_the_focused_subscriber(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    first = (await manager.start_session("qa"))["session_id"]
    second = (await manager.start_session("qa"))["session_id"]
    watcher = manager.subscribe(second)

    await manager.send_message("hi", first)
    await fake_backends[0]._emit(EVENT_OTHER_MESSAGE, _delta_frame("noise"))
    await _wait_turn(manager, first)

    frames = []
    while not watcher.empty():
        row = watcher.get_nowait()
        if row is not None:
            frames.append(row)
    # Numbered frames fan out to everyone (tagged), deltas do not.
    assert any(f["type"] == "user_message" for f in frames)
    assert all(f["session_id"] == first for f in frames)
    assert not any(f["type"] == "agent_delta" for f in frames)
    await manager.stop_session(first)
    await manager.stop_session(second)


async def test_reattach_restores_display_history_but_starts_fresh_qa(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    session_id = (await manager.start_session("qa"))["session_id"]
    await manager.send_message("remember this")
    await _wait_turn(manager)
    await manager.stop_session()

    # A new manager stands in for a server restart: index/JSONL are display
    # history only and cannot restore an edit-capable backend or agent resume.
    restarted = ChatManager(lambda: cfg)
    listing = restarted.list_sessions()
    assert listing["sessions"] == []
    resumable = [e["session_id"] for e in listing["resumable"]]
    assert session_id in resumable
    assert listing["resumable"][0]["title"] == "remember this"

    snapshot = await restarted.reattach(session_id)
    assert snapshot["active"] is True
    assert snapshot["mode"] == "qa"
    texts = [m["text"] for m in snapshot["transcript_tail"]]
    assert "remember this" in texts
    assert "answer 1" in texts
    assert "--resume sess-1" not in fake_backends[-1].init.cfg.claude.command

    await restarted.send_message("and now?")
    await _wait_turn(restarted)
    prompt, _ = fake_backends[-1].turns[0]
    assert "do not create, modify or delete" in prompt
    assert prompt.endswith("and now?")
    await restarted.stop_session()


async def test_reattach_uses_configured_agent_not_untrusted_index_kind(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    session_id = (await manager.start_session("qa", agent_kind="gemini"))["session_id"]
    await manager.send_message("first")
    await _wait_turn(manager)
    await manager.stop_session()

    restarted = ChatManager(lambda: cfg)
    snapshot = await restarted.reattach(session_id)
    assert snapshot["agent_kind"] == cfg.agent.kind
    assert snapshot["mode"] == "qa"
    await restarted.send_message("second")
    await _wait_turn(restarted)
    prompt, is_continuation = fake_backends[-1].turns[0]
    assert "do not create, modify or delete" in prompt
    assert prompt.endswith("second")
    assert is_continuation is False
    await restarted.stop_session()


async def test_reattach_ignores_forged_edit_mode_kind_and_resume_in_index(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    session_id = (await manager.start_session("qa"))["session_id"]
    await manager.stop_session()
    index_path = tmp_path / ".symphony" / "chat" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = index["sessions"][0]
    entry.update({"mode": "edit", "agent_kind": "pi", "agent_session_id": "forged"})
    index_path.write_text(json.dumps(index), encoding="utf-8")

    restarted = ChatManager(lambda: cfg)
    snapshot = await restarted.reattach(session_id)

    assert snapshot["mode"] == "qa"
    assert snapshot["agent_kind"] == cfg.agent.kind
    assert "--resume forged" not in fake_backends[-1].init.cfg.claude.command
    await restarted.stop_session()


def test_untrusted_chat_json_rejects_huge_and_unsafe_sequence_values(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "chat.jsonl"
    transcript.write_text(
        '{"seq":'
        + "9" * 5_000
        + "}\n"
        + '{"seq":9007199254740992}\n'
        + '{"seq":true}\n'
        + '{"seq":1,"type":"agent_message","text":"safe"}\n',
        encoding="utf-8",
    )

    rows = chat_module._load_transcript(transcript)

    assert [(row.seq, row.text) for row in rows] == [(1, "safe")]


def test_untrusted_chat_index_rejects_huge_json_integer(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    index = tmp_path / ".symphony" / "chat" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(
        '{"sessions":[{"turn_count":' + "9" * 5_000 + "}]}", encoding="utf-8"
    )

    manager = ChatManager(lambda: cfg)

    assert manager.list_sessions()["resumable"] == []


async def test_reattach_rejects_unknown_session(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    with pytest.raises(ChatNoSessionError):
        await manager.reattach("20260806-000000-abcdef")


async def test_stop_with_forget_drops_the_index_entry(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    session_id = (await manager.start_session("qa"))["session_id"]
    await manager.stop_session(forget=True)
    assert manager.list_sessions()["resumable"] == []
    # The transcript itself stays on disk as an audit trail.
    assert (tmp_path / ".symphony" / "chat" / f"{session_id}.jsonl").exists()


# ---------------------------------------------------------------------------
# advisory budget
# ---------------------------------------------------------------------------


async def test_turn_completed_carries_the_current_turn_count(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    """The frame is emitted mid-turn; its budget must not lag a turn behind."""
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa", max_turns=2, max_tokens=0)
    await manager.send_message("one")
    await _wait_turn(manager)
    session = manager.active_session
    assert session is not None
    completed = [m for m in session.transcript if m.type == "turn_completed"]
    assert completed[-1].meta["budget"]["turn_count"] == 1
    assert completed[-1].meta["budget"]["exceeded"] is False

    await manager.send_message("two")
    await _wait_turn(manager)
    completed = [m for m in session.transcript if m.type == "turn_completed"]
    assert completed[-1].meta["budget"]["turn_count"] == 2
    assert completed[-1].meta["budget"]["exceeded"] is True
    await manager.stop_session()


async def test_budget_warns_once_and_never_blocks(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa", max_turns=1, max_tokens=0)

    await manager.send_message("first")
    await _wait_turn(manager)
    session = manager.active_session
    assert session is not None
    assert session.budget_exceeded() is True
    warnings = [
        m
        for m in session.transcript
        if m.type == "session_status" and "chat budget reached" in m.text
    ]
    assert len(warnings) == 1
    assert warnings[0].meta["budget"]["max_turns"] == 1

    # Advisory only: the next message still runs, and warns only once.
    await manager.send_message("second")
    await _wait_turn(manager)
    assert len(fake_backends[0].turns) == 2
    assert (
        len(
            [
                m
                for m in session.transcript
                if m.type == "session_status" and "chat budget reached" in m.text
            ]
        )
        == 1
    )
    assert manager.snapshot()["budget"]["exceeded"] is True
    await manager.stop_session()


async def test_token_totals_survive_a_backend_rebuild(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    """A mode switch drops the backend; its cumulative counter restarts at 0."""
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa", max_turns=0, max_tokens=0)
    await manager.send_message("q1")
    await _wait_turn(manager)
    session = manager.active_session
    assert session is not None
    assert session.used_tokens == 7  # _FakeBackend reports 7 cumulative

    await manager.set_mode("edit")
    await manager.send_message("q2")
    await _wait_turn(manager)
    assert session.used_tokens == 14
    await manager.stop_session()


async def test_mode_switch_prepends_notice_on_next_message(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    await manager.send_message("q1")
    await _wait_turn(manager)

    await manager.set_mode("edit")
    await manager.send_message("now file a ticket")
    await _wait_turn(manager)
    prompt, _ = fake_backends[1].turns[0]
    assert prompt.startswith("[Chat mode changed to edit")
    assert prompt.endswith("now file a ticket")

    # The notice is one-shot.
    await manager.send_message("another message")
    await _wait_turn(manager)
    prompt, _ = fake_backends[1].turns[1]
    assert prompt == "another message"
    await manager.stop_session()


# ---------------------------------------------------------------------------
# server-owned project setup choices
# ---------------------------------------------------------------------------


def test_project_setup_marker_with_invalid_path_stays_plain_text() -> None:
    raw = (
        '<symphony-project-setup>{"choice": 1, "name": "Bad", '
        '"path": "/tmp/\\u0000bad"}</symphony-project-setup>'
    )

    visible, proposal = chat_module._project_setup_spec(raw)

    assert visible == raw
    assert proposal is None


def test_project_setup_marker_rejects_duplicate_json_members() -> None:
    raw = (
        '<symphony-project-setup>{"choice": 1, "name": "Bad", '
        '"path": "/tmp/first", "path": "/tmp/second"}'
        "</symphony-project-setup>"
    )

    visible, proposal = chat_module._project_setup_spec(raw)

    assert visible == raw
    assert proposal is None


def test_project_setup_repeated_unclosed_markers_stay_plain_text() -> None:
    raw = "<symphony-project-setup>{" * 10_000

    visible, proposal = chat_module._project_setup_spec(raw)

    assert visible == raw
    assert proposal is None


def test_project_setup_marker_with_symlink_loop_stays_plain_text(
    tmp_path: Path,
) -> None:
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(loop)
    except OSError:
        pytest.skip("symlinks unavailable")
    raw = (
        "<symphony-project-setup>"
        + json.dumps({"choice": 1, "name": "Loop", "path": str(loop)})
        + "</symphony-project-setup>"
    )

    visible, proposal = chat_module._project_setup_spec(raw)

    assert visible == raw
    assert proposal is None


def test_project_setup_proposal_discloses_server_observed_operation(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    git_repo = tmp_path / "git-repo"
    subprocess.run(
        ["git", "init", "-b", "main", str(git_repo)], check=True, capture_output=True
    )

    def proposal_for(path: Path) -> Any:
        raw = (
            "<symphony-project-setup>"
            + json.dumps({"choice": 1, "name": path.name, "path": str(path)})
            + "</symphony-project-setup>"
        )
        _visible, proposal = chat_module._project_setup_spec(raw)
        assert proposal is not None
        return proposal

    assert proposal_for(tmp_path / "missing").operation == "create"
    assert proposal_for(existing).operation == "initialize"
    assert proposal_for(git_repo).operation == "adopt"


async def test_project_setup_choice_is_explicit_and_idempotent(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    calls: list[tuple[str, Path]] = []

    def create_project(name: str, path: Path, *, expected_target: Any | None = None):
        assert expected_target is not None and expected_target.repo == path
        calls.append((name, path))
        from symphony.projects import Project

        return Project(
            "todo-app",
            name,
            str(path),
            str(path / "WORKFLOW.md"),
            "127.0.0.1",
            10000,
        )

    manager = ChatManager(lambda: cfg, project_creator=create_project)
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(
        session,
        "1. Create a separate Todo app.\n"
        + _setup_marker({"choice": 1, "name": "Todo App", "path": str(tmp_path / "todo-app")}),
    )

    action = manager.project_setup_for_choice("1")
    assert action is not None
    assert action.name == "Todo App"
    assert [message.type for message in session.transcript[-2:]] == [
        "agent_message",
        "project_setup_action",
    ]
    assert "symphony-project-setup" not in session.transcript[-2].text
    assert manager.snapshot()["project_setup_actions"] == [action.as_dict()]

    first, second = await asyncio.gather(
        manager.confirm_project_setup(
            action.action_id, confirmation_token=CONFIRMATION_TOKEN
        ),
        manager.confirm_project_setup(
            action.action_id, confirmation_token=CONFIRMATION_TOKEN
        ),
    )
    assert first["status"] == second["status"] == "succeeded"
    assert first["project"] == {
        "id": "todo-app",
        "name": "Todo App",
        "repo_path": str(tmp_path / "todo-app"),
        "workflow_path": str(tmp_path / "todo-app" / "WORKFLOW.md"),
        "host": "127.0.0.1",
        "port": 10000,
    }
    assert calls == [("Todo App", tmp_path / "todo-app")]
    await manager.stop_session()


async def test_default_project_setup_creates_registered_board(
    tmp_path: Path, fake_backends: list[_FakeBackend], monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.projects import ProjectRegistry

    registry_path = tmp_path / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(registry_path))
    source = tmp_path / "source"
    source.mkdir()
    cfg = _cfg(source)
    target = tmp_path / "todo-app"
    manager = ChatManager(lambda: cfg)
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(
        session,
        _setup_marker({"choice": 1, "name": "Todo App", "path": str(target)}),
    )
    action = manager.project_setup_for_choice("1")
    assert action is not None
    result = await manager.confirm_project_setup(
        action.action_id, confirmation_token=CONFIRMATION_TOKEN
    )
    assert result["status"] == "succeeded"
    assert (target / ".git").exists()
    assert (target / "WORKFLOW.md").is_file()
    assert (target / "kanban").is_dir()
    assert not list((source / "kanban").glob("*.md"))
    projects = ProjectRegistry().load()
    assert [(project.id, Path(project.git_repo)) for project in projects] == [
        ("todo-app", target)
    ]
    await manager.stop_session()


async def test_edit_preamble_separates_project_rules_from_user_text(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("edit")

    await manager.send_message("offer a project")
    await _wait_turn(manager)

    prompt, _ = fake_backends[-1].turns[0]
    assert "operator explicitly asks.\n\noffer a project" in prompt
    await manager.stop_session()


async def test_project_setup_rejects_duplicate_live_choice_numbers(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(
        session,
        _setup_marker({"choice": 1, "name": "First", "path": str(tmp_path / "first")}),
    )
    first = manager.project_setup_for_choice("1")
    assert first is not None
    manager._record_agent_message(
        session,
        _setup_marker({"choice": 1, "name": "Second", "path": str(tmp_path / "second")}),
    )

    assert list(session.project_setup_actions) == [first.action_id]
    assert manager.project_setup_for_choice("1") is first
    assert "option number is already in use" in session.transcript[-1].text
    await manager.stop_session()


async def test_project_setup_prunes_terminal_card_before_adding_new_proposal(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    for choice in range(1, 21):
        action = chat_module.ProjectSetupAction(
            action_id=f"project-{choice:032x}",
            choice=choice,
            name=f"Project {choice}",
            path=str(tmp_path / f"project-{choice}"),
            status="succeeded" if choice == 1 else "pending",
        )
        session.project_setup_actions[action.action_id] = action

    manager._record_agent_message(
        session,
        _setup_marker({"choice": 99, "name": "Fresh", "path": str(tmp_path / "fresh")}),
    )

    assert len(session.project_setup_actions) == 20
    assert (
        "project-00000000000000000000000000000001" not in session.project_setup_actions
    )
    assert manager.project_setup_for_choice("99") is not None
    await manager.stop_session()


async def test_project_setup_rejects_git_topology_change_after_proposal(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    calls: list[tuple[str, Path]] = []

    def create_project(
        name: str, path: Path, *, expected_target: Any | None = None
    ) -> None:
        calls.append((name, path))

    parent = tmp_path / "target-parent"
    target = parent / "nested"
    target.mkdir(parents=True)
    manager = ChatManager(lambda: cfg, project_creator=create_project)
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(
        session,
        _setup_marker({"choice": 1, "name": "Nested", "path": str(target)}),
    )
    action = manager.project_setup_for_choice("1")
    assert action is not None and action.path == str(target)
    subprocess.run(
        ["git", "init", "-b", "main", str(parent)], check=True, capture_output=True
    )

    result = await manager.confirm_project_setup(
        action.action_id, confirmation_token=CONFIRMATION_TOKEN
    )
    assert result["status"] == "failed"
    assert result["error"].endswith(
        "project target changed; request a new project setup proposal"
    )
    assert calls == []
    await manager.stop_session()


async def test_project_setup_rejects_git_created_at_same_confirmed_root(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    calls: list[tuple[str, Path]] = []

    def create_project(
        name: str, path: Path, *, expected_target: Any | None = None
    ) -> None:
        calls.append((name, path))

    target = tmp_path / "target"
    target.mkdir()
    manager = ChatManager(lambda: cfg, project_creator=create_project)
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(
        session,
        _setup_marker({"choice": 1, "name": "Target", "path": str(target)}),
    )
    action = manager.project_setup_for_choice("1")
    assert action is not None
    subprocess.run(
        ["git", "init", "-b", "main", str(target)], check=True, capture_output=True
    )

    result = await manager.confirm_project_setup(
        action.action_id, confirmation_token=CONFIRMATION_TOKEN
    )

    assert result["status"] == "failed"
    assert result["error"].endswith(
        "project target changed; request a new project setup proposal"
    )
    assert calls == []
    await manager.stop_session()


async def test_project_setup_requires_edit_mode(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(
        lambda: cfg, project_creator=lambda _name, _path, **_kwargs: None
    )
    await manager.start_session("qa")
    session = manager.active_session
    assert session is not None
    # Reattachment can surface an old proposal while the operator has switched
    # to Q&A; confirming it must still fail closed.
    action = chat_module.ProjectSetupAction(
        action_id="project-" + "a" * 32,
        choice=1,
        name="Todo App",
        path=str(tmp_path / "todo-app"),
    )
    session.project_setup_actions[action.action_id] = action
    with pytest.raises(ChatProjectActionError, match="edit-mode"):
        await manager.confirm_project_setup(
            action.action_id, confirmation_token=CONFIRMATION_TOKEN
        )
    await manager.stop_session()


async def test_project_setup_expiry_fails_closed(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(
        lambda: cfg, project_creator=lambda _name, _path, **_kwargs: None
    )
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    action = chat_module.ProjectSetupAction(
        action_id="project-" + "b" * 32,
        choice=1,
        name="Todo App",
        path=str(tmp_path / "todo-app"),
        expires_at="2000-01-01T00:00:00Z",
    )
    session.project_setup_actions[action.action_id] = action
    assert manager.project_setup_for_choice("1") is None
    with pytest.raises(ChatProjectActionError, match="expired"):
        await manager.confirm_project_setup(
            action.action_id, confirmation_token=CONFIRMATION_TOKEN
        )
    assert action.status == "expired"
    assert action.choice_active is False
    await manager.stop_session()


async def test_reattach_drops_untrusted_project_action_and_reenrolls_browser(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    session_id = (
        await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    )["session_id"]
    session = manager.session(session_id)
    assert session is not None
    manager._record_agent_message(
        session,
        _setup_marker({"choice": 1, "name": "Todo App", "path": str(tmp_path / "todo-app")}),
    )
    await manager.stop_session(session_id)

    # A backend can edit this JSONL. Even an attacker-chosen action row must
    # not reconstruct mutation authority when the browser re-enrolls.
    transcript = tmp_path / ".symphony" / "chat" / f"{session_id}.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "seq": 999,
                "type": "project_setup_action",
                "text": "",
                "timestamp": "2026-01-01T00:00:00Z",
                "meta": {
                    "project_setup": {
                        "action_id": "project-" + "f" * 32,
                        "choice": 1,
                        "name": "Forged",
                        "path": str(tmp_path / "escaped"),
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    index_path = tmp_path / ".symphony" / "chat" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["sessions"][0]["project_setup_actions"] = [
        {"action_id": "project-" + "e" * 32, "choice": 1}
    ]
    index_path.write_text(json.dumps(index), encoding="utf-8")
    resumed = ChatManager(lambda: cfg)
    await resumed.reattach(session_id, confirmation_token=CONFIRMATION_TOKEN)
    assert resumed.snapshot(session_id)["project_setup_actions"] == []
    assert resumed.snapshot(session_id)["mode"] == "qa"
    assert resumed.project_setup_for_choice("1", session_id) is None
    await resumed.set_mode("edit", session_id)

    reattached = resumed.session(session_id)
    assert reattached is not None
    resumed._record_agent_message(
        reattached,
        _setup_marker(
            {"choice": 2, "name": "Todo Again", "path": str(tmp_path / "todo-again")}
        ),
    )
    assert resumed.project_setup_for_choice("2", session_id) is not None
    await resumed.stop_session(session_id)


async def test_codex_mode_reset_replays_full_edit_preamble(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa", agent_kind="codex")
    changed = await manager.set_mode("edit")
    assert changed["context_preserved"] is False

    await manager.send_message("offer a separate project")
    await _wait_turn(manager)
    prompt, is_continuation = fake_backends[1].turns[0]
    assert is_continuation is False
    assert prompt.startswith("You are pair-working with the operator")
    assert "A separate Symphony project is a control-plane action" in prompt
    assert "symphony-project-setup" in prompt
    await manager.stop_session()


# ---------------------------------------------------------------------------
# board preamble — build-request protocol
# ---------------------------------------------------------------------------


def _cfg_with_states(tmp_path: Path, active_states: str) -> ServiceConfig:
    (tmp_path / "WORKFLOW.md").write_text(
        WORKFLOW_TEXT.replace("[Todo, Doing]", active_states), encoding="utf-8"
    )
    (tmp_path / "kanban").mkdir(exist_ok=True)
    state = WorkflowState(tmp_path / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    return cfg


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only: spawns /bin/sh directly; Windows has no /bin/sh "
    "(the SYMPHONY_CLI fallback is exercised by resolve_symphony_cli tests)",
)
def test_board_cli_fallback_runs_with_stripped_path() -> None:
    """The documented shell expansion must not depend on ambient PATH."""

    from symphony.orchestrator.helpers import resolve_symphony_cli

    env = {"PATH": "", "SYMPHONY_CLI": resolve_symphony_cli()}
    result = subprocess.run(
        ["/bin/sh", "-c", "${SYMPHONY_CLI:-symphony} board --help"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "board" in result.stdout.lower()


def test_board_preamble_default_board_routes_by_complexity(tmp_path: Path) -> None:
    preamble = _board_preamble(_cfg(tmp_path))
    # Validated CLI protocol, rendered with the board's actual states.
    assert "${SYMPHONY_CLI:-symphony} board new" in preamble
    # F-19: the CLI may live in a venv the worker's PATH does not carry.
    assert "SYMPHONY_CLI is exported by the orchestrator" in preamble
    assert "board update <ID>" in preamble
    assert "--description-file -" in preamble
    assert "Todo, Doing" in preamble
    assert "SIMPLE task: one ticket in Todo" in preamble
    assert "research -> plan -> adversarial plan-review" in preamble
    # No freehand ticket-markdown instruction survives.
    assert "<IDENTIFIER>.md" not in preamble
    assert "front matter" not in preamble


def test_board_preamble_deep_board_files_one_intake_ticket(tmp_path: Path) -> None:
    cfg = _cfg_with_states(
        tmp_path,
        "[Intake, Research, Plan, Review, Build, QA, Verify, Document]",
    )
    preamble = _board_preamble(cfg)
    assert "ONE Intake ticket" in preamble
    assert "Intake, Research, Plan" in preamble
    # On a deep board the pipeline decomposes; chat does not build the DAG.
    assert "adversarial plan-review" not in preamble
