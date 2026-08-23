"""Coverage contracts for mock agents, release CLI, and process control."""
# ruff: noqa: F405

from tests.coverage_cases._runtime_support import *  # noqa: F403


def test_mock_codex_environment_parsing_uses_values_and_safe_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOCK_INT", "17")
    monkeypatch.setenv("MOCK_FLOAT", "2.5")
    assert mock_codex._env_int("MOCK_INT", 3) == 17
    assert mock_codex._env_float("MOCK_FLOAT", 3.0) == 2.5

    monkeypatch.setenv("MOCK_INT", "not-an-int")
    monkeypatch.setenv("MOCK_FLOAT", "not-a-float")
    assert mock_codex._env_int("MOCK_INT", 3) == 3
    assert mock_codex._env_float("MOCK_FLOAT", 3.0) == 3.0

    monkeypatch.delenv("MOCK_INT")
    monkeypatch.delenv("MOCK_FLOAT")
    assert mock_codex._env_int("MOCK_INT", 4) == 4
    assert mock_codex._env_float("MOCK_FLOAT", 4.0) == 4.0


def test_mock_codex_stdio_round_trips_utf8_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdin = io.BytesIO("요청\n".encode())
    stdout = io.BytesIO()
    monkeypatch.setattr(mock_codex.sys, "stdin", SimpleNamespace(buffer=stdin))
    monkeypatch.setattr(mock_codex.sys, "stdout", SimpleNamespace(buffer=stdout))

    protocol = mock_codex._Stdio()
    assert asyncio.run(protocol.start()) is None
    assert asyncio.run(protocol.readline()) == "요청\n".encode()
    protocol.write_json({"message": "완료"})
    asyncio.run(protocol.drain())

    assert json.loads(stdout.getvalue()) == {"message": "완료"}


def test_mock_codex_stdio_tolerates_closed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosedOutput:
        def write(self, _value: bytes) -> None:
            raise BrokenPipeError

        def flush(self) -> None:
            raise ConnectionResetError

    monkeypatch.setattr(mock_codex.sys, "stdin", SimpleNamespace(buffer=io.BytesIO()))
    monkeypatch.setattr(
        mock_codex.sys, "stdout", SimpleNamespace(buffer=ClosedOutput())
    )
    protocol = mock_codex._Stdio()

    protocol.write_json({"ignored": True})
    asyncio.run(protocol.drain())


def test_mock_codex_emits_monotonic_token_and_message_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = _ProtocolIO()
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mock_codex, "TICK_SECONDS", 1.0)
    monkeypatch.setattr(mock_codex, "TOKENS_PER_TICK", 10)
    monkeypatch.setattr(mock_codex.asyncio, "sleep", no_wait)
    monkeypatch.setattr(mock_codex.random, "randint", lambda low, _high: low)
    monkeypatch.setattr(mock_codex.random, "choice", lambda values: values[0])

    result = asyncio.run(mock_codex._emit_token_progress(protocol, totals, 2.0))

    assert result == {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}
    assert [item["method"] for item in protocol.messages] == [
        "thread/tokenUsage/updated",
        "notification",
        "thread/tokenUsage/updated",
        "notification",
    ]
    assert protocol.messages[-1]["params"]["message"] == "Reading repo structure..."
    assert protocol.drains == 2


def test_mock_codex_main_handles_handshake_thread_archive_and_unknown_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = _ProtocolIO(
        [
            b"not json\n",
            b"[]\n",
            _rpc("initialize", 1),
            _rpc("thread/start", 2, cwd="C:/repo"),
            _rpc("thread/archive", 3),
            _rpc("future/method", 4),
            _rpc("notification/without-id", None),
        ]
    )
    monkeypatch.setattr(mock_codex, "_Stdio", lambda: protocol)

    assert asyncio.run(mock_codex.main()) == 0
    assert protocol.started is True
    assert [message.get("id") for message in protocol.messages] == [1, 2, 3, 4]
    assert protocol.messages[0]["result"]["serverInfo"]["name"] == "mock-codex"
    assert protocol.messages[1]["result"]["thread"]["id"] == "mock-thread-1"
    assert protocol.messages[1]["result"]["cwd"] == "C:/repo"
    assert protocol.messages[2]["result"] == {}
    assert protocol.messages[3]["result"] == {}


