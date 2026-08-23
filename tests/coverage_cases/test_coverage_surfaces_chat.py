"""Coverage contracts for operator chat lifecycle."""
# ruff: noqa: F405

from tests.coverage_cases._surfaces_support import *  # noqa: F403


def test_chat_projection_hides_private_frames_and_summarizes_safe_activity() -> None:
    assert chat._project_setup_is_expired("not-a-timestamp") is True
    assert chat._assistant_text({"role": "user", "content": "private"}) == ""
    assert (
        chat._assistant_text({"role": "assistant", "content": " answer "}) == "answer"
    )
    assert (
        chat._assistant_text({"role": "assistant", "text": " fallback "}) == "fallback"
    )
    assert chat._terminal_agent_message({"messages": "not-a-list"}) == ""
    assert (
        chat._terminal_agent_message(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "thinking", "text": "secret"}],
                    },
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "safe"}],
                    },
                ]
            }
        )
        == "safe"
    )

    assert chat._summarize_claude_frame({"type": "assistant", "message": {}}) == []
    claude = chat._summarize_claude_frame(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [{"type": "text", "text": "done"}],
                    }
                ]
            },
        }
    )
    assert claude == [("tool_activity", "result", {"detail": "done"})]

    assert chat._summarize_pi_frame(
        {
            "type": "tool_execution_start",
            "toolName": "read",
            "args": {"path": "README.md"},
        }
    )[0][:2] == ("tool_activity", "read")
    assert chat._summarize_pi_frame(
        {"type": "tool_execution_end", "tool": "read", "result": "done"}
    ) == [("tool_activity", "read result", {"detail": "done"})]
    assert chat._tool_result_preview({"content": 3}) == ""


def test_chat_project_setup_marker_fails_closed_on_malformed_proposals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opening = chat._PROJECT_SETUP_OPEN
    closing = chat._PROJECT_SETUP_CLOSE
    malformed = [
        f"{closing}{opening}",
        f"{opening}{'x' * (chat._PROJECT_SETUP_MAX_PAYLOAD + 1)}{closing}",
        f"{opening}{json.dumps({'choice': 1})}{closing}",
        f"{opening}{json.dumps({'choice': True, 'name': 'Demo', 'path': str(tmp_path)})}{closing}",
    ]
    for text in malformed:
        assert chat._project_setup_spec(text) == (text, None)

    monkeypatch.setattr(
        chat,
        "project_target_expectation",
        lambda _path: (_ for _ in ()).throw(OSError("unavailable")),
    )
    proposal = (
        opening
        + json.dumps({"choice": 1, "name": "Demo", "path": str(tmp_path.resolve())})
        + closing
    )
    assert chat._project_setup_spec(proposal) == (proposal, None)


def test_chat_codex_projection_allows_only_bounded_public_details() -> None:
    assert chat._codex_safe_detail(None) == ""
    assert chat._codex_file_change_detail("private diff") == ""
    changes = [{"path": f"file-{index}.py"} for index in range(7)] + ["ignored"]
    assert chat._codex_file_change_detail(changes) == (
        "file-0.py, file-1.py, file-2.py, file-3.py, file-4.py, …"
    )
    assert chat._codex_tool_detail({"type": "mcpToolCall", "server": "git"}) == "git"
    assert chat._summarize_codex_frame({"type": "agent_delta", "text": ""}) == []
    assert chat._summarize_codex_frame({"item": "private"}) == []
    assert chat._summarize_codex_frame(
        {
            "item": {
                "type": "dynamicToolCall",
                "status": "completed",
                "success": False,
                "tool": "deploy",
            }
        }
    ) == [("tool_activity", "dynamic tool failed", {"detail": "deploy"})]
    assert (
        chat._summarize_codex_frame(
            {"item": {"type": "commandExecution", "status": "completed", "command": ""}}
        )
        == []
    )


