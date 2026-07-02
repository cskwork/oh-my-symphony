"""Visual / layout constants for the Symphony Textual TUI.

Pulled out so the same palette and lane sizing can be referenced by
widgets, screens, the main `KanbanApp`, and the test suite without
threading them as function arguments.
"""

from __future__ import annotations

# Visual styling per state — Jira-ish color cues. Kept as a public constant
# so other modules and tests can reuse the palette.
STATE_COLOR = {
    "todo": "bright_black",
    "in progress": "cyan",
    "verify": "yellow",
    "learn": "bright_magenta",
    "human review": "magenta",
    "blocked": "red",
    "review": "yellow",
    "done": "green",
    "archive": "bright_black",
    "cancelled": "magenta",
    "canceled": "magenta",
    "duplicate": "magenta",
    "closed": "green",
}

AGENT_COLOR = {
    "codex": "bright_blue",
    "claude": "bright_magenta",
    "gemini": "bright_yellow",
}


# Threshold above which a running card grows a yellow "silent Ns" badge.
# Tuned to be just past the longest expected pi/claude turn warm-up
# (≈30 s for opus-4 cold start) so the indicator never fires on healthy runs.
SILENT_THRESHOLD_S = 30.0


# Card render densities. Compact = one-line summary; Rich = current 3–6 line layout.
DENSITY_RICH = "rich"
DENSITY_COMPACT = "compact"


# Lane fr widths used by `_apply_lane_widths`. Pulled out as constants so the
# layout is tweakable in one place and so unit tests can assert against the
# named widths instead of magic strings scattered across the file.
LANE_WIDTH_NORMAL = "1fr"
LANE_WIDTH_DIM = "0.4fr"
LANE_WIDTH_ZOOMED = "3fr"


# Header bar must stay one line; cap the in-progress IDs we render and roll the
# rest into a `+N` suffix. Five fits comfortably on an 80-col terminal alongside
# agent / tracker / lang / counts; reduce if those grow.
_RUNNING_IDS_MAX = 5
