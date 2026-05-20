"""Jira adapter — helper-level edges complementing `test_tracker_jira.py`.

The base file drives the HTTP layer through MockTransport. This file
covers the pure helpers and the rarer client paths:

  * `_flatten_adf` text-tree walk (newlines after blocks, hardBreak, etc.).
  * `_adf_paragraphs` inverse: plain text -> ADF doc with blank-line handling.
  * `_extract_blockers` filters by inward 'Blocks' link type.
  * `_normalize_issue` priority coercion + minimal vs full shape.
  * JQL builders escape state lists and ids.
  * `_request` transport error wrapping.
  * `fetch_issue_full_by_id` empty-id and not-found return None.
  * `append_note` empty-heading and empty-body paths.
"""

from __future__ import annotations

import json

import httpx
import pytest

from symphony.errors import (
    JiraApiRequestError,
    JiraApiStatusError,
    JiraUnknownPayload,
)
from symphony.issue import Issue
from symphony.trackers.jira import (
    JiraClient,
    _adf_paragraphs,
    _extract_blockers,
    _flatten_adf,
    _jql_for_ids,
    _jql_for_states,
    _normalize_issue,
)
from symphony.workflow import TrackerConfig


SITE = "https://example.atlassian.net"


def _cfg() -> TrackerConfig:
    return TrackerConfig(
        kind="jira",
        endpoint=SITE,
        api_key="tok",
        project_slug="PROJ",
        active_states=("To Do",),
        terminal_states=("Done",),
        email="user@example.com",
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


# ---------------------------------------------------------------------------
# _flatten_adf
# ---------------------------------------------------------------------------


class TestFlattenAdf:
    def test_none_returns_empty(self) -> None:
        assert _flatten_adf(None) == ""

    def test_plain_string_passes_through(self) -> None:
        assert _flatten_adf("hello") == "hello"

    def test_non_dict_non_string_returns_empty(self) -> None:
        assert _flatten_adf(42) == ""
        assert _flatten_adf([1, 2, 3]) == ""

    def test_paragraph_appends_newline(self) -> None:
        node = {
            "type": "paragraph",
            "content": [{"type": "text", "text": "hello"}],
        }
        assert _flatten_adf(node) == "hello\n"

    def test_heading_appends_newline(self) -> None:
        node = {
            "type": "heading",
            "content": [{"type": "text", "text": "Title"}],
        }
        assert _flatten_adf(node) == "Title\n"

    def test_hard_break_produces_newline(self) -> None:
        assert _flatten_adf({"type": "hardBreak"}) == "\n"

    def test_nested_doc_concatenates_paragraphs(self) -> None:
        node = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "line1"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "line2"}],
                },
            ],
        }
        # Each paragraph appends a newline; the outer doc has no decoration.
        assert _flatten_adf(node) == "line1\nline2\n"

    def test_list_item_appends_newline(self) -> None:
        node = {
            "type": "listItem",
            "content": [{"type": "text", "text": "bullet"}],
        }
        assert _flatten_adf(node) == "bullet\n"

    def test_empty_paragraph_does_not_add_trailing_newline(self) -> None:
        # No text -> no decoration; the "if text" guard fires.
        node = {"type": "paragraph", "content": []}
        assert _flatten_adf(node) == ""


# ---------------------------------------------------------------------------
# _adf_paragraphs (inverse)
# ---------------------------------------------------------------------------


class TestAdfParagraphs:
    def test_empty_string_produces_one_empty_paragraph(self) -> None:
        doc = _adf_paragraphs("")
        assert doc["type"] == "doc"
        assert doc["version"] == 1
        assert len(doc["content"]) == 1
        assert doc["content"][0]["content"] == []

    def test_single_line_makes_one_paragraph_with_text_node(self) -> None:
        doc = _adf_paragraphs("hello")
        assert len(doc["content"]) == 1
        assert doc["content"][0]["content"] == [{"type": "text", "text": "hello"}]

    def test_multiple_lines_become_multiple_paragraphs(self) -> None:
        doc = _adf_paragraphs("first\nsecond\nthird")
        assert len(doc["content"]) == 3
        texts = [p["content"][0]["text"] for p in doc["content"]]
        assert texts == ["first", "second", "third"]

    def test_blank_line_becomes_empty_paragraph(self) -> None:
        doc = _adf_paragraphs("a\n\nb")
        assert len(doc["content"]) == 3
        # Blank middle line has empty content array.
        assert doc["content"][1]["content"] == []


