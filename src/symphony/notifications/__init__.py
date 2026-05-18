"""Notifications subsystem.

Public surface kept minimal: the orchestrator only needs the dispatcher and
the event dataclass. Channel-specific config (SlackConfig) is reachable for
tests and for WORKFLOW.md parsing.
"""

from __future__ import annotations

from .config import NotificationsConfig, SlackConfig, build_notifications_config
from .dispatcher import dispatch_notification, build_dispatcher, NotificationDispatcher
from .events import DEFAULT_TEMPLATE, NotificationEvent, render_message


__all__ = [
    "DEFAULT_TEMPLATE",
    "NotificationDispatcher",
    "NotificationEvent",
    "NotificationsConfig",
    "SlackConfig",
    "build_dispatcher",
    "build_notifications_config",
    "dispatch_notification",
    "render_message",
]
