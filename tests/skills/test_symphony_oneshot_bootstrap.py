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
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ONESHOT = REPO_ROOT / "skills" / "symphony-oneshot"
BOOTSTRAP = ONESHOT / "templates" / "bootstrap.sh"
LANES = ONESHOT / "reference" / "lanes.md"


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


def test_bootstrap_script_exists() -> None:
    assert BOOTSTRAP.is_file(), f"missing bootstrap script: {BOOTSTRAP}"


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

    result = subprocess.run(
        ["bash", str(BOOTSTRAP), "test prompt for hermetic bootstrap"],
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
