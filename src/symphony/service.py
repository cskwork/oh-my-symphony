"""Run-state persistence for the built-in `symphony service` command.

This module intentionally avoids launching or stopping processes.  It is the
small, unit-testable layer that records what a service command started and
answers whether an existing record still points at live processes.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from ._shell import _taskkill_tree
from .errors import SymphonyError
from .orchestrator.run_registry import RunRegistry, registry_path_for_workflow
from .runtime_safety import ensure_workflow_repo_is_safe
from .service_identity import (
    SERVICE_INSTANCE_ENV,
    SERVICE_INSTANCE_HEADER,
    normalize_service_instance_id,
    normalize_service_probe_credential,
)
from .workflow import (
    ServerConfig,
    build_service_config,
    load_workflow,
    resolve_workflow_path,
)


ProcessRunningPredicate = Callable[[int | None], bool]
ServiceApiProbe = Callable[[str, int], bool]
ServiceState = Literal["running", "stopped"]

# Module-level runtime bool. Pyright narrows literal `sys.platform == "win32"`
# at evaluation time, marking Win-only branches as unreachable on macOS/Linux.
# A separately-bound bool keeps every branch analyzable on every host.
_IS_WIN32: bool = sys.platform == "win32"
# POSIX-only signal APIs resolved once (same pattern as the getattr'd
# CREATE_NEW_PROCESS_GROUP flags in _spawn): the module imports and
# type-checks on win32, while the guarded branches below keep their exact
# POSIX behavior — os.killpg and signal.SIGKILL always exist there.
_killpg: Callable[[int, int], None] | None = getattr(os, "killpg", None)
_SIGKILL: int = getattr(signal, "SIGKILL", signal.SIGTERM)
DEFAULT_SERVICE_PORT = 9999


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep identity probes bound to the endpoint they were asked to inspect."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _probe_host(host: str) -> str:
    normalized = host.strip()
    if normalized in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    return f"[{normalized}]" if ":" in normalized else normalized


def _probe_json(
    host: str,
    port: int,
    endpoint: str,
    service_instance_id: str | None = None,
) -> Any | None:
    try:
        url = f"http://{_probe_host(host)}:{port}{endpoint}"
        headers = {"Accept": "application/json"}
        normalized_instance_id = normalize_service_probe_credential(
            service_instance_id
        )
        if normalized_instance_id is not None:
            headers[SERVICE_INSTANCE_HEADER] = normalized_instance_id
        request = urllib.request.Request(url, headers=headers)
        opener = urllib.request.build_opener(_RejectRedirects())
        with opener.open(request, timeout=0.5) as response:
            if response.status != 200:
                return None
            return json.loads(response.read(4096).decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ):
        return None


class ServiceLockError(RuntimeError):
    """Raised when another service operation already owns the workflow lock."""


@dataclass(frozen=True)
class ServiceRecord:
    workflow_path: Path
    workflow_dir: Path
    host: str
    port: int
    orchestrator_pid: int | None
    log_path: Path
    started_at: str
    orchestrator_command: list[str] = field(default_factory=list)
    service_instance_id: str | None = None


@dataclass(frozen=True)
class ServiceEndpointIdentity:
    orchestrator_pid: int


@dataclass(frozen=True)
class ServiceStatus:
    state: ServiceState
    record: ServiceRecord | None
    requested_port: int | None = None
    recorded_port: int | None = None
    pid_running: bool = False
    api_reachable: bool = False


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def record_path_for(workflow_path: str | Path) -> Path:
    """Return the deterministic JSON run-state path for a workflow file."""
    resolved = _resolved(workflow_path)
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    return resolved.parent / ".symphony" / "run" / f"{digest}.json"


def lock_path_for(workflow_path: str | Path) -> Path:
    return record_path_for(workflow_path).with_suffix(".lock")


@contextlib.contextmanager
def acquire_service_lock(workflow_path: str | Path):
    """Acquire a per-workflow lock using atomic file creation."""
    path = lock_path_for(workflow_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ServiceLockError(
            f"service operation already in progress: {path}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
        yield path
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _record_to_json(record: ServiceRecord) -> dict[str, Any]:
    return {
        "workflow_path": str(record.workflow_path),
        "workflow_dir": str(record.workflow_dir),
        "host": record.host,
        "port": record.port,
        "orchestrator_pid": record.orchestrator_pid,
        "log_path": str(record.log_path),
        "started_at": record.started_at,
        "orchestrator_command": list(record.orchestrator_command),
        "service_instance_id": record.service_instance_id,
    }


def _record_from_json(data: dict[str, Any]) -> ServiceRecord:
    return ServiceRecord(
        workflow_path=Path(str(data["workflow_path"])),
        workflow_dir=Path(str(data["workflow_dir"])),
        host=str(data["host"]),
        port=int(data["port"]),
        orchestrator_pid=(
            int(data["orchestrator_pid"])
            if data.get("orchestrator_pid") is not None
            else None
        ),
        log_path=Path(str(data["log_path"])),
        started_at=str(data["started_at"]),
        orchestrator_command=[
            str(part) for part in data.get("orchestrator_command", [])
        ],
        service_instance_id=normalize_service_instance_id(
            data.get("service_instance_id")
        ),
    )


def load_record(workflow_path: str | Path) -> ServiceRecord | None:
    path = record_path_for(workflow_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return _record_from_json(data)
    except (TypeError, KeyError, ValueError):
        return None


def is_symphony_api_reachable(host: str, port: int) -> bool:
    """Return whether the recorded service port responds like Symphony."""
    payload = _probe_json(host, port, "/api/v1/state")
    return isinstance(payload, dict) and "health" in payload and "counts" in payload


def _payload_serves_workflow(payload: object, workflow_path: str | Path) -> bool:
    if not isinstance(payload, dict):
        return False
    served_workflow = payload.get("workflow_path")
    if not isinstance(served_workflow, str) or not served_workflow.strip():
        return False
    try:
        served_path = Path(served_workflow).expanduser()
        if not served_path.is_absolute():
            return False
        if served_path.resolve() != _resolved(workflow_path):
            return False
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def is_symphony_workflow_reachable(
    host: str,
    port: int,
    workflow_path: str | Path,
    *,
    service_instance_id: str | None = None,
) -> bool:
    """Return whether a port serves Symphony for the exact workflow."""
    payload = _probe_json(host, port, "/api/v1/health", service_instance_id)
    return _payload_serves_workflow(payload, workflow_path)


def probe_service_endpoint_identity(
    host: str,
    port: int,
    workflow_path: str | Path,
    service_instance_id: str,
) -> ServiceEndpointIdentity | None:
    """Return the serving PID only for an exact managed-service identity."""
    expected_instance_id = normalize_service_probe_credential(service_instance_id)
    if expected_instance_id is None:
        return None
    payload = _probe_json(host, port, "/api/v1/health", expected_instance_id)
    if not _payload_serves_workflow(payload, workflow_path):
        return None
    assert isinstance(payload, dict)
    served_instance_id = normalize_service_probe_credential(
        payload.get("service_instance_id")
    )
    if served_instance_id != expected_instance_id:
        return None
    served_pid = payload.get("orchestrator_pid")
    if (
        not isinstance(served_pid, int)
        or isinstance(served_pid, bool)
        or served_pid <= 0
    ):
        return None
    return ServiceEndpointIdentity(orchestrator_pid=served_pid)


def port_owner_hint(
    workflow_path: str | Path,
    port: int,
    *,
    is_running: ProcessRunningPredicate | None = None,
    is_api_reachable: ServiceApiProbe | None = None,
) -> str | None:
    record = load_record(workflow_path)
    if record is None or record.port != port:
        return None
    alive = is_running or is_process_running
    pid_alive = alive(record.orchestrator_pid)
    if pid_alive:
        api_alive = False
    elif is_api_reachable is None:
        probe_kwargs = (
            {"service_instance_id": record.service_instance_id}
            if record.service_instance_id is not None
            else {}
        )
        api_alive = is_symphony_workflow_reachable(
            record.host,
            record.port,
            _resolved(workflow_path),
            **probe_kwargs,
        )
    else:
        api_alive = is_api_reachable(record.host, record.port)
    if not pid_alive and not api_alive:
        return None
    pid = record.orchestrator_pid
    started = f", started {record.started_at}" if record.started_at else ""
    if api_alive:
        return (
            f"recorded Symphony API responds on this workflow's port, but saved "
            f"pid {pid} is stale{started}; check `symphony service status "
            f"{record.workflow_path}`"
        )
    return (
        f"owned by this workflow's service (pid {pid}{started}); "
        f"check `symphony service status {record.workflow_path}`"
    )


def save_record(record: ServiceRecord) -> Path:
    path = record_path_for(record.workflow_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(_record_to_json(record), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return path


def clear_record(workflow_path: str | Path) -> None:
    try:
        record_path_for(workflow_path).unlink()
    except FileNotFoundError:
        return


def _is_process_running_windows(pid: int) -> bool:
    # PROCESS_QUERY_LIMITED_INFORMATION keeps the handle read-only.  If that
    # right is denied, fall back to PROCESS_QUERY_INFORMATION for older hosts.
    # `ctypes.WinDLL` only exists on Windows; the `_IS_WIN32` gate at every
    # caller site keeps this branch dead on POSIX. Use getattr so Pyright on
    # macOS/Linux doesn't flag the literal `ctypes.WinDLL` attribute.
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return False
    kernel32 = win_dll("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    process_query_information = 0x0400
    still_active = 259

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        handle = kernel32.OpenProcess(process_query_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def is_process_running(pid: int | None) -> bool:
    """Return whether pid appears live, without raising for stale values."""
    try:
        parsed = int(pid) if pid is not None else 0
    except (TypeError, ValueError):
        return False
    if parsed <= 0:
        return False

    if _IS_WIN32:
        try:
            return _is_process_running_windows(parsed)
        except OSError:
            return False

    try:
        os.kill(parsed, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def service_status(
    workflow_path: str | Path,
    *,
    port: int | None = None,
    is_running: ProcessRunningPredicate | None = None,
    is_api_reachable: ServiceApiProbe | None = None,
) -> ServiceStatus:
    """Report persisted service state for a workflow.

    The saved workflow record wins over the requested port. A live recorded PID
    is trusted directly. When the PID is stale, the recorded endpoint must
    identify the exact requested workflow before callers treat it as running.
    """
    record = load_record(workflow_path)
    if record is None:
        return ServiceStatus(
            state="stopped",
            record=None,
            requested_port=port,
            recorded_port=None,
        )

    if is_running is None:
        is_running = is_process_running

    pid_running = is_running(record.orchestrator_pid)
    if pid_running:
        api_reachable = False
    elif is_api_reachable is None:
        probe_kwargs = (
            {"service_instance_id": record.service_instance_id}
            if record.service_instance_id is not None
            else {}
        )
        api_reachable = is_symphony_workflow_reachable(
            record.host,
            record.port,
            _resolved(workflow_path),
            **probe_kwargs,
        )
    else:
        api_reachable = is_api_reachable(record.host, record.port)
    state: ServiceState = "running" if pid_running or api_reachable else "stopped"
    return ServiceStatus(
        state=state,
        record=record,
        requested_port=port,
        recorded_port=record.port,
        pid_running=pid_running,
        api_reachable=api_reachable,
    )


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_orchestrator_command(
    workflow_path: str | Path,
    *,
    host: str,
    port: int,
) -> list[str]:
    """Build the shell-free command used to launch the orchestrator service."""
    workflow = _resolved(workflow_path)
    return [
        sys.executable,
        "-m",
        "symphony.cli",
        str(workflow),
        "--host",
        host,
        "--port",
        str(port),
    ]


def _popen_detached(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    env_overrides: Mapping[str, str] | None = None,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if env_overrides:
        env.update(env_overrides)
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if _IS_WIN32:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **kwargs)
    finally:
        log_handle.close()
    return int(proc.pid)


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float = 0.1,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def terminate_process(pid: int | None, *, force: bool = False) -> bool:
    """Best-effort process-tree termination for a service-managed PID."""
    try:
        parsed = int(pid) if pid is not None else 0
    except (TypeError, ValueError):
        return False
    if parsed <= 0 or not is_process_running(parsed):
        return False
    if _IS_WIN32:
        return _taskkill_tree(parsed, force=force)
    sig = _SIGKILL if force else signal.SIGTERM
    try:
        if _killpg is None:  # pragma: no cover - win32 returned above
            os.kill(parsed, sig)
            return True
        _killpg(parsed, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    except OSError:
        try:
            os.kill(parsed, sig)
        except ProcessLookupError:
            return False
        except OSError:
            return False
    return True


def _active_backend_pids(
    workflow_path: Path, *, owner_pid: int | None = None
) -> list[int]:
    registry_path = registry_path_for_workflow(workflow_path)
    if not registry_path.exists():
        return []
    registry = RunRegistry(registry_path)
    try:
        pids: list[int] = []
        seen: set[int] = set()
        records = list(registry.active_leases())
        if owner_pid is not None:
            records.extend(
                row
                for row in registry.recent_runs(limit=200)
                if row.owner_pid == owner_pid
            )
        for record in records:
            pid = record.backend_agent_pid
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            pids.append(pid)
        return pids
    except Exception as exc:
        print(
            f"warning: could not inspect active backend processes: {exc}",
            file=sys.stderr,
        )
        return []
    finally:
        registry.close()


def _owned_workspace_paths(workflow_path: Path, *, owner_pid: int | None) -> list[Path]:
    if owner_pid is None:
        return []
    registry_path = registry_path_for_workflow(workflow_path)
    if not registry_path.exists():
        return []
    registry = RunRegistry(registry_path)
    try:
        paths: list[Path] = []
        seen: set[str] = set()
        for row in registry.recent_runs(limit=200):
            if row.owner_pid != owner_pid:
                continue
            try:
                resolved = row.workspace_path.resolve(strict=False)
            except OSError:
                resolved = row.workspace_path
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            paths.append(resolved)
        return paths
    except Exception as exc:
        print(
            f"warning: could not inspect workflow workspace paths: {exc}",
            file=sys.stderr,
        )
        return []
    finally:
        registry.close()


# `ps -axo pid=,command=` on POSIX; on win32 the same `pid<space>command`
# line shape comes from PowerShell CIM (wmic is deprecated/removed on
# modern Windows). Null command lines render as an empty command column,
# which the shared substring match below simply never matches.
_WIN_PROCESS_ENUMERATOR = [
    "powershell",
    "-NoProfile",
    "-Command",
    "Get-CimInstance Win32_Process | "
    "ForEach-Object { '{0} {1}' -f $_.ProcessId, $_.CommandLine }",
]


def _workspace_needle_forms(path: str) -> tuple[str, ...]:
    """Normalized substring forms of one workspace path for process matching.

    Command lines record paths with either separator and, on Windows,
    case-insensitive casing; Git-Bash workers may also render a native
    ``D:\\a\\b`` path in MSYS form ``/d/a/b``. All forms are separator-
    normalized (``/``) and casefolded so the match survives every spelling.
    """
    normalized = path.replace("\\", "/").casefold()
    forms = {normalized}
    if len(normalized) > 1 and normalized[1] == ":":
        forms.add("/" + normalized[0] + normalized[2:])
    return tuple(forms)


def _workspace_bound_process_pids(workspace_paths: list[Path]) -> list[int]:
    if not workspace_paths:
        return []
    needles: list[tuple[str, ...]] = [
        _workspace_needle_forms(str(path))
        for path in workspace_paths
        if str(path)
    ]
    if not needles:
        return []
    enumerator = _WIN_PROCESS_ENUMERATOR if _IS_WIN32 else ["ps", "-axo", "pid=,command="]
    try:
        completed = subprocess.run(
            enumerator,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(
            f"warning: could not inspect workspace-bound processes: {exc}",
            file=sys.stderr,
        )
        return []
    pids: list[int] = []
    current_pid = os.getpid()
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        normalized_command = command.replace("\\", "/").casefold()
        if any(any(form in normalized_command for form in forms) for forms in needles):
            pids.append(pid)
    return pids


def _terminate_active_backend_processes(record: ServiceRecord) -> bool:
    all_stopped = True
    pids: list[int] = []
    seen: set[int] = set()
    for pid in _active_backend_pids(
        record.workflow_path, owner_pid=record.orchestrator_pid
    ):
        if pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    for pid in _workspace_bound_process_pids(
        _owned_workspace_paths(
            record.workflow_path,
            owner_pid=record.orchestrator_pid,
        )
    ):
        if pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
    for pid in pids:
        if not is_process_running(pid):
            continue
        terminate_process(pid, force=True)
        stopped = _wait_until(
            lambda pid=pid: not is_process_running(pid),
            timeout_s=2.0,
        )
        if not stopped:
            all_stopped = False
            print(f"warning: backend agent pid={pid} is still running", file=sys.stderr)
    return all_stopped


def _run_doctor_or_print(cfg: Any, *, host: str, port: int) -> bool:
    from dataclasses import replace

    from .cli.doctor import format_results, run_checks

    checked_cfg = replace(cfg, server=ServerConfig(port=port))
    results = run_checks(checked_cfg, host=host)
    print(format_results(results, color=False))
    return not any(result.status == "fail" for result in results)


def _load_cfg(workflow_path: Path) -> Any:
    return build_service_config(load_workflow(workflow_path))


def _resolve_port(raw_port: int | None, cfg: Any) -> int:
    if raw_port is not None:
        return int(raw_port)
    if cfg.server.port is not None:
        return int(cfg.server.port)
    return DEFAULT_SERVICE_PORT


def _start(args: argparse.Namespace) -> int:
    workflow = resolve_workflow_path(args.workflow)
    if not workflow.exists():
        print(f"FAIL workflow file not found: {workflow}", file=sys.stderr)
        return 2
    try:
        cfg = _load_cfg(workflow)
    except SymphonyError as exc:
        print(f"FAIL workflow load failed: {exc}", file=sys.stderr)
        return 2
    try:
        ensure_workflow_repo_is_safe(workflow)
    except SymphonyError as exc:
        print(f"FAIL unsafe workflow repository: {exc}", file=sys.stderr)
        return 1

    try:
        with acquire_service_lock(workflow):
            return _start_locked(args, workflow=workflow, cfg=cfg)
    except ServiceLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _start_locked(args: argparse.Namespace, *, workflow: Path, cfg: Any) -> int:
    port = _resolve_port(args.port, cfg)
    current = service_status(workflow, port=port)
    if current.state == "running" and current.record is not None:
        if args.replace:
            stop_rc = _stop(
                argparse.Namespace(
                    workflow=args.workflow,
                    timeout=10.0,
                    force=True,
                )
            )
            if stop_rc != 0:
                return stop_rc
        else:
            print(
                "already running "
                f"pid={current.record.orchestrator_pid} "
                f"port={current.record.port} "
                f"workflow={current.record.workflow_path}"
            )
            if current.record.port != port:
                print(
                    f"requested port {port} ignored; this workflow is already "
                    f"managed on port {current.record.port}"
                )
            return 0
    elif current.record is not None:
        stop_rc = _stop(
            argparse.Namespace(
                workflow=args.workflow,
                timeout=2.0,
                force=True,
            )
        )
        if stop_rc != 0:
            return stop_rc

    if not args.skip_doctor and not _run_doctor_or_print(
        cfg, host=args.host, port=port
    ):
        print("service start aborted: doctor reported FAIL", file=sys.stderr)
        return 1

    workflow_dir = workflow.parent
    log_path = workflow_dir / "log" / "symphony.log"
    orchestrator_command = build_orchestrator_command(
        workflow,
        host=args.host,
        port=port,
    )
    orchestrator_pid: int | None = None
    service_instance_id = secrets.token_urlsafe(32)
    try:
        orchestrator_pid = _popen_detached(
            orchestrator_command,
            cwd=workflow_dir,
            log_path=log_path,
            env_overrides={SERVICE_INSTANCE_ENV: service_instance_id},
        )
        if not _wait_until(lambda: is_process_running(orchestrator_pid), timeout_s=2.0):
            print(
                f"service start failed: orchestrator exited early; see {log_path}",
                file=sys.stderr,
            )
            return 1
    except OSError as exc:
        if orchestrator_pid is not None:
            terminate_process(orchestrator_pid, force=True)
        print(f"service start failed: {exc}", file=sys.stderr)
        return 1

    record = ServiceRecord(
        workflow_path=workflow.resolve(),
        workflow_dir=workflow_dir.resolve(),
        host=args.host,
        port=port,
        orchestrator_pid=orchestrator_pid,
        log_path=log_path.resolve(),
        started_at=_utc_now(),
        orchestrator_command=orchestrator_command,
        service_instance_id=service_instance_id,
    )
    try:
        save_record(record)
    except Exception as exc:
        if orchestrator_pid is not None:
            terminate_process(orchestrator_pid, force=True)
        print(f"failed to save service record: {exc}", file=sys.stderr)
        return 1

    print(
        f"started symphony service pid={orchestrator_pid} "
        f"url=http://{args.host}:{port}/"
    )
    return 0


def _stop(args: argparse.Namespace) -> int:
    workflow = resolve_workflow_path(args.workflow)
    record = load_record(workflow)
    if record is None:
        print(f"stopped workflow={workflow} (no service record)")
        return 0

    all_stopped = True
    for label, pid in (("orchestrator", record.orchestrator_pid),):
        if _IS_WIN32 and not args.force and record.service_instance_id is not None:
            if is_process_running(pid):
                terminate_process(pid)
                _wait_until(
                    lambda pid=pid: not is_process_running(pid),
                    timeout_s=float(args.timeout),
                )
            identity = probe_service_endpoint_identity(
                record.host,
                record.port,
                record.workflow_path,
                record.service_instance_id,
            )
            if identity is None:
                stopped = False
            else:
                serving_pid = identity.orchestrator_pid
                if is_process_running(serving_pid):
                    terminate_process(serving_pid, force=True)
                stopped = _wait_until(
                    lambda serving_pid=serving_pid, pid=pid: (
                        not is_process_running(serving_pid)
                        and not is_process_running(pid)
                    ),
                    timeout_s=2.0,
                )
            if not stopped:
                all_stopped = False
                print(f"warning: {label} pid={pid} is still running", file=sys.stderr)
            continue
        if not is_process_running(pid):
            continue
        terminate_process(pid)
        stopped = _wait_until(
            lambda pid=pid: not is_process_running(pid),
            timeout_s=float(args.timeout),
        )
        if not stopped and args.force and pid is not None:
            terminate_process(pid, force=True)
            stopped = _wait_until(
                lambda pid=pid: not is_process_running(pid),
                timeout_s=2.0,
            )
        if not stopped:
            all_stopped = False
            print(f"warning: {label} pid={pid} is still running", file=sys.stderr)

    if args.force and not _terminate_active_backend_processes(record):
        all_stopped = False

    if not all_stopped:
        print(
            f"service record kept because workflow is still running: {record.workflow_path}",
            file=sys.stderr,
        )
        return 1

    clear_record(workflow)
    print(f"stopped workflow={record.workflow_path}")
    return 0


def _status(args: argparse.Namespace) -> int:
    workflow = resolve_workflow_path(args.workflow)
    port = int(args.port) if args.port is not None else None
    status = service_status(workflow, port=port)
    if status.state == "stopped":
        if status.record is None:
            print(f"stopped workflow={workflow}")
        else:
            print(
                f"stopped workflow={status.record.workflow_path} "
                f"(stale pid={status.record.orchestrator_pid})"
            )
        return 0

    assert status.record is not None
    record = status.record
    if status.pid_running:
        print(
            f"running workflow={record.workflow_path} "
            f"pid={record.orchestrator_pid} port={record.port} "
            f"url=http://{record.host}:{record.port}/"
        )
    else:
        print(
            f"running workflow={record.workflow_path} "
            f"stale pid={record.orchestrator_pid} port={record.port} "
            f"url=http://{record.host}:{record.port}/ (api alive)"
        )
    if port is not None and record.port != port:
        print(
            f"requested port {port}; existing service for this workflow uses "
            f"{record.port}"
        )
    return 0


def _restart(args: argparse.Namespace) -> int:
    stop_args = argparse.Namespace(
        workflow=args.workflow,
        timeout=args.timeout,
        force=args.force,
    )
    stop_rc = _stop(stop_args)
    if stop_rc != 0:
        return stop_rc
    start_args = argparse.Namespace(
        workflow=args.workflow,
        host=args.host,
        port=args.port,
        replace=False,
        skip_doctor=args.skip_doctor,
    )
    return _start(start_args)


def _logs(args: argparse.Namespace) -> int:
    workflow = resolve_workflow_path(args.workflow)
    record = load_record(workflow)
    if record is None:
        print(f"no service record for {workflow}", file=sys.stderr)
        return 1
    path = record.log_path
    if path is None or not path.exists():
        print(f"log file not found: {path}", file=sys.stderr)
        return 1
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-int(args.lines) :]:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony service",
        description="Manage a background Symphony service for one WORKFLOW.md.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_workflow(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "workflow",
            nargs="?",
            default=None,
            help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
        )

    p_start = sub.add_parser(
        "start", help="start the orchestrator (admin UI serves on --port)"
    )
    add_workflow(p_start)
    p_start.add_argument("--host", default="127.0.0.1")
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("--replace", action="store_true")
    p_start.add_argument("--skip-doctor", action="store_true")
    p_start.set_defaults(func=_start)

    p_stop = sub.add_parser("stop", help="stop a managed service")
    add_workflow(p_stop)
    p_stop.add_argument("--timeout", type=float, default=10.0)
    p_stop.add_argument("--force", action="store_true")
    p_stop.set_defaults(func=_stop)

    p_restart = sub.add_parser("restart", help="stop then start a service")
    add_workflow(p_restart)
    p_restart.add_argument("--host", default="127.0.0.1")
    p_restart.add_argument("--port", type=int, default=None)
    p_restart.add_argument("--skip-doctor", action="store_true")
    p_restart.add_argument("--timeout", type=float, default=10.0)
    p_restart.add_argument("--force", action="store_true")
    p_restart.set_defaults(func=_restart)

    p_status = sub.add_parser("status", help="show managed service status")
    add_workflow(p_status)
    p_status.add_argument("--port", type=int, default=None)
    p_status.set_defaults(func=_status)

    p_logs = sub.add_parser("logs", help="print recent service logs")
    add_workflow(p_logs)
    p_logs.add_argument("--lines", type=int, default=80)
    p_logs.set_defaults(func=_logs)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
