"""SPEC §7, §8, §16 — orchestrator package surface.

The package re-exports every name the rest of Symphony (and the test
suite) consumes from ``symphony.orchestrator`` — the same dotted-path
surface the flat ``orchestrator.py`` module used to expose.

One import-order rule matters here: constants, parsing helpers, the
runtime dataclasses, and the pure module-level helpers all import
before ``core`` so ``core`` itself imports them through
``from .helpers import …`` etc.

The former ``_pkg.<name>`` monkeypatch indirection is gone
(initiative D): ``core`` imports ``build_backend``,
``commit_workspace_on_done``, and ``auto_merge_on_done_best_effort``
directly and calls them through its own module globals, so tests patch
``symphony.orchestrator.core.<name>`` — the consumer's reference.
``build_backend`` is additionally constructor-injectable on
``Orchestrator``. The re-exports below stay for the public API surface.
"""

from __future__ import annotations

# Re-exported for the public API surface (no longer a monkeypatch
# contract; core imports these directly).
from ..backends import build_backend
from ..utils.auto_merge import auto_merge_on_done_best_effort
from ..workspace import commit_workspace_on_done

# Re-export everything callers and the test suite reach for.
from .constants import (
    AUTO_TRIAGE_NOTE,
    AUTO_TRIAGE_TARGET_STATE,
    CONTINUATION_RETRY_DELAY_MS,
    PAUSED_RETRY_HOLD_MS,
    RETRY_BASE_MS,
    STALL_FORCE_EJECT_GRACE_S,
)
from .entries import RetryEntry, RunningEntry, _CodexTotals, _IssueDebug
from .helpers import (
    _branch_already_merged_into_target,
    _branch_hook_env,
    _config_for_issue_agent,
    _from_monotonic_to_iso,
    _is_auto_triage_todo_candidate,
    _is_rewind_transition,
    _max_turns_exhausted_target_state,
    _notify_state_transition,
    _requested_agent_kind,
    _sort_for_dispatch_fifo,
    _task_debug,
    _to_iso,
    _utc_iso_z,
)
from .parsing import _parse_findings_rows, _parse_touched_files
from .core import Orchestrator

__all__ = [
    "Orchestrator",
    "RunningEntry",
    "RetryEntry",
    "_CodexTotals",
    "_IssueDebug",
    "AUTO_TRIAGE_NOTE",
    "AUTO_TRIAGE_TARGET_STATE",
    "CONTINUATION_RETRY_DELAY_MS",
    "PAUSED_RETRY_HOLD_MS",
    "RETRY_BASE_MS",
    "STALL_FORCE_EJECT_GRACE_S",
    # parsers / helpers exposed for tests
    "_branch_already_merged_into_target",
    "_branch_hook_env",
    "_config_for_issue_agent",
    "_from_monotonic_to_iso",
    "_is_auto_triage_todo_candidate",
    "_is_rewind_transition",
    "_max_turns_exhausted_target_state",
    "_notify_state_transition",
    "_parse_findings_rows",
    "_parse_touched_files",
    "_requested_agent_kind",
    "_sort_for_dispatch_fifo",
    "_task_debug",
    "_to_iso",
    "_utc_iso_z",
    # collaborators re-exported so test monkeypatches on the package see them
    "auto_merge_on_done_best_effort",
    "build_backend",
    "commit_workspace_on_done",
]