@pytest.mark.parametrize(
    ("fail_every", "expected_status"), [(0, "completed"), (1, "failed")]
)
def test_mock_codex_main_completes_or_fails_turns_as_configured(
    monkeypatch: pytest.MonkeyPatch,
    fail_every: int,
    expected_status: str,
) -> None:
    protocol = _ProtocolIO([_rpc("thread/start", 1), _rpc("turn/start", 2)])

    async def preserve_totals(
        _io: _ProtocolIO, totals: dict[str, int], _duration: float
    ) -> dict[str, int]:
        return totals

    monkeypatch.setattr(mock_codex, "_Stdio", lambda: protocol)
    monkeypatch.setattr(mock_codex, "_emit_token_progress", preserve_totals)
    monkeypatch.setattr(mock_codex, "TURN_SECONDS", 0.1)
    monkeypatch.setattr(mock_codex, "FAIL_EVERY_N", fail_every)
    monkeypatch.setattr(mock_codex, "MAX_TURNS", 0)
    monkeypatch.setattr(mock_codex.random, "uniform", lambda _low, _high: 0.0)

    assert asyncio.run(mock_codex.main()) == 0
    completed = next(
        item for item in protocol.messages if item.get("method") == "turn/completed"
    )
    final_turn = completed["params"]["turn"]
    assert final_turn["status"] == expected_status
    assert ("error" in final_turn) is (expected_status == "failed")
    agent_message = next(
        item for item in protocol.messages if item.get("method") == "item/completed"
    )
    assert agent_message["params"]["threadId"] == "mock-thread-1"
    assert "Mock turn 1 finished" in agent_message["params"]["item"]["text"]


def test_mock_codex_main_honors_maximum_turn_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = _ProtocolIO([_rpc("turn/start", 1), _rpc("turn/start", 2)])

    async def preserve_totals(
        _io: _ProtocolIO, totals: dict[str, int], _duration: float
    ) -> dict[str, int]:
        return totals

    monkeypatch.setattr(mock_codex, "_Stdio", lambda: protocol)
    monkeypatch.setattr(mock_codex, "_emit_token_progress", preserve_totals)
    monkeypatch.setattr(mock_codex, "TURN_SECONDS", 0.1)
    monkeypatch.setattr(mock_codex, "FAIL_EVERY_N", 0)
    monkeypatch.setattr(mock_codex, "MAX_TURNS", 1)
    monkeypatch.setattr(mock_codex.random, "uniform", lambda _low, _high: 0.0)

    assert asyncio.run(mock_codex.main()) == 0
    assert len(protocol.lines) == 1


def test_mock_codex_diagnostics_are_confined_to_stderr(capsys: Any) -> None:
    mock_codex._log("request", method="initialize", id=7)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "[mock-codex] request method=initialize id=7\n"


@pytest.mark.parametrize(("interrupt", "expected"), [(False, 3), (True, 0)])
def test_mock_codex_module_entrypoint_maps_completion_and_interrupt_to_exit(
    monkeypatch: pytest.MonkeyPatch, interrupt: bool, expected: int
) -> None:
    def run(coroutine: Any) -> int:
        coroutine.close()
        if interrupt:
            raise KeyboardInterrupt
        return 3

    monkeypatch.setattr(asyncio, "run", run)
    monkeypatch.delitem(sys.modules, "symphony.mock_codex")
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("symphony.mock_codex", run_name="__main__")
    assert raised.value.code == expected


