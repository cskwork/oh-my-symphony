"""Coverage contracts for service, progress, and artifact runtime."""
# ruff: noqa: F405

from tests.coverage_cases._runtime_support import *  # noqa: F403


def test_backend_redaction_preserves_tuple_shape_and_nonsecret_values() -> None:
    value = ("session-private", 3, {"nested": "session-private/result"})
    assert redact_session_id(value, "session-private") == (
        "[REDACTED_SESSION]",
        3,
        {"nested": "[REDACTED_SESSION]/result"},
    )


def test_kiro_resume_flag_appends_when_command_has_no_prompt_placeholder() -> None:
    assert _insert_before_prompt_arg("kiro-cli chat   ", "--resume") == (
        "kiro-cli chat --resume"
    )


def test_gemini_helpers_preserve_existing_approval_flag_and_ignore_bad_stats() -> None:
    backend = object.__new__(GeminiBackend)
    backend._gemini = SimpleNamespace(command="gemini --yolo")
    backend._latest_usage = {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
    }

    assert backend._command_for_turn(prompt="ignored", is_continuation=False) == (
        "gemini --yolo"
    )
    assert backend._parse_json_output("") is None
    backend._update_usage_from_stats(
        {
            "models": {
                "not-a-model": 3,
                "missing-tokens": {},
                "bad-tokens": {"tokens": 4},
            }
        }
    )
    assert backend._latest_usage == {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
    }


def test_prime_agent_terminal_detection_fails_closed_for_wrong_tracker_or_io_error() -> (
    None
):
    backend = object.__new__(PrimeAgentBackend)
    backend._pi_tracker = SimpleNamespace(kind="linear", board_root=None)
    assert backend._file_ticket_is_terminal() is False

    class BrokenBoard:
        def glob(self, _pattern: str) -> Any:
            raise OSError("board changed during scan")

    backend._pi_tracker = SimpleNamespace(
        kind="file", board_root=BrokenBoard(), terminal_states=("Done",)
    )
    backend._cwd = Path("workspaces/TICKET-1")
    assert backend._file_ticket_is_terminal() is False


@pytest.mark.parametrize("value", ["not-seconds", "-1"])
def test_tracker_retry_rejects_invalid_or_negative_retry_after(value: str) -> None:
    response = httpx.Response(429, headers={"Retry-After": value})
    assert _retry._retry_after_delay(response) is None


def test_tracker_retry_single_attempt_returns_retryable_response_without_sleep() -> (
    None
):
    response = httpx.Response(503)
    send_calls = 0
    sleep_calls: list[float] = []

    def send() -> httpx.Response:
        nonlocal send_calls
        send_calls += 1
        return response

    result = _retry.send_with_retry(
        send,
        max_attempts=1,
        sleep=sleep_calls.append,
    )

    assert result is response
    assert send_calls == 1
    assert sleep_calls == []


def test_tracker_retry_single_transport_failure_reraises_without_sleep() -> None:
    error = httpx.ConnectError(
        "tracker unavailable",
        request=httpx.Request("GET", "https://tracker.example"),
    )
    send_calls = 0
    sleep_calls: list[float] = []

    def send() -> httpx.Response:
        nonlocal send_calls
        send_calls += 1
        raise error

    with pytest.raises(httpx.TransportError) as exc_info:
        _retry.send_with_retry(
            send,
            max_attempts=1,
            sleep=sleep_calls.append,
        )

    assert exc_info.value is error
    assert send_calls == 1
    assert sleep_calls == []


def test_tracker_retry_zero_attempts_rejected_before_send_or_sleep() -> None:
    send_calls = 0
    sleep_calls: list[float] = []

    def send() -> httpx.Response:
        nonlocal send_calls
        send_calls += 1
        return httpx.Response(200)

    with pytest.raises(ValueError, match="max_attempts"):
        _retry.send_with_retry(
            send,
            max_attempts=0,
            sleep=sleep_calls.append,
        )

    assert send_calls == 0
    assert sleep_calls == []


def test_dependency_walk_ignores_dangling_or_already_visited_edges() -> None:
    edges = {"A": ("B", "missing"), "B": ("missing",)}
    assert _find_cycle_through(edges, "A") is None


