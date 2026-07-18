#!/usr/bin/env python3
"""Evaluator-owned installed-artifact CLI/API lifecycle proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, TextIO


IGNORED_COPY_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".symphony",
    "__pycache__",
    "build",
    "dist",
    "kanban",
    "log",
}
PORT_PATTERN = re.compile(r"board ready at http://127\.0\.0\.1:(\d+)")


class HarnessFailure(RuntimeError):
    """A black-box lifecycle assertion failed."""


def _ignore_copy(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_COPY_NAMES
        or name.endswith((".egg-info", ".pyc"))
        or name in {".coverage", "WORKFLOW-PROGRESS.md"}
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ticket_manifest(board: Path) -> list[dict[str, str]]:
    tickets = [path for path in board.rglob("*.md") if path.is_file()]
    return [
        {"path": path.relative_to(board).as_posix(), "sha256": _sha256(path)}
        for path in sorted(tickets, key=lambda item: item.relative_to(board).as_posix())
    ]


def _manifest_digest(manifest: list[dict[str, str]]) -> str:
    encoded = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _recorded_run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")
    result = {
        "argv": argv,
        "cwd": str(cwd),
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": completed.returncode,
        "log": log_path.name,
        "timeout_seconds": timeout,
    }
    if completed.returncode != 0:
        raise HarnessFailure(
            f"command exited {completed.returncode}: {' '.join(argv)}; log={log_path}"
        )
    return result


def _build_wheel(
    source: Path, temp_root: Path, evidence_dir: Path, evidence: dict[str, Any]
) -> tuple[Path, Path, Path]:
    copied = temp_root / "source"
    shutil.copytree(source, copied, symlinks=True, ignore=_ignore_copy)
    cache = temp_root / "uv-cache"
    dist = temp_root / "dist"
    evidence["commands"]["build"] = _recorded_run(
        [
            "uv",
            "build",
            "--cache-dir",
            str(cache),
            "--out-dir",
            str(dist),
            "--no-create-gitignore",
        ],
        cwd=copied,
        timeout=300,
        log_path=evidence_dir / "runtime-build.txt",
    )
    build_log = (evidence_dir / "runtime-build.txt").read_text(encoding="utf-8")
    if "SetuptoolsDeprecationWarning" in build_log:
        raise HarnessFailure("build emitted SetuptoolsDeprecationWarning")
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise HarnessFailure(f"expected one wheel, got {[path.name for path in wheels]}")
    return copied, cache, wheels[0]


def _install_wheel(
    wheel: Path,
    cache: Path,
    temp_root: Path,
    evidence_dir: Path,
    evidence: dict[str, Any],
) -> Path:
    venv = temp_root / "venv"
    evidence["commands"]["venv"] = _recorded_run(
        [sys.executable, "-m", "venv", str(venv)],
        cwd=temp_root,
        timeout=120,
        log_path=evidence_dir / "runtime-venv.txt",
    )
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    symphony = venv / ("Scripts/symphony.exe" if os.name == "nt" else "bin/symphony")
    evidence["commands"]["install"] = _recorded_run(
        [
            "uv",
            "pip",
            "install",
            "--cache-dir",
            str(cache),
            "--python",
            str(python),
            str(wheel),
        ],
        cwd=temp_root,
        timeout=300,
        log_path=evidence_dir / "runtime-install.txt",
    )
    return symphony


def _build_and_install(
    source: Path, temp_root: Path, evidence_dir: Path, evidence: dict[str, Any]
) -> tuple[Path, Path]:
    copied, cache, wheel = _build_wheel(source, temp_root, evidence_dir, evidence)
    symphony = _install_wheel(wheel, cache, temp_root, evidence_dir, evidence)
    evidence["artifact"] = {
        "wheel": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "installed_cli": str(symphony),
    }
    return copied, symphony


def _workflow_text(board: Path, workspaces: Path, installed_python: Path) -> str:
    command = f"{installed_python} -m symphony.mock_codex app-server"
    return f"""---
tracker:
  kind: file
  board_root: {json.dumps(str(board))}
  active_states: [Todo, "In Progress", Verify, Learn]
  terminal_states: ["Human Review", Done, Blocked, Archive]
  archive_state: Archive
  archive_after_days: 0
  state_descriptions:
    Todo: "Ready"
    "In Progress": "Working"
    Verify: "Checking"
    Learn: "Learning"
    "Human Review": "Waiting for a human"
    Done: "Complete"
    Blocked: "Blocked"
    Archive: "Archived"
