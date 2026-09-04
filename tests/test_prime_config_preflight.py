"""Focused Prime Agent workflow/config and dispatch-preflight contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import symphony.backends.per_turn as per_turn_module
from symphony.backends import (
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    BackendInit,
    build_backend,
)
from symphony.backends.prime_agent import PrimeAgentBackend
from symphony.errors import ConfigValidationError, TurnTimeout
import symphony.workflow as workflow_module
from symphony.workflow import (
    DEFAULT_PRIME_AGENT_COMMAND,
    ServiceConfig,
    build_service_config,
    parse_workflow_text,
)
from symphony.workflow.preflight import validate_for_dispatch
from tests.test_backends import (
    _BlockingStream,
    _FakeSubprocess,
    _install_subprocess_double,
)


def _config(
    tmp_path: Path, *, front_matter: str = "agent: {kind: prime-agent}"
) -> ServiceConfig:
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        f"---\ntracker: {{kind: file, board_root: ./board}}\n"
        f"workspace: {{root: ./workspaces}}\n{front_matter}\n---\nprompt\n"
    )
    workflow = parse_workflow_text(workflow_path.read_text(), workflow_path)
    return build_service_config(workflow)


def test_prime_agent_defaults_are_configured_and_exported(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert "DEFAULT_PRIME_AGENT_COMMAND" in workflow_module.__all__
    assert config.prime_agent.command == DEFAULT_PRIME_AGENT_COMMAND
    assert config.prime_agent.resume_across_turns is True
    assert config.backend_timeouts() == (3_600_000, 20_000, 300_000)
    validate_for_dispatch(config)


def test_legacy_positional_service_config_gets_prime_default(tmp_path: Path) -> None:
    config = _config(tmp_path)
    # These are the fields accepted positionally before Prime Agent was added.
    legacy_fields = (
        "workflow_path",
        "poll_interval_ms",
        "workspace_root",
        "tracker",
        "hooks",
        "agent",
        "codex",
        "claude",
        "gemini",
        "pi",
        "server",
        "agy",
        "kiro",
        "opencode",
        "tui",
        "progress",
        "system",
        "prompts",
        "wiki",
        "notifications",
        "continuous_improvement",
        "raw",
        "prompt_template",
        "workspace_reuse_policy",
    )
    legacy = ServiceConfig(*(getattr(config, field) for field in legacy_fields))

    assert legacy.prime_agent.command == DEFAULT_PRIME_AGENT_COMMAND
    assert legacy.prime_agent.resume_across_turns is True


def test_prime_agent_preflight_rejects_empty_command(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        front_matter=("agent: {kind: prime-agent}\nprime_agent:\n  command: '   '\n"),
    )

    with pytest.raises(
        ConfigValidationError, match=r"prime_agent\.command must be non-empty"
    ):
        validate_for_dispatch(config)


@pytest.mark.asyncio
async def test_prime_backend_contract_uses_prime_resume_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    cwd = config.workspace_root / "ws"
    cwd.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, object]] = []

    async def on_event(event: dict[str, object]) -> None:
        events.append(event)

    commands = _install_subprocess_double(
        monkeypatch,
        per_turn_module,
        [
            _FakeSubprocess(
                stdout_lines=[
                    b'{"type":"session","version":3,"id":"prime-c1"}\n',
                    b'{"type":"agent_end","messages":[{"role":"assistant",'
                    b'"content":[{"type":"text","text":"done"}]}]}\n',
                ]
            ),
            _FakeSubprocess(
                stdout_lines=[
                    b'{"type":"agent_end","messages":[{"role":"assistant",'
                    b'"content":[{"type":"text","text":"continued"}]}]}\n',
                ]
            ),
        ],
    )
    backend = build_backend(
        BackendInit(
            cfg=config, cwd=cwd, workspace_root=config.workspace_root, on_event=on_event
        )
    )

    assert isinstance(backend, PrimeAgentBackend)
    assert (await backend.initialize())["agent"] == "prime-agent"
    await backend.start_session(initial_prompt="hi", issue_title="Contract")
    first = await backend.run_turn(prompt="first", is_continuation=False)
    second = await backend.run_turn(prompt="second", is_continuation=True)
    await backend.stop()

    assert first.last_message == "done"
    assert second.last_message == "continued"
    assert commands == [
        "prime-agent -p --mode json",
        "prime-agent -p --mode json --resume prime-c1",
    ]
    names = [event["event"] for event in events]
    assert EVENT_SESSION_STARTED in names
    assert names.count(EVENT_TURN_COMPLETED) == 2


@pytest.mark.asyncio
async def test_prime_backend_timeout_error_uses_prime_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config = replace(
        config, prime_agent=replace(config.prime_agent, turn_timeout_ms=10)
    )
    cwd = config.workspace_root / "timeout-ws"
    cwd.mkdir(parents=True)
    process = _FakeSubprocess(returncode=0)
    process.stdout = _BlockingStream()
    process.stderr = _BlockingStream()
    _install_subprocess_double(monkeypatch, per_turn_module, [process])

    async def on_event(event: dict[str, object]) -> None:
        del event

    backend = PrimeAgentBackend(
        BackendInit(
            cfg=config, cwd=cwd, workspace_root=config.workspace_root, on_event=on_event
        )
    )
    await backend.start_session(initial_prompt="hi", issue_title="Timeout")

    with pytest.raises(TurnTimeout, match=r"prime-agent turn timed out"):
        await backend.run_turn(prompt="wait", is_continuation=False)
