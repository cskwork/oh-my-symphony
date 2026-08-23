"""Coverage contracts for doctor and backend lifecycle."""
# ruff: noqa: F405

from tests.coverage_cases._runtime_support import *  # noqa: F403


def test_git_inspect_patch_is_capped() -> None:
    proc = _completed(0, stdout="x" * (git_inspect.MAX_PATCH_CHARS + 1))
    result = git_inspect._capped_patch(proc)
    assert result["truncated"] is True
    assert len(result["patch"]) == git_inspect.MAX_PATCH_CHARS


def test_git_sandbox_resolve_and_pointer_io_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_resolve = Path.resolve

    def fail_start(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path == tmp_path:
            raise OSError("cannot resolve")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_start)
    assert git_sandbox.resolve_git_dir(tmp_path) is None
    monkeypatch.setattr(Path, "resolve", original_resolve)

    marker = tmp_path / ".git"
    original_is_dir = Path.is_dir
    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda path: (
            (_ for _ in ()).throw(OSError("marker denied"))
            if path == marker
            else original_is_dir(path)
        ),
    )
    assert git_sandbox.resolve_git_dir(tmp_path) is None
    monkeypatch.setattr(Path, "is_dir", original_is_dir)

    marker.write_text("gitdir:\n", encoding="utf-8")
    assert git_sandbox._read_gitdir_pointer(marker) is None
    marker.write_text("gitdir: ../admin\n", encoding="utf-8")
    assert git_sandbox._read_gitdir_pointer(marker) == (tmp_path / "../admin").resolve()

    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("unreadable"))
            if path == marker
            else original_read(path, *args, **kwargs)
        ),
    )
    assert git_sandbox._read_gitdir_pointer(marker) is None

    monkeypatch.setattr(Path, "read_text", original_read)
    target = marker.parent / "../admin"
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("bad pointer"))
            if path == target
            else original_resolve(path, *args, **kwargs)
        ),
    )
    assert git_sandbox._read_gitdir_pointer(marker) is None


def test_git_sandbox_common_dir_and_root_checks_fall_back_on_io_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    common_file = git_dir / git_sandbox._COMMONDIR_FILE
    monkeypatch.setattr(git_sandbox, "resolve_git_dir", lambda _start: git_dir)
    assert git_sandbox.resolve_git_common_dir(tmp_path) == git_dir

    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: (
            (_ for _ in ()).throw(OSError("common denied"))
            if path == common_file
            else original_is_file(path)
        ),
    )
    assert git_sandbox.resolve_git_common_dir(tmp_path) == git_dir
    monkeypatch.setattr(Path, "is_file", original_is_file)

    common_file.write_text("", encoding="utf-8")
    assert git_sandbox.resolve_git_common_dir(tmp_path) == git_dir
    common_file.write_text("../common", encoding="utf-8")
    original_resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("bad target"))
            if path == git_dir / "../common"
            else original_resolve(path, *args, **kwargs)
        ),
    )
    assert git_sandbox.resolve_git_common_dir(tmp_path) == git_dir

    monkeypatch.setattr(
        git_sandbox,
        "resolve_git_common_dir",
        lambda _start: git_dir,
    )
    original_is_dir = Path.is_dir
    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda path: (
            (_ for _ in ()).throw(OSError("denied"))
            if path == git_dir
            else original_is_dir(path)
        ),
    )
    assert git_sandbox.writable_git_roots(tmp_path) == []


