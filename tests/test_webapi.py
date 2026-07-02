"""REST contract for the built-in web board (`symphony.webapi`).

Drives `build_app` against a real temp WORKFLOW.md + file board, with a
stub orchestrator for the live-run surface.
"""

from __future__ import annotations

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
  active_states: [Todo, Doing]
  terminal_states: [Done, Archive]
  state_descriptions:
    Todo: "triage"

agent:
  kind: claude

prompts:
  stages:
    Todo: ./prompts/stages/todo.md
    Doing: ./prompts/stages/doing.md
---

You are working on {{ issue.identifier }}.
"""

TICKET = """---
id: SEED-1
identifier: SEED-1
title: seeded ticket
state: Todo
priority: 2
labels: [demo]
created_at: '2026-07-01T00:00:00Z'
updated_at: '2026-07-01T00:00:00Z'
---

Seed body.
"""


class _StubOrchestrator:
    def __init__(self, workflow_state: WorkflowState) -> None:
        self._workflow_state = workflow_state
        self.running_identifiers: dict[str, str] = {}
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

    def issue_snapshot(self, _identifier: str) -> dict[str, Any] | None:
        return None

    def request_refresh(self) -> bool:
        self.refresh_calls += 1
        return False

    def find_running_issue_id(self, identifier: str) -> str | None:
        return self.running_identifiers.get(identifier)

    def iter_running_issues(self) -> tuple[Any, ...]:
        return ()

    def is_paused(self, _issue_id: str) -> bool:
        return False

    def pause_worker(self, _issue_id: str) -> bool:
        return True

    def resume_worker(self, _issue_id: str) -> bool:
        return True


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    stages = tmp_path / "prompts" / "stages"
    stages.mkdir(parents=True)
    (stages / "todo.md").write_text("todo prompt", encoding="utf-8")
    (stages / "doing.md").write_text("doing prompt", encoding="utf-8")
    kanban = tmp_path / "kanban"
    kanban.mkdir()
    (kanban / "SEED-1.md").write_text(TICKET, encoding="utf-8")
    skill = tmp_path / "skills" / "tdd"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: test first\n---\nWrite tests first.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest_asyncio.fixture()
async def client(board_dir: Path) -> AsyncIterator[TestClient]:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    stub = _StubOrchestrator(state)
    app = build_app(cast(Orchestrator, stub))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    cli.stub = stub  # type: ignore[attr-defined]
    try:
        yield cli
    finally:
        await cli.close()


# ---------------------------------------------------------------------------
# board + issues
# ---------------------------------------------------------------------------


async def test_board_returns_columns_and_issues(client: TestClient) -> None:
    resp = await client.get("/api/v1/board")
    assert resp.status == 200
    payload = await resp.json()
    names = [c["name"] for c in payload["columns"]]
    assert names == ["Todo", "Doing", "Done", "Archive"]
    todo = payload["columns"][0]
    assert todo["description"] == "triage"
    assert todo["has_prompt"] is True
    assert payload["columns"][2]["terminal"] is True
    assert [i["identifier"] for i in payload["issues"]] == ["SEED-1"]
    assert payload["board"]["read_only"] is False


async def test_create_issue_generates_identifier(client: TestClient) -> None:
    resp = await client.post(
        "/api/v1/issues",
        json={"title": "new work", "skills": ["tdd"], "priority": 1},
    )
    assert resp.status == 201
    payload = await resp.json()
    assert payload["identifier"] == "TASK-1"
    assert payload["state"] == "Todo"
    detail = await (await client.get("/api/v1/issues/TASK-1")).json()
    assert detail["skills"] == ["tdd"]
    assert detail["priority"] == 1
    # Second create advances the sequence.
    resp2 = await client.post("/api/v1/issues", json={"title": "more"})
    assert (await resp2.json())["identifier"] == "TASK-2"


async def test_create_issue_requires_json_content_type(client: TestClient) -> None:
    resp = await client.post(
        "/api/v1/issues",
        data="title=x",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status == 415


async def test_create_issue_validation_errors(client: TestClient) -> None:
    assert (await client.post("/api/v1/issues", json={})).status == 400
    assert (
        await client.post("/api/v1/issues", json={"title": "x", "priority": 9})
    ).status == 400
    assert (
        await client.post("/api/v1/issues", json={"title": "x", "state": "Nope"})
    ).status == 400
    assert (
        await client.post(
            "/api/v1/issues", json={"title": "x", "agent_kind": "hal9000"}
        )
    ).status == 400


async def test_patch_moves_state_and_updates_fields(client: TestClient) -> None:
    resp = await client.patch(
        "/api/v1/issues/SEED-1",
        json={"state": "doing", "title": "renamed", "labels": ["A", "b"]},
    )
    assert resp.status == 200
    detail = await (await client.get("/api/v1/issues/SEED-1")).json()
    assert detail["state"] == "Doing"  # canonical casing restored
    assert detail["title"] == "renamed"
    assert detail["labels"] == ["a", "b"]


async def test_patch_unknown_issue_404_and_empty_400(client: TestClient) -> None:
    assert (
        await client.patch("/api/v1/issues/GHOST-1", json={"title": "x"})
    ).status == 404
    assert (await client.patch("/api/v1/issues/SEED-1", json={})).status == 400


async def test_delete_issue_and_running_guard(client: TestClient) -> None:
    stub = client.stub  # type: ignore[attr-defined]
    stub.running_identifiers["SEED-1"] = "iss-1"
    assert (await client.delete("/api/v1/issues/SEED-1")).status == 409
    stub.running_identifiers.clear()
    assert (await client.delete("/api/v1/issues/SEED-1")).status == 200
    assert (await client.get("/api/v1/issues/SEED-1")).status == 404


# ---------------------------------------------------------------------------
# workflow: states + prompts + branch policy
# ---------------------------------------------------------------------------


async def test_put_states_renames_and_migrates_tickets(
    client: TestClient, board_dir: Path
) -> None:
    resp = await client.put(
        "/api/v1/workflow/states",
        json={
            "states": [
                {"name": "Todo"},
                {"name": "Building", "previous_name": "Doing"},
                {"name": "QA"},
                {"name": "Done", "terminal": True},
                {"name": "Archive", "terminal": True},
            ]
        },
    )
    assert resp.status == 200
    payload = await resp.json()
    assert payload["renamed"] == {"Doing": "Building"}
    assert payload["added"] == ["QA"]
    board = await (await client.get("/api/v1/board")).json()
    assert [c["name"] for c in board["columns"]] == [
        "Todo",
        "Building",
        "QA",
        "Done",
        "Archive",
    ]
    # New active column got a starter prompt.
    assert (board_dir / "prompts" / "stages" / "qa.md").exists()


async def test_put_states_removed_column_moves_tickets_to_fallback(
    client: TestClient,
) -> None:
    await client.patch("/api/v1/issues/SEED-1", json={"state": "Doing"})
    resp = await client.put(
        "/api/v1/workflow/states",
        json={
            "states": [
                {"name": "Todo"},
                {"name": "Done", "terminal": True},
                {"name": "Archive", "terminal": True},
            ]
        },
    )
    assert resp.status == 200
    payload = await resp.json()
    assert payload["removed"] == ["Doing"]
    assert payload["migrated"] == {"SEED-1": "Todo"}
    detail = await (await client.get("/api/v1/issues/SEED-1")).json()
    assert detail["state"] == "Todo"


async def test_put_states_rejects_bad_payloads(client: TestClient) -> None:
    assert (
        await client.put("/api/v1/workflow/states", json={"states": "x"})
    ).status == 400
    assert (
        await client.put(
            "/api/v1/workflow/states", json={"states": [{"name": "OnlyActive"}]}
        )
    ).status == 400


async def test_prompt_get_put_roundtrip(client: TestClient) -> None:
    payload = await (await client.get("/api/v1/workflow/prompts/Todo")).json()
    assert payload["content"] == "todo prompt"
    resp = await client.put(
        "/api/v1/workflow/prompts/Todo", json={"content": "new prompt"}
    )
    assert resp.status == 200
    payload = await (await client.get("/api/v1/workflow/prompts/Todo")).json()
    assert payload["content"] == "new prompt"
    assert (await client.get("/api/v1/workflow/prompts/Ghost")).status == 404


async def test_branch_policy_put_validates_and_persists(
    client: TestClient, board_dir: Path
) -> None:
    assert (
        await client.put("/api/v1/workflow/branch-policy", json={})
    ).status == 400
    assert (
        await client.put(
            "/api/v1/workflow/branch-policy",
            json={"feature_base_branch": "bad branch name!"},
        )
    ).status == 400
    resp = await client.put(
        "/api/v1/workflow/branch-policy", json={"feature_base_branch": "dev"}
    )
    assert resp.status == 200
    text = (board_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    assert "feature_base_branch: dev" in text


# ---------------------------------------------------------------------------
# removed skills endpoint + stats
# ---------------------------------------------------------------------------


async def test_skills_endpoint_is_not_exposed(client: TestClient) -> None:
    resp = await client.get("/api/v1/skills")
    assert resp.status == 404


async def test_stats_endpoint_counts_created_issue(client: TestClient) -> None:
    await client.post("/api/v1/issues", json={"title": "tracked"})
    payload = await (await client.get("/api/v1/stats?days=7")).json()
    assert payload["totals"]["done"] == 0
    assert "live" in payload
    assert (await client.get("/api/v1/stats?days=nope")).status == 400


# ---------------------------------------------------------------------------
# security regressions (2026-07-02 review)
# ---------------------------------------------------------------------------


async def test_traversal_identifiers_rejected_on_get_and_delete(
    client: TestClient,
) -> None:
    # Windows treats backslash as a path separator, and aiohttp's default
    # dynamic segment regex lets it through — the identifier whitelist is
    # the gate. See security review 2026-07-02.
    for payload in ("..%5C..%5Csecret", "..%2e", "a.b", "space name"):
        resp = await client.get(f"/api/v1/issues/{payload}")
        assert resp.status == 400, payload
        resp = await client.delete(f"/api/v1/issues/{payload}")
        assert resp.status == 400, payload


async def test_non_loopback_host_rejected_even_for_get(client: TestClient) -> None:
    resp = await client.get("/api/v1/board", headers={"Host": "evil.example:9993"})
    assert resp.status == 403
    payload = await resp.json()
    assert payload["error"]["code"] == "forbidden_host"
    # Bracketed IPv6 loopback without a port must still be allowed.
    resp = await client.get("/api/v1/board", headers={"Host": "[::1]"})
    assert resp.status == 200


async def test_malformed_workflow_yaml_returns_400_not_500(
    client: TestClient, board_dir: Path
) -> None:
    workflow = board_dir / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker: [unclosed\n---\nbody\n", encoding="utf-8"
    )
    resp = await client.put(
        "/api/v1/workflow/branch-policy", json={"feature_base_branch": "dev"}
    )
    assert resp.status == 400
    payload = await resp.json()
    assert "YAML" in payload["error"]["message"]


async def test_states_put_preserves_omitted_descriptions(
    client: TestClient, board_dir: Path
) -> None:
    # Todo starts with description "triage"; a spec that omits description
    # must keep it, and a rename must carry it over.
    resp = await client.put(
        "/api/v1/workflow/states",
        json={
            "states": [
                {"name": "Todo"},
                {"name": "Building", "previous_name": "Doing"},
                {"name": "Done", "terminal": True},
                {"name": "Archive", "terminal": True},
            ]
        },
    )
    assert resp.status == 200
    board = await (await client.get("/api/v1/board")).json()
    by_name = {c["name"]: c for c in board["columns"]}
    assert by_name["Todo"]["description"] == "triage"
