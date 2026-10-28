from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shlex
import sys
import time

import pytest

from symphony.errors import ConfigValidationError
from symphony.workflow import build_service_config, parse_workflow_text
from symphony.workflow.verify import VerifyConfig, run_verify, verify_config


def _cfg(raw):
    workflow = parse_workflow_text("---\ntracker:\n  kind: file\n---\n", Path("/tmp/WORKFLOW.md"))
    return replace(build_service_config(workflow), raw=raw)


def test_absent_verify_is_disabled():
    assert verify_config(_cfg({})) == VerifyConfig()


def test_verify_config_normalizes_states():
    config = verify_config(_cfg({"verify": {"commands": {" In Progress ": "pytest -q"}}}))
    assert config.commands == {"in progress": "pytest -q"}
    assert config.timeout_ms == 600_000


@pytest.mark.parametrize(("block", "key"), [
    (None, "verify"),
    ({"other": 1}, "verify.other"),
    ({"timeout_ms": True}, "verify.timeout_ms"),
    ({"timeout_ms": 0}, "verify.timeout_ms"),
    ({"timeout_ms": "10"}, "verify.timeout_ms"),
    ({"commands": []}, "verify.commands"),
    ({"commands": {"Build": False}}, "verify.commands.Build"),
    ({"commands": {"Build": " "}}, "verify.commands.Build"),
    ({"commands": {2: "true"}}, "verify.commands.2"),
    ({"commands": {"": "true"}}, "verify.commands."),
    ({"commands": {"Build": "true", " build ": "false"}}, "verify.commands."),
])
def test_invalid_verify_config_names_key(block, key):
    with pytest.raises(ConfigValidationError, match=key.replace(".", r"\.")):
        verify_config(_cfg({"verify": block}))


def test_run_verify_combines_output_and_keeps_last_sixty_lines(tmp_path: Path):
    result = run_verify("for n in {1..65}; do echo line$n; done; echo failure >&2; exit 7", tmp_path, 1000)
    assert not result.passed
    assert result.exit_code == 7
    assert not result.timed_out
    assert len(result.output.splitlines()) == 60
    assert result.output.splitlines()[0] == "line7"
    assert result.output.splitlines()[-1] == "failure"
    assert result.duration_ms >= 0
    note = result.failure_note("example", 1000)
    assert "Command: `example`" in note
    assert "Exit code: 7" in note


def test_run_verify_inherits_environment_and_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VERIFY_PARENT", "parent")
    result = run_verify(
        'printf "%s:%s" "$VERIFY_PARENT" "$VERIFY_CHILD"; pwd', tmp_path, 1000,
        {"VERIFY_CHILD": "child"},
    )
    assert result.passed
    assert result.output == f"parent:child{tmp_path}\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group signal probe")
def test_timeout_kills_whole_process_group(tmp_path: Path):
    pidfile = tmp_path / "child.pid"
    marker = tmp_path / "survived"
    script = (
        "import os, pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); "
        f"time.sleep(3); pathlib.Path({str(marker)!r}).touch()"
    )
    # 2 s leaves a loaded CI host time to start Python and write the pid file
    # before the timeout fires; the grandchild would survive until 3 s.
    result = run_verify(f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} & wait", tmp_path, 2000)
    assert result.timed_out
    assert not result.passed
    assert "timed out after 2 s" in result.failure_note("example", 2000)
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        # A reaped-later zombie cannot perform any further work.
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().split()[2] == "Z":
            break
        time.sleep(0.02)
    else:
        os.kill(pid, 9)
        pytest.fail(f"verify grandchild {pid} survived timeout")
    assert not marker.exists()


def test_command_spawn_error_is_failure(tmp_path: Path):
    result = run_verify("true", tmp_path / "missing", 1000)
    assert result.exit_code == 127
    assert not result.passed


def test_crossed_commands_cover_every_lane_a_forward_move_leaves_or_skips() -> None:
    from symphony.workflow.verify import crossed_commands, gates_from

    lanes = ("Todo", "In Progress", "Verify", "Document")
    commands = {"in progress": "unit", "verify": "integration"}

    assert crossed_commands(commands, "In Progress", "Verify", lanes) == [("in progress", "unit")]
    assert crossed_commands(commands, "In Progress", "Done", lanes) == [
        ("in progress", "unit"),
        ("verify", "integration"),
    ]
    assert crossed_commands(commands, "Todo", "Document", lanes) == [
        ("in progress", "unit"),
        ("verify", "integration"),
    ]
    assert crossed_commands(commands, "Verify", "In Progress", lanes) == []
    assert crossed_commands(commands, "Document", "Done", lanes) == []
    assert gates_from(commands, "Todo", lanes)
    assert not gates_from(commands, "Document", lanes)
    # A custom lane outside the active order is still gated by its own command.
    assert crossed_commands({"triage": "lint"}, "Triage", "Done", lanes) == [("triage", "lint")]
