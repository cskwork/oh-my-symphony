"""SPEC §11 — Jira Cloud REST API v3 tracker adapter.

Mirrors the LinearClient surface (§11.1.x) against the Atlassian Cloud REST
API. Authenticates with Basic Auth using (`tracker.email`, `tracker.api_key`),
where `api_key` is an Atlassian API token (id.atlassian.com → Security →
"Create and manage API tokens").

References (official docs):
- Search:       GET /rest/api/3/search/jql (token-pagination, `isLast`)
- Transitions:  GET /rest/api/3/issue/{key}/transitions
                POST /rest/api/3/issue/{key}/transitions {"transition":{"id":""}}
- Comments:     POST /rest/api/3/issue/{key}/comment (Atlassian Document Format)
"""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from ..errors import (
    JiraApiRequestError,
    JiraApiStatusError,
    JiraTransitionNotFound,
    JiraUnknownPayload,
)
from ..issue import (
    BlockerRef,
    Issue,
    coerce_priority,
    normalize_labels,
    parse_iso_timestamp,
)
from ..logging import get_logger
from ..workflow import TrackerConfig
from ._retry import send_with_retry


API_BASE = "/rest/api/3"
PAGE_SIZE = 50  # mirrors Linear adapter; Jira allows up to 100.
MAX_PAGES = 20
log = get_logger()

# Fields we explicitly request from /search/jql. Keep this list narrow so
# pagination stays small and the response payload predictable.
_SEARCH_FIELDS = (
    "summary,status,priority,labels,description,created,updated,"
    "issuelinks,issuetype"
)
_MINIMAL_SEARCH_FIELDS = "summary,status,updated"


