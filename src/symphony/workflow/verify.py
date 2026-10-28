"""Opt-in deterministic lane gates, independent of worker-authored evidence."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Iterator

from .._shell import resolve_bash
from ..errors import ConfigValidationError
from ..utils.atomic_json import write_json_atomic
from .coercion import _normalize_state_key

if TYPE_CHECKING:
    from .config import ServiceConfig


DEFAULT_VERIFY_TIMEOUT_MS = 600_000
_ACTIVE_GATES: dict[tuple[int, str], int] = {}


def verification_in_progress(owner: object, issue_id: str) -> bool:
    """Host verification is bounded by its own timeout, not agent silence."""
    return (id(owner), issue_id) in _ACTIVE_GATES


@contextmanager
def verification_scope(owner: object, issue_id: str) -> Iterator[None]:
    key = (id(owner), issue_id)
    _ACTIVE_GATES[key] = _ACTIVE_GATES.get(key, 0) + 1
    try:
        yield
    finally:
        remaining = _ACTIVE_GATES[key] - 1
        if remaining:
            _ACTIVE_GATES[key] = remaining
        else:
            del _ACTIVE_GATES[key]


@dataclass(frozen=True)
class VerifyConfig:
    timeout_ms: int = DEFAULT_VERIFY_TIMEOUT_MS
    commands: dict[str, str] = field(default_factory=dict)


def verify_config(cfg: ServiceConfig) -> VerifyConfig:
    """Validate only our block; the shared builder is owned separately."""
    if "verify" not in cfg.raw:
        return VerifyConfig()
    raw = cfg.raw["verify"]
    if not isinstance(raw, dict):
        raise ConfigValidationError("verify must be a mapping")
    for key in raw:
        if key not in {"timeout_ms", "commands"}:
            raise ConfigValidationError(f"verify.{key}: unknown key")
    timeout = raw.get("timeout_ms", DEFAULT_VERIFY_TIMEOUT_MS)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ConfigValidationError("verify.timeout_ms must be a positive integer")
    commands = raw.get("commands", {})
    if not isinstance(commands, dict):
        raise ConfigValidationError("verify.commands must be a mapping")
    normalized: dict[str, str] = {}
    for state, command in commands.items():
        path = f"verify.commands.{state}"
        key = _normalize_state_key(state) if isinstance(state, str) else ""
        if not key:
            raise ConfigValidationError(f"{path}: state must be a non-empty string")
        if not isinstance(command, str) or not command.strip():
            raise ConfigValidationError(f"{path}: command must be a non-empty string")
        if key in normalized:
            raise ConfigValidationError(f"{path}: duplicate normalized state {key!r}")
        normalized[key] = command
    return VerifyConfig(timeout_ms=timeout, commands=normalized)


def crossed_commands(
    commands: dict[str, str],
    producing_state: str,
    current_state: str,
    active_states: tuple[str, ...],
) -> list[tuple[str, str]]:
    """Commands of every lane a forward move leaves or jumps over, in lane order.

    A worker that moves a ticket from In Progress straight to Done must still
    pass the Verify lane's command; otherwise skipping a lane skips its gate.
    A target outside the active lanes (Done) crosses every later lane.
    """
    order = [_normalize_state_key(state) for state in active_states]
    producing = _normalize_state_key(producing_state)
    if producing not in order:
        command = commands.get(producing)
        return [(producing, command)] if command else []
    current = _normalize_state_key(current_state)
    end = order.index(current) if current in order else len(order)
    return [
        (lane, commands[lane])
        for lane in order[order.index(producing):end]
        if lane in commands
    ]


def gates_from(
    commands: dict[str, str], producing_state: str, active_states: tuple[str, ...]
) -> bool:
    """Whether a forward move out of *producing_state* can cross any gate."""
    return bool(crossed_commands(commands, producing_state, "", active_states))


_GATE_LANES_LOCK = threading.Lock()


def _gate_lanes_path(cfg: ServiceConfig) -> Path:
    return cfg.workflow_path.parent / ".symphony" / "verify-pending.json"


def _read_gate_lanes(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, str)}


def gate_lane_of(cfg: ServiceConfig, identifier: str) -> str | None:
    """The gated lane the ticket's last unfinished turn started in, if any."""
    return _read_gate_lanes(_gate_lanes_path(cfg)).get(identifier)


def remember_gate_lane(cfg: ServiceConfig, identifier: str, lane: str | None) -> None:
    """Persist (or with ``None`` forget) the gated lane a turn starts in.

    A forward move whose exit is never handled (service shutdown, host crash)
    is gated from this lane on the next dispatch instead of being lost.
    """
    path = _gate_lanes_path(cfg)
    with _GATE_LANES_LOCK:
        lanes = _read_gate_lanes(path)
        if lanes.get(identifier) == lane:
            return
        if lane is None:
            lanes.pop(identifier, None)
        else:
            lanes[identifier] = lane
        write_json_atomic(path, lanes)


@dataclass(frozen=True)
class VerifyResult:
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    output: str

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    def failure_note(self, command: str, timeout_ms: int) -> str:
        # Keep multiline shell programs on one metadata line for needs readers.
        display = json.dumps(command, ensure_ascii=False) if "\n" in command else f"`{command}`"
        status = (
            f"timed out after {timeout_ms / 1000:g} s"
            if self.timed_out
            else f"Exit code: {self.exit_code}"
        )
        # Indentation keeps shell output from creating fake ticket sections.
        tail = "\n".join(f"    {line}" for line in self.output.splitlines())
        return (
            f"Command: {display}\n\n{status}\n\n"
            f"Duration: {self.duration_ms} ms\n\nOutput (last 60 lines):\n\n{tail}"
        )


def run_verify(
    command: str,
    workspace_path: Path,
    timeout_ms: int,
    env: dict[str, str] | None = None,
) -> VerifyResult:
    """Blocking runner; callers must put this on a worker thread.

    Spool combined output to disk so noisy commands cannot exhaust memory.
    A new process group lets a timeout also kill grandchildren holding files.
    """
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                [resolve_bash(), "-c", command],
                cwd=workspace_path,
                env={**os.environ, **(env or {})},
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
            )
        except OSError as exc:
            return VerifyResult(127, False, int((time.monotonic() - started) * 1000), str(exc))
        try:
            process.wait(timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
        duration = int((time.monotonic() - started) * 1000)
        output.seek(0)
        tail = b"".join(deque(output, maxlen=60)).decode("utf-8", errors="replace")
    return VerifyResult(process.returncode, timed_out, duration, tail)


def append_verify_trace(
    cfg: ServiceConfig,
    identifier: str,
    from_state: str,
    to_state: str,
    command: str,
    result: VerifyResult,
) -> None:
    directory = cfg.workflow_path.parent / ".symphony" / "traces"
    directory.mkdir(parents=True, exist_ok=True)
    # Remote trackers may supply identifiers containing path separators.
    filename = identifier.replace("/", "_").replace("\\", "_")
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "issue": identifier,
        "from": from_state,
        "to": to_state,
        "command": command,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "duration_ms": result.duration_ms,
        "passed": result.passed,
    }
    with (directory / f"{filename}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
