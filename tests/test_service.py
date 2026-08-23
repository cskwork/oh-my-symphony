"""Persistent run-state helpers for `symphony service`."""

from __future__ import annotations

import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from symphony import cli
from symphony import service as service_module
from symphony.issue import Issue
from symphony.orchestrator.run_registry import RunRegistry, registry_path_for_workflow
from symphony.service import (
    ServiceRecord,
    ServiceLockError,
    acquire_service_lock,
    build_orchestrator_command,
    clear_record,
    is_process_running,
    is_symphony_workflow_reachable,
    load_record,
    main as service_main,
    record_path_for,
    save_record,
    service_status,
)


SERVICE_CAPABILITY = "a" * 43
OTHER_SERVICE_CAPABILITY = "b" * 43


def _workflow(tmp_path: Path) -> Path:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("---\ntracker: {kind: file}\n---\nbody\n", encoding="utf-8")
    return workflow


def _issue(identifier: str = "SMA-1") -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="",
        priority=None,
        state="Verify",
        created_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )


def _record(
    workflow_path: Path,
    *,
    pid: int | None = 1234,
    port: int = 9999,
    service_instance_id: str | None = None,
) -> ServiceRecord:
    workflow_dir = workflow_path.parent
    return ServiceRecord(
        workflow_path=workflow_path.resolve(),
        workflow_dir=workflow_dir.resolve(),
        host="127.0.0.1",
        port=port,
        orchestrator_pid=pid,
        log_path=workflow_dir / "log" / "symphony.log",
        started_at="2026-05-16T00:00:00Z",
        orchestrator_command=["symphony", str(workflow_path), "--port", str(port)],
        service_instance_id=service_instance_id,
    )


def test_record_path_is_inside_workflow_run_directory(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)

    path = record_path_for(workflow)

    assert path.parent == tmp_path / ".symphony" / "run"
    assert path.name.endswith(".json")
    assert all(ch.isalnum() or ch in "._-" for ch in path.name)