polling:
  interval_ms: 60000
workspace:
  root: {json.dumps(str(workspaces))}
hooks:
  after_create: ": noop"
agent:
  kind: codex
  max_concurrent_agents: 1
codex:
  command: {json.dumps(command)}
server:
  port: 0
progress:
  enabled: false
system:
  keep_awake: false
---

Disposable QA prompt for {{{{ issue.identifier }}}}.
"""


def _http_json(base_url: str, path: str) -> tuple[int, Any]:
    request = urllib.request.Request(base_url + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else None


def _wait_for_port(proc: subprocess.Popen[str], log_path: Path, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        match = PORT_PATTERN.search(text)
        if match:
            return int(match.group(1))
        code = proc.poll()
        if code is not None:
            raise HarnessFailure(f"runtime exited before publishing port: {code}")
        time.sleep(0.1)
    raise HarnessFailure("runtime did not publish an ephemeral port before timeout")


def _wait_for_exact_health(
    proc: subprocess.Popen[str], base_url: str, timeout: float
) -> tuple[dict[str, Any], list[str]]:
    deadline = time.monotonic() + timeout
    observations: list[str] = []
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HarnessFailure("runtime exited before health became exactly ok")
        try:
            status, payload = _http_json(base_url, "/api/v1/health")
            health = payload.get("status") if isinstance(payload, dict) else None
            observations.append(f"http={status},status={health}")
            if status == 200 and health == "ok":
                return payload, observations
        except (OSError, ValueError, urllib.error.URLError) as exc:
            observations.append(type(exc).__name__)
        time.sleep(0.2)
    raise HarnessFailure(f"health was not exactly ok: {observations[-10:]}")


def _prefixed_identifiers(value: Any, prefix: str) -> list[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        identifier = value.get("identifier")
        if isinstance(identifier, str) and identifier.startswith(prefix):
            found.add(identifier)
        for child in value.values():
            found.update(_prefixed_identifiers(child, prefix))
    elif isinstance(value, list):
        for child in value:
            found.update(_prefixed_identifiers(child, prefix))
    return sorted(found)


def _assert_smoke_deleted(board: Path, base_url: str, prefix: str) -> dict[str, Any]:
    status, payload = _http_json(base_url, "/api/v1/board")
    if status != 200:
        raise HarnessFailure(f"board cleanup probe returned HTTP {status}")
    api_cards = _prefixed_identifiers(payload, prefix)
    disk_cards = sorted(
        path.relative_to(board).as_posix()
        for path in board.rglob("*.md")
        if path.is_file() and prefix in path.read_text(encoding="utf-8", errors="replace")
    )
    if api_cards or disk_cards:
        raise HarnessFailure(
            f"smoke cards remain after script: api={api_cards}, disk={disk_cards}"
        )
    return {
        "api_prefixed_identifiers": api_cards,
        "disk_prefixed_ticket_paths": disk_cards,
    }


def _require_port_closed(port: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                time.sleep(0.1)
        except OSError:
            return {"closed": True, "attempts": attempts, "port": port}
    raise HarnessFailure(f"port {port} remained open after shutdown")


def _registry_counts(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise HarnessFailure(f"run registry missing: {path}")
    with sqlite3.connect(path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY status"
            ).fetchall()
        )
        active = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE status = 'active'"
        ).fetchone()[0]
        lease_holding = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('active', 'reclaiming')"
        ).fetchone()[0]
    if active != 0 or lease_holding != 0:
        raise HarnessFailure(
            f"registry retained active runs: active={active}, lease_holding={lease_holding}"
        )
    return {
        "active": active,
        "lease_holding": lease_holding,
        "statuses": statuses,
    }


def _terminate_process(proc: subprocess.Popen[str], timeout: float) -> tuple[int, str]:
    proc.send_signal(signal.SIGTERM)
    try:
        return proc.wait(timeout=timeout), "SIGTERM"
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        raise HarnessFailure("runtime did not exit within SIGTERM timeout")


def _cleanup_process(proc: subprocess.Popen[str] | None) -> str:
    if proc is None or proc.poll() is not None:
        return "not-running"
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
        return "terminated"
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return "cleanup-failed"
        return "killed"


def _create_runtime_fixture(
    temp_root: Path, installed_python: Path, evidence: dict[str, Any]
) -> tuple[Path, Path, Path]:
    runtime_root = temp_root / "runtime"
    board = runtime_root / "board"
    workspaces = runtime_root / "workspaces"
    board.mkdir(parents=True)
    workspaces.mkdir()
    workflow = runtime_root / "WORKFLOW.md"
    workflow.write_text(
        _workflow_text(board, workspaces, installed_python), encoding="utf-8"
    )
    evidence["workflow"] = {
        "path": str(workflow),
        "server_port": 0,
        "progress_enabled": False,
        "keep_awake": False,
        "board": str(board),
        "workspace": str(workspaces),
    }
    return runtime_root, board, workflow


def _run_doctor(
    symphony: Path,
    workflow: Path,
    runtime_root: Path,
    warnings_env: dict[str, str],
    evidence_dir: Path,
    evidence: dict[str, Any],
) -> None:
    evidence["commands"]["doctor"] = _recorded_run(
        [str(symphony), "doctor", str(workflow), "--no-color"],
        cwd=runtime_root,
        timeout=60,
        log_path=evidence_dir / "runtime-doctor.txt",
        env=warnings_env,
    )


def _launch_runtime(
    symphony: Path,
    workflow: Path,
    runtime_root: Path,
    warnings_env: dict[str, str],
    runtime_log: Path,
    log_handle: TextIO,
    evidence: dict[str, Any],
) -> tuple[subprocess.Popen[str], float]:
    run_argv = [str(symphony), str(workflow), "--log-level", "INFO"]
    started = time.monotonic()
    proc = subprocess.Popen(
        run_argv,
        cwd=runtime_root,
        env=warnings_env,
        text=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    evidence["commands"]["runtime"] = {
        "argv": run_argv,
        "cwd": str(runtime_root),
        "log": runtime_log.name,
        "startup_timeout_seconds": 30,
        "shutdown_timeout_seconds": 30,
    }
    return proc, started


def _record_runtime_readiness(
    proc: subprocess.Popen[str], runtime_log: Path, evidence: dict[str, Any]
) -> tuple[int, str]:
    port = _wait_for_port(proc, runtime_log, 30)
    if port <= 0:
        raise HarnessFailure(f"invalid ephemeral port parsed: {port}")
    base_url = f"http://127.0.0.1:{port}"
    health, observations = _wait_for_exact_health(proc, base_url, 30)
    evidence["runtime"] = {
        "base_url": base_url,
        "ephemeral_port": port,
        "health": health,
        "health_observations": observations,
        "warnings_mode": "error",
    }
    return port, base_url


def _run_smoke_scenario(
    copied: Path,
    installed_python: Path,
    runtime_root: Path,
    board: Path,
    base_url: str,
    warnings_env: dict[str, str],
    evidence_dir: Path,
    evidence: dict[str, Any],
) -> None:
    prefix = "QAPH" + hashlib.sha256(
        f"{time.time_ns()}-{os.getpid()}".encode()
    ).hexdigest()[:8].upper()
    smoke_script = copied / "scripts" / "smoke_web_api.py"
    evidence["commands"]["smoke"] = _recorded_run(
        [
            str(installed_python),
            str(smoke_script),
            "--base-url",
            base_url,
            "--prefix",
            prefix,
        ],
        cwd=runtime_root,
        timeout=60,
        log_path=evidence_dir / "runtime-smoke.txt",
        env=warnings_env,
    )
    evidence["smoke"] = {
        "prefix": prefix,
        "external_cleanup": _assert_smoke_deleted(board, base_url, prefix),
    }


def _verify_runtime_shutdown(
    proc: subprocess.Popen[str],
    log_handle: TextIO,
    runtime_log: Path,
    runtime_root: Path,
    port: int,
    started: float,
    evidence: dict[str, Any],
) -> None:
    exit_code, sent = _terminate_process(proc, 30)
    log_handle.flush()
    if exit_code != 0:
        raise HarnessFailure(f"runtime exited {exit_code} after {sent}")
    log_text = runtime_log.read_text(encoding="utf-8")
    if "shutdown_complete" not in log_text:
        raise HarnessFailure("runtime log does not contain shutdown_complete")
    evidence["shutdown"] = {
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": exit_code,
        "signal": sent,
        "shutdown_complete": True,
        "socket": _require_port_closed(port, 5),
    }
    evidence["registry"] = _registry_counts(runtime_root / ".symphony" / "state.db")


def _run_lifecycle(
    source: Path, temp_root: Path, evidence_dir: Path, evidence: dict[str, Any]
) -> None:
    copied, symphony = _build_and_install(source, temp_root, evidence_dir, evidence)
    installed_python = symphony.parent / ("python.exe" if os.name == "nt" else "python")
    runtime_root, board, workflow = _create_runtime_fixture(
        temp_root, installed_python, evidence
    )

    warnings_env = os.environ.copy()
    warnings_env["PYTHONWARNINGS"] = "error"
    warnings_env["PYTHONDONTWRITEBYTECODE"] = "1"
    _run_doctor(
        symphony, workflow, runtime_root, warnings_env, evidence_dir, evidence
    )

    runtime_log = evidence_dir / "runtime-server.txt"
    proc: subprocess.Popen[str] | None = None
    log_handle = runtime_log.open("w", encoding="utf-8", buffering=1)
    try:
        proc, started = _launch_runtime(
            symphony,
            workflow,
            runtime_root,
            warnings_env,
            runtime_log,
            log_handle,
            evidence,
        )
        port, base_url = _record_runtime_readiness(proc, runtime_log, evidence)
        _run_smoke_scenario(
            copied,
            installed_python,
            runtime_root,
            board,
            base_url,
            warnings_env,
            evidence_dir,
            evidence,
        )
        _verify_runtime_shutdown(
            proc, log_handle, runtime_log, runtime_root, port, started, evidence
        )
        proc = None
    finally:
        evidence["emergency_process_cleanup"] = _cleanup_process(proc)
        log_handle.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--evidence-dir", type=Path, required=True)
    return parser.parse_args()


def _capture_original_board(
    source: Path, evidence_dir: Path
) -> tuple[Path, Path, list[dict[str, str]]]:
    linked_board = source / "kanban"
    if not linked_board.is_symlink():
        raise HarnessFailure(f"expected linked original board: {linked_board}")
    board_target = linked_board.resolve(strict=True)
    before = _ticket_manifest(board_target)
    _write_json(evidence_dir / "original-board-before.json", before)
    return linked_board, board_target, before


def _finalize_evidence(
    board_target: Path,
    before: list[dict[str, str]],
    temp_root: Path,
    evidence_dir: Path,
    evidence: dict[str, Any],
    exit_code: int,
) -> int:
    after = _ticket_manifest(board_target)
    _write_json(evidence_dir / "original-board-after.json", after)
    manifest_equal = before == after
    evidence["original_linked_board"].update(
        {
            "ticket_count_after": len(after),
            "manifest_digest_after": _manifest_digest(after),
            "exact_manifest_equal": manifest_equal,
        }
    )
    if not manifest_equal:
        evidence["manifest_error"] = "linked board additions/removals/content drifted"
        exit_code = 1
    shutil.rmtree(temp_root, ignore_errors=False)
    evidence["teardown"] = {
        "temp_root_removed": not temp_root.exists(),
        "runtime_process": evidence.get("emergency_process_cleanup", "not-started"),
    }
    if temp_root.exists():
        evidence["teardown_error"] = "evaluator temp root remains"
        exit_code = 1
    evidence["observation"] = "completed" if exit_code == 0 else "failed"
    _write_json(evidence_dir / "runtime-lifecycle.json", evidence)
    return exit_code


def main() -> int:
    args = _parse_args()
    source = args.source.resolve()
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    linked_board, board_target, before = _capture_original_board(source, evidence_dir)
    temp_root = Path(
        tempfile.mkdtemp(prefix="symphony-runtime-qa-", dir="/private/tmp")
    )
    evidence: dict[str, Any] = {
        "commands": {},
        "original_linked_board": {
            "link": str(linked_board),
            "target": str(board_target),
            "ticket_count_before": len(before),
            "manifest_digest_before": _manifest_digest(before),
        },
        "temp_root": str(temp_root),
    }
    exit_code = 0
    try:
        _run_lifecycle(source, temp_root, evidence_dir, evidence)
    except Exception as exc:  # evidence is required even for a failed scenario
        evidence["error"] = {"type": type(exc).__name__, "message": str(exc)}
        exit_code = 1
    finally:
        exit_code = _finalize_evidence(
            board_target, before, temp_root, evidence_dir, evidence, exit_code
        )
    print(json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
