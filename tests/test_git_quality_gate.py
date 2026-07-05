"""Tests for local Git hook quality gates."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "git_quality_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("git_quality_gate", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, check=True, stdout=subprocess.PIPE)


def test_pre_commit_checks_staged_whitespace_and_python_syntax(tmp_path: Path) -> None:
    module = _load_module()
    _git(tmp_path, "init")
    script = tmp_path / "example.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    _git(tmp_path, "add", "example.py")

    commands = module.pre_commit_commands(tmp_path, python="pythonX")

    assert [command.label for command in commands] == [
        "staged whitespace check",
        "staged Python syntax check",
    ]
    assert commands[0].argv == ("git", "diff", "--cached", "--check")
    assert commands[1].argv == ("pythonX", "-m", "py_compile", "example.py")


def test_pre_commit_skips_python_syntax_without_staged_python(tmp_path: Path) -> None:
    module = _load_module()
    _git(tmp_path, "init")
    readme = tmp_path / "README.md"
    readme.write_text("docs\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")

    commands = module.pre_commit_commands(tmp_path, python="pythonX")

    assert tuple(command.label for command in commands) == ("staged whitespace check",)


def test_pre_push_runs_whitespace_and_ci_parity_full_pytest() -> None:
    module = _load_module()

    commands = module.pre_push_commands(python="/tmp/ci/bin/python")

    assert [command.argv for command in commands] == [
        ("git", "diff", "--check"),
        ("git", "diff", "--cached", "--check"),
        ("/tmp/ci/bin/python", "-m", "pytest", "-q"),
    ]


def test_hook_subprocess_env_strips_git_local_vars(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("GIT_DIR", "/tmp/parent/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/tmp/parent")
    monkeypatch.setenv("GIT_INDEX_FILE", "/tmp/parent/index")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/tmp/gitconfig")

    env = module._subprocess_env()

    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env
    assert env["GIT_CONFIG_GLOBAL"] == "/tmp/gitconfig"
