"""Coverage contracts for configuration, trackers, and CLI adapters."""
# ruff: noqa: F405

from tests.coverage_cases._runtime_support import *  # noqa: F403


def test_stats_store_drops_after_shutdown_logs_write_once_and_tolerates_flush_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = stats.StatsStore(tmp_path / "stats.jsonl")

    class BrokenWriter:
        def submit(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("shutdown")

    store._writer.shutdown(wait=True)
    store._writer = BrokenWriter()
    store._append({"type": "turn"})
    store.flush()

    warnings: list[str] = []
    monkeypatch.setattr(
        stats.log, "warning", lambda message, **_fields: warnings.append(message)
    )
    original_mkdir = Path.mkdir
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("denied"))
            if path == store.path.parent
            else original_mkdir(path, *args, **kwargs)
        ),
    )
    store._write_line("one")
    store._write_line("two")
    assert warnings == ["stats_write_failed"]


def test_stats_reader_skips_blank_invalid_nonmap_old_and_bad_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "stats.jsonl"
    path.write_text(
        "\nnot-json\n[]\n"
        + json.dumps({"ts": "bad", "type": "turn"})
        + "\n"
        + json.dumps({"ts": "2000-01-01T00:00:00Z", "type": "turn"})
        + "\n",
        encoding="utf-8",
    )
    store = stats.StatsStore(path)
    monkeypatch.setattr(store, "flush", lambda *_args, **_kwargs: None)
    try:
        assert store.read_events(days=1) == []
    finally:
        store._writer.shutdown(wait=True)

    accumulator = stats._Accumulator({"done"})
    accumulator._fold_transition({}, stats.datetime.now(stats.timezone.utc), "today")
    assert stats._parse_ts(None) is None
    assert stats._parse_ts("bad") is None
    assert stats._as_int("bad") == 0


def test_skills_body_frontmatter_discovery_and_fallback_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = skills.Skill("missing", "", tmp_path / "missing.md")
    assert missing.body() == ""
    large = tmp_path / "large.md"
    large.write_text("x" * (skills.MAX_SKILL_CHARS + 1), encoding="utf-8")
    body = skills.Skill("large", "", large).body()
    assert body.endswith("[skill truncated]")

    assert skills._split_frontmatter("plain body") == ({}, "plain body")
    malformed = "---\n[bad\n---\nbody"
    assert skills._split_frontmatter(malformed) == ({}, malformed)
    assert skills._split_frontmatter("---\nname: x") == ({}, "---\nname: x")

    root = tmp_path / "skills"
    bad = root / "FallbackName" / "SKILL.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("---\nname: 'bad name'\n---\nbody", encoding="utf-8")
    assert skills.list_skills(tmp_path)[0].name == "fallbackname"


def test_skills_discovery_skips_unreadable_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "skills" / "broken" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda target, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("denied")) if target == path else ""
        ),
    )
    assert skills.list_skills(tmp_path) == []


def test_runtime_safety_skips_samefile_errors_and_uses_resolved_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_init = Path(runtime_safety.__file__).resolve().with_name("__init__.py")
    original_samefile = Path.samefile
    monkeypatch.setattr(
        Path,
        "samefile",
        lambda path, other: (
            (_ for _ in ()).throw(OSError("denied"))
            if other == package_init
            else original_samefile(path, other)
        ),
    )
    assert runtime_safety.protected_source_common_dir() is None

    protected = tmp_path / "protected"
    workflow = tmp_path / "workflow"
    monkeypatch.setattr(
        runtime_safety, "protected_source_common_dir", lambda: protected
    )
    monkeypatch.setattr(
        runtime_safety, "resolve_git_common_dir", lambda _path: workflow
    )
    monkeypatch.setattr(
        runtime_safety.os.path,
        "samefile",
        lambda *_args: (_ for _ in ()).throw(OSError("unsupported")),
    )
    assert (
        runtime_safety.workflow_uses_protected_source_repo(tmp_path / "WORKFLOW.md")
        is False
    )


def test_structured_logging_short_secret_and_sink_removal_race() -> None:
    assert symphony_logging._redact("short") == "***"

    class BrokenStream:
        def write(self, _line: str) -> None:
            logger._streams.remove(self)
            raise OSError("broken")

        def flush(self) -> None:
            return None

    stream = BrokenStream()
    logger = symphony_logging.StructuredLogger(streams=[stream])
    logger.info("message")
    assert logger._streams == []

    class AlwaysPresentList(list[Any]):
        def __contains__(self, _item: object) -> bool:
            return True

        def remove(self, _item: object) -> None:
            raise ValueError("raced")

    class FailingStream:
        def write(self, _line: str) -> None:
            raise OSError("broken")

        def flush(self) -> None:
            return None

    logger._streams = AlwaysPresentList([FailingStream()])
    logger.info("message")


def test_notification_config_invalid_values_and_template_entries() -> None:
    assert (
        notification_config.build_notifications_config(
            "bad", resolve_var=lambda value: value
        )
        == notification_config.NotificationsConfig()
    )
    assert (
        notification_config.build_notifications_config(
            {"slack": "bad"}, resolve_var=lambda value: value
        )
        == notification_config.NotificationsConfig()
    )
    parsed = notification_config.build_notifications_config(
        {
            "slack": {
                "webhook_url": "https://hooks.example",
                "templates": {"Done": "ok", 3: "bad", "Other": 4},
                "timeout_ms": "bad",
            }
        },
        resolve_var=lambda value: value,
    )
    assert parsed.slack is not None
    assert parsed.slack.templates == {"done": "ok"}
    assert parsed.slack.timeout_ms == notification_config.DEFAULT_SLACK_TIMEOUT_MS


def test_slack_notifier_property_icon_url_default_opener_and_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = notification_config.SlackConfig(
        webhook_url="https://hooks.example",
        icon_url="https://icon.example/icon.png",
    )
    notifier = notification_slack.SlackNotifier(config)
    assert notifier.config is config
    assert notifier._build_payload("text")["icon_url"] == config.icon_url
    event = NotificationEvent("T-1", "Title", "Todo", "Done", "workflow")

    class Response:
        status = 204

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def getcode(self) -> int:
            return 204

    monkeypatch.setattr(
        notification_slack.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    assert notifier.notify(event) is True
    monkeypatch.setattr(
        notification_slack.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    assert notifier.notify(event) is False
    http_error = notification_slack.urllib.error.HTTPError(
        "https://hooks.example", 500, "bad", {}, None
    )
    monkeypatch.setattr(
        notification_slack.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(http_error),
    )
    assert notifier.notify(event) is False


def test_notification_dispatch_convenience_builds_and_dispatches() -> None:
    config = notification_config.NotificationsConfig(
        slack=notification_config.SlackConfig(webhook_url="https://hooks.example")
    )
    event = NotificationEvent("T-1", "Title", "Todo", "Done", "workflow")
    calls: list[str] = []
    original = notification_dispatcher.build_dispatcher
    try:
        notification_dispatcher.build_dispatcher = lambda _config: SimpleNamespace(
            dispatch=lambda _event: calls.append(_event.identifier)
        )
        notification_dispatcher.dispatch_notification(config, event)
    finally:
        notification_dispatcher.build_dispatcher = original
    assert calls == ["T-1"]


def test_workflow_coercion_drops_bad_state_entries_and_resolves_path_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert workflow_coercion._normalize_state_map(
        {3: 1, "bool": True, "bad": "x", "zero": 0, " ": 1, "Good": 2}
    ) == {"good": 2}
    assert workflow_coercion._normalize_state_description_map(
        {3: "x", "bad": 4, "empty": " ", "Good": " text "}
    ) == {"good": "text"}
    assert workflow_coercion._resolve_config_path(tmp_path, "$MISSING_PATH") == tmp_path
    absolute = (tmp_path / "absolute").resolve()
    assert workflow_coercion._resolve_config_path(tmp_path, str(absolute)) == absolute

    file = tmp_path / "prompt.md"
    file.write_text("body", encoding="utf-8")
    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("denied"))
            if path == file
            else original_read(path, *args, **kwargs)
        ),
    )
    with pytest.raises(Exception, match="prompt file unreadable"):
        workflow_coercion._read_prompt_file(file)