def test_chat_transcript_replay_tolerates_missing_and_malformed_rows(
    tmp_path: Path,
) -> None:
    assert chat._load_transcript(tmp_path / "missing.jsonl") == []
    transcript = tmp_path / "chat.jsonl"
    transcript.write_text(
        "\nnot-json\n"
        + json.dumps({"seq": True, "text": "invalid"})
        + "\n"
        + json.dumps({"seq": 2, "type": "agent_message", "text": "safe", "meta": []})
        + "\n",
        encoding="utf-8",
    )
    messages = chat._load_transcript(transcript)
    assert [message.as_dict() for message in messages] == [
        {
            "seq": 2,
            "type": "agent_message",
            "text": "safe",
            "timestamp": "",
            "meta": {},
        }
    ]


@pytest.mark.asyncio
async def test_chat_manager_closed_and_unknown_session_boundaries(
    tmp_path: Path,
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        agent=SimpleNamespace(kind="codex"),
    )
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    manager._closed = True
    with pytest.raises(chat.ChatNoSessionError, match="shut down"):
        await manager.start_session("qa")
    with pytest.raises(chat.ChatNoSessionError, match="shut down"):
        await manager.reattach("20260824-120000-abcdef")

    manager._closed = False
    with pytest.raises(chat.ChatNoSessionError, match="no live chat session"):
        manager._resolve("20260824-120000-missing")
    cfg.agent.kind = "unsupported"
    with pytest.raises(chat.SymphonyError, match="unsupported agent kind"):
        await manager.start_session("qa")


def test_chat_budget_reports_both_exhausted_dimensions() -> None:
    session = _chat_session(
        max_turns=2, turn_count=2, max_tokens=1_000, used_tokens=1_250
    )
    assert session.budget_exceeded() is True
    assert session.budget_reason() == "2/2 turns and 1,250/1,000 tokens"


def test_chat_transcript_writer_degrades_after_shutdown_and_io_failure(
    tmp_path: Path,
) -> None:
    writer = chat._TranscriptWriter(tmp_path / "chat.jsonl")
    writer.close()
    writer.append({"seq": 1})

    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("file", encoding="utf-8")
    failed = chat._TranscriptWriter(blocked_parent / "chat.jsonl")
    try:
        failed._write_line("first")
        failed._write_line("second")
        assert failed._failed_logged is True
    finally:
        failed.close()


@pytest.mark.asyncio
async def test_chat_session_events_are_bounded_and_private_when_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    session = _chat_session(max_tokens=10, used_tokens=10)

    await manager._on_backend_event(
        session,
        {"event": chat.EVENT_SESSION_STARTED, "payload": {"session_id": "private"}},
    )
    assert session.transcript == []

    manager._sessions[session.session_id] = session
    manager._active_id = session.session_id
    assert manager.active_session_id == session.session_id
    await manager._run_turn(session, "hello")
    assert session.transcript[-1].text == "no backend for session"

    await manager._on_backend_event(
        session,
        {"event": chat.EVENT_SESSION_STARTED, "payload": {"session_id": "agent-7"}},
    )
    assert session.agent_session_id == "agent-7"
    await manager._on_backend_event(
        session,
        {"event": chat.EVENT_TURN_FAILED, "payload": {"error": "backend failed"}},
    )
    assert session.turn_failure_broadcast is True
    assert session.transcript[-1].text == "backend failed"

    used_before = session.used_tokens
    manager._accumulate_usage(session, {"total_tokens": "invalid"})
    assert session.used_tokens == used_before

    monkeypatch.setattr(chat, "TRANSCRIPT_LIMIT", 2)
    manager._broadcast(session, "session_status", "one")
    manager._broadcast(session, "session_status", "two")
    assert [row.text for row in session.transcript] == ["one", "two"]


