"""Jira Cloud REST API v3 adapter — focused unit tests.

Drive JiraClient via `httpx.MockTransport`, exercising:
    * /search/jql token-pagination + field normalization (ADF, blockers).
    * /issue/{key}/transitions GET → POST handshake + transition lookup.
    * /issue/{key}/comment ADF wrapping for `append_note`.
    * Error paths: missing transition, non-2xx, malformed JSON.
"""

from __future__ import annotations

import json

import httpx
import pytest

import symphony.trackers.jira as jira_module
from symphony.errors import (
    JiraApiStatusError,
    JiraTransitionNotFound,
    JiraUnknownPayload,
)
from symphony.issue import Issue
from symphony.trackers.jira import JiraClient
from symphony.workflow import TrackerConfig


SITE = "https://example.atlassian.net"


def _cfg() -> TrackerConfig:
    return TrackerConfig(
        kind="jira",
        endpoint=SITE,
        api_key="tok",
        project_slug="PROJ",
        active_states=("To Do", "In Progress"),
        terminal_states=("Done",),
        email="user@example.com",
    )


def _issue(key: str = "PROJ-1", id_: str = "10001") -> Issue:
    return Issue(
        id=id_,
        identifier=key,
        title="t",
        description=None,
        priority=None,
        state="In Progress",
    )


def _client(handler) -> JiraClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url=SITE,
        auth=httpx.BasicAuth("user@example.com", "tok"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    return JiraClient(_cfg(), http_client=http)


def test_owned_http_client_uses_configured_network_timeout() -> None:
    cfg = _cfg()
    cfg = TrackerConfig(
        kind=cfg.kind,
        endpoint=cfg.endpoint,
        api_key=cfg.api_key,
        project_slug=cfg.project_slug,
        active_states=cfg.active_states,
        terminal_states=cfg.terminal_states,
        email=cfg.email,
        network_timeout_seconds=7.5,
    )
    client = JiraClient(cfg)
    try:
        assert client._client.timeout.connect == 7.5
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_fetch_candidate_issues_paginates_and_normalizes() -> None:
    """Two-page response, second page is_Last=True. JQL respects active_states."""
    calls: list[dict] = []

    page1 = {
        "isLast": False,
        "nextPageToken": "tok-2",
        "issues": [
            {
                "id": "10001",
                "key": "PROJ-1",
                "fields": {
                    "summary": "first",
                    "status": {"name": "In Progress"},
                    "priority": {"id": "3", "name": "Medium"},
                    "labels": ["backend", "p1"],
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "hello"}],
                            }
                        ],
                    },
                    "created": "2024-05-01T10:00:00.000+0000",
                    "updated": "2024-05-02T10:00:00.000+0000",
                    "issuelinks": [
                        {
                            "type": {"name": "Blocks"},
                            "inwardIssue": {
                                "id": "20001",
                                "key": "PROJ-5",
                                "fields": {"status": {"name": "Done"}},
                            },
                        }
                    ],
                },
            }
        ],
    }
    page2 = {
        "isLast": True,
        "issues": [
            {
                "id": "10002",
                "key": "PROJ-2",
                "fields": {
                    "summary": "second",
                    "status": {"name": "To Do"},
                    "priority": None,
                    "labels": [],
                    "description": None,
                },
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        if request.url.params.get("nextPageToken") == "tok-2":
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=page1)

    client = _client(handler)
    issues = client.fetch_candidate_issues()

    assert len(issues) == 2
    first, second = issues
    assert first.identifier == "PROJ-1"
    assert first.id == "10001"
    assert first.state == "In Progress"
    assert first.priority == 3
    assert first.labels == ("backend", "p1")
    assert first.description == "hello"
    assert first.url == f"{SITE}/browse/PROJ-1"
    assert first.created_at is not None and first.updated_at is not None
    assert len(first.blocked_by) == 1
    assert first.blocked_by[0].identifier == "PROJ-5"
    assert first.blocked_by[0].state == "Done"
    assert second.identifier == "PROJ-2"
    assert second.description is None

    assert len(calls) == 2
    assert "PROJ" in calls[0]["jql"]
    assert "To Do" in calls[0]["jql"] and "In Progress" in calls[0]["jql"]
    assert calls[1]["nextPageToken"] == "tok-2"


def test_fetch_candidate_issues_stops_at_max_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    warnings: list[tuple[str, dict]] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "isLast": False,
                "nextPageToken": f"tok-{calls}",
                "issues": [],
            },
        )

    monkeypatch.setattr(jira_module, "MAX_PAGES", 2)
    monkeypatch.setattr(
        jira_module.log,
        "warning",
        lambda message, **fields: warnings.append((message, fields)),
    )
    client = _client(handler)

    assert client.fetch_candidate_issues() == []
    assert calls == 2
    assert warnings == [("jira_pagination_max_pages", {"max_pages": 2})]