def test_topological_order_retains_all_nodes_when_input_is_cyclic() -> None:
    assert topological_order({"B": ("A",), "A": ("B",)}) == ["A", "B"]


def test_keep_awake_reuses_active_process_and_ignores_finished_process() -> None:
    awake = keep_awake.KeepAwake()
    active = _WakeProcess()
    awake._proc = active
    assert awake.start() is True

    finished = _WakeProcess(alive=False)
    awake._proc = finished
    awake.stop()
    assert awake._proc is None


def test_keep_awake_stop_tolerates_terminate_and_force_kill_failures() -> None:
    awake = keep_awake.KeepAwake()
    terminate_denied = _WakeProcess(terminate_error=True)
    awake._proc = terminate_denied
    awake.stop()
    assert awake._proc is None

    kill_denied = _WakeProcess(kill_error=True)
    awake._proc = kill_denied
    awake.stop()
    assert kill_denied.killed is True


def test_keep_awake_context_manager_starts_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        keep_awake.KeepAwake, "start", lambda _self: calls.append("start") or True
    )
    monkeypatch.setattr(
        keep_awake.KeepAwake, "stop", lambda _self: calls.append("stop")
    )
    awake = keep_awake.KeepAwake()
    with awake as entered:
        assert entered is awake
    assert calls == ["start", "stop"]


def test_service_probe_normalizes_bracketed_ipv6_and_rejects_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert service._probe_host(" [2001:db8::1] ") == "[2001:db8::1]"

    class Response:
        status = 503

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    opener = SimpleNamespace(open=lambda _request, timeout: Response())
    monkeypatch.setattr(service.urllib.request, "build_opener", lambda _handler: opener)
    assert service._probe_json("localhost", 9999, "/api/v1/health") is None


def test_service_lock_cleanup_tolerates_body_removing_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "service.lock"
    monkeypatch.setattr(service, "lock_path_for", lambda _workflow: lock_path)
    with service.acquire_service_lock(tmp_path / "WORKFLOW.md"):
        assert lock_path.exists()
        lock_path.unlink()
    assert not lock_path.exists()


@pytest.mark.parametrize("raw", ["{", "[]", '{"workflow_path": "only"}'])
def test_service_record_loading_fails_closed_for_corrupt_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(service, "record_path_for", lambda _workflow: record_path)
    assert service.load_record(tmp_path / "WORKFLOW.md") is None


def test_service_api_and_workflow_probes_validate_payload_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = (tmp_path / "WORKFLOW.md").resolve()
    monkeypatch.setattr(
        service,
        "_probe_json",
        lambda *_args, **_kwargs: {"health": {}, "counts": {}},
    )
    assert service.is_symphony_api_reachable("localhost", 9999) is True

    original_resolve = Path.resolve

    def fail_bad_path(path: Path, *args: Any, **kwargs: Any) -> Path:
        if "bad-path" in str(path):
            raise OSError("unresolvable served path")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_bad_path)
    assert (
        service._payload_serves_workflow(
            {"workflow_path": str(tmp_path / "bad-path")}, workflow
        )
        is False
    )


def test_service_process_liveness_collapses_invalid_and_os_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert service.is_process_running("not-a-pid") is False
    assert service.is_process_running(-1) is False

    monkeypatch.setattr(service, "_IS_WIN32", False)
    for error, expected in (
        (ProcessLookupError(), False),
        (PermissionError(), True),
        (OSError("unknown"), False),
    ):
        monkeypatch.setattr(
            service.os,
            "kill",
            lambda _pid, _signal, _error=error: (_ for _ in ()).throw(_error),
        )
        assert service.is_process_running(42) is expected


def test_service_wait_until_runs_final_predicate_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([0.0, 0.0, 2.0])
    calls = 0

    def predicate() -> bool:
        nonlocal calls
        calls += 1
        return calls == 2

    monkeypatch.setattr(service.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)
    assert service._wait_until(predicate, timeout_s=1.0) is True
    assert calls == 2


