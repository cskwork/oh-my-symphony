"""Persistent run-state helpers for `symphony service`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    build_viewer_command,
    clear_record,
    is_process_running,
    load_record,
    main as service_main,
    record_path_for,
    save_record,
    service_status,
)


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


def _record(workflow_path: Path, *, pid: int | None = 1234, port: int = 9999) -> ServiceRecord:
    workflow_dir = workflow_path.parent
    return ServiceRecord(
        workflow_path=workflow_path.resolve(),
        workflow_dir=workflow_dir.resolve(),
        host="127.0.0.1",
        port=port,
        viewer_port=port + 1,
        orchestrator_pid=pid,
        viewer_pid=pid + 1 if pid is not None else None,
        log_path=workflow_dir / "log" / "symphony.log",
        viewer_log_path=workflow_dir / "log" / "symphony-viewer.log",
        started_at="2026-05-16T00:00:00Z",
        orchestrator_command=["symphony", str(workflow_path), "--port", str(port)],
        viewer_command=["symphony", "tui", str(workflow_path)],
    )


def test_record_path_is_inside_workflow_run_directory(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)

    path = record_path_for(workflow)

    assert path.parent == tmp_path / ".symphony" / "run"
    assert path.name.endswith(".json")
    assert all(ch.isalnum() or ch in "._-" for ch in path.name)


def test_save_and_load_record_round_trip(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    record = _record(workflow)

    save_record(record)

    loaded = load_record(workflow)
    assert loaded == record


def test_stale_record_is_reported_stopped(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))

    status = service_status(workflow, port=9999, is_running=lambda pid: False)

    assert status.state == "stopped"
    assert status.record is not None
    assert status.requested_port == 9999
    assert status.recorded_port == 9999


def test_service_status_uses_current_process_checker(tmp_path: Path, monkeypatch) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    monkeypatch.setattr(service_module, "is_process_running", lambda pid: pid == 1234)

    status = service_status(workflow, port=9999)

    assert status.state == "running"


def test_process_running_returns_false_for_invalid_pids() -> None:
    assert is_process_running(None) is False
    assert is_process_running(0) is False
    assert is_process_running(-1) is False


def test_live_record_is_running_even_when_requested_port_differs(tmp_path: Path) -> None:
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


def test_build_viewer_command_passes_workflow_path(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    viewer_dir = tmp_path / "tools" / "board-viewer"
    viewer_dir.mkdir(parents=True)
    (viewer_dir / "server.py").write_text("# viewer\n", encoding="utf-8")

    command = build_viewer_command(
        workflow,
        host="127.0.0.1",
        port=9999,
        viewer_port=8765,
        kanban_dir=tmp_path / "kanban",
    )

    assert command is not None
    assert "--workflow" in command
    assert str(workflow.resolve()) in command


def test_service_status_cli_reports_stopped(tmp_path: Path, capsys) -> None:
    workflow = _workflow(tmp_path)

    rc = service_main(["status", str(workflow)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "stopped" in out


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
    live_pids = {1234, 1235, 5678}
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
    assert stopped == [(1235, False), (1234, False), (5678, True)]
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
    live_pids = {1234, 1235, 5678}
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
    assert stopped == [(1235, False), (1234, False), (5678, True)]
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
    live_pids = {1234, 1235, 9010}
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
            f" 9010 node helper --working-dir {workspace}\n"
            " 9020 unrelated process\n"
        )

    def _fake_run(*args, **kwargs):  # noqa: ANN001, ANN002
        del args, kwargs
        return _Completed()

    monkeypatch.setattr(service_module, "terminate_process", _stop_pid)
    monkeypatch.setattr(service_module.subprocess, "run", _fake_run)

    rc = service_main(["stop", "--force", "--timeout", "0", str(workflow)])

    assert rc == 0
    assert stopped == [(1235, False), (1234, False), (9010, True)]
    assert 9010 not in live_pids
    assert load_record(workflow) is None


def test_service_lock_blocks_second_start_for_same_workflow(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)

    with acquire_service_lock(workflow):
        with pytest.raises(ServiceLockError):
            with acquire_service_lock(workflow):
                pass


def test_start_cleans_live_viewer_from_stale_record_before_doctor(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    save_record(_record(workflow, pid=1234))
    live_pids = {1235}
    stopped: list[int | None] = []
    monkeypatch.setattr(
        service_module,
        "is_process_running",
        lambda pid: pid in live_pids,
    )

    def _stop_pid(pid, *args, **kwargs):  # noqa: ANN001
        stopped.append(pid)
        live_pids.discard(pid)
        return True

    monkeypatch.setattr(service_module, "terminate_process", _stop_pid)
    monkeypatch.setattr(service_module, "_run_doctor_or_print", lambda *args, **kwargs: False)

    rc = service_main(["start", str(workflow)])

    captured = capsys.readouterr()
    assert rc == 1
    assert stopped == [1235]
    assert load_record(workflow) is None
    assert "doctor reported FAIL" in captured.err


def test_start_cleans_spawned_process_if_record_save_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workflow = _workflow(tmp_path)
    stopped: list[int | None] = []
    monkeypatch.setattr(service_module, "_run_doctor_or_print", lambda *args, **kwargs: True)
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

    rc = service_main(["start", "--skip-doctor", "--no-viewer", str(workflow)])

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
