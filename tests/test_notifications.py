"""Notifications subsystem tests."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any


from symphony.notifications import (
    DEFAULT_TEMPLATE,
    NotificationDispatcher,
    NotificationEvent,
    NotificationsConfig,
    SlackConfig,
    build_dispatcher,
    build_notifications_config,
    dispatch_notification,
    render_message,
)
from symphony.notifications.slack import SlackNotifier
from symphony.workflow import build_service_config, parse_workflow_text


# ---------------------------------------------------------------------------
# events / templates
# ---------------------------------------------------------------------------


def test_default_template_renders_full_event():
    event = NotificationEvent(
        identifier="OLV-7",
        title="Hook Slack into transitions",
        prev_state="Review",
        next_state="Done",
        workflow="symphony-multi-agent",
    )
    assert render_message(DEFAULT_TEMPLATE, event) == (
        "[symphony-multi-agent] OLV-7 Review → Done: Hook Slack into transitions"
    )


def test_render_message_missing_key_safe_substitutes():
    event = NotificationEvent(
        identifier="OLV-7",
        title="t",
        prev_state="A",
        next_state="B",
        workflow="w",
    )
    # Unknown placeholder stays literal — never raises.
    assert render_message("${unknown} ${identifier}", event) == "${unknown} OLV-7"


# ---------------------------------------------------------------------------
# config parsing
# ---------------------------------------------------------------------------


def _identity(value: Any) -> Any:
    return value


def test_config_returns_empty_when_no_webhook():
    cfg = build_notifications_config({"slack": {}}, resolve_var=_identity)
    assert cfg.slack is None
    assert cfg.has_any() is False


def test_config_resolves_dollar_var(monkeypatch):
    monkeypatch.setenv("MY_HOOK", "https://hooks.example/abc")

    def resolver(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            import os
            return os.environ.get(value[1:], "")
        return value

    cfg = build_notifications_config(
        {"slack": {"webhook_url": "$MY_HOOK"}},
        resolve_var=resolver,
    )
    assert cfg.slack is not None
    assert cfg.slack.webhook_url == "https://hooks.example/abc"
    assert cfg.slack.enabled is True
    # default = notify on every state
    assert cfg.slack.notify_on_states == ()
    assert cfg.slack.timeout_ms == 5000


def test_config_normalizes_templates_lowercase_keys():
    cfg = build_notifications_config(
        {
            "slack": {
                "webhook_url": "https://hooks.example/x",
                "templates": {"Done": "done!", "In Progress": "ip"},
                "notify_on_states": ["Done"],
                "timeout_ms": 2500,
                "username": "bot",
                "icon_emoji": ":bell:",
            }
        },
        resolve_var=_identity,
    )
    assert cfg.slack is not None
    assert cfg.slack.template_for("done") == "done!"
    assert cfg.slack.template_for("In Progress") == "ip"
    assert cfg.slack.matches_state("Done") is True
    assert cfg.slack.matches_state("Review") is False
    assert cfg.slack.timeout_ms == 2500
    assert cfg.slack.username == "bot"


def test_config_disabled_explicit():
    cfg = build_notifications_config(
        {
            "slack": {
                "webhook_url": "https://hooks.example/x",
                "enabled": False,
            }
        },
        resolve_var=_identity,
    )
    assert cfg.slack is not None
    assert cfg.slack.enabled is False
    assert cfg.has_any() is False


# ---------------------------------------------------------------------------
# SlackNotifier (HTTP transport)
# ---------------------------------------------------------------------------


class _RecordingOpener:
    """Stand-in for urllib's opener; records calls + lets tests pick the response."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def open(self, request, timeout):  # noqa: D401 - urllib opener signature
        body = request.data.decode("utf-8") if request.data else ""
        self.calls.append(
            {
                "url": request.full_url,
                "headers": dict(request.headers),
                "json": json.loads(body) if body else None,
                "timeout": timeout,
                "method": request.get_method(),
            }
        )
        return _FakeResponse(self.status)


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def _slack(**overrides: Any) -> SlackConfig:
    base = {
        "webhook_url": "https://hooks.slack.com/services/T/B/X",
        "enabled": True,
    }
    base.update(overrides)
    return SlackConfig(**base)


def _event(next_state: str = "Done") -> NotificationEvent:
    return NotificationEvent(
        identifier="OLV-1",
        title="Hook Slack into transitions",
        prev_state="Review",
        next_state=next_state,
        workflow="symphony-multi-agent",
    )


def test_slack_notifier_posts_default_template():
    opener = _RecordingOpener()
    notifier = SlackNotifier(_slack(), opener=opener)
    assert notifier.notify(_event()) is True
    assert len(opener.calls) == 1
    call = opener.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://hooks.slack.com/services/T/B/X"
    # urllib normalizes header names to title-case
    assert any(k.lower() == "content-type" for k in call["headers"])
    assert call["json"] == {
        "text": "[symphony-multi-agent] OLV-1 Review → Done: Hook Slack into transitions"
    }


def test_slack_notifier_skips_disabled_channel():
    opener = _RecordingOpener()
    notifier = SlackNotifier(_slack(enabled=False), opener=opener)
    assert notifier.notify(_event()) is False
    assert opener.calls == []


