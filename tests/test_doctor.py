"""Preflight checks emitted by `symphony doctor`."""

from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import types
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from symphony import service as service_module
from symphony.service import ServiceRecord, save_record
from symphony.cli.doctor import (
    check_after_create_hook,
    check_api_token_env,
    check_app_release_contract,
    check_agy_state_dir,
    check_agent_cli,
    check_gemini_auth,
    check_kiro_auth,
    check_pi_auth,
    check_prime_agent_auth,
    check_port,
    check_prompts,
    check_stage_turn_budget,
    check_tracker,
    check_workspace_root,
    format_results,
    main as doctor_main,
    run_checks,
)
from symphony.workflow import ServiceConfig, build_service_config, load_workflow


def _write_workflow(tmp_path: Path, body: str) -> Path:
    """Drop a YAML frontmatter workflow file at tmp_path/WORKFLOW.md."""
    path = tmp_path / "WORKFLOW.md"
    path.write_text(body)
    return path


def _build_cfg(tmp_path: Path, frontmatter: str) -> ServiceConfig:
    text = "---\n" + textwrap.dedent(frontmatter).lstrip() + "---\nbody"
    path = _write_workflow(tmp_path, text)
    return build_service_config(load_workflow(path))


def test_after_create_flags_placeholder(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        hooks:
          after_create: |
            git clone --depth=1 git@github.com:my-org/my-repo.git .
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_after_create_hook(cfg)
    assert result.status == "fail"
    assert "my-org/my-repo" in result.message


def test_after_create_passes_when_customized(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        hooks:
          after_create: ": noop"
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    assert check_after_create_hook(cfg).status == "pass"


def test_after_create_warns_on_masked_install_output(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        hooks:
          after_create: |
            pnpm install 2>&1 | tail -2 || true
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )

    result = check_after_create_hook(cfg)

    assert result.status == "warn"
    assert "|| true" in result.message
    assert "tail -2" in result.message
    assert "pnpm install" in result.message
    assert "WORKFLOW.md:" in result.message


def test_after_create_warning_does_not_fail_by_default(tmp_path: Path, capsys) -> None:
    board = tmp_path / "kanban"
    board.mkdir()
    workflow = _write_workflow(
        tmp_path,
        textwrap.dedent(f"""\
        ---
        tracker: {{ kind: file, board_root: {board} }}
        workspace: {{ root: {tmp_path / "workspaces"} }}
        hooks:
          after_create: |
            pnpm prisma generate 2>&1 | tail -n 2 || true
        agent: {{ kind: codex }}
        codex: {{ command: python -m symphony.mock_codex }}
        ---
        body
        """),
    )

    rc = doctor_main([str(workflow), "--no-color"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "WARN  hooks.after_create" in captured.out
    assert "|| true" in captured.out
    assert "tail -n 2" in captured.out


def test_after_create_warning_can_fail_by_policy(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        hooks:
          fail_on_warning_patterns: true
          after_create: |
            pnpm install 2>&1 | tail -2 || true
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )

    result = check_after_create_hook(cfg)

    assert result.status == "fail"
    assert "fail_on_warning_patterns" in result.message
    assert "WORKFLOW.md:" in result.message


def test_after_create_warns_on_known_setup_failure_text(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        hooks:
          after_create: |
            echo "PrismaConfigEnvError: Cannot resolve environment variable"
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )

    result = check_after_create_hook(cfg)

    assert result.status == "warn"
    assert "PrismaConfigEnvError" in result.message
    assert "WORKFLOW.md:" in result.message


def test_agent_cli_pass_for_python_mock(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: python -m symphony.mock_codex }
        """,
    )
    result = check_agent_cli(cfg)
    assert result.status == "pass"
    assert "python" in result.message


def test_agent_cli_pass_for_opencode_kind(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: opencode }
        opencode: { command: python -m symphony.mock_codex }
        """,
    )
    result = check_agent_cli(cfg)
    assert result.status == "pass"
    assert "python" in result.message


def test_agent_cli_pass_for_agy_kind(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: agy }
        agy: { command: python -m symphony.mock_codex }
        """,
    )
    result = check_agent_cli(cfg)
    assert result.status == "pass"
    assert "python" in result.message


def test_agent_cli_pass_for_kiro_kind(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: kiro }
        kiro: { command: python -m symphony.mock_codex }
        """,
    )
    result = check_agent_cli(cfg)
    assert result.status == "pass"
    assert "python" in result.message


def test_agent_cli_fail_for_missing_binary(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: definitely-not-a-real-binary-xyz123 }
        """,
    )
    result = check_agent_cli(cfg)
    assert result.status == "fail"
    assert "not on $PATH" in result.message


def _force_agent_cli_platform(
    monkeypatch: pytest.MonkeyPatch, *, win32: bool, resolved: str = "/resolved/agent"
) -> list[str]:
    """Pin check_agent_cli's platform branch and stub the PATH lookup.

    Returns the list the stubbed ``shutil.which`` records its binary
    arguments in, so each test can assert exactly what reached the lookup.
    """
    from symphony.cli import doctor as doctor_module

    seen: list[str] = []

    def _which(binary: str) -> str:
        seen.append(binary)
        return resolved

    monkeypatch.setattr(doctor_module, "_IS_WIN32", win32)
    monkeypatch.setattr(doctor_module, "shutil", types.SimpleNamespace(which=_which))
    return seen


def test_agent_cli_windows_keeps_backslash_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """win32 branch: backslash command paths must survive parsing.

    Regression: POSIX-mode shlex ate the backslashes, so
    ``D:\\tools\\python.exe`` reached ``shutil.which`` as
    ``D:toolspython.exe`` and the preflight bogus-failed with
    "not on $PATH". Cross-platform: the win32 branch is forced and the
    lookup stubbed, so the assert runs on every host.
    """
    seen = _force_agent_cli_platform(monkeypatch, win32=True)
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: 'D:\\tools\\python.exe -m symphony.mock_codex' }
        """,
    )

    result = check_agent_cli(cfg)

    assert result.status == "pass"
    assert seen == ["D:\\tools\\python.exe"]


def test_agent_cli_windows_strips_quotes_around_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """win32 branch: quotes shlex leaves on a quoted token are stripped.

    Whitespace-mode splitting keeps the quotes attached to the token;
    the binary handed to ``shutil.which`` must not carry them.
    """
    seen = _force_agent_cli_platform(monkeypatch, win32=True)
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: '"D:\\Program Files\\agent\\cli.exe" --serve' }
        """,
    )

    result = check_agent_cli(cfg)

    assert result.status == "pass"
    assert seen == ["D:\\Program Files\\agent\\cli.exe"]


def test_agent_cli_posix_branch_keeps_posix_splitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-win32 branch stays byte-identical to plain ``shlex.split``.

    POSIX commands use forward slashes, so backslash-eating there is the
    documented shlex semantics this check has always had; the fix must
    not alter it.
    """
    seen = _force_agent_cli_platform(monkeypatch, win32=False)
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: 'D:\\tools\\python.exe -m symphony.mock_codex' }
        """,
    )

    result = check_agent_cli(cfg)

    assert result.status == "pass"
    assert seen == ["D:toolspython.exe"]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real win32 branch end to end with a native "
    "backslash interpreter path that genuinely exists",
)
def test_agent_cli_windows_real_interpreter_path_passes(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker: {{ kind: file, board_root: ./kanban }}
        agent: {{ kind: codex }}
        codex: {{ command: '{sys.executable} -m symphony.mock_codex' }}
        """,
    )

    result = check_agent_cli(cfg)

    assert result.status == "pass"
    assert sys.executable in result.message


