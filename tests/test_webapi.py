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
        self.run_history_error: str | None = None
        self.reset_ci_calls = 0
        self.recover_calls: list[dict[str, str | None]] = []
        self.ci_status: dict[str, Any] = {
            "enabled": True,
            "interval_ms": 60_000,
            "max_turns": 4,
            "turns_used": 2,
            "agent_kind": "codex",
            "in_flight": False,
            "current_phase": None,
            "last_started_at": "2026-07-05T00:00:00Z",
            "last_finished_at": "2026-07-05T00:01:00Z",
            "next_due_at": "2026-07-05T00:31:00Z",
            "last_result": "failed",
            "last_error": "ruff failed",
            "tickets_created": 1,
            "skipped_reason": None,
            "last_verified_branch": "dev",
            "last_verified_sha": "abc123",
        }

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

    def issue_attention(self, issue: Any) -> dict[str, Any] | None:
        if issue.identifier == "SEED-1":
            return {
                "kind": "budget_exhausted",
                "label": "Budget exhausted",
                "message": "max_total_turns reached (1/1)",
                "severity": "warning",
                "due_at": None,
            }
        return None

    def recent_runs(
        self, issue_id: str | None = None, limit: int = 50
    ) -> tuple[list[dict[str, Any]], str | None]:
        if self.run_history_error is not None:
            return [], self.run_history_error
        rows = [
            {
                "run_id": "run-seed",
                "issue_id": "id-SEED-1",
                "identifier": "SEED-1",
                "attempt": None,
                "attempt_kind": "initial",
                "agent_kind": "claude",
                "status": "normal",
                "started_at": "2026-07-03T01:00:00+00:00",
                "completed_at": "2026-07-03T01:01:00+00:00",
                "workspace_path": "/tmp/ws/SEED-1",
            },
            {
                "run_id": "run-other",
                "issue_id": "id-OTHER-1",
                "identifier": "OTHER-1",
                "attempt": 1,
                "attempt_kind": "retry",
                "agent_kind": "codex",
                "status": "force_ejected_zombie",
                "started_at": "2026-07-03T01:02:00+00:00",
                "completed_at": None,
                "workspace_path": None,
            },
        ]
        filtered = [r for r in rows if issue_id is None or r["issue_id"] == issue_id]
        return filtered[:limit], None

    def is_paused(self, _issue_id: str) -> bool:
        return False

    def pause_worker(self, _issue_id: str) -> bool:
        return True

    def resume_worker(self, _issue_id: str) -> bool:
        return True

    async def recover_blocked_issue(
        self,
        identifier: str,
        *,
        target_state: str | None = None,
        agent_kind: str | None = None,
    ) -> tuple[bool, str, dict[str, str]]:
        self.recover_calls.append(
            {
                "identifier": identifier,
                "target_state": target_state,
                "agent_kind": agent_kind,
            }
        )
        rca_state = target_state or "Doing"
        agent = agent_kind or "claude"
        return True, f"RCA-1 opened to unblock {identifier}", {
            "original_state": "Blocked",
            "target_state": "Todo",
            "source_reopen_state": "Todo",
            "rca_identifier": "RCA-1",
            "rca_state": rca_state,
            "agent_kind": agent,
        }

    def continuous_improvement_status(self) -> dict[str, Any]:
        return dict(self.ci_status)

    def reset_continuous_improvement_turns(self) -> None:
        self.reset_ci_calls += 1
        self.ci_status["turns_used"] = 0
        self.ci_status["skipped_reason"] = None


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
    seed = payload["issues"][0]
    assert seed["attention"]["kind"] == "budget_exhausted"
    assert seed["attention"]["label"] == "Budget exhausted"
    assert seed["attention"]["severity"] == "warning"
    assert payload["board"]["read_only"] is False


async def test_issue_detail_includes_attention(client: TestClient) -> None:
    detail = await (await client.get("/api/v1/issues/SEED-1")).json()
    assert detail["attention"]["message"] == "max_total_turns reached (1/1)"
    assert detail["attention"]["due_at"] is None


async def test_issue_detail_serializes_unquoted_frontmatter_timestamps(
    client: TestClient, board_dir: Path
) -> None:
    (board_dir / "kanban" / "TIME-1.md").write_text(
        """---
id: TIME-1
identifier: TIME-1
title: timestamp ticket
state: Todo
priority: 1
created_at: 2026-07-04T13:50:00Z
updated_at: 2026-07-04T14:27:00Z
---

Timestamp body.
""",
        encoding="utf-8",
    )

    resp = await client.get("/api/v1/issues/TIME-1")

    assert resp.status == 200
    detail = await resp.json()
    assert detail["frontmatter"]["created_at"] == "2026-07-04T13:50:00+00:00"
    assert detail["frontmatter"]["updated_at"] == "2026-07-04T14:27:00+00:00"


