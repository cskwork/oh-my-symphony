"""Textual-based Kanban TUI for Symphony.

A modern terminal dashboard built on Textual (https://textual.textualize.io).
Replaces the previous hand-rolled Rich `Live` implementation: lanes are real
focusable widgets, cards are first-class, and mouse / keyboard / modals are
handled by the framework. The orchestrator hooks (snapshot polling + observer
push) are unchanged so cli.py can continue to do `await tui.run()`.

Public surface (kept stable for cli.py and tests):

    KanbanTUI(orchestrator, workflow_state, console=None).run() -> awaitable
    KanbanTUI.request_stop()
    _CardStatus, SILENT_THRESHOLD_S, _parse_iso, _silent_seconds
    STATE_COLOR, AGENT_COLOR

Tests stub `symphony.tui.app._fetch_tracker_snapshot` via
`monkeypatch.setattr`; the app resolves its imported consumer reference.
"""

from __future__ import annotations

from .constants import (
    AGENT_COLOR,
    DENSITY_COMPACT,
    DENSITY_RICH,
    LANE_WIDTH_DIM,
    LANE_WIDTH_NORMAL,
    LANE_WIDTH_ZOOMED,
    SILENT_THRESHOLD_S,
    STATE_COLOR,
    _RUNNING_IDS_MAX,
)
from .helpers import (
    _CardStatus,
    _append_token_meta,
    _build_runtime_index,
    _card_sort_key,
    _compact_rate_limits,
    _fetch_candidates,
    _fetch_tracker_snapshot,
    _fetch_terminals,
    _first_meaningful_line,
    _matches_filter,
    _ordered_column_states,
    _parse_iso,
    _safe_id,
    _silent_seconds,
    _truncate,
)
from .screens import (
    EditIssueScreen,
    NewIssueScreen,
    StatsScreen,
    TicketDetailScreen,
    _RefreshNow,
)
from .widgets import DetailPane, FilterBar, IssueCard, Lane, StatsBar
from .app import KanbanApp, KanbanTUI

__all__ = [
    # entry points
    "KanbanApp",
    "KanbanTUI",
    # widgets / screens
    "DetailPane",
    "FilterBar",
    "IssueCard",
    "Lane",
    "StatsBar",
    "EditIssueScreen",
    "NewIssueScreen",
    "StatsScreen",
    "TicketDetailScreen",
    # constants
    "AGENT_COLOR",
    "DENSITY_COMPACT",
    "DENSITY_RICH",
    "LANE_WIDTH_DIM",
    "LANE_WIDTH_NORMAL",
    "LANE_WIDTH_ZOOMED",
    "SILENT_THRESHOLD_S",
    "STATE_COLOR",
    # helpers (re-exported for tests + monkeypatch entry points)
    "_CardStatus",
    "_RefreshNow",
    "_RUNNING_IDS_MAX",
    "_append_token_meta",
    "_build_runtime_index",
    "_card_sort_key",
    "_compact_rate_limits",
    "_fetch_candidates",
    "_fetch_tracker_snapshot",
    "_fetch_terminals",
    "_first_meaningful_line",
    "_matches_filter",
    "_ordered_column_states",
    "_parse_iso",
    "_safe_id",
    "_silent_seconds",
    "_truncate",
]
