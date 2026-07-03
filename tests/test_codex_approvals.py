from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from symphony.backends import (
    EVENT_APPROVAL_AUTO_APPROVED,
    EVENT_APPROVAL_DENIED,
    BackendInit,
)
from symphony.backends.approval_policy import dangerous_command_reason
from symphony.backends.codex import CodexAppServerBackend
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    OpenCodeConfig,
    PiConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
)


class _FakeStdin:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None


class _FakeStdout:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._lines = [
            (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
            for message in messages
        ]

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProcess:
    pid = 123
    returncode = 0

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(messages)


def _make_cfg(workspace_root: Path) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=workspace_root / "WORKFLOW.md",
        poll_interval_ms=30_000,
        workspace_root=workspace_root,
        tracker=TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            active_states=("Todo",),
            terminal_states=("Done",),
            board_root=workspace_root / "kanban",
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=1,
            max_turns=5,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
        ),
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
        ),
        claude=ClaudeConfig(
            command="claude -p --output-format stream-json --verbose",
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
        ),
        gemini=GeminiConfig(
            command='gemini -p ""',
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
        ),
        opencode=OpenCodeConfig(
            command="opencode run --format json --auto",
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
        ),
        pi=PiConfig(
            command='pi --mode json -p ""',
            turn_timeout_ms=60_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=30_000,
            resume_across_turns=True,
        ),
        server=ServerConfig(port=None),
        prompt_template="hi",
    )


def _backend(tmp_path: Path, messages: list[dict[str, Any]], events: list[dict]) -> CodexAppServerBackend:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    cfg = _make_cfg(tmp_path)

    async def capture(event: dict) -> None:
        events.append(event)

    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=capture)
    )
    backend._process = _FakeProcess(messages)  # type: ignore[assignment]
    return backend


async def _read_response(
    tmp_path: Path, message: dict[str, Any], events: list[dict] | None = None
) -> dict[str, Any]:
    captured = events if events is not None else []
    backend = _backend(tmp_path, [message], captured)
    await backend._stdout_reader()
    lines = backend._process.stdin.data.decode("utf-8").splitlines()  # type: ignore[union-attr]
    assert len(lines) == 1
    return json.loads(lines[0])


@pytest.mark.parametrize(
    "method,params,expected_result",
    [
        (
            "item/commandExecution/requestApproval",
            {"command": "npm test", "cwd": "/repo"},
            {"decision": "accept"},
        ),
        (
            "item/fileChange/requestApproval",
            {"path": "src/app.py"},
            {"decision": "accept"},
        ),
        (
            "mcpServer/elicitation/request",
            {"message": "Allow MCP tool?"},
            {"action": "accept", "content": {}},
        ),
        (
            "item/tool/requestUserInput",
            {"questions": [{"id": "confirm"}, {"id": "details"}]},
            {
                "answers": {
                    "confirm": {"answers": []},
                    "details": {"answers": []},
                }
            },
        ),
        (
            "item/permissions/requestApproval",
            {
                "permissions": {
                    "network": {"enabled": True},
                    "fileSystem": {"write": ["/repo"]},
                }
            },
            {
                "permissions": {
                    "network": {"enabled": True},
                    "fileSystem": {"write": ["/repo"]},
                },
                "scope": "session",
            },
        ),
        (
            "execCommandApproval",
            {"command": ["git", "status"]},
            {"decision": "approved"},
        ),
        (
            "applyPatchApproval",
            {"changes": 1},
            {"decision": "approved"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_codex_server_requests_are_answered(
    tmp_path: Path,
    method: str,
    params: dict[str, Any],
    expected_result: dict[str, Any],
) -> None:
    events: list[dict] = []

    response = await _read_response(
        tmp_path,
        {"jsonrpc": "2.0", "id": "req-1", "method": method, "params": params},
        events,
    )

    assert response == {"jsonrpc": "2.0", "id": "req-1", "result": expected_result}
    assert events[-1]["event"] == EVENT_APPROVAL_AUTO_APPROVED
    assert events[-1]["payload"]["method"] == method


@pytest.mark.asyncio
async def test_codex_declines_dangerous_v2_command_and_emits_denial(
    tmp_path: Path,
) -> None:
    events: list[dict] = []

    response = await _read_response(
        tmp_path,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "cd /repo && rm -rf build"},
        },
        events,
    )

    assert response == {"jsonrpc": "2.0", "id": 7, "result": {"decision": "decline"}}
    assert events[-1]["event"] == EVENT_APPROVAL_DENIED
    assert events[-1]["payload"]["method"] == "item/commandExecution/requestApproval"
    assert events[-1]["payload"]["command"] == "cd /repo && rm -rf build"
    assert "rm" in events[-1]["payload"]["reason"]


@pytest.mark.asyncio
async def test_codex_denies_dangerous_legacy_exec_command(
    tmp_path: Path,
) -> None:
    events: list[dict] = []

    response = await _read_response(
        tmp_path,
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "execCommandApproval",
            "params": {"command": ["git", "clean", "-fdx"]},
        },
        events,
    )

    assert response == {"jsonrpc": "2.0", "id": 8, "result": {"decision": "denied"}}
    assert events[-1]["event"] == EVENT_APPROVAL_DENIED
    assert events[-1]["payload"]["command"] == "git clean -fdx"
    assert "git clean" in events[-1]["payload"]["reason"]


@pytest.mark.asyncio
async def test_codex_unknown_server_request_gets_json_rpc_error(
    tmp_path: Path,
) -> None:
    response = await _read_response(
        tmp_path,
        {"jsonrpc": "2.0", "id": "mystery", "method": "unknown/request", "params": {}},
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": "mystery",
        "error": {
            "code": -32601,
            "message": "unsupported server request: unknown/request",
        },
    }


@pytest.mark.parametrize(
    "command,reason_fragment",
    [
        ("rm -rf build", "rm"),
        ("rm -fr build", "rm"),
        ("rm -r -f build", "rm"),
        ("rm -Rf build", "rm"),
        ("rm --recursive --force build", "rm"),
        ("sudo make install", "sudo"),
        ("cd repo && sudo make install", "sudo"),
        ("mkfs.ext4 /dev/sda1", "mkfs"),
        ("dd if=image.iso of=/dev/disk2 bs=1m", "dd"),
        ("shred secrets.txt", "shred"),
        ("find . -name '*.pyc' -delete", "find"),
        ("git clean -fdx", "git clean"),
        ("git clean -fx -d", "git clean"),
        ('echo "rm -rf build"', "rm"),
    ],
)
def test_dangerous_command_reason_flags_tight_denylist(
    command: str, reason_fragment: str
) -> None:
    reason = dangerous_command_reason(command)

    assert reason is not None
    assert reason_fragment in reason


@pytest.mark.parametrize(
    "command",
    [
        "rm -f single-file",
        "rm -r directory",
        "grep sudo README.md",
        "git clean -fd",
        "git clean -d",
        "git status",
        "dd if=/dev/zero of=./disk.img bs=1m count=1",
    ],
)
def test_dangerous_command_reason_allows_common_safe_commands(command: str) -> None:
    assert dangerous_command_reason(command) is None