def test_git_sandbox_outside_roots_uses_unresolved_cwd_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("denied"))
            if path == tmp_path
            else original_resolve(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(git_sandbox, "writable_git_roots", lambda _start: [])
    assert git_sandbox.git_roots_outside(tmp_path) == []


def test_doctor_bind_port_reports_free_ephemeral_port() -> None:
    result = doctor._bind_port("127.0.0.1", 0)
    assert result.status == "pass" and "is free" in result.message


@pytest.mark.parametrize("kind", ["claude", "gemini", "pi", "prime-agent"])
def test_doctor_agent_cli_selects_each_backend_command(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    cfg = _doctor_cfg(kind, command="agent-cli --serve")
    monkeypatch.setattr(doctor.shutil, "which", lambda binary: f"/bin/{binary}")
    assert doctor.check_agent_cli(cfg).status == "pass"


def test_doctor_agent_cli_reports_unsupported_unparseable_empty_and_python_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert doctor.check_agent_cli(_doctor_cfg("future")).status == "fail"
    assert (
        "not parseable"
        in doctor.check_agent_cli(
            _doctor_cfg("claude", command='"unterminated')
        ).message
    )
    assert (
        "command is empty"
        in doctor.check_agent_cli(_doctor_cfg("gemini", command="")).message
    )
    monkeypatch.setattr(doctor.shutil, "which", lambda _binary: None)
    result = doctor.check_agent_cli(_doctor_cfg("codex", command="python -m module"))
    assert result.status == "pass" and doctor.sys.executable in result.message


def test_doctor_hook_source_mapping_falls_back_on_io_and_missing_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("body", encoding="utf-8")
    cfg = SimpleNamespace(workflow_path=workflow)
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unreadable")),
    )
    assert doctor._after_create_source_lines(cfg, "echo ok") == {}
    assert doctor._format_hook_source_line(cfg, 3, {}) == "line 3"
    assert doctor._warning_after_create_lines(cfg, "\n# comment\necho ok") == []

    monkeypatch.undo()
    assert doctor._after_create_source_lines(cfg, "echo ok") == {}


@pytest.mark.parametrize(
    ("auth_type", "env", "expected"),
    [
        ("gemini-api-key", {}, "fail"),
        ("gemini-api-key", {"GEMINI_API_KEY": "x"}, "pass"),
        ("vertex-ai", {}, "fail"),
        ("vertex-ai", {"GOOGLE_API_KEY": "x"}, "pass"),
        ("unsupported", {}, "fail"),
    ],
)
def test_doctor_gemini_auth_classifies_selected_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auth_type: str,
    env: dict[str, str],
    expected: str,
) -> None:
    settings = tmp_path / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"selectedAuthType": auth_type}), encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert doctor.check_gemini_auth(_doctor_cfg("gemini")).status == expected


def test_doctor_gemini_auth_and_kiro_whoami_report_io_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = tmp_path / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert doctor.check_gemini_auth(_doctor_cfg("gemini")).status == "fail"
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
    )
    assert doctor._kiro_whoami().status == "fail"


def test_doctor_agy_state_directory_creation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    original_mkdir = Path.mkdir

    def mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.name == "antigravity-cli":
            raise OSError("denied")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    assert doctor.check_agy_state_dir(_doctor_cfg("agy")).status == "fail"


def test_doctor_board_reachability_skips_remote_and_missing_file_root() -> None:
    cfg = SimpleNamespace(tracker=SimpleNamespace(kind="linear", board_root=None))
    assert doctor.check_board_reachable_from_workspace(cfg).status == "pass"
    cfg.tracker.kind = "file"
    assert doctor.check_board_reachable_from_workspace(cfg).status == "fail"


def test_doctor_board_reachability_accepts_external_board(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "workflow"
    workflow_dir.mkdir()
    external = tmp_path / "external-board"
    external.mkdir()
    cfg = SimpleNamespace(
        tracker=SimpleNamespace(kind="file", board_root=external),
        workflow_path=workflow_dir / "WORKFLOW.md",
    )
    assert doctor.check_board_reachable_from_workspace(cfg).status == "pass"


def test_doctor_board_cli_handles_probe_error_and_module_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import symphony.orchestrator.helpers as orchestrator_helpers

    cfg = SimpleNamespace(tracker=SimpleNamespace(kind="file"))
    monkeypatch.setattr(
        orchestrator_helpers,
        "resolve_symphony_cli",
        lambda: "python -m symphony.cli",
    )
    monkeypatch.setattr(doctor, "resolve_bash", lambda: "bash")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no shell")),
    )
    result = doctor.check_symphony_cli_reachable(cfg)
    assert result.status == "fail" and "no console script" in result.message


def test_doctor_board_dependencies_skips_remote_and_absent_board(
    tmp_path: Path,
) -> None:
    cfg = SimpleNamespace(tracker=SimpleNamespace(kind="linear", board_root=None))
    assert doctor.check_board_dependencies(cfg).status == "pass"
    cfg.tracker.kind = "file"
    cfg.tracker.board_root = tmp_path / "missing"
    assert doctor.check_board_dependencies(cfg).status == "pass"


def test_doctor_board_dependencies_warns_when_tracker_scan_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import symphony.trackers.file as file_tracker

    board = tmp_path / "board"
    board.mkdir()
    cfg = SimpleNamespace(tracker=SimpleNamespace(kind="file", board_root=board))

    class BrokenTracker:
        def __init__(self, _tracker: Any) -> None:
            return None

        def scan_all(self) -> Any:
            raise SymphonyError("bad board")

    monkeypatch.setattr(file_tracker, "FileBoardTracker", BrokenTracker)
    assert doctor.check_board_dependencies(cfg).status == "warn"


def test_doctor_workspace_root_reports_create_and_write_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    cfg = SimpleNamespace(workspace_root=root)
    original_mkdir = Path.mkdir
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("mkdir denied"))
            if path == root
            else original_mkdir(path, *args, **kwargs)
        ),
    )
    assert doctor.check_workspace_root(cfg).status == "fail"

    monkeypatch.setattr(Path, "mkdir", original_mkdir)
    monkeypatch.setattr(
        doctor.tempfile,
        "NamedTemporaryFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write denied")),
    )
    assert doctor.check_workspace_root(cfg).status == "fail"