def test_workflow_parser_empty_frontmatter_and_io_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(Exception, match="front matter not terminated"):
        workflow_parser.parse_workflow_text("---\nkey: value", tmp_path / "WORKFLOW.md")
    parsed = workflow_parser.parse_workflow_text(
        "---\n\n---\nbody", tmp_path / "WORKFLOW.md"
    )
    assert parsed.config == {}
    parsed = workflow_parser.parse_workflow_text(
        "---\n# comment only\n---\nbody", tmp_path / "WORKFLOW.md"
    )
    assert parsed.config == {}

    path = tmp_path / "WORKFLOW.md"
    path.write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    with pytest.raises(Exception, match="workflow file unreadable"):
        workflow_parser.load_workflow(path)


def test_workflow_state_exposes_last_reload_error(tmp_path: Path) -> None:
    state = workflow_state.WorkflowState(tmp_path / "missing.md")
    _cfg, error = state.reload()
    assert error is not None and state.last_error() is error


@pytest.mark.parametrize("kind", ["pi", "gemini", "agy", "kiro", "opencode"])
def test_workflow_config_backend_timeouts_for_remaining_kinds(kind: str) -> None:
    values = SimpleNamespace(turn_timeout_ms=1, read_timeout_ms=2, stall_timeout_ms=3)
    cfg = SimpleNamespace(
        agent=SimpleNamespace(kind=kind),
        pi=values,
        gemini=values,
        agy=values,
        kiro=values,
        opencode=values,
    )
    assert workflow_config.ServiceConfig.backend_timeouts(cfg) == (1, 2, 3)


def test_workflow_config_prompt_presence_and_unknown_backend() -> None:
    assert workflow_config.PromptConfig(
        stage_templates={"todo": "body"}
    ).has_stage_prompts()
    cfg = SimpleNamespace(agent=SimpleNamespace(kind="future"))
    with pytest.raises(Exception, match="agent.kind must be one of"):
        workflow_config.ServiceConfig.backend_timeouts(cfg)


def test_small_compatibility_entry_and_parsing_tails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    agent_module = importlib.import_module("symphony.agent")
    assert agent_module.CodexAppServerClient is codex_backend.CodexAppServerBackend
    naive = issue_module.parse_iso_timestamp("2026-08-24T12:00:00")
    assert naive is not None and naive.tzinfo is not None
    assert prompt_context._issue_description({"description": "body"}) == "body"
    monkeypatch.setattr(pyright_module.sys, "executable", "")
    with pytest.raises(RuntimeError, match="interpreter path is unavailable"):
        pyright_module.build_command(())


def test_package_module_entrypoint_propagates_cli_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import symphony.cli as cli_package

    monkeypatch.setattr(cli_package, "main", lambda: 6)
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("symphony.__main__", run_name="__main__")
    assert raised.value.code == 6


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (subprocess.TimeoutExpired("bash", 1), "timeout"),
        (RuntimeError("spawn failed"), "error"),
        (subprocess.CompletedProcess(["bash"], 99, b"out", b"err"), "unknown_rc_99"),
    ],
)
def test_auto_merge_classifies_timeout_exception_and_unknown_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: Exception | subprocess.CompletedProcess[bytes],
    expected: str,
) -> None:
    async def run(_function: Any) -> Any:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(auto_merge.asyncio, "to_thread", run)
    result = asyncio.run(
        auto_merge.auto_merge_on_done_best_effort(
            workflow_dir=tmp_path,
            branch="topic",
            identifier="T-1",
            title="Title",
            target_branch="main",
            exclude_paths=(),
        )
    )
    assert result.status == expected


def test_tracker_factory_builds_jira_rejects_unknown_and_swallows_close_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import symphony.trackers.jira as jira_module

    client = SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError("close")))
    monkeypatch.setattr(jira_module, "JiraClient", lambda _tracker: client)
    cfg = SimpleNamespace(tracker=SimpleNamespace(kind="jira"))
    assert tracker_factory.build_tracker_client(cfg) is client
    with tracker_factory.context_manager(cfg) as opened:
        assert opened is client
    cfg.tracker.kind = "future"
    with pytest.raises(Exception, match="tracker kind not supported"):
        tracker_factory.build_tracker_client(cfg)


def test_per_turn_base_callbacks_flags_abstract_hooks_and_broken_stdin() -> None:
    async def broken_callback(_event: dict[str, Any]) -> None:
        raise RuntimeError("observer failed")

    asyncio.run(
        per_turn_backend._emit_event(
            broken_callback,
            "event",
            {},
            usage={},
            rate_limits=None,
            agent_pid=None,
        )
    )
    assert per_turn_backend._has_shell_flag('"unterminated', "--flag") is False
    backend = object.__new__(per_turn_backend.PerTurnCliBackend)
    backend._active_proc = None
    assert backend.latest_rate_limits is None
    with pytest.raises(NotImplementedError):
        backend._command_for_turn(prompt="", is_continuation=False)
    with pytest.raises(NotImplementedError):
        asyncio.run(backend._complete_turn("", 0))

    class BrokenInput:
        def write(self, _value: bytes) -> None:
            raise BrokenPipeError("closed")

    proc = SimpleNamespace(stdin=BrokenInput())
    backend._agent_name = "agent"
    with pytest.raises(Exception, match="stdin closed"):
        asyncio.run(backend._write_prompt(proc, "prompt"))


def test_opencode_helpers_cover_invalid_resume_list_decode_nested_text_and_bad_numbers() -> (
    None
):
    backend = object.__new__(opencode_backend.OpenCodeBackend)
    backend._closed = True
    assert asyncio.run(backend.resume_session("session")) is False
    assert backend._decode_events("") == []
    assert backend._decode_events('[{"type":"one"}, 3]') == [{"type": "one"}]
    assert backend._decode_events("3") == []
    assert (
        opencode_backend._extract_session_id({"data": {"session_id": "sid"}}) == "sid"
    )
    assert opencode_backend._extract_text(["a", {"text": "b"}]) == "a\nb"
    assert list(opencode_backend._usage_dicts([{"usage": {"input_tokens": 1}}])) == [
        {"input_tokens": 1}
    ]
    assert opencode_backend._sum_int_values({"a": "bad", "b": 2}, "a", "b") == 2