def test_service_terminate_process_validates_pid_and_routes_windows_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert service.terminate_process("bad") is False
    monkeypatch.setattr(service, "is_process_running", lambda _pid: False)
    assert service.terminate_process(42) is False

    monkeypatch.setattr(service, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(service, "_IS_WIN32", True)
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        service,
        "_taskkill_tree",
        lambda pid, *, force: calls.append((pid, force)) or True,
    )
    assert service.terminate_process(42, force=True) is True
    assert calls == [(42, True)]


@pytest.mark.parametrize("error", [ProcessLookupError(), PermissionError()])
def test_service_terminate_process_reports_posix_signal_rejection(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    monkeypatch.setattr(service, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(service, "_IS_WIN32", False)
    monkeypatch.setattr(
        service,
        "_killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(error),
    )
    assert service.terminate_process(42) is False


def test_service_terminate_process_falls_back_from_group_to_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(service, "_IS_WIN32", False)
    monkeypatch.setattr(
        service,
        "_killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(OSError("no group")),
    )
    delivered: list[tuple[int, int]] = []
    monkeypatch.setattr(
        service.os, "kill", lambda pid, sig: delivered.append((pid, sig))
    )
    assert service.terminate_process(42) is True
    assert delivered == [(42, signal.SIGTERM)]

    monkeypatch.setattr(
        service.os,
        "kill",
        lambda _pid, _sig: (_ for _ in ()).throw(OSError("denied")),
    )
    assert service.terminate_process(42) is False


def test_workspace_process_scan_handles_host_failure_and_filters_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    workspace = tmp_path / "ticket"
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ps unavailable")),
    )
    assert service._workspace_bound_process_pids([workspace]) == []
    assert "could not inspect workspace-bound processes" in capsys.readouterr().err

    current_pid = service.os.getpid()
    output = f"\nnot-a-pid command\n{current_pid} current\n77 worker {workspace}\n"
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=output),
    )
    assert service._workspace_bound_process_pids([workspace]) == [77]


def test_service_status_command_renders_stale_running_and_port_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    record = _service_record(tmp_path, port=9999)
    monkeypatch.setattr(
        service, "resolve_workflow_path", lambda _raw: record.workflow_path
    )
    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: service.ServiceStatus(
            "stopped", record, pid_running=False, api_reachable=False
        ),
    )
    assert service._status(SimpleNamespace(workflow=None, port=None)) == 0
    assert "stale pid=42" in capsys.readouterr().out

    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: service.ServiceStatus(
            "running", record, pid_running=True, api_reachable=True
        ),
    )
    assert service._status(SimpleNamespace(workflow=None, port=10000)) == 0
    output = capsys.readouterr().out
    assert "running workflow=" in output
    assert "requested port 10000" in output


def test_service_restart_stops_before_starting_and_propagates_stop_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = SimpleNamespace(
        workflow="WORKFLOW.md",
        timeout=2.0,
        force=True,
        host="localhost",
        port=9999,
        skip_doctor=True,
    )
    monkeypatch.setattr(service, "_stop", lambda _args: 3)
    assert service._restart(args) == 3

    received: list[Any] = []
    monkeypatch.setattr(service, "_stop", lambda _args: 0)
    monkeypatch.setattr(service, "_start", lambda start: received.append(start) or 7)
    assert service._restart(args) == 7
    assert received[0].replace is False


def test_service_logs_reports_absent_record_and_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    monkeypatch.setattr(service, "resolve_workflow_path", lambda _raw: workflow)
    monkeypatch.setattr(service, "load_record", lambda _workflow: None)
    assert service._logs(SimpleNamespace(workflow=None, lines=10)) == 1
    assert "no service record" in capsys.readouterr().err

    record = _service_record(tmp_path, log_path=tmp_path / "missing.log")
    monkeypatch.setattr(service, "load_record", lambda _workflow: record)
    assert service._logs(SimpleNamespace(workflow=None, lines=10)) == 1
    assert "log file not found" in capsys.readouterr().err


def test_service_record_clear_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing-record.json"
    monkeypatch.setattr(service, "record_path_for", lambda _workflow: missing)
    service.clear_record(tmp_path / "WORKFLOW.md")


