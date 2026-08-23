from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony import chat as chat_module
from symphony import webapi as webapi_module
from symphony.backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_STARTED,
    BackendInit,
    TurnResult,
)
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
  active_states: [Todo, "In Progress", Verify, Document]
  terminal_states: ["Human Review", Done, Blocked, Archive]
  state_descriptions:
    Todo: "Triage"
    "In Progress": "Plan + implement"
    Verify: "Review + QA"
    Document: "Docs + wiki write-back"
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
    Document: ./prompts/stages/document.md
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

    async def recent_runs(
        self,
        issue_id: str | None = None,
        limit: int = 50,
        *,
        query: str | None = None,
        status: str | None = None,
        agent: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        del issue_id, limit, query, status, agent
        return [self._run_summary()], None

    @staticmethod
    def _run_summary() -> dict[str, Any]:
        return {
            "run_id": "a" * 32,
            "issue_id": "id-SEED-DONE",
            "identifier": "SEED-DONE",
            "title": "Seed done card",
            "state": "Done",
            "attempt": None,
            "attempt_kind": "initial",
            "agent_kind": "codex",
            "status": "normal",
            "started_at": "2026-07-02T00:00:00+00:00",
            "updated_at": "2026-07-02T00:00:05+00:00",
            "completed_at": "2026-07-02T00:00:05+00:00",
            "workspace_path": "/tmp/SEED-DONE",
            "branch_name": "symphony/SEED-DONE",
            "commit_sha": "b" * 40,
            "tokens": {"input": 10, "cache": 2, "output": 3, "total": 15},
            "failure_class": None,
            "failure_message": None,
        }

    async def run_detail(self, run_id: str) -> tuple[dict[str, Any] | None, str | None]:
        if run_id != "a" * 32:
            return None, None
        return {
            "run": self._run_summary(),
            "events": [
                {
                    "event_id": 1,
                    "event_type": "run_acquired",
                    "created_at": "2026-07-02T00:00:00+00:00",
                    "payload": {"agent_kind": "codex", "state": "Done"},
                },
                {
                    "event_id": 2,
                    "event_type": "run_completed",
                    "created_at": "2026-07-02T00:00:05+00:00",
                    "payload": {"status": "normal", "total_tokens": 15},
                },
            ],
        }, None

    async def run_diagnostic(self, run_id: str) -> tuple[dict[str, Any] | None, str | None]:
        detail, error = await self.run_detail(run_id)
        return ({"schema_version": 1, **detail} if detail else None), error

    def request_refresh(self) -> bool:
        return False

    def continuous_improvement_status(self) -> dict[str, Any]:
        return {
            "turns_used": 0,
            "in_flight": False,
            "current_phase": None,
            "last_result": None,
            "skipped_reason": None,
            "tickets_created": 0,
            "next_due_at": None,
        }

    def find_running_issue_id(self, _identifier: str) -> str | None:
        return None

    def iter_running_issues(self) -> tuple[Any, ...]:
        return ()

    def issue_attention(self, _issue: Any) -> dict[str, str] | None:
        return None

    def schedule_snapshot(self) -> dict[str, Any]:
        common = {
            "starvation_promoted": False,
            "retry": None,
            "evaluated_updated_at": "2026-07-02T00:00:00+00:00",
        }
        return {
            "schema_version": 1,
            "available": True,
            "reason": None,
            "generated_at": "2026-07-02T00:00:00Z",
            "stale": False,
            "policy": "dag",
            "policy_order": "starvation, priority, longest_dependency_chain, registration",
            "slots": {
                "running": 0,
                "maximum": 2,
                "available_before": 2,
                "available_after": 2,
            },
            "entries": [
                {
                    **common,
                    "identifier": "E2E-PLAN",
                    "evaluated_state": "Todo",
                    "status": "ready",
                    "code": "ready",
                    "reason": "eligible",
                    "queue_rank": 1,
                    "scan_position": 1,
                    "wave": 0,
                    "critical_path_length": 1,
                },
                {
                    **common,
                    "identifier": "E2E-BUILD",
                    "evaluated_state": "In Progress",
                    "status": "waiting",
                    "code": "waiting_dependency",
                    "reason": "blocked",
                    "queue_rank": None,
                    "scan_position": 2,
                    "wave": 1,
                    "critical_path_length": 0,
                },
            ],
        }

    def dependency_state_resolved(self, state: str | None) -> bool:
        return (state or "").strip().lower() == "done"


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
    for name in ("todo", "in-progress", "verify", "document"):
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
    (kanban / "E2E-PLAN.md").write_text(
        _ticket("E2E-PLAN", "Plan request", "Todo", 1).replace(
            "labels:\n", "request: E2E-REQ\nlabels:\n"
        ),
        encoding="utf-8",
    )
    (kanban / "E2E-BUILD.md").write_text(
        _ticket("E2E-BUILD", "Build request", "In Progress", 1).replace(
            "labels:\n",
            "request: E2E-REQ\nblocked_by: [E2E-PLAN]\nlabels:\n",
        ),
        encoding="utf-8",
    )
    for identifier, blocker in (
        ("E2E-CYCLE-A", "E2E-CYCLE-B"),
        ("E2E-CYCLE-B", "E2E-CYCLE-A"),
    ):
        (kanban / f"{identifier}.md").write_text(
            _ticket(identifier, "Cycle request", "Todo", 2).replace(
                "labels:\n",
                f"request: E2E-CYCLE\nblocked_by: [{blocker}]\nlabels:\n",
            ),
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
    assert await _column_titles(page) == ["Todo", "In Progress", "Verify", "Document"]

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
        "Document",
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
    await modal.get_by_label("Description").fill(
        "## QA evidence\n\n"
        "| Check | Result |\n"
        "| :--- | :---: |\n"
        "| Markdown table | **PASS** |\n"
        "| Safety | <script>window.__xss=1</script> |"
    )
    await modal.get_by_label("State").select_option("Human Review")
    await modal.get_by_label("Labels").fill("browser, e2e")
    await modal.get_by_label("ID prefix").fill("UIE2E")
    await modal.get_by_role("button", name="Create issue").click()
    await page.locator(".card", has_text=title).wait_for()

    await page.locator(".card", has_text=title).click()
    drawer = page.locator("#drawer-panel")
    await drawer.locator(".description-body .md-table").wait_for()
    assert await drawer.locator(".description-body .md-heading").count() == 1
    assert await drawer.locator(".description-body thead th").all_text_contents() == [
        "Check",
        "Result",
    ]
    cells = await drawer.locator(".description-body tbody td").all_text_contents()
    assert cells[:2] == ["Markdown table", "PASS"]
    assert "<script>window.__xss=1</script>" in cells
    assert await drawer.locator(".description-body script").count() == 0
    await drawer.locator(".drawer-title-input").fill(f"{title} updated")
    await drawer.locator(".drawer-title-input").press("Enter")
    await page.locator(".card", has_text=f"{title} updated").wait_for()

    await drawer.get_by_role("button", name="Delete issue").click()
    await page.locator(".modal-form").last.get_by_role("button", name="Delete").click()
    await page.locator(".card", has_text=f"{title} updated").wait_for(state="detached")


async def _exercise_settings_layout(page: Any, web_base_url: str) -> None:
    for width in (1440, 1201, 1100, 769, 390):
        await page.set_viewport_size({"width": width, "height": 900})
        await page.goto(f"{web_base_url}/#/settings", wait_until="networkidle")
        await page.locator(".settings-card").first.wait_for()
        assert await page.locator(".settings-body").get_by_role(
            "heading", level=2
        ).all_text_contents() == [
            "Workspace & interface",
            "Workflow setup",
            "Automation",
        ]
        await _assert_no_document_overflow(page, f"settings at {width}px")
        cards = page.locator(".settings-card")
        for index in range(await cards.count()):
            dims = await cards.nth(index).evaluate(
                "node => ({scrollWidth: node.scrollWidth, clientWidth: node.clientWidth})"
            )
            assert dims["scrollWidth"] <= dims["clientWidth"] + 2, (width, index, dims)
        control_widths = await page.locator(
            ".settings-card select, .settings-card input[type=number]"
        ).evaluate_all("nodes => nodes.map(node => node.clientWidth)")
        assert min(control_widths) >= 150, (width, control_widths)


async def _exercise_mobile_layout(page: Any, web_base_url: str) -> None:
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(f"{web_base_url}/#/board", wait_until="networkidle")
    await page.locator(".board-columns > .column").first.wait_for()
    await page.locator(".mobile-lane-tabs").wait_for()
    assert await page.locator(".board-columns > .column").count() == 1
    assert await page.locator(".add-column-ghost").count() == 0
    await page.get_by_role("tab", name="Document").click()
    assert await _column_titles(page) == ["Document"]
    await _assert_no_document_overflow(page, "mobile active")
    await _assert_no_element_overflow(page, "#board-scroll", "mobile lane tabs")


# ---------------------------------------------------------------------------
# Git + Chat pages
#
# Both pages mutate real state, so this half runs against its own board: a
# git repo with a task branch and a local bare remote, and a fake chat
# backend. The fake keeps the run deterministic and free — a real agent CLI
# would spend tokens and could answer differently on every run — while still
# emitting the exact stream-json frames the UI parses.
# ---------------------------------------------------------------------------


CHAT_WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress"]
  terminal_states: [Done, Archive]

agent:
  kind: claude
---

QA prompt for {{ issue.identifier }}.
"""

_ANSWER = "Two files: `calc.py` and `README.md`."
_DELTAS = ("Two files: ", "`calc.py` ", "and `README.md`.")


class _FakeChatBackend:
    """Emits the claude stream-json frames the chat page consumes."""

    def __init__(self, init: BackendInit) -> None:
        self.init = init
        self.turns: list[str] = []
        self.stopped = False
        # When set, the turn pauses after the deltas so the test can observe
        # the half-typed bubble before the finished message replaces it.
        self.stream_gate: asyncio.Event | None = None
        # Tests can gate or fail exactly the next replacement backend without
        # changing the otherwise shared deterministic fixture.
        self.next_initialize_entered: asyncio.Event | None = None
        self.next_initialize_gate: asyncio.Event | None = None
        self.next_initialize_error: Exception | None = None
        self.initialize_entered: asyncio.Event | None = None
        self.initialize_gate: asyncio.Event | None = None
        self.initialize_error: Exception | None = None

    async def start(self) -> None:
        return None

    async def initialize(self) -> dict[str, Any]:
        if self.initialize_entered is not None:
            self.initialize_entered.set()
        if self.initialize_gate is not None:
            await self.initialize_gate.wait()
        if self.initialize_error is not None:
            raise self.initialize_error
        return {}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        del initial_prompt, issue_title
        return "pending"

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        del is_continuation
        self.turns.append(prompt)
        answer = _ANSWER
        if "offer a separate project" in prompt:
            target = self.init.cwd.parent / "chat-todo-app"
            # Native Windows paths contain backslashes that are invalid as
            # raw JSON escapes; chat's strict parser rejects such payloads,
            # so the marker must be built with a real JSON encoder.
            answer = (
                "1. Create and register a separate Todo app.\n"
                "<symphony-project-setup>"
                + json.dumps(
                    {"choice": 1, "name": "Todo App", "path": str(target)}
                )
                + "</symphony-project-setup>"
            )
        await self._emit(EVENT_TURN_STARTED, {})
        if self.init.cfg.agent.kind == "prime-agent":
            for text in ("Two files:", answer):
                await self._emit(
                    EVENT_OTHER_MESSAGE,
                    {
                        "type": "message_update",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "private"},
                                {"type": "text", "text": text},
                            ],
                        },
                    },
                )
        elif self.init.cfg.agent.kind == "codex":
            answer = "## Done\n\n- Updated `src/app.py`\n- Tests pass"
            items = [
                {
                    "id": "reason-private",
                    "type": "reasoning",
                    "summary": ["PRIVATE CHAIN OF THOUGHT"],
                    "content": ["PRIVATE REASONING CONTENT"],
                },
                {
                    "id": "command-internal",
                    "type": "commandExecution",
                    "command": "git status --short",
                    "cwd": "PRIVATE COMMAND CWD",
                    "status": "completed",
                    "aggregatedOutput": "RAW COMMAND OUTPUT",
                    "exitCode": 0,
                },
                {
                    "id": "cmd-failed",
                    "type": "commandExecution",
                    "command": "deploy --api-key=sk-proj-1234567890abcdef",
                    "cwd": "/private/repo",
                    "status": "failed",
                    "aggregatedOutput": "RAW FAILED OUTPUT",
                    "exitCode": 7,
                },
                {
                    "id": "cmd-declined",
                    "type": "commandExecution",
                    "command": "deploy --api-key=sk-proj-1234567890abcdef",
                    "cwd": "/private/repo",
                    "status": "declined",
                    "aggregatedOutput": "RAW DECLINED OUTPUT",
                    "exitCode": 7,
                },
                {
                    "id": "file-private-id",
                    "type": "fileChange",
                    "status": "completed",
                    "changes": [
                        {
                            "path": "src/app.py",
                            "kind": "update",
                            "diff": "+ password=PRIVATE_FILE_SECRET",
                        },
                        {
                            "path": "tests/test_app.py",
                            "kind": "add",
                            "diff": "+ RAW DIFF",
                        },
                    ],
                },
                {
                    "id": "mcp-private-id",
                    "type": "mcpToolCall",
                    "server": "filesystem",
                    "tool": "read_file",
                    "status": "failed",
                    "arguments": {"path": "/private/input"},
                    "result": {"content": "PRIVATE MCP RESULT"},
                    "error": "PRIVATE MCP ERROR",
                },
                {
                    "id": "dynamic-private-id",
                    "type": "dynamicToolCall",
                    "tool": "lookup_ticket",
                    "status": "completed",
                    "arguments": {"id": "SECRET-123"},
                    "contentItems": [
                        {"type": "inputText", "text": "PRIVATE DYNAMIC RESULT"}
                    ],
                    "success": True,
                },
            ]
            for item in items:
                await self._emit(EVENT_OTHER_MESSAGE, {"item": item})
        else:
            for chunk in _DELTAS:
                await self._emit(
                    EVENT_OTHER_MESSAGE,
                    {
                        "type": "stream_event",
                        "event": {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": chunk},
                        },
                    },
                )
        if self.stream_gate is not None:
            await self.stream_gate.wait()
        if self.init.cfg.agent.kind == "prime-agent":
            message = {
                "role": "assistant",
                "content": [{"type": "text", "text": answer}],
            }
            await self._emit(
                EVENT_OTHER_MESSAGE, {"type": "message_end", "message": message}
            )
            await self._emit(
                EVENT_TURN_COMPLETED,
                {"type": "agent_end", "messages": [message]},
            )
        elif self.init.cfg.agent.kind == "codex":
            await self._emit(
                EVENT_OTHER_MESSAGE,
                {
                    "type": "assistant",
                    "message": answer,
                    "item": {
                        "id": "agent-private-id",
                        "type": "agentMessage",
                        "text": answer,
                        "raw": {"reasoning": "PRIVATE CHAIN OF THOUGHT"},
                    },
                },
            )
            await self._emit(EVENT_TURN_COMPLETED, {"message": answer})
        else:
            await self._emit(
                EVENT_OTHER_MESSAGE,
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": answer}]},
                },
            )
            await self._emit(EVENT_TURN_COMPLETED, {"message": answer})
        return TurnResult(status=EVENT_TURN_COMPLETED, turn_id="t", last_message=answer)

    async def stop(self) -> None:
        self.stopped = True

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await self.init.on_event(
            {
                "event": event,
                "timestamp": "2026-08-06T00:00:00Z",
                "payload": payload,
                "usage": {"total_tokens": 1234},
                "rate_limits": None,
                "agent_pid": 4321,
            }
        )

    @property
    def session_id(self) -> str | None:
        return "agent-sess-1"

    @property
    def pid(self) -> int | None:
        return 4321

    @property
    def latest_usage(self) -> dict[str, int]:
        return {"total_tokens": 1234}

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    def is_progress_event(self, _event: dict[str, Any]) -> bool:
        return True


@pytest.fixture()
def chat_backends(monkeypatch: pytest.MonkeyPatch) -> list[_FakeChatBackend]:
    built: list[_FakeChatBackend] = []

    def _build(init: BackendInit) -> _FakeChatBackend:
        backend = _FakeChatBackend(init)
        if built:
            previous = built[-1]
            backend.initialize_entered = previous.next_initialize_entered
            backend.initialize_gate = previous.next_initialize_gate
            backend.initialize_error = previous.next_initialize_error
            previous.next_initialize_entered = None
            previous.next_initialize_gate = None
            previous.next_initialize_error = None
        built.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", _build)
    return built


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )


@pytest.fixture()
def git_board_dir(tmp_path: Path) -> Path:
    root = tmp_path / "gitboard"
    root.mkdir()
    (root / "WORKFLOW.md").write_text(CHAT_WORKFLOW_TEXT, encoding="utf-8")
    kanban = root / "kanban"
    kanban.mkdir()
    (kanban / "E2E-1.md").write_text(
        _ticket("E2E-1", "Seed task branch card", "Todo"), encoding="utf-8"
    )
    (root / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init board")
    _git(root, "checkout", "-q", "-b", "symphony/E2E-1")
    (root / "feature.py").write_text("print('hi')\n", encoding="utf-8")
    _git(root, "add", "feature.py")
    _git(root, "commit", "-q", "-m", "E2E-1: feature")
    _git(root, "checkout", "-q", "main")
    _git(root, "init", "-q", "--bare", str(tmp_path / "origin.git"))
    _git(root, "remote", "add", "origin", str(tmp_path / "origin.git"))
    return root


@pytest_asyncio.fixture()
async def git_web_base_url(
    git_board_dir: Path,
    chat_backends: list[_FakeChatBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[str]:
    del chat_backends  # ordering only: patch the backend before the server runs
    projects: list[Any] = []

    class _Registry:
        def list(self) -> list[Any]:
            return list(projects)

        def status(self, _project_id: str) -> str:
            return "stopped"

    registry = _Registry()

    def create_project(
        _registry: Any,
        *,
        name: str,
        path: Path,
        expected_target: Any | None = None,
    ) -> Any:
        assert expected_target is not None and expected_target.repo == path
        project = SimpleNamespace(
            id="chat-todo-app",
            name=name,
            git_repo=str(path),
            workflow=str(path / "WORKFLOW.md"),
            host="127.0.0.1",
            port=10000,
        )
        projects.append(project)
        return project

    # This browser test proves the Chat UI/API handshake; the domain service's
    # real Git/registry behavior is covered separately without a global fixture.
    monkeypatch.setattr(webapi_module, "ProjectRegistry", lambda: registry)
    monkeypatch.setattr(
        webapi_module, "_create_or_adopt_registered_project", create_project
    )
    state = WorkflowState(git_board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield str(client.make_url("/")).rstrip("/")
    finally:
        await client.close()


async def _exercise_git_actions(page: Any, base_url: str, board: Path) -> None:
    await page.goto(f"{base_url}/#/git", wait_until="networkidle")
    row = page.locator(".branch-row", has_text="symphony/E2E-1")
    await row.wait_for()
    assert "E2E-1 · Todo" in await row.inner_text()

    # Push reaches the bare remote.
    await row.get_by_role("button", name="Push").click()
    await page.locator(".toast", has_text="Pushed symphony/E2E-1").wait_for()
    remote_heads = subprocess.run(
        ["git", "ls-remote", "--heads", "origin"],
        cwd=str(board),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "refs/heads/symphony/E2E-1" in remote_heads

    # An unmerged branch pre-checks Force; clearing it must be refused, and
    # the refusal stays inside the modal so the choice can be corrected.
    await (
        page.locator(".branch-row", has_text="symphony/E2E-1")
        .get_by_role("button", name="Delete")
        .click()
    )
    modal = page.locator(".modal-form").last
    assert "is NOT merged" in await modal.inner_text()
    force = modal.locator("#git-delete-force")
    assert await force.is_checked()
    await force.uncheck()
    await modal.get_by_role("button", name="Delete branch").click()
    await modal.locator(".modal-error", has_text="not merged into main").wait_for()

    await force.check()
    await modal.get_by_role("button", name="Delete branch").click()
    await page.locator(".toast", has_text="Deleted symphony/E2E-1").wait_for()
    await page.locator(".branch-row", has_text="symphony/E2E-1").wait_for(
        state="detached"
    )

    # Pushing the shared target demands its name typed back.
    await page.get_by_role("button", name="Push target").click()
    target_modal = page.locator(".modal-form").last
    await target_modal.locator("input.input").fill("wrong")
    await target_modal.get_by_role("button", name="Push", exact=True).click()
    await target_modal.locator(".modal-error", has_text="requires confirm").wait_for()
    await target_modal.locator("input.input").fill("main")
    await target_modal.get_by_role("button", name="Push", exact=True).click()
    await page.locator(".toast", has_text="Pushed main").wait_for()


async def _exercise_chat_session(
    page: Any, base_url: str, backends: list[_FakeChatBackend]
) -> tuple[str, str]:
    await page.goto(f"{base_url}/#/chat", wait_until="networkidle")
    await page.locator(".chat-session-bar").wait_for()
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-tab').length === 1"
    )
    listing = await (await page.request.get(f"{base_url}/api/v1/chat/sessions")).json()
    assert listing["sessions"][0]["mode"] == "qa"
    default_session_id = listing["sessions"][0]["session_id"]
    assert not await page.locator(".chat-input").is_disabled()

    # A budget of one turn so the advisory warning is reachable in one send.
    await page.get_by_role("button", name="+ New").click()
    modal = page.locator(".modal-form").last
    await modal.get_by_label("Mode").select_option("qa")
    await modal.get_by_label("Warn after turns (0 = no limit)").fill("1")
    await modal.get_by_role("button", name="Start session").click()
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-tab').length === 2"
    )
    assert "claude" in await page.locator(".chat-controls").inner_text()
    assert "0/1 turns" in await page.locator(".chat-budget-chip").inner_text()

    gate = asyncio.Event()
    backends[-1].stream_gate = gate

    await page.locator(".chat-input").fill("what files are here?")
    await page.get_by_role("button", name="Send", exact=True).click()
    await page.locator(".chat-user .chat-bubble").wait_for()

    # Mid-turn: the answer is still arriving token by token.
    live = page.locator(".chat-bubble-live")
    await live.wait_for()
    await page.wait_for_function(
        "() => (document.querySelector('.chat-bubble-live')||{}).textContent"
        f" === {''.join(_DELTAS)!r}"
    )
    assert await page.locator(".chat-input").is_disabled()

    gate.set()
    await live.wait_for(state="detached")
    bubbles = page.locator(".chat-agent .chat-bubble")
    assert await bubbles.count() == 1
    finished = await bubbles.first.inner_text()
    assert "calc.py" in finished and "README.md" in finished
    # The finished message is markdown, not the raw delta text.
    assert await bubbles.first.locator("code").count() >= 1

    # Budget is advisory: the chip goes red, the composer stays usable.
    await page.locator(".chat-budget-chip.over").wait_for()
    await page.locator(".chat-status", has_text="chat budget reached").wait_for()
    assert not await page.locator(".chat-input").is_disabled()
    listing = await (await page.request.get(f"{base_url}/api/v1/chat/sessions")).json()
    # A completed turn can rebuild Edit -> Q&A and immediately answer again.
    await (
        page.locator(".chat-mode-toggle")
        .get_by_role("button", name="Edit", exact=True)
        .click()
    )
    await page.locator(".chat-mode-btn.active", has_text="Edit").wait_for()
    await (
        page.locator(".chat-mode-toggle")
        .get_by_role("button", name="Q&A", exact=True)
        .click()
    )
    await page.locator(".chat-mode-btn.active", has_text="Q&A").wait_for()
    await page.locator(".chat-input").fill("load the current Kanban issues")
    await page.get_by_role("button", name="Send", exact=True).click()
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-agent .chat-bubble').length === 2"
    )
    assert (
        await page.locator(".chat-error", has_text="no backend for session").count()
        == 0
    )
    assert await page.locator(".chat-input").input_value() == ""
    return default_session_id, listing["active_id"]


async def _exercise_chat_prime_snapshots(
    page: Any, base_url: str, backends: list[_FakeChatBackend]
) -> str:
    await page.get_by_role("button", name="+ New").click()
    modal = page.locator(".modal-form").last
    await modal.get_by_label("Agent").select_option("prime-agent")
    await modal.get_by_role("button", name="Start session").click()
    listing = await (await page.request.get(f"{base_url}/api/v1/chat/sessions")).json()
    session_id = listing["active_id"]
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-tab').length === 3"
    )
    await page.locator(".chat-tab").last.click()
    await page.locator(".chat-controls", has_text="prime-agent").wait_for()

    gate = asyncio.Event()
    backends[-1].stream_gate = gate
    await page.locator(".chat-input").fill("stream the answer")
    await page.get_by_role("button", name="Send", exact=True).click()
    live = page.locator(".chat-bubble-live")
    await live.wait_for()
    await page.wait_for_function(
        "() => (document.querySelector('.chat-bubble-live')||{}).textContent"
        f" === {_ANSWER!r}"
    )
    # Cumulative snapshots replace the live text; they must not concatenate.
    assert await live.inner_text() == _ANSWER
    gate.set()
    await live.wait_for(state="detached")
    assert await page.locator(".chat-agent .chat-bubble").last.inner_text() == (
        "Two files: calc.py and README.md."
    )
    return session_id


async def _exercise_chat_codex_events(page: Any) -> None:
    await page.get_by_role("button", name="Stop").click()
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-tab').length === 2"
    )
    await page.get_by_role("button", name="+ New").click()
    modal = page.locator(".modal-form").last
    await modal.get_by_label("Agent").select_option("codex")
    await modal.get_by_role("button", name="Start session").click()
    await page.locator(".chat-controls", has_text="codex").wait_for()

    await page.locator(".chat-input").fill("inspect the repository")
    await page.get_by_role("button", name="Send", exact=True).click()
    await page.locator(".chat-agent .chat-bubble").wait_for()

    assert await page.locator(".chat-tool-name").all_inner_texts() == [
        "command",
        "command failed",
        "command declined",
        "files changed",
        "MCP tool failed",
        "dynamic tool",
    ]
    details = await page.locator(".chat-tool-detail").all_inner_texts()
    assert details[0] == "git status --short"
    assert details[3:] == [
        "src/app.py, tests/test_app.py",
        "filesystem/read_file",
        "lookup_ticket",
    ]
    transcript = await page.locator(".chat-transcript").inner_text()
    for private_marker in (
        "reason-private",
        "command-internal",
        "cmd-failed",
        "cmd-declined",
        "file-private-id",
        "mcp-private-id",
        "dynamic-private-id",
        "agent-private-id",
        "PRIVATE",
        "RAW",
        "SECRET-123",
        "sk-proj-1234567890abcdef",
        "/private/repo",
        "/private/input",
    ):
        assert private_marker not in transcript
    assert '"type"' not in transcript
    bubble = page.locator(".chat-agent .chat-bubble")
    assert await bubble.locator("h2", has_text="Done").count() == 1
    assert await bubble.locator("code", has_text="src/app.py").count() == 1


async def _exercise_chat_multi_session(
    page: Any, default_session_id: str, budget_session_id: str
) -> None:
    await page.get_by_role("button", name="+ New").click()
    modal = page.locator(".modal-form").last
    await modal.get_by_label("Mode").select_option("edit")
    await modal.get_by_role("button", name="Start session").click()
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-tab').length === 3"
    )
    # The new edit session starts empty; the QA session's transcript is intact.
    assert await page.locator(".chat-agent .chat-bubble").count() == 0
    await page.locator(f'.chat-tab[data-session-id="{budget_session_id}"]').click()
    await page.locator(".chat-agent .chat-bubble").first.wait_for()
    assert await page.locator(".chat-tab.active").count() == 1


async def _exercise_chat_reattach(
    page: Any, session_id: str, default_session_id: str
) -> None:
    await page.locator(f'.chat-tab[data-session-id="{session_id}"]').click()
    await page.locator(".chat-agent .chat-bubble").first.wait_for()
    await page.get_by_role("button", name="Stop").click()
    await page.locator(".chat-resume-select").wait_for()

    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-resume-select option').length >= 2"
    )
    value = (
        await page.locator(".chat-resume-select option").nth(1).get_attribute("value")
    )
    await page.locator(".chat-resume-select").select_option(value)
    await page.locator(".toast", has_text="Session reattached").wait_for()
    # The conversation comes back from the JSONL, not from memory.
    await page.locator(".chat-agent .chat-bubble").first.wait_for()
    assert (
        "calc.py" in await page.locator(".chat-agent .chat-bubble").first.inner_text()
    )

    # Retire the unused auto-created session only after validating reattach;
    # the fake backends share a runtime ID, so stopping it earlier would
    # replace the resumable fixture used above.
    await page.locator(f'.chat-tab[data-session-id="{default_session_id}"]').click()
    await page.get_by_role("button", name="Stop").click()
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-tab').length === 2"
    )


async def test_chat_project_setup_browser_e2e(
    git_web_base_url: str, git_board_dir: Path, chat_backends: list[_FakeChatBackend]
) -> None:
    assert async_playwright is not None
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await page.goto(f"{git_web_base_url}/#/chat", wait_until="networkidle")
            await page.locator(".chat-session-bar").wait_for()
            await (
                page.locator(".chat-mode-toggle")
                .get_by_role("button", name="Edit", exact=True)
                .click()
            )
            await page.locator(".chat-mode-btn.active", has_text="Edit").wait_for()
            await page.locator(".chat-input").fill("offer a separate project")
            await page.get_by_role("button", name="Send", exact=True).click()

            card = page.locator(".chat-project-setup")
            await card.wait_for()
            assert "Todo App" in await card.inner_text()
            assert "New directory" in await card.inner_text()
            assert (
                "symphony-project-setup"
                not in await page.locator(".chat-transcript").inner_text()
            )
            await card.get_by_role("button", name="Select option 1").click()
            await card.locator(
                ".chat-project-setup-status", has_text="Registered"
            ).wait_for()
            await page.locator(
                'select[aria-label="Select project"] option[value="chat-todo-app"]'
            ).wait_for(state="attached")
            listing = await (
                await page.request.get(f"{git_web_base_url}/api/v1/chat/sessions")
            ).json()
            session_id = listing["active_id"]
            snapshot = await (
                await page.request.get(
                    f"{git_web_base_url}/api/v1/chat/sessions/{session_id}"
                )
            ).json()
            [action] = snapshot["project_setup_actions"]
            assert action["status"] == "succeeded"
            assert action["project"]["id"] == "chat-todo-app"
            assert chat_backends[-1].turns[-1].endswith("offer a separate project")
            # The registration action is distinct from filing a current-board ticket.
            assert sorted(
                path.name for path in (git_board_dir / "kanban").iterdir()
            ) == ["E2E-1.md"]
            assert page_errors == []
        finally:
            await browser.close()


async def test_chat_pending_project_setup_card_disappears_after_stop(
    git_web_base_url: str, chat_backends: list[_FakeChatBackend]
) -> None:
    assert async_playwright is not None
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await page.goto(f"{git_web_base_url}/#/chat", wait_until="networkidle")
            await (
                page.locator(".chat-mode-toggle")
                .get_by_role("button", name="Edit", exact=True)
                .click()
            )
            await page.locator(".chat-mode-btn.active", has_text="Edit").wait_for()
            await page.locator(".chat-input").fill("offer a separate project")
            await page.get_by_role("button", name="Send", exact=True).click()
            await page.locator(".chat-project-setup").wait_for()

            await page.get_by_role("button", name="Stop").click()

            await page.wait_for_function(
                "() => document.querySelectorAll('.chat-project-setup').length === 0"
            )
            assert page_errors == []
            assert any(
                backend.turns and backend.turns[-1].endswith("offer a separate project")
                for backend in chat_backends
            )
        finally:
            await browser.close()


async def test_chat_failed_mode_rebuild_exposes_resume_and_preserves_draft(
    git_web_base_url: str, chat_backends: list[_FakeChatBackend]
) -> None:
    assert async_playwright is not None
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        release = asyncio.Event()
        try:
            await page.goto(f"{git_web_base_url}/#/chat", wait_until="networkidle")
            await page.wait_for_function(
                "() => document.querySelectorAll('.chat-tab').length === 1"
            )
            listing = await (
                await page.request.get(f"{git_web_base_url}/api/v1/chat/sessions")
            ).json()
            session_id = listing["active_id"]
            entered = asyncio.Event()
            current = chat_backends[-1]
            current.next_initialize_entered = entered
            current.next_initialize_gate = release
            current.next_initialize_error = RuntimeError("cannot rebuild chat backend")

            draft = "keep this unsent draft"
            await page.locator(".chat-input").fill(draft)
            await (
                page.locator(".chat-mode-toggle")
                .get_by_role("button", name="Edit", exact=True)
                .click()
            )
            await entered.wait()
            try:
                assert await page.locator(".chat-mode-btn").evaluate_all(
                    "(buttons) => buttons.every((button) => button.disabled)"
                )
                assert await page.get_by_role("button", name="Stop").is_disabled()
                assert await page.get_by_role("button", name="+ New").is_disabled()
                assert await page.locator(".chat-input").is_disabled()
            finally:
                release.set()

            await page.locator(".toast-error").wait_for()
            await page.locator(
                f'.chat-resume-select option[value="{session_id}"]'
            ).wait_for(state="attached")
            assert await page.locator(".chat-resume-select").is_visible()
            assert await page.locator(".chat-input").input_value() == draft
            assert not await page.locator(".chat-input").is_disabled()
            assert current.stopped is True
            assert chat_backends[1].stopped is True
            assert page_errors == []
        finally:
            release.set()
            await browser.close()


async def test_web_git_and_chat_browser_e2e(
    git_web_base_url: str,
    git_board_dir: Path,
    chat_backends: list[_FakeChatBackend],
) -> None:
    assert async_playwright is not None
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        page_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: (
                console_errors.append(msg.text) if msg.type == "error" else None
            ),
        )
        try:
            await _exercise_git_actions(page, git_web_base_url, git_board_dir)
            default_session_id, budget_session_id = await _exercise_chat_session(
                page, git_web_base_url, chat_backends
            )
            await _exercise_chat_multi_session(
                page, default_session_id, budget_session_id
            )
            await _exercise_chat_reattach(page, budget_session_id, default_session_id)
            await _exercise_chat_prime_snapshots(page, git_web_base_url, chat_backends)
            await _exercise_chat_codex_events(page)
            # Unlike the board flow, this one deliberately drives rejected
            # requests (unmerged delete, mistyped push confirmation, snapshot
            # of a just-stopped session). The browser logs each as a resource
            # error, so only real exceptions and other console output fail.
            assert page_errors == []
            unexpected = [
                text for text in console_errors if "Failed to load resource" not in text
            ]
            assert unexpected == []
        finally:
            await browser.close()


async def _exercise_runs_page(page: Any, base_url: str) -> None:
    await page.goto(f"{base_url}/#/runs")
    await page.locator(".run-attempt-row").first.wait_for()
    assert "SEED-DONE" in await page.locator(".run-attempt-row").first.inner_text()
    await page.locator(".run-attempt-row").first.click()
    await page.locator(".run-timeline-event").nth(1).wait_for()
    assert await page.locator(".run-timeline-event").count() == 2
    assert "Seed done card" in await page.locator(".run-attempt-detail h2").inner_text()
    async with page.expect_request(
        lambda request: request.url.endswith("query=missing-run")
    ):
        await page.locator("#runs-search").fill("missing-run")
    await page.get_by_text("No recorded runs match these filters").wait_for()
    await page.locator("#runs-search").fill("")
    await page.locator(".run-attempt-row").first.wait_for()
    async with page.expect_request(lambda request: "status=normal" in request.url):
        await page.locator("#runs-status-filter").select_option("normal")
    await page.locator("#runs-status-filter").select_option("")
    await page.goto(f"{base_url}/#/board")
    await page.get_by_role("button", name="+ New Issue").wait_for()


async def _exercise_request_schedule(
    page: Any, base_url: str, errors: list[str]
) -> None:
    await page.goto(f"{base_url}/#/board")
    await page.get_by_role("button", name="Request", exact=True).click()
    await page.get_by_role("heading", name="Request schedule").wait_for()
    picker = page.locator(".request-picker")
    await picker.wait_for()
    option = (
        await picker.locator("option").filter(has_text="E2E-REQ").get_attribute("value")
    )
    assert option is not None
    await picker.select_option(option)
    await page.locator(".request-node-id", has_text="E2E-PLAN").wait_for()
    await page.locator(".request-node-id", has_text="E2E-BUILD").wait_for()
    assert await page.locator(".schedule-status", has_text="Ready").count() >= 1
    assert await page.locator(".schedule-status", has_text="Waiting").count() >= 1
    await page.locator(".request-node-details summary").first.click()
    await page.get_by_text("Decision code", exact=True).first.wait_for()

    cycle_option = (
        await picker.locator("option")
        .filter(has_text="E2E-CYCLE")
        .get_attribute("value")
    )
    assert cycle_option is not None
    await picker.select_option(cycle_option)
    await page.get_by_text("Dependency cycle detected", exact=False).wait_for()
    assert await page.locator(".cycle-node").count() == 2

    async def _fail_schedule(route: Any) -> None:
        await route.fulfill(
            status=503,
            content_type="application/json",
            body='{"error":{"code":"test_failure","message":"temporary outage"}}',
        )

    await page.route("**/api/v1/requests/schedule?*", _fail_schedule)
    await picker.select_option(option)
    await page.get_by_text("Schedule unavailable", exact=False).wait_for()
    errors[:] = [error for error in errors if "503" not in error]
    await page.unroute("**/api/v1/requests/schedule?*", _fail_schedule)

    await page.get_by_role("button", name="Lanes", exact=True).click()
    await page.get_by_role("button", name="+ New Issue").wait_for()


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
            await _exercise_request_schedule(page, web_base_url, errors)
            await _exercise_column_scope(page, web_base_url)
            await _exercise_runs_page(page, web_base_url)
            await _exercise_issue_crud(page)
            await _exercise_settings_layout(page, web_base_url)
            await _exercise_mobile_layout(page, web_base_url)
            assert errors == []
        finally:
            await browser.close()