def test_port_pass_when_unconfigured(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    # No `server: { port: ... }` block → port is None → check passes trivially.
    assert check_port(cfg).status == "pass"


def test_port_fail_when_already_bound(tmp_path: Path) -> None:
    # Bind an ephemeral port and feed it back to the doctor.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    bound_port = sock.getsockname()[1]
    try:
        cfg = _build_cfg(
            tmp_path,
            f"""
            tracker: {{ kind: file, board_root: ./kanban }}
            agent: {{ kind: codex }}
            codex: {{ command: codex app-server }}
            server: {{ port: {bound_port} }}
            """,
        )
        result = check_port(cfg)
        assert result.status == "fail"
        # Doctor wraps the OSError as `cannot bind <host>:<port> — <exc>`.
        # Avoid asserting on the OSError text — Windows OSes return a
        # localized "Address already in use" (e.g. Korean "주소 …") that does
        # not contain the English substring.
        assert "cannot bind" in result.message
        assert f"127.0.0.1:{bound_port}" in result.message
    finally:
        sock.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX TIME_WAIT semantics")
def test_port_passes_when_only_time_wait_remains(tmp_path: Path) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    bound_port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", bound_port))
    accepted, _ = listener.accept()
    try:
        accepted.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b""
    finally:
        accepted.close()
        client.close()
        listener.close()

    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker: {{ kind: file, board_root: ./kanban }}
        agent: {{ kind: codex }}
        codex: {{ command: codex app-server }}
        server: {{ port: {bound_port} }}
        """,
    )

    assert check_port(cfg).status == "pass"


def test_port_fail_names_this_workflow_service_when_record_matches(
    tmp_path: Path,
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    bound_port = sock.getsockname()[1]
    try:
        cfg = _build_cfg(
            tmp_path,
            f"""
            tracker: {{ kind: file, board_root: ./kanban }}
            agent: {{ kind: codex }}
            codex: {{ command: codex app-server }}
            server: {{ port: {bound_port} }}
            """,
        )
        save_record(
            ServiceRecord(
                workflow_path=cfg.workflow_path,
                workflow_dir=cfg.workflow_path.parent,
                host="127.0.0.1",
                port=bound_port,
                orchestrator_pid=4242,
                log_path=tmp_path / "log" / "symphony.log",
                started_at="2026-07-03T01:00:00Z",
                orchestrator_command=["symphony", str(cfg.workflow_path)],
            )
        )
        result = check_port(cfg, is_running=lambda pid: pid == 4242)
        assert result.status == "fail"
        assert "owned by this workflow's service" in result.message
        assert "symphony service status" in result.message
        assert "pid 4242" in result.message
    finally:
        sock.close()


def test_port_fail_names_stale_record_when_api_still_responds(
    tmp_path: Path, monkeypatch
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    bound_port = sock.getsockname()[1]
    try:
        cfg = _build_cfg(
            tmp_path,
            f"""
            tracker: {{ kind: file, board_root: ./kanban }}
            agent: {{ kind: codex }}
            codex: {{ command: codex app-server }}
            server: {{ port: {bound_port} }}
            """,
        )
        save_record(
            ServiceRecord(
                workflow_path=cfg.workflow_path,
                workflow_dir=cfg.workflow_path.parent,
                host="127.0.0.1",
                port=bound_port,
                orchestrator_pid=4242,
                log_path=tmp_path / "log" / "symphony.log",
                started_at="2026-07-03T01:00:00Z",
                orchestrator_command=["symphony", str(cfg.workflow_path)],
            )
        )
        monkeypatch.setattr(
            service_module,
            "is_symphony_workflow_reachable",
            lambda host, port, workflow: (
                (
                    host,
                    port,
                    Path(workflow),
                )
                == ("127.0.0.1", bound_port, cfg.workflow_path.resolve())
            ),
        )

        result = check_port(cfg, is_running=lambda pid: False)

        assert result.status == "fail"
        assert "saved pid 4242 is stale" in result.message
        assert "API responds" in result.message
        assert "symphony service status" in result.message
    finally:
        sock.close()


def test_prompt_visibility_lists_configured_prompt_paths(tmp_path: Path) -> None:
    (tmp_path / "base.md").write_text("base", encoding="utf-8")
    stages = tmp_path / "prompts" / "stages"
    stages.mkdir(parents=True)
    (stages / "todo.md").write_text("todo", encoding="utf-8")
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        prompts:
          base: ./base.md
          stages:
            Todo: ./prompts/stages/todo.md
        """,
    )

    result = check_prompts(cfg)

    assert result.status == "pass"
    assert "2 prompt file" in result.message
    assert "base.md" in result.message