async def test_runs_endpoint_filters_and_clamps(client: TestClient) -> None:
    resp = await client.get("/api/v1/runs?issue=id-SEED-1&limit=500")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["count"] == 1
    assert payload["runs"][0]["identifier"] == "SEED-1"
    assert payload["runs"][0]["attempt_kind"] == "initial"
    assert payload["runs"][0]["workspace_path"] == "/tmp/ws/SEED-1"


async def test_runs_endpoint_registry_error_returns_empty_history(
    client: TestClient,
) -> None:
    stub = client.stub  # type: ignore[attr-defined]
    stub.run_history_error = "run_registry_error: database is locked"

    resp = await client.get("/api/v1/runs")

    assert resp.status == 200
    payload = await resp.json()
    assert payload["runs"] == []
    assert payload["registry_error"] == "run_registry_error: database is locked"


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


async def test_recover_blocked_route_calls_orchestrator(client: TestClient) -> None:
    resp = await client.post(
        "/api/v1/issues/SEED-1/recover-blocked",
        json={"target_state": "Doing", "agent_kind": "codex"},
    )

    assert resp.status == 200
    payload = await resp.json()
    assert payload["identifier"] == "SEED-1"
    assert payload["rca_created"] is True
    assert payload["target_state"] == "Todo"
    assert payload["source_reopen_state"] == "Todo"
    assert payload["rca_identifier"] == "RCA-1"
    assert payload["rca_state"] == "Doing"
    assert payload["agent_kind"] == "codex"
    stub = client.stub  # type: ignore[attr-defined]
    assert stub.recover_calls == [
        {
            "identifier": "SEED-1",
            "target_state": "Doing",
            "agent_kind": "codex",
        }
    ]


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


async def test_workflow_get_includes_continuous_improvement(
    client: TestClient,
) -> None:
    resp = await client.get("/api/v1/workflow")

    assert resp.status == 200
    payload = await resp.json()
    ci = payload["continuous_improvement"]
    assert ci == {
        "enabled": False,
        "interval_ms": 1_800_000,
        "max_turns": 48,
        "agent_kind": "",
        "ticket_prefix": "CI",
        "max_tickets_per_run": 5,
        "require_idle_board": True,
    }
    assert "codex" in payload["agent_kinds"]
    assert "claude" in payload["agent_kinds"]


async def test_continuous_improvement_put_validates_and_persists(
    client: TestClient, board_dir: Path
) -> None:
    bad_payloads = [
        {},
        {"enabled": "true"},
        {"interval_ms": 59_999},
        {"max_turns": -1},
        {"agent_kind": "unknown"},
        {"enabled": True, "unexpected": True},
    ]
    for body in bad_payloads:
        resp = await client.put(
            "/api/v1/workflow/continuous-improvement", json=body
        )
        assert resp.status == 400, body

    resp = await client.put(
        "/api/v1/workflow/continuous-improvement",
        json={
            "enabled": True,
            "interval_ms": 120_000,
            "max_turns": 3,
            "agent_kind": "opencode",
        },
    )

    assert resp.status == 200
    payload = await resp.json()
    assert payload["updated"] == [
        "agent_kind",
        "enabled",
        "interval_ms",
        "max_turns",
    ]
    assert payload["continuous_improvement"]["enabled"] is True
    assert payload["continuous_improvement"]["interval_ms"] == 120_000
    assert payload["continuous_improvement"]["max_turns"] == 3
    assert payload["continuous_improvement"]["agent_kind"] == "opencode"
    text = (board_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    assert "continuous_improvement:" in text
    assert "enabled: true" in text
    assert "interval_ms: 120000" in text
    assert "max_turns: 3" in text
    assert "agent_kind: opencode" in text


async def test_continuous_improvement_put_guards_json_contract(
    client: TestClient,
) -> None:
    resp = await client.put(
        "/api/v1/workflow/continuous-improvement",
        data="{",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400

    resp = await client.put(
        "/api/v1/workflow/continuous-improvement",
        data='{"enabled":true}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status == 415

    resp = await client.put(
        "/api/v1/workflow/continuous-improvement",
        json={"enabled": True},
        headers={"Host": "evil.example:9993"},
    )
    assert resp.status == 403


async def test_continuous_improvement_status_and_reset(
    client: TestClient,
) -> None:
    status_resp = await client.get("/api/v1/continuous-improvement/status")
    assert status_resp.status == 200
    status = await status_resp.json()
    assert status["turns_used"] == 2
    assert status["last_result"] == "failed"
    assert status["last_verified_branch"] == "dev"

    reset_resp = await client.post(
        "/api/v1/workflow/continuous-improvement/reset-turns"
    )

    assert reset_resp.status == 200
    payload = await reset_resp.json()
    assert payload["status"]["turns_used"] == 0
    stub = client.stub  # type: ignore[attr-defined]
    assert stub.reset_ci_calls == 1


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
