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
    assert ".terminal-section" in css
    assert ".terminal-group" in css
    assert ".terminal-card-list" in css