def test_prompt_visibility_reports_builtin_template(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )

    result = check_prompts(cfg)

    assert result.status == "pass"
    assert "built-in" in result.message


def test_missing_prompt_file_surfaces_load_error_without_traceback(
    tmp_path: Path, capsys
) -> None:
    workflow = _write_workflow(
        tmp_path,
        textwrap.dedent("""\
        ---
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        prompts:
          stages:
            Todo: ./missing.md
        ---
        body
        """),
    )

    rc = doctor_main([str(workflow), "--no-color"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "workflow load failed" in captured.err
    assert "missing.md" in captured.err
    assert "Traceback" not in captured.err


def test_workspace_root_creates_and_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker: {{ kind: file, board_root: ./kanban }}
        workspace: {{ root: {workspace} }}
        agent: {{ kind: codex }}
        codex: {{ command: codex app-server }}
        """,
    )
    result = check_workspace_root(cfg)
    assert result.status == "pass"
    assert workspace.exists()


def test_tracker_file_warns_on_missing_board_root(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"
    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker: {{ kind: file, board_root: {missing} }}
        agent: {{ kind: codex }}
        codex: {{ command: codex app-server }}
        """,
    )
    result = check_tracker(cfg)
    assert result.status == "fail"
    assert "does not exist" in result.message
    assert "symphony board init" in result.message


def test_tracker_file_passes_with_tickets(tmp_path: Path) -> None:
    board = tmp_path / "kanban"
    board.mkdir()
    (board / "X-1.md").write_text("---\nidentifier: X-1\ntitle: t\nstate: Todo\n---\n")
    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker: {{ kind: file, board_root: {board} }}
        agent: {{ kind: codex }}
        codex: {{ command: codex app-server }}
        """,
    )
    result = check_tracker(cfg)
    assert result.status == "pass"
    assert "1 ticket" in result.message


def test_run_checks_returns_one_result_per_check(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    results = run_checks(cfg)
    # protected repository + port + api_token + shell + max_turns + agent
    # + pi_auth + prime_agent_auth + gemini_auth + agy_state + kiro_auth
    # + prompts + after_create + workspace + git_history + agent_git_grant
    # + tracker + board.reachable + deep_merge_contract + stage_contracts
    # + board.cli + board.dependencies + app.release-contract + state.db = 24
    assert len(results) == 24
    assert {r.name.split("=")[0].split(".")[0] for r in results} >= {
        "agent",
        "hooks",
        "workspace",
        "tracker",
    }


def test_api_token_env_unset_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    monkeypatch.delenv("SYMPHONY_API_TOKEN", raising=False)
    result = check_api_token_env(cfg)
    assert result.status == "pass"
    assert "unset" in result.message


def test_api_token_env_internal_whitespace_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token with inner spaces can never match the two-word bearer
    parse, so every /api/ request 401s — advisory, not a failure."""
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "sekrit token")
    result = check_api_token_env(cfg)
    assert result.status == "warn"
    assert "whitespace" in result.message