@pytest.mark.parametrize(
    ("tracker", "resolved", "expected"),
    [
        (None, None, None),
        ("not-a-map", None, None),
        ({"kind": "linear"}, None, None),
        (
            {"kind": "file"},
            None,
            PurePosixPath(release.DEFAULT_BOARD_ROOT_NAME),
        ),
        (
            {"kind": "file", "board_root": ""},
            None,
            PurePosixPath(release.DEFAULT_BOARD_ROOT_NAME),
        ),
        (
            {"kind": "file", "board_root": 17},
            None,
            PurePosixPath(release.DEFAULT_BOARD_ROOT_NAME),
        ),
        ({"kind": "file", "board_root": "$BOARD"}, None, None),
        (
            {"kind": "file", "board_root": "$BOARD"},
            "./tickets",
            PurePosixPath("tickets"),
        ),
        ({"kind": "file", "board_root": "/srv/board"}, None, None),
        (
            {"kind": "linear", "board_root": "./custom"},
            None,
            PurePosixPath("custom"),
        ),
    ],
)
def test_release_board_mount_normalizes_defaults_indirection_and_host_paths(
    monkeypatch: pytest.MonkeyPatch,
    tracker: object,
    resolved: str | None,
    expected: PurePosixPath | None,
) -> None:
    workflow = WorkflowDefinition(
        config={"tracker": tracker}, prompt_template="", source_path=Path("WORKFLOW.md")
    )
    monkeypatch.setattr(release, "resolve_var_indirection", lambda _raw: resolved)

    assert release._configured_board_mount(workflow) == expected


def test_release_repository_root_requires_the_checkout_git_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = tmp_path / "repo"
    common = repo / ".git"
    common.mkdir(parents=True)
    monkeypatch.setattr(release, "resolve_git_common_dir", lambda _root: common)
    assert release._canonical_repository_root(workspace) == repo

    monkeypatch.setattr(release, "resolve_git_common_dir", lambda _root: None)
    assert release._canonical_repository_root(workspace) is None

    missing = tmp_path / "missing" / ".git"
    monkeypatch.setattr(release, "resolve_git_common_dir", lambda _root: missing)
    assert release._canonical_repository_root(workspace) is None

    shared = repo / "shared-git"
    shared.mkdir()
    monkeypatch.setattr(release, "resolve_git_common_dir", lambda _root: shared)
    assert release._canonical_repository_root(workspace) is None


