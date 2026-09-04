"""Intent proposals and approval: the single human gate of the chat cycle.

The chat agent may only *propose* an intent through the strict marker; the
operator approves it from the card (or a bare ``approve`` reply) and the
server files the request ticket. These tests cover the parser's strictness,
the session bookkeeping, and the approval contract (token, expiry,
idempotency, failure path, agent notice).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from symphony.chat import ChatManager
from symphony.errors import ChatIntentActionError, ChatIntentAuthorizationError
from symphony.intent import IntentAction, parse_intent_marker
from symphony.workflow import ServiceConfig, WorkflowState

from tests.test_chat import (
    CONFIRMATION_TOKEN,
    WORKFLOW_TEXT,
    _cfg,
    _wait_turn,
    fake_backends,
)

# pytest discovers the imported fixture by name; the tuple marks it used so
# ruff does not flag the test parameters as redefinitions (F811).
_SHARED_FIXTURES = (fake_backends,)

INTENT_BODY = (
    "## Problem\n\nOperators cannot see failed turns.\n\n"
    "## Evidence\n\n- log lines show worker_exit with error [verified: log]\n\n"
    "## Success criteria\n\n- [ ] the board card shows the last error\n"
    "- [ ] `pytest tests/test_board.py` passes\n\n"
    "## Out of scope\n\n- TUI changes\n\n"
    "## Constraints\n\n- no new dependency\n\n"
    "## Open questions\n\n- none\n"
)


def _marker(**overrides: Any) -> str:
    payload = {
        "slug": "show-last-error",
        "title": "Show the last error on the board card",
        "track": "full",
        "intent": INTENT_BODY,
    }
    payload.update(overrides)
    return "<symphony-intent>" + json.dumps(payload) + "</symphony-intent>"


def _cfg_linear(tmp_path: Path) -> ServiceConfig:
    text = WORKFLOW_TEXT.replace("kind: file\n  board_root: ./kanban\n", "kind: linear\n")
    text = text.replace("tracker:\n", "tracker:\n  api_key: x\n  project_slug: p\n  team_key: t\n", 1)
    (tmp_path / "WORKFLOW.md").write_text(text, encoding="utf-8")
    state = WorkflowState(tmp_path / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    return cfg


# ---------------------------------------------------------------------------
# parser strictness
# ---------------------------------------------------------------------------


def test_parse_intent_marker_accepts_the_documented_shape() -> None:
    visible, action = parse_intent_marker("Here is my proposal.\n" + _marker())
    assert action is not None
    assert visible == "Here is my proposal."
    assert action.slug == "show-last-error"
    assert action.track == "full"
    assert action.status == "pending"
    assert action.action_id.startswith("intent-")
    assert "## Success criteria" in action.intent
    assert "symphony-intent" not in visible


@pytest.mark.parametrize(
    "text",
    [
        "no marker at all",
        "<symphony-intent>not json</symphony-intent>",
        "<symphony-intent>" + json.dumps({"slug": "a-b"}) + "</symphony-intent>",
        _marker(extra="field"),
        _marker(slug="Bad Slug"),
        _marker(slug="a"),
        _marker(track="huge"),
        _marker(title=""),
        _marker(title="t" * 201),
        _marker(intent="## Problem\n\nno success section\n\n## Out of scope\n"),
        _marker(intent="## Problem\n\n## Success criteria\n\nno checkbox\n\n## Out of scope\n"),
        _marker(intent="x" * (32 * 1024 + 1)),
        # two openers or two closers stay prose (no quadratic rescans either)
        _marker() + "<symphony-intent>",
        "</symphony-intent>" + _marker(),
    ],
)
def test_parse_intent_marker_rejects_malformed_payloads(text: str) -> None:
    visible, action = parse_intent_marker(text)
    assert action is None
    assert visible == text


def test_parse_intent_marker_rejects_duplicate_json_members() -> None:
    raw = (
        '<symphony-intent>{"slug": "a-b", "slug": "c-d", "title": "t", '
        '"track": "full", "intent": ' + json.dumps(INTENT_BODY) + "}</symphony-intent>"
    )
    assert parse_intent_marker(raw) == (raw, None)


# ---------------------------------------------------------------------------
# session bookkeeping
# ---------------------------------------------------------------------------


async def test_intent_proposal_is_recorded_in_both_modes(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    cfg = _cfg(tmp_path)
    for mode in ("qa", "edit"):
        manager = ChatManager(lambda: cfg)
        await manager.start_session(mode, confirmation_token=CONFIRMATION_TOKEN)
        session = manager.active_session
        assert session is not None
        manager._record_agent_message(session, "Proposal:\n" + _marker())
        [action] = session.intent_actions.values()
        assert action.status == "pending"
        assert [m.type for m in session.transcript[-2:]] == [
            "agent_message",
            "intent_action",
        ]
        assert "symphony-intent" not in session.transcript[-2].text
        assert manager.snapshot()["intent_actions"] == [action.as_dict()]
        await manager.stop_session()


async def test_intent_proposal_requires_a_confirmation_capability(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    manager = ChatManager(lambda: _cfg(tmp_path))
    await manager.start_session("qa")  # raw API session, no browser token
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(session, _marker())
    assert session.intent_actions == {}
    assert "Intent approval is unavailable" in session.transcript[-1].text
    await manager.stop_session()


async def test_intent_marker_is_plain_text_on_non_file_trackers(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    manager = ChatManager(lambda: _cfg_linear(tmp_path))
    await manager.start_session("qa", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(session, _marker())
    assert session.intent_actions == {}
    assert "symphony-intent" in session.transcript[-1].text
    await manager.stop_session()


async def test_ordinary_reply_supersedes_pending_intent(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    manager = ChatManager(lambda: _cfg(tmp_path))
    await manager.start_session("qa", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(session, _marker())
    [first] = session.intent_actions.values()

    await manager.send_message("please narrow the scope to the web board")
    await _wait_turn(manager)
    assert first.status == "superseded"
    assert manager.intent_for_reply("approve") is None
    statuses = [m for m in session.transcript if m.type == "intent_status"]
    assert statuses and statuses[-1].meta["intent"]["status"] == "superseded"

    # A newer proposal supersedes any pending one and becomes the live card.
    manager._record_agent_message(session, _marker(slug="web-board-error"))
    manager._record_agent_message(session, _marker(slug="web-board-error-v2"))
    live = manager.intent_for_reply("approve")
    assert live is not None and live.slug == "web-board-error-v2"
    assert manager.intent_for_reply("approve web-board-error") is None
    assert manager.intent_for_reply("APPROVE web-board-error-v2") is live
    assert manager.intent_for_reply("approve please now") is None
    await manager.stop_session()


async def test_intent_expiry_fails_closed(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    manager = ChatManager(lambda: _cfg(tmp_path))
    await manager.start_session("qa", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(session, _marker())
    [action] = session.intent_actions.values()
    action.expires_at = "2000-01-01T00:00:00Z"
    assert manager.intent_for_reply("approve") is None
    with pytest.raises(ChatIntentActionError, match="expired"):
        await manager.confirm_intent(action.action_id, confirmation_token=CONFIRMATION_TOKEN)
    assert manager.snapshot()["intent_actions"][0]["status"] == "expired"
    await manager.stop_session()


# ---------------------------------------------------------------------------
# approval contract
# ---------------------------------------------------------------------------


async def test_confirm_intent_files_once_and_notifies_the_agent(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    cfg = _cfg(tmp_path)
    calls: list[tuple[str, str]] = []
    refreshes: list[int] = []

    def filer(
        cfg_arg: ServiceConfig, action: IntentAction, *, approved_at: str, session_id: str
    ) -> dict[str, Any]:
        assert cfg_arg is cfg and approved_at.endswith("Z") and session_id
        calls.append((action.slug, action.track))
        return {"identifier": "REQ-1", "state": "Todo", "request": action.slug}

    manager = ChatManager(
        lambda: cfg, request_refresh=lambda: refreshes.append(1), intent_filer=filer
    )
    await manager.start_session("qa", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(session, _marker())
    action = manager.intent_for_reply("approve")
    assert action is not None

    with pytest.raises(ChatIntentAuthorizationError):
        await manager.confirm_intent(action.action_id)
    with pytest.raises(ChatIntentActionError, match="unknown intent action"):
        await manager.confirm_intent("intent-" + "0" * 32, confirmation_token=CONFIRMATION_TOKEN)

    first, second = await asyncio.gather(
        manager.confirm_intent(action.action_id, confirmation_token=CONFIRMATION_TOKEN),
        manager.confirm_intent(action.action_id, confirmation_token=CONFIRMATION_TOKEN),
    )
    assert first["status"] == second["status"] == "approved"
    assert first["ticket"]["identifier"] == "REQ-1"
    assert calls == [("show-last-error", "full")]
    assert refreshes == [1]
    # Idempotent after completion as well.
    again = await manager.confirm_intent(action.action_id, confirmation_token=CONFIRMATION_TOKEN)
    assert again["status"] == "approved" and calls == [("show-last-error", "full")]
    assert manager.intent_for_reply("approve") is None

    # The agent learns about the filed ticket on its next turn.
    await manager.send_message("thanks")
    await _wait_turn(manager)
    prompt, _ = fake_backends[-1].turns[-1]
    assert "approved intent 'show-last-error'" in prompt
    assert "REQ-1" in prompt and prompt.endswith("thanks")
    assert session.pending_notices == []
    await manager.stop_session()


async def test_confirm_intent_failure_stays_retryable(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    attempts: list[int] = []

    def filer(cfg: ServiceConfig, action: IntentAction, *, approved_at: str, session_id: str):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("board locked by another writer")
        return {"identifier": "REQ-2", "state": "Todo", "request": action.slug}

    manager = ChatManager(lambda: _cfg(tmp_path), intent_filer=filer)
    await manager.start_session("edit", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(session, _marker())
    [action] = session.intent_actions.values()

    failed = await manager.confirm_intent(action.action_id, confirmation_token=CONFIRMATION_TOKEN)
    assert failed["status"] == "failed"
    assert "board locked" in (failed["error"] or "")
    assert manager.intent_for_reply("approve") is action  # still approvable

    ok = await manager.confirm_intent(action.action_id, confirmation_token=CONFIRMATION_TOKEN)
    assert ok["status"] == "approved" and ok["ticket"]["identifier"] == "REQ-2"
    assert attempts == [1, 1]
    await manager.stop_session()


async def test_superseded_intent_cannot_be_approved(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    manager = ChatManager(lambda: _cfg(tmp_path), intent_filer=lambda *a, **k: {})
    await manager.start_session("qa", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    manager._record_agent_message(session, _marker())
    [old] = session.intent_actions.values()
    manager._record_agent_message(session, _marker(slug="newer-idea"))
    with pytest.raises(ChatIntentActionError, match="superseded"):
        await manager.confirm_intent(old.action_id, confirmation_token=CONFIRMATION_TOKEN)
    await manager.stop_session()


async def test_intent_actions_are_bounded_per_session(
    tmp_path: Path, fake_backends: list[Any]
) -> None:
    manager = ChatManager(lambda: _cfg(tmp_path))
    await manager.start_session("qa", confirmation_token=CONFIRMATION_TOKEN)
    session = manager.active_session
    assert session is not None
    for i in range(25):
        manager._record_agent_message(session, _marker(slug=f"idea-{i}"))
    assert len(session.intent_actions) <= 20
    live = [a for a in session.intent_actions.values() if a.status == "pending"]
    assert [a.slug for a in live] == ["idea-24"]
    removed = [m for m in session.transcript if m.type == "intent_removed"]
    assert removed, "stale superseded cards are pruned"
    await manager.stop_session()
