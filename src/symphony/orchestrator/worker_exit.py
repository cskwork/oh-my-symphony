"""SPEC §16.6 — worker-exit state machine, extracted from ``core.Orchestrator``.

``handle_worker_exit`` is the body that used to live inline in
``Orchestrator._on_worker_exit_impl``. It runs these phases in order:

1. pop      — ``_pop_running_entry``: AF-01 identity gate, pop the entry from
              ``_running``, release the per-worker transition lock / wait-age
              bonus / pause event, log ``worker_exit_entered`` and
              ``worker_exit_pop``.
2. account  — ``_record_exit_accounting``: elapsed seconds, run totals, the
              per-issue ``_IssueDebug`` record, and the stats store.
3. classify — branch on ``reason``:
   * ``"normal"``               → ``_handle_normal_exit``: release-finalizer
                                  guard, verifier hand-off, token / stage /
                                  total-turn budgets, then
                                  ``_finalize_clean_exit`` (delivery history
                                  gate, terminal post-processing, continuation
                                  scheduling, ``max_turns`` stop).
   * ``"shutdown_interrupted"`` → ``_handle_shutdown_interrupted``.
   * anything else              → ``_handle_worker_error``: retry vs auto-pause
                                  classification and exponential back-off.
4. emit     — ``_emit_worker_exit``: the final ``worker_exit`` log line plus
              observer notification. Phases that stop the exit early return
              ``False`` so this step is skipped, exactly as the inline
              ``return`` statements did before.

Convention: every function takes the ``Orchestrator`` instance explicitly as
its first parameter (``orch``) instead of being a method. All state still
lives on the orchestrator; this module only sequences it. ``core`` imports
this module at load time, so the reverse reference is late-bound through
``_core()``. That indirection also preserves the documented test seam:
``commit_workspace_on_done`` and ``finalize_delivery_history`` are read from
``symphony.orchestrator.core``'s globals at call time, which is where tests
monkeypatch them.

Behaviour contract: this is a pure extraction. Side-effect ordering, log event
names and fields, tracker calls, retry math, and exception handling are the
same as the pre-split method.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from ..errors import SymphonyError
from ..issue import Issue, normalize_state
from ..logging import get_logger
from ..workflow import ServiceConfig
from ..workspace import HISTORY_PUSH_FAILED, HistoryGateResult
from .constants import CONTINUATION_RETRY_DELAY_MS, RETRY_BASE_MS
from .entries import RunningEntry, _IssueDebug
from .helpers import _max_turns_exhausted_target_state
from .release_cycle import (
    is_release_success_state as _is_release_success_state,
    release_verifier_state as _release_verifier_state,
)
from .run_registry import ReleaseGate

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()


def _core():
    """Late-bound handle to ``symphony.orchestrator.core``.

    ``core`` imports this module, so the import has to wait until call time.
    Collaborators that tests monkeypatch on ``core`` (``commit_workspace_on_done``,
    ``finalize_delivery_history``) and the worker-error classifiers that the
    rest of ``core`` shares must be resolved through this handle, never bound
    at import time.
    """
    from . import core

    return core


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------


async def handle_worker_exit(
    orch: Orchestrator,
    issue_id: str,
    reason: str,
    error: str | None,
    *,
    owning_task: asyncio.Task[None] | None = None,
    defer_lease_finish: bool = False,
) -> None:
    entry = _pop_running_entry(orch, issue_id, reason, owning_task=owning_task)
    if entry is None:
        return
    if not defer_lease_finish:
        orch._finish_run_lease(issue_id, entry, reason, error)
    debug = _record_exit_accounting(orch, issue_id, entry, reason, error)

    if reason == "normal":
        if not await _handle_normal_exit(orch, issue_id, entry, debug, reason, error):
            return
    elif reason == "shutdown_interrupted":
        _handle_shutdown_interrupted(issue_id, entry, debug)
    else:
        _handle_worker_error(orch, issue_id, entry, debug, reason, error)
    await _emit_worker_exit(orch, issue_id, entry, reason, error)


# ----------------------------------------------------------------------
# phase 1 — pop
# ----------------------------------------------------------------------


def _pop_running_entry(
    orch: Orchestrator,
    issue_id: str,
    reason: str,
    *,
    owning_task: asyncio.Task[None] | None,
) -> RunningEntry | None:
    # AF-01 — identity gate before the pop. `owning_task` is only passed
    # by the two real callers (the worker's own `finally` and
    # `_on_worker_task_done`); direct-call test sites and other internal
    # callers omit it, which is treated as "no check" (pre-AF-01
    # behavior) rather than "owned by nobody" — many existing tests
    # exercise this method against entries with `worker_task=None`.
    if owning_task is not None and (
        orch._running.get(issue_id) is None
        or orch._dispatch_state.entry_foreign_to(issue_id, owning_task)
    ):
        log.warning(
            "worker_exit_stale_task",
            issue_id=issue_id,
            reason=reason,
        )
        return None
    # INFO-level entry marker — pairs with `worker_finally_entered`.
    # If `worker_finally_entered` is in the log but this is missing,
    # the outer finally's `await self._on_worker_exit(...)` was
    # cancelled before the coroutine body started executing.
    log.info(
        "worker_exit_entered",
        issue_id=issue_id,
        reason=reason,
        running_keys_before_pop=list(orch._running.keys()),
    )
    entry = orch._running.pop(issue_id, None)
    owned_transition = orch._app_release_transition_locks.get(issue_id)
    if (
        entry is not None
        and owned_transition is not None
        and owned_transition[0] is entry
    ):
        orch._app_release_transition_locks.pop(issue_id, None)
    # G3 — clear any stale wait-age bonus once the worker exits. The
    # next entry into `_claimed` (conflict, budget, etc.) will record
    # a fresh release timestamp, so leaving the old one behind would
    # falsely promote the ticket on its next candidate-list appearance.
    orch._claim_released_at.pop(issue_id, None)
    # The wakeup event is per-worker — pop it so a fresh worker (if
    # any) starts with a clean gate. `_paused_issue_ids` is per-issue
    # and is intentionally preserved: it's what lets `_eligible`
    # refuse to re-dispatch a ticket the operator chose to hold.
    pause_event = orch._pause_events.pop(issue_id, None)
    if pause_event is not None and not pause_event.is_set():
        # Unblock anything still awaiting the event so the worker's
        # cancellation path can run to completion.
        pause_event.set()
    log.info(
        "worker_exit_pop",
        issue_id=issue_id,
        reason=reason,
        popped=entry is not None,
        running_keys_after_pop=list(orch._running.keys()),
    )
    return entry


# ----------------------------------------------------------------------
# phase 2 — account
# ----------------------------------------------------------------------


def _record_exit_accounting(
    orch: Orchestrator,
    issue_id: str,
    entry: RunningEntry,
    reason: str,
    error: str | None,
) -> _IssueDebug:
    elapsed = (datetime.now(timezone.utc) - entry.started_at).total_seconds()
    orch._totals.seconds_running += elapsed
    debug = orch._issue_debug.setdefault(issue_id, _IssueDebug())
    debug.last_workspace = entry.workspace_path
    debug.last_error = error
    debug.completed_turn_count += entry.turn_count
    if orch._stats is not None:
        orch._stats.record_run_end(
            issue=entry.issue.identifier,
            state=normalize_state(entry.issue.state),
            agent=orch._entry_agent_kind(entry),
            outcome=reason,
            turns=entry.turn_count,
            seconds=elapsed,
        )
    return debug


# ----------------------------------------------------------------------
# phase 3a — reason == "normal"
# ----------------------------------------------------------------------


async def _handle_normal_exit(
    orch: Orchestrator,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
    reason: str,
    error: str | None,
) -> bool:
    """Return ``True`` when the exit should still emit ``worker_exit``."""
    cfg = orch._workflow_state.current()
    if entry.known_app_release_finalizer and cfg is not None:
        if not await _guard_release_finalizer_at_exit(
            orch, cfg, issue_id, entry, debug
        ):
            return False
    if entry.release_verifier_handoff_complete:
        await _complete_release_verifier_handoff(orch, cfg, issue_id, entry, reason)
        return True
    orch._persisted_retry_attempts.pop(issue_id, None)
    orch._clear_issue_flags(issue_id, retry_attempt=True)
    if entry.release_gate_exhausted:
        _pause_release_gate_exhausted(orch, issue_id, entry, debug)
        return False
    if entry.hit_token_budget:
        if not await _handle_token_budget(orch, cfg, issue_id, entry, debug):
            return False

    if entry.hit_no_stage_change:
        await _handle_no_stage_change(orch, cfg, issue_id, entry, debug)
        return False

    max_total_turns = cfg.agent.max_total_turns if cfg is not None else 60
    if debug.completed_turn_count >= max_total_turns:
        await _stop_on_total_turn_budget(
            orch, cfg, issue_id, entry, debug, max_total_turns
        )
        return False
    return await _finalize_clean_exit(orch, cfg, issue_id, entry, debug, reason)


def _finalizer_rewind_state(cfg: ServiceConfig, entry: RunningEntry) -> str:
    return entry.release_finalizer_rewind_state or next(
        (
            state
            for state in reversed(cfg.tracker.active_states)
            if normalize_state(state) != "verify"
        ),
        _release_verifier_state(cfg),
    )


async def _guard_release_finalizer_at_exit(
    orch: Orchestrator,
    cfg: ServiceConfig,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
) -> bool:
    """Return ``False`` when the finalizer approval was refused at exit."""
    refreshed_finalizer = await orch._refresh_issue_full(cfg, issue_id)
    if refreshed_finalizer is not None:
        entry.issue = refreshed_finalizer
    try:
        finalizer_gate = cast(
            ReleaseGate | None,
            orch._release_registry_call(
                cfg,
                "read_finalizer_gate_at_exit",
                lambda registry: registry.get_release_gate(
                    entry.release_gate_finalizer or entry.issue.identifier
                ),
            ),
        )
        if finalizer_gate is None:
            raise SymphonyError(
                "application release finalizer authority disappeared",
                finalizer=entry.issue.identifier,
            )
        entry.issue, completion_token = orch._guard_release_finalizer_with_version(
            cfg=cfg,
            issue=entry.issue,
            gate=finalizer_gate,
            rewind_state=entry.release_finalizer_rewind_state or None,
            expected_run_id=entry.run_id,
            require_run_authority=True,
        )
        if _is_release_success_state(cfg, entry.issue.state):
            finalizer_gate = orch._mark_release_finalizer_completed(
                cfg=cfg,
                issue=entry.issue,
                gate=finalizer_gate,
                completion_token=completion_token,
                rewind_state=(entry.release_finalizer_rewind_state or None),
            )
    except Exception as exc:
        try:
            entry.issue = await orch._rewind_app_release_transition(
                cfg=cfg,
                issue=entry.issue,
                producing_state=_finalizer_rewind_state(cfg, entry),
                note_body=(
                    "Final delivery was stopped at worker exit because "
                    f"the release approval is invalid: {exc}"
                ),
            )
        except Exception as rewind_exc:
            log.error(
                "release_finalizer_exit_rewind_failed",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                gate_error=str(exc),
                rewind_error=str(rewind_exc),
            )
        debug.last_error = str(exc)
        log.warning(
            "release_finalizer_exit_refused",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
            error=str(exc),
        )
        return False
    return True


async def _complete_release_verifier_handoff(
    orch: Orchestrator,
    cfg: ServiceConfig | None,
    issue_id: str,
    entry: RunningEntry,
    reason: str,
) -> None:
    orch._dispatch_state.cancel_pending_retry(issue_id)
    orch._claimed.discard(issue_id)
    orch._persisted_retry_attempts.pop(issue_id, None)
    orch._clear_issue_flags(issue_id, retry_attempt=True)
    cleanup_started = entry.workspace_cleanup_started
    if cfg is not None and cfg.agent.auto_commit_on_done and not cleanup_started:
        await _core().commit_workspace_on_done(
            entry.workspace_path,
            identifier=entry.issue.identifier,
            title=entry.issue.title,
            exit_reason=reason,
            state=entry.issue.state,
            extra_excludes=orch._artifact_commit_excludes(cfg),
        )
    if cfg is not None and not cleanup_started and orch._workspace_manager is not None:
        await orch._workspace_manager.remove(entry.workspace_path)
    log.info(
        "release_verifier_handoff_completed",
        issue_id=issue_id,
        identifier=entry.issue.identifier,
        finalizer=entry.release_gate_finalizer,
        generation=entry.release_gate_generation,
    )


def _pause_release_gate_exhausted(
    orch: Orchestrator,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
) -> None:
    pause_reason = (
        "application release verification exhausted its rewind budget; "
        "the verifier remains in Verify and requires operator action"
    )
    orch._claimed.add(issue_id)
    orch._paused_issue_ids.add(issue_id)
    orch._pause_reasons[issue_id] = pause_reason
    orch._set_issue_flags(
        issue_id,
        paused=True,
        pause_reason=pause_reason,
    )
    debug.last_error = pause_reason
    log.warning(
        "release_gate_rewind_budget_exhausted",
        issue_id=issue_id,
        issue_identifier=entry.issue.identifier,
        state=entry.issue.state,
    )


async def _handle_token_budget(
    orch: Orchestrator,
    cfg: ServiceConfig | None,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
) -> bool:
    """Return ``True`` only when the stage advanced and the exit continues."""
    if cfg is not None:
        before_state = normalize_state(entry.issue.state)
        refreshed = await orch._refresh_issue_state(cfg, issue_id)
        if refreshed is not None:
            entry.issue = refreshed
        after_state = normalize_state(entry.issue.state)
        if refreshed is not None and after_state != before_state:
            log.info(
                "token_budget_stage_advanced",
                issue_id=issue_id,
                issue_identifier=entry.issue.identifier,
                from_state=before_state,
                to_state=after_state,
            )
            return True
        orch._mark_budget_exhausted(issue_id)
        orch._claimed.add(issue_id)
        cap = entry.token_budget_cap or orch._token_cap_for_entry(cfg, entry)
        debug.last_error = (
            f"max_total_tokens reached "
            f"({entry.codex_state_total_tokens}/{cap} "
            f"in {entry.issue.state}); "
            f"state still {entry.issue.state}"
        )
        log.warning(
            "worker_token_budget_exhausted",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
            state_total_tokens=entry.codex_state_total_tokens,
            total_tokens=entry.codex_total_tokens,
            max_total_tokens=cap,
            state=entry.issue.state,
        )
        await orch._persist_budget_exhausted_state(
            cfg=cfg,
            entry=entry,
            issue_id=issue_id,
            target_state=cfg.agent.budget_exhausted_state,
            budget_kind="tokens",
        )
        return False
    orch._mark_budget_exhausted(issue_id)
    orch._claimed.add(issue_id)
    debug.last_error = "max_total_tokens reached; workflow config unavailable"
    return False


async def _handle_no_stage_change(
    orch: Orchestrator,
    cfg: ServiceConfig | None,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
) -> None:
    count = debug.state_turn_count
    state_name = entry.issue.state or debug.state_turn_state
    action = cfg.agent.no_stage_change_action if cfg is not None else "block"
    if cfg is not None and action != "block":
        persisted = await _persist_no_stage_change_handoff(
            orch,
            cfg=cfg,
            entry=entry,
            issue_id=issue_id,
            target_state=action,
            turn_count=count,
            state_name=state_name,
        )
        if persisted:
            entry.issue = replace(entry.issue, state=action)
        debug.last_error = (
            f"no stage change after {count} turns in {state_name}; "
            f"moved to {action}"
        )
        return
    orch._claimed.add(issue_id)
    target_state = cfg.agent.budget_exhausted_state if cfg is not None else ""
    if cfg is not None and target_state:
        state_turn_limit = orch._max_state_turns_for_state(cfg, state_name)
        persisted = await orch._persist_budget_exhausted_state(
            cfg=cfg,
            entry=entry,
            issue_id=issue_id,
            target_state=target_state,
            budget_kind="no_stage_change",
            state_turn_limit=state_turn_limit,
        )
        if persisted:
            entry.issue = replace(entry.issue, state=target_state)
    pause_reason = (
        f"no stage change after {count} turns in {state_name} - "
        "operator action required"
    )
    debug.last_error = pause_reason
    orch._paused_issue_ids.add(issue_id)
    orch._pause_reasons[issue_id] = pause_reason
    orch._set_issue_flags(
        issue_id,
        paused=True,
        pause_reason=pause_reason,
    )


async def _stop_on_total_turn_budget(
    orch: Orchestrator,
    cfg: ServiceConfig | None,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
    max_total_turns: int,
) -> None:
    orch._mark_budget_exhausted(issue_id)
    orch._claimed.add(issue_id)
    debug.last_error = (
        f"max_total_turns reached ({debug.completed_turn_count}/{max_total_turns})"
    )
    log.warning(
        "worker_total_turn_budget_exhausted",
        issue_id=issue_id,
        issue_identifier=entry.issue.identifier,
        total_turns=debug.completed_turn_count,
        max_total_turns=max_total_turns,
    )
    # Persistence: in-memory `_turn_budget_exhausted` clears on
    # service restart, so without an explicit transition the
    # same ticket runs again next boot. When the operator opted
    # in via `agent.budget_exhausted_state`, write the new
    # state through the tracker so the decision survives
    # restart and reaches anyone reviewing the board.
    target_state = cfg.agent.budget_exhausted_state if cfg is not None else ""
    if target_state and cfg is not None:
        await orch._persist_budget_exhausted_state(
            cfg=cfg,
            entry=entry,
            issue_id=issue_id,
            target_state=target_state,
            budget_kind="turns",
        )


async def _finalize_clean_exit(
    orch: Orchestrator,
    cfg: ServiceConfig | None,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
    reason: str,
) -> bool:
    """Delivery history gate, terminal post-processing, continuation.

    Return ``False`` only when the finalizer pre-cleanup guard rewound the
    ticket (that branch never emitted ``worker_exit``).
    """
    cleanup_started = entry.workspace_cleanup_started
    release_evidence_only = (
        entry.known_app_release
        or entry.known_release_cycle_verifier
        or entry.known_app_release_finalizer
    )
    history_unpublished = await _record_delivery_history(
        orch,
        cfg,
        entry,
        reason,
        cleanup_started=cleanup_started,
        release_evidence_only=release_evidence_only,
    )
    # When the worker ran the ticket all the way to Done, the
    # reconcile path that normally fires after_done/auto_merge/remove
    # will *not* fire here: this entry was just popped from
    # `_running` and `_reconcile_running` only iterates entries it
    # finds there. Run the same terminal-state post-processing
    # inline so a clean win produces the same artefacts as a
    # reconcile-driven termination.
    is_done = (entry.issue.state or "").strip().lower() == "done"
    terminal_states = (
        {normalize_state(s) for s in cfg.tracker.terminal_states}
        if cfg is not None
        else set()
    )
    is_terminal = normalize_state(entry.issue.state) in terminal_states
    if cleanup_started:
        pass
    elif (
        release_evidence_only
        and is_terminal
        and cfg is not None
        and orch._workspace_manager is not None
    ):
        # Release verifiers/finalizers prove an already-integrated
        # target. Their branch is snapshotted for audit, never merged
        # or delivered through `after_done`.
        if entry.known_app_release_finalizer:
            if not await _guard_release_finalizer_before_cleanup(orch, cfg, entry):
                return False
        await orch._workspace_manager.remove(entry.workspace_path)
    elif history_unpublished:
        # Commit exists, remote does not have it. The card is now in
        # `Human Review`; keep the workspace so an operator can finish
        # the push by hand, and skip the Done post-processing that
        # would merge and reap it.
        pass
    elif is_done and cfg is not None and orch._workspace_manager is not None:
        await _post_process_done(orch, cfg, entry, debug)
        # Don't schedule a continuation — a Done ticket has nothing
        # to continue. Skip straight to the worker_exit emit below.
    elif not is_terminal and not entry.hit_max_turns:
        _schedule_continuation(orch, issue_id, entry)
    elif entry.hit_max_turns:
        await _stop_on_max_turns(orch, cfg, issue_id, entry, debug)
    return True


async def _record_delivery_history(
    orch: Orchestrator,
    cfg: ServiceConfig | None,
    entry: RunningEntry,
    reason: str,
    *,
    cleanup_started: bool,
    release_evidence_only: bool,
) -> bool:
    """Return ``True`` when the delivery commit exists locally but not remotely."""
    core = _core()
    # Final History Gate, host-side. The agent cannot be the one to
    # prove delivery: it runs sandboxed and may not reach the object
    # database at all (see `utils.git_sandbox`). The orchestrator is
    # unsandboxed, so it always records the branch locally. It pushes
    # and re-reads the remote tip only when
    # `agent.auto_merge_push_target` is true; local-only workflows
    # never publish the feature branch before the target merge.
    history_unpublished = False
    if (
        release_evidence_only
        and cfg is not None
        and cfg.agent.auto_commit_on_done
        and not cleanup_started
    ):
        await core.commit_workspace_on_done(
            # Release evidence is an audit snapshot of an already
            # host-authorized target; keep this path local-only even
            # when normal tickets publish their history.
            entry.workspace_path,
            identifier=entry.issue.identifier,
            title=entry.issue.title,
            exit_reason=reason,
            state=entry.issue.state,
            extra_excludes=orch._artifact_commit_excludes(cfg),
        )
    elif (
        cfg is not None
        and cfg.agent.auto_commit_on_done
        and not cleanup_started
        and normalize_state(entry.issue.state) in ("done", "human review")
    ):
        history = await core.finalize_delivery_history(
            entry.workspace_path,
            identifier=entry.issue.identifier,
            title=entry.issue.title,
            state=entry.issue.state,
            push=cfg.agent.auto_merge_push_target,
        )
        if history.status == HISTORY_PUSH_FAILED:
            history_unpublished = True
            await _flag_unpublished_history(orch, cfg, entry.issue, history)
    elif cfg is not None and cfg.agent.auto_commit_on_done and not cleanup_started:
        # Snapshot whatever the agent left in the worktree, even if
        # the ticket isn't strictly at Done. The worker stopped
        # cleanly (`reason == "normal"`); any subsequent reconcile or
        # operator cleanup would `git worktree remove --force` and
        # discard uncommitted work otherwise. Lenient — failures only
        # warn; a missed snapshot must not block the queue.
        await core.commit_workspace_on_done(
            entry.workspace_path,
            identifier=entry.issue.identifier,
            title=entry.issue.title,
            exit_reason=reason,
            state=entry.issue.state,
            extra_excludes=orch._artifact_commit_excludes(cfg),
        )
    return history_unpublished


async def _guard_release_finalizer_before_cleanup(
    orch: Orchestrator,
    cfg: ServiceConfig,
    entry: RunningEntry,
) -> bool:
    """Return ``False`` when the approval was invalid and the ticket rewound."""
    try:
        finalizer_gate = cast(
            ReleaseGate | None,
            orch._release_registry_call(
                cfg,
                "finalizer_pre_cleanup_gate",
                lambda registry: registry.get_release_gate(
                    entry.release_gate_finalizer or entry.issue.identifier
                ),
            ),
        )
        if finalizer_gate is None:
            raise SymphonyError(
                "application release finalizer authority disappeared",
                finalizer=entry.issue.identifier,
            )
        entry.issue = orch._guard_release_finalizer(
            cfg=cfg,
            issue=entry.issue,
            gate=finalizer_gate,
            rewind_state=(entry.release_finalizer_rewind_state or None),
            expected_run_id=entry.run_id,
            require_run_authority=True,
        )
    except Exception as exc:
        entry.issue = await orch._rewind_app_release_transition(
            cfg=cfg,
            issue=entry.issue,
            producing_state=_finalizer_rewind_state(cfg, entry),
            note_body=(
                "Final delivery was stopped immediately before "
                f"cleanup because the approval is invalid: {exc}"
            ),
        )
        return False
    return True


async def _post_process_done(
    orch: Orchestrator,
    cfg: ServiceConfig,
    entry: RunningEntry,
    debug: _IssueDebug,
) -> None:
    merge_ok = await orch._auto_merge_done_gate_or_block(
        cfg,
        entry.issue,
        entry.workspace_path,
        debug_target=debug,
    )
    if merge_ok:
        await orch._after_done_then_remove_per_policy(
            cfg,
            entry.workspace_path,
            identifier=entry.issue.identifier,
            title=entry.issue.title,
            debug_target=debug,
        )
        # C5 — count this Done and run wiki-sweep if the cadence
        # configured by `wiki.sweep_every_n` is up. Failures are
        # absorbed inside the helper so we never block the
        # Done transition on a wiki housekeeping nudge.
        await orch._maybe_run_wiki_sweep(cfg, identifier=entry.issue.identifier)


def _schedule_continuation(
    orch: Orchestrator,
    issue_id: str,
    entry: RunningEntry,
) -> None:
    orch._schedule_retry(
        issue_id,
        identifier=entry.issue.identifier,
        attempt=1,
        delay_ms=CONTINUATION_RETRY_DELAY_MS,
        error=None,
        kind="continuation",
        touched_files=frozenset(orch._touched_files_for(entry.issue)),
    )


async def _stop_on_max_turns(
    orch: Orchestrator,
    cfg: ServiceConfig | None,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
) -> None:
    # `max_turns` exhausted without a terminal transition: stop
    # auto-continuation and, when the workflow exposes a Blocked
    # terminal state, persist that state so the web/TUI boards do
    # not look idle while the ticket is actually operator-blocked.
    orch._claimed.add(issue_id)
    attempt_cap = cfg.agent.max_turns if cfg is not None else 0
    target_state = _max_turns_exhausted_target_state(cfg) if cfg is not None else ""
    persisted = False
    if cfg is not None and target_state:
        persisted = await orch._persist_budget_exhausted_state(
            cfg=cfg,
            entry=entry,
            issue_id=issue_id,
            target_state=target_state,
            budget_kind="max_turns",
        )
        if persisted:
            entry.issue = replace(entry.issue, state=target_state)
    suffix = (
        f"; moved to {target_state}" if persisted else " — operator action required"
    )
    debug.last_error = f"max_turns reached ({attempt_cap}/attempt){suffix}"


# ----------------------------------------------------------------------
# phase 3b — reason == "shutdown_interrupted"
# ----------------------------------------------------------------------


def _handle_shutdown_interrupted(
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
) -> None:
    # A managed stop is a recovery boundary, not a worker failure.
    # Do not persist pause/retry flags; the next service instance will
    # atomically claim the latest completed-turn checkpoint.
    debug.last_error = None
    log.info(
        "worker_shutdown_interrupted",
        issue_id=issue_id,
        issue_identifier=entry.issue.identifier,
    )


# ----------------------------------------------------------------------
# phase 3c — worker error: retry or auto-pause, then back-off
# ----------------------------------------------------------------------


def _handle_worker_error(
    orch: Orchestrator,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
    reason: str,
    error: str | None,
) -> None:
    core = _core()
    failure_reason = f"{reason}: {error}" if error else reason
    cleaned_failure = core._clean_board_error_message(failure_reason)
    cfg = orch._workflow_state.current()
    if _switch_backend_on_quota_error(
        orch, cfg, issue_id, entry, debug, reason, error, cleaned_failure
    ):
        pass
    elif core._is_retryable_worker_error(orch._entry_agent_kind(entry), reason, error):
        debug.last_error = cleaned_failure
        log.warning(
            "worker_error_retry_scheduled",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
            reason=reason,
            error=error,
        )
    else:
        pause_reason = core._worker_error_pause_reason(reason, error)
        debug.last_error = pause_reason
        orch._paused_issue_ids.add(issue_id)
        orch._pause_reasons[issue_id] = pause_reason
        orch._set_issue_flags(
            issue_id,
            paused=True,
            pause_reason=pause_reason,
        )
        log.warning(
            "worker_error_auto_paused",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
            reason=reason,
            error=error,
            pause_reason=pause_reason,
        )
    next_attempt = (entry.retry_attempt or 0) + 1
    cap = cfg.agent.max_retry_backoff_ms if cfg is not None else 300_000
    delay_ms = min(RETRY_BASE_MS * (2 ** (next_attempt - 1)), cap)
    orch._schedule_retry(
        issue_id,
        identifier=entry.issue.identifier,
        attempt=next_attempt,
        delay_ms=delay_ms,
        error=cleaned_failure,
        kind="retry",
        touched_files=frozenset(orch._touched_files_for(entry.issue)),
    )


def _switch_backend_on_quota_error(
    orch: Orchestrator,
    cfg: ServiceConfig | None,
    issue_id: str,
    entry: RunningEntry,
    debug: _IssueDebug,
    reason: str,
    error: str | None,
    cleaned_failure: str,
) -> bool:
    """Pin the next `agent.fallback_kinds` backend after a quota error.

    Returns True when the ticket was re-pinned (the caller then schedules
    the normal retry instead of pausing). A quota error with no untried
    fallback, or a tracker that cannot carry the pin, falls through to the
    existing retry/auto-pause decision.
    """
    core = _core()
    if cfg is None or not cfg.agent.fallback_kinds:
        return False
    if not core._is_quota_worker_error(reason, error):
        return False
    current = orch._entry_agent_kind(entry)
    debug.quota_exhausted_kinds.add(current)
    next_kind = orch._next_fallback_agent_kind(cfg, entry, debug)
    if next_kind is None:
        log.warning(
            "backend_fallback_exhausted",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
            tried=sorted(debug.quota_exhausted_kinds),
            error=error,
        )
        return False
    if not orch._pin_fallback_agent_kind(cfg, entry.issue.identifier, next_kind):
        return False
    note = (
        f"`{current}` reported a quota/usage limit: {cleaned_failure}. Symphony "
        f"pinned this ticket to `{next_kind}` (agent.fallback_kinds) and "
        "scheduled a retry instead of pausing. Remove the `agent.kind` pin "
        "from the frontmatter to return to the board default."
    )
    try:
        orch._tracker_call_append_note(cfg, entry.issue, "Backend Fallback", note)
    except Exception as exc:
        log.warning(
            "backend_fallback_note_failed",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
            error=str(exc),
        )
    entry.issue = replace(entry.issue, agent_kind=next_kind)
    entry.agent_kind = next_kind
    debug.last_error = f"quota on {current}; falling back to {next_kind}"
    log.warning(
        "worker_quota_backend_fallback",
        issue_id=issue_id,
        issue_identifier=entry.issue.identifier,
        from_kind=current,
        to_kind=next_kind,
        error=error,
    )
    return True


# ----------------------------------------------------------------------
# phase 4 — emit
# ----------------------------------------------------------------------


async def _emit_worker_exit(
    orch: Orchestrator,
    issue_id: str,
    entry: RunningEntry,
    reason: str,
    error: str | None,
) -> None:
    log.info(
        "worker_exit",
        issue_id=issue_id,
        issue_identifier=entry.issue.identifier,
        reason=reason,
        error=error,
    )
    await orch._notify_observers()


# ----------------------------------------------------------------------
# tracker-facing helpers used only by this path
# ----------------------------------------------------------------------


async def _flag_unpublished_history(
    orch: Orchestrator,
    cfg: ServiceConfig,
    issue: Issue,
    result: HistoryGateResult,
) -> None:
    """Downgrade a card whose commit landed locally but never reached the remote.

    `Human Review` rather than `Blocked`: the work is committed and cannot
    be lost, so there is nothing for an RCA agent to root-cause — only a
    remote an operator has to settle. Blocking here would stall the queue
    over a publishing problem the pipeline already survived.
    """
    note = (
        "The delivery commit was recorded locally but Symphony could not "
        "verify it on the remote, so this card is not `Done` yet.\n\n"
        f"- branch: `{result.branch or '(unknown)'}`\n"
        f"- local commit: `{result.local_sha[:12] or 'none'}`\n"
        f"- remote tip: `{result.remote_sha[:12] or 'not found'}`\n"
        f"- classification: `{result.failure_kind}`\n\n"
        "Workspace preserved. Publish the branch and move the card to "
        "`Done`, or say why it should not be published.\n\n"
        f"```\n{result.detail[:1000]}\n```"
    )
    try:
        await asyncio.to_thread(
            orch._tracker_call_append_note,
            cfg,
            issue,
            "History Not Published",
            note,
        )
        await asyncio.to_thread(
            orch._tracker_call_update_state, cfg, issue, "Human Review"
        )
    except Exception as exc:
        log.warning(
            "history_gate_downgrade_failed",
            identifier=issue.identifier,
            branch=result.branch,
            error=str(exc),
        )
        return
    log.warning(
        "history_gate_unpublished",
        identifier=issue.identifier,
        branch=result.branch,
        local_sha=result.local_sha,
        remote_sha=result.remote_sha,
    )
    orch.request_refresh()


async def _persist_no_stage_change_handoff(
    orch: Orchestrator,
    *,
    cfg: ServiceConfig,
    entry: RunningEntry,
    issue_id: str,
    target_state: str,
    turn_count: int,
    state_name: str,
) -> bool:
    note_body = (
        f"Symphony stopped this worker: no stage change after {turn_count} "
        f"turns in {state_name}. "
        f"The workflow is configured to hand off to {target_state}, so "
        "Symphony moved the ticket there for the next stage."
    )
    try:
        await asyncio.to_thread(
            orch._tracker_call_update_state,
            cfg,
            entry.issue,
            target_state,
        )
        await asyncio.to_thread(
            orch._tracker_call_append_note,
            cfg,
            entry.issue,
            "Stage Watchdog Handoff",
            note_body,
        )
        orch._clear_tracker_error(issue_id)
        return True
    except Exception as exc:
        log.warning(
            "no_stage_change_handoff_failed",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
            target_state=target_state,
            error=str(exc),
        )
        orch._record_tracker_error(issue_id, exc)
        return False
