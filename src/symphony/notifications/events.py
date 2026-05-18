"""Notification event payload and template rendering.

A ``NotificationEvent`` is built once per tracker state transition and handed
to each enabled notifier (Slack, future Discord, ...). Templates use
:class:`string.Template` substitution so authors can write ``${identifier}``
without pulling in a templating dep.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template
from typing import Mapping


# Default message body. Used when the user hasn't supplied a per-state
# template. Plain text is fine for Slack incoming webhooks — markdown links
# and emojis Just Work. Keep it terse: PMs read this in a feed.
DEFAULT_TEMPLATE = "[${workflow}] ${identifier} ${prev_state} → ${next_state}: ${title}"


@dataclass(frozen=True)
class NotificationEvent:
    """One ticket transition emitted by the orchestrator."""

    identifier: str
    title: str
    prev_state: str
    next_state: str
    workflow: str
    # Optional human-readable reason ("auto-triage", "budget exhausted", ...).
    # Surfaced via ``${reason}`` so authors can branch templates on it.
    reason: str = ""

    def as_mapping(self) -> dict[str, str]:
        return {
            "identifier": self.identifier,
            "title": self.title,
            "prev_state": self.prev_state,
            "next_state": self.next_state,
            "workflow": self.workflow,
            "reason": self.reason,
        }


def render_message(template: str, event: NotificationEvent) -> str:
    """Render a template against an event.

    Uses ``safe_substitute`` so missing keys render as literal ``$key`` rather
    than raising — a bad template never blocks a notification.
    """
    return Template(template).safe_substitute(_coerced(event.as_mapping()))


def _coerced(mapping: Mapping[str, str]) -> dict[str, str]:
    # ``string.Template`` rejects non-string values with TypeError; coerce so
    # numeric or None values render to empty/string without crashing.
    return {k: "" if v is None else str(v) for k, v in mapping.items()}