def test_opencode_complete_turn_requires_resume_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(opencode_backend.OpenCodeBackend)
    backend._streamed_event_counts = opencode_backend.Counter()
    backend._expected_resume_session_id = "session-old"
    backend._resume_session_confirmed = False

    async def emit(_event: str, _payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(backend, "_emit", emit)
    with pytest.raises(Exception, match="did not confirm"):
        asyncio.run(backend._complete_turn("", 0))


def test_opencode_heartbeat_stops_when_process_exits_during_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(opencode_backend.OpenCodeBackend)
    proc = SimpleNamespace(returncode=None, pid=42)

    async def process_exit(_delay: float) -> None:
        proc.returncode = 0

    monkeypatch.setattr(opencode_backend.asyncio, "sleep", process_exit)
    asyncio.run(backend._emit_heartbeats(proc))
    assert proc.returncode == 0


def test_per_turn_run_handles_closed_during_spawn_and_collect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(per_turn_backend.PerTurnCliBackend)
    backend._closed = False
    backend._active_proc = None
    backend._on_process_started = None
    backend._agent_name = "agent"
    monkeypatch.setattr(
        backend,
        "_command_for_turn",
        lambda *, prompt, is_continuation: "command",
    )
    monkeypatch.setattr(backend, "_stdin_payload", lambda _prompt: None)
    proc = SimpleNamespace(pid=42, returncode=None)

    async def spawn(_command: str, *, pipe_stdin: bool) -> Any:
        del pipe_stdin
        backend._closed = True
        return proc

    async def reap(_proc: Any) -> None:
        return None

    monkeypatch.setattr(backend, "_spawn", spawn)
    monkeypatch.setattr(backend, "_reap", reap)
    with pytest.raises(ResponseError, match="closed during spawn"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))
    assert backend._active_proc is None

    class SlowStream:
        async def read(self) -> bytes:
            await asyncio.Event().wait()
            return b""

    backend._turn_timeout_ms = 1
    backend._closed = False
    proc = SimpleNamespace(stdout=SlowStream(), stderr=SlowStream(), returncode=None)
    events: list[str] = []

    async def read_stdout(_stream: Any) -> bytes:
        await asyncio.Event().wait()
        return b""

    async def emit(event: str, _payload: dict[str, Any]) -> None:
        events.append(event)

    async def safe_wait(_proc: Any, *, timeout: float | None = None) -> int:
        del timeout
        await asyncio.Event().wait()
        return 0

    monkeypatch.setattr(backend, "_read_stdout", read_stdout)
    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(per_turn_backend, "safe_proc_wait", safe_wait)
    with pytest.raises(Exception, match="turn timed out"):
        asyncio.run(backend._collect(proc))
    assert events == [per_turn_backend.EVENT_TURN_FAILED]


def test_pi_run_turn_handles_closed_spawn_and_broken_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _pi_runtime_backend()
    started: list[int] = []
    backend._on_process_started = started.append
    proc = SimpleNamespace(
        pid=42,
        stdin=_WritableInput(),
        stdout=object(),
        stderr=None,
        returncode=None,
    )

    async def spawn_closed(*_args: Any, **_kwargs: Any) -> Any:
        backend._closed = True
        return proc

    async def reap(_proc: Any) -> None:
        return None

    monkeypatch.setattr(pi_backend.asyncio, "create_subprocess_exec", spawn_closed)
    monkeypatch.setattr(backend, "_reap", reap)
    with pytest.raises(ResponseError, match="closed during spawn"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))
    assert started == [42] and backend._active_proc is None

    backend = _pi_runtime_backend()
    proc.stdin = _WritableInput(broken=True)

    async def spawn_broken(*_args: Any, **_kwargs: Any) -> Any:
        return proc

    async def emit(_event: str, _payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(pi_backend.asyncio, "create_subprocess_exec", spawn_broken)
    monkeypatch.setattr(backend, "_emit", emit)
    with pytest.raises(Exception, match="stdin closed"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))


def test_pi_run_turn_reports_corrupt_stream_and_terminal_failure_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    async def spawn(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            pid=42,
            stdin=_WritableInput(),
            stdout=object(),
            stderr=None,
            returncode=0,
        )

    async def wait(_proc: Any, *, timeout: float) -> int:
        del timeout
        return 0

    async def drain(_proc: Any) -> None:
        return None

    monkeypatch.setattr(pi_backend.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(pi_backend, "safe_proc_wait", wait)

    backend = _pi_runtime_backend()
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_drain_stderr", drain)

    async def corrupt(_proc: Any) -> None:
        backend._stream_corrupt = "bad line"
        return None

    monkeypatch.setattr(backend, "_consume_stream", corrupt)
    with pytest.raises(Exception, match="stream unreadable"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))

    backend = _pi_runtime_backend()
    events = []
    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_drain_stderr", drain)
    monkeypatch.setattr(backend, "_stderr_blob", lambda: "diagnostic")

    async def terminal(_proc: Any) -> dict[str, Any]:
        return {
            "type": "agent_end",
            "messages": [
                {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "model failed",
                }
            ],
        }

    monkeypatch.setattr(backend, "_consume_stream", terminal)
    with pytest.raises(Exception, match="model failed; stderr: diagnostic"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))


def test_pi_run_turn_tolerates_late_stderr_drain_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _pi_runtime_backend()
    proc = SimpleNamespace(
        pid=42,
        stdin=_WritableInput(),
        stdout=object(),
        stderr=object(),
        returncode=0,
    )

    async def spawn(*_args: Any, **_kwargs: Any) -> Any:
        return proc

    async def no_terminal(_proc: Any) -> None:
        return None

    async def slow_stderr(_proc: Any) -> None:
        await asyncio.Event().wait()

    async def wait(_proc: Any, *, timeout: float) -> int:
        del timeout
        return 0

    async def emit(_event: str, _payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(pi_backend.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(backend, "_consume_stream", no_terminal)
    monkeypatch.setattr(backend, "_drain_stderr", slow_stderr)
    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_stderr_blob", lambda: "")
    monkeypatch.setattr(pi_backend, "safe_proc_wait", wait)
    with pytest.raises(Exception, match="no agent_end event"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))


def test_claude_run_turn_closed_spawn_broken_stdin_timeout_and_resume_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _claude_runtime_backend()
    started: list[int] = []
    backend._on_process_started = started.append
    proc = SimpleNamespace(
        pid=42,
        stdin=_WritableInput(),
        stdout=object(),
        stderr=None,
        returncode=None,
    )

    async def spawn_closed(*_args: Any, **_kwargs: Any) -> Any:
        backend._closed = True
        return proc

    async def reap(_proc: Any) -> None:
        return None

    monkeypatch.setattr(claude_backend.asyncio, "create_subprocess_exec", spawn_closed)
    monkeypatch.setattr(backend, "_reap", reap)
    with pytest.raises(ResponseError, match="closed during spawn"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))
    assert started == [42]

    backend = _claude_runtime_backend()
    proc.stdin = _WritableInput(broken=True)

    async def spawn(*_args: Any, **_kwargs: Any) -> Any:
        return proc

    async def emit(_event: str, _payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(claude_backend.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(backend, "_emit", emit)
    with pytest.raises(Exception, match="stdin closed"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))

    backend = _claude_runtime_backend()
    proc.stdin = _WritableInput()
    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_reap", reap)

    async def slow(_proc: Any) -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(backend, "_consume_stream", slow)
    with pytest.raises(Exception, match="turn timed out"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))

    backend = _claude_runtime_backend()
    backend._expected_resume_session_id = "session-old"
    backend._resume_session_confirmed = False
    proc.stdin = _WritableInput()
    proc.returncode = 0
    monkeypatch.setattr(backend, "_emit", emit)

    async def result(_proc: Any) -> dict[str, Any]:
        return {"type": "result", "subtype": "success", "result": "ok"}

    async def safe_wait(_proc: Any, *, timeout: float) -> int:
        del timeout
        return 0

    monkeypatch.setattr(backend, "_consume_stream", result)
    monkeypatch.setattr(claude_backend, "safe_proc_wait", safe_wait)
    with pytest.raises(Exception, match="did not confirm"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))


