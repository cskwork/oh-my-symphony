"""Pure helpers + the per-card runtime overlay dataclass.

Everything in this module is dependency-light: rendering uses Rich
`Text`, but the helpers themselves don't touch Textual widgets. Tests
import most of these directly to assert behavior without spinning up a
Textual app.

`_fetch_candidates` and `_fetch_terminals` deliberately live here (not
in the app) so unit tests can `monkeypatch.setattr("symphony.tui._fetch_candidates", ...)`
and the live `KanbanApp` picks up the stub at call time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from rich.text import Text

from ..issue import Issue, normalize_state, registration_order_key
from ..trackers import build_tracker_client
from ..workflow import ServiceConfig


@dataclass
class _CardStatus:
    """Per-issue runtime overlay for a kanban card."""

    runtime: str = "idle"  # idle, running, retrying, completed
    turn: int = 0
    attempt_turn: int = 0
    attempt_kind: str = ""
    last_event: str = ""
    last_event_at: datetime | None = None
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    attempt: int | None = None
    error: str | None = None
    last_message: str = ""
    agent_kind: str = ""
    # True when the orchestrator has been asked to hold this worker at the
    # next turn boundary. Surfaced from `snapshot()["running"][N]["paused"]`.
    paused: bool = False
    attention: dict[str, Any] | None = None


def _parse_iso(value: Any) -> datetime | None:
    """Parse the ISO-8601 strings the orchestrator emits for `last_event_at`.

    Returns None for missing/malformed values — the renderer treats `None`
    as "no data", which is the right fallback during the first poll tick
    (before any agent event has fired) and across orchestrator restarts.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _silent_seconds(last_event_at: datetime | None) -> float | None:
    if last_event_at is None:
        return None
    now = datetime.now(timezone.utc)
    return max(0.0, (now - last_event_at).total_seconds())


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: max(n - 1, 1)] + "…"


def _attention_label(attention: dict[str, Any] | None) -> str:
    if not attention:
        return ""
    label = attention.get("label") or attention.get("kind") or "Attention"
    return str(label)


def _append_attention_meta(
    text: Text, attention: dict[str, Any] | None, *, include_due_at: bool
) -> None:
    if not attention:
        return
    severity = str(attention.get("severity") or "warning")
    style = {
        "error": "bold red",
        "warning": "bold yellow",
        "info": "bold cyan",
    }.get(severity, "bold yellow")
    label = _attention_label(attention)
    message = str(attention.get("message") or "").strip()
    text.append(f"! {label}", style=style)
    if message:
        text.append(f": {message}", style="dim")
    due_at = attention.get("due_at")
    if include_due_at and due_at:
        text.append(f"  due {due_at}", style="dim cyan")


def _first_meaningful_line(description: str | None) -> str:
    """description 본문에서 첫 의미 있는 줄 (markdown 헤딩/코드펜스 skip) 반환."""
    if not description:
        return ""
    for raw in description.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(("#", "```", "---")):
            continue
        return s
    return ""


def _card_sort_key(issue: Issue) -> tuple[int, str, int, float, str]:
    return registration_order_key(issue)


def _ordered_column_states(cfg: ServiceConfig) -> list[str]:
    column_states: list[str] = list(cfg.tracker.active_states) + list(
        cfg.tracker.terminal_states
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for state in column_states:
        key = normalize_state(state)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(state)
    return ordered


def _stage_position(
    state: str, cfg: ServiceConfig | None
) -> tuple[int, int] | None:
    if cfg is None:
        return None
    active = list(cfg.tracker.active_states)
    total = len(active)
    if total == 0:
        return None
    state_key = normalize_state(state)
    for idx, name in enumerate(active, start=1):
        if normalize_state(name) == state_key:
            return idx, total
    return None


def _compact_rate_limits(rl: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in rl.items():
        if isinstance(value, (int, float, str)):
            parts.append(f"{key}={value}")
        if len(parts) >= 3:
            break
    return ", ".join(parts) if parts else "n/a"


def _build_runtime_index(snap: dict[str, Any]) -> dict[str, _CardStatus]:
    index: dict[str, _CardStatus] = {}
    for row in snap.get("running", []) or []:
        issue_id = row.get("issue_id") or ""
        tokens_block = row.get("tokens") or {}
        index[issue_id] = _CardStatus(
            runtime="running",
            turn=int(row.get("turn_count", 0) or 0),
            attempt_turn=int(row.get("attempt_turn_count", 0) or 0),
            attempt_kind=str(row.get("attempt_kind") or ""),
            last_event=str(row.get("last_event") or ""),
            last_event_at=_parse_iso(row.get("last_event_at")),
            tokens=int(tokens_block.get("total_tokens") or 0),
            input_tokens=int(tokens_block.get("input_tokens") or 0),
            output_tokens=int(tokens_block.get("output_tokens") or 0),
            last_message=str(row.get("last_message") or ""),
            agent_kind=str(row.get("agent_kind") or ""),
            paused=bool(row.get("paused", False)),
            attention=row.get("attention") if isinstance(row.get("attention"), dict) else None,
        )
    for row in snap.get("retrying", []) or []:
        issue_id = row.get("issue_id") or ""
        index[issue_id] = _CardStatus(
            runtime="retrying",
            attempt=int(row.get("attempt", 0) or 0),
            error=str(row.get("error") or "") or None,
            paused=bool(row.get("paused", False)),
            attention=row.get("attention") if isinstance(row.get("attention"), dict) else None,
        )
    return index


def _fetch_candidates(cfg: ServiceConfig) -> list[Issue]:
    client = build_tracker_client(cfg)
    try:
        return client.fetch_candidate_issues()
    finally:
        client.close()


def _fetch_terminals(cfg: ServiceConfig) -> list[Issue]:
    client = build_tracker_client(cfg)
    try:
        return client.fetch_issues_by_states(cfg.tracker.terminal_states)
    finally:
        client.close()


def _append_token_meta(text: Text, status: _CardStatus, *, dim: bool) -> None:
    input_style = "dim cyan" if dim else "cyan"
    output_style = "dim bright_cyan" if dim else "bright_cyan"
    total_style = "dim bold cyan" if dim else "bold cyan"
    text.append(f"in={status.input_tokens:,}", style=input_style)
    text.append(" / ", style="dim")
    text.append(f"out={status.output_tokens:,}", style=output_style)
    text.append(" / ", style="dim")
    text.append(f"total={status.tokens:,}", style=total_style)


def _safe_id(value: str) -> str:
    """Coerce arbitrary tracker IDs into Textual-safe widget IDs."""
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    return out or "unnamed"


def _matches_filter(issue: Issue, query: str) -> bool:
    """Case-insensitive substring match against identifier / title / labels.

    `query` must already be lowercased by the caller — saving the .lower() per
    candidate keeps the per-tick filter cheap when the board has many cards.
    """
    if not query:
        return True
    if query in (issue.identifier or "").lower():
        return True
    if query in (issue.title or "").lower():
        return True
    for label in issue.labels or ():
        if query in (label or "").lower():
            return True
    return False
