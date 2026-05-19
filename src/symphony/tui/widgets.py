"""Lane / card / detail-pane / filter-bar / stats-bar Textual widgets.

These are the visual surfaces the user actually focuses and interacts
with. The main `KanbanApp` (in `app.py`) is responsible for composing
them; tests instantiate them directly through `app.run_test()`.

`IssueCard.open_details` lazy-imports `TicketDetailScreen` to keep the
widgets→screens edge one-way at import time.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.widgets import Input, Static

from ..i18n import t
from ..issue import Issue, normalize_state
from ..workflow import ServiceConfig
from .constants import (
    AGENT_COLOR,
    DENSITY_COMPACT,
    DENSITY_RICH,
    SILENT_THRESHOLD_S,
    STATE_COLOR,
    _RUNNING_IDS_MAX,
)
from .helpers import (
    _CardStatus,
    _append_token_meta,
    _compact_rate_limits,
    _first_meaningful_line,
    _safe_id,
    _silent_seconds,
    _truncate,
)


class IssueCard(Static):
    """A focusable card for a single ticket. Body text is set via `update()`."""

    DEFAULT_CSS = """
    IssueCard {
        border: round $surface;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
        min-height: 3;
    }
    IssueCard.-compact {
        margin-bottom: 0;
        min-height: 1;
        padding: 0 1;
        border: none;
    }
    IssueCard:focus { border: round $accent; background: $boost; }
    IssueCard.-compact:focus { background: $boost; border: none; }
    IssueCard.-running { border: round green; }
    IssueCard.-retrying { border: round yellow; }
    IssueCard.-completed { border: round $success-darken-1; color: $text-muted; }
    IssueCard.-paused { border: round magenta; }
    IssueCard.-compact.-running { color: green; border: none; }
    IssueCard.-compact.-retrying { color: yellow; border: none; }
    IssueCard.-compact.-completed { color: $text-muted; border: none; }
    IssueCard.-compact.-paused { color: magenta; border: none; }
    """

    can_focus = True

    def __init__(
        self,
        issue: Issue,
        status: _CardStatus,
        language: str,
        *,
        density: str = DENSITY_RICH,
    ) -> None:
        super().__init__("")
        self._issue = issue
        self._status = status
        self._language = language
        self._density = density
        self.id = f"card-{_safe_id(issue.id)}"
        self._refresh_body()

    @property
    def issue(self) -> Issue:
        return self._issue

    @property
    def status(self) -> _CardStatus:
        return self._status

    @property
    def density(self) -> str:
        return self._density

    def update_status(self, status: _CardStatus) -> None:
        self._status = status
        self._refresh_body()

    def set_density(self, density: str) -> None:
        if density == self._density:
            return
        self._density = density
        self._refresh_body()

    def _refresh_body(self) -> None:
        self.set_classes("")  # reset variant classes
        if self._density == DENSITY_COMPACT:
            self.add_class("-compact")
        if self._status.runtime in ("running", "retrying", "completed"):
            self.add_class(f"-{self._status.runtime}")
        # Pause variant is orthogonal to runtime — it overlays "running" so
        # the border colour changes while still flagging it as in-flight.
        if self._status.paused:
            self.add_class("-paused")
        self.update(self._render_body())

    def _render_body(self) -> Text:
        if self._density == DENSITY_COMPACT:
            return self._render_compact()
        return self._render_rich()

    def _render_compact(self) -> Text:
        """One-line summary for dense boards: ID • badge • title • tokens."""
        issue = self._issue
        status = self._status
        color = STATE_COLOR.get(normalize_state(issue.state), "white")
        line = Text()
        line.append(issue.identifier, style=f"bold {color}")
        if status.runtime == "running" and status.paused:
            line.append(" ⏸", style="bold bright_magenta")
        elif status.runtime == "running":
            line.append(" ●", style="bold green")
        elif status.runtime == "retrying":
            line.append(" ↻", style="bold yellow")
        elif status.runtime == "completed":
            line.append(" ✓", style="bold green")
        if issue.priority:
            line.append(f" P{issue.priority}", style="bright_red bold")
        line.append("  ")
        line.append(_truncate(issue.title or "", 60), style="white")
        if status.runtime == "running":
            silent_s = _silent_seconds(status.last_event_at)
            if silent_s is not None and silent_s >= SILENT_THRESHOLD_S:
                line.append(f"  silent {int(silent_s)}s", style="bold yellow")
        if status.tokens:
            line.append(f"  {status.tokens:,}t", style="dim cyan")
        return line

    def _render_rich(self) -> Text:
        issue = self._issue
        status = self._status
        language = self._language
        color = STATE_COLOR.get(normalize_state(issue.state), "white")

        title = Text(issue.identifier, style=f"bold {color}")
        if status.runtime == "running" and status.paused:
            title.append("  ⏸", style="bold bright_magenta")
        elif status.runtime == "running":
            title.append("  ●", style="bold green")
        elif status.runtime == "retrying":
            title.append("  ↻", style="bold yellow")
        elif status.runtime == "completed":
            title.append("  ✓", style="bold green")

        body = Text()
        body.append(_truncate(issue.title, 60), style="white")
        if issue.priority:
            body.append(f"  P{issue.priority}", style="bright_red bold")

        meta = Text()
        if status.runtime == "running":
            if status.paused:
                meta.append(
                    f"{t('card.paused', language)}  ",
                    style="bold bright_magenta",
                )
            if status.agent_kind:
                agent_color = AGENT_COLOR.get(status.agent_kind, "white")
                meta.append(status.agent_kind, style=f"bold {agent_color}")
                meta.append("  ")
            meta.append(f"{t('card.turn', language)} {status.turn}", style="green")
            if status.attempt_kind in ("continuation", "retry") and status.attempt_turn:
                meta.append(
                    f"  {status.attempt_kind} {status.attempt_turn}",
                    style="dim",
                )
            silent_s = _silent_seconds(status.last_event_at)
            # Paused workers are intentionally idle — suppress the silent
            # badge so the card doesn't look stuck when the operator put
            # it on hold.
            if (
                not status.paused
                and silent_s is not None
                and silent_s >= SILENT_THRESHOLD_S
            ):
                meta.append(f"  silent {int(silent_s)}s", style="bold yellow")
            if status.last_event:
                meta.append(f"  {status.last_event}", style="dim")
            if status.input_tokens or status.output_tokens or status.tokens:
                meta.append("  ")
                _append_token_meta(meta, status, dim=False)
        elif status.runtime == "retrying":
            meta.append(f"{t('card.retry', language)}{status.attempt}", style="yellow")
            if status.error:
                meta.append(f"  {_truncate(status.error, 40)}", style="dim red")
        elif issue.blocked_by:
            blocker_names = [b.identifier for b in issue.blocked_by[:3] if b.identifier]
            if blocker_names:
                meta.append(
                    f"{t('card.blocked_by', language)} {', '.join(blocker_names)}",
                    style="dim red",
                )
        elif issue.labels:
            meta.append("  ".join(f"#{l}" for l in issue.labels[:3]), style="dim")

        # Idle/completed cards still surface aggregate token spend so an
        # operator can audit cost after a run wraps.
        tokens_line = Text()
        if status.runtime != "running" and (
            status.input_tokens or status.output_tokens or status.tokens
        ):
            _append_token_meta(tokens_line, status, dim=True)

        out = Text.assemble(title, "\n", body)
        desc_preview = _first_meaningful_line(issue.description)
        if desc_preview:
            out.append("\n")
            out.append(_truncate(desc_preview, 80), style="dim")
        if meta.plain.strip():
            out.append("\n")
            out.append_text(meta)
        if tokens_line.plain.strip():
            out.append("\n")
            out.append_text(tokens_line)
        if status.last_message:
            out.append("\n")
            out.append(_truncate(status.last_message, 90), style="dim italic")
        return out

    def on_click(self) -> None:
        self.focus()

    def open_details(self) -> None:
        # Lazy import keeps the widgets→screens edge one-way at module
        # load time, breaking the would-be cycle (screens.py imports
        # `_CardStatus` from helpers, which would otherwise tug widgets in).
        from .screens import TicketDetailScreen

        self.app.push_screen(TicketDetailScreen(self._issue, self._status, self._language))


class Lane(Vertical):
    """One Kanban lane: title bar + a vertical scroll of IssueCards."""

    DEFAULT_CSS = """
    Lane {
        width: 1fr;
        height: 1fr;
        border: round $surface;
        padding: 0 1;
    }
    Lane.-active { border: round $accent; }
    Lane.-empty { border: round $surface-darken-1; color: $text-muted; }
    Lane.-zoomed { border: round $accent; }
    Lane.-terminal { border: round $surface-darken-1; }
    Lane > .lane-title { height: 1; text-style: bold; }
    Lane > .lane-legend { height: auto; color: $text-muted; text-style: italic; }
    Lane > VerticalScroll { height: 1fr; }
    """

    can_focus = True

    def __init__(self, state_label: str, color: str, legend: str | None) -> None:
        super().__init__()
        self._state_label = state_label
        self._color = color
        self._legend = legend
        self._title = Static("", classes="lane-title")
        self._legend_widget = Static(legend or "", classes="lane-legend")
        self._scroll = VerticalScroll()
        self.id = f"lane-{_safe_id(state_label)}"
        self.border_title = state_label
        self._card_count = 0

    @property
    def state_label(self) -> str:
        return self._state_label

    @property
    def card_count(self) -> int:
        return self._card_count

    @property
    def is_empty(self) -> bool:
        return self._card_count == 0

    def compose(self) -> ComposeResult:
        yield self._title
        if self._legend:
            yield self._legend_widget
        yield self._scroll

    def set_count(self, count: int) -> None:
        self._card_count = count
        self._title.update(Text(f"{self._state_label} ({count})", style=f"bold {self._color}"))
        self.border_title = f"{self._state_label} ({count})"

    def render_cards(
        self,
        cards: list[tuple[Issue, _CardStatus]],
        empty_text: str,
        language: str,
        *,
        density: str = DENSITY_RICH,
    ) -> None:
        # Diff against existing widgets so we never tear down a card the user
        # is interacting with (focus / scroll position). `remove_children()`
        # is asynchronous in Textual; remounting on every tick would race the
        # pending removal queue and raise DuplicateIds.
        existing: dict[str, IssueCard] = {
            child.id: child  # type: ignore[misc]
            for child in self._scroll.children
            if isinstance(child, IssueCard) and child.id
        }
        # Drop the empty-state widget if we now have cards.
        if cards:
            for child in list(self._scroll.children):
                if not isinstance(child, IssueCard):
                    child.remove()
        wanted_ids: set[str] = set()
        for issue, status in cards:
            card_id = f"card-{_safe_id(issue.id)}"
            wanted_ids.add(card_id)
            existing_card = existing.pop(card_id, None)
            if existing_card is not None:
                existing_card.update_status(status)
                existing_card.set_density(density)
                continue
            self._scroll.mount(IssueCard(issue, status, language, density=density))
        # Stale cards (issue moved to another lane / closed) get removed.
        for stale_id, stale_card in existing.items():
            if stale_id not in wanted_ids:
                stale_card.remove()
        if not cards and not any(
            isinstance(child, Static) and not isinstance(child, IssueCard)
            for child in self._scroll.children
        ):
            self._scroll.mount(Static(empty_text, classes="lane-empty"))


class StatsBar(Static):
    """Top status row: agent / tracker / counts / tokens."""

    DEFAULT_CSS = """
    StatsBar { height: 1; padding: 0 1; background: $boost; color: $text; }
    """

    def update_from(
        self,
        cfg: ServiceConfig,
        snap: dict[str, Any],
        language: str | None = None,
    ) -> None:
        # `language` lets the App pass the in-session override (`L` toggle).
        # Falls back to `cfg.tui.language` so existing callers / tests that
        # don't know about the override still work.
        lang = language if language is not None else cfg.tui.language
        agent_kind = cfg.agent.kind
        agent_color = AGENT_COLOR.get(agent_kind, "white")
        counts = snap.get("counts", {})
        totals = snap.get("codex_totals", {})

        line = Text()
        line.append(f"{t('header.agent', lang)}", style="dim")
        line.append(agent_kind, style=f"bold {agent_color}")
        line.append(f"  {t('header.tracker', lang)}{cfg.tracker.kind}", style="dim")
        line.append(f"  {t('header.workflow', lang)}{cfg.workflow_path.name}", style="dim")
        line.append(f"  {t('header.lang', lang)}{lang}", style="bright_magenta")
        line.append("    ")
        line.append(f"{t('header.running', lang)}{counts.get('running', 0)}", style="green")
        running_rows = snap.get("running") or []
        if running_rows:
            visible_ids = [
                str(row.get("issue_id") or "")
                for row in running_rows[:_RUNNING_IDS_MAX]
            ]
            visible_ids = [vid for vid in visible_ids if vid]
            if visible_ids:
                line.append(" [", style="green")
                line.append(", ".join(visible_ids), style="bold green")
                overflow = len(running_rows) - len(visible_ids)
                if overflow > 0:
                    line.append(f" +{overflow}", style="dim green")
                line.append("]", style="green")
        line.append("  ")
        line.append(f"{t('header.retrying', lang)}{counts.get('retrying', 0)}  ", style="yellow")
        # Paused count is folded into the header only when non-zero so the
        # status bar stays compact on the common case.
        paused_count = sum(
            1 for row in running_rows if row.get("paused")
        )
        if paused_count:
            line.append(
                f"{t('header.paused', lang)}{paused_count}  ",
                style="bright_magenta",
            )
        line.append("│  ", style="dim")
        line.append(f"{t('footer.tokens', lang)} ", style="dim")
        line.append(f"in={totals.get('input_tokens', 0):,} ", style="cyan")
        line.append(f"out={totals.get('output_tokens', 0):,} ", style="bright_cyan")
        line.append(f"total={totals.get('total_tokens', 0):,}", style="bold cyan")
        rl = snap.get("rate_limits")
        if rl:
            line.append(f"  │  {t('footer.rate_limits', lang)}", style="dim")
            line.append(_compact_rate_limits(rl), style="yellow")
        self.update(line)


class DetailPane(Vertical):
    """Right-side pane mirroring the focused IssueCard.

    Lives next to `#board` inside `#main`. Toggled with `p`. Width collapses to
    `0` (display: none) when hidden so it does not steal lane width — this is
    the cheap operator-mode that lets cards stay one-line in `#board` while
    the full description / last_message / token block lives over here.
    """

    DEFAULT_CSS = """
    DetailPane {
        width: 0;
        display: none;
        border: round $accent;
        padding: 0 1;
    }
    DetailPane.-visible {
        width: 60;
        display: block;
    }
    DetailPane > #detail-title { height: auto; text-style: bold; }
    DetailPane > #detail-meta { height: auto; color: $text-muted; margin-bottom: 1; }
    DetailPane > VerticalScroll { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__(id="detail-pane")
        self._title = Static("", id="detail-title")
        self._meta = Static("", id="detail-meta")
        self._body = Static("", id="detail-body", markup=False)
        self._scroll = VerticalScroll()

    def compose(self) -> ComposeResult:
        yield self._title
        yield self._meta
        with self._scroll:
            yield self._body

    @property
    def scroll(self) -> VerticalScroll:
        # Exposed so the App can `.focus()` the inner scroll directly — once
        # focus lives there, `_focused_scroll` walks straight up to it and
        # arrow / j / k / pgup / pgdn target the description body.
        return self._scroll

    def set_visible(self, visible: bool) -> None:
        if visible:
            self.add_class("-visible")
        else:
            self.remove_class("-visible")

    @property
    def is_open(self) -> bool:
        return self.has_class("-visible")

    def show_for(self, issue: Issue, status: _CardStatus) -> None:
        color = STATE_COLOR.get(normalize_state(issue.state), "white")
        title = Text(issue.identifier, style=f"bold {color}")
        if issue.title:
            title.append(f"  {issue.title}", style="white")
        self._title.update(title)

        meta = Text()
        meta.append(f"state={issue.state}", style="dim")
        if issue.priority:
            meta.append(f"  P{issue.priority}", style="bright_red bold")
        if issue.labels:
            meta.append("  " + " ".join(f"#{l}" for l in issue.labels), style="dim")
        if status.runtime != "idle":
            runtime_label = (
                f"runtime={status.runtime} (paused)"
                if status.paused
                else f"runtime={status.runtime}"
            )
            runtime_style = "bright_magenta" if status.paused else "green"
            meta.append(f"\n{runtime_label}", style=runtime_style)
            if status.agent_kind:
                meta.append(f"  agent={status.agent_kind}", style="dim")
            if status.turn:
                meta.append(f"  turn={status.turn}", style="dim")
            if status.attempt:
                meta.append(f"  retry#{status.attempt}", style="yellow")
            if status.error:
                meta.append(f"\nerror: {status.error}", style="red")
        if status.tokens or status.input_tokens or status.output_tokens:
            meta.append("\n")
            _append_token_meta(meta, status, dim=False)
        self._meta.update(meta)

        body = issue.description or "(no description)"
        if status.last_message:
            body = f"{body}\n\n— last message —\n{status.last_message}"
        self._body.update(body)

    def show_placeholder(self) -> None:
        self._title.update(Text("(no card focused)", style="dim italic"))
        self._meta.update("")
        self._body.update("Press p to hide this pane, or focus a card.")


class FilterBar(Container):
    """One-line filter prompt above the footer. Hidden until `/` is pressed."""

    DEFAULT_CSS = """
    FilterBar {
        height: 0;
        display: none;
    }
    FilterBar.-visible { height: 3; display: block; }
    FilterBar > Input { height: 3; }
    """

    def __init__(self) -> None:
        super().__init__(id="filter-bar")

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="filter: type to match identifier/title/labels — esc to clear",
            id="filter-input",
        )

    def set_visible(self, visible: bool) -> None:
        if visible:
            self.add_class("-visible")
        else:
            self.remove_class("-visible")

    @property
    def is_open(self) -> bool:
        return self.has_class("-visible")