def test_fetch_issues_by_states_skips_empty_input() -> None:
    client = _client(lambda r: httpx.Response(500))  # never called
    assert client.fetch_issues_by_states([]) == []


def test_fetch_issue_states_by_ids_uses_id_jql() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["jql"] = request.url.params.get("jql")
        return httpx.Response(
            200,
            json={
                "isLast": True,
                "issues": [
                    {
                        "id": "10001",
                        "key": "PROJ-1",
                        "fields": {
                            "summary": "x",
                            "status": {"name": "Done"},
                            "updated": None,
                        },
                    }
                ],
            },
        )

    client = _client(handler)
    issues = client.fetch_issue_states_by_ids(["10001", "10002"])
    assert len(issues) == 1
    assert issues[0].state == "Done"
    assert captured["jql"].startswith("id in (")


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def test_update_state_resolves_transition_then_posts() -> None:
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            calls.append(("GET", request.url.path, {}))
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "11", "name": "Start", "to": {"name": "In Progress"}},
                        {"id": "31", "name": "Done", "to": {"name": "Done"}},
                    ]
                },
            )
        body = json.loads(request.content)
        calls.append(("POST", request.url.path, body))
        return httpx.Response(204)

    client = _client(handler)
    client.update_state(_issue("PROJ-1"), "Done")

    assert [(m, p) for m, p, _ in calls] == [
        ("GET", "/rest/api/3/issue/PROJ-1/transitions"),
        ("POST", "/rest/api/3/issue/PROJ-1/transitions"),
    ]
    assert calls[1][2] == {"transition": {"id": "31"}}


