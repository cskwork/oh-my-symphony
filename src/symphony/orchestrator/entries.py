"""Runtime data structures owned by the Orchestrator.

These dataclasses are pure value types; everything mutating happens
inside `Orchestrator` methods. Keeping them in their own module lets
tests construct `RunningEntry(...)` or `_IssueDebug(...)` directly
without dragging in the whole state machine for a unit assertion.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..backends import AgentBackend

if TYPE_CHECKING:
    from .run_registry import ContinuationCheckpoint
from ..issue import Issue


@dataclass
class RunningEntry:
    issue: Issue
    started_at: datetime
    retry_attempt: int | None
    worker_task: asyncio.Task[None] | None
    workspace_path: Path
    attempt_kind: str = "initial"
    agent_kind: str = ""
    run_id: str = ""
    # A recovered attempt always owns a new run id. The predecessor link is
    # public history; the private checkpoint is consumed only by the backend
    # startup path and never serialized from this in-memory entry.
    continued_from_run_id: str = ""
    continuation_checkpoint: "ContinuationCheckpoint | None" = None
    recovery_session_resumed: bool = False
    # Monotonic application-release identity captured before the worker can
    # mutate board labels.  `release_gate_*` comes from the host-owned SQLite
    # authority row; labels remain routing/audit data only.
    known_app_release: bool = False
    known_release_cycle_verifier: bool = False
    known_app_release_finalizer: bool = False
    release_gate_finalizer: str = ""
    release_gate_expected_contract_sha256: str = ""
    release_gate_cycle_fingerprint: str = ""
    release_gate_generation: str = ""
    release_finalizer_rewind_state: str = ""
    release_gate_exhausted: bool = False
    release_authority_resolved: bool = False
    # Ephemeral proof that this exact verifier run durably created its RED
    # successor lifecycle. It is deliberately not serialized: restart recovery
    # re-proves the handoff from the registry and finalizer link instead.
    release_verifier_handoff_complete: bool = False
    # Live backend driver for this attempt. Populated by `_run_agent_attempt`
    # immediately after `build_backend(...)` so `_on_codex_event` can route
    # the stall-progress predicate through `backend.is_progress_event(...)`
    # without re-implementing per-backend filters inside the orchestrator.
    client: AgentBackend | None = None
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    # Exact backend conversation id used only for authority checkpointing.
    # Unlike `session_id`, it is never decorated with a turn id or serialized.
    resume_session_id: str | None = None
    # Latest local turn for which EVENT_TURN_COMPLETED was observed.
    last_completed_turn_event: int = 0
    turn_count: int = 0
    last_codex_event: str | None = None
    last_codex_message: str = ""
    last_codex_timestamp: datetime | None = None
    # Updated only on events that signify the agent is actually advancing
    # the turn (model output, lifecycle events, token deltas) — NOT on
    # passthrough EVENT_OTHER_MESSAGE for tool_result echoes or stream
    # keepalive. Stall detection reads this; UI keeps last_codex_timestamp
    # to show "any activity at all". See _on_codex_event for the predicate.
    last_progress_timestamp: datetime | None = None
    # Fresh stall-detection floor set by `resume_worker`. A long operator
    # pause is not backend inactivity, so reconcile grants one full stall
    # window after resume without rewriting the progress timestamp.
    resumed_at: datetime | None = None
    codex_input_tokens: int = 0
    codex_cache_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_total_tokens: int = 0
    codex_state_input_tokens: int = 0
    codex_state_cache_input_tokens: int = 0
    codex_state_output_tokens: int = 0
    codex_state_total_tokens: int = 0
    last_reported_input_tokens: int = 0
    last_reported_cache_input_tokens: int = 0
    last_reported_output_tokens: int = 0
    last_reported_total_tokens: int = 0
    # Cumulative state-local total tokens at the close of the previous
    # turn — used by the EMA updater to derive per-turn deltas. Reset to
    # 0 alongside `codex_state_total_tokens` on phase transitions so the
    # EMA samples turn-cost-within-stage, not cross-stage history.
    last_ema_state_total_tokens: int = 0
    # Cumulative run-local counters at the last stats event — the stats
    # recorder derives per-turn deltas from these (never reset mid-run).
    stats_input_tokens: int = 0
    stats_cache_input_tokens: int = 0
    stats_output_tokens: int = 0
    stats_total_tokens: int = 0
    # Cumulative run-local total at the last completed-turn token attention
    # check. Separate from stats so attention still works when stats are off.
    token_attention_total_tokens: int = 0
    # The state the current turn STARTED in. Captured at worker_turn_started
    # so the EMA samples the stage that actually consumed the tokens. Without
    # this, `_update_token_ema` reads `entry.issue.state` at
    # EVENT_TURN_COMPLETED time — but the agent may already have flipped
    # `state:` in the ticket body before the turn ends, so the sample would
    # land under the destination state, not the source. Live claude demo
    # 2026-05-17 reproduced this on the old long pipeline; the same hazard
    # applies to Todo -> In Progress -> Verify transitions.
    state_at_turn_start: str = ""
    agent_pgid: int | None = None
    # A backend stop raised after teardown began. Some drivers become closed
    # before the failing await, so a later idempotent stop cannot confirm that
    # this process group exited and must not erase its force-eject target.
    backend_cleanup_unconfirmed: bool = False
    last_error: str | None = None
    # Set when a lease heartbeat found a conflicting active holder and
    # re-acquisition failed. The worker is being stopped; the flag keeps the
    # per-tick heartbeat from re-attempting acquisition on every tick.
    lease_lost: bool = False
    # Set to `now` the first time stall detection cancels this worker. Used
    # by the next reconcile tick to escalate from "cancel sent" to "force
    # eject" if the worker is stuck on a non-cancellable await.
    cancelled_at: datetime | None = None
    # Set when the worker's own `finally` starts exit cleanup. The task done
    # callback is only a fallback for workers that never reached this point.
    exit_started_at: datetime | None = None
    # First reconcile tick that observed this running ticket in a terminal
    # tracker state. Genuine model progress may extend the cleanup grace;
    # backend keepalives cannot, so chatty CLIs do not hold a slot forever
    # after the board already says the work is terminal.
    terminal_seen_at: datetime | None = None
    # Set when reconcile already snapshotted/removed this workspace after a
    # terminal or inactive tracker move. Worker exit must not repeat that git
    # cleanup against the same worktree.
    workspace_cleanup_started: bool = False
    workspace_cleanup_finished: asyncio.Event = field(
        default_factory=asyncio.Event,
        repr=False,
    )
    # Set when the per-attempt `max_turns` ceiling halted the worker without
    # the ticket having reached a terminal state. Treated as an explicit
    # non-success outcome in `_on_worker_exit`: no automatic continuation is
    # scheduled. The operator must transition the ticket or resume manually.
    hit_max_turns: bool = False
    # Set when completed turns keep returning in the same workflow state.
    # This is cumulative across attempts via `_IssueDebug.state_turn_count`.
    hit_no_stage_change: bool = False
    # Set when the current state's `agent.max_total_tokens` budget is
    # crossed. Worker exit refreshes the ticket: a stage change continues,
    # unchanged state is budget-blocked.
    hit_token_budget: bool = False
    token_budget_cap: int = 0
    # G2 — empty-response loop guard. Counts consecutive `EVENT_TURN_COMPLETED`
    # events whose turn produced no fresh preview text. Reset to 0 when a turn
    # completes with non-empty `current_turn_message`. Crossing the threshold
    # (orchestrator-side constant) is held until the worker refreshes the
    # tracker; a real stage transition clears it, an unchanged state escalates.
    consecutive_empty_turns: int = 0
    hit_empty_response_loop: bool = False
    # Per-turn preview accumulator. Updated alongside `last_codex_message` for
    # any payload that yields preview text; cleared back to "" on every
    # `EVENT_TURN_COMPLETED` after the empty-loop check. `last_codex_message`
    # is sticky for UI continuity; this buffer is what the guard reads.
    current_turn_message: str = ""


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: float
    timer_handle: asyncio.TimerHandle
    error: str | None = None
    kind: str = "retry"
    holds_slot: bool = True
    # `## Touched Files` snapshot of the exited ticket so the C1 conflict
    # pre-check can see retry-pending work (GitHub issue #28).
    touched_files: frozenset[str] = frozenset()


@dataclass
class _CodexTotals:
    input_tokens: int = 0
    cache_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    seconds_running: float = 0.0


# Keep snapshot of recent events for §13.7 issue endpoint.
@dataclass
class _IssueDebug:
    restart_count: int = 0
    current_retry_attempt: int = 0
    current_attempt_kind: str | None = None
    completed_turn_count: int = 0
    rewind_count: int = 0
    # Backends that already hit a quota/usage limit on this ticket.
    quota_exhausted_kinds: set[str] = field(default_factory=set)
    state_turn_state: str = ""
    state_turn_count: int = 0
    last_workspace: Path | None = None
    last_error: str | None = None
    tracker_error: str | None = None
    token_attention: dict[str, str | None] | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)