def test_file_tracker_lock_platform_failures_and_posix_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(_fd: int, _mode: int, _size: int) -> None:
            raise OSError(errno.EIO, "lock device failed")

    monkeypatch.setattr(tracker_file.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", BrokenMsvcrt)
    with pytest.raises(OSError, match="lock device failed"):
        with tracker_file._exclusive_lock(tmp_path / "windows.lock"):
            pytest.fail("an unavailable lock must not enter the critical section")

    calls: list[int] = []
    fake_fcntl = SimpleNamespace(
        LOCK_EX=10,
        LOCK_UN=20,
        flock=lambda _fd, operation: calls.append(operation),
    )
    monkeypatch.setattr(tracker_file.os, "name", "posix")
    monkeypatch.setattr(tracker_file, "fcntl", fake_fcntl)
    with tracker_file._exclusive_lock(tmp_path / "nested" / "posix.lock"):
        assert calls == [fake_fcntl.LOCK_EX]
    assert calls == [fake_fcntl.LOCK_EX, fake_fcntl.LOCK_UN]


def test_file_tracker_parsing_recovery_boundaries(tmp_path: Path) -> None:
    empty_front = tmp_path / "empty-front.md"
    empty_front.write_text("---\n\n---\nbody\n", encoding="utf-8")
    assert tracker_file.parse_ticket_file(empty_front) == ({}, "body")

    sequence_front = tmp_path / "sequence-front.md"
    sequence_front.write_text("---\n- one\n---\n", encoding="utf-8")
    with pytest.raises(SymphonyError, match="must be a map"):
        tracker_file.parse_ticket_file(sequence_front)

    helper = tracker_file._parse_front_matter_prefix_without_delimiters
    assert helper(tmp_path / "x.md", []) is None
    assert helper(tmp_path / "x.md", ["state: Todo", "## Body"]) == (
        {"state": "Todo", "id": "x", "identifier": "x", "title": "x"},
        "## Body",
    )
    assert helper(tmp_path / "x.md", ["state: Todo", "mystery: value"]) is None
    assert helper(tmp_path / "x.md", ["state: Todo", "plain body"]) == (
        {"state": "Todo", "id": "x", "identifier": "x", "title": "x"},
        "plain body",
    )
    assert helper(tmp_path / "x.md", ["state: [unterminated"]) is None
    assert helper(tmp_path / "x.md", ["title: no state"]) is None

    assert tracker_file._auto_heal_markdown_in_front_matter(
        tmp_path / "x.md", ["---", "## Moved", "---"], 2
    ) == ({}, "## Moved")
    assert (
        tracker_file._auto_heal_markdown_in_front_matter(
            tmp_path / "x.md",
            ["---", "state: [", "## Moved", "---"],
            3,
        )
        is None
    )
    assert (
        tracker_file._auto_heal_markdown_in_front_matter(
            tmp_path / "x.md", ["---", "  - item", "## Moved", "---"], 3
        )
        is None
    )
    assert (
        tracker_file._auto_heal_misindented_top_level_front_matter(
            tmp_path / "x.md", ["---", "  state: [", "---"], 2
        )
        is None
    )


def test_file_tracker_parsing_and_filesystem_error_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blockers = tracker_file._parse_blockers([{}, {"identifier": "A-1"}])
    assert [blocker.identifier for blocker in blockers] == ["A-1"]
    merged = tracker_file._merge_body_dependency_blockers(
        blockers, "## Dependencies\n- A-1\n- B-2"
    )
    assert [blocker.identifier for blocker in merged] == ["A-1", "B-2"]

    class Unstatable:
        def stat(self) -> None:
            raise OSError("gone")

    broken = Unstatable()
    assert tracker_file._file_ctime_iso(broken) is None  # type: ignore[arg-type]
    assert tracker_file._file_mtime_iso(broken) is None  # type: ignore[arg-type]
    assert tracker_file._file_mtime_ns(broken) is None  # type: ignore[arg-type]

    target = tmp_path / "ticket.md"
    monkeypatch.setattr(
        tracker_file.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace")),
    )
    monkeypatch.setattr(
        tracker_file.os,
        "unlink",
        lambda *_args: (_ for _ in ()).throw(OSError("unlink")),
    )
    with pytest.raises(OSError, match="replace"):
        tracker_file.write_ticket_atomic(target, {"state": "Todo"}, "body")

    candidate = SimpleNamespace(name=".tmp-corrupt.md", suffix=".md")
    monkeypatch.setattr(
        tracker_file,
        "issue_from_file",
        lambda _path: (_ for _ in ()).throw(SymphonyError("bad ticket")),
    )
    assert not tracker_file._is_owned_tracker_temp(candidate)


def test_file_tracker_context_empty_queries_and_racy_missing_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = tracker_file.FileBoardTracker(_file_tracker_config(tmp_path))
    assert tracker.__enter__() is tracker
    assert tracker.board_root == tmp_path.resolve()
    assert tracker.fetch_issue_states_by_ids([]) == []
    assert tracker.fetch_issue_full_by_id("") is None
    assert tracker.__exit__(None, None, None) is None

    ticket = tracker.create(identifier="RACE-1", title="Race")
    results = iter([ticket, None])
    monkeypatch.setattr(tracker, "find_path", lambda _identifier: next(results))
    assert (
        tracker._mutate_ticket(
            "RACE-1", lambda front, body: (front, body), missing_ok=True
        )
        is None
    )

    results = iter([ticket, None])
    monkeypatch.setattr(tracker, "find_path", lambda _identifier: next(results))
    with pytest.raises(SymphonyError, match="ticket not found"):
        tracker._mutate_ticket("RACE-1", lambda front, body: (front, body))


def test_file_tracker_stale_temp_scan_tolerates_stat_and_unlink_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TempCandidate:
        name = ".tmp-symphony-ticket-12345678.tmp"
        suffix = ".tmp"

        def __init__(self, *, stat_error: bool) -> None:
            self.stat_error = stat_error

        def stat(self) -> Any:
            if self.stat_error:
                raise OSError("stat race")
            return SimpleNamespace(st_mtime=0.0)

        def unlink(self) -> None:
            raise OSError("unlink race")

        def __str__(self) -> str:
            return self.name

    tracker = object.__new__(tracker_file.FileBoardTracker)
    tracker._root = SimpleNamespace(
        glob=lambda _pattern: [
            TempCandidate(stat_error=True),
            TempCandidate(stat_error=False),
        ]
    )
    monkeypatch.setattr(tracker_file.time, "time", lambda: 1000.0)
    tracker._sweep_stale_temps()


def test_file_tracker_cas_convergence_and_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = tracker_file.FileBoardTracker(_file_tracker_config(tmp_path))
    path = tmp_path / "CAS-1.md"
    parsed = iter([({"updated_at": "new"}, "latest")])
    monkeypatch.setattr(tracker_file, "parse_ticket_file", lambda _path: next(parsed))
    monkeypatch.setattr(tracker_file, "_file_mtime_ns", lambda _path: 2)
    wrote: list[tuple[dict[str, Any], str]] = []
    monkeypatch.setattr(
        tracker_file,
        "write_ticket_atomic",
        lambda _path, front, body: wrote.append((dict(front), body)),
    )
    tracker._write_ticket_with_updated_at_cas(
        path,
        ("old", 1),
        lambda _front, _body: None,
        {"updated_at": "old"},
        "old",
    )
    assert wrote == []

    counter = iter(range(3))
    monkeypatch.setattr(
        tracker_file,
        "parse_ticket_file",
        lambda _path: ({"updated_at": f"remote-{next(counter)}"}, "remote"),
    )
    tracker._write_ticket_with_updated_at_cas(
        path,
        ("old", 1),
        lambda front, body: ({**front, "owner": "local"}, body),
        {"updated_at": "old"},
        "old",
    )
    assert wrote[-1][0]["owner"] == "local"
    assert wrote[-1][1] == "remote"


def test_file_tracker_idempotent_artifacts_audit_and_field_clears(
    tmp_path: Path,
) -> None:
    tracker = tracker_file.FileBoardTracker(_file_tracker_config(tmp_path))
    ticket = tracker.create(
        identifier="EDIT-1",
        title="Edit",
        priority=1,
        skills=["pytest"],
        agent_kind="codex",
    )
    assert tracker.upsert_artifacts_section("EDIT-1", "") == ticket
    assert tracker.record_last_agent_kind("EDIT-1", "") == ticket
    tracker.update_fields(
        "EDIT-1",
        clear_priority=True,
        skills=[],
        agent_kind="",
    )
    tracker.update_fields("EDIT-1", skills=["ruff"])
    front, _body = tracker_file.parse_ticket_file(ticket)
    assert "priority" not in front
    assert front["skills"] == ["ruff"]
    assert "agent" not in front
    with pytest.raises(SymphonyError, match="ticket not found"):
        tracker.delete("MISSING")


def test_file_tracker_allocator_collision_retries_and_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = tracker_file.FileBoardTracker(_file_tracker_config(tmp_path))
    monkeypatch.setattr(tracker_file, "_GENERATED_ID_ATTEMPTS", 2)
    monkeypatch.setattr(tracker, "scan_all", lambda: [])
    monkeypatch.setattr(tracker, "_next_identifier_unlocked", lambda _prefix: "TASK-1")
    monkeypatch.setattr(
        tracker,
        "create",
        lambda **_kwargs: (_ for _ in ()).throw(SymphonyError("collision")),
    )

    with pytest.raises(SymphonyError, match="collision"):
        tracker.create_validated(identifier="EXPLICIT-1", title="Explicit")
    with pytest.raises(SymphonyError, match="collision"):
        tracker.create_validated(identifier=None, title="Generated")
    with pytest.raises(SymphonyError, match="collision"):
        tracker.create_with_next_identifier("TASK", title="Generated")


def test_workflow_builder_prompt_map_filters_invalid_stage_entries(
    tmp_path: Path,
) -> None:
    built = workflow_builder._build_prompt_config(
        {"stages": {1: "ignored.md", "Todo": None, "Done": "  "}}, tmp_path
    )
    assert built.stage_templates == {}
    assert built.stage_paths == {}


@pytest.mark.parametrize(
    "section",
    [
        "tracker",
        "polling",
        "workspace",
        "hooks",
        "agent",
        "codex",
        "claude",
        "gemini",
        "agy",
        "kiro",
        "opencode",
        "pi",
        "prime_agent",
        "server",
        "progress",
        "system",
        "wiki",
        "continuous_improvement",
    ],
)
def test_workflow_builder_non_mapping_optional_sections_use_defaults(
    tmp_path: Path, section: str
) -> None:
    cfg = workflow_builder.build_service_config(
        _workflow_definition(tmp_path, {section: "not-a-map"})
    )
    assert cfg.workflow_path == tmp_path / "WORKFLOW.md"


def test_workflow_builder_unresolved_and_absolute_paths_fall_back_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SYMPHONY_COVERAGE_MISSING", raising=False)
    progress_path = (tmp_path / "progress.md").resolve()
    wiki_path = (tmp_path / "wiki").resolve()
    cfg = workflow_builder.build_service_config(
        _workflow_definition(
            tmp_path,
            {
                "tracker": {"kind": "file", "board_root": "$SYMPHONY_COVERAGE_MISSING"},
                "workspace": {"root": "$SYMPHONY_COVERAGE_MISSING"},
                "progress": {"path": str(progress_path)},
                "wiki": {"root": str(wiki_path)},
                "server": {"port": True},
            },
        )
    )
    assert cfg.tracker.board_root is None
    assert cfg.workspace_root.name == "symphony_workspaces"
    assert cfg.progress.path == progress_path
    assert cfg.wiki.root == wiki_path
    assert cfg.server.port is None

    fallback_wiki = workflow_builder.build_service_config(
        _workflow_definition(tmp_path, {"wiki": {"root": "$SYMPHONY_COVERAGE_MISSING"}})
    )
    assert fallback_wiki.wiki.root == (tmp_path / "docs" / "llm-wiki").resolve()


@pytest.mark.parametrize("value", [True, -1])
def test_workflow_builder_rejects_invalid_archive_retention(
    tmp_path: Path, value: Any
) -> None:
    with pytest.raises(ConfigValidationError, match="archive_after_days"):
        workflow_builder.build_service_config(
            _workflow_definition(tmp_path, {"tracker": {"archive_after_days": value}})
        )


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"preview": "bad"}, "preview must be a mapping"),
        ({"preview": {"health_path": "relative"}}, "absolute URL path"),
        ({"preview": {"acceptance": [""]}}, "acceptance"),
        ({"preview": {"enabled": True}}, "command is required"),
        ({"artifacts": "bad"}, "artifacts must be a mapping"),
        ({"progress": {"max_transitions": True}}, "max_transitions"),
        ({"progress": {"max_transitions": -1}}, "max_transitions"),
    ],
)
def test_workflow_builder_rejects_invalid_surface_configuration(
    tmp_path: Path, config: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigValidationError, match=message):
        workflow_builder.build_service_config(_workflow_definition(tmp_path, config))


