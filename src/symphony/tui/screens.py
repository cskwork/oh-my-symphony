"""Modal screens + cross-thread messages used by the Kanban TUI.

`_RefreshNow` is the message the orchestrator observer thread posts to
ask the Textual loop for a redraw. `TicketDetailScreen` is the full-screen
modal opened by Enter on a focused card.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static

from ..issue import Issue, normalize_state
from .constants import STATE_COLOR
from .helpers import _CardStatus, _append_token_meta


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
            meta.append("  " + " ".join(f"#{l}" for l in self._issue.labels), style="dim")
        if self._status.tokens or self._status.input_tokens or self._status.output_tokens:
            meta.append("\n")
            _append_token_meta(meta, self._status, dim=False)
        if self._status.last_message:
            meta.append("\n")
            meta.append(self._status.last_message, style="italic")
        return meta