def test_service_windows_liveness_uses_read_only_handle_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.ctypes, "WinDLL", None, raising=False)
    assert service._is_process_running_windows(42) is False

    class Kernel:
        def __init__(self) -> None:
            self.opens = 0
            self.closed: list[int] = []

        def OpenProcess(self, _rights: int, _inherit: bool, _pid: int) -> int:
            self.opens += 1
            return 0 if self.opens == 1 else 7

        def GetExitCodeProcess(self, _handle: int, pointer: Any) -> bool:
            pointer._obj.value = 259
            return True

        def CloseHandle(self, handle: int) -> None:
            self.closed.append(handle)

    kernel = Kernel()
    monkeypatch.setattr(
        service.ctypes,
        "WinDLL",
        lambda _name, **_kwargs: kernel,
        raising=False,
    )
    assert service._is_process_running_windows(42) is True
    assert kernel.opens == 2 and kernel.closed == [7]

    kernel.GetExitCodeProcess = lambda _handle, _pointer: False
    assert service._is_process_running_windows(42) is False


def test_service_process_liveness_handles_windows_probe_error_and_posix_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "_IS_WIN32", True)
    monkeypatch.setattr(
        service,
        "_is_process_running_windows",
        lambda _pid: (_ for _ in ()).throw(OSError("access denied")),
    )
    assert service.is_process_running(42) is False

    monkeypatch.setattr(service, "_IS_WIN32", False)
    monkeypatch.setattr(service.os, "kill", lambda _pid, _signal: None)
    assert service.is_process_running(42) is True


def test_service_terminate_process_fallback_handles_disappearing_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(service, "_IS_WIN32", False)
    monkeypatch.setattr(
        service,
        "_killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(OSError("no group")),
    )
    monkeypatch.setattr(
        service.os,
        "kill",
        lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert service.terminate_process(42) is False


def test_service_registry_helpers_fail_closed_and_deduplicate_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    registry_path = tmp_path / "registry.db"
    registry_path.touch()
    closed: list[bool] = []

    class BrokenRegistry:
        def __init__(self, _path: Path) -> None:
            return None

        def active_leases(self) -> Any:
            raise RuntimeError("registry unavailable")

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        service, "registry_path_for_workflow", lambda _path: registry_path
    )
    monkeypatch.setattr(service, "RunRegistry", BrokenRegistry)
    assert service._active_backend_pids(tmp_path / "WORKFLOW.md") == []
    assert closed == [True]
    assert "could not inspect active backend processes" in capsys.readouterr().err
    assert (
        service._owned_workspace_paths(tmp_path / "WORKFLOW.md", owner_pid=None) == []
    )

    class BadPath:
        def resolve(self, *, strict: bool = False) -> Path:
            del strict
            raise OSError("cannot resolve")

        def __str__(self) -> str:
            return "workspace-bad"

    class WorkspaceRegistry:
        def __init__(self, _path: Path) -> None:
            return None

        def recent_runs(self, *, limit: int) -> list[Any]:
            assert limit == 200
            row = SimpleNamespace(owner_pid=42, workspace_path=BadPath())
            return [row, row]

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(service, "RunRegistry", WorkspaceRegistry)
    paths = service._owned_workspace_paths(tmp_path / "WORKFLOW.md", owner_pid=42)
    assert [str(path) for path in paths] == ["workspace-bad"]


def test_service_backend_termination_deduplicates_and_reports_survivor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    record = _service_record(tmp_path)
    monkeypatch.setattr(
        service, "_active_backend_pids", lambda *_args, **_kwargs: [7, 7, 8]
    )
    monkeypatch.setattr(
        service, "_owned_workspace_paths", lambda *_args, **_kwargs: [tmp_path]
    )
    monkeypatch.setattr(service, "_workspace_bound_process_pids", lambda _paths: [7, 9])
    monkeypatch.setattr(service, "is_process_running", lambda pid: pid != 8)
    terminated: list[int] = []
    monkeypatch.setattr(
        service,
        "terminate_process",
        lambda pid, *, force: terminated.append(pid) or True,
    )
    monkeypatch.setattr(service, "_wait_until", lambda predicate, **_kwargs: False)

    assert service._terminate_active_backend_processes(record) is False
    assert terminated == [7, 9]
    assert "backend agent pid=7 is still running" in capsys.readouterr().err