def test_workflow_builder_numeric_and_policy_validation_boundaries() -> None:
    with pytest.raises(ConfigValidationError, match="positive integer"):
        workflow_builder._validated_positive_or_default(True, 1, name="value")
    with pytest.raises(ConfigValidationError, match="positive integer"):
        workflow_builder._validated_positive_or_default(object(), 1, name="value")
    with pytest.raises(ConfigValidationError, match="positive number"):
        workflow_builder._validated_positive_float_or_default(True, 1.0, name="value")
    with pytest.raises(ConfigValidationError, match="positive number"):
        workflow_builder._validated_positive_float_or_default(
            object(), 1.0, name="value"
        )
    with pytest.raises(ConfigValidationError, match="non-negative integer"):
        workflow_builder._validated_nonnegative_or_default(True, 1, name="value")
    with pytest.raises(ConfigValidationError, match="non-negative integer"):
        workflow_builder._validated_nonnegative_or_default(object(), 1, name="value")

    assert (
        workflow_builder._validated_stage_kinds(
            {1: "codex", " ": "codex"},
            active_states=("Todo",),
            terminal_states=("Done",),
        )
        == {}
    )
    with pytest.raises(ConfigValidationError, match="after_done_failure_policy"):
        workflow_builder._validated_after_done_failure_policy("stop")
    assert workflow_builder._validated_after_done_failure_policy("block") == "block"