def test_chat_slow_or_broken_subscribers_never_block_a_turn(tmp_path: Path) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    manager._active_id = "active"

    class BrokenQueue(asyncio.Queue[dict[str, Any] | None]):
        def full(self) -> bool:
            return True

        def get_nowait(self) -> dict[str, Any] | None:
            raise asyncio.QueueEmpty

        def put_nowait(self, item: dict[str, Any] | None) -> None:
            del item
            raise asyncio.QueueFull

    queue = BrokenQueue(maxsize=1)
    manager._subscribers[queue] = None
    manager._push({"session_id": "active", "type": "message"})
    manager._push(
        {"session_id": "background", "type": "agent_delta"}, focused_only=True
    )


def test_chat_index_reader_rejects_oversized_and_wrong_shaped_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    index = manager._chat_dir() / chat._INDEX_NAME
    index.parent.mkdir(parents=True)
    index.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(chat, "_MAX_INDEX_BYTES", 1)
    assert manager._read_index() == []

    monkeypatch.setattr(chat, "_MAX_INDEX_BYTES", 1_000)
    index.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
    assert manager._read_index() == []
    assert chat._as_int(True, 7) == 7
    assert chat._as_int("not-an-int", 8) == 8


@pytest.mark.asyncio
async def test_chat_message_and_project_choice_boundaries_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        tracker=SimpleNamespace(kind="linear", board_root=None),
    )
    assert chat._board_preamble(cfg) == ""  # type: ignore[arg-type]
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    session = _chat_session(backend=SimpleNamespace())
    manager._sessions[session.session_id] = session
    manager._active_id = session.session_id
    with pytest.raises(chat.SymphonyError, match="message text is required"):
        await manager.send_message("   ", session.session_id)

    action = chat.ProjectSetupAction(
        action_id="project-" + "a" * 32,
        choice=1,
        name="Demo",
        path=str(tmp_path),
        expires_at="2000-01-01T00:00:00Z",
    )
    session.mode = "edit"
    session.project_setup_actions[action.action_id] = action
    assert manager.project_setup_for_choice("not-a-number", session.session_id) is None
    assert manager.project_setup_for_choice("01", session.session_id) is None
    manager._expire_project_setup_actions(session)
    assert action.status == "expired"
    monkeypatch.setattr(chat, "_MAX_PROJECT_ACTIONS_PER_SESSION", 0)
    assert manager._prune_project_setup_actions(session) == [action.action_id]


@pytest.mark.asyncio
async def test_chat_project_setup_without_target_binding_fails_without_mutation(
    tmp_path: Path,
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    session = _chat_session(mode="edit")
    action = chat.ProjectSetupAction(
        action_id="project-" + "b" * 32,
        choice=2,
        name="Demo",
        path=str(tmp_path),
        target_expectation=None,
    )
    await manager._run_project_setup(session, action)
    assert action.status == "failed"
    assert "target binding is unavailable" in str(action.error)


@pytest.mark.asyncio
async def test_chat_start_failure_removes_unusable_live_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        agent=SimpleNamespace(kind="codex"),
    )
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]

    async def fail_backend(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("backend failed")

    monkeypatch.setattr(manager, "_build_backend", fail_backend)
    with pytest.raises(RuntimeError, match="backend failed"):
        await manager.start_session("qa")
    assert manager.live_count == 0


@pytest.mark.asyncio
async def test_chat_stop_cancels_turn_waits_actions_and_tolerates_backend_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]

    class Backend:
        async def stop(self) -> None:
            raise RuntimeError("already gone")

    session = _chat_session(backend=Backend())
    session.turn_task = asyncio.create_task(asyncio.sleep(60))
    action = chat.ProjectSetupAction(
        action_id="project-" + "c" * 32,
        choice=3,
        name="Demo",
        path=str(tmp_path),
    )
    action.task = asyncio.create_task(asyncio.sleep(0))
    session.project_setup_actions[action.action_id] = action
    manager._sessions[session.session_id] = session
    manager._active_id = session.session_id
    monkeypatch.setattr(manager, "_save_index", lambda: None)
    await manager.stop_session(session.session_id)
    assert manager.live_count == 0