def test_save_and_load_record_round_trip(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    record = _record(workflow, service_instance_id="instance-a")

    save_record(record)

    loaded = load_record(workflow)
    assert loaded == record


def test_load_legacy_record_without_service_instance_id(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    record = _record(workflow)
    save_record(record)
    path = record_path_for(workflow)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("service_instance_id", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_record(workflow)

    assert loaded is not None
    assert loaded.service_instance_id is None


@pytest.mark.parametrize(
    "malformed_instance_id",
    [
        pytest.param(True, id="boolean"),
        pytest.param(1234, id="integer"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace"),
        pytest.param("x" * 129, id="oversized"),
    ],
)
def test_load_record_normalizes_malformed_service_instance_id(
    tmp_path: Path, malformed_instance_id: object
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow))
    path = record_path_for(workflow)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["service_instance_id"] = malformed_instance_id
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_record(workflow)

    assert loaded is not None
    assert loaded.service_instance_id is None


def test_stale_record_is_reported_stopped(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))

    status = service_status(
        workflow,
        port=9999,
        is_running=lambda pid: False,
        is_api_reachable=lambda host, port: False,
    )

    assert status.state == "stopped"
    assert status.record is not None
    assert status.requested_port == 9999
    assert status.recorded_port == 9999


def test_stale_pid_with_live_api_is_reported_running(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, port=9999))

    status = service_status(
        workflow,
        port=9999,
        is_running=lambda pid: False,
        is_api_reachable=lambda host, port: (host, port) == ("127.0.0.1", 9999),
    )

    assert status.state == "running"
    assert status.pid_running is False
    assert status.api_reachable is True
    assert status.record is not None
    assert status.record.orchestrator_pid == 1234


def test_service_status_uses_current_process_checker(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    monkeypatch.setattr(service_module, "is_process_running", lambda pid: pid == 1234)
    monkeypatch.setattr(
        service_module,
        "is_symphony_workflow_reachable",
        lambda *_args: pytest.fail("live PID must not perform an HTTP identity probe"),
    )

    status = service_status(workflow, port=9999)

    assert status.state == "running"


def test_stale_service_status_uses_recorded_instance_for_health_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    seen: list[tuple[str, int, Path, str | None]] = []

    def _reachable(
        host: str,
        port: int,
        served_workflow: str | Path,
        *,
        service_instance_id: str | None = None,
    ) -> bool:
        seen.append((host, port, Path(served_workflow), service_instance_id))
        return service_instance_id == "instance-a"

    monkeypatch.setattr(
        service_module, "is_symphony_workflow_reachable", _reachable
    )

    status = service_status(workflow, is_running=lambda _pid: False)

    assert status.state == "running"
    assert status.api_reachable is True
    assert seen == [("127.0.0.1", 9999, workflow.resolve(), "instance-a")]


def test_stale_port_owner_hint_uses_recorded_instance_for_health_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    seen: list[tuple[str, int, Path, str | None]] = []

    def _reachable(
        host: str,
        port: int,
        served_workflow: str | Path,
        *,
        service_instance_id: str | None = None,
    ) -> bool:
        seen.append((host, port, Path(served_workflow), service_instance_id))
        return service_instance_id == "instance-a"

    monkeypatch.setattr(
        service_module, "is_symphony_workflow_reachable", _reachable
    )

    hint = service_module.port_owner_hint(
        workflow, 9999, is_running=lambda _pid: False
    )

    assert hint is not None
    assert "recorded Symphony API responds" in hint
    assert seen == [("127.0.0.1", 9999, workflow.resolve(), "instance-a")]


def test_exact_workflow_probe_uses_health_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path).resolve()
    seen: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            seen["limit"] = limit
            return json.dumps({"workflow_path": str(workflow)}).encode()

    class Opener:
        def open(self, request: object, timeout: float) -> Response:
            seen["url"] = request.full_url  # type: ignore[attr-defined]
            seen["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        service_module.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    assert is_symphony_workflow_reachable("0.0.0.0", 9999, workflow) is True
    assert seen == {
        "url": "http://127.0.0.1:9999/api/v1/health",
        "timeout": 0.5,
        "limit": 4096,
    }


def test_service_identity_probe_accepts_exact_instance_and_serving_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path).resolve()
    monkeypatch.setattr(
        service_module,
        "_probe_json",
        lambda *_args: {
            "workflow_path": str(workflow),
            "service_instance_id": SERVICE_CAPABILITY,
            "orchestrator_pid": 5678,
        },
    )

    identity = service_module.probe_service_endpoint_identity(
        "127.0.0.1", 9999, workflow, SERVICE_CAPABILITY
    )

    assert identity is not None
    assert identity.orchestrator_pid == 5678


def test_service_identity_probe_sends_instance_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path).resolve()
    seen: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "workflow_path": str(workflow),
                    "service_instance_id": SERVICE_CAPABILITY,
                    "orchestrator_pid": 5678,
                }
            ).encode()

    class Opener:
        def open(self, request: object, **_kwargs: object) -> Response:
            seen["instance_header"] = request.get_header(  # type: ignore[attr-defined]
                "X-symphony-service-instance"
            )
            return Response()

    monkeypatch.setattr(
        service_module.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    identity = service_module.probe_service_endpoint_identity(
        "127.0.0.1", 9999, workflow, SERVICE_CAPABILITY
    )

    assert identity is not None
    assert identity.orchestrator_pid == 5678
    assert seen == {"instance_header": SERVICE_CAPABILITY}


def test_service_identity_probe_rejects_weak_instance_without_http_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path).resolve()
    monkeypatch.setattr(
        service_module,
        "_probe_json",
        lambda *_args: pytest.fail("weak service instance IDs must not be probed"),
    )

    assert (
        service_module.probe_service_endpoint_identity(
            "127.0.0.1", 9999, workflow, "instance-a"
        )
        is None
    )


