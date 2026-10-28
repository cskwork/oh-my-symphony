"""`agent.profiles` — plain-text agent CLIs declared as WORKFLOW.md data."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from symphony.backends import BackendInit, build_backend
from symphony.backends import per_turn as per_turn_module
from symphony.backends.agy import AgyBackend
from symphony.backends.plain_cli import PlainCliBackend
from symphony.cli.doctor import check_agent_cli
from symphony.errors import ConfigValidationError
from symphony.workflow import ServiceConfig, build_service_config, parse_workflow_text
from tests.test_backends import _FakeSubprocess, _install_subprocess_double, _noop_event


def _cfg(tmp_path: Path, frontmatter: str) -> ServiceConfig:
    text = "---\n" + textwrap.dedent(frontmatter).lstrip() + "---\nbody\n"
    return build_service_config(parse_workflow_text(text, tmp_path / "WORKFLOW.md"))


_ECHO_PROFILE = """
tracker: {kind: file, board_root: ./board}
agent:
  kind: echo-cli
  profiles:
    Echo-CLI:
      command: 'echo-cli run "$(cat)"'
      format: text
      unattended_flags: [--yes, --no-color]
      continuation_flag: --continue
      continuation_position: before_prompt_arg
      turn_timeout_ms: 1000
      read_timeout_ms: 2000
      stall_timeout_ms: 3000
      resume_across_turns: true