def _flatten_adf(node: Any) -> str:
    """Best-effort ADF → plain text.

    Jira Cloud returns rich descriptions and comments as Atlassian Document
    Format trees. We only need a textual rendering for prompt templating,
    not a faithful re-serialization, so we walk the tree and concatenate
    `text` nodes, inserting newlines after paragraphs and list items.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    parts: list[str] = []
    node_type = node.get("type")
    if node_type == "text" and isinstance(node.get("text"), str):
        parts.append(node["text"])
    content = node.get("content")
    if isinstance(content, list):
        for child in content:
            parts.append(_flatten_adf(child))
    text = "".join(parts)
    if node_type in {"paragraph", "heading", "listItem", "blockquote"} and text:
        return text + "\n"
    if node_type == "hardBreak":
        return "\n"
    return text


def _adf_paragraphs(text: str) -> dict[str, Any]:
    """Wrap plain text into a minimal ADF doc (one paragraph per line)."""
    paragraphs: list[dict[str, Any]] = []
    for line in (text or "").splitlines() or [""]:
        paragraphs.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}] if line else [],
            }
        )
    return {"type": "doc", "version": 1, "content": paragraphs}


def _extract_blockers(issuelinks: Any) -> tuple[BlockerRef, ...]:
    """Inward 'Blocks'-type links → BlockerRef tuple."""
    if not isinstance(issuelinks, list):
        return ()
    blockers: list[BlockerRef] = []
    for link in issuelinks:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        if (link_type.get("name") or "").lower() != "blocks":
            continue
        inward = link.get("inwardIssue")
        if not isinstance(inward, dict):
            continue
        fields = inward.get("fields") or {}
        status = (fields.get("status") or {}).get("name") if isinstance(fields, dict) else None
        blockers.append(
            BlockerRef(
                id=inward.get("id"),
                identifier=inward.get("key"),
                state=status,
            )
        )
    return tuple(blockers)


def _normalize_issue(
    node: dict[str, Any],
    *,
    site_url: str,
    minimal: bool = False,
) -> Issue:
    fields = node.get("fields") or {}
    status_obj = fields.get("status") or {}
    state_name = status_obj.get("name") or ""
    key = node.get("key") or ""
    title = fields.get("summary") or ""
    updated = parse_iso_timestamp(fields.get("updated"))
    if minimal:
        return Issue(
            id=str(node.get("id") or ""),
            identifier=key,
            title=title,
            description=None,
            priority=None,
            state=state_name,
            updated_at=updated,
        )
    description_raw = fields.get("description")
    description = _flatten_adf(description_raw).strip() if description_raw else None
    priority_obj = fields.get("priority")
    priority_int: int | None = None
    if isinstance(priority_obj, dict):
        # Jira ships priority.id as a numeric string ("3"). Convert once
        # locally rather than widening the shared `coerce_priority` contract.
        raw_pid = priority_obj.get("id")
        if isinstance(raw_pid, str) and raw_pid.isdigit():
            priority_int = int(raw_pid)
        else:
            priority_int = coerce_priority(raw_pid)
    return Issue(
        id=str(node.get("id") or ""),
        identifier=key,
        title=title,
        description=description or None,
        priority=priority_int,
        state=state_name,
        branch_name=None,
        url=f"{site_url}/browse/{key}" if key and site_url else None,
        labels=normalize_labels(fields.get("labels")),
        blocked_by=_extract_blockers(fields.get("issuelinks")),
        created_at=parse_iso_timestamp(fields.get("created")),
        updated_at=updated,
    )


def _jql_for_states(project_key: str, states: Iterable[str]) -> str:
    state_list = ", ".join(f'"{s}"' for s in states if s)
    if not state_list:
        return f'project = "{project_key}"'
    return f'project = "{project_key}" AND status in ({state_list}) ORDER BY created ASC'


def _jql_for_ids(ids: Iterable[str]) -> str:
    quoted = ", ".join(f'"{i}"' for i in ids if i)
    return f"id in ({quoted})"


class JiraClient:
    """Jira Cloud adapter exposing the TrackerClient operations.

    All methods are synchronous; the orchestrator runs them in an executor
    so the asyncio event loop is not blocked.
    """

    def __init__(self, tracker: TrackerConfig, http_client: httpx.Client | None = None) -> None:
        self._tracker = tracker
        self._site = (tracker.endpoint or "").rstrip("/")
        self._owns_client = http_client is None
        if http_client is None:
            self._client = httpx.Client(
                base_url=self._site,
                timeout=tracker.network_timeout_seconds,
                auth=httpx.BasicAuth(tracker.email, tracker.api_key),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "symphony-reference/0.1",
                },
            )
        else:
            self._client = http_client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    # §11.1.1
    def fetch_candidate_issues(self) -> list[Issue]:
        jql = _jql_for_states(
            self._tracker.project_slug, self._tracker.active_states
        )
        return self._search_paginated(jql, fields=_SEARCH_FIELDS, minimal=False)

    # §11.1.2
    def fetch_issues_by_states(self, state_names: Iterable[str]) -> list[Issue]:
        states = [s for s in state_names if s]
        if not states:
            return []
        jql = _jql_for_states(self._tracker.project_slug, states)
        return self._search_paginated(jql, fields=_MINIMAL_SEARCH_FIELDS, minimal=True)

    # §11.1.3
    def fetch_issue_states_by_ids(self, ids: Iterable[str]) -> list[Issue]:
        id_list = [str(i) for i in ids if i]
        if not id_list:
            return []
        jql = _jql_for_ids(id_list)
        return self._search_paginated(jql, fields=_MINIMAL_SEARCH_FIELDS, minimal=True)

    def fetch_issue_full_by_id(self, issue_id: str) -> Issue | None:
        """Issue with full body (description, priority, labels, blockers).

        Used by the contract validator. Falls back to JQL search rather
        than a direct GET /issue/{key} so the existing `_normalize_issue`
        normalization path is reused unchanged — the orchestrator only
        ever passes one id, so pagination overhead is negligible.
        """
        if not issue_id:
            return None
        jql = _jql_for_ids([issue_id])
        results = self._search_paginated(jql, fields=_SEARCH_FIELDS, minimal=False)
        return results[0] if results else None

    def update_state(self, issue: Issue, target_state: str) -> None:
        """Transition `issue` so its status name becomes `target_state`.

        Resolves the transition id via GET /transitions on the specific
        issue (transitions are workflow-and-position dependent), then POSTs
        the transition. Caching across issues is unsafe because the same
        target status name can map to different transition ids per workflow.
        """
        key = issue.identifier or issue.id
        if not key:
            raise JiraUnknownPayload("issue.identifier and issue.id both empty")
        transition_id = self._find_transition_id(key, target_state)
        path = f"{API_BASE}/issue/{key}/transitions"
        response = self._request("POST", path, json={"transition": {"id": transition_id}})
        # Spec says 204 No Content on success; tolerate 200 too.
        if response.status_code not in (200, 204):
            raise JiraApiStatusError(
                "transition POST returned non-2xx",
                status=response.status_code,
                body_preview=response.text[:200],
            )

    def append_note(self, issue: Issue, heading: str, body: str) -> None:
        """Add a comment to the issue. Heading is rendered as a bold first line."""
        key = issue.identifier or issue.id
        if not key:
            raise JiraUnknownPayload("issue.identifier and issue.id both empty")
        text = f"{heading}\n\n{body}" if heading else body
        adf = _adf_paragraphs(text)
        path = f"{API_BASE}/issue/{key}/comment"
        response = self._request("POST", path, json={"body": adf})
        if response.status_code not in (200, 201):
            raise JiraApiStatusError(
                "comment POST returned non-2xx",
                status=response.status_code,
                body_preview=response.text[:200],
            )

    # ------------------------------------------------------------------

    def _search_paginated(
        self, jql: str, *, fields: str, minimal: bool
    ) -> list[Issue]:
        out: list[Issue] = []
        next_token: str | None = None
        page_count = 0
        while True:
            page_count += 1
            params: dict[str, Any] = {
                "jql": jql,
                "fields": fields,
                "maxResults": PAGE_SIZE,
            }
            if next_token:
                params["nextPageToken"] = next_token
            response = self._request("GET", f"{API_BASE}/search/jql", params=params)
            payload = self._json_or_raise(response)
            issues = payload.get("issues")
            if not isinstance(issues, list):
                raise JiraUnknownPayload("data.issues missing or wrong type")
            for node in issues:
                if not isinstance(node, dict):
                    continue
                out.append(
                    _normalize_issue(node, site_url=self._site, minimal=minimal)
                )
            if payload.get("isLast", True):
                break
            if page_count >= MAX_PAGES:
                log.warning("jira_pagination_max_pages", max_pages=MAX_PAGES)
                break
            token = payload.get("nextPageToken")
            if not isinstance(token, str) or not token:
                break
            next_token = token
        return out

    def _find_transition_id(self, issue_key: str, target_state: str) -> str:
        path = f"{API_BASE}/issue/{issue_key}/transitions"
        response = self._request("GET", path)
        payload = self._json_or_raise(response)
        transitions = payload.get("transitions")
        if not isinstance(transitions, list):
            raise JiraUnknownPayload(
                "transitions field missing", issue_key=issue_key
            )
        target_lower = target_state.lower()
        for t in transitions:
            if not isinstance(t, dict):
                continue
            to_obj = t.get("to") or {}
            to_name = (to_obj.get("name") or "").lower() if isinstance(to_obj, dict) else ""
            transition_name = (t.get("name") or "").lower()
            if to_name == target_lower or transition_name == target_lower:
                tid = t.get("id")
                if isinstance(tid, (str, int)) and str(tid):
                    return str(tid)
        raise JiraTransitionNotFound(
            "no transition reaches target state from current status",
            issue_key=issue_key,
            target_state=target_state,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = send_with_retry(
                lambda: self._client.request(method, path, params=params, json=json)
            )
        except httpx.HTTPError as exc:
            raise JiraApiRequestError(
                "transport failure", method=method, path=path, error=str(exc)
            ) from exc
        return response

    @staticmethod
    def _json_or_raise(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise JiraApiStatusError(
                "non-2xx response",
                status=response.status_code,
                body_preview=response.text[:200],
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise JiraUnknownPayload("invalid JSON in response") from exc
        if not isinstance(payload, dict):
            raise JiraUnknownPayload("payload is not an object")
        return payload