def test_service_doctor_formats_results_and_returns_false_for_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    import symphony.cli.doctor as doctor

    @dataclass
    class Config:
        server: Any

    cfg = Config(server=service.ServerConfig(port=None))
    results = [SimpleNamespace(status="pass"), SimpleNamespace(status="fail")]
    received: dict[str, Any] = {}

    def run_checks(checked: Any, *, host: str) -> list[Any]:
        received["cfg"] = checked
        received["host"] = host
        return results

    monkeypatch.setattr(doctor, "run_checks", run_checks)
    monkeypatch.setattr(
        doctor, "format_results", lambda value, *, color: "doctor output"
    )
    assert service._run_doctor_or_print(cfg, host="localhost", port=1234) is False
    assert received["cfg"].server.port == 1234
    assert received["host"] == "localhost"
    assert capsys.readouterr().out == "doctor output\n"


def test_service_port_resolution_prefers_argument_config_then_default() -> None:
    assert (
        service._resolve_port(1234, SimpleNamespace(server=SimpleNamespace(port=5678)))
        == 1234
    )
    assert (
        service._resolve_port(None, SimpleNamespace(server=SimpleNamespace(port=5678)))
        == 5678
    )
    assert service._resolve_port(
        None, SimpleNamespace(server=SimpleNamespace(port=None))
    ) == (service.DEFAULT_SERVICE_PORT)


def test_service_start_reports_missing_invalid_and_unsafe_workflows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    args = SimpleNamespace(workflow=None)
    monkeypatch.setattr(service, "resolve_workflow_path", lambda _raw: workflow)
    assert service._start(args) == 2
    assert "workflow file not found" in capsys.readouterr().err

    workflow.touch()
    monkeypatch.setattr(
        service,
        "_load_cfg",
        lambda _path: (_ for _ in ()).throw(SymphonyError("bad config")),
    )
    assert service._start(args) == 2
    assert "workflow load failed" in capsys.readouterr().err

    monkeypatch.setattr(service, "_load_cfg", lambda _path: SimpleNamespace())
    monkeypatch.setattr(
        service,
        "ensure_workflow_repo_is_safe",
        lambda _path: (_ for _ in ()).throw(SymphonyError("unsafe")),
    )
    assert service._start(args) == 1
    assert "unsafe workflow repository" in capsys.readouterr().err


def test_service_stop_without_record_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    monkeypatch.setattr(service, "resolve_workflow_path", lambda _raw: workflow)
    monkeypatch.setattr(service, "load_record", lambda _path: None)
    assert service._stop(SimpleNamespace(workflow=None)) == 0
    assert "no service record" in capsys.readouterr().out


def test_service_logs_prints_requested_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    record = _service_record(tmp_path)
    record.log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr(
        service, "resolve_workflow_path", lambda _raw: record.workflow_path
    )
    monkeypatch.setattr(service, "load_record", lambda _path: record)
    assert service._logs(SimpleNamespace(workflow=None, lines=2)) == 0
    assert capsys.readouterr().out == "two\nthree\n"


def test_service_port_owner_hint_filters_wrong_stopped_and_live_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _service_record(tmp_path, port=9999)
    monkeypatch.setattr(service, "load_record", lambda _path: record)
    assert service.port_owner_hint(record.workflow_path, 10000) is None
    assert (
        service.port_owner_hint(
            record.workflow_path,
            9999,
            is_running=lambda _pid: False,
            is_api_reachable=lambda _host, _port: False,
        )
        is None
    )
    hint = service.port_owner_hint(
        record.workflow_path,
        9999,
        is_running=lambda _pid: True,
        is_api_reachable=lambda _host, _port: False,
    )
    assert hint is not None and "owned by this workflow's service" in hint


def test_service_windows_liveness_rejects_pid_without_query_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SimpleNamespace(OpenProcess=lambda *_args: 0)
    monkeypatch.setattr(
        service.ctypes,
        "WinDLL",
        lambda _name, **_kwargs: kernel,
        raising=False,
    )
    assert service._is_process_running_windows(42) is False


