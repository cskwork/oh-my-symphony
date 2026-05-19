"""Linear adapter — coverage beyond the archive happy path.

`test_tracker_linear_archive.py` covers the 3-call archive sequence and
cache. This file pins:

  * `fetch_candidate_issues` paginates and normalizes with labels/blockers.
  * `fetch_issues_by_states` empty-input shortcut.
  * `fetch_issue_states_by_ids` minimal normalization.
  * `fetch_issue_full_by_id` empty-id guard, missing-node return None.
  * `_team_id_for_issue` raises when team unresolvable; cache prevents resend.
  * `_post` HTTP error / non-200 / non-JSON / GraphQL-errors raise targeted errors.
  * `_paginate` raises when `hasNextPage=true` but `endCursor` empty.
  * `_extract_issues_payload` / `_extract_nodes` validation.
  * `execute_raw` passthrough for the linear_graphql tool extension.
"""

from __future__ import annotations

import json

import httpx
import pytest

from symphony.errors import (
    LinearApiRequestError,
    LinearApiStatusError,
    LinearGraphQLErrors,
    LinearMissingEndCursor,
    LinearUnknownPayload,
)
from symphony.issue import Issue
from symphony.trackers.linear import LinearClient
from symphony.workflow import TrackerConfig


def _cfg() -> TrackerConfig:
    return TrackerConfig(
        kind="linear",
        endpoint="https://example.test/graphql",
        api_key="tok",
        project_slug="proj",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done",),
    )


def _client(handler) -> LinearClient:
    transport = httpx.MockTransport(handler)
    return LinearClient(_cfg(), http_client=httpx.Client(transport=transport))


def _issue(uuid: str = "u-1", state: str = "Done") -> Issue:
    return Issue(
        id=uuid,
        identifier="SMA-1",
        title="t",
        description=None,
        priority=None,
        state=state,
    )


# ---------------------------------------------------------------------------
# fetch_candidate_issues — pagination + full normalization
# ---------------------------------------------------------------------------


def test_fetch_candidate_issues_paginates_through_cursor_and_normalizes() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body.get("variables") or {})
        after = (body.get("variables") or {}).get("after")
        if after is None:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "nodes": [
                                {
                                    "id": "u-1",
                                    "identifier": "TEAM-1",
                                    "title": "first",
                                    "description": "body",
                                    "priority": 2,
                                    "state": {"name": "Todo"},
                                    "branchName": "feat/first",
                                    "url": "https://app.linear/issue/u-1",
                                    "labels": {
                                        "nodes": [{"name": "backend"}, {"name": "p1"}]
                                    },
                                    "inverseRelations": {
                                        "nodes": [
                                            {
                                                "type": "blocks",
                                                "issue": {
                                                    "id": "u-blocker",
                                                    "identifier": "TEAM-2",
                                                    "state": {"name": "In Progress"},
                                                },
                                            }
                                        ]
                                    },
                                    "createdAt": "2026-01-01T00:00:00Z",
                                    "updatedAt": "2026-01-02T00:00:00Z",
                                }
                            ],
                            "pageInfo": {"hasNextPage": True, "endCursor": "cur-1"},
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "u-2",
                                "identifier": "TEAM-3",
                                "title": "second",
                                "description": None,
                                "priority": None,
                                "state": {"name": "In Progress"},
                                "labels": {"nodes": []},
                                "inverseRelations": {"nodes": []},
                            }
                        ],
                        "pageInfo": {"hasNextPage": False},
                    }
                }
            },
        )

    client = _client(handler)
    issues = client.fetch_candidate_issues()

    assert len(issues) == 2
    first, second = issues

    # Full normalization on page 1.
    assert first.identifier == "TEAM-1"
    assert first.title == "first"
    assert first.description == "body"
    assert first.priority == 2
    assert first.state == "Todo"
    assert first.branch_name == "feat/first"
    assert first.url == "https://app.linear/issue/u-1"
    assert first.labels == ("backend", "p1")
    assert len(first.blocked_by) == 1
    assert first.blocked_by[0].identifier == "TEAM-2"
    assert first.blocked_by[0].state == "In Progress"
    assert first.created_at is not None
    assert first.updated_at is not None

    # Pagination: page 1 had no `after`; page 2 was sent the cursor.
    assert calls[0].get("after") is None
    assert calls[1].get("after") == "cur-1"

    # Second page normalized fine with empty labels/blockers.
    assert second.identifier == "TEAM-3"
    assert second.labels == ()
    assert second.blocked_by == ()


