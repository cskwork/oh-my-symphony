"""The server-owned filer that turns an approved intent into board work."""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.errors import ChatIntentActionError
from symphony.intent import (
    IntentAction,
    file_intent_request,
    intent_artifact_path,
    intent_target_state,
)
from symphony.workflow import ServiceConfig, WorkflowState

from tests.test_chat_intent import INTENT_BODY, _cfg_linear

WORKFLOW = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [{states}]
  terminal_states: [Done, Archive]

agent:
  kind: claude
---

You are working on {{{{ issue.identifier }}}}.
"""


def _cfg(tmp_path: Path, states: str) -> ServiceConfig:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW.format(states=states), encoding="utf-8")
    (tmp_path / "kanban").mkdir(exist_ok=True)
    state = WorkflowState(tmp_path / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    return cfg


def _action(slug: str = "todo-app", track: str = "full") -> IntentAction:
    return IntentAction(
        action_id="intent-" + "a" * 32,
        slug=slug,
        title="Build a Todo web app",
        track=track,
        intent=INTENT_BODY,
    )


def test_target_state_prefers_intake_then_first_active(tmp_path: Path) -> None:
    deep = _cfg(tmp_path, "Intake, Research, Plan, Review, Build, QA, Verify, Document")
    assert intent_target_state(deep) == "Intake"
    default = _cfg(tmp_path, "Todo, \"In Progress\", Verify, Document")
    assert intent_target_state(default) == "Todo"


def test_file_intent_request_creates_ticket_and_artifact(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, "Intake, Research, Plan, Review, Build, QA, Verify, Document")
    result = file_intent_request(
        cfg, _action(), approved_at="2026-09-05T10:00:00Z", session_id="20260905-100000-abcdef"
    )
    assert result["identifier"] == "REQ-1"
    assert result["state"] == "Intake"
    assert result["request"] == "todo-app"
    ticket = Path(result["path"])
    assert ticket == tmp_path / "kanban" / "REQ-1.md"
    text = ticket.read_text(encoding="utf-8")
    assert "state: Intake" in text
    assert "request: todo-app" in text
    assert "## Problem" in text and "## Success criteria" in text
    assert "## Track\n\nfull" in text
    assert "## Approval" in text and "2026-09-05T10:00:00Z" in text

    artifact = intent_artifact_path(cfg, "todo-app")
    assert Path(result["intent_path"]) == artifact
    assert artifact == tmp_path / ".sdlc" / "work" / "todo-app" / "intent.md"
    body = artifact.read_text(encoding="utf-8")
    assert body.startswith("# Intent: todo-app\n")
    assert "- Track: full" in body
    assert "- Ticket: REQ-1" in body
    assert "## Success criteria" in body

    # A second approval allocates the next request id and its own artifact.
    second = file_intent_request(
        cfg, _action(slug="reports", track="micro"), approved_at="2026-09-05T11:00:00Z", session_id="s"
    )
    assert second["identifier"] == "REQ-2"
    assert "## Track\n\nmicro" in Path(second["path"]).read_text(encoding="utf-8")


def test_file_intent_request_uses_first_state_on_default_boards(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, "Todo, \"In Progress\", Verify, Document")
    result = file_intent_request(cfg, _action(), approved_at="2026-09-05T10:00:00Z", session_id="s")
    assert result["state"] == "Todo"
    assert "state: Todo" in Path(result["path"]).read_text(encoding="utf-8")


def test_file_intent_request_refuses_non_file_trackers(tmp_path: Path) -> None:
    cfg = _cfg_linear(tmp_path)
    with pytest.raises(ChatIntentActionError, match="file board"):
        file_intent_request(cfg, _action(), approved_at="2026-09-05T10:00:00Z", session_id="s")
    assert not (tmp_path / ".sdlc").exists()
