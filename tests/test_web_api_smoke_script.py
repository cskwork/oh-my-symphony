from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any, AsyncIterator, cast

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState


WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress", Verify, Learn]
  terminal_states: ["Human Review", Done, Blocked, Archive]
  state_descriptions:
    Todo: "Triage"
    "Human Review": "Human confirmation"
    Done: "Complete"
    Blocked: "Blocked"
    Archive: "Archived"

agent:
  kind: codex

prompts:
  stages:
    Todo: ./prompts/stages/todo.md
    "In Progress": ./prompts/stages/in-progress.md
    Verify: ./prompts/stages/verify.md
    Learn: ./prompts/stages/learn.md
---

Smoke prompt for {{ issue.identifier }}.
"""


class _StubOrchestrator:
    def __init__(self, workflow_state: WorkflowState) -> None:
        self._workflow_state = workflow_state
        self.refresh_calls = 0

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": "2026-07-02T00:00:00Z",
            "counts": {"running": 0, "retrying": 0},
            "running": [],
            "retrying": [],
            "codex_totals": {
                "input_tokens": 0,
                "cache_input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "seconds_running": 0,
            },
            "rate_limits": None,
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "degraded_reasons": [],
            "version": "test",
            "generated_at": "2026-07-03T00:00:00Z",
        }

    def issue_snapshot(self, _identifier: str) -> dict[str, Any] | None:
        return None

    def request_refresh(self) -> bool:
        self.refresh_calls += 1
        return False

    def find_running_issue_id(self, _identifier: str) -> str | None:
        return None

    def iter_running_issues(self) -> tuple[Any, ...]:
        return ()

    def issue_attention(self, _issue: Any) -> dict[str, str] | None:
        return None


def _load_smoke_module():
    path = Path("scripts/smoke_web_api.py")
    spec = importlib.util.spec_from_file_location("smoke_web_api", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    stages = tmp_path / "prompts" / "stages"
    stages.mkdir(parents=True)
    for name in ("todo", "in-progress", "verify", "learn"):
        (stages / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")
    (tmp_path / "kanban").mkdir()
    return tmp_path


@pytest_asyncio.fixture()
async def web_base_url(board_dir: Path) -> AsyncIterator[str]:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield str(client.make_url("/")).rstrip("/")
    finally:
        await client.close()


async def test_smoke_web_api_runs_against_test_server(web_base_url: str) -> None:
    smoke = _load_smoke_module()
    checks = await asyncio.to_thread(smoke.run_smoke, web_base_url, prefix="SMOKET")
    assert [c.name for c in checks] == [
        "health",
        "state",
        "board",
        "static assets",
        "issue create",
        "issue detail",
        "issue patch",
        "refresh",
        "workflow stats skills",
    ]


def test_smoke_degraded_health_reports_reasons(monkeypatch) -> None:
    smoke = _load_smoke_module()

    def fake_request(
        _base_url: str,
        _method: str,
        path: str,
        _body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if path == "/api/v1/health":
            return 200, {"status": "degraded", "degraded_reasons": ["tick_failures"]}
        return 200, {}

    monkeypatch.setattr(smoke, "request", fake_request)

    with pytest.raises(smoke.SmokeFailure) as exc:
        smoke.run_smoke("http://127.0.0.1:9999")

    message = str(exc.value)
    assert "GET /api/v1/health" in message
    assert "tick_failures" in message
    assert "Next step:" in message