def test_doctor_git_history_reports_object_failure_and_multiple_agent_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    common = tmp_path / ".git"
    monkeypatch.setattr(doctor, "resolve_git_common_dir", lambda _repo: common)
    monkeypatch.setattr(
        doctor.tempfile,
        "NamedTemporaryFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write denied")),
    )
    assert doctor.check_git_history_writable(cfg).status == "fail"

    class TempFile:
        def __enter__(self) -> "TempFile":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(
        doctor.tempfile, "NamedTemporaryFile", lambda *_args, **_kwargs: TempFile()
    )
    monkeypatch.setattr(doctor, "writable_git_roots", lambda _repo: ["one", "two"])
    result = doctor.check_git_history_writable(cfg)
    assert result.status == "pass" and "agents also need one, two" in result.message


def test_doctor_tracker_linear_and_unknown_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = SimpleNamespace(kind="file", board_root=None, api_key="")
    assert doctor.check_tracker(SimpleNamespace(tracker=tracker)).status == "fail"
    tracker.kind = "linear"
    assert doctor.check_tracker(SimpleNamespace(tracker=tracker)).status == "fail"
    tracker.api_key = "$MISSING_LINEAR_TOKEN"
    monkeypatch.delenv("MISSING_LINEAR_TOKEN", raising=False)
    assert doctor.check_tracker(SimpleNamespace(tracker=tracker)).status == "fail"
    tracker.api_key = "token"
    assert doctor.check_tracker(SimpleNamespace(tracker=tracker)).status == "pass"
    tracker.kind = "future"
    assert doctor.check_tracker(SimpleNamespace(tracker=tracker)).status == "warn"


def test_doctor_agent_git_grant_covers_environment_literal_and_wrapper_paths() -> None:
    cfg = _doctor_cfg("gemini", command="wrapper")
    assert doctor.check_agent_git_grant(cfg).status == "pass"

    cfg = _doctor_cfg("claude", command="claude --print")
    assert "injects git roots" in doctor.check_agent_git_grant(cfg).message
    cfg.claude.command = "wrapper claude"
    result = doctor.check_agent_git_grant(cfg)
    assert result.status == "warn" and "SYMPHONY_GIT_WRITABLE_ROOTS" in result.message


def test_doctor_shell_missing_paths_are_platform_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "resolve_bash", lambda: "missing-bash")
    monkeypatch.setattr(doctor.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    monkeypatch.setattr(doctor, "_IS_WIN32", True)
    assert "Git for Windows" in doctor.check_shell().message
    monkeypatch.setattr(doctor, "_IS_WIN32", False)
    assert "install bash" in doctor.check_shell().message

    monkeypatch.setattr(doctor, "_IS_WIN32", True)
    monkeypatch.setattr(doctor, "resolve_bash", lambda: r"C:\Windows\System32\bash.exe")
    monkeypatch.setattr(doctor.os.path, "isfile", lambda _path: True)
    assert "WSL launcher" in doctor.check_shell().message


def test_doctor_workflow_registry_absent_open_error_old_and_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import symphony.orchestrator.migrations as migrations
    import symphony.orchestrator.run_registry as registry_module

    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    registry_path = tmp_path / "state.db"
    monkeypatch.setattr(
        registry_module, "registry_path_for_workflow", lambda _path: registry_path
    )
    assert doctor.check_workflow_registry(cfg).status == "pass"
    registry_path.touch()

    class BrokenRegistry:
        def __init__(self, _path: Path) -> None:
            raise RuntimeError("cannot open")

    monkeypatch.setattr(registry_module, "RunRegistry", BrokenRegistry)
    assert doctor.check_workflow_registry(cfg).status == "fail"

    class Registry:
        def __init__(self, _path: Path, version: int, applied: list[int]) -> None:
            self.version = version
            self.applied_migrations = applied

        def schema_version(self) -> int:
            return self.version

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        registry_module,
        "RunRegistry",
        lambda path: Registry(path, migrations.LATEST_SCHEMA_VERSION - 1, []),
    )
    assert doctor.check_workflow_registry(cfg).status == "fail"
    monkeypatch.setattr(
        registry_module,
        "RunRegistry",
        lambda path: Registry(path, migrations.LATEST_SCHEMA_VERSION, [2, 3]),
    )
    assert "applied 2, 3" in doctor.check_workflow_registry(cfg).message