def test_api_token_env_surrounding_whitespace_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "  sekrit-token\t")
    result = check_api_token_env(cfg)
    assert result.status == "pass"
    assert "required" in result.message


def test_api_token_env_blank_passes_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "   ")
    result = check_api_token_env(cfg)
    assert result.status == "pass"
    assert "unset" in result.message


def test_pi_auth_skipped_for_non_pi(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_pi_auth(cfg)
    assert result.status == "pass"
    assert "skipped" in result.message


def test_prime_agent_auth_skipped_for_non_prime_agent(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_prime_agent_auth(cfg)
    assert result.status == "pass"
    assert "skipped" in result.message


def test_gemini_auth_skipped_for_non_gemini(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_gemini_auth(cfg)
    assert result.status == "pass"
    assert "skipped" in result.message


def test_kiro_auth_skipped_for_non_kiro(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_kiro_auth(cfg)
    assert result.status == "pass"
    assert "skipped" in result.message


def test_agy_state_dir_skipped_for_non_agy(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_agy_state_dir(cfg)
    assert result.status == "pass"
    assert "skipped" in result.message


def _isolate_home(monkeypatch, home: Path) -> None:
    """Make Path.home() resolve to ``home`` on every platform.

    POSIX reads HOME; Windows reads USERPROFILE first and falls back to
    HOMEDRIVE+HOMEPATH. Setting all three keeps the tests platform-neutral.
    """
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOMEDRIVE", home.drive or "")
    monkeypatch.setenv(
        "HOMEPATH", str(home)[len(home.drive) :] if home.drive else str(home)
    )


def test_pi_auth_warns_when_missing(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: pi }
        pi: { command: 'pi --mode json -p \"\"' }
        """,
    )
    result = check_pi_auth(cfg)
    assert result.status == "warn"
    assert "auth.json" in result.message


def test_pi_auth_passes_when_present(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    auth = tmp_path / ".pi" / "agent" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}")
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: pi }
        pi: { command: 'pi --mode json -p \"\"' }
        """,
    )
    result = check_pi_auth(cfg)
    assert result.status == "pass"
    assert "auth.json" in result.message


def test_prime_agent_auth_warns_when_missing(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.delenv("PRIME_AGENT_CODING_AGENT_DIR", raising=False)
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: prime-agent }
        prime_agent: { command: 'prime-agent -p --mode json' }
        """,
    )
    result = check_prime_agent_auth(cfg)
    assert result.status == "warn"
    assert ".prime" in result.message
    assert "auth.json" in result.message


def test_prime_agent_auth_passes_when_present(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.delenv("PRIME_AGENT_CODING_AGENT_DIR", raising=False)
    auth = tmp_path / ".prime" / "agent" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}")
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: prime-agent }
        prime_agent: { command: 'prime-agent -p --mode json' }
        """,
    )
    result = check_prime_agent_auth(cfg)
    assert result.status == "pass"
    assert "auth.json" in result.message


def test_prime_agent_auth_uses_config_dir_override(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    auth_dir = tmp_path / "prime-config"
    monkeypatch.setenv("PRIME_AGENT_CODING_AGENT_DIR", str(auth_dir))
    auth = auth_dir / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}")
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: prime-agent }
        prime_agent: { command: 'prime-agent -p --mode json' }
        """,
    )
    result = check_prime_agent_auth(cfg)
    assert result.status == "pass"
    assert str(auth) in result.message


def test_gemini_auth_fails_without_root_auth_or_env(
    tmp_path: Path, monkeypatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = tmp_path / ".gemini" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}}}',
        encoding="utf-8",
    )
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: gemini }
        gemini: { command: 'gemini -p ""' }
        """,
    )
    result = check_gemini_auth(cfg)
    assert result.status == "fail"
    assert "selectedAuthType" in result.message
    assert "security.auth.selectedType" in result.message


def test_gemini_auth_accepts_api_key_env(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "redacted")
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: gemini }
        gemini: { command: 'gemini -p ""' }
        """,
    )
    result = check_gemini_auth(cfg)
    assert result.status == "pass"
    assert "GEMINI_API_KEY" in result.message


def test_gemini_auth_accepts_oauth_selected_type(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = tmp_path / ".gemini" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"selectedAuthType":"oauth-personal"}', encoding="utf-8")
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: gemini }
        gemini: { command: 'gemini -p ""' }
        """,
    )
    result = check_gemini_auth(cfg)
    assert result.status == "pass"
    assert "oauth-personal" in result.message