def test_probe_json_does_not_send_short_service_instance_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b"{}"

    class Opener:
        def open(self, request: object, **_kwargs: object) -> Response:
            seen["instance_header"] = request.get_header(  # type: ignore[attr-defined]
                "X-symphony-service-instance"
            )
            return Response()

    monkeypatch.setattr(
        service_module.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    assert service_module._probe_json(
        "127.0.0.1", 9999, "/api/v1/health", "instance-a"
    ) == {}
    assert seen == {"instance_header": None}


@pytest.mark.parametrize(
    ("service_instance_id", "served_instance_id"),
    [
        pytest.param(SERVICE_CAPABILITY, None, id="missing"),
        pytest.param(SERVICE_CAPABILITY, "", id="empty"),
        pytest.param(SERVICE_CAPABILITY, True, id="boolean"),
        pytest.param(
            SERVICE_CAPABILITY,
            OTHER_SERVICE_CAPABILITY,
            id="mismatch",
        ),
        pytest.param(SERVICE_CAPABILITY, "x" * 129, id="oversized-served"),
        pytest.param("", "", id="empty-expected"),
        pytest.param("   ", "   ", id="whitespace-expected"),
        pytest.param("x" * 129, "x" * 129, id="oversized-expected"),
    ],
)
def test_service_identity_probe_rejects_invalid_or_mismatched_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_instance_id: str,
    served_instance_id: object,
) -> None:
    workflow = _workflow(tmp_path).resolve()
    monkeypatch.setattr(
        service_module,
        "_probe_json",
        lambda *_args: {
            "workflow_path": str(workflow),
            "service_instance_id": served_instance_id,
            "orchestrator_pid": 5678,
        },
    )

    assert (
        service_module.probe_service_endpoint_identity(
            "127.0.0.1", 9999, workflow, service_instance_id
        )
        is None
    )


@pytest.mark.parametrize(
    "served_pid",
    [
        pytest.param(True, id="boolean"),
        pytest.param("1234", id="string"),
        pytest.param(1234.0, id="float"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
    ],
)
def test_service_identity_probe_rejects_invalid_orchestrator_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    served_pid: object,
) -> None:
    workflow = _workflow(tmp_path).resolve()
    monkeypatch.setattr(
        service_module,
        "_probe_json",
        lambda *_args: {
            "workflow_path": str(workflow),
            "service_instance_id": SERVICE_CAPABILITY,
            "orchestrator_pid": served_pid,
        },
    )

    assert (
        service_module.probe_service_endpoint_identity(
            "127.0.0.1", 9999, workflow, SERVICE_CAPABILITY
        )
        is None
    )


def test_service_identity_probe_rejects_other_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path).resolve()
    monkeypatch.setattr(
        service_module,
        "_probe_json",
        lambda *_args: {
            "workflow_path": str(tmp_path / "other" / "WORKFLOW.md"),
            "service_instance_id": SERVICE_CAPABILITY,
            "orchestrator_pid": 5678,
        },
    )

    assert (
        service_module.probe_service_endpoint_identity(
            "127.0.0.1", 9999, workflow, SERVICE_CAPABILITY
        )
        is None
    )


def test_exact_workflow_probe_keeps_legacy_three_argument_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path).resolve()
    monkeypatch.setattr(
        service_module,
        "_probe_json",
        lambda *_args: {"workflow_path": str(workflow)},
    )

    assert is_symphony_workflow_reachable("127.0.0.1", 9999, workflow) is True


