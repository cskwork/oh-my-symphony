"""Default-off, read-only Jira inbox synchronization for file boards."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from .errors import (
    JiraApiRequestError,
    JiraApiStatusError,
    JiraUnknownPayload,
    SymphonyError,
)
from .trackers.file import (
    JIRA_SOURCE_END,
    JIRA_SOURCE_START,
    ExternalSourceUpdate,
    FileBoardTracker,
)
from .trackers.jira import JiraClient, JiraInboxIssue
from .workflow import ServiceConfig, TrackerConfig


MAX_RENDERED_CARD_BYTES = 256_000
MAX_RENDERED_BATCH_BYTES = 4_000_000
SOURCE_SCHEMA = "aidt-jira-source-v1"
_ENV_REFERENCE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
_ACCEPTANCE_CRITERIA = re.compile(
    r"\b(acceptance)\s+(criteria)\b", re.IGNORECASE
)
_FAILURE_CATEGORIES = {
    "board_preflight_failed",
    "config_invalid",
    "content_invalid",
    "http_error",
    "internal_error",
    "invalid_response",
    "transport_error",
    "unauthorized",
}


class JiraIntakeFailure(SymphonyError):
    code = "jira_intake_failure"

    def __init__(self, category: str, *, status: int | None = None) -> None:
        safe_category = category if category in _FAILURE_CATEGORIES else "internal_error"
        context = {"status": status} if isinstance(status, int) else {}
        super().__init__(safe_category, **context)
        self.category = safe_category
        self.status = status if isinstance(status, int) else None

    @property
    def health_message(self) -> str:
        if self.status is not None:
            return f"{self.category} (HTTP {self.status})"
        return self.category


@dataclass(frozen=True)
class JiraIntakeResult:
    enabled: bool
    fetched: int
    changed: int


@dataclass(frozen=True)
class _JiraIntakeSettings:
    endpoint: str
    email: str
    api_key: str
    project: str
    statuses: tuple[str, ...]
    new_card_state: str


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JiraIntakeFailure("config_invalid")
    return value


def _resolve_env(raw: dict[str, Any], key: str) -> str:
    reference = _required_text(raw, key)
    match = _ENV_REFERENCE.fullmatch(reference)
    if match is None:
        raise JiraIntakeFailure("config_invalid")
    value = os.environ.get(match.group(1))
    if not isinstance(value, str) or not value:
        raise JiraIntakeFailure("config_invalid")
    return value


def _settings(cfg: ServiceConfig) -> _JiraIntakeSettings | None:
    raw = cfg.raw.get("jira_intake")
    if raw is None:
        return None
    if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
        raise JiraIntakeFailure("config_invalid")
    if raw.get("enabled") is not True:
        return None
    if cfg.tracker.kind != "file" or cfg.tracker.board_root is None:
        raise JiraIntakeFailure("config_invalid")
    statuses = raw.get("statuses")
    if not isinstance(statuses, list) or not all(isinstance(item, str) for item in statuses):
        raise JiraIntakeFailure("config_invalid")
    return _JiraIntakeSettings(
        endpoint=_required_text(raw, "endpoint"),
        email=_resolve_env(raw, "email"),
        api_key=_resolve_env(raw, "api_key"),
        project=_required_text(raw, "project"),
        statuses=tuple(statuses),
        new_card_state=_required_text(raw, "new_card_state"),
    )


def _jira_tracker(settings: _JiraIntakeSettings) -> TrackerConfig:
    return TrackerConfig(
        kind="jira",
        endpoint=settings.endpoint,
        api_key=settings.api_key,
        project_slug=settings.project,
        active_states=settings.statuses,
        terminal_states=(),
        email=settings.email,
    )


def _quoted_lines(lines: list[str]) -> list[str]:
    rendered: list[str] = []
    for line in lines:
        parts = line.splitlines() or [""]
        for part in parts:
            escaped = html.escape(part, quote=False)
            inert = _ACCEPTANCE_CRITERIA.sub(r"\1&#32;\2", escaped)
            rendered.append(f"> {inert}")
    return rendered


def _source_parent(item: JiraInboxIssue) -> dict[str, Any] | None:
    if item.parent_key is None:
        return None
    return {
        "key": item.parent_key,
        "summary": item.parent_summary or "",
        "description": item.parent_description or "",
        "components": sorted(item.parent_components, key=str.casefold),
    }


def _source_revision(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"schema": SOURCE_SCHEMA, "value": value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_source_snapshot(item: JiraInboxIssue) -> dict[str, Any]:
    """Return the complete structured Jira routing input and its revision."""
    source: dict[str, Any] = {
        "schema": SOURCE_SCHEMA,
        "kind": "jira",
        "key": item.key,
        "summary": item.summary,
        "description": item.description,
        "components": sorted(item.components, key=str.casefold),
        "status": item.status,
        "priority": item.priority,
        "issue_type": item.issue_type,
        "updated": item.updated,
        "url": item.url,
        "parent": _source_parent(item),
    }
    source["revision"] = _source_revision(source)
    return source


def render_jira_source(item: JiraInboxIssue) -> str:
    """Render Jira-controlled text as inert blockquote lines between markers."""
    lines = [
        f"Jira key: {item.key}",
        f"Issue type: {item.issue_type}",
        f"Status: {item.status}",
        f"Priority: {item.priority or ''}",
        f"Components: {', '.join(item.components)}",
        f"URL: {item.url}",
        f"Summary: {item.summary}",
        "Description:",
        item.description,
    ]
    if item.parent_key is not None:
        lines.extend(
            [
                f"Parent key: {item.parent_key}",
                f"Parent summary: {item.parent_summary or ''}",
                f"Parent components: {', '.join(item.parent_components)}",
                "Parent description:",
                item.parent_description or "",
            ]
        )
    block = "\n".join([JIRA_SOURCE_START, *_quoted_lines(lines), JIRA_SOURCE_END])
    if len(block.encode("utf-8")) > MAX_RENDERED_CARD_BYTES:
        raise JiraIntakeFailure("content_invalid")
    return block


def _updates(
    items: list[JiraInboxIssue], settings: _JiraIntakeSettings
) -> list[ExternalSourceUpdate]:
    updates: list[ExternalSourceUpdate] = []
    total_bytes = 0
    for item in items:
        body = render_jira_source(item)
        total_bytes += len(body.encode("utf-8"))
        if total_bytes > MAX_RENDERED_BATCH_BYTES:
            raise JiraIntakeFailure("content_invalid")
        updates.append(
            ExternalSourceUpdate(
                identifier=item.key,
                title=item.summary,
                state=settings.new_card_state,
                source_kind="jira",
                source_key=item.key,
                body=body,
                source=build_source_snapshot(item),
            )
        )
    return updates


def _normalized_failure(exc: Exception) -> JiraIntakeFailure:
    if isinstance(exc, JiraIntakeFailure):
        return exc
    if isinstance(exc, JiraApiStatusError):
        status = exc.context.get("status")
        safe_status = status if isinstance(status, int) else None
        category = "unauthorized" if safe_status in {401, 403} else "http_error"
        return JiraIntakeFailure(category, status=safe_status)
    if isinstance(exc, JiraApiRequestError):
        return JiraIntakeFailure("transport_error")
    if isinstance(exc, JiraUnknownPayload):
        return JiraIntakeFailure("invalid_response")
    if isinstance(exc, SymphonyError):
        return JiraIntakeFailure("board_preflight_failed")
    return JiraIntakeFailure("internal_error")


def run_jira_intake(
    cfg: ServiceConfig,
    *,
    jira_client_factory: Callable[[TrackerConfig], JiraClient] = JiraClient,
) -> JiraIntakeResult:
    """Fetch the complete Jira batch before opening the file-board write phase."""
    settings = _settings(cfg)
    if settings is None:
        return JiraIntakeResult(enabled=False, fetched=0, changed=0)
    client: JiraClient | None = None
    try:
        client = jira_client_factory(_jira_tracker(settings))
        items = client.fetch_assigned_inbox()
        updates = _updates(items, settings)
        board = FileBoardTracker(cfg.tracker)
        changed = board.upsert_external_sources(updates)
        return JiraIntakeResult(enabled=True, fetched=len(items), changed=changed)
    except Exception as exc:
        raise _normalized_failure(exc) from exc
    finally:
        if client is not None:
            client.close()