def test_kiro_auth_fails_without_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("KIRO_API_KEY", raising=False)
    monkeypatch.setattr(
        "symphony.cli.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr="not logged in",
        ),
    )
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: kiro }
        kiro: { command: kiro-cli chat --no-interactive "hello" }
        """,
    )
    result = check_kiro_auth(cfg)
    assert result.status == "fail"
    assert "KIRO_API_KEY" in result.message
    assert "kiro-cli whoami" in result.message


def test_kiro_auth_accepts_cli_login(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("KIRO_API_KEY", raising=False)
    monkeypatch.setattr(
        "symphony.cli.doctor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="Logged in with Builder ID\nEmail: redacted@example.com\n",
            stderr="",
        ),
    )
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: kiro }
        kiro: { command: kiro-cli chat --no-interactive "hello" }
        """,
    )
    result = check_kiro_auth(cfg)
    assert result.status == "pass"
    assert "whoami" in result.message
    assert "Email" not in result.message


def test_kiro_auth_accepts_api_key_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIRO_API_KEY", "redacted")
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: kiro }
        kiro: { command: kiro-cli chat --no-interactive "hello" }
        """,
    )
    result = check_kiro_auth(cfg)
    assert result.status == "pass"
    assert "KIRO_API_KEY" in result.message


def test_agy_state_dir_passes_when_home_state_is_writable(
    tmp_path: Path, monkeypatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: agy }
        agy: { command: agy --print "$(cat)" }
        """,
    )

    result = check_agy_state_dir(cfg)

    assert result.status == "pass"
    assert ".gemini" in result.message


def test_agy_state_dir_fails_when_state_is_not_writable(
    tmp_path: Path, monkeypatch
) -> None:
    _isolate_home(monkeypatch, tmp_path)

    def fail_temp_file(*args, **kwargs):
        raise OSError("operation not permitted")

    monkeypatch.setattr(
        "symphony.cli.doctor.tempfile.NamedTemporaryFile",
        fail_temp_file,
    )
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: agy }
        agy: { command: agy --print "$(cat)" }
        """,
    )

    result = check_agy_state_dir(cfg)

    assert result.status == "fail"
    assert "not writable" in result.message
    assert "operation not permitted" in result.message


def test_stage_turn_budget_fails_for_multi_stage_one_turn_workflow(
    tmp_path: Path,
) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker:
          kind: file
          board_root: ./kanban
          active_states: [Todo, In Progress, Verify, Document]
          terminal_states: [Done, Blocked]
        agent:
          kind: codex
          max_turns: 1
        codex: { command: codex app-server }
        """,
    )

    result = check_stage_turn_budget(cfg)

    assert result.status == "fail"
    assert "agent.max_turns=1" in result.message
    assert "4 active states" in result.message


