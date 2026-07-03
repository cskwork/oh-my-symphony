from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator, cast

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState

pytestmark = [
    pytest.mark.browser_e2e,
    pytest.mark.skipif(
        os.environ.get("SYMPHONY_BROWSER_E2E") != "1",
        reason="set SYMPHONY_BROWSER_E2E=1 to run browser E2E",
    ),
]

if os.environ.get("SYMPHONY_BROWSER_E2E") == "1":
    playwright = pytest.importorskip("playwright.async_api")
    async_playwright = playwright.async_playwright
else:
    async_playwright = None


WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress", Verify, Learn]
  terminal_states: ["Human Review", Done, Blocked, Archive]
  state_descriptions:
    Todo: "Triage"
    "In Progress": "Plan + implement"
    Verify: "Review + QA"
    Learn: "Wiki write-back"
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

QA prompt for {{ issue.identifier }}.
"""


class _StubOrchestrator:
    def __init__(self, workflow_state: WorkflowState) -> None:
        self._workflow_state = workflow_state

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

    def issue_snapshot(self, _identifier: str) -> dict[str, Any] | None:
        return None

    def recent_runs(
        self, issue_id: str | None = None, limit: int = 50
    ) -> tuple[list[dict[str, Any]], str | None]:
        del issue_id, limit
        return [], None

    def request_refresh(self) -> bool:
        return False

    def find_running_issue_id(self, _identifier: str) -> str | None:
        return None

    def iter_running_issues(self) -> tuple[Any, ...]:
        return ()

    def issue_attention(self, _issue: Any) -> dict[str, str] | None:
        return None


def _ticket(identifier: str, title: str, state: str, priority: int = 2) -> str:
    return f"""---
id: {identifier}
identifier: {identifier}
title: {title}
state: {state}
priority: {priority}
labels:
- e2e
created_at: '2026-07-02T00:00:00Z'
updated_at: '2026-07-02T00:00:00Z'
---

Seed body for {identifier}.
"""


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    stages = tmp_path / "prompts" / "stages"
    stages.mkdir(parents=True)
    for name in ("todo", "in-progress", "verify", "learn"):
        (stages / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")

    kanban = tmp_path / "kanban"
    kanban.mkdir()
    seeds = (
        ("SEED-REVIEW", "Seed human review card", "Human Review", 2),
        ("SEED-DONE", "Seed done card", "Done", 3),
        ("SEED-BLOCKED", "Seed blocked card", "Blocked", 1),
    )
    for identifier, title, state, priority in seeds:
        (kanban / f"{identifier}.md").write_text(
            _ticket(identifier, title, state, priority),
            encoding="utf-8",
        )
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


async def _column_titles(page: Any) -> list[str]:
    return await page.locator(
        ".board-columns > .column .column-header .column-title"
    ).evaluate_all("(nodes) => nodes.map((n) => n.textContent.trim())")


async def _assert_no_document_overflow(page: Any, label: str) -> None:
    dims = await page.evaluate(
        """() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
        })"""
    )
    assert dims["scrollWidth"] <= dims["clientWidth"] + 2, (label, dims)


async def _assert_no_element_overflow(page: Any, selector: str, label: str) -> None:
    dims = await page.locator(selector).evaluate(
        """(node) => ({
            scrollWidth: node.scrollWidth,
            clientWidth: node.clientWidth,
        })"""
    )
    assert dims["scrollWidth"] <= dims["clientWidth"] + 2, (label, dims)


async def _exercise_column_scope(page: Any, web_base_url: str) -> None:
    await page.goto(f"{web_base_url}/#/board", wait_until="networkidle")
    await page.locator(".board-columns > .column").first.wait_for()
    assert await _column_titles(page) == ["Todo", "In Progress", "Verify", "Learn"]

    terminal_text = await page.locator(".terminal-section").inner_text()
    assert "Human Review" in terminal_text
    assert "Done" in terminal_text
    assert "Blocked" in terminal_text
    assert "Seed human review card" in terminal_text
    await _assert_no_document_overflow(page, "desktop active")

    await page.get_by_role("button", name="All").click()
    await page.wait_for_function(
        "() => document.querySelectorAll('.board-columns > .column').length === 8"
    )
    assert await _column_titles(page) == [
        "Todo",
        "In Progress",
        "Verify",
        "Learn",
        "Human Review",
        "Done",
        "Blocked",
        "Archive",
    ]
    assert await page.locator(".terminal-section").count() == 0
    await page.get_by_role("button", name="Active").click()


async def _exercise_issue_crud(page: Any) -> None:
    title = "UI browser E2E card"
    await page.get_by_role("button", name="+ New Issue").click()
    modal = page.locator(".modal-form").last
    await modal.get_by_label("Title").fill(title)
    await modal.get_by_label("Description").fill("Created by browser E2E.")
    await modal.get_by_label("State").select_option("Human Review")
    await modal.get_by_label("Labels").fill("browser, e2e")
    await modal.get_by_label("ID prefix").fill("UIE2E")
    await modal.get_by_role("button", name="Create issue").click()
    await page.locator(".card", has_text=title).wait_for()

    await page.locator(".card", has_text=title).click()
    drawer = page.locator("#drawer-panel")
    await drawer.locator(".drawer-title-input").fill(f"{title} updated")
    await drawer.locator(".drawer-title-input").press("Enter")
    await page.locator(".card", has_text=f"{title} updated").wait_for()

    await drawer.get_by_role("button", name="Delete issue").click()
    await page.locator(".modal-form").last.get_by_role("button", name="Delete").click()
    await page.locator(".card", has_text=f"{title} updated").wait_for(
        state="detached"
    )


async def _exercise_mobile_layout(page: Any, web_base_url: str) -> None:
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(f"{web_base_url}/#/board", wait_until="networkidle")
    await page.locator(".board-columns > .column").first.wait_for()
    await page.locator(".mobile-lane-tabs").wait_for()
    assert await page.locator(".board-columns > .column").count() == 1
    assert await page.locator(".add-column-ghost").count() == 0
    await page.get_by_role("tab", name="Learn").click()
    assert await _column_titles(page) == ["Learn"]
    await _assert_no_document_overflow(page, "mobile active")
    await _assert_no_element_overflow(page, "#board-scroll", "mobile lane tabs")


async def test_web_board_browser_e2e(web_base_url: str) -> None:
    assert async_playwright is not None
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        try:
            await _exercise_column_scope(page, web_base_url)
            await _exercise_issue_crud(page)
            await _exercise_mobile_layout(page, web_base_url)
            assert errors == []
        finally:
            await browser.close()
