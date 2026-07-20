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

import re
from dataclasses import dataclass
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
_INTAKE_SEARCH_FIELDS = "summary,description,assignee,issuetype,parent"
INTAKE_MAX_PAGES = 20
INTAKE_MAX_ISSUES = 500
INTAKE_MAX_RESPONSE_BYTES = 1_000_000
INTAKE_MAX_TOTAL_RESPONSE_BYTES = 4_000_000
INTAKE_MAX_ADF_DEPTH = 20
INTAKE_MAX_ADF_NODES = 5_000
INTAKE_MAX_FIELD_BYTES = 128_000
INTAKE_MAX_CARD_BYTES = 256_000
INTAKE_MAX_BATCH_BYTES = 4_000_000
_INTAKE_MAX_LITERAL_LENGTH = 128
_INTAKE_MAX_STATUSES = 25
_INTAKE_MAX_TOKEN_LENGTH = 512


@dataclass(frozen=True)
class JiraInboxIssue:
    """Fully validated read-only Jira context for one file-board card."""

    key: str
    summary: str
    description: str
    issue_type: str
    parent_key: str | None = None
    parent_summary: str | None = None
    parent_description: str | None = None


def _intake_literal(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _INTAKE_MAX_LITERAL_LENGTH:
        raise JiraUnknownPayload("invalid intake JQL literal", field=field)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise JiraUnknownPayload("invalid intake JQL literal", field=field)
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _intake_jql(project: Any, statuses: Iterable[Any]) -> str:
    escaped_project = _intake_literal(project, field="project")
    status_values = list(statuses)
    if not status_values or len(status_values) > _INTAKE_MAX_STATUSES:
        raise JiraUnknownPayload("invalid intake status list")
    escaped_statuses = [
        f'"{_intake_literal(status, field="status")}"' for status in status_values
    ]
    return (
        f'project = "{escaped_project}" AND status in '
        f'({", ".join(escaped_statuses)}) AND assignee = currentUser()'
    )


def _walk_intake_adf(
    node: Any,
    *,
    depth: int,
    count: list[int],
    output: list[str],
) -> None:
    if depth > INTAKE_MAX_ADF_DEPTH or count[0] >= INTAKE_MAX_ADF_NODES:
        raise JiraUnknownPayload("intake ADF limit exceeded")
    if not isinstance(node, dict):
        raise JiraUnknownPayload("invalid intake ADF node")
    count[0] += 1
    node_type = node.get("type")
    if not isinstance(node_type, str) or not node_type:
        raise JiraUnknownPayload("invalid intake ADF node type")
    if node_type == "text":
        text = node.get("text")
        if not isinstance(text, str):
            raise JiraUnknownPayload("invalid intake ADF text")
        output.append(text)
    content = node.get("content", [])
    if not isinstance(content, list):
        raise JiraUnknownPayload("invalid intake ADF content")
    for child in content:
        _walk_intake_adf(child, depth=depth + 1, count=count, output=output)
    if node_type in {"paragraph", "heading", "listItem", "blockquote", "hardBreak"}:
        output.append("\n")


def _flatten_intake_adf(value: Any, *, required: bool = False) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        output: list[str] = []
        _walk_intake_adf(value, depth=0, count=[0], output=output)
        text = "".join(output).strip()
    if len(text.encode("utf-8")) > INTAKE_MAX_FIELD_BYTES:
        raise JiraUnknownPayload("intake field limit exceeded")
    if required and not text.strip():
        raise JiraUnknownPayload("required intake content missing")
    return text


def _intake_text(value: Any, *, field: str, required: bool) -> str:
    if not isinstance(value, str):
        raise JiraUnknownPayload("invalid intake text field", field=field)
    if len(value.encode("utf-8")) > INTAKE_MAX_FIELD_BYTES:
        raise JiraUnknownPayload("intake field limit exceeded", field=field)
    if required and not value.strip():
        raise JiraUnknownPayload("required intake content missing", field=field)
    return value


def _intake_key(value: Any, *, project: str) -> str:
    if not isinstance(value, str):
        raise JiraUnknownPayload("invalid intake issue key")
    pattern = rf"^{re.escape(project)}-[1-9][0-9]*$"
    if re.fullmatch(pattern, value) is None:
        raise JiraUnknownPayload("invalid intake issue key")
    return value


def _intake_card_size(item: JiraInboxIssue) -> int:
    values = (
        item.key,
        item.summary,
        item.description,
        item.issue_type,
        item.parent_key or "",
        item.parent_summary or "",
        item.parent_description or "",
    )
    return sum(len(value.encode("utf-8")) for value in values)


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

    def fetch_assigned_inbox(self) -> list[JiraInboxIssue]:
        """Fetch one complete, identity-checked, GET-only Jira inbox batch."""
        project = self._tracker.project_slug
        jql = _intake_jql(project, self._tracker.active_states)
        response_bytes = [0]
        myself = self._intake_get_json(f"{API_BASE}/myself", response_bytes)
        account_id = self._intake_account_id(myself)
        nodes = self._search_assigned_inbox(jql, project, response_bytes)
        return self._normalize_inbox_batch(
            nodes,
            project=project,
            account_id=account_id,
            response_bytes=response_bytes,
        )

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

    def _intake_get_json(
        self,
        path: str,
        response_bytes: list[int],
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._request("GET", path, params=params)
        size = len(response.content)
        response_bytes[0] += size
        if size > INTAKE_MAX_RESPONSE_BYTES:
            raise JiraUnknownPayload("intake response limit exceeded")
        if response_bytes[0] > INTAKE_MAX_TOTAL_RESPONSE_BYTES:
            raise JiraUnknownPayload("intake response batch limit exceeded")
        return self._json_or_raise(response)

    @staticmethod
    def _intake_account_id(payload: dict[str, Any]) -> str:
        account_id = payload.get("accountId")
        if payload.get("active") is not True:
            raise JiraUnknownPayload("inactive Jira intake identity")
        if not isinstance(account_id, str) or not account_id.strip():
            raise JiraUnknownPayload("invalid Jira intake identity")
        return account_id

    def _search_assigned_inbox(
        self,
        jql: str,
        project: str,
        response_bytes: list[int],
    ) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        seen_tokens: set[str] = set()
        next_token: str | None = None
        for page_number in range(1, INTAKE_MAX_PAGES + 1):
            payload = self._intake_search_page(jql, next_token, response_bytes)
            self._append_intake_nodes(payload, project, nodes, seen_keys)
            is_last = payload.get("isLast")
            if type(is_last) is not bool:
                raise JiraUnknownPayload("invalid intake isLast flag")
            if is_last:
                return nodes
            next_token = self._next_intake_token(payload, seen_tokens)
            if page_number == INTAKE_MAX_PAGES:
                raise JiraUnknownPayload("intake page limit exceeded")
        raise JiraUnknownPayload("incomplete intake pagination")

    def _intake_search_page(
        self,
        jql: str,
        next_token: str | None,
        response_bytes: list[int],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "jql": jql,
            "fields": _INTAKE_SEARCH_FIELDS,
            "maxResults": PAGE_SIZE,
        }
        if next_token is not None:
            params["nextPageToken"] = next_token
        return self._intake_get_json(
            f"{API_BASE}/search/jql", response_bytes, params=params
        )

    @staticmethod
    def _append_intake_nodes(
        payload: dict[str, Any],
        project: str,
        nodes: list[dict[str, Any]],
        seen_keys: set[str],
    ) -> None:
        issues = payload.get("issues")
        if not isinstance(issues, list):
            raise JiraUnknownPayload("invalid intake issues list")
        if len(nodes) + len(issues) > INTAKE_MAX_ISSUES:
            raise JiraUnknownPayload("intake issue limit exceeded")
        for node in issues:
            if not isinstance(node, dict):
                raise JiraUnknownPayload("invalid intake issue row")
            key = _intake_key(node.get("key"), project=project)
            if key in seen_keys:
                raise JiraUnknownPayload("duplicate intake issue key")
            seen_keys.add(key)
            nodes.append(node)

    @staticmethod
    def _next_intake_token(
        payload: dict[str, Any], seen_tokens: set[str]
    ) -> str:
        token = payload.get("nextPageToken")
        if not isinstance(token, str) or not token.strip():
            raise JiraUnknownPayload("missing intake page token")
        if len(token) > _INTAKE_MAX_TOKEN_LENGTH or token in seen_tokens:
            raise JiraUnknownPayload("invalid intake page token")
        seen_tokens.add(token)
        return token

    def _normalize_inbox_batch(
        self,
        nodes: list[dict[str, Any]],
        *,
        project: str,
        account_id: str,
        response_bytes: list[int],
    ) -> list[JiraInboxIssue]:
        items: list[JiraInboxIssue] = []
        parent_cache: dict[str, tuple[str, str]] = {}
        total_size = 0
        for node in nodes:
            item = self._normalize_inbox_node(
                node,
                project=project,
                account_id=account_id,
                response_bytes=response_bytes,
                parent_cache=parent_cache,
            )
            item_size = _intake_card_size(item)
            if item_size > INTAKE_MAX_CARD_BYTES:
                raise JiraUnknownPayload("intake card limit exceeded")
            total_size += item_size
            if total_size > INTAKE_MAX_BATCH_BYTES:
                raise JiraUnknownPayload("intake content batch limit exceeded")
            items.append(item)
        return items

    def _normalize_inbox_node(
        self,
        node: dict[str, Any],
        *,
        project: str,
        account_id: str,
        response_bytes: list[int],
        parent_cache: dict[str, tuple[str, str]],
    ) -> JiraInboxIssue:
        key = _intake_key(node.get("key"), project=project)
        fields = node.get("fields")
        if not isinstance(fields, dict):
            raise JiraUnknownPayload("invalid intake issue fields")
        summary = _intake_text(fields.get("summary"), field="summary", required=True)
        issue_type, is_subtask = self._intake_issue_type(fields)
        self._validate_intake_assignee(fields, account_id)
        description = _flatten_intake_adf(fields.get("description"))
        parent_key = self._intake_parent_key(fields, project, is_subtask, description)
        parent_summary: str | None = None
        parent_description: str | None = None
        if parent_key is not None:
            parent_summary, parent_description = self._load_intake_parent(
                parent_key, project, response_bytes, parent_cache
            )
        return JiraInboxIssue(
            key=key,
            summary=summary,
            description=description,
            issue_type=issue_type,
            parent_key=parent_key,
            parent_summary=parent_summary,
            parent_description=parent_description,
        )

    @staticmethod
    def _intake_issue_type(fields: dict[str, Any]) -> tuple[str, bool]:
        issue_type = fields.get("issuetype")
        if not isinstance(issue_type, dict):
            raise JiraUnknownPayload("invalid intake issue type")
        name = _intake_text(issue_type.get("name"), field="issue_type", required=True)
        is_subtask = issue_type.get("subtask")
        if type(is_subtask) is not bool:
            raise JiraUnknownPayload("invalid intake issue type")
        return name, is_subtask

    @staticmethod
    def _validate_intake_assignee(fields: dict[str, Any], account_id: str) -> None:
        assignee = fields.get("assignee")
        if not isinstance(assignee, dict):
            raise JiraUnknownPayload("missing intake assignee")
        assigned_id = assignee.get("accountId")
        if not isinstance(assigned_id, str) or assigned_id != account_id:
            raise JiraUnknownPayload("foreign intake assignee")

    @staticmethod
    def _intake_parent_key(
        fields: dict[str, Any], project: str, is_subtask: bool, description: str
    ) -> str | None:
        parent = fields.get("parent")
        if parent is None:
            if is_subtask and not description:
                raise JiraUnknownPayload("missing required intake parent")
            return None
        if not isinstance(parent, dict):
            raise JiraUnknownPayload("invalid intake parent")
        parent_key = _intake_key(parent.get("key"), project=project)
        if is_subtask and not description:
            return parent_key
        return None

    def _load_intake_parent(
        self,
        parent_key: str,
        project: str,
        response_bytes: list[int],
        cache: dict[str, tuple[str, str]],
    ) -> tuple[str, str]:
        cached = cache.get(parent_key)
        if cached is not None:
            return cached
        payload = self._intake_get_json(
            f"{API_BASE}/issue/{parent_key}",
            response_bytes,
            params={"fields": "summary,description,issuetype"},
        )
        response_key = _intake_key(payload.get("key"), project=project)
        if response_key != parent_key:
            raise JiraUnknownPayload("mismatched intake parent key")
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            raise JiraUnknownPayload("invalid intake parent fields")
        self._intake_issue_type(fields)
        summary = _intake_text(
            fields.get("summary"), field="parent_summary", required=True
        )
        description = _flatten_intake_adf(fields.get("description"), required=True)
        cache[parent_key] = (summary, description)
        return summary, description

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
