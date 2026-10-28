"""Core keeps working when the optional operator chat distribution is absent.

`oh-my-symphony-chat` (packages/chat) contributes `symphony.chat`. These tests
make that package unimportable in-process; CI also runs a core-only wheel smoke.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from aiohttp.test_utils import TestClient, TestServer

import symphony
from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState
from tests.test_webapi import _StubOrchestrator, board_dir  # noqa: F401  (fixture)
from tests.test_webapi_chat import CONFIRMATION_TOKEN, _FakeBackend, fake_backends  # noqa: F401

STATIC_ROOT = Path(symphony.__file__).parent / "web" / "static"


@pytest.fixture()
def without_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(sys.modules):
        if name == "symphony.chat" or name.startswith("symphony.chat."):
            monkeypatch.delitem(sys.modules, name)
    # A None entry makes both `import symphony.chat` and find_spec report absence.
    monkeypatch.setitem(sys.modules, "symphony.chat", None)
    assert not symphony.component_installed("chat")


def test_chat_installed_reports_the_editable_dev_install() -> None:
    assert symphony.component_installed("chat")
    assert symphony.installed_components()["chat"] is True


def _app(board_dir: Path):  # noqa: F811
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    return build_app(cast(Orchestrator, _StubOrchestrator(state)))


async def test_web_app_starts_without_chat_routes_and_reports_availability(
    without_chat: None, board_dir: Path  # noqa: F811
) -> None:
    app = _app(board_dir)
    paths = {resource.canonical for resource in app.router.resources()}
    assert not any(path.startswith("/api/v1/chat") for path in paths)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        board = await client.get("/api/v1/board")
        assert board.status == 200
        assert (await board.json())["board"]["components"]["chat"] is False
        assert (await client.get("/")).status == 200
        assert (await client.get("/api/v1/chat/sessions")).status == 404
        assert (await client.get("/api/v1/chat/session")).status == 404
        assert (await client.get("/api/v1/chat/ws")).status == 404
        # Unrelated core routes stay registered.
        assert (await client.get("/api/v1/projects")).status == 200
    finally:
        await client.close()


async def test_web_app_registers_chat_routes_when_installed(
    board_dir: Path, fake_backends: list[_FakeBackend]  # noqa: F811
) -> None:
    client = TestClient(TestServer(_app(board_dir)))
    await client.start_server()
    try:
        board = await (await client.get("/api/v1/board")).json()
        assert board["board"]["components"]["chat"] is True
        created = await client.post(
            "/api/v1/chat/sessions",
            json={"mode": "qa", "confirmation_token": CONFIRMATION_TOKEN},
        )
        assert created.status == 201, await created.text()
        session_id = (await created.json())["session_id"]
        listing = await (await client.get("/api/v1/chat/sessions")).json()
        assert [row["session_id"] for row in listing["sessions"]] == [session_id]
        assert len(fake_backends) == 1
    finally:
        await client.close()


def test_spa_gates_the_chat_page_on_the_component() -> None:
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    route = js[js.index("async function renderChatRoute(view)"):]
    route = route[: route.index("\n  }\n")]
    # The availability check must run before anything opens the WebSocket.
    assert route.index("applyChatAvailability(board)") < route.index("renderChatPage(view)")
    assert "components.chat === false" in js
    assert "case 'chat':\n        renderChatRoute(view);" in js
    assert "applyChatAvailability(board);" in js[js.index("async function pollBoard()"):]


def test_core_paths_do_not_import_chat(tmp_path: Path) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n---\nWork on {{ issue.identifier }}.\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban").mkdir()
    code = (
        "import sys\n"
        "import symphony.orchestrator, symphony.orchestrator.sweeps, symphony.webapi\n"
        "import symphony.cli.main, symphony.tui, symphony.server\n"
        "from symphony.workflow import WorkflowState\n"
        f"cfg, err = WorkflowState({str(workflow)!r}).reload()\n"
        "assert err is None, err\n"
        "print(sorted(name for name in sys.modules if name.startswith('symphony.chat')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