def test_release_board_root_anchors_relative_mount_to_host_repository(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "copied-workspace" / "board"
    repository = tmp_path / "host-repository"
    assert (
        release._canonical_board_root(
            configured_board_root=configured,
            board_mount=None,
            repository_root=repository,
        )
        == configured
    )
    assert (
        release._canonical_board_root(
            configured_board_root=configured,
            board_mount=PurePosixPath("ops/board"),
            repository_root=repository,
        )
        == repository / "ops" / "board"
    )


def test_release_check_reports_missing_or_invalid_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    missing = tmp_path / "missing.md"
    assert (
        release.main(
            [
                "check",
                str(missing),
                "--ticket",
                "VERIFY-1",
                "--workspace",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "workflow file not found" in capsys.readouterr().err

    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text("invalid", encoding="utf-8")

    def reject_workflow(_path: Path) -> WorkflowDefinition:
        raise SymphonyError("invalid workflow")

    monkeypatch.setattr(release, "load_workflow", reject_workflow)
    assert (
        release.main(
            [
                "check",
                str(workflow_path),
                "--ticket",
                "VERIFY-1",
                "--workspace",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "workflow load failed" in capsys.readouterr().err


def test_release_check_rejects_workspace_without_host_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text("workflow", encoding="utf-8")
    workflow = WorkflowDefinition({}, "", workflow_path)
    cfg = SimpleNamespace(
        tracker=SimpleNamespace(board_root=None),
        agent=SimpleNamespace(auto_merge_target_branch="main"),
    )
    monkeypatch.setattr(release, "load_workflow", lambda _path: workflow)
    monkeypatch.setattr(release, "build_service_config", lambda _workflow: cfg)
    monkeypatch.setattr(release, "_canonical_repository_root", lambda _root: None)

    assert (
        release.main(
            [
                "check",
                str(workflow_path),
                "--ticket",
                "VERIFY-1",
                "--workspace",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "Git host repository could not be resolved" in capsys.readouterr().err


@pytest.mark.parametrize(("as_json", "passed"), [(True, True), (False, False)])
def test_release_check_renders_immutable_result_and_exit_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
    as_json: bool,
    passed: bool,
) -> None:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text("workflow", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = tmp_path / "repo"
    repository.mkdir()
    workflow = WorkflowDefinition(
        {"tracker": {"kind": "file", "board_root": "./kanban"}},
        "",
        workflow_path,
    )
    cfg = SimpleNamespace(
        tracker=SimpleNamespace(board_root=workspace / "kanban"),
        agent=SimpleNamespace(auto_merge_target_branch="main"),
    )
    result = ReleaseValidationResult(
        passed=passed,
        evidence_errors=() if passed else ("evidence stale",),
        repairable_failures=(),
        target_branch="main",
        target_sha="a" * 40,
        contract_sha256="b" * 64,
        fingerprint="fingerprint-1",
        finalizer_ticket="FINAL-1",
        note_text="evidence verified" if passed else "evidence stale",
    )
    received: dict[str, Any] = {}

    def validate(**kwargs: Any) -> ReleaseValidationResult:
        received.update(kwargs)
        return result

    monkeypatch.setattr(release, "load_workflow", lambda _path: workflow)
    monkeypatch.setattr(release, "build_service_config", lambda _workflow: cfg)
    monkeypatch.setattr(release, "_canonical_repository_root", lambda _root: repository)
    monkeypatch.setattr(release, "validate_release_contract", validate)
    argv = [
        "check",
        str(workflow_path),
        "--ticket",
        "VERIFY-1",
        "--workspace",
        str(workspace),
    ]
    if as_json:
        argv.append("--json")

    assert release.main(argv) == (0 if passed else 1)
    output = capsys.readouterr().out
    if as_json:
        assert json.loads(output)["fingerprint"] == "fingerprint-1"
    else:
        assert "FAIL  app.release-contract" in output
        assert "Target: main@" + "a" * 40 in output
        assert "Contract SHA-256: " + "b" * 64 in output
        assert "Fingerprint: fingerprint-1" in output
    assert received["repository_root"] == repository
    assert received["board_root"] == repository / "kanban"
    assert received["board_mount"] == PurePosixPath("kanban")


def test_release_main_fails_closed_for_unrecognized_parsed_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = SimpleNamespace(parse_args=lambda _argv: SimpleNamespace(command="future"))
    monkeypatch.setattr(release, "_build_parser", lambda: parser)
    assert release.main([]) == 2


def test_cli_module_entrypoint_propagates_main_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    cli_main = importlib.import_module("symphony.cli.main")

    monkeypatch.setattr(cli_main, "main", lambda: 7)
    with pytest.raises(SystemExit, match="7"):
        runpy.run_module("symphony.cli.__main__", run_name="__main__")


def test_resolve_bash_uses_native_posix_name_and_windows_path_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYMPHONY_BASH", raising=False)
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    _shell.resolve_bash.cache_clear()
    assert _shell.resolve_bash() == "bash"

    monkeypatch.setattr(_shell.sys, "platform", "win32")
    monkeypatch.setattr(_shell, "_WIN_GIT_BASH_CANDIDATES", ())
    monkeypatch.setattr(_shell.shutil, "which", lambda _name: "C:/Git/bin/bash.exe")
    _shell.resolve_bash.cache_clear()
    assert _shell.resolve_bash() == "C:/Git/bin/bash.exe"
    _shell.resolve_bash.cache_clear()


def test_safe_proc_wait_handles_missing_pid_and_posix_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    assert (
        asyncio.run(
            _shell.safe_proc_wait(SimpleNamespace(returncode=None, pid=None), timeout=1)
        )
        is None
    )

    monkeypatch.setattr(_shell.os, "waitpid", lambda _pid, _flags: (41, 99))
    monkeypatch.setattr(_shell.os, "WIFEXITED", lambda _status: True, raising=False)
    monkeypatch.setattr(_shell.os, "WEXITSTATUS", lambda _status: 9, raising=False)
    proc = SimpleNamespace(returncode=None, pid=41)
    assert asyncio.run(_shell.safe_proc_wait(proc)) == 9


def test_safe_proc_wait_recovers_signal_status_and_already_reaped_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    monkeypatch.setattr(_shell.os, "waitpid", lambda _pid, _flags: (41, 99))
    monkeypatch.setattr(_shell.os, "WIFEXITED", lambda _status: False, raising=False)
    monkeypatch.setattr(_shell.os, "WIFSIGNALED", lambda _status: True, raising=False)
    monkeypatch.setattr(
        _shell.os, "WTERMSIG", lambda _status: signal.SIGTERM, raising=False
    )
    assert (
        asyncio.run(
            _shell.safe_proc_wait(SimpleNamespace(returncode=None, pid=41), timeout=1)
        )
        == -signal.SIGTERM
    )

    def already_reaped(_pid: int, _flags: int) -> tuple[int, int]:
        raise OSError(errno.ECHILD, "already reaped")

    class ReapedProcess:
        returncode = None
        pid = 42

        async def wait(self) -> int:
            return 4

    monkeypatch.setattr(_shell.os, "waitpid", already_reaped)
    assert asyncio.run(_shell.safe_proc_wait(ReapedProcess())) == 4

    plain_echild = OSError("already reaped")
    plain_echild.errno = errno.ECHILD

    def reaped_as_plain_os_error(_pid: int, _flags: int) -> tuple[int, int]:
        raise plain_echild

    monkeypatch.setattr(_shell.os, "waitpid", reaped_as_plain_os_error)
    assert asyncio.run(_shell.safe_proc_wait(ReapedProcess())) == 4


def test_safe_proc_wait_covers_native_wait_and_asyncio_fallback_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WaitingProcess:
        returncode: int | None = None
        pid = 42

        async def wait(self) -> int:
            return 8

    proc = WaitingProcess()
    monkeypatch.setattr(_shell.sys, "platform", "win32")
    assert asyncio.run(_shell.safe_proc_wait(proc)) == 8

    monkeypatch.setattr(_shell.sys, "platform", "linux")

    def reaped_with_code(_pid: int, _flags: int) -> tuple[int, int]:
        proc.returncode = 7
        raise ChildProcessError

    monkeypatch.setattr(_shell.os, "waitpid", reaped_with_code)
    assert asyncio.run(_shell.safe_proc_wait(proc)) == 7

    proc.returncode = None
    monkeypatch.setattr(
        _shell.os,
        "waitpid",
        lambda _pid, _flags: (_ for _ in ()).throw(ChildProcessError()),
    )
    without_wait = SimpleNamespace(returncode=None, pid=42)
    assert asyncio.run(_shell.safe_proc_wait(without_wait)) is None

    class SlowWatcher:
        returncode = None
        pid = 42

        async def wait(self) -> int:
            await asyncio.sleep(1)
            return 8

    assert asyncio.run(_shell.safe_proc_wait(SlowWatcher())) is None


def test_safe_proc_wait_handles_indeterminate_status_and_blocking_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    monkeypatch.setattr(_shell.os, "waitpid", lambda _pid, _flags: (42, 99))
    monkeypatch.setattr(_shell.os, "WIFEXITED", lambda _status: False, raising=False)
    monkeypatch.setattr(_shell.os, "WIFSIGNALED", lambda _status: False, raising=False)

    class Watcher:
        returncode = None
        pid = 42

        async def wait(self) -> int:
            return 6

    assert asyncio.run(_shell.safe_proc_wait(Watcher(), timeout=1)) == 6

    async def slow_thread(_function: Any) -> None:
        await asyncio.sleep(0.1)

    monkeypatch.setattr(_shell.asyncio, "to_thread", slow_thread)
    assert asyncio.run(_shell.safe_proc_wait(Watcher(), timeout=0.001)) is None


def test_safe_proc_wait_propagates_unexpected_waitpid_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_shell.sys, "platform", "linux")

    def denied(_pid: int, _flags: int) -> tuple[int, int]:
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(_shell.os, "waitpid", denied)
    with pytest.raises(OSError, match="denied"):
        asyncio.run(
            _shell.safe_proc_wait(SimpleNamespace(returncode=None, pid=41), timeout=1)
        )


def test_process_group_signal_falls_back_to_pid_and_reports_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def group_denied(_pid: int, _sig: int) -> None:
        raise OSError("group unavailable")

    monkeypatch.setattr(_shell, "_killpg", group_denied)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(_shell.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert _shell._signal_process_group(51, signal.SIGTERM) is True
    assert sent == [(51, signal.SIGTERM)]

    monkeypatch.setattr(
        _shell.os, "kill", lambda _pid, _sig: (_ for _ in ()).throw(OSError())
    )
    assert _shell._signal_process_group(51, signal.SIGTERM) is False


def test_process_group_signal_reports_successful_group_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered: list[tuple[int, int]] = []
    monkeypatch.setattr(
        _shell, "_killpg", lambda pid, sig: delivered.append((pid, sig))
    )
    assert _shell._signal_process_group(51, signal.SIGTERM) is True
    assert delivered == [(51, signal.SIGTERM)]

    monkeypatch.setattr(
        _shell,
        "_killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert _shell._signal_process_group(51, signal.SIGTERM) is False


@pytest.mark.parametrize(
    ("platform", "boot_output", "expected"),
    [
        ("linux", "boot-1\n", "boot-1"),
        ("darwin", "{ sec = 1 }\n", "{ sec = 1 }"),
        ("win32", "unused", None),
    ],
)
def test_host_boot_token_uses_platform_stable_identity(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    boot_output: str,
    expected: str | None,
) -> None:
    _shell._host_boot_token.cache_clear()
    monkeypatch.setattr(_shell.sys, "platform", platform)
    monkeypatch.setattr(Path, "read_text", lambda _self, **_kwargs: boot_output)
    monkeypatch.setattr(
        _shell.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=boot_output),
    )
    assert _shell._host_boot_token() == expected
    _shell._host_boot_token.cache_clear()


def test_host_boot_token_tolerates_unavailable_platform_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shell._host_boot_token.cache_clear()
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    monkeypatch.setattr(
        Path, "read_text", lambda _self, **_kwargs: (_ for _ in ()).throw(OSError())
    )
    assert _shell._host_boot_token() is None

    _shell._host_boot_token.cache_clear()
    monkeypatch.setattr(_shell.sys, "platform", "darwin")
    monkeypatch.setattr(
        _shell.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.SubprocessError()),
    )
    assert _shell._host_boot_token() is None
    _shell._host_boot_token.cache_clear()


def test_process_identity_hashes_boot_pid_and_platform_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_shell, "_host_boot_token", lambda: "boot-token")
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    stat = "42 (worker name) " + " ".join(str(index) for index in range(30))
    monkeypatch.setattr(Path, "read_text", lambda _self, **_kwargs: stat)
    identity = _shell.process_identity(42)
    assert identity is not None and len(identity) == 64
    assert _shell.process_identity(0) is None

    monkeypatch.setattr(_shell.sys, "platform", "darwin")
    monkeypatch.setattr(
        _shell.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="Mon Jan 1\n"),
    )
    assert _shell.process_identity(42) is not None


def test_process_identity_fails_closed_when_start_time_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_shell, "_host_boot_token", lambda: None)
    assert _shell.process_identity(42) is None

    monkeypatch.setattr(_shell, "_host_boot_token", lambda: "boot")
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_text", lambda _self, **_kwargs: "truncated)")
    assert _shell.process_identity(42) is None

    monkeypatch.setattr(_shell.sys, "platform", "darwin")
    monkeypatch.setattr(
        _shell.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )
    assert _shell.process_identity(42) is None

    monkeypatch.setattr(_shell.sys, "platform", "win32")
    assert _shell.process_identity(42) is None


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, "S\nZ\n", True),
        (0, "Z\n", False),
        (1, "", False),
        (0, "", None),
        (2, "S\n", None),
    ],
)
def test_process_group_exists_distinguishes_live_zombie_and_missing_groups(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    expected: bool | None,
) -> None:
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    monkeypatch.setattr(_shell.os, "killpg", lambda _pid, _sig: None, raising=False)
    monkeypatch.setattr(
        _shell.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=returncode, stdout=stdout),
    )
    assert _shell.process_group_exists(42) is expected


def test_process_group_exists_handles_invalid_absent_and_uninspectable_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _shell.process_group_exists(0) is None
    monkeypatch.setattr(_shell.sys, "platform", "win32")
    assert _shell.process_group_exists(42) is None

    monkeypatch.setattr(_shell.sys, "platform", "linux")
    monkeypatch.setattr(
        _shell.os,
        "killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()),
        raising=False,
    )
    assert _shell.process_group_exists(42) is False
    monkeypatch.setattr(
        _shell.os,
        "killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(PermissionError()),
        raising=False,
    )
    assert _shell.process_group_exists(42) is None

    monkeypatch.setattr(_shell.os, "killpg", lambda _pid, _sig: None, raising=False)
    monkeypatch.setattr(
        _shell.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.SubprocessError()),
    )
    assert _shell.process_group_exists(42) is None