# ---------------------------------------------------------------------------
# _extract_blockers
# ---------------------------------------------------------------------------


class TestExtractBlockers:
    def test_non_list_returns_empty_tuple(self) -> None:
        assert _extract_blockers(None) == ()
        assert _extract_blockers("not-a-list") == ()
        assert _extract_blockers({}) == ()

    def test_filters_out_non_blocks_link_types(self) -> None:
        links = [
            {
                "type": {"name": "Relates"},
                "inwardIssue": {"id": "1", "key": "PROJ-1"},
            },
            {
                "type": {"name": "Blocks"},
                "inwardIssue": {
                    "id": "2",
                    "key": "PROJ-2",
                    "fields": {"status": {"name": "Done"}},
                },
            },
        ]
        out = _extract_blockers(links)
        assert len(out) == 1
        assert out[0].identifier == "PROJ-2"
        assert out[0].state == "Done"

    def test_case_insensitive_blocks_match(self) -> None:
        links = [
            {
                "type": {"name": "BLOCKS"},
                "inwardIssue": {
                    "id": "2",
                    "key": "PROJ-2",
                    "fields": {"status": {"name": "Done"}},
                },
            }
        ]
        assert len(_extract_blockers(links)) == 1

    def test_skips_links_without_inward_issue_dict(self) -> None:
        links = [
            {"type": {"name": "Blocks"}, "inwardIssue": None},
            {"type": {"name": "Blocks"}},
            "not-a-dict",
        ]
        assert _extract_blockers(links) == ()

    def test_state_is_none_when_status_missing(self) -> None:
        links = [
            {
                "type": {"name": "Blocks"},
                "inwardIssue": {"id": "2", "key": "PROJ-2", "fields": {}},
            }
        ]
        out = _extract_blockers(links)
        assert out[0].state is None


# ---------------------------------------------------------------------------
# _normalize_issue priority + minimal
# ---------------------------------------------------------------------------


class TestNormalizeIssue:
    def test_minimal_drops_description_priority_labels_blockers(self) -> None:
        node = {
            "id": "10001",
            "key": "PROJ-1",
            "fields": {
                "summary": "x",
                "status": {"name": "Done"},
                "priority": {"id": "3", "name": "Medium"},
                "labels": ["ignored"],
                "description": "ignored",
                "updated": "2026-01-01T00:00:00.000+0000",
                "issuelinks": [],
            },
        }
        out = _normalize_issue(node, site_url=SITE, minimal=True)
        assert out.description is None
        assert out.priority is None
        assert out.labels == ()
        assert out.blocked_by == ()
        # But state and updated_at survive.
        assert out.state == "Done"
        assert out.updated_at is not None

    def test_full_normalizes_numeric_string_priority_to_int(self) -> None:
        node = {
            "id": "10001",
            "key": "PROJ-1",
            "fields": {
                "summary": "x",
                "status": {"name": "To Do"},
                "priority": {"id": "2", "name": "High"},
            },
        }
        out = _normalize_issue(node, site_url=SITE, minimal=False)
        assert out.priority == 2
        # URL is composed from site + key.
        assert out.url == f"{SITE}/browse/PROJ-1"

    def test_url_is_none_when_site_or_key_missing(self) -> None:
        node = {"id": "10001", "key": "", "fields": {"summary": "x", "status": {}}}
        out = _normalize_issue(node, site_url=SITE, minimal=False)
        assert out.url is None

        node["key"] = "PROJ-1"
        out = _normalize_issue(node, site_url="", minimal=False)
        assert out.url is None

    def test_adf_description_flattens_to_plain_text(self) -> None:
        node = {
            "id": "1",
            "key": "PROJ-1",
            "fields": {
                "summary": "x",
                "status": {"name": "Done"},
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "alpha"}],
                        },
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "beta"}],
                        },
                    ],
                },
            },
        }
        out = _normalize_issue(node, site_url=SITE, minimal=False)
        assert out.description == "alpha\nbeta"

    def test_description_empty_after_flatten_normalizes_to_none(self) -> None:
        node = {
            "id": "1",
            "key": "PROJ-1",
            "fields": {
                "summary": "x",
                "status": {"name": "Done"},
                "description": {"type": "doc", "content": []},
            },
        }
        out = _normalize_issue(node, site_url=SITE, minimal=False)
        assert out.description is None