def test_service_detached_spawn_uses_posix_session_and_closes_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, Any] = {}

    def popen(command: list[str], **kwargs: Any) -> SimpleNamespace:
        received["command"] = command
        received.update(kwargs)
        return SimpleNamespace(pid=55)

    monkeypatch.setattr(service, "_IS_WIN32", False)
    monkeypatch.setattr(service.subprocess, "Popen", popen)
    assert (
        service._popen_detached(
            ["python", "worker.py"],
            cwd=tmp_path,
            log_path=tmp_path / "log" / "service.log",
        )
        == 55
    )
    assert received["start_new_session"] is True
    assert received["stdout"].closed is True


def test_service_owned_workspace_scan_reports_registry_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    registry_path = tmp_path / "registry.db"
    registry_path.touch()

    class BrokenRegistry:
        def __init__(self, _path: Path) -> None:
            return None

        def recent_runs(self, *, limit: int) -> Any:
            del limit
            raise RuntimeError("broken rows")

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        service, "registry_path_for_workflow", lambda _path: registry_path
    )
    monkeypatch.setattr(service, "RunRegistry", BrokenRegistry)
    assert service._owned_workspace_paths(tmp_path / "WORKFLOW.md", owner_pid=42) == []
    assert "could not inspect workflow workspace paths" in capsys.readouterr().err


def test_service_start_lock_conflict_and_existing_service_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.touch()
    cfg = SimpleNamespace(server=SimpleNamespace(port=9999))
    args = SimpleNamespace(
        workflow=str(workflow),
        port=10000,
        host="localhost",
        replace=False,
        skip_doctor=True,
    )
    monkeypatch.setattr(service, "resolve_workflow_path", lambda _raw: workflow)
    monkeypatch.setattr(service, "_load_cfg", lambda _path: cfg)
    monkeypatch.setattr(service, "ensure_workflow_repo_is_safe", lambda _path: None)

    @contextlib.contextmanager
    def locked(_path: Path) -> Any:
        raise service.ServiceLockError("busy")
        yield

    monkeypatch.setattr(service, "acquire_service_lock", locked)
    assert service._start(args) == 1
    assert "busy" in capsys.readouterr().err

    record = _service_record(tmp_path, port=9999)
    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: service.ServiceStatus("running", record),
    )
    assert service._start_locked(args, workflow=workflow, cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "already running" in output and "requested port 10000 ignored" in output

    args.replace = True
    monkeypatch.setattr(service, "_stop", lambda _stop_args: 4)
    assert service._start_locked(args, workflow=workflow, cfg=cfg) == 4

    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: service.ServiceStatus("stopped", record),
    )
    assert service._start_locked(args, workflow=workflow, cfg=cfg) == 4


def test_service_start_locked_reports_early_exit_and_cleanup_on_probe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    cfg = SimpleNamespace(server=SimpleNamespace(port=9999))
    args = SimpleNamespace(
        workflow=str(workflow),
        port=None,
        host="localhost",
        replace=False,
        skip_doctor=True,
    )
    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: service.ServiceStatus("stopped", None),
    )
    monkeypatch.setattr(service, "_popen_detached", lambda *_args, **_kwargs: 42)
    monkeypatch.setattr(service, "_wait_until", lambda *_args, **_kwargs: False)
    assert service._start_locked(args, workflow=workflow, cfg=cfg) == 1
    assert "orchestrator exited early" in capsys.readouterr().err

    def probe_error(*_args: Any, **_kwargs: Any) -> bool:
        raise OSError("probe failed")

    terminated: list[int] = []
    monkeypatch.setattr(service, "_wait_until", probe_error)
    monkeypatch.setattr(
        service,
        "terminate_process",
        lambda pid, *, force: terminated.append(pid) or True,
    )
    assert service._start_locked(args, workflow=workflow, cfg=cfg) == 1
    assert terminated == [42]
    assert "probe failed" in capsys.readouterr().err