def test_taskkill_tree_builds_tree_command_and_collapses_host_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(_shell.subprocess, "run", run)
    assert _shell._taskkill_tree(42) is True
    assert _shell._taskkill_tree(43, force=False) is True
    assert commands == [
        ["taskkill", "/PID", "42", "/T", "/F"],
        ["taskkill", "/PID", "43", "/T"],
    ]

    for error in (subprocess.TimeoutExpired("taskkill", 1), OSError("missing")):
        monkeypatch.setattr(
            _shell.subprocess,
            "run",
            lambda *_args, _error=error, **_kwargs: (_ for _ in ()).throw(_error),
        )
        assert _shell._taskkill_tree(42) is False


def test_kill_process_group_validates_identity_and_routes_by_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_shell.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(_shell, "process_identity", lambda _pid: "current")
    assert _shell.kill_process_group(42, identity="stale") is False

    monkeypatch.setattr(_shell.sys, "platform", "win32")
    monkeypatch.setattr(_shell, "_taskkill_tree", lambda pid: pid == 42)
    assert _shell.kill_process_group(42, identity="current") is True

    monkeypatch.setattr(_shell.sys, "platform", "linux")
    monkeypatch.setattr(
        _shell, "_signal_process_group", lambda pid, sig: (pid, sig) == (42, 9)
    )
    assert _shell.kill_process_group(42, identity="current") is True


