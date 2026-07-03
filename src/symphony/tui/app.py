"""KanbanApp + KanbanTUI — the main Textual application and its async wrapper.

The App owns:
  - lane / detail-pane / filter-bar / stats-bar composition,
  - tracker poll worker (kicked from `_kick_tracker_refresh`),
  - orchestrator observer wiring (`_on_orchestrator_tick` posts `_RefreshNow`),
  - all key bindings + actions (zoom, pagination, density, filter, archive,
    pause/resume, language toggle, scroll).

`_fetch_candidates` / `_fetch_terminals` are referenced via the parent
package namespace (`_tui_pkg._fetch_candidates`) so that test stubs
applied via `monkeypatch.setattr("symphony.tui._fetch_candidates", ...)`
take effect — a direct `from .helpers import _fetch_candidates` would
bind the function once at import time and ignore the patch.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Iterable

from rich.console import Console
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Input

from ..i18n import SUPPORTED_LANGUAGES, t
from ..issue import Issue, normalize_state
from ..logging import attach_file_handler, get_logger
from ..orchestrator import Orchestrator
from ..trackers import build_tracker_client
from ..workflow import ServiceConfig, WorkflowState
from .constants import (
    DENSITY_COMPACT,
    DENSITY_RICH,
    LANE_WIDTH_DIM,
    LANE_WIDTH_NORMAL,
    LANE_WIDTH_ZOOMED,
    STATE_COLOR,
)
from .helpers import (
    _CardStatus,
    _build_runtime_index,
    _card_sort_key,
    _matches_filter,
    _ordered_column_states,
    _stage_position,
)
from .screens import EditIssueScreen, NewIssueScreen, StatsScreen, _RefreshNow
from .widgets import DetailPane, FilterBar, IssueCard, Lane, StatsBar


log = get_logger()


# Bind to the parent package object so `_tui_pkg._fetch_candidates` does
# attribute lookup at call time — that's how test monkeypatches on
# `symphony.tui._fetch_candidates` reach this code path.
import symphony.tui as _tui_pkg  # noqa: E402  (intentional post-import side effect)


class KanbanApp(App):
    """The Textual application that draws the board."""

    CSS = """
    Screen { background: $background; }
    #main { layout: horizontal; height: 1fr; padding: 0 1; }
    #board { layout: horizontal; height: 1fr; width: 1fr; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("?", "help", "Help"),
        Binding("tab", "focus_next", "Next pane", show=False),
        Binding("shift+tab", "focus_previous", "Prev pane", show=False),
        Binding("j,down", "scroll_down", "Down", show=False),
        Binding("k,up", "scroll_up", "Up", show=False),
        Binding("g,home", "scroll_top", "Top", show=False),
        Binding("G,end", "scroll_bottom", "Bottom", show=False),
        Binding("space,pagedown", "page_down", "Page down", show=False),
        Binding("b,pageup", "page_up", "Page up", show=False),
        Binding("enter", "open_details", "Details"),
        # Iter1: Focus zoom — digits 1..9 zoom that lane to 3fr (others 0.4fr).
        # `0` resets. Escape also resets a zoom (and closes filter if open).
        Binding("1", "zoom_lane(0)", show=False),
        Binding("2", "zoom_lane(1)", show=False),
        Binding("3", "zoom_lane(2)", show=False),
        Binding("4", "zoom_lane(3)", show=False),
        Binding("5", "zoom_lane(4)", show=False),
        Binding("6", "zoom_lane(5)", show=False),
        Binding("7", "zoom_lane(6)", show=False),
        Binding("8", "zoom_lane(7)", show=False),
        Binding("9", "zoom_lane(8)", show=False),
        Binding("0", "reset_zoom", "Reset zoom", show=False),
        # Lane window pagination — show N lanes at a time, page through the rest.
        # `t` advances; `T` (shift+t) goes back. `+` / `-` resize the window.
        Binding("t", "next_page", "Next lanes"),
        Binding("T", "prev_page", "Prev lanes", show=False),
        Binding("plus,equals_sign", "grow_window", "Wider", show=False),
        Binding("minus", "shrink_window", "Narrower", show=False),
        Binding("d", "toggle_density", "Density"),
        Binding("p", "toggle_detail", "Detail pane"),
        # `]` parks focus inside the detail pane so arrow / j / k / pgup / pgdn
        # scroll the description body. `[` returns focus to the board.
        Binding("right_square_bracket", "focus_detail", "Detail focus", show=False),
        Binding("left_square_bracket", "focus_board", "Board focus", show=False),
        Binding("L", "toggle_language", "Language"),
        Binding("a", "archive_focused", "Archive"),
        Binding("c", "confirm_done_focused", "Confirm done"),
        Binding("S", "skip_learn_focused", "Skip Learn"),
        Binding("P", "toggle_pause_focused", "Pause/resume"),
        Binding("n", "new_issue", "New issue"),
        Binding("e", "edit_focused", "Edit issue"),
        Binding("s", "stats", "Stats"),
        Binding("slash", "open_filter", "Filter"),
        Binding("escape", "escape", "Close filter / zoom", show=False),
    ]

    def __init__(
        self,
        orchestrator: Orchestrator,
        workflow_state: WorkflowState,
    ) -> None:
        super().__init__()
        self._orch = orchestrator
        self._ws = workflow_state
        self._candidates: list[Issue] = []
        self._terminal_issues: list[Issue] = []
        self._lanes: dict[str, Lane] = {}
        # Lane keys in the order they were composed. Used by digit zoom — index
        # `i` lights up `_lane_order[i]`.
        self._lane_order: list[str] = []
        # Set of normalized terminal state keys (Done, Closed, Cancelled, ...).
        self._terminal_keys: set[str] = set()
        self._tracker_lock = asyncio.Lock()
        # UX state.
        self._zoomed_lane: str | None = None
        # Compact density default — one-line cards keep many lanes scannable
        # at once. Press `d` to flip to the multi-line rich layout.
        self._density: str = DENSITY_COMPACT
        # Detail pane is default-on so the focused card always has a place
        # to spread out — keeps each lane card terse without losing detail.
        self._detail_visible: bool = True
        self._filter_query: str = ""
        # Lane window pagination — show `_window_size` consecutive lanes
        # starting at index `_window_start`. `t` advances by a full page,
        # `+`/`-` adjust the window size at runtime. Initial size comes from
        # `tui.visible_lanes` in WORKFLOW.md (default 5).
        cfg = self._ws.current()
        self._window_size: int = cfg.tui.visible_lanes if cfg else 5
        self._window_start: int = 0
        # Cache the last-rendered focused card so we don't re-render the
        # detail pane every 0.5 s heartbeat unless focus actually moved.
        self._last_focused_card_id: str | None = None
        # In-session language override. None = follow `tui.language` from the
        # WORKFLOW.md config; set by `L` to flip chrome locale without
        # restarting the TUI. Reset on relaunch — persistence belongs in
        # WORKFLOW.md / SYMPHONY_LANG, not in TUI state.
        self._language_override: str | None = None

    # ----- composition -------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatsBar(id="stats")
        cfg = self._ws.current()
        terminal_states = set(cfg.tracker.terminal_states) if cfg else set()
        self._terminal_keys = {normalize_state(s) for s in terminal_states}
        with Container(id="main"):
            with Container(id="board"):
                ordered = _ordered_column_states(cfg) if cfg else []
                descriptions = cfg.tracker.state_descriptions if cfg else {}
                for state_label in ordered:
                    key = normalize_state(state_label)
                    color = STATE_COLOR.get(key, "white")
                    lane = Lane(
                        state_label,
                        color,
                        descriptions.get(key),
                        stage_pos=_stage_position(state_label, cfg),
                    )
                    if key in self._terminal_keys:
                        lane.add_class("-terminal")
                    self._lanes[key] = lane
                    self._lane_order.append(key)
                    yield lane
            pane = DetailPane()
            # Detail pane is on by default — toggle with `p`.
            pane.set_visible(self._detail_visible)
            yield pane
        yield FilterBar()
        yield Footer()

    # ----- lifecycle ---------------------------------------------------

    def on_mount(self) -> None:
        self.title = "oh-my-symphony"
        cfg = self._ws.current()
        if cfg is not None:
            self.sub_title = f"{cfg.agent.kind} · {cfg.tracker.kind}"
        # Hook orchestrator events. Observer is invoked from the orchestrator
        # task; bouncing through call_from_thread keeps widget updates on the
        # Textual event loop.
        self._orch.add_observer(self._on_orchestrator_tick)
        # Redraw heartbeat — picks up "silent N s" tick-overs without
        # waiting on orchestrator events.
        self.set_interval(0.5, self._refresh_runtime)
        # Tracker poll. Runs in a thread worker so a slow Linear API doesn't
        # stall the UI.
        cfg = self._ws.current()
        poll_s = max(5.0, (cfg.poll_interval_ms / 1000.0) if cfg else 30.0)
        self.set_interval(poll_s, self._kick_tracker_refresh)
        self._kick_tracker_refresh()  # prime
        self._refresh_runtime()

    async def _on_orchestrator_tick(self) -> None:
        # Called from the orchestrator's asyncio task. Posting a message keeps
        # us on Textual's loop without race conditions on the widget tree.
        try:
            self.post_message(_RefreshNow())
        except Exception:  # app may already be shutting down
            log.debug("tui_post_refresh_failed")

    def on__refresh_now(self, message: _RefreshNow) -> None:  # noqa: N802 (Textual naming)
        del message
        self._refresh_runtime()

    # ----- data refresh ------------------------------------------------

    def _kick_tracker_refresh(self) -> None:
        cfg = self._ws.current()
        if cfg is None:
            return
        # Thread worker: tracker clients use blocking httpx.
        self.run_worker(self._refresh_tracker(cfg), thread=False, exclusive=True, group="tracker")

    async def _refresh_tracker(self, cfg: ServiceConfig) -> None:
        try:
            candidates = await asyncio.to_thread(_tui_pkg._fetch_candidates, cfg)
            terminals = await asyncio.to_thread(_tui_pkg._fetch_terminals, cfg)
        except Exception as exc:
            log.debug("tui_tracker_fetch_failed", error=str(exc))
            return
        async with self._tracker_lock:
            self._candidates = candidates
            self._terminal_issues = terminals
        self._refresh_runtime()

    def _refresh_runtime(self) -> None:
        cfg = self._ws.current()
        if cfg is None:
            return
        snapshot = self._orch.snapshot()
        try:
            stats = self.query_one(StatsBar)
        except (NoMatches, ScreenStackError):
            return
        stats.update_from(
            cfg, snapshot, language=self._effective_language()
        )
        runtime_index = _build_runtime_index(snapshot)
        issues_by_state: dict[str, list[Issue]] = {k: [] for k in self._lanes}
        for issue in self._all_known_issues():
            key = normalize_state(issue.state)
            if key in issues_by_state:
                issues_by_state[key].append(issue)
        # Apply substring filter — empty query is a no-op so the hot path stays
        # identical to the unfiltered branch.
        if self._filter_query:
            q = self._filter_query
            for key in list(issues_by_state.keys()):
                issues_by_state[key] = [
                    i for i in issues_by_state[key] if _matches_filter(i, q)
                ]
        language = self._effective_language()
        empty_text = t("column.empty", language)
        for key, lane in self._lanes.items():
            issues = sorted(issues_by_state.get(key, []), key=_card_sort_key)
            lane.set_count(len(issues))
            cards = [
                (issue, self._card_status_for_issue(issue, runtime_index))
                for issue in issues
            ]
            lane.render_cards(
                cards, empty_text, language, density=self._density
            )
        # Lane widths depend on counts (empty → dim) and on user state
        # (zoom / show_terminals), so re-apply after counts settle.
        self._apply_lane_widths()
        self._refresh_detail_pane()

    def _all_known_issues(self) -> Iterable[Issue]:
        seen: set[str] = set()
        for source in (
            self._candidates,
            self._terminal_issues,
            list(self._orch.iter_running_issues()),
        ):
            for issue in source:
                if issue.id in seen:
                    continue
                seen.add(issue.id)
                yield issue

    def _card_status_for_issue(
        self, issue: Issue, runtime_index: dict[str, _CardStatus]
    ) -> _CardStatus:
        status = runtime_index.get(issue.id, _CardStatus())
        if status.attention is not None:
            return status
        attention_fn = getattr(self._orch, "issue_attention", None)
        if not callable(attention_fn):
            return status
        try:
            attention = attention_fn(issue)
        except Exception as exc:
            log.debug(
                "tui_issue_attention_failed",
                identifier=issue.identifier,
                error=str(exc),
            )
            return status
        if not attention:
            return status
        return replace(status, attention=attention)

    # ----- actions -----------------------------------------------------

    def action_refresh(self) -> None:
        self._kick_tracker_refresh()
        self._refresh_runtime()
        self.notify("refreshed")

    def action_help(self) -> None:
        lang = self._effective_language()
        page = self._current_page_index() + 1
        total_pages = self._page_count()
        msg = (
            "q quit · r refresh · enter details · "
            "1-9 zoom lane · 0/esc reset · "
            f"t/T page lanes ({page}/{total_pages}) · +/- resize window · "
            "d density · p detail-pane · ]/[ focus detail/board · "
            "L language · a archive · c confirm done · S skip Learn · "
            "P pause/resume · n new · e edit · / filter · "
            "tab focus · j/k scroll · g/G top/bottom · "
            f"lang={lang}"
        )
        self.notify(msg, timeout=8)

    def action_open_details(self) -> None:
        focused = self.focused
        if isinstance(focused, IssueCard):
            focused.open_details()

    # ----- Iter1: focus zoom + empty lane collapse ---------------------

    def action_zoom_lane(self, idx: int) -> None:
        # `idx` is 0-based within the *current* window — pressing `1` always
        # zooms the leftmost visible lane regardless of pagination.
        window = sorted(self._window_indices())
        if idx < 0 or idx >= len(window):
            return
        target = self._lane_order[window[idx]]
        if self._zoomed_lane == target:
            self._zoomed_lane = None
        else:
            self._zoomed_lane = target
        self._apply_lane_widths()

    def action_reset_zoom(self) -> None:
        if self._zoomed_lane is None:
            return
        self._zoomed_lane = None
        self._apply_lane_widths()

    def _apply_lane_widths(self) -> None:
        """Single source of truth for lane sizing + visibility.

        Priority order (top wins):
            1. Outside the current window → `display: none` (paged off-screen).
            2. Zoom — the zoomed lane gets `LANE_WIDTH_ZOOMED`, others dim.
            3. Empty lane — narrow + `.-empty` class for muted styling.
            4. Terminal lane — narrow (Done/Closed are reference, not workspace).
            5. Default — `LANE_WIDTH_NORMAL`.
        """
        window = self._window_indices()
        for idx, lane_key in enumerate(self._lane_order):
            lane = self._lanes[lane_key]
            is_terminal = lane_key in self._terminal_keys
            lane.remove_class("-zoomed")
            lane.remove_class("-empty")

            if idx not in window:
                # Paged off-screen — hide entirely so visible lanes get the
                # full width allocation.
                lane.display = False
                continue
            lane.display = True

            if self._zoomed_lane is not None:
                if lane_key == self._zoomed_lane:
                    lane.styles.width = LANE_WIDTH_ZOOMED
                    lane.add_class("-zoomed")
                else:
                    lane.styles.width = LANE_WIDTH_DIM
                continue

            if lane.is_empty:
                lane.styles.width = LANE_WIDTH_DIM
                lane.add_class("-empty")
            elif is_terminal:
                lane.styles.width = LANE_WIDTH_DIM
            else:
                lane.styles.width = LANE_WIDTH_NORMAL

    def _window_indices(self) -> set[int]:
        """Indices of lanes currently visible in the lane window.

        Honors partial trailing pages — if `total=8, size=5`, page 1 shows
        indices {5, 6, 7} (3 lanes), not {3, 4, 5, 6, 7}. Snapping back to
        a full window would force the user to re-see lanes they already saw.
        """
        total = len(self._lane_order)
        if total == 0:
            return set()
        size = max(1, self._window_size)
        if self._window_start < 0 or self._window_start >= total:
            # Wrapped or invalidated → reset to the start.
            self._window_start = 0
        end = min(total, self._window_start + size)
        return set(range(self._window_start, end))

    def _page_count(self) -> int:
        total = len(self._lane_order)
        if total == 0:
            return 1
        size = max(1, self._window_size)
        # Ceil division — a partial last page still counts as a page.
        return (total + size - 1) // size

    def _current_page_index(self) -> int:
        size = max(1, self._window_size)
        return self._window_start // size

    # ----- lane window pagination -------------------------------------

    def action_next_page(self) -> None:
        """Slide the lane window forward one full page (wraps to 0)."""
        total = len(self._lane_order)
        if total == 0:
            return
        size = max(1, self._window_size)
        next_start = self._window_start + size
        if next_start >= total:
            next_start = 0
        self._window_start = next_start
        # Zoom is bound to a specific lane; if that lane just paged off,
        # clear zoom so we don't show "lane is zoomed but invisible" state.
        if self._zoomed_lane is not None and self._lanes[self._zoomed_lane].display is False:
            self._zoomed_lane = None
        self._apply_lane_widths()
        self._notify_page()

    def action_prev_page(self) -> None:
        total = len(self._lane_order)
        if total == 0:
            return
        size = max(1, self._window_size)
        if self._window_start == 0:
            # Wrap to the last page on a page-aligned boundary.
            last_page_start = ((total - 1) // size) * size
            self._window_start = max(0, last_page_start)
        else:
            self._window_start = max(0, self._window_start - size)
        if self._zoomed_lane is not None and self._lanes[self._zoomed_lane].display is False:
            self._zoomed_lane = None
        self._apply_lane_widths()
        self._notify_page()

    def action_grow_window(self) -> None:
        if self._window_size >= len(self._lane_order):
            return
        self._window_size += 1
        self._apply_lane_widths()
        self._notify_page(prefix="window")

    def action_shrink_window(self) -> None:
        if self._window_size <= 1:
            return
        self._window_size -= 1
        self._apply_lane_widths()
        self._notify_page(prefix="window")

    def _notify_page(self, *, prefix: str = "page") -> None:
        page = self._current_page_index() + 1
        total_pages = self._page_count()
        size = self._window_size
        self.notify(
            f"{prefix} {page}/{total_pages}  ({size} lanes/page)",
            timeout=2,
        )

    # ----- card density -----------------------------------------------

    def action_toggle_density(self) -> None:
        self._density = (
            DENSITY_COMPACT if self._density == DENSITY_RICH else DENSITY_RICH
        )
        # Cards re-render through the next _refresh_runtime tick; trigger one
        # immediately so the keystroke feels instant.
        self._refresh_runtime()
        self.notify(f"density: {self._density}", timeout=2)

    def _effective_language(self) -> str:
        """Resolve the language used for chrome rendering this frame.

        In-session override (`L`) wins over `tui.language` from WORKFLOW.md
        so the toggle feels instant without rewriting config.
        """
        if self._language_override is not None:
            return self._language_override
        cfg = self._ws.current()
        return cfg.tui.language if cfg else "en"

    def action_toggle_language(self) -> None:
        current = self._effective_language()
        try:
            idx = SUPPORTED_LANGUAGES.index(current)
        except ValueError:
            idx = -1
        self._language_override = SUPPORTED_LANGUAGES[
            (idx + 1) % len(SUPPORTED_LANGUAGES)
        ]
        self._refresh_runtime()
        self.notify(f"language: {self._language_override}", timeout=2)

    def action_archive_focused(self) -> None:
        """Move the focused card to the configured archive state.

        Only fires for Done cards. Human Review is terminal too, but it
        requires explicit confirmation before it can become archiveable.
        """
        focused = self.focused
        if not isinstance(focused, IssueCard):
            self.notify("focus a card first", timeout=2)
            return
        cfg = self._ws.current()
        if cfg is None:
            return
        archive_key = normalize_state(cfg.tracker.archive_state)
        issue = focused.issue
        state_key = normalize_state(issue.state)
        if state_key != "done":
            self.notify(
                f"only Done cards can be archived (state={issue.state})",
                timeout=3,
            )
            return
        if state_key == archive_key:
            self.notify("already archived", timeout=2)
            return
        # Tracker mutation is blocking httpx / file IO — punt to a worker
        # so the keystroke stays responsive. After the call lands, kick a
        # tracker refresh so the lane re-paints.
        self.run_worker(
            self._archive_issue(cfg, issue),
            thread=False,
            exclusive=False,
            group="archive",
        )

    async def _archive_issue(self, cfg: ServiceConfig, issue: Issue) -> None:
        target = cfg.tracker.archive_state
        try:
            await asyncio.to_thread(self._call_update_state, cfg, issue, target)
        except Exception as exc:
            log.warning(
                "tui_archive_failed", identifier=issue.identifier, error=str(exc)
            )
            self.notify(f"archive failed: {exc}", timeout=4, severity="error")
            return
        self.notify(f"archived {issue.identifier}", timeout=2)
        self._kick_tracker_refresh()

    def action_confirm_done_focused(self) -> None:
        """Move a Human Review card to Done after operator approval."""
        focused = self.focused
        if not isinstance(focused, IssueCard):
            self.notify("focus a card first", timeout=2)
            return
        cfg = self._ws.current()
        if cfg is None:
            return
        issue = focused.issue
        if normalize_state(issue.state) != "human review":
            self.notify(
                f"only Human Review cards can be confirmed (state={issue.state})",
                timeout=3,
            )
            return
        self.run_worker(
            self._confirm_done_issue(cfg, issue),
            thread=False,
            exclusive=False,
            group="confirm_done",
        )

    async def _confirm_done_issue(self, cfg: ServiceConfig, issue: Issue) -> None:
        try:
            await asyncio.to_thread(self._call_update_state, cfg, issue, "Done")
        except Exception as exc:
            log.warning(
                "tui_confirm_done_failed", identifier=issue.identifier, error=str(exc)
            )
            self.notify(f"confirm failed: {exc}", timeout=4, severity="error")
            return
        self.notify(f"confirmed {issue.identifier} as Done", timeout=2)
        self._kick_tracker_refresh()

    def action_skip_learn_focused(self) -> None:
        """Move a Learn card to Human Review without running Learn."""
        focused = self.focused
        if not isinstance(focused, IssueCard):
            self.notify("focus a card first", timeout=2)
            return
        issue = focused.issue
        if normalize_state(issue.state) != "learn":
            self.notify(
                f"only Learn cards can be skipped (state={issue.state})",
                timeout=3,
            )
            return
        self.run_worker(
            self._skip_learn_issue(issue),
            thread=False,
            exclusive=False,
            group="skip_learn",
        )

    async def _skip_learn_issue(self, issue: Issue) -> None:
        try:
            changed, message = await self._orch.skip_learn(issue.identifier)
        except Exception as exc:
            log.warning(
                "tui_skip_learn_failed", identifier=issue.identifier, error=str(exc)
            )
            self.notify(f"skip failed: {exc}", timeout=4, severity="error")
            return
        if not changed:
            self.notify(message, timeout=4, severity="warning")
            return
        self.notify(message, timeout=2)
        self._kick_tracker_refresh()

    @staticmethod
    def _call_update_state(
        cfg: ServiceConfig, issue: Issue, target_state: str
    ) -> None:
        client = build_tracker_client(cfg)
        try:
            client.update_state(issue, target_state)
        finally:
            client.close()

    # ----- new issue ('n') + stats ('s') --------------------------------

    def action_new_issue(self) -> None:
        """Register a new ticket on the file board via a modal form."""
        cfg = self._ws.current()
        if cfg is None:
            return
        if cfg.tracker.kind != "file":
            self.notify(
                "issue creation from the TUI requires tracker.kind: file",
                timeout=3,
            )
            return
        # Skill discovery reads SKILL.md files — do it off the UI loop,
        # then open the modal once the list is in hand.
        self.run_worker(
            self._open_new_issue(cfg), thread=False, exclusive=True, group="new_issue"
        )

    async def _open_new_issue(self, cfg: ServiceConfig) -> None:
        from ..workflow import SUPPORTED_AGENT_KINDS

        def _on_result(form: dict[str, Any] | None) -> None:
            if form:
                self.run_worker(
                    self._create_issue(cfg, form),
                    thread=False,
                    exclusive=False,
                    group="create_issue",
                )

        self.push_screen(
            NewIssueScreen(
                states=list(cfg.tracker.active_states),
                agent_kinds=sorted(SUPPORTED_AGENT_KINDS),
            ),
            _on_result,
        )

    async def _create_issue(self, cfg: ServiceConfig, form: dict[str, Any]) -> None:
        from ..stats import stats_store_for
        from ..trackers.file import FileBoardTracker

        def _create() -> str:
            tracker = FileBoardTracker(cfg.tracker)
            identifier, _ = tracker.create_with_next_identifier(
                "TASK",
                title=form["title"],
                state=form["state"],
                priority=form["priority"],
                labels=form["labels"],
                description=form["description"],
                agent_kind=form["agent_kind"] or None,
            )
            stats_store_for(
                cfg.workflow_path.parent / ".symphony" / "stats.jsonl"
            ).record_transition(
                issue=identifier, from_state="", to_state=form["state"].lower()
            )
            return identifier

        try:
            identifier = await asyncio.to_thread(_create)
        except Exception as exc:
            log.warning("tui_create_issue_failed", error=str(exc))
            self.notify(f"create failed: {exc}", timeout=4, severity="error")
            return
        self.notify(f"created {identifier}", timeout=2)
        self._kick_tracker_refresh()

    def action_edit_focused(self) -> None:
        """Edit the focused file-board ticket in a modal."""
        focused = self.focused
        if not isinstance(focused, IssueCard):
            self.notify("focus a card first", timeout=2)
            return
        cfg = self._ws.current()
        if cfg is None:
            return
        if cfg.tracker.kind != "file":
            self.notify(
                "issue editing from the TUI requires tracker.kind: file",
                timeout=3,
            )
            return
        issue = focused.issue
        if self._orch.find_running_issue_id(issue.identifier) is not None:
            self.notify(
                f"{issue.identifier} is running; wait before editing",
                timeout=3,
                severity="warning",
            )
            return

        def _on_result(form: dict[str, Any] | None) -> None:
            if form:
                self.run_worker(
                    self._update_issue(cfg, issue, form),
                    thread=False,
                    exclusive=False,
                    group="edit_issue",
                )

        from ..workflow import SUPPORTED_AGENT_KINDS

        self.push_screen(
            EditIssueScreen(
                issue,
                states=list(cfg.tracker.active_states) + list(cfg.tracker.terminal_states),
                agent_kinds=sorted(SUPPORTED_AGENT_KINDS),
            ),
            _on_result,
        )

    async def _update_issue(
        self, cfg: ServiceConfig, issue: Issue, form: dict[str, Any]
    ) -> None:
        from ..stats import stats_store_for
        from ..trackers.file import FileBoardTracker

        def _update() -> None:
            tracker = FileBoardTracker(cfg.tracker)
            tracker.update_fields(
                issue.identifier,
                title=form["title"],
                description=form["description"],
                state=form["state"],
                priority=form["priority"],
                clear_priority=form["priority"] is None,
                labels=form["labels"],
                agent_kind=form["agent_kind"],
            )
            if normalize_state(form["state"]) != normalize_state(issue.state):
                stats_store_for(
                    cfg.workflow_path.parent / ".symphony" / "stats.jsonl"
                ).record_transition(
                    issue=issue.identifier,
                    from_state=issue.state.lower(),
                    to_state=form["state"].lower(),
                )

        try:
            await asyncio.to_thread(_update)
        except Exception as exc:
            log.warning(
                "tui_update_issue_failed", identifier=issue.identifier, error=str(exc)
            )
            self.notify(f"edit failed: {exc}", timeout=4, severity="error")
            return
        self.notify(f"updated {issue.identifier}", timeout=2)
        self._kick_tracker_refresh()

    def action_stats(self) -> None:
        cfg = self._ws.current()
        if cfg is None:
            return
        self.run_worker(
            self._open_stats(cfg), thread=False, exclusive=True, group="stats"
        )

    async def _open_stats(self, cfg: ServiceConfig) -> None:
        from ..stats import stats_store_for

        store = getattr(self._orch, "stats", None) or stats_store_for(
            cfg.workflow_path.parent / ".symphony" / "stats.jsonl"
        )
        terminal = {s.lower() for s in cfg.tracker.terminal_states}
        done_states = {"done"} if "done" in terminal else terminal
        aggregate = await asyncio.to_thread(store.aggregate, 30, done_states)
        casing = {
            s.lower(): s
            for s in (*cfg.tracker.active_states, *cfg.tracker.terminal_states)
        }
        self.push_screen(StatsScreen(aggregate, casing))

    # ----- pause / resume ---------------------------------------------

    def action_toggle_pause_focused(self) -> None:
        """Hold or release the worker behind the focused card.

        The pause is queued — the in-flight turn (if any) is allowed to
        finish so the model isn't aborted mid-thought. Pause is only
        offered for currently running cards; resume is offered for any
        paused card (running OR retrying) because pause now persists
        across worker exit and a held ticket may have moved into the
        retry queue.
        """
        focused = self.focused
        if not isinstance(focused, IssueCard):
            self.notify("focus a card first", timeout=2)
            return
        issue_id = focused.issue.id
        if self._orch.is_paused(issue_id):
            if self._orch.resume_worker(issue_id):
                self.notify(f"resumed {focused.issue.identifier}", timeout=2)
            else:
                self.notify("resume had no effect", timeout=2)
        else:
            if focused.status.runtime != "running":
                self.notify(
                    f"only running workers can be paused (runtime={focused.status.runtime})",
                    timeout=3,
                )
                return
            if self._orch.pause_worker(issue_id):
                self.notify(
                    f"paused {focused.issue.identifier} (after current turn)",
                    timeout=3,
                )
            else:
                self.notify("pause had no effect", timeout=2)
        # Snapshot polling drives the visual update on the next tick; force
        # an immediate redraw so the keystroke feels responsive.
        self._refresh_runtime()

    # ----- Iter3: detail pane + filter --------------------------------

    def action_toggle_detail(self) -> None:
        self._detail_visible = not self._detail_visible
        pane = self.query_one(DetailPane)
        pane.set_visible(self._detail_visible)
        self._last_focused_card_id = None  # force a refresh
        self._refresh_detail_pane()

    def _refresh_detail_pane(self) -> None:
        if not self._detail_visible:
            return
        try:
            pane = self.query_one(DetailPane)
        except Exception:
            return
        focused = self.focused
        if isinstance(focused, IssueCard):
            # Live runtime fields (turn count, tokens, last_message) keep
            # changing each tick, so we re-render even when the same card
            # is still focused.
            self._last_focused_card_id = focused.id
            pane.show_for(focused.issue, focused.status, focused.stage_pos)
            return
        # Focus may have shifted INTO the pane itself (user pressed `]` to
        # scroll the description). Keep showing the previously focused card
        # — otherwise the pane would blank out the moment the user sat down
        # in it. Live runtime fields still update by re-resolving the card.
        if (
            focused is not None
            and self._last_focused_card_id is not None
            and self._is_within_detail_pane(focused)
        ):
            card = self._find_card_by_id(self._last_focused_card_id)
            if card is not None:
                pane.show_for(card.issue, card.status, card.stage_pos)
            return
        if self._last_focused_card_id is not None:
            self._last_focused_card_id = None
            pane.show_placeholder()

    @staticmethod
    def _is_within_detail_pane(node: Any) -> bool:
        cur: Any = node
        while cur is not None:
            if isinstance(cur, DetailPane):
                return True
            cur = getattr(cur, "parent", None)
        return False

    def _find_card_by_id(self, card_id: str) -> IssueCard | None:
        try:
            return self.query_one(f"#{card_id}", IssueCard)
        except Exception:
            return None

    def action_focus_detail(self) -> None:
        """Park focus inside the detail pane so arrow keys scroll its body."""
        if not self._detail_visible:
            self.notify("detail pane is hidden — press p to show", timeout=2)
            return
        try:
            pane = self.query_one(DetailPane)
        except Exception:
            return
        pane.scroll.focus()

    def action_focus_board(self) -> None:
        """Return focus to the first card in the first non-empty visible lane."""
        for lane in self._lanes.values():
            if not lane.display or lane.is_empty:
                continue
            for card in lane.query(IssueCard):
                card.focus()
                return
            lane.focus()
            return

    def action_open_filter(self) -> None:
        bar = self.query_one(FilterBar)
        bar.set_visible(True)
        try:
            inp = bar.query_one("#filter-input", Input)
        except Exception:
            return
        inp.focus()

    def action_escape(self) -> None:
        # Esc cascades — if filter is open, close it; else if zoomed, unzoom.
        bar = self.query_one(FilterBar)
        if bar.is_open:
            self._close_filter()
            return
        if self._zoomed_lane is not None:
            self._zoomed_lane = None
            self._apply_lane_widths()

    def _close_filter(self) -> None:
        bar = self.query_one(FilterBar)
        try:
            inp = bar.query_one("#filter-input", Input)
            inp.value = ""
        except Exception:
            pass
        bar.set_visible(False)
        self._filter_query = ""
        self._refresh_runtime()
        # Move focus back to the first non-empty visible lane so j/k still work.
        for lane in self._lanes.values():
            if lane.display and not lane.is_empty:
                lane.focus()
                break

    def on_input_changed(self, event: Input.Changed) -> None:
        if getattr(event.input, "id", "") != "filter-input":
            return
        self._filter_query = (event.value or "").strip().lower()
        self._refresh_runtime()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if getattr(event.input, "id", "") != "filter-input":
            return
        # Enter: keep filter active, but move focus back to the board so
        # arrow keys / digits work again.
        for lane in self._lanes.values():
            if lane.display and not lane.is_empty:
                lane.focus()
                break

    def on_descendant_focus(self, event: Any) -> None:
        # Whenever focus lands on a different IssueCard, update the detail
        # pane. Cheaper than polling self.focused on every heartbeat tick.
        del event
        self._refresh_detail_pane()

    def action_scroll_down(self) -> None:
        self._scroll_focused(1)

    def action_scroll_up(self) -> None:
        self._scroll_focused(-1)

    def action_page_down(self) -> None:
        self._scroll_focused(10)

    def action_page_up(self) -> None:
        self._scroll_focused(-10)

    def action_scroll_top(self) -> None:
        scroll = self._focused_scroll()
        if scroll is not None:
            scroll.scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        scroll = self._focused_scroll()
        if scroll is not None:
            scroll.scroll_end(animate=False)

    def _focused_scroll(self) -> VerticalScroll | None:
        node = self.focused
        while node is not None:
            if isinstance(node, VerticalScroll):
                return node
            node = node.parent  # type: ignore[assignment]
        # Fall back to the first lane's scroll so j/k still works without focus.
        for lane in self._lanes.values():
            try:
                return lane.query_one(VerticalScroll)
            except Exception:
                continue
        return None

    def _scroll_focused(self, delta: int) -> None:
        scroll = self._focused_scroll()
        if scroll is None:
            return
        scroll.scroll_relative(y=delta, animate=False)


class KanbanTUI:
    """Async wrapper around `KanbanApp` so cli.py can keep `await tui.run()`.

    The legacy implementation accepted a `console` kwarg for unit-test
    rendering; the Textual app manages its own renderer so the argument is
    accepted and ignored. A `_KanbanTUI` instance is single-use — call
    `run()` once.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        workflow_state: WorkflowState,
        *,
        console: Console | None = None,
    ) -> None:
        self._orch = orchestrator
        self._ws = workflow_state
        self._console = console  # accepted for API compat; not used here
        self._app: KanbanApp | None = None

    async def run(self) -> None:
        self._attach_file_log_sink()
        self._app = KanbanApp(self._orch, self._ws)
        try:
            await self._app.run_async()
        except asyncio.CancelledError:
            self.request_stop()
            raise

    def _attach_file_log_sink(self) -> None:
        """G4 — mirror the headless service's `log/symphony.log` sink so
        TUI sessions also produce a structured log. `SYMPHONY_LOG_FILE`
        overrides the default for tests / non-standard layouts. Failures
        are swallowed: a broken log sink must not block TUI startup.
        """
        import os
        from pathlib import Path
        try:
            override = os.environ.get("SYMPHONY_LOG_FILE")
            if override:
                log_path = Path(override)
            else:
                cfg = self._ws.current()
                workflow_dir = cfg.workflow_path.parent if cfg is not None else None
                if workflow_dir is None:
                    return
                log_path = workflow_dir / "log" / "symphony.log"
            attach_file_handler(get_logger(), log_path)
        except Exception:
            pass

    def request_stop(self) -> None:
        if self._app is not None:
            try:
                self._app.exit()
            except Exception:  # already exiting
                pass