def test_update_state_matches_target_by_transition_name_too() -> None:
    """Some Jira workflows name transitions like the target status."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {
                            "id": "21",
                            "name": "Move to Review",
                            "to": {"name": "Review"},
                        }
                    ]
                },
            )
        return httpx.Response(204)

    client = _client(handler)
    client.update_state(_issue("PROJ-1"), "Review")


def test_update_state_raises_when_no_matching_transition() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transitions": [
                    {"id": "11", "name": "Start", "to": {"name": "In Progress"}}
                ]
            },
        )

    client = _client(handler)
    with pytest.raises(JiraTransitionNotFound):
        client.update_state(_issue("PROJ-1"), "Done")


def test_update_state_raises_when_post_non_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "31", "name": "Done", "to": {"name": "Done"}}
                    ]
                },
            )
        return httpx.Response(400, text="bad")

    client = _client(handler)
    with pytest.raises(JiraApiStatusError):
        client.update_state(_issue("PROJ-1"), "Done")


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def test_append_note_posts_adf_with_heading_first() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "10000"})

    client = _client(handler)
    client.append_note(_issue("PROJ-1"), "Symphony", "line1\nline2")

    assert captured["path"] == "/rest/api/3/issue/PROJ-1/comment"
    doc = captured["body"]["body"]
    assert doc["type"] == "doc" and doc["version"] == 1
    texts = [
        c["content"][0]["text"]
        for c in doc["content"]
        if c["content"]  # skip empty paragraph used for blank lines
    ]
    assert texts == ["Symphony", "line1", "line2"]


def test_search_raises_on_malformed_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = _client(handler)
    with pytest.raises(JiraUnknownPayload):
        client.fetch_candidate_issues()


# ---------------------------------------------------------------------------
# Structured notes + idempotent delivery
# ---------------------------------------------------------------------------


def _comment(cid: str, text: str) -> dict:
    return {"id": cid, "author": {"accountId": "me"}, "body": jira_module._adf_from_markdown(text)}


def test_append_note_renders_markdown_subset_as_adf() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "1"})

    client = _client(handler)
    client.append_note(_issue(), "", "### 의도\n- **상황** 신고\n\n- `Foo.java` 확인\n일반 문단")
    content = captured["body"]["body"]["content"]
    assert [n["type"] for n in content] == ["heading", "bulletList", "paragraph"]
    assert content[0]["attrs"]["level"] == 3
    items = content[1]["content"]
    assert len(items) == 2
    assert items[0]["content"][0]["content"][0] == {
        "type": "text", "text": "상황", "marks": [{"type": "strong"}]
    }
    assert items[1]["content"][0]["content"][0]["marks"] == [{"type": "code"}]


def test_fetch_comments_paginates_and_refuses_truncation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("startAt", "0"))
        if start == 0:
            return httpx.Response(200, json={"comments": [_comment("1", "a")], "total": 2, "startAt": 0})
        return httpx.Response(200, json={"comments": [_comment("2", "b")], "total": 2, "startAt": 1})

    assert [c["id"] for c in _client(handler).fetch_comments(_issue())] == ["1", "2"]

    def truncated(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"comments": [_comment("1", "a")], "total": 3})

    with pytest.raises(JiraUnknownPayload):
        _client(truncated).fetch_comments(_issue())


def test_append_note_once_reuses_matching_marked_comment_without_posting() -> None:
    marker = "symphony PROJ-1 abc123"
    posts: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(201, json={"id": "9"})
        body = jira_module._note_doc("H", f"line\n\n{marker}")
        return httpx.Response(200, json={"comments": [{"id": "7", "body": body}], "total": 1})

    assert _client(handler).append_note_once(_issue(), "H", "line", marker) == "7"
    assert posts == []


def test_append_note_once_raises_conflict_when_marked_comment_differs() -> None:
    from symphony.errors import JiraCommentConflict

    marker = "symphony PROJ-1 abc123"

    def handler(request: httpx.Request) -> httpx.Response:
        body = jira_module._note_doc("H", f"edited by human\n\n{marker}")
        return httpx.Response(200, json={"comments": [{"id": "7", "body": body}], "total": 1})

    with pytest.raises(JiraCommentConflict):
        _client(handler).append_note_once(_issue(), "H", "line", marker)


def test_append_note_once_posts_then_verifies_read_back() -> None:
    marker = "symphony PROJ-1 abc123"
    state: dict = {"posted": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["posted"] = json.loads(request.content)["body"]
            return httpx.Response(201, json={"id": "9"})
        comments = [{"id": "9", "body": state["posted"]}] if state["posted"] else []
        return httpx.Response(200, json={"comments": comments, "total": len(comments)})

    assert _client(handler).append_note_once(_issue(), "H", "line", marker) == "9"
    assert marker in jira_module._flatten_adf(state["posted"])


def test_append_note_once_is_uncertain_when_read_back_misses() -> None:
    from symphony.errors import JiraCommentDeliveryUncertain

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "9"})
        return httpx.Response(200, json={"comments": [], "total": 0})

    with pytest.raises(JiraCommentDeliveryUncertain):
        _client(handler).append_note_once(_issue(), "H", "line", "symphony PROJ-1 abc123")