def test_slack_notifier_state_filter_skips_unmatched():
    opener = _RecordingOpener()
    notifier = SlackNotifier(
        _slack(notify_on_states=("Done", "Blocked")),
        opener=opener,
    )
    assert notifier.notify(_event(next_state="In Progress")) is False
    assert opener.calls == []
    assert notifier.notify(_event(next_state="Done")) is True
    assert len(opener.calls) == 1


def test_slack_notifier_uses_state_specific_template():
    opener = _RecordingOpener()
    notifier = SlackNotifier(
        _slack(templates={"done": "${identifier} is done"}),
        opener=opener,
    )
    notifier.notify(_event())
    assert opener.calls[0]["json"]["text"] == "OLV-1 is done"


def test_slack_notifier_passes_username_and_icon():
    opener = _RecordingOpener()
    notifier = SlackNotifier(
        _slack(username="Symphony", icon_emoji=":robot_face:", channel="#dev"),
        opener=opener,
    )
    notifier.notify(_event())
    payload = opener.calls[0]["json"]
    assert payload["username"] == "Symphony"
    assert payload["icon_emoji"] == ":robot_face:"
    assert payload["channel"] == "#dev"


def test_slack_notifier_swallows_network_errors():
    """Network failure must return False rather than propagate."""
    import urllib.error

    class _Boom:
        def open(self, request, timeout):
            raise urllib.error.URLError("connection refused")

    notifier = SlackNotifier(_slack(), opener=_Boom())
    assert notifier.notify(_event()) is False


def test_slack_notifier_logs_non_2xx():
    opener = _RecordingOpener(status=500)
    notifier = SlackNotifier(_slack(), opener=opener)
    assert notifier.notify(_event()) is False


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


class _RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[NotificationEvent] = []

    def notify(self, event: NotificationEvent) -> bool:
        self.events.append(event)
        return True


def test_dispatcher_skips_identity_transition():
    recorder = _RecordingNotifier()
    dispatcher = NotificationDispatcher(notifiers=(recorder,))
    dispatcher.dispatch(
        NotificationEvent(
            identifier="X",
            title="t",
            prev_state="Done",
            next_state="Done",
            workflow="w",
        )
    )
    assert recorder.events == []


def test_dispatcher_isolates_notifier_failures():
    class _BrokenNotifier:
        def notify(self, event):
            raise RuntimeError("boom")

    good = _RecordingNotifier()
    dispatcher = NotificationDispatcher(notifiers=(_BrokenNotifier(), good))
    dispatcher.dispatch(
        NotificationEvent(
            identifier="X",
            title="t",
            prev_state="A",
            next_state="B",
            workflow="w",
        )
    )
    # The crashing notifier didn't block the second one.
    assert len(good.events) == 1


def test_dispatch_notification_noop_when_disabled():
    cfg = NotificationsConfig()
    dispatch_notification(
        cfg,
        NotificationEvent(
            identifier="X",
            title="t",
            prev_state="A",
            next_state="B",
            workflow="w",
        ),
    )  # should not raise / not require network


def test_build_dispatcher_includes_only_enabled_channels():
    enabled = NotificationsConfig(
        slack=SlackConfig(webhook_url="https://x", enabled=True)
    )
    disabled = NotificationsConfig(
        slack=SlackConfig(webhook_url="https://x", enabled=False)
    )
    assert len(build_dispatcher(enabled).notifiers) == 1
    assert len(build_dispatcher(disabled).notifiers) == 0


# ---------------------------------------------------------------------------
# WORKFLOW.md integration
# ---------------------------------------------------------------------------


def test_workflow_md_parses_notifications_block(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/from-env")
    text = textwrap.dedent(
        """
        ---
        tracker:
          kind: file
          board_root: ./kanban
        notifications:
          slack:
            webhook_url: $SLACK_WEBHOOK_URL
            notify_on_states:
              - Done
              - Blocked
            templates:
              Done: "${identifier} done"
            username: Symphony
        ---
        prompt body
        """
    ).strip()
    path = tmp_path / "WORKFLOW.md"
    path.write_text(text, encoding="utf-8")
    wf = parse_workflow_text(text, path)
    cfg = build_service_config(wf)
    assert cfg.notifications.slack is not None
    assert cfg.notifications.slack.webhook_url == "https://hooks.example/from-env"
    assert cfg.notifications.slack.notify_on_states == ("Done", "Blocked")
    assert cfg.notifications.slack.template_for("Done") == "${identifier} done"
    assert cfg.notifications.slack.username == "Symphony"
    assert cfg.notifications.has_any() is True


def test_workflow_md_missing_notifications_is_empty(tmp_path: Path):
    text = textwrap.dedent(
        """
        ---
        tracker:
          kind: file
          board_root: ./kanban
        ---
        prompt body
        """
    ).strip()
    path = tmp_path / "WORKFLOW.md"
    path.write_text(text, encoding="utf-8")
    wf = parse_workflow_text(text, path)
    cfg = build_service_config(wf)
    assert cfg.notifications.has_any() is False