def test_format_results_includes_all_statuses() -> None:
    from symphony.cli.doctor import CheckResult

    text = format_results(
        [
            CheckResult("a", "pass", "ok"),
            CheckResult("b", "warn", "careful"),
            CheckResult("c", "fail", "broken"),
        ],
        color=False,
    )
    assert "PASS" in text and "WARN" in text and "FAIL" in text


# ---------------------------------------------------------------------------
# F-04 — board reachability from the worker workspace
# ---------------------------------------------------------------------------


def test_board_reachable_warns_when_hook_ignores_board_root(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_board_reachable_from_workspace

    (tmp_path / "my-board").mkdir()
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./my-board }
        hooks:
          after_create: |
            for dir in kanban; do ln -s "$SYMPHONY_WORKFLOW_DIR/$dir" "$dir"; done
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_board_reachable_from_workspace(cfg)
    assert result.status == "warn"
    assert "my-board" in result.message


def test_board_reachable_passes_when_hook_uses_board_root_env(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_board_reachable_from_workspace

    (tmp_path / "my-board").mkdir()
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./my-board }
        hooks:
          after_create: |
            for dir in ${SYMPHONY_BOARD_ROOT_NAME:-kanban}; do ln -s x "$dir"; done
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    assert check_board_reachable_from_workspace(cfg).status == "pass"


def test_board_reachable_reads_the_script_the_hook_invokes(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_board_reachable_from_workspace

    (tmp_path / "kanban").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "setup.sh").write_text(
        "for dir in ${SYMPHONY_BOARD_ROOT_NAME:-kanban}; do :; done\n",
        encoding="utf-8",
    )
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        hooks:
          after_create: |
            bash "$SYMPHONY_WORKFLOW_DIR/scripts/setup.sh"
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    assert check_board_reachable_from_workspace(cfg).status == "pass"


def test_board_reachable_fails_when_board_root_missing(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_board_reachable_from_workspace

    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./nope }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_board_reachable_from_workspace(cfg)
    assert result.status == "fail"
    assert "does not exist" in result.message


def test_run_checks_includes_board_reachable_row(tmp_path: Path) -> None:
    (tmp_path / "kanban").mkdir()
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    names = [r.name for r in run_checks(cfg)]
    assert "board.reachable" in names


# ---------------------------------------------------------------------------
# F-05 — deep preset merge contract
# ---------------------------------------------------------------------------


_DEEP_STATES = (
    "active_states: [Intake, Research, Plan, Review, Build, QA, Verify, Document]"
)


