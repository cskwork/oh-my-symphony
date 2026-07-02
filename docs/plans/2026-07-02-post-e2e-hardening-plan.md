# Symphony Post-E2E Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the manual post-pipeline E2E findings into repeatable browser/API QA and targeted UX hardening.

**Architecture:** Keep the Python test stack as the primary contract layer. Add an opt-in browser E2E test path that uses Python Playwright against the built-in aiohttp web app, then layer small UI/API improvements on top of existing `webapi.py`, `app.js`, and orchestrator status surfaces.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, aiohttp TestClient/TestServer, optional Python Playwright, Symphony file-board tracker, vanilla JS/CSS web UI.

---

## Deep-Dive Research

### Current Coverage

- `tests/test_web_static_contract.py` verifies the active/default board strings in `app.js` and terminal CSS selectors, but it cannot catch DOM behavior regressions, strict-selector issues, drawer CRUD failures, mobile layout problems, or console errors.
- `tests/test_webapi.py` already exercises `/api/v1/board`, issue CRUD, workflow states/prompts, branch policy, stats, security guards, and removed skills route through an in-process aiohttp app.
- The manual live E2E caught a scheduler issue: `max_total_turns` exhaustion was marked in memory, then stale-claim pruning allowed a later redispatch. The fix is covered by `tests/test_orchestrator_dispatch.py::test_turn_budget_exhaustion_survives_next_tick_claim_prune`, but the UI still does not clearly show the operator when an active ticket is exhausted but not persisted to `Blocked`.
- The repo has no tracked `package.json`; `pyproject.toml` is the dependency source of truth. Browser QA should not assume Node dependencies unless the project intentionally adopts them.
- Root examples already set `agent.budget_exhausted_state: Blocked`, so the clearest remaining budget UX gap is custom boards where that setting is empty.

### Recommended Direction

1. Add tracked browser E2E as an opt-in pytest marker using Python Playwright. This preserves the Python-only repo shape while making the exact manual checks repeatable.
2. Add a small API smoke script for operators to run against any local Symphony server.
3. Expose budget-exhausted status in the board API and web cards for boards that choose not to persist exhausted tickets to `Blocked`.
4. Improve mobile board ergonomics with a lane switcher instead of relying only on horizontal scroll.
5. Tighten terminal-state wording and documentation after the functional safeguards are in place.

### Rejected Alternatives

- **Adopt Node + `@playwright/test` as the main E2E stack.** It is a strong browser-test framework, but adding a second package manager for one repo-local test lane is heavier than needed.
- **Run browser E2E on every `pytest` by default.** This would make ordinary Python test runs depend on browser binaries and macOS/Linux sandbox details.
- **Only keep the manual Playwright script in notes.** That repeats the current weakness: the checks that found a real scheduler bug remain easy to forget.
- **Move all exhausted tickets to `Blocked` unconditionally.** Existing custom boards rely on `agent.budget_exhausted_state` as an explicit policy choice.

---

## File Map

- Create `tests/test_web_browser_e2e.py` - opt-in Playwright browser test against an in-process Symphony web app.
- Modify `pyproject.toml` - add optional browser dependency and pytest marker.
- Create `scripts/smoke_web_api.py` - repeatable live API/action smoke command.
- Create `tests/test_web_api_smoke_script.py` - verifies the smoke command against a temporary aiohttp web app.
- Modify `src/symphony/orchestrator/core.py` - expose per-issue operator attention for budget-exhausted tickets.
- Modify `src/symphony/webapi.py` - include issue attention in board/detail payloads.
- Modify `src/symphony/web/static/app.js` - render budget-exhausted badges and mobile lane tabs.
- Modify `src/symphony/web/static/style.css` - style attention badges and mobile lane controls.
- Modify `tests/test_webapi.py` - assert attention payloads.
- Modify `tests/test_web_static_contract.py` - keep static guards aligned with new UI helpers.
- Modify `README.md`, `README.ko.md`, and `docs/PIPELINE.md` - document browser QA and terminal/exhausted-state behavior.
- Modify `docs/changelog/changelog-2026-07-02.md` - record decisions and rejected alternatives.

---

