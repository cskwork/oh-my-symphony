#!/usr/bin/env python3
"""Build and inspect Symphony from an isolated source copy."""

from __future__ import annotations

import argparse
import email
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


IGNORED_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".symphony",
    "__pycache__",
    "build",
    "dist",
    "kanban",
    "log",
}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_NAMES
        or name.endswith((".egg-info", ".pyc"))
        or name in {".coverage", "WORKFLOW-PROGRESS.md"}
    }


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n{completed.stdout}"
        )
    return completed.stdout


def _one(paths: list[Path], label: str) -> Path:
    if len(paths) != 1:
        raise AssertionError(f"expected one {label}, got {[str(path) for path in paths]}")
    return paths[0]


def _inspect_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = _one(
            [Path(name) for name in names if name.endswith(".dist-info/METADATA")],
            "wheel METADATA",
        ).as_posix()
        metadata = email.message_from_bytes(archive.read(metadata_name))
        if metadata.get("License-Expression") != "Apache-2.0":
            raise AssertionError("wheel License-Expression is not Apache-2.0")
        legal_basenames = {
            Path(name).name
            for name in names
            if ".dist-info/licenses/" in name
        }
        if not {"LICENSE", "NOTICE"}.issubset(legal_basenames):
            raise AssertionError(f"wheel legal files missing: {legal_basenames}")


def _inspect_sdist(sdist: Path) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        basenames = {Path(name).name for name in archive.getnames()}
    if not {"LICENSE", "NOTICE"}.issubset(basenames):
        raise AssertionError(f"sdist legal files missing: {basenames}")


def _static_resource_probe(python: Path, cwd: Path) -> None:
    code = (
        "from importlib.resources import files; "
        "p = files('symphony').joinpath('web/static/index.html'); "
        "assert p.is_file(), p"
    )
    _run([str(python), "-c", code], cwd=cwd)


def _build_artifacts(source: Path, temp_root: Path) -> tuple[Path, Path, Path]:
    copied = temp_root / "source"
    shutil.copytree(source, copied, symlinks=True, ignore=_ignore)
    cache = temp_root / "uv-cache"
    dist = temp_root / "dist"
    output = _run(
        [
            "uv",
            "build",
            "--cache-dir",
            str(cache),
            "--out-dir",
            str(dist),
            "--no-create-gitignore",
        ],
        cwd=copied,
    )
    if "SetuptoolsDeprecationWarning" in output:
        raise AssertionError(f"setuptools deprecation warning:\n{output}")
    wheel = _one(sorted(dist.glob("*.whl")), "wheel")
    sdist = _one(sorted(dist.glob("*.tar.gz")), "sdist")
    _inspect_wheel(wheel)
    _inspect_sdist(sdist)
    return wheel, sdist, cache


def _install_and_probe(wheel: Path, cache: Path, temp_root: Path) -> None:
    venv = temp_root / "venv"
    _run([sys.executable, "-m", "venv", str(venv)], cwd=temp_root)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    symphony = venv / ("Scripts/symphony.exe" if os.name == "nt" else "bin/symphony")
    _run(
        [
            "uv",
            "pip",
            "install",
            "--cache-dir",
            str(cache),
            "--python",
            str(python),
            str(wheel),
        ],
        cwd=temp_root,
    )
    _run([str(symphony), "--help"], cwd=temp_root)
    _run([str(symphony), "--version"], cwd=temp_root)
    _static_resource_probe(python, temp_root)


def _result(wheel: Path, sdist: Path) -> dict[str, object]:
    return {
        "wheel": wheel.name,
        "sdist": sdist.name,
        "license_expression": "Apache-2.0",
        "legal_files": ["LICENSE", "NOTICE"],
        "installed_cli": True,
        "static_resource": True,
        "source_tree_pollution": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path.cwd())
    args = parser.parse_args()
    source = args.source.resolve()
    before_egg_info = sorted(source.glob("src/*.egg-info"))
    temp_root = Path(tempfile.mkdtemp(prefix="symphony-package-", dir="/private/tmp"))
    try:
        wheel, sdist, cache = _build_artifacts(source, temp_root)
        _install_and_probe(wheel, cache, temp_root)
        after_egg_info = sorted(source.glob("src/*.egg-info"))
        if after_egg_info != before_egg_info:
            raise AssertionError("package build polluted the real source tree")
        print(json.dumps(_result(wheel, sdist), indent=2, sort_keys=True))
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