def test_service_force_stop_escalates_and_keeps_record_for_backend_survivor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    record = _service_record(tmp_path)
    monkeypatch.setattr(
        service, "resolve_workflow_path", lambda _raw: record.workflow_path
    )
    monkeypatch.setattr(service, "load_record", lambda _path: record)
    monkeypatch.setattr(service, "_IS_WIN32", False)
    monkeypatch.setattr(service, "is_process_running", lambda _pid: True)
    terminated: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        service,
        "terminate_process",
        lambda pid, *, force=False: terminated.append((pid, force)) or True,
    )
    waits = iter([False, True])
    monkeypatch.setattr(service, "_wait_until", lambda *_args, **_kwargs: next(waits))
    monkeypatch.setattr(
        service, "_terminate_active_backend_processes", lambda _record: False
    )
    args = SimpleNamespace(workflow=None, timeout=0.1, force=True)
    assert service._stop(args) == 1
    assert terminated == [(42, False), (42, True)]
    assert "service record kept" in capsys.readouterr().err


def test_progress_formatters_cover_short_hour_retry_pause_and_retry_queue() -> None:
    assert progress_md._format_elapsed(-3) == "0s"
    assert progress_md._format_elapsed(3_660) == "1h01m"
    assert progress_md._format_tokens(0) == ""
    assert progress_md._format_tokens(999) == "999 tok"

    now = progress_md.datetime(2026, 8, 24, tzinfo=progress_md.timezone.utc)
    row = {
        "started_at": "not-a-date",
        "attempt_kind": "retry",
        "attempt": 2,
        "paused": True,
    }
    assert progress_md._format_running_meta(row, now) == "retry 2 · paused"
    issue = SimpleNamespace(identifier="TICKET-1")
    assert (
        progress_md._format_ticket_cell(
            issue,
            None,
            {"attempt": 3, "kind": "backoff"},
            now,
        )
        == "TICKET-1 (backoff 3)"
    )


def test_progress_board_url_returns_trimmed_explicit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYMPHONY_BOARD_URL", "  https://board.example.test/path  ")
    assert progress_md._resolve_board_url(SimpleNamespace()) == (
        "https://board.example.test/path"
    )


def test_progress_atomic_write_reraises_and_tolerates_failed_temp_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        progress_md.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    monkeypatch.setattr(
        progress_md.os,
        "unlink",
        lambda *_args: (_ for _ in ()).throw(OSError("cleanup failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        progress_md._atomic_write_text(tmp_path / "progress.md", "body")


def test_progress_writer_path_and_missing_configuration_are_noops(
    tmp_path: Path,
) -> None:
    writer = progress_md.ProgressFileWriter(
        SimpleNamespace(),
        SimpleNamespace(current=lambda: None),
        tmp_path / "progress.md",
    )
    assert writer.path == tmp_path / "progress.md"
    asyncio.run(writer._refresh())
    assert not writer.path.exists()


def test_progress_tracker_scan_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        progress_md.tracker_module,
        "context_manager",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("tracker unavailable")),
    )
    cfg = SimpleNamespace(
        tracker=SimpleNamespace(active_states=("Todo",), terminal_states=("Done",))
    )
    assert progress_md.ProgressFileWriter._scan_tracker(cfg) == []


def test_artifact_text_name_and_size_boundaries() -> None:
    assert artifacts._clean_manifest_text(17) == ""
    assert len(artifacts.sanitize_artifact_name("x" * 200)) == 120
    assert artifacts.format_bytes(1_023) == "1023 B"
    assert artifacts.format_bytes(1_024) == "1 KB"
    assert artifacts.format_bytes(1024**2 + 512 * 1024) == "1.5 MB"
    assert artifacts.format_bytes(1024**3) == "1 GB"


def test_artifact_manifest_ignores_wrong_container_and_entries(tmp_path: Path) -> None:
    magic = tmp_path / artifacts.DEFAULT_MAGIC_DIR
    magic.mkdir()
    manifest = magic / artifacts.MANIFEST_NAME
    manifest.write_text('{"artifacts": "not-a-list"}', encoding="utf-8")
    assert artifacts._parse_manifest(magic) == {}

    manifest.write_text(
        json.dumps(
            {
                "artifacts": [
                    "not-a-map",
                    {"file": ""},
                    {"file": " report.txt ", "title": 17, "summary": None},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert artifacts._parse_manifest(magic) == {
        "report.txt": {"title": "", "summary": ""}
    }