def test_paginate_raises_when_has_next_page_but_end_cursor_missing() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [],
                        # Server claims more pages but forgot endCursor.
                        "pageInfo": {"hasNextPage": True, "endCursor": ""},
                    }
                }
            },
        )

    client = _client(handler)
    with pytest.raises(LinearMissingEndCursor):
        client.fetch_candidate_issues()


# ---------------------------------------------------------------------------
# fetch_issues_by_states / fetch_issue_states_by_ids
# ---------------------------------------------------------------------------


def test_fetch_issues_by_states_skips_network_when_input_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called for empty state list")

    client = _client(handler)
    # Both an empty iterable and one full of falsey strings should short-circuit.
    assert client.fetch_issues_by_states([]) == []
    assert client.fetch_issues_by_states([""]) == []


def test_fetch_issue_states_by_ids_skips_network_when_input_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called for empty id list")

    client = _client(handler)
    assert client.fetch_issue_states_by_ids([]) == []
    assert client.fetch_issue_states_by_ids([""]) == []


def test_fetch_issue_states_by_ids_returns_minimal_normalization() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "u-1",
                                "identifier": "TEAM-1",
                                "title": "x",
                                # The "minimal" path should ignore labels/description.
                                "labels": {"nodes": [{"name": "ignored"}]},
                                "description": "ignored",
                                "state": {"name": "Done"},
                                "updatedAt": "2026-01-02T00:00:00Z",
                            }
                        ]
                    }
                }
            },
        )

    client = _client(handler)
    issues = client.fetch_issue_states_by_ids(["u-1"])
    assert len(issues) == 1
    # Minimal path drops description and labels.
    assert issues[0].description is None
    assert issues[0].labels == ()
    assert issues[0].state == "Done"


# ---------------------------------------------------------------------------
# fetch_issue_full_by_id
# ---------------------------------------------------------------------------


def test_fetch_issue_full_by_id_empty_returns_none_without_network() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called for empty id")

    client = _client(handler)
    assert client.fetch_issue_full_by_id("") is None


def test_fetch_issue_full_by_id_missing_node_returns_none() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"issue": None}})

    client = _client(handler)
    assert client.fetch_issue_full_by_id("u-404") is None


def test_fetch_issue_full_by_id_normalizes_full_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "issue": {
                        "id": "u-1",
                        "identifier": "TEAM-1",
                        "title": "full",
                        "description": "the body",
                        "priority": 1,
                        "state": {"name": "Review"},
                        "labels": {"nodes": [{"name": "qa"}]},
                        "inverseRelations": {"nodes": []},
                    }
                }
            },
        )

    client = _client(handler)
    issue = client.fetch_issue_full_by_id("u-1")
    assert issue is not None
    assert issue.identifier == "TEAM-1"
    assert issue.description == "the body"
    assert issue.priority == 1
    assert issue.labels == ("qa",)


# ---------------------------------------------------------------------------
# Team-id lookup caching + failure
# ---------------------------------------------------------------------------


def test_team_id_lookup_raises_when_team_missing_in_payload() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        # `issue.team` is null — team can't be resolved.
        return httpx.Response(200, json={"data": {"issue": {"id": "u-1"}}})

    client = _client(handler)
    with pytest.raises(LinearUnknownPayload):
        # update_state will fan out to _team_id_for_issue first.
        client.update_state(_issue("u-1"), "Done")


def test_team_id_cache_avoids_second_lookup_for_same_issue_id() -> None:
    """Two update_state calls on the same issue id should reuse the team UUID."""

    issue_team_calls = 0
    workflow_states_calls = 0
    issue_update_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issue_team_calls, workflow_states_calls, issue_update_calls
        body = json.loads(request.content)
        query = body.get("query") or ""
        if "IssueTeam" in query:
            issue_team_calls += 1
            return httpx.Response(
                200,
                json={
                    "data": {"issue": {"id": "u-1", "team": {"id": "team-A"}}}
                },
            )
        if "WorkflowStates" in query:
            workflow_states_calls += 1
            return httpx.Response(
                200,
                json={
                    "data": {
                        "workflowStates": {
                            "nodes": [
                                {"id": "s-todo", "name": "Todo"},
                                {"id": "s-done", "name": "Done"},
                            ]
                        }
                    }
                },
            )
        issue_update_calls += 1
        return httpx.Response(
            200,
            json={
                "data": {
                    "issueUpdate": {
                        "success": True,
                        "issue": {"id": "u-1", "state": {"name": "Done"}},
                    }
                }
            },
        )

    client = _client(handler)
    client.update_state(_issue("u-1"), "Done")
    client.update_state(_issue("u-1"), "Todo")

    # IssueTeam: cached after first call. WorkflowStates: cached per (team, state).
    assert issue_team_calls == 1
    # Two distinct (team, state) combos => two workflow-state lookups.
    assert workflow_states_calls == 2
    assert issue_update_calls == 2