"""


def test_profile_parses_with_all_keys(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _ECHO_PROFILE)

    assert cfg.agent.kind == "echo-cli"
    profile = cfg.agent.profiles["echo-cli"]
    assert profile.name == "echo-cli"
    assert profile.command == 'echo-cli run "$(cat)"'
    assert profile.format == "text"
    assert profile.unattended_flags == ("--yes", "--no-color")
    assert profile.continuation_flag == "--continue"
    assert profile.continuation_position == "before_prompt_arg"
    assert profile.resume_across_turns is True
    assert cfg.backend_timeouts() == (1000, 2000, 3000)
    assert cfg.agent_profile("echo-cli") == profile


def test_profile_defaults(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        """
        tracker: {kind: file, board_root: ./board}
        agent:
          kind: codex
          profiles:
            mini: {command: mini-cli}
        """,
    )

    profile = cfg.agent.profiles["mini"]
    assert profile.format == "text"
    assert profile.unattended_flags == ()
    assert profile.continuation_flag is None
    assert profile.continuation_position == "append"
    assert profile.resume_across_turns is False
    assert cfg.agent.kind == "codex"


def test_stage_kinds_may_route_to_a_profile(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        """
        tracker: {kind: file, board_root: ./board, active_states: [Todo, Verify]}
        agent:
          kind: codex
          stage_kinds: {Verify: mini}
          profiles:
            mini: {command: mini-cli}
        """,
    )

    assert cfg.agent.kind_for_state("Verify") == "mini"


@pytest.mark.parametrize(
    ("agent_block", "message"),
    [
        (
            "{kind: mini, profiles: {mini: {command: mini-cli, format: ndjson}}}",
            "agent.profiles.mini.format must be one of ['text', 'acp']",
        ),
        (
            "{kind: codex, profiles: {agy: {command: my-agy}}}",
            "shadows the built-in agent kind 'agy'",
        ),
        (
            "{kind: codex, profiles: {Antigravity: {command: my-agy}}}",
            "shadows the built-in agent kind 'antigravity'",
        ),
        (
            "{kind: codex, profiles: {mini: {format: text}}}",
            "agent.profiles.mini.command must be a non-empty string",
        ),
        (
            "{kind: codex, profiles: {mini: {cmd: mini-cli}}}",
            "agent.profiles.mini has unknown keys ['cmd']",
        ),
        (
            "{kind: codex, profiles: {mini: {command: m, unattended_flags: --yes}}}",
            "agent.profiles.mini.unattended_flags must be a list",
        ),
        (
            "{kind: codex, profiles: {mini: {command: m, continuation_position: end}}}",
            "agent.profiles.mini.continuation_position must be one of",
        ),
        (
            "{kind: codex, profiles: {mini: {command: m, resume_across_turns: 'yes'}}}",
            "agent.profiles.mini.resume_across_turns must be a boolean",
        ),
        ("{kind: codex, profiles: [mini]}", "agent.profiles must be a map"),
        ("{kind: mini}", "agent.kind must be one of"),
        (
            "{kind: codex, stage_kinds: {todo: nope}, profiles: {mini: {command: m}}}",
            "agent.stage_kinds['todo'] must be one of",
        ),
    ],
)
def test_profile_validation_errors(tmp_path: Path, agent_block: str, message: str) -> None:
    with pytest.raises(ConfigValidationError) as excinfo:
        _cfg(
            tmp_path,
            f"tracker: {{kind: file, board_root: ./board}}\nagent: {agent_block}\n",
        )

    assert message in str(excinfo.value)


def _build(cfg: ServiceConfig, tmp_path: Path) -> object:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    return build_backend(
        BackendInit(cfg=cfg, cwd=cwd, workspace_root=tmp_path, on_event=_noop_event)
    )


def _backend(cfg: ServiceConfig, tmp_path: Path) -> PlainCliBackend:
    backend = _build(cfg, tmp_path)
    assert type(backend) is PlainCliBackend
    return backend


@pytest.mark.asyncio
async def test_profile_dispatch_inserts_flags_before_prompt_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(_cfg(tmp_path, _ECHO_PROFILE), tmp_path)
    sid = await backend.start_session(initial_prompt="hi", issue_title="T")
    first = _FakeSubprocess(stdout_blob=b"answer one\n")
    commands = _install_subprocess_double(
        monkeypatch, per_turn_module, [first, _FakeSubprocess(stdout_blob=b"two\n")]
    )

    result = await backend.run_turn(prompt="first", is_continuation=False)
    await backend.run_turn(prompt="second", is_continuation=True)

    assert result.last_message == "answer one"
    assert result.turn_id == sid
    assert first.stdin.data == b"first"
    assert commands == [
        'echo-cli run --yes --no-color "$(cat)"',
        'echo-cli run --yes --no-color --continue "$(cat)"',
    ]


@pytest.mark.asyncio
async def test_profile_appends_flags_and_skips_ones_already_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(
        tmp_path,
        """
        tracker: {kind: file, board_root: ./board}
        agent:
          kind: mini
          profiles:
            mini:
              command: mini-cli --yes
              unattended_flags: [--yes, --quiet]
              continuation_flag: --resume
              resume_across_turns: true
        """,
    )
    backend = _backend(cfg, tmp_path)
    await backend.start_session(initial_prompt="hi", issue_title="T")
    commands = _install_subprocess_double(
        monkeypatch,
        per_turn_module,
        [_FakeSubprocess(stdout_blob=b"1"), _FakeSubprocess(stdout_blob=b"2")],
    )

    await backend.run_turn(prompt="a", is_continuation=False)
    await backend.run_turn(prompt="b", is_continuation=True)

    assert commands == [
        "mini-cli --yes --quiet",
        "mini-cli --yes --quiet --resume",
    ]


@pytest.mark.asyncio
async def test_profile_without_resume_never_adds_continuation_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(
        tmp_path,
        """
        tracker: {kind: file, board_root: ./board}
        agent:
          kind: mini
          profiles:
            mini: {command: mini-cli, continuation_flag: --resume}
        """,
    )
    backend = _backend(cfg, tmp_path)
    await backend.start_session(initial_prompt="hi", issue_title="T")
    commands = _install_subprocess_double(
        monkeypatch,
        per_turn_module,
        [_FakeSubprocess(stdout_blob=b"1"), _FakeSubprocess(stdout_blob=b"2")],
    )

    await backend.run_turn(prompt="a", is_continuation=False)
    await backend.run_turn(prompt="b", is_continuation=True)

    assert commands == ["mini-cli", "mini-cli"]


def test_builtin_agy_profile_reads_the_agy_block(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        """
        tracker: {kind: file, board_root: ./board}
        agent: {kind: antigravity}
        agy: {command: my-agy, turn_timeout_ms: 1234, resume_across_turns: true}
        """,
    )

    profile = cfg.agent_profile("agy")
    assert profile is not None
    assert profile.command == "my-agy"
    assert profile.turn_timeout_ms == 1234
    assert profile.resume_across_turns is True
    assert profile.unattended_flags == ("--dangerously-skip-permissions",)
    assert isinstance(_build(cfg, tmp_path), AgyBackend)


def test_doctor_checks_profile_command(tmp_path: Path) -> None:
    ok = _cfg(
        tmp_path,
        """
        tracker: {kind: file, board_root: ./board}
        agent:
          kind: mini
          profiles:
            mini: {command: python -m symphony.mock_codex}
        """,
    )
    missing = _cfg(
        tmp_path,
        """
        tracker: {kind: file, board_root: ./board}
        agent:
          kind: mini
          profiles:
            mini: {command: definitely-not-a-real-cli-xyz}
        """,
    )

    ok_result = check_agent_cli(ok)
    missing_result = check_agent_cli(missing)

    assert ok_result.name == "agent.kind=mini"
    assert ok_result.status == "pass"
    assert missing_result.status == "fail"
    assert "not on $PATH" in missing_result.message


def test_selectable_agent_kinds_includes_profiles_and_preserves_builtins(
    tmp_path: Path,
) -> None:
    from symphony.workflow import SUPPORTED_AGENT_KINDS, selectable_agent_kinds

    cfg = _cfg(tmp_path, _ECHO_PROFILE)
    assert selectable_agent_kinds() == SUPPORTED_AGENT_KINDS
    assert selectable_agent_kinds(cfg.agent.profiles) == SUPPORTED_AGENT_KINDS | {"echo-cli"}


def test_fallback_kinds_accept_profiles_and_keep_order(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        _ECHO_PROFILE.replace(
            "  kind: echo-cli", "  kind: codex\n  fallback_kinds: [Echo-CLI, claude, echo-cli]"
        ),
    )

    assert cfg.agent.fallback_kinds == ("echo-cli", "claude")


@pytest.mark.parametrize("setting", ["fallback_kinds: [missing]", "kind: missing"])
def test_unknown_kind_errors_list_configured_profiles(
    tmp_path: Path, setting: str
) -> None:
    with pytest.raises(ConfigValidationError) as excinfo:
        _cfg(tmp_path, _ECHO_PROFILE.replace("kind: echo-cli", setting))

    assert "must be one of" in str(excinfo.value)
    assert "echo-cli" in str(excinfo.value)
    assert "codex" in str(excinfo.value)
    assert "missing" not in excinfo.value.message


def test_continuous_improvement_accepts_profile(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        _ECHO_PROFILE + "continuous_improvement: {agent_kind: Echo-CLI}\n",
    )

    assert cfg.continuous_improvement.agent_kind == "echo-cli"


def test_continuous_improvement_unknown_kind_lists_profiles(tmp_path: Path) -> None:
    with pytest.raises(ConfigValidationError) as excinfo:
        _cfg(
            tmp_path,
            _ECHO_PROFILE + "continuous_improvement: {agent_kind: missing}\n",
        )

    assert "continuous_improvement.agent_kind" in excinfo.value.message
    assert "echo-cli" in excinfo.value.message
    assert "codex" in excinfo.value.message


def test_continuous_improvement_profile_mutation_roundtrip(tmp_path: Path) -> None:
    from symphony.workflow import load_workflow
    from symphony.workflow.mutate import (
        WorkflowMutationError,
        set_continuous_improvement_settings,
    )

    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("---\n" + _ECHO_PROFILE.lstrip() + "---\nbody\n")
    set_continuous_improvement_settings(workflow, agent_kind=" Echo-CLI ")

    cfg = build_service_config(load_workflow(workflow))
    assert cfg.continuous_improvement.agent_kind == "echo-cli"
    assert cfg.agent.kind == "echo-cli"
    saved = workflow.read_bytes()
    with pytest.raises(WorkflowMutationError) as excinfo:
        set_continuous_improvement_settings(workflow, agent_kind="missing")
    assert "echo-cli" in excinfo.value.message
    assert "codex" in excinfo.value.message
    assert workflow.read_bytes() == saved


def test_doctor_unknown_kind_lists_profiles(tmp_path: Path) -> None:
    from dataclasses import replace

    cfg = _cfg(tmp_path, _ECHO_PROFILE)
    cfg = replace(cfg, agent=replace(cfg.agent, kind="missing"))
    result = check_agent_cli(cfg)

    assert result.status == "fail"
    assert "unsupported agent kind 'missing'" in result.message
    assert "echo-cli" in result.message
    assert "codex" in result.message


@pytest.mark.parametrize("boundary", ["backend", "timeouts"])
def test_backend_unknown_kind_lists_configured_profiles(
    tmp_path: Path, boundary: str
) -> None:
    from dataclasses import replace

    cfg = _cfg(tmp_path, _ECHO_PROFILE)
    cfg = replace(cfg, agent=replace(cfg.agent, kind="missing"))
    with pytest.raises(ConfigValidationError) as excinfo:
        if boundary == "backend":
            _build(cfg, tmp_path)
        else:
            cfg.backend_timeouts()

    assert "echo-cli" in excinfo.value.message
    assert "codex" in excinfo.value.message
