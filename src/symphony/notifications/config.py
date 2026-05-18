"""Typed configuration for the notifications subsystem.

Parsed from the ``notifications:`` block of WORKFLOW.md frontmatter. The
``slack`` block is the only built-in channel today, but the dispatcher is
designed so a future ``discord:`` / ``webhook:`` block slots in next to it
without touching the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


DEFAULT_SLACK_TIMEOUT_MS = 5_000


@dataclass(frozen=True)
class SlackConfig:
    """Slack incoming-webhook channel."""

    webhook_url: str
    enabled: bool = True
    # Empty tuple = notify on every state transition (default ask).
    # Populated = notify only when the *target* state matches one of these
    # (case-insensitive). Lets PMs subscribe to ``["Done", "Blocked"]``
    # without seeing every Explore/Plan handoff.
    notify_on_states: tuple[str, ...] = ()
    # Per-target-state message overrides; key is the lowercased state name.
    # See :mod:`symphony.notifications.events` for available placeholders.
    templates: dict[str, str] = field(default_factory=dict)
    username: str = ""
    icon_emoji: str = ""
    icon_url: str = ""
    channel: str = ""
    timeout_ms: int = DEFAULT_SLACK_TIMEOUT_MS

    def template_for(self, state: str) -> str | None:
        return self.templates.get(state.strip().lower())

    def matches_state(self, state: str) -> bool:
        if not self.notify_on_states:
            return True
        target = state.strip().lower()
        return any(target == s.strip().lower() for s in self.notify_on_states)


@dataclass(frozen=True)
class NotificationsConfig:
    """Top-level container; one optional sub-block per channel."""

    slack: SlackConfig | None = None

    def has_any(self) -> bool:
        return self.slack is not None and self.slack.enabled


def build_notifications_config(
    raw: Any,
    *,
    resolve_var: Callable[[Any], Any],
) -> NotificationsConfig:
    """Parse the ``notifications:`` block.

    ``resolve_var`` is :func:`symphony.workflow.resolve_var_indirection`
    injected here to avoid a circular import. It maps ``$VAR_NAME`` to the
    environment value so secrets stay out of the workflow file.
    """
    if not isinstance(raw, dict):
        return NotificationsConfig()
    slack_raw = raw.get("slack")
    if not isinstance(slack_raw, dict):
        return NotificationsConfig()

    webhook_url = _resolved_str(slack_raw.get("webhook_url"), resolve_var)
    if not webhook_url:
        # No webhook = silently disabled. Don't raise: the user may have
        # forgotten to export the env var on a dev box.
        return NotificationsConfig()

    enabled = _as_bool(slack_raw.get("enabled"), default=True)
    notify_on_states = _as_str_tuple(slack_raw.get("notify_on_states"))
    templates = _as_template_map(slack_raw.get("templates"))
    timeout_ms = _as_positive_int(
        slack_raw.get("timeout_ms"), default=DEFAULT_SLACK_TIMEOUT_MS
    )

    slack = SlackConfig(
        webhook_url=webhook_url,
        enabled=enabled,
        notify_on_states=notify_on_states,
        templates=templates,
        username=_as_str(slack_raw.get("username")),
        icon_emoji=_as_str(slack_raw.get("icon_emoji")),
        icon_url=_as_str(slack_raw.get("icon_url")),
        channel=_as_str(slack_raw.get("channel")),
        timeout_ms=timeout_ms,
    )
    return NotificationsConfig(slack=slack)


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _resolved_str(value: Any, resolve_var: Callable[[Any], Any]) -> str:
    """Apply ``$VAR`` resolution then coerce to string."""
    resolved = resolve_var(value) if isinstance(value, str) else value
    return resolved if isinstance(resolved, str) else ""


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _as_template_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not isinstance(raw, str):
            continue
        out[key.strip().lower()] = raw
    return out


def _as_positive_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return default
    return ivalue if ivalue > 0 else default