def test_kill_process_group_warns_once_for_legacy_pid_without_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[dict[str, Any]] = []
    logger = SimpleNamespace(warning=lambda _message, **fields: warnings.append(fields))
    monkeypatch.setattr(_shell, "_warned_kill_without_identity", False)
    monkeypatch.setattr(_shell, "get_logger", lambda: logger)
    monkeypatch.setattr(_shell.sys, "platform", "win32")
    monkeypatch.setattr(_shell, "_taskkill_tree", lambda _pid: True)

    assert _shell.kill_process_group(42) is True
    assert _shell.kill_process_group(43) is True
    assert warnings == [
        {
            "pid": 42,
            "hint": "pid reuse could kill an unrelated process tree; "
            "capture process_identity() when the pid is recorded",
        }
    ]


def test_terminate_process_tree_short_circuits_and_escalates_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert asyncio.run(_shell.terminate_process_tree(_TerminableProcess(1, 7))) == 7

    proc = _TerminableProcess(42)
    proc.raise_terminate = True
    proc.raise_kill = True
    waits = iter([None, 9])

    async def wait(_proc: Any, *, timeout: float | None = None) -> int | None:
        del timeout
        return next(waits)

    killed: list[int] = []
    monkeypatch.setattr(_shell.sys, "platform", "win32")
    monkeypatch.setattr(
        _shell, "_taskkill_tree", lambda pid: killed.append(pid) is None
    )
    monkeypatch.setattr(_shell, "safe_proc_wait", wait)

    assert (
        asyncio.run(
            _shell.terminate_process_tree(proc, term_timeout=0.1, kill_timeout=0.2)
        )
        == 9
    )
    assert killed == [42]
    assert proc.terminated == 1 and proc.killed == 1


def test_terminate_process_tree_handles_pidless_and_grouped_posix_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_shell.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(_shell.sys, "platform", "linux")
    pidless = _TerminableProcess(None)
    pidless.raise_terminate = True
    pidless.raise_kill = True
    waits = iter([None, 5])

    async def wait(_proc: Any, *, timeout: float | None = None) -> int | None:
        del timeout
        return next(waits)

    monkeypatch.setattr(_shell, "safe_proc_wait", wait)
    assert asyncio.run(_shell.terminate_process_tree(pidless)) == 5

    grouped = _TerminableProcess(42)
    waits = iter([None, 6])
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        _shell,
        "_signal_process_group",
        lambda pid, sig: signals.append((pid, sig)) is None,
    )
    assert asyncio.run(_shell.terminate_process_tree(grouped)) == 6
    assert signals == [(42, signal.SIGTERM), (42, 9)]
