"""Contract: symphony-oneshot bootstrap seeds the vault skeleton the gate needs.

`reference/lanes.md` documents the Deliver gate as literal bash that greps the
vault before a ticket may close. The Brief/Plan lanes only ever populate those
files; bootstrap.sh must create them up front so the gate has something to
check. This test pins that promise.

`bootstrap.sh` hard-requires the `symphony` CLI on PATH (it exits non-zero
otherwise). When the CLI is available we run the script hermetically in a tmp
dir and assert the real skeleton on disk. When it is not, we assert the same
skeleton statically from the script body. Either way: no network, no agent CLI.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from symphony._shell import resolve_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
ONESHOT = REPO_ROOT / "skills" / "symphony-oneshot"
BOOTSTRAP = ONESHOT / "templates" / "bootstrap.sh"
LANES = ONESHOT / "reference" / "lanes.md"
WORKFLOW = ONESHOT / "templates" / "WORKFLOW.oneshot.md"


def _deliver_gate_vault_files() -> list[str]:
    """Parse the Deliver-gate bash block in lanes.md for the vault files it
    unconditionally requires (the `test -s .oneshot/vault/...` lines outside the
    `.is_browser_app` branch). Deriving these from the doc keeps the test honest:
    if the gate's required skeleton changes, this test must see it too.
    """
    text = LANES.read_text(encoding="utf-8")
    match = re.search(
        r"## The Deliver gate.*?```bash\n(.*?)\n```", text, re.DOTALL
    )
    assert match, "could not locate the Deliver gate bash block in lanes.md"
    gate = match.group(1)

    required: list[str] = []
    in_browser_branch = False
    for line in gate.splitlines():
        stripped = line.strip()
        if stripped.startswith("if [") and ".is_browser_app" in stripped:
            in_browser_branch = True
            continue
        if stripped == "fi":
            in_browser_branch = False
            continue
        if in_browser_branch:
            continue  # browser-only artifacts are produced later, not by bootstrap
        m = re.match(r"test -s (\.oneshot/vault/\S+)", stripped)
        if m:
            required.append(m.group(1))
    assert required, "no unconditional vault files parsed from the Deliver gate"
    return required


def _stub_agent_cli(bin_dir: Path, name: str = "claude") -> None:
    """Put a no-op executable named ``name`` on PATH.

    bootstrap.sh's final step runs ``symphony doctor``, whose ``check_agent_cli``
    fails (exit 1) when the configured agent binary (``claude`` by default) is
    not on ``$PATH`` — so on CI, where no agent CLI is installed, bootstrap
    exits 1 and never reaches the skeleton this test pins. The stub satisfies
    that ``shutil.which`` lookup without a real agent; it is never executed
    (doctor only resolves the path), keeping the run hermetic — no agent CLI.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        (bin_dir / f"{name}.cmd").write_text("@echo off\nexit /b 0\n", encoding="utf-8")
    else:
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)


def _hermetic_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    project.mkdir()
    # Make the dir look like the oh-my-symphony repo so the script's warn guard
    # passes; the marker file is enough for its `grep -qE name = "...symphony"`.
    (project / "pyproject.toml").write_text(
        '[project]\nname = "oh-my-symphony"\n', encoding="utf-8"
    )

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(fake_home)  # keep ~/symphony_workspaces inside the sandbox

    # Doctor's agent-CLI check needs `claude` on PATH; CI has no agent CLI, so
    # stub one (never invoked) and prepend it so the run stays self-contained.
    stub_bin = tmp_path / "bin"
    _stub_agent_cli(stub_bin)
    env["PATH"] = str(stub_bin) + os.pathsep + env.get("PATH", "")
    return project, env


def test_bootstrap_script_exists() -> None:
    assert BOOTSTRAP.is_file(), f"missing bootstrap script: {BOOTSTRAP}"


def test_plan_lane_requires_self_contained_ticket_descriptions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "The board description is the worker prompt" in workflow
    assert "Acceptance criteria:" in workflow
    assert "Verification:" in workflow
    assert "Done evidence:" in workflow
    assert "Read .oneshot/vault/plan.md §BUILD-1 for the spec." not in workflow


@pytest.mark.skipif(
    shutil.which("symphony") is not None,
    reason="symphony CLI present — covered by the hermetic-run test instead",
)
def test_bootstrap_seeds_gate_files_statically() -> None:
    """Without the CLI, assert the script body creates every gate file."""
    body = BOOTSTRAP.read_text(encoding="utf-8")
    for vault_file in _deliver_gate_vault_files():
        # bootstrap writes to $PROJECT_ROOT/<vault_file>; match the tail.
        assert vault_file in body, (
            f"bootstrap.sh never creates Deliver-gate file {vault_file!r}"
        )


@pytest.mark.skipif(
    shutil.which("symphony") is None,
    reason="symphony CLI not on PATH — cannot run bootstrap end to end",
)
def test_bootstrap_creates_vault_skeleton(tmp_path: Path) -> None:
    """With the CLI, run bootstrap hermetically and assert the real skeleton."""
    project, env = _hermetic_project(tmp_path)

    result = subprocess.run(
        # resolve_bash(): on Windows, a bare "bash" can resolve to the WSL
        # launcher, which can't open the Windows-drive script path; use the
        # same MSYS/Git bash the product spawns with. as_posix(): a backslash
        # path makes bash collapse the separators (rc 127) before the script
        # runs. On Linux/CI both are the prior behavior (plain "bash", "/"-paths).
        [resolve_bash(), BOOTSTRAP.as_posix(), "test prompt for hermetic bootstrap"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"bootstrap.sh exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    vault = project / ".oneshot" / "vault"
    assert vault.is_dir(), f"vault dir not created: {vault}"
    for vault_file in _deliver_gate_vault_files():
        # vault_file is repo-relative (".oneshot/vault/..."); resolve under project.
        path = project / vault_file
        assert path.is_file(), f"bootstrap did not create gate file {path}"

    workflow_text = (project / "WORKFLOW.md").read_text(encoding="utf-8")
    system_text = (project / ".oneshot" / "SYSTEM.md").read_text(encoding="utf-8")
    assert "__ONESHOT_ROOT__" not in workflow_text
    assert "__ONESHOT_PORT__" not in workflow_text
    assert "__ONESHOT_PORT__" not in system_text

    match = re.search(r"(?m)^  port: (\d+)$", workflow_text)
    assert match, "generated WORKFLOW.md did not contain a numeric server port"
    port = match.group(1)
    assert f"127.0.0.1:{port}/api/v1/..." in system_text


@pytest.mark.skipif(
    shutil.which("symphony") is None,
    reason="symphony CLI not on PATH — cannot run bootstrap end to end",
)
def test_bootstrap_rejects_explicit_busy_port(tmp_path: Path) -> None:
    """An operator-selected occupied port must fail before writing a bad workflow."""
    project, env = _hermetic_project(tmp_path)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = str(sock.getsockname()[1])
        env["SYMPHONY_ONESHOT_PORT"] = port

        result = subprocess.run(
            [resolve_bash(), BOOTSTRAP.as_posix(), "test prompt"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    assert result.returncode == 1
    assert f"requested SYMPHONY_ONESHOT_PORT={port} is not available" in result.stderr
    assert not (project / "WORKFLOW.md").exists()
    assert not (project / ".oneshot").exists()
