"""REST contract for the built-in web board (`symphony.webapi`).

Drives `build_app` against a real temp WORKFLOW.md + file board, with a
stub orchestrator for the live-run surface.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.utils.auto_merge import AutoMergeResult
from symphony.utils.git_ops import GitOpResult
from symphony.webapi import (
    _PUBLIC_SCHEDULE_REASONS,
    _request_is_loopback,
    TRUSTED_ORIGINS_ENV,
)
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
        self.skip_calls: list[str] = []
        self.schedule_payload: dict[str, Any] = {
            "schema_version": 1,
            "available": True,
            "reason": None,
            "generated_at": "2026-07-02T00:00:00Z",
            "stale": False,
            "policy": "fifo",
            "policy_order": "starvation, registration",
            "slots": {
                "running": 0,
                "maximum": 2,
                "available_before": 2,
                "available_after": 2,
            },
            "entries": [
                {
                    "identifier": "SEED-1",
                    "status": "ready",
                    "code": "ready",
                    "reason": "eligible",
                    "queue_rank": 1,
                    "scan_position": 1,
                    "wave": 0,
                    "critical_path_length": 0,
                    "starvation_promoted": False,
                    "retry": None,
                }
            ],
        }
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

    async def skip_document(self, identifier: str) -> tuple[bool, str]:
        self.skip_calls.append(identifier)
        return True, f"moved {identifier} to Human Review"

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

    async def recent_runs(
        self,
        issue_id: str | None = None,
        limit: int = 50,
        *,
        query: str | None = None,
        status: str | None = None,
        agent: str | None = None,
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
        if query:
            needle = query.lower()
            filtered = [
                r
                for r in filtered
                if any(
                    needle in str(r[field]).lower()
                    for field in ("identifier", "agent_kind", "status")
                )
            ]
        if status:
            filtered = [r for r in filtered if r["status"] == status]
        if agent:
            filtered = [r for r in filtered if r["agent_kind"] == agent]
        return filtered[:limit], None

    async def run_detail(self, run_id: str) -> tuple[dict[str, Any] | None, str | None]:
        if run_id != "a" * 32:
            return None, None
        return {
            "run": {
                "run_id": run_id,
                "identifier": "SEED-1",
                "title": "seeded ticket",
                "state": "Done",
                "status": "normal",
                "tokens": {"input": 1, "cache": 2, "output": 3, "total": 6},
            },
            "events": [
                {"event_type": "run_completed", "payload": {"status": "normal"}}
            ],
        }, None

    async def run_diagnostic(
        self, run_id: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        detail, error = await self.run_detail(run_id)
        if detail is None:
            return None, error
        return {"schema_version": 1, **detail}, None

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
        return (
            True,
            f"FIX-1 opened to unblock {identifier}",
            {
                "original_state": "Blocked",
                "target_state": "Todo",
                "source_reopen_state": "Todo",
                "fix_identifier": "FIX-1",
                "fix_state": rca_state,
                "rca_identifier": "FIX-1",
                "rca_state": rca_state,
                "agent_kind": agent,
            },
        )

    def continuous_improvement_status(self) -> dict[str, Any]:
        return dict(self.ci_status)

    def reset_continuous_improvement_turns(self) -> None:
        self.reset_ci_calls += 1
        self.ci_status["turns_used"] = 0
        self.ci_status["skipped_reason"] = None

    def schedule_snapshot(self) -> dict[str, Any]:
        return dict(self.schedule_payload)

    def dependency_state_resolved(self, state: str | None) -> bool:
        return (state or "").strip().lower() == "done"


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
    # F-11: preset applies refuse a board without the shipped prompt bodies,
    # so the fixture mirrors the documented `cp -R docs/symphony-prompts` step.
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "docs" / "symphony-prompts",
        tmp_path / "docs" / "symphony-prompts",
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


async def test_request_schedule_groups_explicit_request_and_explains_queue(
    client: TestClient, board_dir: Path
) -> None:
    tickets = {
        "EXT-0": """---
id: EXT-0
identifier: EXT-0
title: transitive external prerequisite
state: Todo
---
External root.
""",
        "EXT-1": """---
id: EXT-1
identifier: EXT-1
title: external prerequisite
state: Todo
blocked_by: [EXT-0]
---
External.
""",
        "PLAN-1": """---
id: PLAN-1
identifier: PLAN-1
title: plan
state: Todo
request: REQ-7
blocked_by: [EXT-1]
---
Plan.
""",
        "BUILD-1": """---
id: BUILD-1
identifier: BUILD-1
title: build
state: Todo
request: REQ-7
blocked_by: [PLAN-1]
---
Build.
""",
    }
    for identifier, body in tickets.items():
        (board_dir / "kanban" / f"{identifier}.md").write_text(body, encoding="utf-8")
    stub = client.stub  # type: ignore[attr-defined]
    stub.schedule_payload["entries"] = [
        {
            "identifier": "EXT-1",
            "status": "ready",
            "code": "ready",
            "reason": "private-session-abc /Users/operator/secret",
            "queue_rank": 1,
            "scan_position": 1,
            "wave": 0,
            "critical_path_length": 2,
            "starvation_promoted": False,
            "retry": None,
        },
        {
            "identifier": "PLAN-1",
            "status": "waiting",
            "code": "waiting_dependency",
            "reason": "blocker unresolved: EXT-1",
            "queue_rank": None,
            "scan_position": 2,
            "wave": 1,
            "critical_path_length": 1,
            "starvation_promoted": False,
            "retry": None,
        },
        {
            "identifier": "BUILD-1",
            "status": "waiting",
            "code": "waiting_dependency",
            "reason": "blocker unresolved: PLAN-1",
            "queue_rank": None,
            "scan_position": 3,
            "wave": 2,
            "critical_path_length": 0,
            "starvation_promoted": False,
            "retry": None,
        },
    ]

    requests_resp = await client.get("/api/v1/requests")
    assert requests_resp.status == 200
    requests_payload = await requests_resp.json()
    assert requests_payload["policy"] == "fifo"
    assert {row["kind"] for row in requests_payload["requests"]} == {
        "request",
        "ticket",
    }
    request_row = next(
        row for row in requests_payload["requests"] if row["id"] == "REQ-7"
    )
    assert request_row["node_count"] == 2
    assert request_row["counts"]["waiting"] == 2

    resp = await client.get("/api/v1/requests/REQ-7/schedule")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["request"] == {"kind": "request", "id": "REQ-7"}
    assert payload["summary"]["longest_unresolved_chain_nodes"] == 4
    assert payload["edges"] == [
        {"from": "EXT-0", "to": "EXT-1"},
        {"from": "EXT-1", "to": "PLAN-1"},
        {"from": "PLAN-1", "to": "BUILD-1"},
    ]
    assert [node["identifier"] for node in payload["nodes"]] == [
        "EXT-0",
        "EXT-1",
        "PLAN-1",
        "BUILD-1",
    ]
    assert payload["nodes"][0]["scope"] == "external"
    assert "private-session-abc" not in json.dumps(payload)
    assert "/Users/operator/secret" not in json.dumps(payload)
    assert payload["nodes"][2]["decision"]["code"] == "waiting_dependency"

    standalone_resp = await client.get("/api/v1/requests/ticket/SEED-1/schedule")
    assert standalone_resp.status == 200
    standalone = await standalone_resp.json()
    assert standalone["request"] == {"kind": "ticket", "id": "SEED-1"}


async def test_request_schedule_marks_dependency_cycles_invalid(
    client: TestClient, board_dir: Path
) -> None:
    for identifier, blocker in (("CYCLE-A", "CYCLE-B"), ("CYCLE-B", "CYCLE-A")):
        (board_dir / "kanban" / f"{identifier}.md").write_text(
            f"""---