def test_project_cli_list_remove_start_and_status_paths(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    projects = [_project("one", port=9999), _project("two", port=10000)]

    class Registry:
        entries = projects

        def load(self) -> list[Any]:
            return list(self.entries)

        def get(self, identifier: str) -> Any:
            return next(project for project in self.entries if project.id == identifier)

        def remove(self, identifier: str) -> Any:
            return self.get(identifier)

    monkeypatch.setattr(project_cli, "ProjectRegistry", Registry)
    service_calls: list[list[str]] = []
    monkeypatch.setattr(
        project_cli.service,
        "main",
        lambda argv: service_calls.append(list(argv)) or (1 if "two" in argv[1] else 0),
    )

    assert project_cli.cmd_list(argparse.Namespace()) == 0
    assert "ID  NAME" in capsys.readouterr().out
    assert project_cli.cmd_remove(argparse.Namespace(id="one")) == 0
    assert "repository left untouched" in capsys.readouterr().out
    assert (
        project_cli.cmd_start(
            argparse.Namespace(id="one", replace=True, skip_doctor=True)
        )
        == 0
    )
    assert service_calls[-1][-2:] == ["--replace", "--skip-doctor"]
    assert project_cli.cmd_status(argparse.Namespace(id=None)) == 1
    assert "[one] ONE" in capsys.readouterr().out

    Registry.entries = []
    assert project_cli.cmd_list(argparse.Namespace()) == 0
    assert project_cli.cmd_status(argparse.Namespace(id=None)) == 0
    assert capsys.readouterr().out.count("(no projects)") == 2


def test_project_cli_create_rejects_name_without_identifier_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(project_cli, "source_checkout", lambda: Path("C:/source"))
    with pytest.raises(project_cli.ProjectError, match="letter or digit"):
        project_cli.cmd_create(
            argparse.Namespace(
                id=None,
                name="!!!",
                path=None,
                workflow="WORKFLOW.md",
                host="127.0.0.1",
                port=None,
            )
        )


def test_board_cli_tracker_resolution_and_operator_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert board_cli._operator_message(ValueError("plain")) == "plain"
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("body", encoding="utf-8")
    args = argparse.Namespace(workflow=str(workflow), root=None)
    monkeypatch.setattr(
        board_cli,
        "load_workflow",
        lambda _path: (_ for _ in ()).throw(SymphonyError("bad workflow")),
    )
    assert board_cli._resolve_tracker(args) is None
    assert "using --root" in capsys.readouterr().err

    monkeypatch.setattr(board_cli, "load_workflow", lambda _path: object())
    monkeypatch.setattr(
        board_cli,
        "build_service_config",
        lambda _workflow: SimpleNamespace(
            tracker=TrackerConfig(
                kind="linear",
                endpoint="",
                api_key="",
                project_slug="",
                active_states=(),
                terminal_states=(),
            )
        ),
    )
    assert board_cli._resolve_tracker(args) is None
    assert "not 'file'" in capsys.readouterr().err

    missing = argparse.Namespace(workflow=str(tmp_path / "missing.md"), root=None)
    resolved = board_cli._get_tracker(missing)
    assert resolved.board_root == (tmp_path / "board").resolve()


def test_board_cli_update_show_and_graph_boundary_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "board"
    tracker = tracker_file.FileBoardTracker(_file_tracker_config(root))
    tracker.create(
        identifier="SHOW-1",
        title="Nested agent",
        request="REQ-A",
        agent_kind="codex",
        description="Body text",
        blocked_by=["MISSING-1"],
    )
    tracker.create(
        identifier="OTHER-1",
        title="Flat agent",
        request="REQ-B",
        blocked_by=["MISSING-2"],
    )
    other = root / "OTHER-1.md"
    front, body = tracker_file.parse_ticket_file(other)
    front["agent_kind"] = "claude"
    tracker_file.write_ticket_atomic(other, front, body)

    assert (
        board_cli.main(["update", "--root", str(root), "SHOW-1", "--request", "REQ-C"])
        == 0
    )
    assert "request=REQ-C" in capsys.readouterr().out
    assert board_cli.main(["show", "--root", str(root), "SHOW-1"]) == 0
    shown = capsys.readouterr().out
    assert "request: REQ-C" in shown
    assert "agent: codex" in shown
    assert board_cli.main(["show", "--root", str(root), "OTHER-1"]) == 0
    assert "agent: claude" in capsys.readouterr().out

    assert board_cli.main(["graph", "--root", str(root), "--request", "REQ-C"]) == 0
    assert "SHOW-1" in capsys.readouterr().out
    empty = tmp_path / "empty"
    assert board_cli.main(["graph", "--root", str(empty)]) == 0
    assert "(no tickets)" in capsys.readouterr().out


def test_server_request_validation_and_rejected_operations() -> None:
    class Orchestrator:
        def snapshot(self) -> dict[str, Any]:
            return {}

        def issue_snapshot(self, _identifier: str) -> None:
            return None

        def request_refresh(self) -> bool:
            return False

        async def skip_document(self, identifier: str) -> tuple[bool, str]:
            if identifier == "OK":
                return True, "skipped"
            return False, f"unknown issue {identifier}"

        async def recover_blocked_issue(
            self,
            identifier: str,
            *,
            target_state: str | None,
            agent_kind: str | None,
        ) -> tuple[bool, str, dict[str, Any]]:
            del target_state, agent_kind
            return False, f"unknown issue {identifier}", {}

    async def exercise() -> None:
        client = TestClient(TestServer(server_module.build_app(Orchestrator())))  # type: ignore[arg-type]
        await client.start_server()
        try:
            response = await client.post("/api/v1/refresh", json=["bad"])
            assert response.status == 400

            response = await client.post("/api/v1/OK/skip-document")
            assert response.status == 200
            response = await client.post("/api/v1/MISSING/skip-document")
            assert response.status == 404

            response = await client.post(
                "/api/v1/X/recover-blocked",
                data="{",
                headers={"Content-Type": "application/json"},
            )
            assert response.status == 400
            response = await client.post("/api/v1/X/recover-blocked", json=["bad"])
            assert response.status == 400
            response = await client.post(
                "/api/v1/X/recover-blocked", json={"fix_state": 3}
            )
            assert response.status == 400
            response = await client.post(
                "/api/v1/X/recover-blocked", json={"agent_kind": 3}
            )
            assert response.status == 400
            response = await client.post("/api/v1/X/recover-blocked", json={})
            assert response.status == 404
        finally:
            await client.close()

    asyncio.run(exercise())


def test_jira_tracker_remaining_normalization_query_and_payload_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = jira_tracker._normalize_issue(
        {
            "id": "1",
            "key": "J-1",
            "fields": {
                "summary": "Issue",
                "status": {"name": "Todo"},
                "priority": {"id": "high"},
            },
        },
        site_url="https://jira.example",
    )
    assert normalized.priority is None

    client = object.__new__(jira_tracker.JiraClient)
    client._tracker = TrackerConfig(
        kind="jira",
        endpoint="https://jira.example",
        api_key="token",
        project_slug="PROJ",
        active_states=("Todo",),
        terminal_states=("Done",),
    )
    client._site = "https://jira.example"
    client._owns_client = False
    client._client = SimpleNamespace()
    assert client.__enter__() is client
    assert client.__exit__(None, None, None) is None

    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: httpx.Response(
            200, json={"issues": [], "isLast": True}
        ),
    )
    assert client.fetch_issues_by_states(["Todo"]) == []
    assert client.fetch_issue_states_by_ids([]) == []

    empty_issue = issue_module.Issue(
        id="", identifier="", title="", description=None, priority=None, state="Todo"
    )
    with pytest.raises(Exception, match="both empty"):
        client.update_state(empty_issue, "Done")

    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: httpx.Response(200, json={"issues": "bad"}),
    )
    with pytest.raises(Exception, match="issues missing"):
        client._search_paginated("query", fields="id", minimal=True)

    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: httpx.Response(
            200, json={"issues": ["bad"], "isLast": False}
        ),
    )
    assert client._search_paginated("query", fields="id", minimal=True) == []

    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: httpx.Response(200, json={"transitions": "bad"}),
    )
    with pytest.raises(Exception, match="transitions field missing"):
        client._find_transition_id("J-1", "Done")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: httpx.Response(200, json={"transitions": ["bad"]}),
    )
    with pytest.raises(Exception, match="no transition"):
        client._find_transition_id("J-1", "Done")


def test_linear_tracker_remaining_relation_context_and_pagination_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = linear_tracker._normalize_node(
        {
            "id": "1",
            "state": {"name": "Todo"},
            "inverseRelations": {"nodes": ["bad", {"type": "relates", "issue": {}}]},
        }
    )
    assert normalized.blocked_by == ()

    client = object.__new__(linear_tracker.LinearClient)
    client._tracker = TrackerConfig(
        kind="linear",
        endpoint="https://linear.example",
        api_key="token",
        project_slug="project",
        active_states=("Todo",),
        terminal_states=("Done",),
    )
    client._owns_client = False
    client._client = SimpleNamespace()
    client._state_id_cache = {}
    client._issue_team_cache = {}
    assert client.__enter__() is client
    assert client.__exit__(None, None, None) is None

    monkeypatch.setattr(
        client,
        "_post",
        lambda _body: {
            "data": {
                "workflowStates": {"nodes": ["bad", {"id": "state-1", "name": "Done"}]}
            }
        },
    )
    assert client._state_id_for("team", "Done") == "state-1"

    monkeypatch.setattr(
        client,
        "_post",
        lambda _body: {
            "data": {"issues": {"nodes": ["bad"], "pageInfo": {"hasNextPage": False}}}
        },
    )
    assert (
        client._paginate("query", states=["Todo"], normalizer=lambda node: node) == []
    )


