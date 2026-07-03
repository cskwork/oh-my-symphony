from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "static_todo_browser_acceptance.py"
    spec = importlib.util.spec_from_file_location(
        "static_todo_browser_acceptance", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_launch_chromium_uses_isolated_writable_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    captured: dict = {}

    class FakeTemporaryDirectory:
        def __init__(self, prefix: str):
            assert prefix == "static-todo-browser-"

        def __enter__(self):
            return str(tmp_path)

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeBrowser:
        closed = False

        async def close(self):
            self.closed = True

    class FakeChromium:
        async def launch(self, **kwargs):
            captured.update(kwargs)
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", FakeTemporaryDirectory)

    async with module._launch_chromium(FakePlaywright()) as browser:
        assert isinstance(browser, FakeBrowser)
        assert captured["env"]["HOME"] == str(tmp_path)
        assert captured["env"]["XDG_CONFIG_HOME"] == str(tmp_path / ".config")
        assert captured["env"]["XDG_CACHE_HOME"] == str(tmp_path / ".cache")
        assert "--disable-crashpad" in captured["args"]

    assert browser.closed is True
