"""Preflight checks emitted by `symphony doctor`."""

from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from symphony import service as service_module
from symphony.service import ServiceRecord, save_record
from symphony.cli.doctor import (
    check_after_create_hook,
    check_agy_state_dir,
    check_agent_cli,
    check_board_viewer,
    check_gemini_auth,
    check_kiro_auth,
    check_pi_auth,
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


def test_after_create_warning_does_not_fail_by_default(
    tmp_path: Path, capsys
) -> None:
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
                viewer_port=None,
                orchestrator_pid=4242,
                viewer_pid=None,
                log_path=tmp_path / "log" / "symphony.log",
                viewer_log_path=None,
                started_at="2026-07-03T01:00:00Z",
                orchestrator_command=["symphony", str(cfg.workflow_path)],
                viewer_command=[],
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
                viewer_port=None,
                orchestrator_pid=4242,
                viewer_pid=None,
                log_path=tmp_path / "log" / "symphony.log",
                viewer_log_path=None,
                started_at="2026-07-03T01:00:00Z",
                orchestrator_command=["symphony", str(cfg.workflow_path)],
                viewer_command=[],
            )
        )
        monkeypatch.setattr(
            service_module,
            "is_symphony_api_reachable",
            lambda host, port: (host, port) == ("127.0.0.1", bound_port),
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
    # port + shell + max_turns + agent + pi_auth + gemini_auth + agy_state + kiro_auth
    # + prompts + after_create + workspace + tracker + viewer = 13
    assert len(results) == 13
    assert {r.name.split("=")[0].split(".")[0] for r in results} >= {
        "agent",
        "hooks",
        "workspace",
        "tracker",
        "viewer",
    }


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
    monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive):] if home.drive else str(home))


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


def test_gemini_auth_accepts_oauth_selected_type(
    tmp_path: Path, monkeypatch
) -> None:
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
        agy: { command: agy --print - }
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
        agy: { command: agy --print - }
        """,
    )

    result = check_agy_state_dir(cfg)

    assert result.status == "fail"
    assert "not writable" in result.message
    assert "operation not permitted" in result.message


def test_stage_turn_budget_fails_for_multi_stage_one_turn_workflow(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker:
          kind: file
          board_root: ./kanban
          active_states: [Todo, In Progress, Verify, Learn]
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


def test_board_viewer_pass_when_script_present(tmp_path: Path) -> None:
    """WARN downgrades to PASS once `tools/board-viewer/server.py` exists."""
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    viewer = tmp_path / "tools" / "board-viewer" / "server.py"
    viewer.parent.mkdir(parents=True)
    viewer.write_text("# stub viewer\n")

    result = check_board_viewer(cfg)
    assert result.status == "pass"
    assert "server.py" in result.message


def test_board_viewer_warns_when_script_missing(tmp_path: Path) -> None:
    """WARN (not FAIL) so the orchestrator can still launch headless."""
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    result = check_board_viewer(cfg)
    assert result.status == "warn"
    # Windows renders the script path with backslashes — compare separator-
    # agnostically so the assertion holds on every platform.
    assert "tools/board-viewer/server.py" in result.message.replace("\\", "/")
    assert "no-op" in result.message