def test_cli_runtime_rejects_noninteractive_tui_and_cleans_start_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_main = importlib.import_module("symphony.cli.main")
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("body", encoding="utf-8")
    cfg = _cli_runtime_config()

    class State:
        def __init__(self, _path: Path) -> None:
            pass

        def reload(self) -> tuple[Any, None]:
            return cfg, None

    log = SimpleNamespace(error=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_main, "configure_logging", lambda _level: log)
    monkeypatch.setattr(cli_main, "resolve_workflow_path", lambda _path: workflow)
    monkeypatch.setattr(cli_main, "WorkflowState", State)
    monkeypatch.setattr(cli_main.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(cli_main.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    assert asyncio.run(cli_main._run(_cli_runtime_args(workflow, tui=True))) == 1

    awake = SimpleNamespace(start=lambda: None, stop=lambda: None)

    class BrokenOrchestrator:
        def __init__(self, _state: Any) -> None:
            pass

        async def start(self) -> None:
            raise SymphonyError("startup broke")

    monkeypatch.setattr(cli_main, "KeepAwake", lambda: awake)
    monkeypatch.setattr(cli_main, "Orchestrator", BrokenOrchestrator)
    monkeypatch.setattr(cli_main, "_startup_preflight_failures", lambda _cfg: [])
    assert asyncio.run(cli_main._run(_cli_runtime_args(workflow, keep_awake=True))) == 1


def test_cli_runtime_headless_shutdown_progress_default_and_no_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_main = importlib.import_module("symphony.cli.main")
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("body", encoding="utf-8")
    cfg = _cli_runtime_config()
    events: list[str] = []

    class State:
        def __init__(self, _path: Path) -> None:
            pass

        def reload(self) -> tuple[Any, None]:
            return cfg, None

    class Orchestrator:
        def __init__(self, _state: Any) -> None:
            pass

        async def start(self) -> None:
            events.append("start")

        async def stop(self) -> None:
            events.append("stop")

    class Progress:
        def __init__(self, _orch: Any, _state: Any, path: Path, **_kwargs: Any) -> None:
            assert path == (tmp_path / "WORKFLOW-PROGRESS.md").resolve()

        def register(self) -> None:
            events.append("progress")

    class Loop:
        calls = 0

        def add_signal_handler(self, _sig: Any, callback: Any) -> None:
            self.calls += 1
            if self.calls == 1:
                callback()
            else:
                raise NotImplementedError

    log = SimpleNamespace(
        error=lambda *_args, **_kwargs: None,
        info=lambda event, **_kwargs: events.append(event),
    )
    monkeypatch.setattr(cli_main, "configure_logging", lambda _level: log)
    monkeypatch.setattr(cli_main, "resolve_workflow_path", lambda _path: workflow)
    monkeypatch.setattr(cli_main, "WorkflowState", State)
    monkeypatch.setattr(cli_main, "Orchestrator", Orchestrator)
    monkeypatch.setattr(cli_main, "ProgressFileWriter", Progress)
    monkeypatch.setattr(cli_main, "_startup_preflight_failures", lambda _cfg: [])
    monkeypatch.setattr(cli_main.asyncio, "get_running_loop", lambda: Loop())

    result = asyncio.run(cli_main._run(_cli_runtime_args(workflow, progress_md=True)))
    assert result == 0
    assert events[:3] == ["start", "progress", "progress_md_active"]
    assert "http_extension_disabled" in events
    assert events[-2:] == ["stop", "shutdown_complete"]


def test_cli_runtime_tui_http_progress_override_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_main = importlib.import_module("symphony.cli.main")
    tui_module = importlib.import_module("symphony.tui")
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("body", encoding="utf-8")
    cfg = _cli_runtime_config()
    events: list[str] = []

    class State:
        def __init__(self, _path: Path) -> None:
            pass

        def reload(self) -> tuple[Any, None]:
            return cfg, None

    class Orchestrator:
        def __init__(self, _state: Any) -> None:
            pass

        async def start(self) -> None:
            events.append("start")

        async def stop(self) -> None:
            events.append("stop")

    class Tui:
        def __init__(self, _orch: Any, _state: Any) -> None:
            pass

        async def run(self) -> None:
            events.append("tui")
            raise RuntimeError("TUI closed")

    class Runner:
        async def cleanup(self) -> None:
            events.append("cleanup")

    class Progress:
        def __init__(self, _orch: Any, _state: Any, path: Path, **_kwargs: Any) -> None:
            assert path == (Path.cwd() / "relative-progress.md").resolve()

        def register(self) -> None:
            events.append("progress")

    log = SimpleNamespace(
        error=lambda *_args, **_kwargs: None,
        info=lambda event, **_kwargs: events.append(event),
    )

    async def run_server(_app: Any, _host: str, _port: int) -> tuple[Any, int]:
        return Runner(), 43210

    monkeypatch.setattr(cli_main, "configure_logging", lambda _level: log)
    monkeypatch.setattr(cli_main, "resolve_workflow_path", lambda _path: workflow)
    monkeypatch.setattr(cli_main, "WorkflowState", State)
    monkeypatch.setattr(cli_main, "Orchestrator", Orchestrator)
    monkeypatch.setattr(cli_main, "ProgressFileWriter", Progress)
    monkeypatch.setattr(
        cli_main,
        "KeepAwake",
        lambda: SimpleNamespace(
            start=lambda: events.append("awake-start"),
            stop=lambda: events.append("awake-stop"),
        ),
    )
    monkeypatch.setattr(cli_main, "build_app", lambda _orch: object())
    monkeypatch.setattr(cli_main, "run_server", run_server)
    monkeypatch.setattr(cli_main, "_startup_preflight_failures", lambda _cfg: [])
    monkeypatch.setattr(tui_module, "KanbanTUI", Tui)
    monkeypatch.setattr(cli_main.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(cli_main.sys, "stdout", SimpleNamespace(isatty=lambda: True))

    result = asyncio.run(
        cli_main._run(
            _cli_runtime_args(
                workflow,
                tui=True,
                progress_md=True,
                progress_md_path="relative-progress.md",
                port=0,
                keep_awake=True,
            )
        )
    )
    assert result == 0
    assert "tui" in events
    assert "http_extension_active" in events
    assert events[-4:] == ["stop", "cleanup", "awake-stop", "shutdown_complete"]


def test_cli_inline_helpers_relative_wiki_and_existing_empty_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_main = importlib.import_module("symphony.cli.main")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli_main._wiki_sweep_main(["--root", "wiki", "--dry-run"]) == 0
    capsys.readouterr()

    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("body", encoding="utf-8")
    registry_path = cli_main.registry_path_for_workflow(workflow)
    registry = cli_main.RunRegistry(registry_path)
    registry.close()
    assert cli_main._runs_main([str(workflow)]) == 0
    assert "no runs recorded" in capsys.readouterr().out


def test_workflow_mutation_load_and_atomic_write_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(workflow_mutate.WorkflowMutationError, match="not terminated"):
        workflow_mutate._split_workflow("---\ntracker: {}\n")

    class Unreadable:
        def read_text(self, **_kwargs: Any) -> str:
            raise OSError("access denied")

    with pytest.raises(workflow_mutate.WorkflowMutationError, match="cannot read"):
        workflow_mutate._load_frontmatter(Unreadable())  # type: ignore[arg-type]

    empty = _write_workflow_front(tmp_path, "")
    data, body = workflow_mutate._load_frontmatter(empty)
    assert data == {}
    assert body == "Body\n"
    sequence = _write_workflow_front(tmp_path, "- one")
    with pytest.raises(workflow_mutate.WorkflowMutationError, match="YAML map"):
        workflow_mutate._load_frontmatter(sequence)

    monkeypatch.setattr(
        workflow_mutate.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    monkeypatch.setattr(
        workflow_mutate.os,
        "unlink",
        lambda *_args: (_ for _ in ()).throw(OSError("unlink failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        workflow_mutate._write_workflow_atomic(
            empty, workflow_mutate.CommentedMap(), "Body\n"
        )


def test_workflow_mutation_mapping_and_stage_prompt_boundaries(tmp_path: Path) -> None:
    data = workflow_mutate.CommentedMap({"tracker": "bad"})
    workflow_mutate._rename_map_keys(data, "tracker", "states", {}, [])
    data["tracker"] = {"states": "bad"}
    workflow_mutate._rename_map_keys(data, "tracker", "states", {}, [])

    workflow = tmp_path / "WORKFLOW.md"
    workflow_mutate._add_stage_prompts(
        workflow_mutate.CommentedMap({"prompts": {"stages": {}}}),
        workflow,
        [workflow_mutate.StateSpec("QA")],
    )
    workflow_mutate._add_stage_prompts(
        workflow_mutate.CommentedMap({"prompts": {"stages": {"Todo": " "}}}),
        workflow,
        [workflow_mutate.StateSpec("QA")],
    )
    with pytest.raises(workflow_mutate.WorkflowMutationError, match="escapes"):
        workflow_mutate._add_stage_prompts(
            workflow_mutate.CommentedMap(
                {"prompts": {"stages": {"Todo": "../../outside/todo.md"}}}
            ),
            workflow,
            [workflow_mutate.StateSpec("QA")],
        )


def test_workflow_mutation_prompt_lookup_read_and_write_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_prompts = _write_workflow_front(tmp_path, "prompts: bad")
    assert workflow_mutate.resolve_prompt_path(no_prompts, "Todo") is None
    no_stages = _write_workflow_front(tmp_path, "prompts:\n  stages: bad")
    assert workflow_mutate.resolve_prompt_path(no_stages, "Todo") is None
    configured = _write_workflow_front(
        tmp_path, "prompts:\n  stages:\n    Todo: prompts/todo.md"
    )
    prompt_info = workflow_mutate.read_prompt(configured, "Todo")
    assert prompt_info == {
        "state": "Todo",
        "path": "prompts/todo.md",
        "content": "",
        "exists": False,
    }
    with pytest.raises(workflow_mutate.WorkflowMutationError, match="too large"):
        workflow_mutate.write_prompt(configured, "Todo", "x" * 512_001)

    monkeypatch.setattr(
        workflow_mutate.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    monkeypatch.setattr(
        workflow_mutate.os,
        "unlink",
        lambda *_args: (_ for _ in ()).throw(OSError("unlink failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        workflow_mutate.write_prompt(configured, "Todo", "new prompt")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"enabled": 1},
        {"interval_ms": True},
        {"max_turns": True},
        {"agent_kind": 1},
    ],
)
def test_workflow_mutation_continuous_improvement_type_validation(
    tmp_path: Path, kwargs: dict[str, Any]
) -> None:
    workflow = _write_workflow_front(tmp_path, "tracker: {}")
    with pytest.raises(workflow_mutate.WorkflowMutationError):
        workflow_mutate.set_continuous_improvement_settings(workflow, **kwargs)


def test_workflow_mutation_preset_removes_stale_and_case_duplicate_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _write_workflow_front(
        tmp_path,
        """tracker:
  active_states: [Old]
  terminal_states: [Done]
prompts:
  stages:
    todo: old-todo.md
    Stale: stale.md
agent:
  stage_kinds:
    Old: codex""",
    )
    preset = workflow_mutate.LanePreset(
        name="coverage",
        label="Coverage",
        active_states=("Todo",),
        terminal_states=("Done",),
        state_descriptions={"Todo": "Work"},
        base_prompt="base.md",
        stage_prompts={"Todo": "todo.md"},
    )
    require_prompt_files = workflow_mutate._require_preset_prompt_files
    monkeypatch.setattr(workflow_mutate, "get_lane_preset", lambda _name: preset)
    monkeypatch.setattr(
        workflow_mutate, "_require_preset_prompt_files", lambda *_args: None
    )
    plan = workflow_mutate.apply_lane_preset(workflow, "coverage")
    assert plan.removed == ["Old"]
    data, _body = workflow_mutate._load_frontmatter(workflow)
    assert data["prompts"]["stages"] == {"Todo": "todo.md"}

    escaping = dataclasses.replace(preset, base_prompt="../outside.md")
    with pytest.raises(workflow_mutate.WorkflowMutationError, match="escapes"):
        require_prompt_files(workflow, escaping)


def test_findings_parser_bounds_adversarially_large_source_line_number() -> None:
    huge_line_number = "9" * 5_000
    rows = orchestrator_parsing._parse_findings_rows(
        "## Review Findings\n"
        f"- HIGH: src/security_boundary.py:{huge_line_number} — inspect safely"
    )

    assert rows == [
        {
            "severity": "HIGH",
            "file": "src/security_boundary.py",
            "line": 0,
            "fix": "inspect safely",
        }
    ]


def test_findings_parser_bounds_adversarially_large_contract_row_number() -> None:
    huge_row_number = "9" * 5_000
    rows = orchestrator_parsing._parse_findings_rows(
        "## Contract Failure\n"
        f"- ## Verify row {huge_row_number}: found `FAIL`; expected `PASS`"
    )

    assert rows == [
        {
            "severity": "CONTRACT",
            "file": "",
            "line": 0,
            "fix": "## Verify row 0: found `FAIL`; expected `PASS`",
            "section": "## Verify",
            "found": "FAIL",
            "expected": "`PASS`",
        }
    ]


def test_continuation_acquisition_rolls_back_corrupt_empty_checkpoint_timestamp(
    tmp_path: Path,
) -> None:
    registry = run_registry_module.RunRegistry(tmp_path / "state.db")
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    issue = issue_module.Issue(
        id="id-CORRUPT-1",
        identifier="CORRUPT-1",
        title="Corrupt continuation boundary",
        description="",
        priority=None,
        state="In Progress",
        created_at=now,
        updated_at=now,
    )
    predecessor = registry.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert predecessor is not None
    assert registry.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=predecessor,
        resume_session_id="private-session",
        state=issue.state,
        turn=1,
        now=now + timedelta(seconds=1),
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=predecessor,
        status="shutdown_interrupted",
        now=now + timedelta(seconds=2),
    )

    with sqlite3.connect(registry.path) as connection:
        connection.execute(
            "UPDATE runs SET checkpointed_at = '' WHERE run_id = ?",
            (predecessor,),
        )

    assert (
        registry.acquire_continuation_run(
            issue,
            continued_from_run_id=predecessor,
            workspace_path=tmp_path / issue.identifier,
            attempt=1,
            attempt_kind="recovery",
            agent_kind="codex",
            now=now + timedelta(seconds=3),
        )
        is None
    )
    with sqlite3.connect(registry.path) as connection:
        successor_count = connection.execute(
            "SELECT count(*) FROM runs WHERE continued_from_run_id = ?",
            (predecessor,),
        ).fetchone()[0]
    assert successor_count == 0
    assert (
        registry.acquire_run(
            issue,
            workspace_path=tmp_path / "fresh" / issue.identifier,
            attempt=1,
            attempt_kind="retry",
            agent_kind="codex",
            now=now + timedelta(seconds=4),
        )
        is not None
    )
    registry.close()


def test_strict_release_yaml_loader_rejects_non_mapping_node() -> None:
    loader = release_contracts._UniqueKeySafeLoader("scalar")
    node = yaml.ScalarNode(tag="tag:yaml.org,2002:str", value="scalar")
    try:
        with pytest.raises(
            yaml.constructor.ConstructorError,
            match="expected a mapping node, but found scalar",
        ):
            loader.construct_mapping(node)
    finally:
        loader.dispose()
