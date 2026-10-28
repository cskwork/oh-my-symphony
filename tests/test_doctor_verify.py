"""Doctor verification is opt-in, isolated, and reports actionable failures."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from symphony.cli import doctor
from symphony.workflow import ServiceConfig, build_service_config, load_workflow


def _config(tmp_path: Path, **frontmatter: object) -> ServiceConfig:
    raw: dict[str, object] = {
        "tracker": {"kind": "file", "board_root": "./kanban"},
        "agent": {"kind": "codex"},
        "claude": {"command": "claude -p --permission-mode acceptEdits"},
        **frontmatter,
    }
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("---\n" + yaml.safe_dump(raw) + "---\nbody\n", encoding="utf-8")
    return build_service_config(load_workflow(workflow))


def _verify_config(
    monkeypatch: pytest.MonkeyPatch, commands: dict[str, str], *, timeout_ms: int = 1000,
) -> None:
    monkeypatch.setattr(
        doctor, "verify_config",
        lambda cfg: SimpleNamespace(commands=commands, timeout_ms=timeout_ms),
    )


@pytest.mark.parametrize(
    ("command", "status", "message"),
    [
        ("missing-verify-command --check", "warn", "not on $PATH"),
        ("'quoted missing command' --check", "warn", "quoted missing command"),
        ("CHECK_MODE=yes missing-verify-command", "warn", "missing-verify-command"),
        ("printf '%s' done", "pass", "skipped"),
        ("if true; then false; fi", "pass", "skipped"),
        ("{ missing-verify-command; }", "pass", "skipped"),
    ],
)
def test_verify_path_check_does_not_execute_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    command: str, status: str, message: str,
) -> None:
    cfg = _config(tmp_path)
    _verify_config(monkeypatch, {"build": command})
    monkeypatch.setattr(doctor.shutil, "which", lambda binary: None)
    monkeypatch.setattr(
        doctor, "run_verify", lambda *a, **k: pytest.fail("PATH check executed command"),
    )

    result, = doctor.check_verify_commands(cfg)

    assert result.status == status
    assert message in result.message


def test_verify_path_check_resolves_quoted_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    _verify_config(monkeypatch, {"verify": f"{shlex.quote(sys.executable)} -m pytest"})

    result, = doctor.check_verify_commands(cfg)

    assert result.status == "pass"
    assert sys.executable in result.message


def test_verify_path_check_warns_on_bad_shell_quoting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    _verify_config(monkeypatch, {"build": "python 'unterminated"})

    result, = doctor.check_verify_commands(cfg)

    assert result.status == "warn"
    assert "not parseable" in result.message


def test_verify_path_check_preserves_windows_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    binary = r"C:\Program Files\Python\python.exe"
    _verify_config(monkeypatch, {"build": f'"{binary}" -m pytest'})
    monkeypatch.setattr(doctor, "_IS_WIN32", True)
    seen: list[str] = []

    def which(command: str) -> str:
        seen.append(command)
        return command

    monkeypatch.setattr(doctor.shutil, "which", which)

    result, = doctor.check_verify_commands(cfg)

    assert seen == [binary]
    assert result.status == "pass"


@pytest.mark.parametrize("flag", ["--permission-mode acceptEdits", "--permission-mode=acceptEdits"])
def test_claude_active_stage_warns_with_exact_project_allow_fix(
    tmp_path: Path, flag: str,
) -> None:
    cfg = _config(
        tmp_path,
        agent={"kind": "codex", "stage_kinds": {"Verify": "claude"}},
        claude={"command": f"claude -p {flag}"},
    )

    result = doctor.check_claude_bash_permissions(cfg)

    assert result.status == "warn"
    assert "Verify" in result.message
    assert '.claude/settings.json' in result.message
    assert '{"permissions":{"allow":["Bash(python3 -m pytest:*)","Bash(git merge-tree:*)"]}}' in result.message


@pytest.mark.parametrize("filename", ["settings.json", "settings.local.json"])
def test_claude_project_bash_allow_satisfies_check(tmp_path: Path, filename: str) -> None:
    cfg = _config(tmp_path, agent={"kind": "claude"})
    settings = tmp_path / ".claude" / filename
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(git status:*)"]}}))

    result = doctor.check_claude_bash_permissions(cfg)

    assert result.status == "pass"
    assert filename in result.message


@pytest.mark.parametrize("settings", ['{"permissions":{"allow":["Read"]}}', "[]", "broken"])
def test_claude_non_bash_or_invalid_project_settings_still_warns(
    tmp_path: Path, settings: str,
) -> None:
    cfg = _config(tmp_path, agent={"kind": "claude"})
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(settings)

    assert doctor.check_claude_bash_permissions(cfg).status == "warn"


def test_claude_default_does_not_warn_when_all_active_states_route_elsewhere(tmp_path: Path) -> None:
    cfg = _config(tmp_path, agent={"kind": "claude"})
    cfg = replace(cfg, agent=replace(cfg.agent, stage_kinds={
        state.lower(): "codex" for state in cfg.tracker.active_states
    }))

    assert doctor.check_claude_bash_permissions(cfg).status == "pass"


def test_claude_accept_edits_outside_active_states_does_not_warn(tmp_path: Path) -> None:
    cfg = _config(tmp_path, agent={"kind": "codex", "stage_kinds": {"Done": "claude"}})

    assert doctor.check_claude_bash_permissions(cfg).status == "pass"


def test_claude_other_permission_mode_does_not_warn(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path, agent={"kind": "claude"},
        claude={"command": "claude -p --permission-mode bypassPermissions"},
    )

    assert doctor.check_claude_bash_permissions(cfg).status == "pass"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True,
        capture_output=True, text=True,
    )


def _git_config(tmp_path: Path) -> ServiceConfig:
    cfg = _config(tmp_path)
    _git(tmp_path, "init", "--quiet")
    (tmp_path / "baseline.txt").write_text("committed\n")
    _git(tmp_path, "add", ".")
    tree = _git(tmp_path, "write-tree").stdout.strip()
    commit = _git(
        tmp_path, "-c", "user.name=Doctor Test", "-c", "user.email=doctor@test.invalid",
        "commit-tree", tree, "-m", "baseline",
    ).stdout.strip()
    _git(tmp_path, "update-ref", "HEAD", commit)
    return cfg


@pytest.mark.parametrize(("command", "status", "exit_code"), [
    ("printf success; printf changed > baseline.txt; touch scratch.txt", "pass", 0),
    ("printf failure-tail; printf changed > baseline.txt; touch scratch.txt; exit 7", "fail", 7),
    ("printf timeout-tail; sleep 10", "fail", None),
])
def test_run_verify_is_detached_head_once_and_always_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    command: str, status: str, exit_code: int | None,
) -> None:
    cfg = _git_config(tmp_path)
    before = _git(tmp_path, "worktree", "list", "--porcelain").stdout
    (tmp_path / "baseline.txt").write_text("host changes\n")
    (tmp_path / "untracked.txt").write_text("host only\n")
    _verify_config(monkeypatch, {"verify": command}, timeout_ms=100)
    run_verify = doctor.run_verify
    workspaces: list[Path] = []

    def inspect_run(command: str, workspace: Path, timeout_ms: int) -> object:
        workspaces.append(workspace)
        assert (workspace / "baseline.txt").read_text() == "committed\n"
        assert not (workspace / "untracked.txt").exists()
        assert _git(workspace, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "HEAD"
        return run_verify(command, workspace, timeout_ms)

    monkeypatch.setattr(doctor, "run_verify", inspect_run)

    result, = doctor.check_run_verify(cfg)

    assert result.status == status
    if exit_code is None:
        assert "exit_code=" in result.message
        assert "timed_out=true" in result.message
        assert "timeout-tail" in result.message
    else:
        assert f"exit_code={exit_code}" in result.message
        if exit_code:
            assert "failure-tail" in result.message
    assert len(workspaces) == 1
    assert not workspaces[0].exists()
    assert _git(tmp_path, "worktree", "list", "--porcelain").stdout == before
    assert (tmp_path / "baseline.txt").read_text() == "host changes\n"
    assert (tmp_path / "untracked.txt").read_text() == "host only\n"
    assert not (tmp_path / "scratch.txt").exists()


def test_run_verify_runs_later_commands_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _git_config(tmp_path)
    _verify_config(monkeypatch, {"build": "exit 3", "verify": "exit 0"})

    results = doctor.check_run_verify(cfg)

    assert [(row.name, row.status) for row in results] == [
        ("verify.run=build", "fail"), ("verify.run=verify", "pass"),
    ]


def test_run_checks_reads_raw_verify_config_without_executing(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    cfg = _config(
        tmp_path,
        tracker={"kind": "linear", "api_key": "test", "project_slug": "TEST"},
        workspace={"root": str(tmp_path / "workspaces")},
        codex={"command": shlex.quote(sys.executable)},
        verify={"commands": {" Build ": f"touch {shlex.quote(str(marker))}"}},
    )

    results = doctor.run_checks(cfg)

    assert not marker.exists()
    assert any(row.name == "verify.command=build" for row in results)
    assert not any(row.name.startswith("verify.run") for row in results)


def test_invalid_verify_config_reports_failure(tmp_path: Path) -> None:
    cfg = _config(tmp_path, verify={"timeout_ms": 0})

    result, = doctor.check_verify_commands(cfg)

    assert result.name == "verify.config"
    assert result.status == "fail"
    assert "positive integer" in result.message


def test_run_verify_without_commands_does_not_need_git(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    result, = doctor.check_run_verify(cfg)

    assert result.status == "pass"
    assert "no verification commands" in result.message


def test_run_verify_reports_worktree_creation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    _verify_config(monkeypatch, {"verify": "exit 0"})
    monkeypatch.setattr(doctor, "run_verify", lambda *a: pytest.fail("must not run without worktree"))

    result, = doctor.check_run_verify(cfg)

    assert result.name == "verify.worktree"
    assert result.status == "fail"
    assert "exit_code=128" in result.message


def test_run_verify_preserves_full_runner_output_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    _verify_config(monkeypatch, {"verify": "exit 7"})
    tail = "first tail line\n" + "x" * 3000 + "\nlast tail line\n"
    monkeypatch.setattr(doctor.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, stdout="", stderr="",
    ))
    monkeypatch.setattr(doctor, "run_verify", lambda *args: SimpleNamespace(
        passed=False, output=tail, exit_code=7, timed_out=False, duration_ms=1,
    ))

    result, = doctor.check_run_verify(cfg)

    assert result.status == "fail"
    assert tail.rstrip() in result.message


def test_run_verify_removes_worktree_when_runner_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _git_config(tmp_path)
    before = _git(tmp_path, "worktree", "list", "--porcelain").stdout
    _verify_config(monkeypatch, {"build": "first", "verify": "second"})
    calls: list[str] = []

    def fail_run(command: str, *args: object) -> object:
        calls.append(command)
        raise OSError("runner failed")

    monkeypatch.setattr(doctor, "run_verify", fail_run)

    results = doctor.check_run_verify(cfg)

    assert calls == ["first", "second"]
    assert len(results) == 2
    assert all(result.status == "fail" and "runner failed" in result.message for result in results)
    assert _git(tmp_path, "worktree", "list", "--porcelain").stdout == before


@pytest.mark.parametrize("run_flag", [False, True])
def test_doctor_cli_runs_verification_only_with_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_flag: bool,
) -> None:
    cfg = _config(tmp_path)
    calls: list[bool] = []

    def checks(cfg: ServiceConfig, host: str = "127.0.0.1", *, run_verification: bool = False) -> list[doctor.CheckResult]:
        calls.append(run_verification)
        return []

    monkeypatch.setattr(doctor, "run_checks", checks)

    code = doctor.main([str(cfg.workflow_path), "--no-color", *(["--run-verify"] if run_flag else [])])

    assert code == 0
    assert calls == [run_flag]
