"""Modal screens + cross-thread messages used by the Kanban TUI.

`_RefreshNow` is the message the orchestrator observer thread posts to
ask the Textual loop for a redraw. `TicketDetailScreen` is the full-screen
modal opened by Enter on a focused card. `NewIssueScreen` ('n') registers
a ticket on the file board; `StatsScreen` ('s') shows run statistics;
`HelpScreen` ('?') lists every key binding grouped by purpose.
"""

from __future__ import annotations

from typing import Any

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, TextArea

from ..issue import Issue, normalize_state
from .constants import STATE_COLOR
from .helpers import _CardStatus, _append_attention_meta, _append_token_meta


class _RefreshNow(Message):
    """Posted from the orchestrator observer thread to request a redraw."""


class TicketDetailScreen(ModalScreen[None]):
    """Full ticket detail. Dismiss with Esc or q."""

    DEFAULT_CSS = """
    TicketDetailScreen { align: center middle; }
    #ticket-dialog {
        width: 80%;
        max-width: 120;
        height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #ticket-dialog #ticket-title { text-style: bold; color: $accent; }
    #ticket-dialog #ticket-meta { color: $text-muted; margin-bottom: 1; }
    #ticket-dialog VerticalScroll { height: 1fr; border: round $boost; padding: 0 1; }
    """
    BINDINGS = [
        Binding("escape,q", "dismiss", "Close"),
    ]

    def __init__(self, issue: Issue, status: _CardStatus, language: str) -> None:
        super().__init__()
        self._issue = issue
        self._status = status
        self._language = language

    def compose(self) -> ComposeResult:
        with Container(id="ticket-dialog"):
            yield Static(self._title_text(), id="ticket-title")
            yield Static(self._meta_text(), id="ticket-meta")
            with VerticalScroll():
                yield Static(self._issue.description or "(no description)", markup=False)
            yield Static("[dim]esc / q to close[/dim]")

    def _title_text(self) -> Text:
        color = STATE_COLOR.get(normalize_state(self._issue.state), "white")
        title = Text(f"{self._issue.identifier}  ", style=f"bold {color}")
        title.append(self._issue.title or "")
        return title

    def _meta_text(self) -> Text:
        meta = Text()
        meta.append(f"state={self._issue.state}", style="dim")
        if self._issue.priority:
            meta.append(f"  P{self._issue.priority}", style="bright_red bold")
        if self._issue.labels:
            meta.append("  " + " ".join(f"#{label}" for label in self._issue.labels), style="dim")
        if self._status.attention:
            meta.append("\n")
            _append_attention_meta(meta, self._status.attention, include_due_at=True)
        if self._status.tokens or self._status.input_tokens or self._status.output_tokens:
            meta.append("\n")
            _append_token_meta(meta, self._status, dim=False)
        if self._status.last_message:
            meta.append("\n")
            meta.append(self._status.last_message, style="italic")
        return meta


# (keys, description) rows for the `?` help modal, grouped by purpose. Keep in
# sync with `KanbanApp.BINDINGS` and the README key table.
HELP_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Board",
        (
            ("q", "quit (asks twice while workers run)"),
            ("r", "refresh + re-poll the tracker"),
            ("?", "this help"),
            ("L", "cycle TUI + doc language"),
            ("s", "run statistics"),
            ("/", "filter cards by id / title / label"),
            ("esc", "close filter, reset zoom"),
        ),
    ),
    (
        "Navigate",
        (
            ("tab / shift+tab", "focus next / previous card"),
            ("j / k, ↓ / ↑", "scroll the focused lane"),
            ("g / G, home / end", "jump to top / bottom"),
            ("space / pgdn, b / pgup", "page down / up"),
            ("enter", "open the full-detail modal"),
            ("p", "show / hide the detail pane"),
            ("] / [", "focus the detail pane / back to the board"),
        ),
    ),
    (
        "Layout",
        (
            ("1-9 / 0", "zoom that lane / reset zoom"),
            ("t / T", "next / previous page of lanes"),
            ("+ / -", "more / fewer lanes per page"),
            ("d", "toggle compact / rich cards"),
        ),
    ),
    (
        "Ticket",
        (
            ("n", "new ticket (file board only)"),
            ("e", "edit the focused ticket (file board only)"),
            ("a", "archive the focused Done card"),
            ("c", "confirm the focused Human Review card as Done"),
            ("S", "skip Document for the focused card"),
            ("P", "pause / resume the focused running worker"),
        ),
    ),
)


