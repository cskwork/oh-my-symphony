"""Slack incoming-webhook notifier.

Stdlib-only: ``urllib.request`` POSTs a JSON body to the webhook URL. We
intentionally avoid the ``slack_sdk`` package — adding a dependency for one
HTTP call would be cost without benefit, and webhook URLs already encode the
channel and auth, so the SDK's richer features don't apply here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..logging import get_logger
from .config import SlackConfig
from .events import DEFAULT_TEMPLATE, NotificationEvent, render_message


log = get_logger()


class SlackNotifier:
    """Send a Slack message per :class:`NotificationEvent`.

    Network failures are caught and logged — a Slack outage must never
    propagate up into the orchestrator's transition path.
    """

    def __init__(
        self,
        config: SlackConfig,
        *,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self._config = config
        # Injectable opener makes unit tests deterministic without
        # monkey-patching ``urllib.request.urlopen`` globally.
        self._opener = opener

    @property
    def config(self) -> SlackConfig:
        return self._config

    def notify(self, event: NotificationEvent) -> bool:
        """Post a Slack message. Returns True on HTTP 2xx, False otherwise."""
        if not self._config.enabled:
            return False
        if not self._config.matches_state(event.next_state):
            return False
        template = self._config.template_for(event.next_state) or DEFAULT_TEMPLATE
        text = render_message(template, event)
        body = self._build_payload(text)
        return self._post(body, event)

    def _build_payload(self, text: str) -> dict[str, str]:
        payload: dict[str, str] = {"text": text}
        if self._config.username:
            payload["username"] = self._config.username
        if self._config.icon_emoji:
            payload["icon_emoji"] = self._config.icon_emoji
        elif self._config.icon_url:
            payload["icon_url"] = self._config.icon_url
        if self._config.channel:
            payload["channel"] = self._config.channel
        return payload

    def _post(self, payload: dict[str, str], event: NotificationEvent) -> bool:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._config.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = max(self._config.timeout_ms, 1) / 1000.0
        try:
            if self._opener is not None:
                response = self._opener.open(request, timeout=timeout)
            else:
                response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - webhook is operator-supplied
        except urllib.error.HTTPError as exc:
            log.warning(
                "slack_notify_http_error",
                identifier=event.identifier,
                status=exc.code,
                error=str(exc),
            )
            return False
        except (urllib.error.URLError, OSError) as exc:
            log.warning(
                "slack_notify_network_error",
                identifier=event.identifier,
                error=str(exc),
            )
            return False
        with response:
            status = getattr(response, "status", None) or response.getcode()
            ok = 200 <= int(status) < 300
        if not ok:
            log.warning(
                "slack_notify_non_2xx",
                identifier=event.identifier,
                status=status,
            )
        return ok