id: {identifier}
identifier: {identifier}
title: cyclic ticket
state: Todo
request: REQ-CYCLE
blocked_by: [{blocker}]
---
Cycle.
""",
            encoding="utf-8",
        )

    resp = await client.get("/api/v1/requests/REQ-CYCLE/schedule")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["execution_valid"] is False
    assert payload["warnings"] == ["dependency_cycle"]
    assert payload["summary"]["longest_unresolved_chain_nodes"] is None
    assert {node["identifier"] for node in payload["nodes"] if node["cycle"]} == {
        "CYCLE-A",
        "CYCLE-B",
    }


async def test_request_schedule_query_supports_any_explicit_nonblank_request_id(
    client: TestClient, board_dir: Path
) -> None:
    (board_dir / "kanban" / "SPECIAL-1.md").write_text(
        """---
id: SPECIAL-1
identifier: SPECIAL-1
title: special request key
state: Todo
request: 'Release / Phase A'
---
Special.
""",
        encoding="utf-8",
    )

    resp = await client.get(
        "/api/v1/requests/schedule",
        params={"kind": "request", "id": "Release / Phase A"},
    )
    assert resp.status == 200
    payload = await resp.json()
    assert payload["request"] == {
        "kind": "request",
        "id": "Release / Phase A",
    }
    assert payload["nodes"][0]["identifier"] == "SPECIAL-1"


async def test_request_schedule_explicitly_reports_unsupported_tracker(
    client: TestClient, board_dir: Path
) -> None:
    workflow_path = board_dir / "WORKFLOW.md"
    workflow = workflow_path.read_text(encoding="utf-8").replace(
        "  kind: file\n  board_root: ./kanban",
        "  kind: linear\n  endpoint: https://api.linear.app/graphql\n  api_key: tok\n  project_slug: demo",
    )
    workflow_path.write_text(workflow, encoding="utf-8")
    cfg, err = client.stub.workflow_state.reload()  # type: ignore[attr-defined]
    assert err is None and cfg is not None

    resp = await client.get("/api/v1/requests")
    assert resp.status == 200
    payload = await resp.json()
    assert payload == {
        "available": False,
        "reason": "unsupported_tracker",
        "tracker_kind": "linear",
        "requests": [],
    }


async def test_request_schedule_rejects_unknown_group(client: TestClient) -> None:
    resp = await client.get("/api/v1/requests/request/REQ-404/schedule")
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "request_not_found"


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


async def test_create_issue_with_blocked_by_and_request(
    client: TestClient,
) -> None:
    resp = await client.post(
        "/api/v1/issues",
        json={
            "title": "child work",
            "identifier": "CHILD-1",
            "blocked_by": ["SEED-1"],
            "request": "REQ-1",
        },
    )
    assert resp.status == 201
    detail = await (await client.get("/api/v1/issues/CHILD-1")).json()
    assert detail["request"] == "REQ-1"
    assert [b["identifier"] for b in detail["blocked_by"]] == ["SEED-1"]
    assert detail["frontmatter"]["request"] == "REQ-1"


async def test_create_issue_rejects_unknown_blocker(client: TestClient) -> None:
    resp = await client.post(
        "/api/v1/issues",
        json={"title": "x", "blocked_by": ["GHOST-1"]},
    )
    assert resp.status == 400
    payload = await resp.json()
    assert payload["error"]["code"] == "board_dependency_error"
    assert "GHOST-1" in payload["error"]["message"]


async def test_create_issue_rejects_cycle_with_path_in_message(
    client: TestClient, board_dir: Path
) -> None:
    # SEED-1 already (danglingly) depends on LOOP-1; creating LOOP-1 <- SEED-1
    # would close the loop.
    (board_dir / "kanban" / "SEED-1.md").write_text(
        TICKET.replace("---\n\nSeed body.", "blocked_by: [LOOP-1]\n---\n\nSeed body."),
        encoding="utf-8",
    )
    resp = await client.post(
        "/api/v1/issues",
        json={"title": "loop", "identifier": "LOOP-1", "blocked_by": ["SEED-1"]},
    )
    assert resp.status == 400
    payload = await resp.json()
    assert payload["error"]["code"] == "board_dependency_error"
    assert "LOOP-1 -> SEED-1 -> LOOP-1" in payload["error"]["message"]
    assert not (board_dir / "kanban" / "LOOP-1.md").exists()


async def test_create_issue_rejects_malformed_blocked_by_and_request(
    client: TestClient,
) -> None:
    assert (
        await client.post("/api/v1/issues", json={"title": "x", "blocked_by": "SEED-1"})
    ).status == 400
    assert (
        await client.post(
            "/api/v1/issues", json={"title": "x", "request": "bad request!"}
        )
    ).status == 400


async def test_patch_updates_blocked_by_and_request(client: TestClient) -> None:
    created = await client.post(
        "/api/v1/issues", json={"title": "dep", "identifier": "DEP-1"}
    )
    assert created.status == 201
    resp = await client.patch(
        "/api/v1/issues/DEP-1",
        json={"blocked_by": ["SEED-1"], "request": "REQ-2"},
    )
    assert resp.status == 200
    detail = await (await client.get("/api/v1/issues/DEP-1")).json()
    assert detail["request"] == "REQ-2"
    assert [b["identifier"] for b in detail["blocked_by"]] == ["SEED-1"]

    cleared = await client.patch(
        "/api/v1/issues/DEP-1", json={"blocked_by": [], "request": ""}
    )
    assert cleared.status == 200
    detail = await (await client.get("/api/v1/issues/DEP-1")).json()
    assert detail["request"] == ""
    assert detail["blocked_by"] == []


async def test_patch_rejects_self_blocking_cycle(client: TestClient) -> None:
    resp = await client.patch("/api/v1/issues/SEED-1", json={"blocked_by": ["SEED-1"]})
    assert resp.status == 400
    payload = await resp.json()
    assert payload["error"]["code"] == "board_dependency_error"
    assert "cycle" in payload["error"]["message"]


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


async def test_patch_rejects_running_state_change_without_mutating_file(
    client: TestClient, board_dir: Path
) -> None:
    stub = client.stub  # type: ignore[attr-defined]
    stub.running_identifiers["SEED-1"] = "iss-1"
    ticket_path = board_dir / "kanban" / "SEED-1.md"
    before = ticket_path.read_bytes()

    resp = await client.patch(
        "/api/v1/issues/SEED-1",
        json={"state": "Done", "title": "must not be written"},
    )

    assert resp.status == 409
    payload = await resp.json()
    assert payload["error"]["code"] == "state_in_use"
    assert "pause or wait" in payload["error"]["message"]
    assert ticket_path.read_bytes() == before


async def test_patch_allows_running_non_state_and_same_state_edits(
    client: TestClient,
) -> None:
    stub = client.stub  # type: ignore[attr-defined]
    stub.running_identifiers["SEED-1"] = "iss-1"

    title_resp = await client.patch(
        "/api/v1/issues/SEED-1", json={"title": "running edit"}
    )
    same_state_resp = await client.patch(
        "/api/v1/issues/SEED-1", json={"state": "todo"}
    )

    assert title_resp.status == 200
    assert same_state_resp.status == 200


async def test_patch_unknown_issue_404_and_empty_400(client: TestClient) -> None:
    assert (
        await client.patch("/api/v1/issues/GHOST-1", json={"title": "x"})
    ).status == 404
    assert (await client.patch("/api/v1/issues/SEED-1", json={})).status == 400


async def test_recover_blocked_route_calls_orchestrator(client: TestClient) -> None:
    resp = await client.post(
        "/api/v1/issues/SEED-1/recover-blocked",
        json={"fix_state": "Doing", "agent_kind": "codex"},
    )

    assert resp.status == 200
    payload = await resp.json()
    assert payload["identifier"] == "SEED-1"
    assert payload["fix_created"] is True
    assert payload["rca_created"] is True  # deprecated alias
    assert payload["target_state"] == "Todo"
    assert payload["source_reopen_state"] == "Todo"
    assert payload["fix_identifier"] == "FIX-1"
    assert payload["fix_state"] == "Doing"
    assert payload["rca_identifier"] == "FIX-1"  # deprecated alias
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


async def test_skip_document_route_and_legacy_skip_learn_alias(
    client: TestClient,
) -> None:
    """`/skip-document` is the route; `/skip-learn` stays a deprecated alias."""
    stub = client.stub  # type: ignore[attr-defined]

    resp = await client.post("/api/v1/issues/SEED-1/skip-document")
    assert resp.status == 200
    payload = await resp.json()
    assert payload == {
        "identifier": "SEED-1",
        "skipped": True,
        "message": "moved SEED-1 to Human Review",
    }

    legacy = await client.post("/api/v1/issues/SEED-1/skip-learn")
    assert legacy.status == 200
    legacy_payload = await legacy.json()
    assert legacy_payload["skipped"] is True

    assert stub.skip_calls == ["SEED-1", "SEED-1"]


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
    assert (await client.put("/api/v1/workflow/branch-policy", json={})).status == 400
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


async def test_git_merge_passes_local_only_policy(
    git_repo: Path, client: TestClient, board_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = board_dir / "WORKFLOW.md"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "agent:\n  kind: claude",
            "agent:\n  kind: claude\n  auto_merge_push_target: false",
        ),
        encoding="utf-8",
    )
    client.stub.workflow_state.reload()  # type: ignore[attr-defined]
    captured: dict[str, object] = {}

    async def local_only_merge(**kwargs: object) -> AutoMergeResult:
        captured.update(kwargs)
        return AutoMergeResult(ok=True, status="merged", detail="local")

    monkeypatch.setattr(
        "symphony.webapi.auto_merge_on_done_best_effort", local_only_merge
    )

    resp = await client.post("/api/v1/git/merge", json={"branch": "symphony/SEED-1"})

    assert resp.status == 200
    assert captured["push_target"] is False


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
        "modes": [],
        # Disabled: nothing resolves, so nothing runs.
        "resolved_modes": [],
        "supported_modes": [
            "readiness",
            "blocked_fixes",
            "security",
            "market_research",
            "feature_improvements",
        ],
        "mode_interval_hours": {
            "readiness": 0.0,
            "blocked_fixes": 0.0,
            "security": 24.0,
            "market_research": 168.0,
            "feature_improvements": 72.0,
        },
        "max_improvement_tickets_per_run": 3,
    }
    assert "codex" in payload["agent_kinds"]
    assert "claude" in payload["agent_kinds"]
    assert payload["agent"]["auto_merge_push_target"] is True


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
        resp = await client.put("/api/v1/workflow/continuous-improvement", json=body)
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


async def test_continuous_improvement_put_modes_roundtrip(
    client: TestClient, board_dir: Path
) -> None:
    resp = await client.put(
        "/api/v1/workflow/continuous-improvement",
        json={"modes": ["nonsense"]},
    )
    assert resp.status == 400

    resp = await client.put(
        "/api/v1/workflow/continuous-improvement",
        json={"enabled": True, "modes": ["market_research", "Blocked_Fixes"]},
    )

    assert resp.status == 200
    payload = await resp.json()
    ci = payload["continuous_improvement"]
    assert payload["updated"] == ["enabled", "modes"]
    assert ci["modes"] == ["blocked_fixes", "market_research"]
    assert ci["resolved_modes"] == ["blocked_fixes", "market_research"]
    text = (board_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    assert "modes:" in text

    # Clearing the list falls back to the readiness-only default.
    resp = await client.put(
        "/api/v1/workflow/continuous-improvement", json={"modes": []}
    )
    assert resp.status == 200
    ci = (await resp.json())["continuous_improvement"]
    assert ci["modes"] == []
    assert ci["resolved_modes"] == ["readiness"]


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
    workflow.write_text("---\ntracker: [unclosed\n---\nbody\n", encoding="utf-8")
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


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


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
def git_repo(board_dir: Path) -> Path:
    """Turn the workflow dir into a git repo with a symphony/SEED-1 branch."""
    _git(board_dir, "init", "-q", "-b", "main")
    _git(board_dir, "add", "-A")
    _git(board_dir, "commit", "-q", "-m", "init board")
    _git(board_dir, "checkout", "-q", "-b", "symphony/SEED-1")
    (board_dir / "feature.py").write_text("print('hi')\n")
    _git(board_dir, "add", "feature.py")
    _git(board_dir, "commit", "-q", "-m", "SEED-1: feature")
    _git(board_dir, "checkout", "-q", "main")
    return board_dir


async def test_git_endpoints_degrade_without_repo(client: TestClient) -> None:
    resp = await client.get("/api/v1/git/log")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["note"] == "not_a_git_repo"
    assert payload["commits"] == []

    resp = await client.get("/api/v1/git/task-branches")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["note"] == "not_a_git_repo"
    assert payload["branches"] == []

    resp = await client.get("/api/v1/git/compare?branch=main")
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "not_a_git_repo"


async def test_git_log_lists_commits_and_validates_params(
    git_repo: Path, client: TestClient
) -> None:
    resp = await client.get("/api/v1/git/log")
    assert resp.status == 200
    payload = await resp.json()
    subjects = {c["subject"] for c in payload["commits"]}
    assert subjects == {"init board", "SEED-1: feature"}
    assert payload["note"] is None

    resp = await client.get("/api/v1/git/log?branch=main&limit=1")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["branch"] == "main"
    assert [c["subject"] for c in payload["commits"]] == ["init board"]
    head = payload["commits"][0]
    assert set(head) == {"sha", "short_sha", "author", "date", "refs", "subject"}

    resp = await client.get("/api/v1/git/log?branch=no-such-branch")
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "unknown_ref"

    # Leading dash never reaches git as an option.
    resp = await client.get("/api/v1/git/log?branch=--all")
    assert resp.status == 400

    resp = await client.get("/api/v1/git/log?limit=abc")
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "invalid_limit"


async def test_git_task_branches_map_tickets_and_running(
    git_repo: Path, client: TestClient
) -> None:
    resp = await client.get("/api/v1/git/task-branches")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["target_branch"] == "main"
    assert payload["auto_merge_enabled"] is True
    assert payload["note"] is None
    (row,) = payload["branches"]
    assert row["branch"] == "symphony/SEED-1"
    assert row["identifier"] == "SEED-1"
    assert row["ticket"] == {
        "identifier": "SEED-1",
        "title": "seeded ticket",
        "state": "Todo",
    }
    assert row["merged"] is False
    assert row["ahead"] == 1
    assert row["behind"] == 0
    assert row["running"] is False
    assert row["last_commit"]["subject"] == "SEED-1: feature"

    client.stub.running_identifiers["SEED-1"] = "id-SEED-1"  # type: ignore[attr-defined]
    resp = await client.get("/api/v1/git/task-branches")
    (row,) = (await resp.json())["branches"]
    assert row["running"] is True


async def test_git_compare_defaults_target_to_current_branch(
    git_repo: Path, client: TestClient
) -> None:
    resp = await client.get("/api/v1/git/compare?branch=symphony/SEED-1")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["target"] == "main"
    assert payload["ahead"] == 1
    assert payload["behind"] == 0
    assert payload["merged"] is False
    assert [c["subject"] for c in payload["commits"]] == ["SEED-1: feature"]
    assert payload["stat"]["total"] == {"files": 1, "insertions": 1, "deletions": 0}
    assert payload["stat"]["files"][0]["path"] == "feature.py"

    resp = await client.get("/api/v1/git/compare")
    assert resp.status == 400  # branch is required

    resp = await client.get("/api/v1/git/compare?branch=symphony/SEED-1&target=no-such")
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "unknown_ref"


async def test_git_merge_validates_branch(client: TestClient) -> None:
    resp = await client.post("/api/v1/git/merge", json={})
    assert resp.status == 400

    resp = await client.post("/api/v1/git/merge", json={"branch": "main"})
    assert resp.status == 400
    assert "task branches" in (await resp.json())["error"]["message"]

    resp = await client.post("/api/v1/git/merge", json={"branch": "symphony/../escape"})
    assert resp.status == 400


async def test_git_merge_blocks_running_worker(
    git_repo: Path, client: TestClient
) -> None:
    client.stub.running_identifiers["SEED-1"] = "id-SEED-1"  # type: ignore[attr-defined]
    resp = await client.post("/api/v1/git/merge", json={"branch": "symphony/SEED-1"})
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "state_in_use"


async def test_git_merge_merges_branch_and_appends_note(
    git_repo: Path, client: TestClient
) -> None:
    resp = await client.post("/api/v1/git/merge", json={"branch": "symphony/SEED-1"})
    assert resp.status == 200
    payload = await resp.json()
    assert payload["ok"] is True
    assert payload["status"] == "merged"
    assert payload["branch"] == "symphony/SEED-1"
    assert payload["target"] == "main"
    assert payload["ticket_note_appended"] is True

    log_out = subprocess.run(
        ["git", "log", "--oneline", "main"],
        cwd=str(git_repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "SEED-1" in log_out
    assert (git_repo / "feature.py").exists()
    assert "Manual Merge" in (git_repo / "kanban" / "SEED-1.md").read_text()
    assert client.stub.refresh_calls >= 1  # type: ignore[attr-defined]

    resp = await client.get("/api/v1/git/task-branches")
    (row,) = (await resp.json())["branches"]
    assert row["merged"] is True


async def test_git_merge_maps_failure_statuses_to_409(
    git_repo: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_merge(**_kwargs: Any) -> AutoMergeResult:
        return AutoMergeResult(
            ok=False, status="dirty_overlap", detail="host has overlapping dirty files"
        )

    monkeypatch.setattr("symphony.webapi.auto_merge_on_done_best_effort", failing_merge)
    resp = await client.post("/api/v1/git/merge", json={"branch": "symphony/SEED-1"})
    assert resp.status == 409
    error = (await resp.json())["error"]
    assert error["code"] == "merge_dirty_overlap"
    assert "dirty" in error["message"]


async def test_git_merge_rejects_concurrent_requests(
    git_repo: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_merge(**_kwargs: Any) -> AutoMergeResult:
        started.set()
        await release.wait()
        return AutoMergeResult(ok=True, status="merged", detail="")

    monkeypatch.setattr("symphony.webapi.auto_merge_on_done_best_effort", slow_merge)

    async def post() -> tuple[int, dict[str, Any]]:
        resp = await client.post(
            "/api/v1/git/merge", json={"branch": "symphony/SEED-1"}
        )
        return resp.status, await resp.json()

    first = asyncio.create_task(post())
    await asyncio.wait_for(started.wait(), timeout=5)

    status, payload = await post()
    assert status == 409
    assert payload["error"]["code"] == "merge_in_progress"

    release.set()
    status, payload = await first
    assert status == 200
    assert payload["ok"] is True


@pytest.fixture()
def git_remote(git_repo: Path, tmp_path: Path) -> Path:
    """Give the repo a real (local, bare) `origin` so pushes actually run."""
    bare = tmp_path / "origin.git"
    _git(git_repo, "init", "-q", "--bare", str(bare))
    _git(git_repo, "remote", "add", "origin", str(bare))
    return bare


async def test_git_remote_status_reports_remotes(
    git_repo: Path, client: TestClient
) -> None:
    resp = await client.get("/api/v1/git/remote-status")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["remotes"] == []
    assert payload["default_remote"] is None
    assert payload["current_branch"] == "main"
    assert payload["target_branch"] == "main"
    assert isinstance(payload["gh_available"], bool)


async def test_git_remote_status_lists_configured_remote(
    git_remote: Path, client: TestClient
) -> None:
    payload = await (await client.get("/api/v1/git/remote-status")).json()
    assert payload["remotes"] == ["origin"]
    assert payload["default_remote"] == "origin"


async def test_git_branch_delete_needs_force_when_unmerged(
    git_repo: Path, client: TestClient
) -> None:
    resp = await client.post(
        "/api/v1/git/branch/delete", json={"branch": "symphony/SEED-1"}
    )
    assert resp.status == 409
    error = (await resp.json())["error"]
    assert error["code"] == "not_merged"
    assert "main" in error["message"]

    resp = await client.post(
        "/api/v1/git/branch/delete",
        json={"branch": "symphony/SEED-1", "force": True},
    )
    assert resp.status == 200
    assert (await resp.json())["status"] == "deleted"
    branches = (await (await client.get("/api/v1/git/branches")).json())["branches"]
    assert "symphony/SEED-1" not in branches


async def test_git_branch_delete_allows_merged_branch(
    git_repo: Path, client: TestClient
) -> None:
    assert (
        await client.post("/api/v1/git/merge", json={"branch": "symphony/SEED-1"})
    ).status == 200
    resp = await client.post(
        "/api/v1/git/branch/delete", json={"branch": "symphony/SEED-1"}
    )
    assert resp.status == 200
    assert (await resp.json())["ok"] is True


async def test_git_branch_delete_guards(git_repo: Path, client: TestClient) -> None:
    resp = await client.post("/api/v1/git/branch/delete", json={"branch": "main"})
    assert resp.status == 400
    assert "task branches" in (await resp.json())["error"]["message"]

    resp = await client.post(
        "/api/v1/git/branch/delete", json={"branch": "symphony/NOPE-9"}
    )
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "unknown_ref"

    client.stub.running_identifiers["SEED-1"] = "id-SEED-1"  # type: ignore[attr-defined]
    resp = await client.post(
        "/api/v1/git/branch/delete", json={"branch": "symphony/SEED-1"}
    )
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "state_in_use"


async def test_git_branch_delete_refuses_checked_out_branch(
    git_repo: Path, client: TestClient
) -> None:
    _git(git_repo, "checkout", "-q", "symphony/SEED-1")
    resp = await client.post(
        "/api/v1/git/branch/delete",
        json={"branch": "symphony/SEED-1", "force": True},
    )
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "checked_out"


async def test_git_push_sends_task_branch_to_remote(
    git_remote: Path, client: TestClient
) -> None:
    resp = await client.post("/api/v1/git/push", json={"branch": "symphony/SEED-1"})
    assert resp.status == 200
    payload = await resp.json()
    assert payload["ok"] is True and payload["remote"] == "origin"

    refs = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(git_remote),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "symphony/SEED-1" in refs


async def test_git_push_of_target_branch_requires_confirmation(
    git_remote: Path, client: TestClient
) -> None:
    resp = await client.post("/api/v1/git/push", json={"branch": "main"})
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "confirm_required"

    resp = await client.post(
        "/api/v1/git/push", json={"branch": "main", "confirm": "nope"}
    )
    assert resp.status == 400

    resp = await client.post(
        "/api/v1/git/push", json={"branch": "main", "confirm": "main"}
    )
    assert resp.status == 200
    assert (await resp.json())["status"] == "pushed"


async def test_git_push_rejects_unrelated_branch_and_missing_remote(
    git_repo: Path, client: TestClient
) -> None:
    _git(git_repo, "branch", "scratch")
    resp = await client.post("/api/v1/git/push", json={"branch": "scratch"})
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "branch_not_pushable"

    # Task branch is in scope, but this repo still has no remote.
    resp = await client.post("/api/v1/git/push", json={"branch": "symphony/SEED-1"})
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "no_remote"


async def test_git_pr_requires_gh_and_a_pushed_branch(
    git_remote: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("symphony.utils.git_ops.gh_available", lambda: False)
    resp = await client.post("/api/v1/git/pr", json={"branch": "symphony/SEED-1"})
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "gh_unavailable"

    monkeypatch.setattr("symphony.utils.git_ops.gh_available", lambda: True)
    resp = await client.post("/api/v1/git/pr", json={"branch": "symphony/SEED-1"})
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "branch_not_pushed"


async def test_git_pr_creates_pull_request_with_ticket_title(
    git_remote: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_create(
        workflow_dir: Path, branch: str, target: str, title: str, body: str
    ):
        calls.append((branch, target, title, body))
        return GitOpResult(True, "created", "", url="https://example.test/pr/1")

    monkeypatch.setattr("symphony.utils.git_ops.gh_available", lambda: True)
    monkeypatch.setattr(
        "symphony.utils.git_ops.branch_on_remote", lambda *_a, **_k: True
    )
    monkeypatch.setattr("symphony.utils.git_ops.create_pull_request", fake_create)

    resp = await client.post("/api/v1/git/pr", json={"branch": "symphony/SEED-1"})
    assert resp.status == 200
    payload = await resp.json()
    assert payload["url"] == "https://example.test/pr/1"
    assert payload["target"] == "main"
    branch, target, title, body = calls[0]
    assert branch == "symphony/SEED-1" and target == "main"
    assert title.startswith("SEED-1")
    assert "SEED-1" in body


async def test_git_diff_returns_patch_for_branch_and_commit(
    git_repo: Path, client: TestClient
) -> None:
    resp = await client.get("/api/v1/git/diff?branch=symphony/SEED-1")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["target"] == "main"
    assert "diff --git a/feature.py b/feature.py" in payload["patch"]
    assert payload["truncated"] is False

    resp = await client.get("/api/v1/git/diff?branch=symphony/SEED-1&path=feature.py")
    assert "feature.py" in (await resp.json())["patch"]

    sha = subprocess.run(
        ["git", "rev-parse", "symphony/SEED-1"],
        cwd=str(git_repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    resp = await client.get(f"/api/v1/git/diff?commit={sha}")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["commit"] == sha
    assert "SEED-1: feature" in payload["patch"]

    resp = await client.get("/api/v1/git/diff?commit=zzzz")
    assert resp.status == 400
    resp = await client.get("/api/v1/git/diff?commit=deadbeef")
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "unknown_ref"
    resp = await client.get("/api/v1/git/diff?branch=no-such-branch")
    assert resp.status == 400
    resp = await client.get("/api/v1/git/diff?branch=symphony/SEED-1&path=--evil")
    assert resp.status == 400


# ---------------------------------------------------------------------------
# workflow: lane presets
# ---------------------------------------------------------------------------


async def test_lane_presets_get_lists_shipped_presets(client: TestClient) -> None:
    resp = await client.get("/api/v1/workflow/presets")
    assert resp.status == 200
    payload = await resp.json()
    assert [p["name"] for p in payload["presets"]] == ["default", "deep"]
    deep = payload["presets"][1]
    assert deep["active_states"][0] == "Intake"
    assert deep["active_states"][-1] == "Document"
    # The fixture board (Todo/Doing) matches no shipped preset.
    assert payload["current"] is None


async def test_lane_preset_apply_rewrites_workflow_and_migrates_tickets(
    client: TestClient, board_dir: Path
) -> None:
    await client.patch("/api/v1/issues/SEED-1", json={"state": "Doing"})

    resp = await client.post("/api/v1/workflow/presets/apply", json={"name": "deep"})
    assert resp.status == 200
    payload = await resp.json()
    assert payload["applied"] == "deep"
    assert payload["removed"] == ["Todo", "Doing"]
    assert payload["fallback_state"] == "Intake"
    assert payload["migrated"] == {"SEED-1": "Intake"}
    text = (board_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    assert (
        "active_states: [Intake, Research, Plan, Review, Build, QA, Verify, Document]"
        in text
    )
    assert "base: ./docs/symphony-prompts/file/deep/base.md" in text
    detail = await (await client.get("/api/v1/issues/SEED-1")).json()
    assert detail["state"] == "Intake"

    # The board now guesses as the deep preset.
    presets = await (await client.get("/api/v1/workflow/presets")).json()
    assert presets["current"] == "deep"


async def test_lane_preset_apply_rejects_bad_payloads(client: TestClient) -> None:
    resp = await client.post("/api/v1/workflow/presets/apply", json={})
    assert resp.status == 400
    resp = await client.post("/api/v1/workflow/presets/apply", json={"name": "mystery"})
    assert resp.status == 400
    assert "unknown lane preset" in (await resp.json())["error"]["message"]


async def test_lane_preset_apply_blocks_running_worker_in_removed_lane(
    client: TestClient, board_dir: Path
) -> None:
    stub = client.stub  # type: ignore[attr-defined]
    seed = await (await client.get("/api/v1/issues/SEED-1")).json()
    running = SimpleNamespace(identifier="SEED-1", state=seed["state"])
    stub.iter_running_issues = lambda: (running,)
    before = (board_dir / "WORKFLOW.md").read_bytes()

    resp = await client.post("/api/v1/workflow/presets/apply", json={"name": "deep"})

    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "state_in_use"
    assert (board_dir / "WORKFLOW.md").read_bytes() == before


async def test_lane_preset_apply_warns_when_max_turns_cannot_cover_the_lanes(
    client: TestClient, board_dir: Path
) -> None:
    """F-23: nothing validated the board after a preset switch."""
    workflow = board_dir / "WORKFLOW.md"
    text = workflow.read_text(encoding="utf-8")
    assert "agent:" in text
    workflow.write_text(
        text.replace("agent:", "agent:\n  max_turns: 3", 1), encoding="utf-8"
    )
    client.stub.workflow_state.reload()  # type: ignore[attr-defined]

    resp = await client.post("/api/v1/workflow/presets/apply", json={"name": "deep"})

    assert resp.status == 200
    payload = await resp.json()
    assert payload["applied"] == "deep"
    assert payload["warning"] is not None
    assert "agent.max_turns=3" in payload["warning"]


async def test_lane_preset_apply_reports_no_warning_for_a_sane_budget(
    client: TestClient,
) -> None:
    resp = await client.post("/api/v1/workflow/presets/apply", json={"name": "default"})

    assert resp.status == 200
    assert (await resp.json())["warning"] is None


@pytest.mark.asyncio
async def test_product_preview_status_defaults_disabled(client: TestClient):
    response = await client.get("/api/v1/preview")
    assert response.status == 200
    payload = await response.json()
    assert payload["enabled"] is False
    assert payload["phase"] == "disabled"


@pytest.mark.asyncio
async def test_product_preview_start_rejects_disabled(client: TestClient):
    response = await client.post("/api/v1/preview/start", json={})
    assert response.status == 409
    payload = await response.json()
    assert payload["error"]["code"] == "preview_error"


@pytest.mark.asyncio
async def test_product_preview_process_controls_require_json(client: TestClient):
    response = await client.post("/api/v1/preview/stop")
    assert response.status == 415
    payload = await response.json()
    assert payload["error"]["code"] == "unsupported_media_type"


@pytest.mark.asyncio
async def test_product_preview_start_waits_for_done_release_ticket(
    board_dir: Path,
):
    workflow = board_dir / "WORKFLOW.md"
    text = workflow.read_text(encoding="utf-8").replace(
        "agent:\n  kind: claude\n",
        "agent:\n  kind: claude\n  auto_merge_target_branch: dev\n\n"
        "preview:\n  enabled: true\n  command: python3 -m http.server ${PORT} --bind ${HOST}\n"
        "  release_ticket: SEED-1\n",
    )
    workflow.write_text(text, encoding="utf-8")
    state = WorkflowState(workflow)
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    try:
        response = await cli.post("/api/v1/preview/start", json={})
        assert response.status == 409
        payload = await response.json()
        assert payload["error"]["code"] == "release_not_ready"
    finally:
        await cli.close()


# ---------------------------------------------------------------------------
# project identity, management, and independent switching
# ---------------------------------------------------------------------------


class _FakeProjectRegistry:
    def __init__(self, projects: list[Any], *, broken: set[str] | None = None) -> None:
        self.projects = projects
        self.broken = broken or set()
        self.running: set[str] = set()
        self.started: list[str] = []

    def list(self) -> list[Any]:
        return list(self.projects)

    def get(self, project_id: str) -> Any:
        for project in self.projects:
            if project.id == project_id:
                return project
        from symphony.projects import ProjectError

        raise ProjectError(
            f"unknown project {project_id!r}; run `symphony project list`"
        )

    def status(self, project_id: str) -> Any:
        if project_id in self.broken:
            raise RuntimeError("private status detail")
        project = self.get(project_id)
        record = SimpleNamespace(host=project.host, port=project.port)
        return SimpleNamespace(
            state="running" if project_id in self.running else "stopped", record=record
        )

    def start(self, project_id: str) -> int:
        self.started.append(project_id)
        self.running.add(project_id)
        return 0


async def _project_client(
    board_dir: Path, monkeypatch: pytest.MonkeyPatch, registry: Any
) -> TestClient:
    from symphony import webapi

    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    monkeypatch.setattr(webapi, "ProjectRegistry", lambda: registry)
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_projects_expose_current_canonical_paths_and_isolate_broken_status(
    board_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.projects import Project

    current = Project(
        "current",
        "Current",
        str(board_dir.resolve()),
        str((board_dir / "WORKFLOW.md").resolve()),
        "127.0.0.1",
        9999,
    )
    other = Project(
        "other",
        "Other",
        str((board_dir / "other").resolve()),
        str((board_dir / "other" / "WORKFLOW.md").resolve()),
        "127.0.0.1",
        10000,
    )
    client = await _project_client(
        board_dir, monkeypatch, _FakeProjectRegistry([current, other], broken={"other"})
    )
    try:
        response = await client.get("/api/v1/projects")
        assert response.status == 200
        body = await response.json()
        assert body["current"] == {
            "id": "current",
            "name": "Current",
            "repo_path": str(board_dir.resolve()),
            "workflow_path": str((board_dir / "WORKFLOW.md").resolve()),
            "board_path": str((board_dir / "kanban").resolve()),
            "registered": True,
        }
        assert body["projects"][0]["current"] is True
        assert body["projects"][1]["status_error"] == "status unavailable"
        assert "private status detail" not in await response.text()
    finally:
        await client.close()


async def test_open_project_starts_only_destination_and_returns_independent_url(
    board_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.projects import Project

    destination = Project(
        "other", "Other", "/tmp/other", "/tmp/other/WORKFLOW.md", "0.0.0.0", 10001
    )
    registry = _FakeProjectRegistry([destination])
    client = await _project_client(board_dir, monkeypatch, registry)
    try:
        empty = await client.post("/api/v1/projects/other/open")
        assert empty.status == 415
        text = await client.post(
            "/api/v1/projects/other/open",
            data="{}",
            headers={"Content-Type": "text/plain"},
        )
        assert text.status == 415
        assert registry.started == []

        response = await client.post("/api/v1/projects/other/open", json={})
        assert response.status == 200
        assert await response.json() == {
            "project_id": "other",
            "running": True,
            "url": "http://127.0.0.1:10001/",
        }
        assert registry.started == ["other"]
        assert client.server.app is not None
    finally:
        await client.close()


async def test_open_project_start_failure_returns_actionable_guidance(
    board_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.projects import Project

    destination = Project(
        "other", "Other", "/tmp/other", "/tmp/other/WORKFLOW.md", "127.0.0.1", 10001
    )
    registry = _FakeProjectRegistry([destination])
    registry.start = lambda _project_id: 1  # type: ignore[method-assign]
    client = await _project_client(board_dir, monkeypatch, registry)
    try:
        response = await client.post("/api/v1/projects/other/open", json={})
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 409
    assert payload["error"]["code"] == "project_open_failed"
    assert (
        "symphony service status /tmp/other/WORKFLOW.md" in payload["error"]["message"]
    )
    assert "service command exited" not in payload["error"]["message"]


async def test_project_mutations_reject_cross_origin(
    board_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await _project_client(board_dir, monkeypatch, _FakeProjectRegistry([]))
    try:
        response = await client.post(
            "/api/v1/projects",
            json={"name": "Demo", "path": "/tmp/demo"},
            headers={"Origin": "https://attacker.example"},
        )
        assert response.status == 403
        assert (await response.json())["error"]["code"] == "forbidden_origin"
        opaque = await client.post(
            "/api/v1/projects",
            json={"name": "Demo", "path": "/tmp/demo"},
            headers={"Origin": "null"},
        )
        assert opaque.status == 403
        assert (await opaque.json())["error"]["code"] == "forbidden_origin"
    finally:
        await client.close()


async def test_project_mutations_accept_local_and_declared_origins(
    board_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TLS-terminating proxy or tunnel must not look like an attacker.

    The empty payload stops each request at field validation, so a 400
    proves the origin guard let it through without touching the registry.
    """
    client = await _project_client(board_dir, monkeypatch, _FakeProjectRegistry([]))
    try:
        # Same machine, different scheme and port than the browser used.
        for origin in ("https://127.0.0.1:9999", "http://localhost:1234"):
            allowed = await client.post(
                "/api/v1/projects", json={"name": "", "path": ""},
                headers={"Origin": origin},
            )
            assert allowed.status == 400, origin
            assert (await allowed.json())["error"]["code"] == "invalid_project_name"

        tunnel = "https://symphony.example.com"
        blocked = await client.post(
            "/api/v1/projects",
            json={"name": "", "path": ""},
            headers={"Origin": tunnel},
        )
        assert blocked.status == 403
        assert TRUSTED_ORIGINS_ENV in (await blocked.json())["error"]["message"]

        monkeypatch.setenv(TRUSTED_ORIGINS_ENV, f"{tunnel} , https://other.example")
        declared = await client.post(
            "/api/v1/projects",
            json={"name": "", "path": ""},
            headers={"Origin": tunnel},
        )
        assert declared.status == 400
        assert (await declared.json())["error"]["code"] == "invalid_project_name"
    finally:
        await client.close()


