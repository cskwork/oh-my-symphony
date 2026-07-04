from __future__ import annotations

from pathlib import Path


STATIC_ROOT = Path("src/symphony/web/static")


def test_web_board_defaults_to_active_lanes_with_terminal_group() -> None:
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "boardScope: 'active'" in js
    assert "function buildBoardScopeToggle()" in js
    assert "function visibleBoardColumns(columns)" in js
    assert "state.boardScope === 'all' ? columns : activeColumns(columns)" in js
    assert "function buildTerminalSectionEl(groups, live, readOnly)" in js
    assert "function buildAttentionBadge(attention)" in js
    assert "getRuns: ({ issue, limit } = {})" in js
    assert "function buildRunHistorySection(detail)" in js
    assert "api.getRuns({ issue: identifier, limit: 10 })" in js
    assert "Run history" in js
    assert "Default agent" in js
    assert "(wf.agent && wf.agent.kind) ||" in js
    assert "function buildMobileLaneTabs(columns)" in js
    assert "function isMobileBoardViewport()" in js
    assert "Review and parked" in js
    assert "'aria-label': 'Terminal states'" in js
    assert ".terminal-section" in css
    assert ".terminal-group" in css
    assert ".terminal-card-list" in css
    assert ".chip-attention" in css
    assert ".drawer-attention" in css
    assert ".drawer-run-history" in css
    assert ".run-history-row" in css
    assert ".mobile-lane-tabs" in css
    assert ".mobile-lane-tab.active" in css