@pytest.mark.asyncio
async def test_chat_mode_and_confirmation_failures_remain_explicit(
    tmp_path: Path,
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    session = _chat_session()
    manager._sessions[session.session_id] = session
    assert await manager.set_mode("qa", session.session_id) == {
        "mode": "qa",
        "context_preserved": True,
        "mode_enforced": True,
    }
    await session.turn_lock.acquire()
    try:
        with pytest.raises(chat.ChatBusyError, match="turn is running"):
            await manager.set_mode("edit", session.session_id)
    finally:
        session.turn_lock.release()
    with pytest.raises(
        chat.ChatProjectActionError, match="unknown project setup action"
    ):
        await manager.confirm_project_setup("project-" + "d" * 32, session.session_id)


def test_chat_frame_projection_handles_sparse_public_shapes() -> None:
    assert chat._terminal_agent_message({"messages": []}) == ""
    assert (
        chat._summarize_claude_frame(
            {"type": "assistant", "message": {"content": ["not-a-block"]}}
        )
        == []
    )
    assert chat._tool_result_preview({"content": " result "}) == "result"
    assert (
        chat._codex_file_change_detail(
            ["not-a-change", {"path": ""}, {"path": "README.md"}]
        )
        == "README.md"
    )


def test_chat_transcript_tail_seek_discards_partial_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = tmp_path / "large.jsonl"
    transcript.write_text(
        json.dumps({"seq": 1, "text": "old"})
        + "\n"
        + json.dumps({"seq": 2, "text": "new"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(chat, "_REPLAY_TAIL_BYTES", 30)
    assert [message.seq for message in chat._load_transcript(transcript)] == [2]


@pytest.mark.asyncio
async def test_chat_reattach_guards_live_limit_shutdown_kind_and_backend_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        agent=SimpleNamespace(kind="codex"),
    )
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    session = _chat_session()
    manager._sessions[session.session_id] = session
    assert (await manager.reattach(session.session_id))["active"] is True

    monkeypatch.setattr(chat, "MAX_SESSIONS", 1)
    with pytest.raises(chat.ChatSessionExistsError, match="session limit"):
        await manager.reattach("20260824-120000-other1")

    manager._sessions.clear()

    def close_during_lookup(_session_id: str) -> dict[str, Any]:
        manager._closed = True
        return {"created_at": "2026-08-24T12:00:00Z"}

    monkeypatch.setattr(manager, "_find_index_entry", close_during_lookup)
    with pytest.raises(chat.ChatNoSessionError, match="shut down"):
        await manager.reattach("20260824-120000-other2")

    manager._closed = False
    monkeypatch.setattr(
        manager,
        "_find_index_entry",
        lambda _session_id: {"created_at": "2026-08-24T12:00:00Z"},
    )
    cfg.agent.kind = "unsupported"
    with pytest.raises(chat.SymphonyError, match="unsupported agent kind"):
        await manager.reattach("20260824-120000-other3")

    cfg.agent.kind = "codex"

    async def fail_backend(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("reattach backend failed")

    monkeypatch.setattr(manager, "_build_backend", fail_backend)
    with pytest.raises(RuntimeError, match="reattach backend failed"):
        await manager.reattach("20260824-120000-other4")
    assert manager.live_count == 0


@pytest.mark.asyncio
async def test_chat_mode_change_and_turn_failures_remain_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    refreshes: list[bool] = []
    manager = chat.ChatManager(
        cast(Any, lambda: cfg),
        request_refresh=lambda: (_ for _ in ()).throw(RuntimeError("refresh failed")),
    )

    class Backend:
        session_id = "agent-session"

        async def stop(self) -> None:
            raise RuntimeError("already stopped")

        async def run_turn(self, **_kwargs: Any) -> None:
            raise RuntimeError("turn crashed")

    session = _chat_session(backend=Backend())
    manager._sessions[session.session_id] = session
    manager._active_id = session.session_id

    async def build(*_args: Any, **_kwargs: Any) -> None:
        session.backend = cast(Any, Backend())

    monkeypatch.setattr(manager, "_build_backend", build)
    monkeypatch.setattr(manager, "_save_index", lambda: None)
    changed = await manager.set_mode("edit", session.session_id)
    assert changed["mode"] == "edit"

    await manager._run_turn(session, "hello")
    assert any(message.text == "turn crashed" for message in session.transcript)
    assert refreshes == []

    class CancelBackend(Backend):
        async def run_turn(self, **_kwargs: Any) -> None:
            raise asyncio.CancelledError

    session.backend = cast(Any, CancelBackend())
    with pytest.raises(asyncio.CancelledError):
        await manager._run_turn(session, "cancel")


@pytest.mark.asyncio
async def test_chat_close_and_project_action_bookkeeping_tolerate_broken_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    session = _chat_session()
    manager._sessions[session.session_id] = session

    async def fail_stop(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("stop failed")

    monkeypatch.setattr(manager, "stop_session", fail_stop)

    class BrokenQueue(asyncio.Queue[dict[str, Any] | None]):
        def full(self) -> bool:
            return True

        def get_nowait(self) -> dict[str, Any] | None:
            raise asyncio.QueueEmpty

        def put_nowait(self, item: dict[str, Any] | None) -> None:
            del item
            raise asyncio.QueueFull

    manager._subscribers[BrokenQueue(maxsize=1)] = None
    await manager.close()
    assert manager._subscribers == {}

    manager._closed = False
    manager._sessions = {session.session_id: session}
    action = chat.ProjectSetupAction(
        action_id="project-" + "e" * 32,
        choice=5,
        name="Demo",
        path=str(tmp_path),
    )
    session.project_setup_actions[action.action_id] = action
    manager._close_project_setup_choice_windows(session, reason="testing")
    assert action.choice_active is False
    action.choice_active = True
    monkeypatch.setattr(chat, "_MAX_PROJECT_ACTIONS_PER_SESSION", 0)
    assert manager._prune_project_setup_actions(session) == []

    proposal = chat.ProjectSetupAction(
        action_id="project-" + "f" * 32,
        choice=6,
        name="No capability",
        path=str(tmp_path),
    )
    session.mode = "edit"
    monkeypatch.setattr(
        chat, "_project_setup_spec", lambda _text: ("visible", proposal)
    )
    manager._record_agent_message(session, "proposal")
    assert "could not be safely prepared" in session.transcript[-1].text.lower()


def test_chat_index_write_failure_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    monkeypatch.setattr(
        chat.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("read only")),
    )
    manager._write_index([])


@pytest.mark.asyncio
async def test_chat_lifecycle_rechecks_live_identity_after_lock_wait(
    tmp_path: Path,
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]

    class RemovingLock:
        async def __aenter__(self) -> None:
            manager._sessions.clear()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    for operation in ("stop", "mode"):
        session = _chat_session()
        session.lifecycle_lock = cast(Any, RemovingLock())
        manager._sessions[session.session_id] = session
        with pytest.raises(chat.ChatNoSessionError, match="no live chat session"):
            if operation == "stop":
                await manager.stop_session(session.session_id)
            else:
                await manager.set_mode("edit", session.session_id)


@pytest.mark.asyncio
async def test_chat_backend_start_failure_tolerates_stop_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        agent=SimpleNamespace(kind="custom"),
    )
    manager = chat.ChatManager(lambda: cfg)  # type: ignore[arg-type]
    session = _chat_session(agent_kind="custom")

    class Backend:
        async def start(self) -> None:
            raise RuntimeError("start failed")

        async def stop(self) -> None:
            raise RuntimeError("stop failed")

    monkeypatch.setattr(chat, "build_backend", lambda _init: Backend())
    with pytest.raises(RuntimeError, match="start failed"):
        await manager._build_backend(cfg, session)  # type: ignore[arg-type]
