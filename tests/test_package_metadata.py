from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_declares_pep639_license_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert "setuptools>=77" in config["build-system"]["requires"]
    assert config["project"]["license"] == "Apache-2.0"
    assert config["project"]["license-files"] == ["LICENSE", "NOTICE"]