class HelpScreen(ModalScreen[None]):
    """Every key binding, grouped. Dismiss with Esc, q, or ?."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help-dialog {
        width: 70%;
        max-width: 90;
        height: 85%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #help-dialog #help-title { text-style: bold; color: $accent; }
    #help-dialog #help-meta { color: $text-muted; margin-bottom: 1; }
    #help-dialog VerticalScroll { height: 1fr; }
    """
    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Close")]

    def __init__(self, *, language: str, page: int, total_pages: int) -> None:
        super().__init__()
        self._language = language
        self._page = page
        self._total_pages = total_pages

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Static("Key bindings", id="help-title")
            yield Static(
                f"language={self._language}  lanes page {self._page}/{self._total_pages}",
                id="help-meta",
            )
            with VerticalScroll():
                for title, rows in HELP_SECTIONS:
                    yield Static(self._section_table(title, rows))
            yield Static("[dim]esc / q / ? to close[/dim]")

    @staticmethod
    def _section_table(title: str, rows: tuple[tuple[str, str], ...]) -> Table:
        table = Table(title=title, title_justify="left", expand=True, show_header=False)
        table.add_column("key", style="bold", no_wrap=True, ratio=1)
        table.add_column("action", ratio=3)
        for key, action in rows:
            table.add_row(key, action)
        return table


class NewIssueScreen(ModalScreen[dict[str, Any] | None]):
    """Register a new ticket on the file board. Dismisses with the form
    values (or None on cancel); the app performs the tracker write."""

    DEFAULT_CSS = """
    NewIssueScreen { align: center middle; }
    #new-issue-dialog {
        width: 70%;
        max-width: 100;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #new-issue-dialog Input, #new-issue-dialog Select, #new-issue-dialog TextArea { margin-bottom: 1; }
    #new-issue-dialog TextArea { height: 8; }
    #new-issue-dialog #dialog-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #new-issue-buttons { height: auto; align-horizontal: right; }
    #new-issue-buttons Button { margin-left: 2; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Create", show=False),
    ]

    def __init__(
        self,
        states: list[str],
        agent_kinds: list[str],
    ) -> None:
        super().__init__()
        self._states = states or ["Todo"]
        self._agent_kinds = agent_kinds

    def compose(self) -> ComposeResult:
        with Container(id="new-issue-dialog"):
            yield Static("New issue", id="dialog-title")
            yield Input(placeholder="title (required)", id="ni-title")
            yield TextArea(
                "",
                id="ni-description",
            )
            yield Select(
                [(s, s) for s in self._states],
                value=self._states[0],
                id="ni-state",
            )
            yield Select(
                [("no priority", -1)] + [(f"P{p}", p) for p in range(5)],
                value=-1,
                id="ni-priority",
            )
            yield Select(
                [("default agent", "")] + [(k, k) for k in self._agent_kinds],
                value="",
                id="ni-agent",
            )
            yield Input(placeholder="labels (comma-separated, optional)", id="ni-labels")
            with Horizontal(id="new-issue-buttons"):
                yield Button("Cancel", id="ni-cancel")
                yield Button("Create", variant="primary", id="ni-create")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ni-cancel":
            self.dismiss(None)
            return
        if event.button.id == "ni-create":
            self._submit()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        self._submit()

    def _submit(self) -> None:
        title = self.query_one("#ni-title", Input).value.strip()
        if not title:
            self.notify("title is required", severity="error", timeout=3)
            return
        priority = self.query_one("#ni-priority", Select).value
        state = self.query_one("#ni-state", Select).value
        self.dismiss(
            {
                "title": title,
                "description": self.query_one("#ni-description", TextArea).text.strip(),
                "state": state if isinstance(state, str) else self._states[0],
                "priority": priority if isinstance(priority, int) and priority >= 0 else None,
                "agent_kind": self.query_one("#ni-agent", Select).value or "",
                "labels": _split_csv(self.query_one("#ni-labels", Input).value),
            }
        )


class EditIssueScreen(ModalScreen[dict[str, Any] | None]):
    """Edit a file-board ticket. The app performs the tracker write."""

    DEFAULT_CSS = """
    EditIssueScreen { align: center middle; }
    #edit-issue-dialog {
        width: 75%;
        max-width: 110;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #edit-issue-dialog Input, #edit-issue-dialog Select, #edit-issue-dialog TextArea { margin-bottom: 1; }
    #edit-issue-dialog TextArea { height: 10; }
    #edit-issue-dialog #dialog-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #edit-issue-buttons { height: auto; align-horizontal: right; }
    #edit-issue-buttons Button { margin-left: 2; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Save", show=False),
    ]

    def __init__(
        self,
        issue: Issue,
        states: list[str],
        agent_kinds: list[str],
    ) -> None:
        super().__init__()
        self._issue = issue
        self._states = list(states) or [issue.state or "Todo"]
        # Ticket files may spell a state differently from WORKFLOW.md
        # (`state: done` vs `Done`). Match case-insensitively so a title-only
        # edit never re-queues the ticket to `states[0]`; an unknown state is
        # offered as an extra option so it round-trips untouched.
        self._initial_state = _match_state(issue.state, self._states)
        if self._initial_state is None:
            self._initial_state = issue.state or self._states[0]
            if self._initial_state not in self._states:
                self._states.append(self._initial_state)
        self._agent_kinds = agent_kinds

    def compose(self) -> ComposeResult:
        priority_value = self._issue.priority if self._issue.priority is not None else -1
        with Container(id="edit-issue-dialog"):
            yield Static(f"Edit {self._issue.identifier}", id="dialog-title")
            yield Input(
                value=self._issue.title or "",
                placeholder="title (required)",
                id="ei-title",
            )
            yield TextArea(
                self._issue.description or "",
                id="ei-description",
            )
            yield Select(
                [(s, s) for s in self._states],
                value=self._initial_state,
                id="ei-state",
            )
            yield Select(
                [("no priority", -1)] + [(f"P{p}", p) for p in range(5)],
                value=priority_value,
                id="ei-priority",
            )
            yield Select(
                [("default agent", "")] + [(k, k) for k in self._agent_kinds],
                value=self._issue.agent_kind or "",
                id="ei-agent",
            )
            yield Input(
                value=", ".join(self._issue.labels),
                placeholder="labels (comma-separated, optional)",
                id="ei-labels",
            )
            with Horizontal(id="edit-issue-buttons"):
                yield Button("Cancel", id="ei-cancel")
                yield Button("Save", variant="primary", id="ei-save")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ei-cancel":
            self.dismiss(None)
            return
        if event.button.id == "ei-save":
            self._submit()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        self._submit()

    def _submit(self) -> None:
        title = self.query_one("#ei-title", Input).value.strip()
        if not title:
            self.notify("title is required", severity="error", timeout=3)
            return
        priority = self.query_one("#ei-priority", Select).value
        state = self.query_one("#ei-state", Select).value
        self.dismiss(
            {
                "title": title,
                "description": self.query_one("#ei-description", TextArea).text.strip(),
                "state": state if isinstance(state, str) else self._issue.state,
                "priority": priority if isinstance(priority, int) and priority >= 0 else None,
                "agent_kind": self.query_one("#ei-agent", Select).value or "",
                "labels": _split_csv(self.query_one("#ei-labels", Input).value),
            }
        )