def test_doctor_source_repository_and_app_release_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        doctor, "workflow_uses_protected_source_repo", lambda _path: True
    )
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md")
    assert doctor.check_source_repository(cfg).status == "fail"

    cfg.tracker = SimpleNamespace(kind="file", active_states=(), terminal_states=())

    class BrokenTracker:
        def __init__(self, _tracker: Any) -> None:
            raise RuntimeError("cannot inspect")

    monkeypatch.setattr(doctor, "FileBoardTracker", BrokenTracker)
    assert doctor.check_app_release_contract(cfg).status == "fail"

    class EmptyTracker:
        def __init__(self, _tracker: Any) -> None:
            return None

        def fetch_issues_by_states(self, _states: Any) -> list[Any]:
            return []

        def close(self) -> None:
            return None

    monkeypatch.setattr(doctor, "FileBoardTracker", EmptyTracker)
    import symphony.orchestrator.run_registry as registry_module

    monkeypatch.setattr(
        registry_module,
        "RunRegistry",
        lambda _path: (_ for _ in ()).throw(RuntimeError("authority unavailable")),
    )
    assert (
        "durable app-release authority"
        in doctor.check_app_release_contract(cfg).message
    )


def test_doctor_color_format_and_missing_workflow_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    rendered = doctor.format_results(
        [doctor.CheckResult("check", "pass", "ok")], color=True
    )
    assert "\x1b[32m" in rendered
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(doctor, "resolve_workflow_path", lambda _raw: missing)
    assert doctor.main([]) == 2
    assert "workflow file not found" in capsys.readouterr().err