def test_deep_merge_contract_skipped_on_default_board(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_deep_preset_merge_contract

    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_deep_preset_merge_contract(cfg)
    assert result.status == "pass"
    assert "not a deep-preset board" in result.message


def test_deep_merge_contract_passes_on_default_branch_policy(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_deep_preset_merge_contract

    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker:
          kind: file
          board_root: ./kanban
          {_DEEP_STATES}
          terminal_states: [Done, Human Review, Blocked, Cancelled]
        agent:
          kind: codex
          max_turns: 100
        codex: {{ command: codex app-server }}
        """,
    )
    assert check_deep_preset_merge_contract(cfg).status == "pass"


def test_deep_merge_contract_fails_without_auto_merge(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_deep_preset_merge_contract

    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker:
          kind: file
          board_root: ./kanban
          {_DEEP_STATES}
          terminal_states: [Done, Human Review, Blocked, Cancelled]
        agent:
          kind: codex
          max_turns: 100
          auto_merge_on_done: false
        codex: {{ command: codex app-server }}
        """,
    )
    result = check_deep_preset_merge_contract(cfg)
    assert result.status == "fail"
    assert "auto_merge_on_done" in result.message


def test_deep_merge_contract_fails_when_base_and_target_diverge(
    tmp_path: Path,
) -> None:
    from symphony.cli.doctor import check_deep_preset_merge_contract

    cfg = _build_cfg(
        tmp_path,
        f"""
        tracker:
          kind: file
          board_root: ./kanban
          {_DEEP_STATES}
          terminal_states: [Done, Human Review, Blocked, Cancelled]
        agent:
          kind: codex
          max_turns: 100
          feature_base_branch: main
          auto_merge_target_branch: release
        codex: {{ command: codex app-server }}
        """,
    )
    result = check_deep_preset_merge_contract(cfg)
    assert result.status == "fail"
    assert "feature_base_branch" in result.message


def test_stage_contracts_doctor_row_warns_on_renamed_lane_board(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_stage_contracts

    cfg = _build_cfg(
        tmp_path,
        """
        tracker:
          kind: file
          board_root: ./kanban
          active_states: [Todo, In Progress, Verify, Docs]
          terminal_states: [Done]
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_stage_contracts(cfg)
    assert result.status == "warn"
    assert "Docs" in result.message
    assert "stage_contracts: on" in result.message


def test_stage_contracts_doctor_row_passes_on_default_board(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_stage_contracts

    cfg = _build_cfg(
        tmp_path,
        """
        tracker:
          kind: file
          board_root: ./kanban
          active_states: [Todo, In Progress, Verify, Document]
          terminal_states: [Done]
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    assert check_stage_contracts(cfg).status == "pass"


def test_stage_contracts_doctor_row_warns_when_explicitly_off(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_stage_contracts

    cfg = _build_cfg(
        tmp_path,
        """
        tracker:
          kind: file
          board_root: ./kanban
          active_states: [Todo, In Progress, Verify, Document]
          terminal_states: [Done]
        agent: { kind: codex, stage_contracts: off }
        codex: { command: codex app-server }
        """,
    )
    result = check_stage_contracts(cfg)
    assert result.status == "warn"
    assert "prompts are the only gate" in result.message


# ---------------------------------------------------------------------------
# F-19 — the board CLI must be reachable from a worker
# ---------------------------------------------------------------------------


def test_symphony_cli_check_skipped_for_non_file_trackers(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_symphony_cli_reachable

    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: linear, api_key: tok, project_slug: p }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    assert check_symphony_cli_reachable(cfg).status == "pass"


def test_symphony_cli_check_warns_when_not_on_login_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import symphony.cli.doctor as doctor_module

    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )

    class _Missing:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(doctor_module.subprocess, "run", lambda *a, **k: _Missing())
    monkeypatch.setattr(
        "symphony.orchestrator.helpers.resolve_symphony_cli",
        lambda: "/opt/venv/bin/symphony",
    )
    result = doctor_module.check_symphony_cli_reachable(cfg)
    assert result.status == "warn"
    assert "SYMPHONY_CLI=/opt/venv/bin/symphony" in result.message


def test_symphony_cli_check_passes_when_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import symphony.cli.doctor as doctor_module

    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )

    class _Found:
        returncode = 0
        stdout = "/usr/local/bin/symphony\n"

    monkeypatch.setattr(doctor_module.subprocess, "run", lambda *a, **k: _Found())
    assert doctor_module.check_symphony_cli_reachable(cfg).status == "pass"


# ---------------------------------------------------------------------------
# F-13 — dangling blockers / cycles are reported, not silently deadlocking
# ---------------------------------------------------------------------------


def _board_with(tmp_path: Path, tickets: dict[str, list[str]]) -> "ServiceConfig":
    from symphony.trackers.file import write_ticket_atomic

    board = tmp_path / "kanban"
    board.mkdir(exist_ok=True)
    for identifier, blockers in tickets.items():
        front = {
            "id": identifier,
            "identifier": identifier,
            "title": identifier,
            "state": "Todo",
            "created_at": "2026-01-01T00:00:00Z",
        }
        if blockers:
            front["blocked_by"] = list(blockers)
        write_ticket_atomic(board / f"{identifier}.md", front, "body")
    return _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )


def test_board_dependencies_passes_on_a_clean_board(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_board_dependencies

    cfg = _board_with(tmp_path, {"A-1": [], "A-2": ["A-1"]})
    result = check_board_dependencies(cfg)
    assert result.status == "pass"
    assert "no dangling blockers" in result.message


def test_board_dependencies_fails_on_a_dangling_blocker(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_board_dependencies

    cfg = _board_with(tmp_path, {"A-1": ["TYPO-9"]})
    result = check_board_dependencies(cfg)
    assert result.status == "fail"
    assert "TYPO-9" in result.message
    assert "never be dispatched" in result.message


def test_board_dependencies_fails_on_a_cycle(tmp_path: Path) -> None:
    from symphony.cli.doctor import check_board_dependencies

    cfg = _board_with(tmp_path, {"A-1": ["A-2"], "A-2": ["A-1"]})
    result = check_board_dependencies(cfg)
    assert result.status == "fail"
    assert "cycle" in result.message


def _app_release_cfg(tmp_path: Path, *, labeled: bool = True) -> ServiceConfig:
    from symphony.trackers.file import write_ticket_atomic

    board = tmp_path / "kanban"
    board.mkdir(exist_ok=True)
    write_ticket_atomic(
        board / "VERIFY-1.md",
        {
            "id": "VERIFY-1",
            "identifier": "VERIFY-1",
            "title": "Release verifier",
            "state": "Verify",
            "labels": ["app-release"] if labeled else [],
            "created_at": "2026-01-01T00:00:00Z",
        },
        "body",
    )
    return _build_cfg(
        tmp_path,
        """
        tracker:
          kind: file
          board_root: ./kanban
          active_states: [Build, Verify, Document]
        agent:
          kind: codex
          auto_merge_target_branch: main
        codex: { command: codex app-server }
        """,
    )


def _write_doctor_contract(tmp_path: Path) -> None:
    kinds = (
        "feature",
        "control",
        "visual",
        "responsive",
        "accessibility",
        "reliability",
    )
    contract = {
        "schema_version": 1,
        "target_branch": "main",
        "finalizer_ticket": "APP-FINAL",
        "implementation_tickets": ["APP-1"],
        "launch": {"command": "npm run dev"},
        "runner": {
            "command": "python tools/release_runner.py",
            "sources": [
                {
                    "path": "tools/release_runner.py",
                    "sha256": "a" * 64,
                }
            ],
        },
        "viewports": {"desktop": {"width": 1440, "height": 900}},
        "checks": [
            {
                "id": f"{kind}-check",
                "kind": kind,
                "description": f"{kind} passes",
                "repair_group": kind,
                "required_viewports": ["desktop"],
            }
            for kind in kinds
        ],
    }
    (tmp_path / "release-contract.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False), encoding="utf-8"
    )


def test_app_release_doctor_ignores_non_app_file_board(tmp_path: Path) -> None:
    cfg = _app_release_cfg(tmp_path, labeled=False)

    result = check_app_release_contract(cfg)

    assert result.status == "pass"
    assert "not enabled" in result.message


def test_app_release_doctor_fails_labeled_board_without_contract(
    tmp_path: Path,
) -> None:
    cfg = _app_release_cfg(tmp_path)

    result = check_app_release_contract(cfg)

    assert result.status == "fail"
    assert "missing release contract" in result.message


def test_app_release_doctor_validates_contract_schema(tmp_path: Path) -> None:
    cfg = _app_release_cfg(tmp_path)
    (tmp_path / "release-contract.yaml").write_text(
        "schema_version: 1\ntarget_branch: main\n", encoding="utf-8"
    )

    result = check_app_release_contract(cfg)

    assert result.status == "fail"
    assert "missing field" in result.message


def test_app_release_doctor_passes_valid_local_contract(tmp_path: Path) -> None:
    cfg = _app_release_cfg(tmp_path)
    _write_doctor_contract(tmp_path)

    result = check_app_release_contract(cfg)

    assert result.status == "pass"
    assert "atomic file-tracker lifecycle" in result.message


def test_app_release_doctor_requires_active_verify_lane(tmp_path: Path) -> None:
    cfg = _app_release_cfg(tmp_path)
    cfg = replace(
        cfg,
        tracker=replace(cfg.tracker, active_states=("Build", "Document")),
    )
    _write_doctor_contract(tmp_path)

    result = check_app_release_contract(cfg)

    assert result.status == "fail"
    assert "active Verify" in result.message


def test_app_release_doctor_requires_non_verify_finalizer_lane(
    tmp_path: Path,
) -> None:
    cfg = _app_release_cfg(tmp_path)
    cfg = replace(
        cfg,
        tracker=replace(cfg.tracker, active_states=("Verify",)),
    )
    _write_doctor_contract(tmp_path)

    result = check_app_release_contract(cfg)

    assert result.status == "fail"
    assert "non-Verify active lane" in result.message


def test_app_release_doctor_requires_explicit_success_terminal(tmp_path: Path) -> None:
    cfg = _app_release_cfg(tmp_path)
    cfg = replace(
        cfg,
        tracker=replace(cfg.tracker, terminal_states=("Failed", "Rejected")),
    )
    _write_doctor_contract(tmp_path)

    result = check_app_release_contract(cfg)

    assert result.status == "fail"
    assert "successful terminal lane" in result.message


def test_app_release_doctor_leaves_unselected_remote_tracker_quiet(
    tmp_path: Path,
) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: linear, project_slug: TEST, api_key: token }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )

    result = check_app_release_contract(cfg)

    assert result.status == "pass"
    assert "not inspected" in result.message
