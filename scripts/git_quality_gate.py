#!/usr/bin/env python3
"""Local Git hook gates that mirror the CI test environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


GATE_VERSION = "2026-07-03.1"


@dataclass(frozen=True)
class GateCommand:
    label: str
    argv: tuple[str, ...]


def _quote(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key == "GIT_CONFIG_GLOBAL" or not key.startswith("GIT_"):
            continue
        env.pop(key, None)
    return env


def _run(command: GateCommand, *, cwd: Path) -> None:
    print(f"\n==> {command.label}", file=sys.stderr)
    print(f"+ {_quote(command.argv)}", file=sys.stderr)
    subprocess.run(command.argv, cwd=cwd, check=True, env=_subprocess_env())


def _git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env=_subprocess_env(),
    )
    return result.stdout.strip()


def repo_root() -> Path:
    return Path(_git_output(Path.cwd(), "rev-parse", "--show-toplevel"))


def git_path(root: Path, relative_path: str) -> Path:
    resolved = Path(_git_output(root, "rev-parse", "--git-path", relative_path))
    return resolved if resolved.is_absolute() else root / resolved


def staged_python_files(root: Path) -> tuple[str, ...]:
    output = _git_output(
        root,
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACMR",
    )
    return tuple(line for line in output.splitlines() if line.endswith(".py"))


def pre_commit_commands(root: Path, *, python: str) -> tuple[GateCommand, ...]:
    commands = [
        GateCommand("staged whitespace check", ("git", "diff", "--cached", "--check"))
    ]
    python_files = staged_python_files(root)
    if python_files:
        commands.append(
            GateCommand(
                "staged Python syntax check",
                (python, "-m", "py_compile", *python_files),
            )
        )
    return tuple(commands)


def pre_push_commands(*, python: str) -> tuple[GateCommand, ...]:
    return (
        GateCommand("working tree whitespace check", ("git", "diff", "--check")),
        GateCommand("staged whitespace check", ("git", "diff", "--cached", "--check")),
        GateCommand("CI-parity full pytest", (python, "-m", "pytest", "-q")),
    )


def _gate_stamp(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    payload = {
        "gate_version": GATE_VERSION,
        "python": sys.version,
        "pyproject_sha256": hashlib.sha256(pyproject.read_bytes()).hexdigest(),
    }
    return json.dumps(payload, sort_keys=True)


def ensure_ci_venv(root: Path) -> Path:
    venv_dir = git_path(root, "symphony-quality/ci-dev-venv")
    stamp_path = git_path(root, "symphony-quality/ci-dev-venv.stamp")
    python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    stamp = _gate_stamp(root)
    if python.exists() and stamp_path.exists() and stamp_path.read_text() == stamp:
        return python

    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    _run(GateCommand("upgrade CI-parity pip", (str(python), "-m", "pip", "install", "--upgrade", "pip")), cwd=root)
    _run(GateCommand("install CI-parity dev extra", (str(python), "-m", "pip", "install", "-e", ".[dev]")), cwd=root)
    stamp_path.write_text(stamp, encoding="utf-8")
    return python


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("pre-commit", "pre-push"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = repo_root()
    if args.stage == "pre-commit":
        commands = pre_commit_commands(root, python=sys.executable)
    else:
        ci_python = "<ci-dev-venv-python>" if args.dry_run else str(ensure_ci_venv(root))
        commands = pre_push_commands(python=ci_python)

    for command in commands:
        if args.dry_run:
            print(f"{command.label}: {_quote(command.argv)}")
        else:
            _run(command, cwd=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