def test_codex_helpers_cover_symlink_failure_coercion_and_event_categories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    link_like = root / "link"
    link_like.write_text("x", encoding="utf-8")
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link_like)
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("bad link"))
            if path == link_like
            else original_resolve(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(codex_backend, "git_roots_outside", lambda *_roots: [])
    assert codex_backend._scan_workspace_symlinks(root) == []
    assert codex_backend._coerce_turn("not-a-map") == {}

    assert codex_backend._normalize_event_name("turn/cancelled") == (
        codex_backend.EVENT_TURN_CANCELLED
    )
    assert codex_backend._normalize_event_name("input/required") == (
        codex_backend.EVENT_TURN_INPUT_REQUIRED
    )
    assert codex_backend._normalize_event_name("tool/request") == (
        codex_backend.EVENT_UNSUPPORTED_TOOL_CALL
    )
    assert codex_backend._normalize_event_name("notification") == (
        codex_backend.EVENT_NOTIFICATION
    )
    assert codex_backend._command_text(3) == ""
    assert codex_backend._empty_user_input_answers({"questions": "bad"}) == {}
    assert codex_backend._empty_user_input_answers(
        {"questions": ["bad", {"id": "q1"}]}
    ) == {"q1": {"answers": []}}


def test_codex_backend_session_lifecycle_and_turn_parameter_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._cwd = tmp_path
    backend._thread_sandbox = "workspace-write"
    backend._approval_policy = "never"
    backend._thread_id = None
    backend._latest_rate_limits = {"remaining": 2}
    backend._sandbox_policy = "workspace-write"
    backend._writable_roots = [str(tmp_path)]
    backend._codex = SimpleNamespace(model="model", reasoning_effort="high")
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    monkeypatch.setattr(backend, "_emit", emit)
    assert backend.latest_rate_limits == {"remaining": 2}

    async def initialize(method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == codex_backend.METHOD_INITIALIZE
        assert params["clientInfo"]["name"] == "symphony"
        return {"ok": True}

    monkeypatch.setattr(backend, "_request", initialize)
    assert asyncio.run(backend.initialize()) == {"ok": True}

    async def no_thread(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(backend, "_request", no_thread)
    with pytest.raises(ResponseError, match="no thread id"):
        asyncio.run(backend.start_session(initial_prompt="ignored", issue_title=None))

    async def thread(_method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert params["sandbox"] == "workspace-write"
        assert params["approvalPolicy"] == "never"
        return {"thread": {"id": "thread-1"}}

    monkeypatch.setattr(backend, "_request", thread)
    assert (
        asyncio.run(
            backend.start_session(initial_prompt="ignored", issue_title="title")
        )
        == "thread-1"
    )
    assert events[-1][1]["session_id"] == "thread-1"

    backend._thread_id = None
    with pytest.raises(ResponseError, match="before thread started"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))
    assert asyncio.run(backend.resume_session("")) is False

    backend._thread_id = "thread-1"
    params = backend._build_turn_params("body")
    assert params["sandboxPolicy"]["type"] == "workspaceWrite"
    assert params["approvalPolicy"] == "never"
    assert params["model"] == "model" and params["effort"] == "high"


def test_codex_request_reports_unstarted_broken_timeout_and_rpc_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._closed = False
    backend._process = None
    with pytest.raises(ResponseError, match="subprocess not started"):
        asyncio.run(backend._request("method", {}))
    with pytest.raises(ResponseError, match="subprocess not started"):
        asyncio.run(backend._write_json_rpc({}))

    backend._process = SimpleNamespace(stdin=object())
    backend._next_id = 1
    backend._pending = {}
    backend._codex = SimpleNamespace(read_timeout_ms=1)

    async def broken(_payload: dict[str, Any]) -> None:
        raise BrokenPipeError("closed")

    monkeypatch.setattr(backend, "_write_json_rpc", broken)
    with pytest.raises(Exception, match="stdin closed"):
        asyncio.run(backend._request("method", {}))
    assert backend._pending == {}

    async def no_reply(_payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(backend, "_write_json_rpc", no_reply)
    with pytest.raises(Exception, match="response timeout"):
        asyncio.run(backend._request("method", {}, timeout_s=0.001))
    assert backend._pending == {}

    async def rpc_error(payload: dict[str, Any]) -> None:
        backend._pending[payload["id"]].set_result(
            {"error": {"message": "server rejected request"}}
        )

    monkeypatch.setattr(backend, "_write_json_rpc", rpc_error)
    with pytest.raises(ResponseError, match="server rejected request"):
        asyncio.run(backend._request("method", {}))


def test_pi_backend_invalid_resume_rate_limit_and_message_helpers() -> None:
    backend = object.__new__(pi_backend.PiBackend)
    backend._closed = True
    assert backend.latest_rate_limits is None
    assert asyncio.run(backend.resume_session("session")) is False
    assert pi_backend._extract_text("bad") == ""
    assert pi_backend._extract_last_assistant_message("bad") == ""
    assert (
        pi_backend._extract_last_assistant_message(
            [
                {"role": "user", "content": "ignored"},
                {"role": "assistant", "content": "ok"},
            ]
        )
        == "ok"
    )


def test_claude_backend_rate_limit_and_error_message_helpers() -> None:
    backend = object.__new__(claude_backend.ClaudeCodeBackend)
    backend._latest_rate_limits = {"limit": 10}
    assert backend.latest_rate_limits == {"limit": 10}
    assert (
        claude_backend._error_result_message(
            {"error": {"content": [{"type": "text", "text": "nested error"}]}}
        )
        == "nested error"
    )
    assert claude_backend._error_result_message({"subtype": "error_max_turns"}) == (
        "error_max_turns"
    )
    assert claude_backend._error_result_message({}) == "claude turn failed"


def test_codex_prepare_command_exports_and_injects_writable_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._cwd = tmp_path / "cwd"
    backend._workspace_root = tmp_path / "root"
    backend._thread_sandbox = "workspace-write"
    backend._sandbox_policy = None
    backend._codex = SimpleNamespace(command="codex app-server")
    roots = [str(tmp_path / "outside")]
    monkeypatch.setattr(codex_backend, "git_roots_env", lambda *_args: {})
    monkeypatch.setattr(codex_backend, "_scan_workspace_symlinks", lambda *_args: roots)
    command, env = backend._prepare_command_and_env()
    assert command.startswith("codex -c ")
    assert env["SYMPHONY_CODEX_WRITABLE_ROOTS"] == roots[0]
    assert backend._writable_roots == roots

    backend._codex.command = "wrapper-script"
    command, env = backend._prepare_command_and_env()
    assert command == "wrapper-script"
    assert env["SYMPHONY_CODEX_WRITABLE_ROOTS"] == roots[0]


def test_codex_symlink_scan_returns_resolved_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    link_like = root / "link"
    link_like.write_text("x", encoding="utf-8")
    target = tmp_path / "target"
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link_like)
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: (
            target if path == link_like else original_resolve(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(codex_backend, "git_roots_outside", lambda *_args: [])
    assert codex_backend._scan_workspace_symlinks(root) == [str(target)]


def test_codex_turn_resolution_success_completion_and_nondict_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._thread_id = "thread-1"
    backend._current_turn_id = None
    backend._latest_assistant_message = "answer"
    backend._turn_completion_waiter = None
    backend._codex = SimpleNamespace(turn_timeout_ms=1000)
    events: list[str] = []

    async def emit(event: str, _payload: dict[str, Any]) -> None:
        events.append(event)

    async def immediate(
        _method: str, _params: dict[str, Any], *, timeout_s: float
    ) -> dict[str, Any]:
        del timeout_s
        return {"turn": {"id": "turn-1", "status": "completed"}}

    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_request", immediate)
    completion: asyncio.Future[dict[str, Any]]

    async def run_immediate() -> None:
        nonlocal completion
        completion = asyncio.get_running_loop().create_future()
        turn = await backend._send_turn_and_resolve({}, completion)
        assert turn["status"] == "completed"

    asyncio.run(run_immediate())

    async def in_progress(
        _method: str, _params: dict[str, Any], *, timeout_s: float
    ) -> dict[str, Any]:
        del timeout_s
        return {"turn": {"id": "turn-2", "status": "inProgress"}}

    monkeypatch.setattr(backend, "_request", in_progress)

    async def run_completed() -> None:
        future = asyncio.get_running_loop().create_future()
        future.set_result({"turn": {"id": "turn-2", "status": "completed"}})
        turn = await backend._send_turn_and_resolve({}, future)
        assert turn["status"] == "completed"

    asyncio.run(run_completed())
    with pytest.raises(Exception, match="opaque error"):
        asyncio.run(
            backend._raise_for_terminal_status(
                {"status": "failed", "error": "opaque error"}
            )
        )


def test_codex_run_turn_emits_success_and_clears_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._thread_id = "thread-1"
    backend._current_turn_id = "turn-1"
    backend._latest_assistant_message = "answer"
    backend._turn_completion_waiter = None
    events: list[str] = []

    monkeypatch.setattr(backend, "_build_turn_params", lambda _prompt: {})

    def arm() -> asyncio.Future[dict[str, Any]]:
        future = asyncio.get_running_loop().create_future()
        backend._turn_completion_waiter = future
        return future

    async def resolve(
        _params: dict[str, Any], _completion: asyncio.Future[dict[str, Any]]
    ) -> dict[str, Any]:
        return {"status": "completed"}

    async def terminal(_turn: dict[str, Any]) -> None:
        return None

    async def emit(event: str, _payload: dict[str, Any]) -> None:
        events.append(event)

    monkeypatch.setattr(backend, "_arm_completion_waiter", arm)
    monkeypatch.setattr(backend, "_send_turn_and_resolve", resolve)
    monkeypatch.setattr(backend, "_raise_for_terminal_status", terminal)
    monkeypatch.setattr(backend, "_emit", emit)
    result = asyncio.run(backend.run_turn(prompt="body", is_continuation=False))
    assert result.last_message == "answer"
    assert events == [codex_backend.EVENT_TURN_COMPLETED]
    assert backend._turn_completion_waiter is None


def test_codex_stdout_and_stderr_readers_route_response_and_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._process = SimpleNamespace(
        stdout=_AsyncLineStream(b"\n", b'{"id": 1, "result": {"ok": true}}\n', b""),
        stderr=_AsyncLineStream(RuntimeError("stderr failed")),
        returncode=0,
    )
    backend._pending = {}
    backend._turn_completion_waiter = None
    backend._closed = False

    async def emit(_event: str, _payload: dict[str, Any]) -> None:
        return None

    async def run_reader() -> None:
        future = asyncio.get_running_loop().create_future()
        backend._pending[1] = future
        await backend._stdout_reader()
        assert (await future)["result"] == {"ok": True}

    monkeypatch.setattr(backend, "_emit", emit)
    asyncio.run(run_reader())
    asyncio.run(backend._stderr_reader())


def test_codex_notifications_cover_rate_item_approval_and_tool_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._latest_rate_limits = None
    events: list[tuple[str, Any]] = []
    handled: list[str] = []

    async def emit(event: str, payload: Any) -> None:
        events.append((event, payload))

    async def approval(_params: Any) -> None:
        handled.append("approval")

    async def tool(_params: Any) -> None:
        handled.append("tool")

    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_handle_approval", approval)
    monkeypatch.setattr(backend, "_handle_tool_call", tool)
    asyncio.run(
        backend._handle_notification(
            {
                "method": codex_backend.NOTIF_RATE_LIMITS,
                "params": {"rateLimits": {"x": 1}},
            }
        )
    )
    assert backend._latest_rate_limits == {"x": 1}
    asyncio.run(
        backend._handle_notification(
            {"method": codex_backend.NOTIF_RATE_LIMITS, "params": {"x": 2}}
        )
    )
    assert backend._latest_rate_limits == {"x": 2}
    asyncio.run(
        backend._handle_notification(
            {
                "method": codex_backend.NOTIF_ITEM_COMPLETED,
                "params": {"item": {"type": "tool"}},
            }
        )
    )
    asyncio.run(
        backend._handle_notification({"method": "approval.requested", "params": {}})
    )
    asyncio.run(
        backend._handle_notification({"method": "tool.requested", "params": {}})
    )
    assert handled == ["approval", "tool"]
    assert events[-1][1]["item"]["type"] == "tool"


def test_codex_start_reports_callback_and_missing_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._cwd = tmp_path
    backend._on_process_started = lambda pid: started.append(pid)
    backend._reader_task = None
    backend._stderr_task = None
    started: list[int] = []
    monkeypatch.setattr(backend, "_prepare_command_and_env", lambda: ("command", {}))

    async def no_reader() -> None:
        return None

    monkeypatch.setattr(backend, "_stdout_reader", no_reader)
    monkeypatch.setattr(backend, "_stderr_reader", no_reader)
    processes = iter(
        [
            SimpleNamespace(pid=42, stdout=object(), stdin=object(), stderr=object()),
            SimpleNamespace(pid=43, stdout=None, stdin=object(), stderr=object()),
        ]
    )

    async def spawn(*_args: Any, **_kwargs: Any) -> Any:
        return next(processes)

    monkeypatch.setattr(codex_backend.asyncio, "create_subprocess_exec", spawn)
    asyncio.run(backend.start())
    assert started == [42]
    with pytest.raises(Exception, match="pipes not available"):
        asyncio.run(backend.start())
    assert started == [42, 43]


def test_codex_turn_timeout_paths_emit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._codex = SimpleNamespace(turn_timeout_ms=1)
    events: list[str] = []

    async def emit(event: str, _payload: dict[str, Any]) -> None:
        events.append(event)

    async def request_timeout(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise codex_backend.ResponseTimeout("timeout")

    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_request", request_timeout)

    async def first_timeout() -> None:
        future = asyncio.get_running_loop().create_future()
        with pytest.raises(Exception, match="turn timed out"):
            await backend._send_turn_and_resolve({}, future)

    asyncio.run(first_timeout())

    async def in_progress(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"turn": {"id": "turn", "status": "inProgress"}}

    monkeypatch.setattr(backend, "_request", in_progress)

    async def completion_timeout() -> None:
        future = asyncio.get_running_loop().create_future()
        with pytest.raises(Exception, match="waiting for completion"):
            await backend._send_turn_and_resolve({}, future)

    asyncio.run(completion_timeout())
    assert events == [codex_backend.EVENT_TURN_FAILED, codex_backend.EVENT_TURN_FAILED]


def test_codex_request_success_reader_errors_pending_eof_and_stderr_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._closed = False
    backend._process = SimpleNamespace(stdin=object())
    backend._next_id = 1
    backend._pending = {}
    backend._codex = SimpleNamespace(read_timeout_ms=100)

    async def reply(payload: dict[str, Any]) -> None:
        backend._pending[payload["id"]].set_result({"result": {"ok": True}})

    monkeypatch.setattr(backend, "_write_json_rpc", reply)
    assert asyncio.run(backend._request("method", {})) == {"ok": True}

    async def emit(_event: str, _payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(backend, "_emit", emit)
    backend._turn_completion_waiter = None
    backend._process = SimpleNamespace(
        stdout=_AsyncLineStream(RuntimeError("stdout failed")),
        stderr=_AsyncLineStream(b"diagnostic\n", b""),
        stdin=object(),
        returncode=1,
    )
    asyncio.run(backend._stdout_reader())
    asyncio.run(backend._stderr_reader())

    async def pending_eof() -> None:
        future = asyncio.get_running_loop().create_future()
        backend._pending = {9: future}
        backend._process.stdout = _AsyncLineStream(b"")
        await backend._stdout_reader()
        assert future.done()
        with pytest.raises(Exception, match="closed stdout"):
            future.result()

    asyncio.run(pending_eof())


def test_codex_server_request_token_guards_approval_and_tool_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(codex_backend.CodexAppServerBackend)
    responses: list[dict[str, Any]] = []
    events: list[str] = []

    async def write(payload: dict[str, Any]) -> None:
        responses.append(payload)

    async def emit(event: str, _payload: dict[str, Any]) -> None:
        events.append(event)

    monkeypatch.setattr(backend, "_write_json_rpc", write)
    monkeypatch.setattr(backend, "_emit", emit)
    asyncio.run(
        backend._handle_server_request(
            {"id": 1, "method": "future/request", "params": "not-a-map"}
        )
    )
    assert responses[0]["error"]["code"] == -32601
    backend._latest_usage = {}
    backend._update_tokens_absolute("bad")
    backend._update_tokens_from_v2_block("bad")
    asyncio.run(backend._handle_approval({}))
    asyncio.run(backend._handle_approval({"id": "approval-1"}))
    asyncio.run(backend._handle_tool_call({"name": "tool"}))
    assert events == [
        codex_backend.EVENT_APPROVAL_AUTO_APPROVED,
        codex_backend.EVENT_UNSUPPORTED_TOOL_CALL,
    ]


def test_codex_readers_propagate_cancellation() -> None:
    class CancelledStream:
        async def readline(self) -> bytes:
            raise asyncio.CancelledError

    backend = object.__new__(codex_backend.CodexAppServerBackend)
    backend._process = SimpleNamespace(
        stdout=CancelledStream(), stderr=CancelledStream(), returncode=None
    )
    backend._pending = {}
    backend._turn_completion_waiter = None
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(backend._stdout_reader())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(backend._stderr_reader())


def test_pi_stream_handles_reader_error_nonmap_turn_end_and_compaction_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(pi_backend.PiBackend)
    backend._agent_name = "pi"
    backend._last_message = ""
    backend._stream_corrupt = None
    backend._stderr_tail = []
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    async def drain(_proc: Any) -> None:
        return None

    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_drain_stderr", drain)
    lines = [
        b"[]\n",
        json.dumps(
            {
                "type": "turn_end",
                "message": {"role": "assistant", "content": "turn text"},
            }
        ).encode()
        + b"\n",
        json.dumps(
            {
                "type": "compaction_end",
                "result": {},
                "errorMessage": "compact failed",
            }
        ).encode()
        + b"\n",
        b"",
    ]
    proc = SimpleNamespace(stdout=_AsyncLineStream(*lines), stderr=None)
    assert asyncio.run(backend._consume_stream(proc)) is None
    assert backend._last_message == "turn text"
    assert any(payload.get("error") == "compact failed" for _, payload in events)

    proc.stdout = _AsyncLineStream(RuntimeError("reader failed"))
    assert asyncio.run(backend._consume_stream(proc)) is None


def test_pi_stream_stderr_timeout_and_error_cleanup_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _pi_runtime_backend()

    async def emit(_event: str, _payload: dict[str, Any]) -> None:
        return None

    async def slow(_proc: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_drain_stderr", slow)
    proc = SimpleNamespace(stdout=_AsyncLineStream(b""), stderr=object())
    assert asyncio.run(backend._consume_stream(proc)) is None

    async def broken(_proc: Any) -> None:
        raise RuntimeError("stderr failed")

    monkeypatch.setattr(backend, "_drain_stderr", broken)
    assert asyncio.run(backend._consume_stream(proc)) is None
    assert (
        pi_backend._extract_last_assistant_message(
            [
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "ignored"},
            ]
        )
        == "ok"
    )


def test_claude_stream_and_stderr_cover_nonmap_user_unknown_and_reader_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object.__new__(claude_backend.ClaudeCodeBackend)
    backend._last_message = ""
    backend._stream_corrupt = None
    backend._stderr_tail = []
    backend._session_id = None
    backend._expected_resume_session_id = None
    backend._resume_session_confirmed = False
    backend._latest_usage = {
        "input_tokens": 0,
        "cache_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    events: list[str] = []

    async def emit(event: str, _payload: dict[str, Any]) -> None:
        events.append(event)

    async def drain(_proc: Any) -> None:
        return None

    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(backend, "_drain_stderr", drain)
    lines = [
        b"[]\n",
        b'{"type":"user"}\n',
        b'{"type":"future"}\n',
        b"",
    ]
    proc = SimpleNamespace(stdout=_AsyncLineStream(*lines), stderr=None)
    assert asyncio.run(backend._consume_stream(proc)) is None
    assert events == [
        claude_backend.EVENT_OTHER_MESSAGE,
        claude_backend.EVENT_OTHER_MESSAGE,
    ]
    proc.stdout = _AsyncLineStream(RuntimeError("reader failed"))
    assert asyncio.run(backend._consume_stream(proc)) is None

    assert asyncio.run(backend._drain_stderr(SimpleNamespace(stderr=None))) is None
    proc.stderr = _AsyncLineStream(b"secret line\n", b"")
    asyncio.run(claude_backend.ClaudeCodeBackend._drain_stderr(backend, proc))
    assert list(backend._stderr_tail) == ["secret line"]
    backend._update_usage_absolute("bad")


def test_claude_run_turn_reports_corrupt_stream_and_stderr_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _claude_runtime_backend()
    proc = SimpleNamespace(
        pid=42,
        stdin=_WritableInput(),
        stdout=object(),
        stderr=None,
        returncode=0,
    )
    events: list[str] = []

    async def spawn(*_args: Any, **_kwargs: Any) -> Any:
        return proc

    async def corrupt(_proc: Any) -> None:
        backend._stream_corrupt = "bad line"
        return None

    async def emit(event: str, _payload: dict[str, Any]) -> None:
        events.append(event)

    async def wait(_proc: Any, *, timeout: float) -> int:
        del timeout
        return 0

    monkeypatch.setattr(claude_backend.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(backend, "_consume_stream", corrupt)
    monkeypatch.setattr(backend, "_emit", emit)
    monkeypatch.setattr(claude_backend, "safe_proc_wait", wait)
    with pytest.raises(Exception, match="stream unreadable"):
        asyncio.run(backend.run_turn(prompt="body", is_continuation=False))
    assert events[-1] == claude_backend.EVENT_TURN_FAILED
    assert (
        asyncio.run(
            claude_backend.ClaudeCodeBackend._drain_stderr(
                backend, SimpleNamespace(stderr=None)
            )
        )
        is None
    )