def test_exact_workflow_probe_rejects_other_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {"workflow_path": str(tmp_path / "other" / "WORKFLOW.md")}
            ).encode()

    class Opener:
        def open(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(
        service_module.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    assert is_symphony_workflow_reachable("127.0.0.1", 9999, workflow) is False


def test_stale_record_port_serving_other_workflow_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {"workflow_path": str(tmp_path / "other" / "WORKFLOW.md")}
            ).encode()

    class Opener:
        def open(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(
        service_module.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    status = service_status(workflow, is_running=lambda _pid: False)

    assert status.state == "stopped"
    assert status.api_reachable is False


def test_exact_workflow_probe_rejects_redirects(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path).resolve()

    class RedirectingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api/v1/health":
                self.send_response(302)
                self.send_header("Location", "/matching-workflow")
                self.end_headers()
                return
            payload = json.dumps({"workflow_path": str(workflow)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectingHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert (
            is_symphony_workflow_reachable("127.0.0.1", server.server_port, workflow)
            is False
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_exact_workflow_probe_brackets_ipv6_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path).resolve()
    seen_urls: list[str] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps({"workflow_path": str(workflow)}).encode()

    class Opener:
        def open(self, request: object, **_kwargs: object) -> Response:
            seen_urls.append(request.full_url)  # type: ignore[attr-defined]
            return Response()

    monkeypatch.setattr(
        service_module.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    assert is_symphony_workflow_reachable("::1", 9999, workflow) is True
    assert seen_urls == ["http://[::1]:9999/api/v1/health"]


@pytest.mark.parametrize("served_workflow", ["", "relative/WORKFLOW.md", "/tmp/\x00"])
def test_exact_workflow_probe_rejects_malformed_peer_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    served_workflow: str,
) -> None:
    workflow = _workflow(tmp_path).resolve()

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps({"workflow_path": served_workflow}).encode()

    class Opener:
        def open(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(
        service_module.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    assert is_symphony_workflow_reachable("127.0.0.1", 9999, workflow) is False


def test_process_running_returns_false_for_invalid_pids() -> None:
    assert is_process_running(None) is False
    assert is_process_running(0) is False
    assert is_process_running(-1) is False


def test_live_record_is_running_even_when_requested_port_differs(
    tmp_path: Path,
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, port=9999))

    status = service_status(workflow, port=10000, is_running=lambda pid: pid == 1234)

    assert status.state == "running"
    assert status.record is not None
    assert status.requested_port == 10000
    assert status.recorded_port == 9999


def test_clear_record_removes_saved_state(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow))

    assert load_record(workflow) is not None
    clear_record(workflow)

    assert load_record(workflow) is None
    assert not record_path_for(workflow).exists()


def test_build_orchestrator_command_uses_python_module(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)

    command = build_orchestrator_command(workflow, host="127.0.0.1", port=9999)

    assert command[1:3] == ["-m", "symphony.cli"]
    assert str(workflow.resolve()) in command
    assert "--port" in command
    assert "--host" in command


def test_service_status_cli_reports_stopped(tmp_path: Path, capsys) -> None:
    workflow = _workflow(tmp_path)

    rc = service_main(["status", str(workflow)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "stopped" in out


def test_service_status_cli_reports_live_api_with_stale_pid(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, port=9999))
    monkeypatch.setattr(service_module, "is_process_running", lambda pid: False)
    monkeypatch.setattr(
        service_module,
        "is_symphony_workflow_reachable",
        lambda host, port, served_workflow: (
            (
                host,
                port,
                Path(served_workflow),
            )
            == ("127.0.0.1", 9999, workflow)
        ),
    )

    rc = service_main(["status", str(workflow)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "running" in out
    assert "stale pid=1234" in out
    assert "api alive" in out


def test_top_level_cli_routes_service_status(tmp_path: Path, capsys) -> None:
    workflow = _workflow(tmp_path)

    rc = cli.main(["service", "status", str(workflow)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "stopped" in out


def test_service_stop_keeps_record_when_process_survives(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    monkeypatch.setattr(service_module, "is_process_running", lambda pid: True)
    monkeypatch.setattr(service_module, "terminate_process", lambda pid: True)
    monkeypatch.setattr(service_module, "_wait_until", lambda *args, **kwargs: False)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    captured = capsys.readouterr()
    assert rc == 1
    assert "record kept" in captured.err
    assert load_record(workflow) is not None


def test_windows_service_stop_escalates_exact_instance_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    live_pids = {1234, 5678}
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(
        service_module, "is_process_running", lambda pid: pid in live_pids
    )
    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        lambda host, port, served_workflow, instance_id: (
            SimpleNamespace(orchestrator_pid=5678)
            if (host, port, Path(served_workflow), instance_id)
            == ("127.0.0.1", 9999, workflow.resolve(), "instance-a")
            else None
        ),
        raising=False,
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )
    monkeypatch.setattr(
        service_module,
        "_terminate_active_backend_processes",
        lambda _record: pytest.fail("plain stop must not sweep backend processes"),
    )

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        if force:
            live_pids.discard(pid)
            live_pids.discard(1234)
        return True

    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 0
    assert terminated == [(1234, False), (5678, True)]
    assert load_record(workflow) is None


@pytest.mark.parametrize("instance_id", [None, "instance-a"], ids=["legacy", "mismatch"])
def test_windows_service_stop_does_not_force_unverified_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    instance_id: str | None,
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id=instance_id))
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(service_module, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        lambda *_args: None,
        raising=False,
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )
    monkeypatch.setattr(
        service_module,
        "_terminate_active_backend_processes",
        lambda _record: pytest.fail("plain stop must not sweep backend processes"),
    )

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        return True

    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 1
    assert terminated == [(1234, False)]
    assert load_record(workflow) is not None


def test_windows_service_stop_keeps_record_when_forced_taskkill_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    taskkills: list[tuple[int, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(service_module, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        lambda *_args: SimpleNamespace(orchestrator_pid=5678),
        raising=False,
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    def _deny_taskkill(pid: int, *, force: bool = False) -> bool:
        taskkills.append((pid, force))
        return False

    monkeypatch.setattr(service_module, "_taskkill_tree", _deny_taskkill)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 1
    assert taskkills == [(1234, False), (5678, True)]
    assert load_record(workflow) is not None


def test_posix_service_stop_never_auto_forces_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", False)
    monkeypatch.setattr(service_module, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        lambda *_args, **_kwargs: pytest.fail("POSIX stop must not probe for escalation"),
        raising=False,
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        return True

    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 1
    assert terminated == [(1234, False)]
    assert load_record(workflow) is not None


def test_windows_service_stop_does_not_force_when_target_exits_after_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    live_pids = {1234, 5678}
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(
        service_module, "is_process_running", lambda pid: pid in live_pids
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    def _authorize_and_exit(
        host: str,
        port: int,
        served_workflow: str | Path,
        instance_id: str,
    ) -> object:
        assert (host, port, Path(served_workflow), instance_id) == (
            "127.0.0.1",
            9999,
            workflow.resolve(),
            "instance-a",
        )
        live_pids.remove(5678)
        return SimpleNamespace(orchestrator_pid=5678)

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        return True

    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        _authorize_and_exit,
        raising=False,
    )
    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 1
    assert terminated == [(1234, False)]
    assert load_record(workflow) is not None


def test_windows_service_stop_retains_record_when_pid_exits_during_failed_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    live_pids = {1234}
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(
        service_module, "is_process_running", lambda pid: pid in live_pids
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    def _reject_after_exit(*_args: object, **_kwargs: object) -> None:
        live_pids.remove(1234)
        return None

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        return True

    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        _reject_after_exit,
        raising=False,
    )
    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 1
    assert terminated == [(1234, False)]
    assert load_record(workflow) is not None


@pytest.mark.parametrize(
    "health_payload",
    [
        pytest.param(None, id="unreachable"),
        pytest.param(
            {
                "workflow_path": "__WORKFLOW__",
                "service_instance_id": "instance-b",
                "orchestrator_pid": 5678,
            },
            id="instance-mismatch",
        ),
        pytest.param(
            {
                "workflow_path": "__WORKFLOW__",
                "service_instance_id": "instance-a",
                "orchestrator_pid": "5678",
            },
            id="malformed-serving-pid",
        ),
    ],
)
def test_windows_service_stop_retains_token_record_when_launcher_is_absent_and_probe_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    health_payload: dict[str, object] | None,
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    payload = (
        {
            key: str(workflow.resolve()) if value == "__WORKFLOW__" else value
            for key, value in health_payload.items()
        }
        if health_payload is not None
        else None
    )
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(service_module, "is_process_running", lambda _pid: False)
    monkeypatch.setattr(service_module, "_probe_json", lambda *_args: payload)
    monkeypatch.setattr(
        service_module,
        "terminate_process",
        lambda *_args, **_kwargs: pytest.fail("an unproved PID must not be terminated"),
    )

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 1
    assert load_record(workflow) is not None


def test_windows_service_stop_still_stops_proved_target_when_launcher_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    live_pids = {1234, 5678}
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(
        service_module, "is_process_running", lambda pid: pid in live_pids
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    def _prove_after_launcher_exit(*_args: object) -> object:
        live_pids.remove(1234)
        return SimpleNamespace(orchestrator_pid=5678)

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        if force:
            live_pids.discard(pid)
        return True

    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        _prove_after_launcher_exit,
    )
    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 0
    assert terminated == [(1234, False), (5678, True)]
    assert load_record(workflow) is None


def test_windows_service_stop_probes_after_initial_launcher_stop_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    live_pids = {1234, 5678}
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(
        service_module, "is_process_running", lambda pid: pid in live_pids
    )
    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        lambda *_args: SimpleNamespace(orchestrator_pid=5678),
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        live_pids.discard(pid)
        return True

    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 0
    assert terminated == [(1234, False), (5678, True)]
    assert load_record(workflow) is None


def test_windows_service_stop_probes_when_launcher_is_already_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    live_pids = {5678}
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(
        service_module, "is_process_running", lambda pid: pid in live_pids
    )
    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        lambda *_args: SimpleNamespace(orchestrator_pid=5678),
    )
    monkeypatch.setattr(
        service_module,
        "_wait_until",
        lambda predicate, **_kwargs: predicate(),
    )

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        live_pids.discard(pid)
        return True

    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 0
    assert terminated == [(5678, True)]
    assert load_record(workflow) is None


def test_posix_plain_stop_does_not_add_a_post_timeout_liveness_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    running_checks: list[int | None] = []
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", False)

    def _is_running(pid: int | None) -> bool:
        running_checks.append(pid)
        return len(running_checks) == 1

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        return True

    monkeypatch.setattr(service_module, "is_process_running", _is_running)
    monkeypatch.setattr(service_module, "terminate_process", _terminate)
    monkeypatch.setattr(service_module, "_wait_until", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        lambda *_args, **_kwargs: pytest.fail("POSIX stop must not probe for escalation"),
        raising=False,
    )

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 1
    assert running_checks == [1234]
    assert terminated == [(1234, False)]
    assert load_record(workflow) is not None


def test_windows_service_stop_never_forces_reused_launcher_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234, service_instance_id="instance-a"))
    live_pids = {1234, 5678}
    terminated: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(service_module, "_IS_WIN32", True)
    monkeypatch.setattr(
        service_module, "is_process_running", lambda pid: pid in live_pids
    )
    monkeypatch.setattr(
        service_module,
        "probe_service_endpoint_identity",
        lambda *_args: SimpleNamespace(orchestrator_pid=5678),
        raising=False,
    )
    monkeypatch.setattr(
        service_module, "_wait_until", lambda predicate, **_kwargs: predicate()
    )

    def _terminate(pid: int | None, *, force: bool = False) -> bool:
        terminated.append((pid, force))
        if force:
            live_pids.discard(pid)
        return True

    monkeypatch.setattr(service_module, "terminate_process", _terminate)

    rc = service_main(["stop", "--timeout", "0", str(workflow)])

    assert rc == 1
    assert terminated == [(1234, False), (5678, True)]
    assert load_record(workflow) is not None


def test_force_stop_terminates_active_backend_processes_from_registry(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    registry = RunRegistry(
        registry_path_for_workflow(workflow),
        lease_ttl=timedelta(minutes=5),
    )
    issue = _issue()
    now = datetime.now(timezone.utc)
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "workspaces" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="pi",
        now=now,
    )
    assert run_id
    assert registry.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=1),
        backend_agent_pid=5678,
    )
    registry.close()
    live_pids = {1234, 5678}
    stopped: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(
        service_module,
        "is_process_running",
        lambda pid: pid in live_pids,
    )
    monkeypatch.setattr(
        service_module,
        "is_symphony_api_reachable",
        lambda host, port: False,
    )

    def _stop_pid(pid, *, force=False):  # noqa: ANN001, ANN002
        stopped.append((pid, force))
        live_pids.discard(pid)
        return True

    monkeypatch.setattr(service_module, "terminate_process", _stop_pid)

    rc = service_main(["stop", "--force", "--timeout", "0", str(workflow)])

    assert rc == 0
    assert stopped == [(1234, False), (5678, True)]
    assert 5678 not in live_pids
    assert load_record(workflow) is None


def test_force_stop_terminates_owned_backend_process_after_run_completed(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    registry = RunRegistry(
        registry_path_for_workflow(workflow),
        lease_ttl=timedelta(minutes=5),
        owner_pid=1234,
    )
    issue = _issue()
    now = datetime.now(timezone.utc)
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "workspaces" / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="opencode",
        now=now,
    )
    assert run_id
    assert registry.heartbeat(
        issue_id=issue.id,
        run_id=run_id,
        now=now + timedelta(seconds=1),
        backend_agent_pid=5678,
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="normal",
        now=now + timedelta(seconds=2),
    )
    registry.close()
    live_pids = {1234, 5678}
    stopped: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(
        service_module,
        "is_process_running",
        lambda pid: pid in live_pids,
    )

    def _stop_pid(pid, *, force=False):  # noqa: ANN001, ANN002
        stopped.append((pid, force))
        live_pids.discard(pid)
        return True

    monkeypatch.setattr(service_module, "terminate_process", _stop_pid)

    rc = service_main(["stop", "--force", "--timeout", "0", str(workflow)])

    assert rc == 0
    assert stopped == [(1234, False), (5678, True)]
    assert 5678 not in live_pids
    assert load_record(workflow) is None


def test_force_stop_terminates_processes_referencing_owned_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    registry = RunRegistry(
        registry_path_for_workflow(workflow),
        lease_ttl=timedelta(minutes=5),
        owner_pid=1234,
    )
    issue = _issue()
    workspace = tmp_path / "workspaces" / issue.identifier
    now = datetime.now(timezone.utc)
    run_id = registry.acquire_run(
        issue,
        workspace_path=workspace,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=now,
    )
    assert run_id
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="normal",
        now=now + timedelta(seconds=1),
    )
    registry.close()
    live_pids = {1234, 9010}
    stopped: list[tuple[int | None, bool]] = []
    monkeypatch.setattr(
        service_module,
        "is_process_running",
        lambda pid: pid in live_pids,
    )

    def _stop_pid(pid, *, force=False):  # noqa: ANN001, ANN002
        stopped.append((pid, force))
        live_pids.discard(pid)
        return True

    class _Completed:
        stdout = (
            f" 9010 node helper --working-dir {workspace}\n 9020 unrelated process\n"
        )

    def _fake_run(*args, **kwargs):  # noqa: ANN001, ANN002
        del args, kwargs
        return _Completed()

    monkeypatch.setattr(service_module, "terminate_process", _stop_pid)
    monkeypatch.setattr(service_module.subprocess, "run", _fake_run)

    rc = service_main(["stop", "--force", "--timeout", "0", str(workflow)])

    assert rc == 0
    assert stopped == [(1234, False), (9010, True)]
    assert 9010 not in live_pids
    assert load_record(workflow) is None


def test_service_lock_blocks_second_start_for_same_workflow(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)

    with acquire_service_lock(workflow):
        with pytest.raises(ServiceLockError):
            with acquire_service_lock(workflow):
                pass


def test_popen_detached_overwrites_instance_only_in_child_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class _Process:
        pid = 1234

    def _popen(command: list[str], **kwargs: object) -> _Process:
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return _Process()

    monkeypatch.setenv("SYMPHONY_SERVICE_INSTANCE_ID", "stale-parent")
    monkeypatch.setattr(service_module.subprocess, "Popen", _popen)

    pid = service_module._popen_detached(
        ["python", "-m", "symphony.cli"],
        cwd=tmp_path,
        log_path=tmp_path / "symphony.log",
        env_overrides={"SYMPHONY_SERVICE_INSTANCE_ID": "instance-a"},
    )

    assert pid == 1234
    assert seen["command"] == ["python", "-m", "symphony.cli"]
    assert isinstance(seen["env"], dict)
    assert seen["env"]["SYMPHONY_SERVICE_INSTANCE_ID"] == "instance-a"
    assert os.environ["SYMPHONY_SERVICE_INSTANCE_ID"] == "stale-parent"


def test_start_generates_and_persists_one_service_instance_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(tmp_path)
    token_sizes: list[int] = []
    child_environments: list[dict[str, str]] = []

    def _token_urlsafe(size: int) -> str:
        token_sizes.append(size)
        return "instance-a"

    def _spawn(*_args: object, **kwargs: object) -> int:
        child_environments.append(kwargs["env_overrides"])  # type: ignore[arg-type]
        return 1234

    monkeypatch.setattr(secrets, "token_urlsafe", _token_urlsafe)
    monkeypatch.setattr(service_module, "_popen_detached", _spawn)
    monkeypatch.setattr(service_module, "_wait_until", lambda *_args, **_kwargs: True)

    rc = service_main(["start", "--skip-doctor", str(workflow)])

    assert rc == 0
    assert token_sizes == [32]
    assert child_environments == [
        {"SYMPHONY_SERVICE_INSTANCE_ID": "instance-a"}
    ]
    record = load_record(workflow)
    assert record is not None
    assert record.service_instance_id == "instance-a"


def test_start_clears_stale_record_before_doctor(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    monkeypatch.setattr(
        service_module,
        "is_process_running",
        lambda pid: False,
    )
    monkeypatch.setattr(
        service_module,
        "is_symphony_workflow_reachable",
        lambda host, port, served_workflow: False,
    )
    monkeypatch.setattr(
        service_module, "_run_doctor_or_print", lambda *args, **kwargs: False
    )

    rc = service_main(["start", str(workflow)])

    captured = capsys.readouterr()
    assert rc == 1
    assert load_record(workflow) is None
    assert "doctor reported FAIL" in captured.err


def test_start_cleans_spawned_process_if_record_save_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    stopped: list[int | None] = []
    monkeypatch.setattr(
        service_module, "_run_doctor_or_print", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(service_module, "_popen_detached", lambda *args, **kwargs: 1234)
    monkeypatch.setattr(service_module, "_wait_until", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        service_module,
        "save_record",
        lambda record: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        service_module,
        "terminate_process",
        lambda pid, *args, **kwargs: stopped.append(pid) or True,
    )

    rc = service_main(["start", "--skip-doctor", str(workflow)])

    captured = capsys.readouterr()
    assert rc == 1
    assert stopped == [1234]
    assert "failed to save service record" in captured.err


def test_restart_aborts_when_stop_fails(tmp_path: Path, monkeypatch) -> None:
    workflow = _workflow(tmp_path)
    starts: list[object] = []
    monkeypatch.setattr(service_module, "_stop", lambda args: 1)
    monkeypatch.setattr(
        service_module,
        "_start",
        lambda args: starts.append(args) or 0,
    )

    rc = service_main(["restart", str(workflow)])

    assert rc == 1
    assert starts == []
