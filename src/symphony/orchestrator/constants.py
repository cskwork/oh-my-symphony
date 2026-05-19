"""SPEC §7, §8 — orchestrator constants + regex patterns + rewind transitions.

Pulled out so the long-running loops, the retry scheduler, and the per-tick
reconciliation pass can reference the same numbers without those constants
drowning out the state machine code itself.
"""

from __future__ import annotations

import re


AUTO_TRIAGE_TARGET_STATE = "Explore"
AUTO_TRIAGE_NOTE = "Ticket is actionable; routing to Explore."
_AUTO_TRIAGE_ACCEPTANCE_RE = re.compile(r"\bacceptance\s+criteria\b", re.IGNORECASE)
_AUTO_TRIAGE_TRIAGE_RE = re.compile(r"^##\s+Triage\b", re.IGNORECASE | re.MULTILINE)


CONTINUATION_RETRY_DELAY_MS = 1_000  # §7.1
PAUSED_RETRY_HOLD_MS = 5_000  # operator-pause re-park cadence (_on_retry_timer)
RETRY_BASE_MS = 10_000  # §8.4

# Grace window after `worker_task.cancel()` before we forcibly remove the
# entry from `_running`. A worker stuck on a non-cancellable await (e.g. a
# fork that never returns, a DNS lookup, a misbehaving subprocess) would
# otherwise hold its concurrency slot forever and starve the rest of the
# board. The cancel is still issued; this just stops the slot from leaking.
STALL_FORCE_EJECT_GRACE_S = 30.0


# Backward stage transitions that count against the rewind budget.
# `normalize_state` lowercases its input, so compare in lowercase.
_REWIND_TRANSITIONS = frozenset(
    {
        ("review", "in progress"),
        ("qa", "in progress"),
        ("in progress", "plan"),
    }
)


# Adaptive token-budget EMA — C3 (workflow-v0.5.2.md).
# Alpha=0.3 weights recent turns ~70% by the third sample, fast enough to
# track stage-cost drift without single-turn whiplash. Persisted to disk so
# the soft budget survives orchestrator restarts.
_TOKEN_EMA_ALPHA = 0.3

# Section heading patterns parsed out of ticket markdown bodies.
# These are intentionally permissive: leading/trailing whitespace, optional
# trailing colon, and content up to the next `## ` heading or end-of-body.
_TOUCHED_FILES_HEADING_RE = re.compile(
    r"^##\s+Touched\s+Files\s*:?\s*$", re.IGNORECASE | re.MULTILINE
)
_REVIEW_FINDINGS_HEADING_RE = re.compile(
    r"^##\s+Review\s+Findings\s*:?\s*$", re.IGNORECASE | re.MULTILINE
)
_QA_FAILURE_HEADING_RE = re.compile(
    r"^##\s+QA\s+Failure\s*:?\s*$", re.IGNORECASE | re.MULTILINE
)
_NEXT_HEADING_RE = re.compile(r"^##\s+\S", re.MULTILINE)
# Bullet list rows: `- path/to/file.py` (optionally with surrounding backticks).
# Two forms, tried in order:
#   1. `- \`path with spaces/foo.py\` <anything>`  (backticks delimit; spaces OK
#      inside; ANY trailing annotation like `(new)`, `(deleted)`, `— note`
#      after the closing backtick is accepted and ignored)
#   2. `- path/to/file.py <anything>`              (no backticks; first token only)
#
# The lenient trailing-content match is intentional — real agent output
# routinely uses `(new)`, `(deleted)`, `(M)`, `- modified` and similar
# annotations after the path. A strict `$` anchor would silently drop
# real entries from the conflict pre-check (verified live 2026-05-17).
_BULLET_PATH_BACKTICK_RE = re.compile(
    r"^\s*[-*]\s+`(?P<path>[^`]+)`"
)
_BULLET_PATH_PLAIN_RE = re.compile(
    r"^\s*[-*]\s+(?P<path>[^\s`]+)"
)