async def test_declared_origin_host_passes_the_api_host_guard(
    board_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proxies that forward the public Host verbatim reach the API too."""
    client = await _project_client(board_dir, monkeypatch, _FakeProjectRegistry([]))
    try:
        rejected = await client.get(
            "/api/v1/projects", headers={"Host": "symphony.example.com"}
        )
        assert rejected.status == 403
        assert (await rejected.json())["error"]["code"] == "forbidden_host"

        monkeypatch.setenv(TRUSTED_ORIGINS_ENV, "https://symphony.example.com")
        accepted = await client.get(
            "/api/v1/projects", headers={"Host": "symphony.example.com"}
        )
        assert accepted.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_runs_endpoint_supports_explorer_filters(client: TestClient) -> None:
    resp = await client.get(
        "/api/v1/runs?query=other&status=force_ejected_zombie&agent=codex"
    )
    assert resp.status == 200
    payload = await resp.json()
    assert [row["identifier"] for row in payload["runs"]] == ["OTHER-1"]
    agent_query = await client.get("/api/v1/runs?query=codex")
    assert [row["identifier"] for row in (await agent_query.json())["runs"]] == [
        "OTHER-1"
    ]
    status_query = await client.get("/api/v1/runs?query=force_ejected_zombie")
    assert [row["identifier"] for row in (await status_query.json())["runs"]] == [
        "OTHER-1"
    ]


@pytest.mark.asyncio
async def test_run_detail_and_attachment_diagnostic_endpoints(
    client: TestClient,
) -> None:
    run_id = "a" * 32
    detail_resp = await client.get(f"/api/v1/runs/{run_id}")
    assert detail_resp.status == 200
    assert detail_resp.headers["Cache-Control"] == "no-store"
    detail = await detail_resp.json()
    assert detail["run"]["title"] == "seeded ticket"
    assert detail["events"][0]["event_type"] == "run_completed"

    diagnostic_resp = await client.get(f"/api/v1/runs/{run_id}/diagnostic")
    assert diagnostic_resp.status == 200
    assert diagnostic_resp.headers["Content-Type"].startswith("application/json")
    assert diagnostic_resp.headers["Cache-Control"] == "no-store, private"
    assert diagnostic_resp.headers["X-Content-Type-Options"] == "nosniff"
    assert diagnostic_resp.headers["Content-Disposition"] == (
        f'attachment; filename="symphony-run-{run_id}-diagnostic.json"'
    )
    diagnostic = await diagnostic_resp.json()
    assert diagnostic["schema_version"] == 1


@pytest.mark.asyncio
async def test_run_detail_validates_ids_and_returns_not_found(
    client: TestClient,
) -> None:
    invalid = await client.get("/api/v1/runs/not-a-run")
    assert invalid.status == 400
    assert (await invalid.json())["error"]["code"] == "invalid_run_id"
    uppercase = await client.get(f"/api/v1/runs/{'A' * 32}")
    assert uppercase.status == 400

    missing = await client.get(f"/api/v1/runs/{'b' * 32}")
    assert missing.status == 404
    assert (await missing.json())["error"]["code"] == "run_not_found"


def test_run_diagnostics_loopback_guard() -> None:
    assert _request_is_loopback(SimpleNamespace(remote="127.0.0.1", app={}))  # type: ignore[arg-type]
    assert not _request_is_loopback(SimpleNamespace(remote="10.0.0.8", app={}))  # type: ignore[arg-type]


def test_schedule_reason_taxonomy_covers_every_authoritative_code() -> None:
    required = {
        "not_evaluated",
        "ready",
        "dispatched",
        "running",
        "retry_scheduled",
        "auto_triage",
        "continuous_improvement",
        "leased_elsewhere",
        "registry_unavailable",
        "historical_release_verifier",
        "claimed",
        "paused",
        "budget_exhausted",
        "finalizing",
        "inactive",
        "incomplete_identity",
        "unsupported_agent",
        "waiting_dependency",
        "waiting_global_capacity",
        "waiting_state_capacity",
        "refused_conflict",
        "refused_dispatch_authority",
        "terminal_success",
        "terminal_needs_action",
        "dangling_dependency",
        "snapshot_unavailable",
        "decision_stale",
    }
    assert required == set(_PUBLIC_SCHEDULE_REASONS)
    app = Path("src/symphony/web/static/app.js").read_text(encoding="utf-8")
    for code in required:
        assert f"      {code}: t('schedule." in app


# ---------------------------------------------------------------------------
# ticket artifacts
# ---------------------------------------------------------------------------


def _seed_artifact(
    board_dir: Path,
    identifier: str = "SEED-1",
    name: str = "shot.png",
    content: bytes = b"png-bytes",
    *,
    content_type: str = "image/png",
    title: str = "Login page",
    summary: str = "After the fix",
) -> None:
    store = board_dir / ".symphony" / "artifacts" / identifier
    (store / "files").mkdir(parents=True, exist_ok=True)
    (store / "files" / name).write_bytes(content)
    index = store / "index.json"
    entries = []
    if index.exists():
        entries = json.loads(index.read_text(encoding="utf-8"))["entries"]
    entries.append(
        {
            "name": name,
            "title": title,
            "summary": summary,
            "content_type": content_type,
            "byte_size": len(content),
            "sha256": "0" * 64,
            "collected_at": "2026-08-12T00:00:00Z",
            "run_id": "run-1",
            "turn": 2,
        }
    )
    index.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")


async def test_issue_artifacts_lists_collected_files(
    client: TestClient, board_dir: Path
) -> None:
    _seed_artifact(board_dir)

    resp = await client.get("/api/v1/issues/SEED-1/artifacts")

    assert resp.status == 200
    payload = await resp.json()
    assert payload["enabled"] is True
    assert len(payload["artifacts"]) == 1
    entry = payload["artifacts"][0]
    assert entry["name"] == "shot.png"
    assert entry["title"] == "Login page"
    assert entry["summary"] == "After the fix"
    assert entry["byte_size"] == 9
    assert entry["inline"] is True
    assert entry["url"] == "/api/v1/issues/SEED-1/artifacts/shot.png"


async def test_issue_artifacts_empty_for_unknown_ticket(client: TestClient) -> None:
    resp = await client.get("/api/v1/issues/NOPE-9/artifacts")
    assert resp.status == 200
    assert (await resp.json())["artifacts"] == []


async def test_issue_detail_carries_artifacts(
    client: TestClient, board_dir: Path
) -> None:
    _seed_artifact(board_dir)

    resp = await client.get("/api/v1/issues/SEED-1")

    assert resp.status == 200
    payload = await resp.json()
    assert [a["name"] for a in payload["artifacts"]] == ["shot.png"]


async def test_artifact_file_serves_image_inline(
    client: TestClient, board_dir: Path
) -> None:
    _seed_artifact(board_dir)

    resp = await client.get("/api/v1/issues/SEED-1/artifacts/shot.png")

    assert resp.status == 200
    assert await resp.read() == b"png-bytes"
    assert resp.headers["Content-Type"] == "image/png"
    assert resp.headers["Content-Disposition"].startswith("inline")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


async def test_artifact_file_forces_download_for_executable_types(
    client: TestClient, board_dir: Path
) -> None:
    """Worker-authored HTML/SVG must never render on the board's origin."""
    _seed_artifact(
        board_dir,
        name="evil.html",
        content=b"<script>alert(1)</script>",
        content_type="text/html",
        title="Report",
    )

    resp = await client.get("/api/v1/issues/SEED-1/artifacts/evil.html")

    assert resp.status == 200
    assert resp.headers["Content-Disposition"].startswith("attachment")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


async def test_artifact_file_rejects_unlisted_and_traversal_names(
    client: TestClient, board_dir: Path
) -> None:
    _seed_artifact(board_dir)
    (board_dir / "secret.txt").write_text("classified", encoding="utf-8")

    missing = await client.get("/api/v1/issues/SEED-1/artifacts/other.png")
    assert missing.status == 404

    # index.json sits beside files/ and is never a listed artifact.
    index = await client.get("/api/v1/issues/SEED-1/artifacts/index.json")
    assert index.status == 404

    escape = await client.get(
        "/api/v1/issues/SEED-1/artifacts/..%2F..%2F..%2Fsecret.txt"
    )
    assert escape.status in (400, 404)
    if escape.status == 200:  # pragma: no cover - guard against silent regress
        assert b"classified" not in await escape.read()


def test_executable_content_types_are_never_inline() -> None:
    """Pin the allowlist: these render script on the board's own origin.

    Adding `image/svg+xml` here to "preview diagrams" would flip both the
    server (Content-Disposition: inline) and the client (top-level
    same-origin link) in one edit, turning a worker-authored file into
    stored XSS. Keep this list a constraint, not a comment.
    """
    from symphony.webapi import _INLINE_ARTIFACT_TYPES

    for content_type in (
        "text/html",
        "image/svg+xml",
        "application/xhtml+xml",
        "text/xml",
        "application/xml",
        "application/javascript",
        "text/javascript",
    ):
        assert content_type not in _INLINE_ARTIFACT_TYPES


async def test_artifact_file_carries_a_sandbox_csp(
    client: TestClient, board_dir: Path
) -> None:
    """Backstop if a renderable type ever reaches the inline allowlist."""
    _seed_artifact(board_dir)

    resp = await client.get("/api/v1/issues/SEED-1/artifacts/shot.png")

    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "sandbox" in csp
    assert "allow-downloads" in csp  # attachment responses must still save
    assert resp.headers["X-Frame-Options"] == "DENY"