# ---------------------------------------------------------------------------
# JQL builders
# ---------------------------------------------------------------------------


class TestJqlBuilders:
    def test_jql_for_states_quotes_each_state(self) -> None:
        jql = _jql_for_states("PROJ", ["To Do", "In Progress"])
        assert 'project = "PROJ"' in jql
        assert 'status in ("To Do", "In Progress")' in jql
        assert "ORDER BY created ASC" in jql

    def test_jql_for_states_skips_empty_entries(self) -> None:
        jql = _jql_for_states("PROJ", ["", None, "Done"])  # type: ignore[list-item]
        assert 'status in ("Done")' in jql

    def test_jql_for_states_falls_back_when_all_filtered_out(self) -> None:
        jql = _jql_for_states("PROJ", ["", ""])
        assert jql == 'project = "PROJ"'

    def test_jql_for_ids_quotes_each_id_and_skips_empties(self) -> None:
        jql = _jql_for_ids(["10001", "", "10002"])
        assert jql == 'id in ("10001", "10002")'


# ---------------------------------------------------------------------------
# Transport-level error mapping
# ---------------------------------------------------------------------------


class TestRequestErrorMapping:
    def test_transport_failure_wraps_in_jira_api_request_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection reset")

        client = _client(handler)
        with pytest.raises(JiraApiRequestError):
            client.fetch_candidate_issues()

    def test_non_2xx_search_raises_status_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = _client(handler)
        with pytest.raises(JiraApiStatusError):
            client.fetch_candidate_issues()

    def test_non_object_payload_raises_unknown(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3])

        client = _client(handler)
        with pytest.raises(JiraUnknownPayload):
            client.fetch_candidate_issues()


# ---------------------------------------------------------------------------
# fetch_issue_full_by_id behavior
# ---------------------------------------------------------------------------


class TestFetchIssueFullById:
    def test_empty_id_returns_none_without_network(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("network must not be called for empty id")

        client = _client(handler)
        assert client.fetch_issue_full_by_id("") is None

    def test_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"isLast": True, "issues": []})

        client = _client(handler)
        assert client.fetch_issue_full_by_id("PROJ-404") is None

    def test_found_returns_full_normalized_issue(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "isLast": True,
                    "issues": [
                        {
                            "id": "10001",
                            "key": "PROJ-1",
                            "fields": {
                                "summary": "full",
                                "status": {"name": "Review"},
                                "priority": {"id": "1"},
                                "labels": ["qa"],
                                "description": {
                                    "type": "doc",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [
                                                {"type": "text", "text": "body"}
                                            ],
                                        }
                                    ],
                                },
                            },
                        }
                    ],
                },
            )

        client = _client(handler)
        issue = client.fetch_issue_full_by_id("PROJ-1")
        assert issue is not None
        assert issue.identifier == "PROJ-1"
        assert issue.priority == 1
        assert issue.labels == ("qa",)
        assert issue.description == "body"


# ---------------------------------------------------------------------------
# append_note edge inputs
# ---------------------------------------------------------------------------


class TestAppendNoteEdges:
    def test_empty_heading_posts_only_body(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={})

        client = _client(handler)
        issue = Issue(
            id="1",
            identifier="PROJ-1",
            title="t",
            description=None,
            priority=None,
            state="In Progress",
        )
        client.append_note(issue, "", "only body content")
        doc = captured["body"]["body"]
        # First paragraph carries the body text (no leading heading line).
        assert doc["content"][0]["content"] == [
            {"type": "text", "text": "only body content"}
        ]

    def test_non_2xx_raises_status_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = _client(handler)
        issue = Issue(
            id="1",
            identifier="PROJ-1",
            title="t",
            description=None,
            priority=None,
            state="In Progress",
        )
        with pytest.raises(JiraApiStatusError):
            client.append_note(issue, "H", "B")

    def test_empty_key_raises_unknown_payload(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not reach network for empty key")

        client = _client(handler)
        issue = Issue(
            id="",
            identifier="",
            title="t",
            description=None,
            priority=None,
            state="In Progress",
        )
        with pytest.raises(JiraUnknownPayload):
            client.append_note(issue, "H", "B")
