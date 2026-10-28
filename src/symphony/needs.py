"""Durable, actionable summaries of tickets waiting for operator help."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from .issue import Issue, normalize_state
from .logging import get_logger
from .notifications import NotificationEvent, dispatch_notification
from .utils.atomic_json import write_json_atomic
from .workflow import ServiceConfig

log = get_logger()


class Need(TypedDict):
    state: str
    since: str
    reason: str
    action: str


def needs_path(workflow_path: Path) -> Path:
    return workflow_path.parent / ".symphony" / "needs.json"


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("needs_load_failed", path=str(path), error=str(exc))
        return {}
    return value if isinstance(value, dict) else {}


def load_needs(workflow_path: Path) -> dict[str, Need]:
    result: dict[str, Need] = {}
    for identifier, value in _read_object(needs_path(workflow_path)).items():
        if isinstance(value, dict) and all(
            isinstance(value.get(key), str) for key in ("state", "since", "reason", "action")
        ):
            result[identifier] = Need(
                state=value["state"], since=value["since"],
                reason=value["reason"], action=value["action"],
            )
    return result


def needs_states(cfg: ServiceConfig) -> tuple[str, ...]:
    states = [cfg.agent.budget_exhausted_state.strip()]
    states.extend(s for s in cfg.tracker.terminal_states if normalize_state(s) == "blocked")
    return tuple(dict.fromkeys(state for state in states if state))


def _reason_action(description: str | None) -> tuple[str, str]:
    # Ignore headings in fenced command output, which may itself contain Markdown.
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    fence: str | None = None
    for line in (description or "").splitlines():
        if line.startswith(("    ", "\t")):
            continue
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        heading = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if heading:
            name = heading.group(1).strip().casefold()
            current = []
            if name in {"verify failure", "environment block", "blocked", "contract failure"}:
                sections.append((name, current))
        elif current is not None and line.strip():
            current.append(line.strip())
    if not sections:
        return "unknown", "open the ticket"
    heading, lines = sections[-1]
    if heading == "verify failure":
        for line in lines:
            command = re.match(r"^(?:[-*]\s+)?Command:\s*`(.+)`\s*$", line, re.IGNORECASE)
            if command:
                cmd = command.group(1)
                return f"verify command failed: {cmd}", f"make this command pass: {cmd}"
            if line.startswith("Command: "):
                try:
                    cmd = json.loads(line.removeprefix("Command: "))
                except ValueError:
                    continue
                if isinstance(cmd, str) and cmd:
                    return f"verify command failed: {cmd}", f"make this command pass: {cmd}"
        return "unknown", "open the ticket"
    reason = lines[0] if lines else "unknown"
    action = lines[1] if len(lines) > 1 else "open the ticket"
    for line in lines:
        labelled = re.match(r"^(?:[-*]\s+)?(Reason|Action):\s*(.+)$", line, re.IGNORECASE)
        if labelled:
            if labelled.group(1).lower() == "reason":
                reason = labelled.group(2)
            else:
                action = labelled.group(2)
    return reason, action


def refresh_needs(
    cfg: ServiceConfig, issues: list[Issue], *, now: datetime | None = None,
) -> dict[str, Need]:
    """Reconcile a complete needs-state snapshot, retaining each entry's first sighting."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    previous = load_needs(cfg.workflow_path)
    wanted = {normalize_state(state) for state in needs_states(cfg)}
    needs: dict[str, Need] = {}
    for issue in issues:
        if normalize_state(issue.state) not in wanted:
            continue
        reason, action = _reason_action(issue.description)
        old = previous.get(issue.identifier)
        needs[issue.identifier] = Need(
            state=issue.state,
            since=old["since"] if old else now.isoformat().replace("+00:00", "Z"),
            reason=reason,
            action=action,
        )
    if needs != previous:
        write_json_atomic(needs_path(cfg.workflow_path), needs)
    _notify_needs(cfg, needs, now)
    return needs


def _notify_needs(cfg: ServiceConfig, needs: dict[str, Need], now: datetime) -> None:
    if not needs or not cfg.notifications.has_any():
        return
    path = cfg.workflow_path.parent / ".symphony" / "needs-sent.json"
    day = now.date().isoformat()
    persisted = _read_object(path)
    raw_sent = persisted.get("reasons", []) if persisted.get("day") == day else []
    sent = {item for item in raw_sent if isinstance(item, str)} if isinstance(raw_sent, list) else set()
    groups: dict[str, list[str]] = {}
    for identifier, need in needs.items():
        groups.setdefault(need["reason"], []).append(identifier)
    for reason, identifiers in groups.items():
        if reason in sent:
            continue
        identifiers.sort()
        need = needs[identifiers[0]]
        event = NotificationEvent(
            identifier=", ".join(identifiers),
            title=f"{reason}; {need['action']}",
            prev_state="", next_state=need["state"],
            workflow=cfg.workflow_path.parent.name,
            reason=reason,
        )
        try:
            delivered = dispatch_notification(cfg.notifications, event)
        except Exception as exc:
            log.warning("needs_notification_failed", reason=reason, error=str(exc))
            continue
        if not delivered:
            log.warning("needs_notification_failed", reason=reason, error="not delivered")
            continue
        sent.add(reason)
        try:
            write_json_atomic(path, {"day": day, "reasons": sorted(sent)})
        except OSError as exc:
            log.warning("needs_notification_persist_failed", error=str(exc))