def test_state_id_lookup_is_case_insensitive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query = body.get("query") or ""
        if "IssueTeam" in query:
            return httpx.Response(
                200, json={"data": {"issue": {"id": "u-1", "team": {"id": "t-A"}}}}
            )
        if "WorkflowStates" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "workflowStates": {
                            "nodes": [{"id": "s-arch", "name": "Archive"}]
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={"data": {"issueUpdate": {"success": True, "issue": {"id": "u-1"}}}},
        )

    client = _client(handler)
    # "archive" (lowercase) must still resolve to the "Archive" state.
    client.update_state(_issue("u-1"), "archive")


def test_update_state_with_empty_issue_id_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach network with empty issue id")

    client = _client(handler)
    issue = Issue(id="", identifier="X", title="", description=None, priority=None, state="x")
    with pytest.raises(LinearUnknownPayload):
        client.update_state(issue, "Done")


# ---------------------------------------------------------------------------
# _post error mapping
# ---------------------------------------------------------------------------


def test_post_raises_request_error_on_transport_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dial tcp: i/o timeout")

    client = _client(handler)
    with pytest.raises(LinearApiRequestError):
        client.fetch_candidate_issues()


def test_post_raises_status_error_on_non_200() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server explosion")

    client = _client(handler)
    with pytest.raises(LinearApiStatusError):
        client.fetch_candidate_issues()


def test_post_raises_unknown_payload_on_non_json_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = _client(handler)
    with pytest.raises(LinearUnknownPayload):
        client.fetch_candidate_issues()


def test_post_raises_unknown_payload_when_body_is_not_object() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    client = _client(handler)
    with pytest.raises(LinearUnknownPayload):
        client.fetch_candidate_issues()


def test_post_raises_graphql_errors_when_errors_array_present() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}},
                "errors": [{"message": "deprecated field"}],
            },
        )

    client = _client(handler)
    with pytest.raises(LinearGraphQLErrors):
        client.fetch_candidate_issues()


# ---------------------------------------------------------------------------
# _extract_issues_payload / _extract_nodes
# ---------------------------------------------------------------------------


def test_extract_issues_payload_rejects_missing_data() -> None:
    with pytest.raises(LinearUnknownPayload):
        LinearClient._extract_issues_payload({})


def test_extract_issues_payload_rejects_missing_data_issues() -> None:
    with pytest.raises(LinearUnknownPayload):
        LinearClient._extract_issues_payload({"data": {}})


def test_extract_nodes_rejects_when_nodes_is_not_list() -> None:
    payload = {"data": {"issues": {"nodes": "not-a-list"}}}
    with pytest.raises(LinearUnknownPayload):
        LinearClient._extract_nodes(payload)


def test_extract_nodes_filters_non_dict_entries() -> None:
    payload = {
        "data": {
            "issues": {
                "nodes": [
                    {"id": "u-1"},
                    "ignored-string",
                    None,
                    {"id": "u-2"},
                ]
            }
        }
    }
    nodes = LinearClient._extract_nodes(payload)
    assert [n["id"] for n in nodes] == ["u-1", "u-2"]


# ---------------------------------------------------------------------------
# execute_raw — linear_graphql tool passthrough
# ---------------------------------------------------------------------------


def test_execute_raw_posts_query_and_returns_payload() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"viewer": {"id": "v-1"}}})

    client = _client(handler)
    out = client.execute_raw("query { viewer { id } }", variables={"x": 1})
    assert out == {"data": {"viewer": {"id": "v-1"}}}
    assert captured["body"]["query"].startswith("query")
    assert captured["body"]["variables"] == {"x": 1}


def test_execute_raw_defaults_variables_to_empty_dict() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {}})

    client = _client(handler)
    client.execute_raw("query { __schema { types { name } } }")
    assert captured["body"]["variables"] == {}
