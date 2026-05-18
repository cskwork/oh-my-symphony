"""Channel-agnostic dispatcher.

The orchestrator calls :func:`dispatch_notification` after every successful
tracker state write. The dispatcher inspects ``cfg.notifications``, builds
the set of currently-enabled notifiers, and forwards the event to each.

A single channel today (Slack), but the indirection means adding a Discord
or generic-webhook notifier later is a one-file change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..logging import get_logger
from .config import NotificationsConfig
from .events import NotificationEvent
from .slack import SlackNotifier


log = get_logger()


class Notifier(Protocol):
    """Each channel implementation conforms to this surface."""

    def notify(self, event: NotificationEvent) -> bool: ...


@dataclass(frozen=True)
class NotificationDispatcher:
    """Holds the resolved set of notifiers for one config snapshot."""

    notifiers: Sequence[Notifier]

    def dispatch(self, event: NotificationEvent) -> None:
        # Guard transitions where ``prev == next``: e.g. a re-write that
        # lands on the same state shouldn't ping Slack. The orchestrator
        # currently doesn't issue identity transitions, but defensive
        # filtering here keeps the contract clear.
        if event.prev_state.strip().lower() == event.next_state.strip().lower():
            return
        for notifier in self.notifiers:
            try:
                notifier.notify(event)
            except Exception as exc:
                # A misbehaving notifier must not break the orchestrator's
                # transition path. Log loudly so the operator notices.
                log.warning(
                    "notification_dispatch_failed",
                    notifier=type(notifier).__name__,
                    identifier=event.identifier,
                    error=str(exc),
                )


def build_dispatcher(config: NotificationsConfig) -> NotificationDispatcher:
    """Resolve the active notifier set from a parsed config."""
    notifiers: list[Notifier] = []
    if config.slack is not None and config.slack.enabled:
        notifiers.append(SlackNotifier(config.slack))
    return NotificationDispatcher(notifiers=tuple(notifiers))


def dispatch_notification(
    config: NotificationsConfig,
    event: NotificationEvent,
) -> None:
    """Convenience entry point: build + dispatch in one call.

    Cheap to call repeatedly — building a dispatcher is just instantiating
    a handful of dataclasses. The orchestrator uses this so it doesn't have
    to track dispatcher lifecycle across config reloads.
    """
    if not config.has_any():
        return
    build_dispatcher(config).dispatch(event)
