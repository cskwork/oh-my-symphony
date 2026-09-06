"""The printable manual's key/command tables must match the running code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "sync_manual_tables.py"


def test_manual_tables_are_in_sync_with_help_sections_and_subcommands() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_manual_html_and_css_exist_for_both_languages() -> None:
    manual = ROOT / "docs" / "manual"
    assert (manual / "manual.css").is_file()
    for lang in ("en", "ko"):
        for name in ("cheatsheet", "tutorial"):
            path = manual / lang / f"{name}.html"
            assert path.is_file(), path
            text = path.read_text(encoding="utf-8")
            assert f'lang="{lang}"' in text, f"{path} must declare lang={lang}"
            assert "../manual.css" in text, f"{path} must link the shared stylesheet"