def _match_state(state: str | None, states: list[str]) -> str | None:
    key = normalize_state(state).strip()
    for candidate in states:
        if normalize_state(candidate).strip() == key:
            return candidate
    return None


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


class StatsScreen(ModalScreen[None]):
    """Run statistics (tokens, throughput, per-column dwell). Data comes
    pre-aggregated from `StatsStore.aggregate` — this screen only renders."""

    DEFAULT_CSS = """
    StatsScreen { align: center middle; }
    #stats-dialog {
        width: 90%;
        max-width: 140;
        height: 85%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #stats-dialog #stats-title { text-style: bold; color: $accent; }
    #stats-dialog VerticalScroll { height: 1fr; }
    """
    BINDINGS = [Binding("escape,q,s", "dismiss", "Close")]

    def __init__(self, aggregate: dict[str, Any], state_casing: dict[str, str]) -> None:
        super().__init__()
        self._agg = aggregate
        self._casing = state_casing

    def compose(self) -> ComposeResult:
        agg = self._agg
        totals = agg.get("totals", {})
        cycle = agg.get("cycle", {})
        with Container(id="stats-dialog"):
            yield Static(
                f"Stats — last {agg.get('days', '?')} days", id="stats-title"
            )
            yield Static(
                f"done={totals.get('done', 0)}  runs={totals.get('runs', 0)}  "
                f"turns={totals.get('turns', 0)}  tokens={totals.get('total', 0):,}  "
                f"avg cycle={_fmt_seconds(cycle.get('avg_seconds', 0))} "
                f"({cycle.get('done_tickets', 0)} tickets)"
            )
            with VerticalScroll():
                yield Static(self._state_table())
                yield Static(self._agent_table())
                yield Static(self._day_table())
            yield Static("[dim]esc / q / s to close[/dim]")

    def _display_state(self, state: str) -> str:
        return self._casing.get(state.lower(), state)

    def _state_table(self) -> Table:
        table = Table(title="By column", expand=True)
        for col in ("column", "tokens", "turns", "runs", "avg run", "avg dwell"):
            table.add_column(col)
        for row in self._agg.get("by_state", []):
            table.add_row(
                self._display_state(str(row.get("state", "?"))),
                f"{row.get('total_tokens', 0):,}",
                str(row.get("turns", 0)),
                str(row.get("runs", 0)),
                _fmt_seconds(row.get("avg_run_seconds", 0)),
                _fmt_seconds(row.get("avg_dwell_seconds", 0)),
            )
        return table

    def _agent_table(self) -> Table:
        table = Table(title="By agent", expand=True)
        for col in ("agent", "tokens", "turns", "runs"):
            table.add_column(col)
        for row in self._agg.get("by_agent", []):
            table.add_row(
                str(row.get("agent", "?")),
                f"{row.get('total_tokens', 0):,}",
                str(row.get("turns", 0)),
                str(row.get("runs", 0)),
            )
        return table

    def _day_table(self) -> Table:
        table = Table(title="By day", expand=True)
        for col in ("date", "tokens", "turns", "done"):
            table.add_column(col)
        for row in self._agg.get("by_day", [])[-14:]:
            table.add_row(
                str(row.get("date", "?")),
                f"{row.get('total', 0):,}",
                str(row.get("turns", 0)),
                str(row.get("done", 0)),
            )
        return table


def _fmt_seconds(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "-"
    if seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"