## Task 1: Track Browser E2E As Opt-In Pytest

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_web_browser_e2e.py`
- Modify: `tests/test_web_static_contract.py`

- [ ] **Step 1: Add the optional browser extra and pytest marker**

Patch `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
browser = [
    "playwright>=1.48",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "browser_e2e: browser-driven web UI tests; requires SYMPHONY_BROWSER_E2E=1 and Playwright browser binaries",
]
```

Run:

```bash
python -m pytest --markers | rg browser_e2e
```

Expected: marker description is listed.

- [ ] **Step 2: Create the browser E2E fixture and skip gate**

Create `tests/test_web_browser_e2e.py` with this structure:

```python
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

playwright = pytest.importorskip("playwright.async_api")
async_playwright = playwright.async_playwright


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

    def request_refresh(self) -> bool:
        return False

    def find_running_issue_id(self, _identifier: str) -> str | None:
        return None

    def iter_running_issues(self) -> tuple[Any, ...]:
        return ()

    def issue_attention(self, _issue: Any) -> dict[str, str] | None:
        return None
```

- [ ] **Step 3: Add temp board seed helpers**

Add the helper functions below the stub:

```python
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
    (kanban / "SEED-REVIEW.md").write_text(
        _ticket("SEED-REVIEW", "Seed human review card", "Human Review"),
        encoding="utf-8",
    )
    (kanban / "SEED-DONE.md").write_text(
        _ticket("SEED-DONE", "Seed done card", "Done", priority=3),
        encoding="utf-8",
    )
    (kanban / "SEED-BLOCKED.md").write_text(
        _ticket("SEED-BLOCKED", "Seed blocked card", "Blocked", priority=1),
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
```

- [ ] **Step 4: Write the browser test**

Add the browser test:

```python
async def _column_titles(page) -> list[str]:
    return await page.locator(
        ".board-columns > .column .column-header .column-title"
    ).evaluate_all("(nodes) => nodes.map((n) => n.textContent.trim())")


async def _assert_no_document_overflow(page, label: str) -> None:
    dims = await page.evaluate(
        """() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
        })"""
    )
    assert dims["scrollWidth"] <= dims["clientWidth"] + 2, (label, dims)


async def test_web_board_browser_e2e(web_base_url: str) -> None:
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
            await page.goto(f"{web_base_url}/#/board", wait_until="networkidle")
            await page.locator(".board-columns > .column").first.wait_for()

            assert await _column_titles(page) == [
                "Todo",
                "In Progress",
                "Verify",
                "Learn",
            ]
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
            await page.locator(".card", has_text=f"{title} updated").wait_for(state="detached")

            await page.set_viewport_size({"width": 390, "height": 844})
            await page.goto(f"{web_base_url}/#/board", wait_until="networkidle")
            await page.locator(".board-columns > .column").first.wait_for()
            await _assert_no_document_overflow(page, "mobile active")

            assert errors == []
        finally:
            await browser.close()
```

- [ ] **Step 5: Run default test behavior**

Run:

```bash
python -m pytest tests/test_web_browser_e2e.py -q
```

Expected: skipped unless `SYMPHONY_BROWSER_E2E=1` is set.

- [ ] **Step 6: Run enabled browser behavior**

Run:

```bash
python -m pip install -e ".[dev,browser]"
python -m playwright install chromium
SYMPHONY_BROWSER_E2E=1 python -m pytest tests/test_web_browser_e2e.py -q
```

Expected: `1 passed`.

- [ ] **Step 7: Keep static contract aligned**

Update `tests/test_web_static_contract.py` with string guards for any helper names added by later tasks:

```python
assert "function buildMobileLaneTabs" in js
assert "function buildAttentionBadge" in js
```

Expected after Task 1 only: do not add these assertions yet. Add them in Task 3/4 when the helpers exist.

---

## Task 2: Add Repeatable Live API Smoke Command

**Files:**
- Create: `scripts/smoke_web_api.py`
- Create: `tests/test_web_api_smoke_script.py`

- [ ] **Step 1: Create script skeleton**

Create `scripts/smoke_web_api.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


class SmokeFailure(RuntimeError):
    pass


def request(base_url: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, json.loads(text) if text else None
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        return exc.code, json.loads(text) if text else None


def expect(base_url: str, method: str, path: str, status: int, body: dict[str, Any] | None = None) -> Any:
    actual, payload = request(base_url, method, path, body)
    if actual != status:
        raise SmokeFailure(f"{method} {path}: expected {status}, got {actual}, body={payload!r}")
    return payload
```

- [ ] **Step 2: Implement smoke checks**

Add `run_smoke`:

```python
def run_smoke(base_url: str, *, prefix: str = "SMOKE", learn_id: str = "") -> list[Check]:
    checks: list[Check] = []
    created: list[str] = []

    def ok(name: str) -> None:
        checks.append(Check(name, True))

    try:
        state = expect(base_url, "GET", "/api/v1/state", 200)
        if "counts" not in state:
            raise SmokeFailure("state payload missing counts")
        ok("state")

        board = expect(base_url, "GET", "/api/v1/board", 200)
        if not board["columns"]:
            raise SmokeFailure("board has no columns")
        ok("board")

        app_js_status, app_js = request(base_url, "GET", "/static/app.js")
        if app_js_status != 200 or "boardScope" not in str(app_js):
            raise SmokeFailure("static app.js missing boardScope")
        ok("static assets")

        stamp = str(int(time.time() * 1000))[-8:]
        issue = f"{prefix}{stamp}"
        created.append(issue)
        expect(
            base_url,
            "POST",
            "/api/v1/issues",
            201,
            {
                "identifier": issue,
                "title": "API smoke card",
                "state": "Human Review",
                "labels": ["smoke"],
                "description": "Created by smoke_web_api.py.",
            },
        )
        ok("issue create")

        detail = expect(base_url, "GET", f"/api/v1/issues/{issue}", 200)
        if detail["description"] != "Created by smoke_web_api.py.":
            raise SmokeFailure("detail description mismatch")
        ok("issue detail")

        expect(base_url, "PATCH", f"/api/v1/issues/{issue}", 200, {"state": "Done", "title": "API smoke done"})
        detail = expect(base_url, "GET", f"/api/v1/issues/{issue}", 200)
        if detail["state"] != "Done" or detail["title"] != "API smoke done":
            raise SmokeFailure("patch did not persist")
        ok("issue patch")

        if learn_id:
            expect(base_url, "POST", f"/api/v1/{learn_id}/skip-learn", 200)
            ok("skip learn")

        expect(base_url, "GET", "/api/v1/refresh", 405)
        expect(base_url, "POST", "/api/v1/refresh", 202)
        ok("refresh")

        expect(base_url, "GET", "/api/v1/workflow", 200)
        expect(base_url, "GET", "/api/v1/stats?days=7", 200)
        expect(base_url, "GET", "/api/v1/skills", 404)
        ok("workflow stats skills")
    finally:
        for identifier in created:
            request(base_url, "DELETE", f"/api/v1/issues/{identifier}")

    return checks
```

- [ ] **Step 3: Add CLI entrypoint**

Add:

```python
def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a running Symphony web/API server.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9999")
    parser.add_argument("--prefix", default="SMOKE")
    parser.add_argument("--learn-id", default="", help="Optional idle Learn issue to exercise skip-learn")
    args = parser.parse_args()

    checks = run_smoke(args.base_url, prefix=args.prefix, learn_id=args.learn_id)
    for check in checks:
        print(f"ok {check.name}")
    print(json.dumps({"count": len(checks), "checks": [c.name for c in checks]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Test the script against an in-process web app**

Create `tests/test_web_api_smoke_script.py` by reusing the temp web fixture shape from `tests/test_webapi.py`. Use importlib so `scripts/` does not need to become a package:

```python
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, AsyncIterator, cast

import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState


def _load_smoke_module():
    path = Path("scripts/smoke_web_api.py")
    spec = importlib.util.spec_from_file_location("smoke_web_api", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

Expected test:

```python
async def test_smoke_web_api_runs_against_test_server(web_base_url: str) -> None:
    smoke = _load_smoke_module()
    checks = await asyncio.to_thread(smoke.run_smoke, web_base_url, prefix="SMOKET")
    assert [c.name for c in checks] == [
        "state",
        "board",
        "static assets",
        "issue create",
        "issue detail",
        "issue patch",
        "refresh",
        "workflow stats skills",
    ]
```

Run:

```bash
python -m pytest tests/test_web_api_smoke_script.py -q
```

Expected: `1 passed`.

---

## Task 3: Surface Budget-Exhausted Tickets In The Web Board

**Files:**
- Modify: `src/symphony/orchestrator/core.py`
- Modify: `src/symphony/webapi.py`
- Modify: `src/symphony/web/static/app.js`
- Modify: `src/symphony/web/static/style.css`
- Modify: `tests/test_webapi.py`
- Modify: `tests/test_web_static_contract.py`

- [ ] **Step 1: Add an orchestrator attention helper**

Add to `Orchestrator`:

```python
def issue_attention(self, issue: Issue) -> dict[str, str] | None:
    if issue.id not in self._turn_budget_exhausted:
        return None
    debug = self._issue_debug.get(issue.id, _IssueDebug())
    message = debug.last_error or "agent budget exhausted"
    return {
        "kind": "budget_exhausted",
        "label": "Budget exhausted",
        "message": message,
    }
```

Why: `webapi.handle_board` already has each fetched `Issue`, so matching on `issue.id` avoids guessing identifier mappings after a worker exits.

- [ ] **Step 2: Include attention in issue cards**

Change `_issue_card` in `src/symphony/webapi.py`:

```python
def _issue_card(issue: Issue, *, attention: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "identifier": issue.identifier,
        "title": issue.title,
        "state": issue.state,
        "priority": issue.priority,
        "labels": list(issue.labels),
        "skills": list(issue.skills),
        "agent_kind": issue.agent_kind or "",
        "attention": attention,
        ...
    }
```

Update board serialization:

```python
issues = [
    _issue_card(i, attention=orchestrator.issue_attention(i))
    for i in sorted(fetched, key=registration_order_key)
]
```

Update detail serialization:

```python
card = (
    _issue_card(issue, attention=orchestrator.issue_attention(issue))
    if issue
    else {"identifier": identifier}
)
```

- [ ] **Step 3: Render attention badges in cards and drawer**

Add to `src/symphony/web/static/app.js`:

```javascript
function buildAttentionBadge(attention) {
  if (!attention) return null;
  return el('span', {
    class: `chip-attention attention-${attention.kind || 'info'}`,
    title: attention.message || attention.label || 'Attention required',
  }, attention.label || 'Attention');
}
```

In `buildCardEl`, after priority/labels:

```javascript
const attentionBadge = buildAttentionBadge(issue.attention);
if (attentionBadge) badges.appendChild(attentionBadge);
```

In `buildDrawerContent`, above metadata:

```javascript
if (detail.attention) {
  container.appendChild(el('div', { class: `drawer-attention attention-${detail.attention.kind || 'info'}` }, [
    el('strong', null, detail.attention.label || 'Attention'),
    el('span', null, detail.attention.message || ''),
  ]));
}
```

- [ ] **Step 4: Style attention badges**

Add to `style.css`:

```css
.chip-attention {
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 11px;
  font-weight: 700;
  background: #fff4d6;
  color: #7a4b00;
  border: 1px solid #f0c36a;
}

.drawer-attention {
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fff8e6;
  border: 1px solid #f0c36a;
  color: #5f3c00;
}
```

- [ ] **Step 5: Add API tests**

In `tests/test_webapi.py`, teach `_StubOrchestrator`:

```python
def issue_attention(self, issue: Any) -> dict[str, str] | None:
    if issue.identifier == "SEED-1":
        return {
            "kind": "budget_exhausted",
            "label": "Budget exhausted",
            "message": "max_total_turns reached (1/1)",
        }
    return None
```

Update `test_board_returns_columns_and_issues`:

```python
seed = payload["issues"][0]
assert seed["attention"]["kind"] == "budget_exhausted"
assert seed["attention"]["label"] == "Budget exhausted"
```

Add detail assertion:

```python
async def test_issue_detail_includes_attention(client: TestClient) -> None:
    detail = await (await client.get("/api/v1/issues/SEED-1")).json()
    assert detail["attention"]["message"] == "max_total_turns reached (1/1)"
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
python -m pytest tests/test_webapi.py tests/test_web_static_contract.py tests/test_orchestrator_dispatch.py::test_turn_budget_exhaustion_survives_next_tick_claim_prune -q
```

Expected: all pass.

---

## Task 4: Improve Mobile Board Ergonomics

**Files:**
- Modify: `src/symphony/web/static/app.js`
- Modify: `src/symphony/web/static/style.css`
- Modify: `tests/test_web_static_contract.py`
- Modify: `tests/test_web_browser_e2e.py`

- [ ] **Step 1: Add mobile board state**

Extend the `state` object in `app.js`:

```javascript
mobileColumnIndex: 0,
```

Add helper:

```javascript
function isMobileBoardViewport() {
  return window.matchMedia('(max-width: 720px)').matches;
}
```

- [ ] **Step 2: Add mobile lane tabs**

Add:

```javascript
function buildMobileLaneTabs(columns) {
  const maxIndex = Math.max(columns.length - 1, 0);
  if (state.mobileColumnIndex > maxIndex) state.mobileColumnIndex = maxIndex;
  return el('div', { class: 'mobile-lane-tabs', role: 'tablist', 'aria-label': 'Active lanes' },
    columns.map((col, index) => el('button', {
      class: `mobile-lane-tab${index === state.mobileColumnIndex ? ' active' : ''}`,
      type: 'button',
      role: 'tab',
      'aria-selected': index === state.mobileColumnIndex ? 'true' : 'false',
      onClick: () => {
        state.mobileColumnIndex = index;
        renderBoardColumns(document.getElementById('board-scroll'));
      },
    }, col.name))
  );
}
```

- [ ] **Step 3: Use one active lane on mobile**

Change `renderBoardColumns`:

```javascript
const visibleColumns = visibleBoardColumns(columns);
const mobileSingleLane = isMobileBoardViewport() && state.boardScope !== 'all';
if (mobileSingleLane) layout.appendChild(buildMobileLaneTabs(visibleColumns));
const columnsToRender = mobileSingleLane
  ? visibleColumns.slice(state.mobileColumnIndex, state.mobileColumnIndex + 1)
  : visibleColumns;
for (const col of columnsToRender) {
  grid.appendChild(buildColumnEl(col, byColumn.get(col.name) || [], live, board.read_only));
}
```

Keep `All` mode as full horizontal board because All is an explicit board-surgery mode.

- [ ] **Step 4: Add CSS**

Add:

```css
.mobile-lane-tabs {
  display: none;
}

@media (max-width: 720px) {
  .mobile-lane-tabs {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding: 0 0 10px;
  }

  .mobile-lane-tab {
    flex: 0 0 auto;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 13px;
    font-weight: 600;
  }

  .mobile-lane-tab.active {
    background: var(--text);
    color: var(--surface);
    border-color: var(--text);
  }

  .board-columns {
    grid-template-columns: minmax(280px, 1fr);
  }
}
```

- [ ] **Step 5: Extend browser E2E**

In `tests/test_web_browser_e2e.py`, after setting mobile viewport:

```python
await page.locator(".mobile-lane-tabs").wait_for()
assert await page.locator(".board-columns > .column").count() == 1
await page.get_by_role("tab", name="Learn").click()
assert await _column_titles(page) == ["Learn"]
await _assert_no_document_overflow(page, "mobile lane tabs")
```

- [ ] **Step 6: Run browser E2E**

Run:

```bash
SYMPHONY_BROWSER_E2E=1 python -m pytest tests/test_web_browser_e2e.py -q
```

Expected: browser E2E passes and mobile displays one active lane plus tabs.

---

## Task 5: Clarify Terminal-State Semantics Without Re-expanding The Board

**Files:**
- Modify: `src/symphony/web/static/app.js`
- Modify: `README.md`
- Modify: `README.ko.md`
- Modify: `docs/PIPELINE.md`
- Modify: `tests/test_web_static_contract.py`

- [ ] **Step 1: Keep compact grouping, refine header**

Change terminal group title in `buildTerminalSectionEl`:

```javascript
el('div', { class: 'terminal-section-title' }, 'Review and parked')
```

Keep `aria-label: 'Terminal states'` so assistive tech remains explicit.

- [ ] **Step 2: Add a static contract assertion**

Update `tests/test_web_static_contract.py`:

```python
assert "Review and parked" in js
assert "'aria-label': 'Terminal states'" in js
```

- [ ] **Step 3: Document the behavior**

In `README.md`, add one sentence near the web board section:

```markdown
The web board opens on active agent lanes; `Human Review`, `Done`, `Blocked`, and `Archive` stay visible in the compact **Review and parked** group until you switch to `All`.
```

In `README.ko.md`, add the matching Korean sentence:

```markdown
웹 보드는 기본적으로 에이전트가 처리하는 active 레인만 보여주며, `Human Review`, `Done`, `Blocked`, `Archive`는 `All`로 펼치기 전까지 **Review and parked** 그룹에 작게 표시됩니다.
```

In `docs/PIPELINE.md`, add the same concept to the 4-stage board explanation.

- [ ] **Step 4: Run docs/static tests**

Run:

```bash
python -m pytest tests/test_web_static_contract.py tests/test_workflow_pipeline_prompt.py -q
```

Expected: all pass.

---

## Task 6: Full Verification Pass

**Files:**
- No new files; this is the closeout gate.

- [ ] **Step 1: Run static checks**

```bash
node --check src/symphony/web/static/app.js
git diff --check
```

Expected: both pass with no output from `git diff --check`.

- [ ] **Step 2: Run Python suite**

```bash
python -m pytest -q
```

Expected: all tests pass; browser E2E is skipped unless enabled.

- [ ] **Step 3: Run browser E2E locally**

```bash
SYMPHONY_BROWSER_E2E=1 python -m pytest tests/test_web_browser_e2e.py -q
```

Expected: `1 passed`.

- [ ] **Step 4: Run live API smoke against a real server**

Start a temp server with `WORKFLOW.file.example.md` or a copied temp workflow, then run:

```bash
python scripts/smoke_web_api.py --base-url http://127.0.0.1:9999 --prefix SMOKE
```

Expected: printed `ok ...` lines and JSON summary with all checks passed. Created smoke issue is deleted on success or failure.

- [ ] **Step 5: Manual UI QA checklist**

Verify in a browser:

- Active default shows only `Todo`, `In Progress`, `Verify`, `Learn`.
- `Review and parked` shows non-empty terminal states only.
- `All` expands all configured columns.
- Human Review issue create/edit/delete works from the UI.
- Budget-exhausted issue displays a badge and drawer message.
- Mobile width shows lane tabs, no document-level horizontal overflow.
- Console has no errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml scripts/smoke_web_api.py tests/test_web_browser_e2e.py tests/test_web_api_smoke_script.py tests/test_web_static_contract.py tests/test_webapi.py src/symphony/orchestrator/core.py src/symphony/webapi.py src/symphony/web/static/app.js src/symphony/web/static/style.css README.md README.ko.md docs/PIPELINE.md docs/changelog/changelog-2026-07-02.md
git commit -m "Add repeatable web E2E hardening"
```

---

## Acceptance Criteria

- Browser UI QA is repeatable from a tracked pytest file.
- Normal `python -m pytest -q` remains usable on machines without browser binaries.
- Operators have a live API smoke command that cleans up its own test issue.
- Budget-exhausted active tickets are visible in web card/detail UI when not persisted to `Blocked`.
- Mobile board use no longer depends on partially offscreen columns for the default active view.
- Public docs explain the compact terminal-state group without reintroducing default-column noise.

## Rollback Plan

- Browser E2E can be disabled by leaving `SYMPHONY_BROWSER_E2E` unset.
- API smoke script is additive and can be removed without runtime impact.
- Attention badges are additive fields in API payloads; old clients can ignore them.
- Mobile lane tabs affect only `max-width: 720px` active-board mode; `All` remains the existing full-board layout.
