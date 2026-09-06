"""Worker body — one agent attempt, extracted from ``core.Orchestrator``.

``run_agent_attempt`` is the body that used to live inline in
``Orchestrator._run_agent_attempt``. It runs these phases in order:

1. startup  — ``_start_attempt``: resolve the release authority
              (``_resolve_release_authority``), route the workflow config for
              the ticket's agent kind, bind the workspace, heartbeat the run
              lease, run the ``before_run`` hook, and build the backend
              (``_build_attempt_backend``).
2. backend  — ``_start_backend``: ``client.start()`` / ``client.initialize()``
              with the agent-pid bookkeeping around them.
3. session  — ``_open_session``: first-turn prompt, exact session
              continuation (or a fresh ``start_session``), and the phase
              bookkeeping the turn loop starts from.
4. turns    — ``_run_turn_loop``: per turn, honour the operator pause gate
              (``_honour_pause_gate``), enforce the total-turn budget, rebuild
              the backend on a phase transition (``_transition_phase``), build
              the prompt (``_build_turn_prompt``), run it (``_run_turn``), run
              the post-turn hooks (``_after_turn_hooks``), checkpoint
              (``_checkpoint_completed_turn``), then refresh the ticket and
              decide whether to continue (``_evaluate_turn_result``).
5. cleanup  — ``_cleanup_backend``: final ``client.stop()``, the deferred
              ``after_run`` hook, and the artifact salvage pass. Runs in a
              ``finally`` that begins right after the backend is built —
              exactly where the inline ``try`` began, so a failing
              ``client.start()`` still reaches ``client.stop()``.
6. exit     — back in ``run_agent_attempt``: classify the exception (if any)
              into an outcome, stamp ``exit_started_at``, and hand off to
              ``orch._on_worker_exit`` under ``asyncio.shield``.

Control flow: the inline body used ``return`` (with ``outcome`` / ``error``
set first) and ``break`` from deep inside the loop. Phases signal the same
thing through their return value — ``_AttemptExit`` carries the
``(outcome, error)`` pair for an early return, ``_TurnFlow.STOP`` is the loop
``break`` — and the callers return / break at the same points.

State: the inline body kept ~17 locals alive across the phases, and its
``finally`` blocks read the *latest* values of ``issue`` / ``cfg`` /
``client`` / ``after_run_pending`` even when a phase raised. ``_AttemptState``
is that closure made explicit: one mutable record passed to every phase.
Nothing is stored on the orchestrator.

Convention: every function takes the ``Orchestrator`` instance explicitly as
its first parameter (``orch``). ``core`` imports this module at load time, so
the reverse reference is late-bound through ``_core()`` for the helpers that
still live there (``_AgentPhaseState``, ``_backend_agent_pid``,
``_has_app_release_label``, ``_update_state_turn_counter``).

Behaviour contract: this is a pure extraction. Await ordering, side effects,
log event names and fields, exception handling, and ``finally`` semantics are
the same as the pre-split method.
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, cast

from ..backends import AgentBackend, BackendInit, redact_session_id
from ..backends.codex import linear_graphql_tool
from ..errors import (
    SymphonyError,
    TurnCancelled,
    TurnFailed,
    TurnInputRequired,
    TurnTimeout,
)
from ..issue import Issue, normalize_state
from ..logging import get_logger
from ..prompt import build_continuation_prompt, build_first_turn_prompt
from ..skills import render_skill_block
from ..utils import git_inspect
from ..workflow import ServiceConfig
from ..workspace import Workspace, WorkspaceManager
from .entries import RunningEntry, _IssueDebug
from .helpers import _config_for_issue_agent, _is_rewind_transition
from .release_cycle import release_failure_target_state as _release_failure_target_state
from .run_registry import ReleaseGate, RunRegistry

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()


def _core():
    """Late-bound handle to ``symphony.orchestrator.core``.

    ``core`` imports this module, so the import has to wait until call time.
    The phase-state dataclass and the small helpers the loop shares with the
    rest of ``core`` (``_backend_agent_pid``, ``_has_app_release_label``,
    ``_update_state_turn_counter``) are resolved through this handle.
    """
    from . import core

    return core


def _workspace_manager(orch: Orchestrator) -> WorkspaceManager:
    """The live workspace manager, read at call time.

    Config reload can swap ``orch._workspace_manager`` mid-run, so phases must
    not cache it. Startup asserts it is set before any of them run.
    """
    manager = orch._workspace_manager
    assert manager is not None
    return manager


# ----------------------------------------------------------------------
# phase signalling + state
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _AttemptExit:
    """Early exit: the ``(outcome, error)`` pair handed to ``_on_worker_exit``."""

    outcome: str
    error: str | None = None


class _TurnFlow(Enum):
    """Turn-loop control returned by phases that may end the loop cleanly."""

    CONTINUE = "continue"
    STOP = "stop"


@dataclass
class _AttemptState:
    """The former closure locals of ``_run_agent_attempt``, made explicit.

    Mutable on purpose: the turn loop reassigns ``issue`` / ``cfg`` / ``client``
    on every phase transition, and ``_cleanup_backend`` must see the latest
    values even when the loop raised. ``_start_attempt`` fills the first block;
    ``_open_session`` fills the turn bookkeeping; the loop advances it.
    """

    running_issue_id: str
    attempt: int | None
    # The *unrouted* workflow config (see `_start_attempt`).
    base_cfg: ServiceConfig
    issue: Issue
    cfg: ServiceConfig
    workspace: Workspace
    # Last non-None `_running` entry this worker observed.
    running: RunningEntry
    client: AgentBackend
    after_run_pending: bool = False
    # Turn bookkeeping — set by `_open_session`, advanced by the loop.
    turn_number: int = 0
    debug: _IssueDebug = field(default_factory=_IssueDebug)
    doc_language: str = ""
    first_prompt: str = ""
    prev_phase_state: str = ""
    prev_phase_state_raw: str = ""
    known_app_release: bool = False
    current_state: str = ""


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------


async def run_agent_attempt(
    orch: Orchestrator, issue: Issue, attempt: int | None, cfg: ServiceConfig
) -> None:
    running_issue_id = issue.id
    outcome: str = "normal"
    error: str | None = None
    try:
        early_exit = await _run_attempt(orch, issue, attempt, cfg)
        if early_exit is not None:
            outcome = early_exit.outcome
            error = early_exit.error
    except asyncio.CancelledError:
        outcome = "shutdown_interrupted" if orch._stopping else "cancelled"
        error = None
        raise
    except SymphonyError as exc:
        outcome = "error"
        running = orch._running.get(running_issue_id)
        private_session_id = running.resume_session_id if running is not None else None
        error = str(redact_session_id(str(exc), private_session_id))
    except Exception as exc:
        outcome = "error"
        running = orch._running.get(running_issue_id)
        private_session_id = running.resume_session_id if running is not None else None
        error = str(redact_session_id(str(exc), private_session_id))
        log.error(
            "worker_unhandled_error",
            issue_id=running_issue_id,
            error=error,
            exc_type=type(exc).__name__,
            traceback=str(
                redact_session_id(traceback.format_exc(), private_session_id)
            ),
        )
    finally:
        # Diagnostic marker — pairs with `worker_task_done_without_cleanup`
        # to localize the path that leaves entries in `_running`. If
        # this line is missing from the log right before that error,
        # the outer finally never ran (Python contract violation =
        # interpreter shutdown / OS-level kill). If it IS present,
        # the bypass is inside `_on_worker_exit` itself.
        log.info(
            "worker_finally_entered",
            issue_id=running_issue_id,
            outcome=outcome,
            error=error,
        )
        # AF-01 — a force-ejected zombie's `finally` can run after a
        # retry already installed a fresh entry under this issue id
        # (the zombie task is never cancelled by force-eject, only its
        # bookkeeping is dropped). Only the task that actually owns the
        # current entry may stamp `exit_started_at` or enter
        # `_on_worker_exit`; a foreign owner must not touch either.
        # The handler keeps its own identity check as the single guard
        # around the eventual pop.
        # `entry.worker_task is None` counts as owned — many existing
        # tests drive this coroutine directly against a hand-installed
        # entry that never went through `_dispatch`.
        owning_task = asyncio.current_task()
        entry = orch._running.get(running_issue_id)
        stale_entry = (
            entry is not None
            and owning_task is not None
            and orch._dispatch_state.entry_foreign_to(running_issue_id, owning_task)
        )
        if stale_entry:
            log.warning(
                "worker_finally_stale_entry",
                issue_id=running_issue_id,
                reason=outcome,
            )
        elif entry is not None:
            entry.exit_started_at = datetime.now(timezone.utc)
            await asyncio.shield(
                orch._on_worker_exit(
                    running_issue_id, outcome, error, owning_task=owning_task
                )
            )


async def _run_attempt(
    orch: Orchestrator, issue: Issue, attempt: int | None, cfg: ServiceConfig
) -> _AttemptExit | None:
    """startup → (backend, session, turns) with cleanup in a ``finally``."""
    started = await _start_attempt(orch, issue, attempt, cfg)
    if isinstance(started, _AttemptExit):
        return started
    st = started
    try:
        await _start_backend(orch, st)
        await _open_session(orch, st)
        return await _run_turn_loop(orch, st)
    finally:
        await _cleanup_backend(orch, st)


# ----------------------------------------------------------------------
# phase 1 — startup
# ----------------------------------------------------------------------


async def _start_attempt(
    orch: Orchestrator, issue: Issue, attempt: int | None, cfg: ServiceConfig
) -> _AttemptState | _AttemptExit:
    running_issue_id = issue.id
    resolved = _resolve_release_authority(orch, issue, cfg)
    if isinstance(resolved, _AttemptExit):
        return resolved
    issue = resolved
    # Keep the *unrouted* workflow config: `agent.stage_kinds` must be
    # re-resolved at every in-run phase transition, and re-resolving
    # against an already-routed cfg would pin the first lane's backend
    # for the whole dispatch (the normal Todo→…→Document path).
    base_cfg = cfg
    cfg = _config_for_issue_agent(base_cfg, issue)
    running = orch._running.get(running_issue_id)
    if running is not None:
        running.agent_kind = cfg.agent.kind
    assert orch._workspace_manager is not None
    workspace = await orch._workspace_manager.create_or_reuse(issue.identifier)
    running = orch._running.get(running_issue_id)
    if running is None:
        # Slot was reclaimed externally between dispatch and the
        # first await completing. Surface the orphan path instead
        # of crashing on `KeyError(running_issue_id)` — that crash
        # was the source of the worker_task_finished_without_cleanup
        # cascade observed on OLV-002.
        log.warning(
            "worker_running_entry_vanished",
            issue_id=running_issue_id,
            site="workspace_bind",
        )
        return _AttemptExit("orphaned", "running entry vanished before workspace bind")
    running.workspace_path = workspace.path
    if (
        running.known_app_release
        or running.known_release_cycle_verifier
        or running.known_app_release_finalizer
    ):
        if not orch._heartbeat_run_lease(running_issue_id, running):
            return _AttemptExit(
                "release_authority_error",
                "application release lease was lost before workspace use",
            )
        try:
            running.issue = orch._require_running_release_authority(
                cfg=cfg,
                entry=running,
                workspace_path=workspace.path,
            )
            issue = running.issue
        except Exception as exc:
            return _AttemptExit("release_authority_error", str(exc))
    try:
        await orch._workspace_manager.before_run(workspace.path)
    except Exception as exc:
        return _AttemptExit("before_run_error", str(exc))

    client = _build_attempt_backend(orch, running_issue_id, issue, cfg, workspace)
    # Expose the live backend to `_on_codex_event` so the stall-progress
    # predicate routes through `client.is_progress_event(...)`.
    running.client = client
    return _AttemptState(
        running_issue_id=running_issue_id,
        attempt=attempt,
        base_cfg=base_cfg,
        issue=issue,
        cfg=cfg,
        workspace=workspace,
        running=running,
        client=client,
    )


def _resolve_release_authority(
    orch: Orchestrator, issue: Issue, cfg: ServiceConfig
) -> Issue | _AttemptExit:
    running = orch._running.get(issue.id)
    if running is not None and not running.release_authority_resolved:
        try:
            release_authority = orch._prepare_release_dispatch(issue, cfg)
        except SymphonyError as exc:
            error = str(exc)
            log.error(
                "release_execution_refused",
                issue_id=issue.id,
                identifier=issue.identifier,
                tracker_kind=cfg.tracker.kind,
                error=error,
            )
            return _AttemptExit("error", error)
        issue = release_authority.issue
        running.issue = issue
        running.known_app_release = release_authority.app_release
        running.known_release_cycle_verifier = release_authority.cycle_verifier
        running.known_app_release_finalizer = release_authority.finalizer
        if release_authority.gate is not None:
            running.release_gate_finalizer = release_authority.gate.finalizer_identifier
            running.release_gate_expected_contract_sha256 = (
                release_authority.gate.expected_contract_sha256
            )
            running.release_gate_cycle_fingerprint = (
                release_authority.gate.cycle_fingerprint
            )
            running.release_gate_generation = release_authority.gate.generation
        if release_authority.finalizer:
            running.release_finalizer_rewind_state = issue.state
        running.release_authority_resolved = True
    return issue


def _build_attempt_backend(
    orch: Orchestrator,
    running_issue_id: str,
    issue: Issue,
    cfg: ServiceConfig,
    workspace: Workspace,
) -> AgentBackend:
    tools = []
    if cfg.tracker.kind == "linear" and cfg.agent.kind == "codex":
        tools.append(linear_graphql_tool())

    # Initial dispatch is always forward (no rewind). The env rides
    # in BackendInit so the subprocess spawned by `client.start()`
    # sees exactly this dispatch's values.
    return orch._build_agent_backend(
        BackendInit(
            cfg=cfg,
            cwd=workspace.path,
            workspace_root=cfg.workspace_root,
            on_event=lambda ev, issue_id=running_issue_id: orch._on_codex_event(
                issue_id, ev
            ),
            on_process_started=lambda pid, issue_id=running_issue_id: (
                orch._sync_backend_agent_pid(issue_id, pid)
            ),
            client_tools=tools,
            env=orch._dispatch_env_for(issue=issue, cfg=cfg, is_rewind=False),
        )
    )


# ----------------------------------------------------------------------
# phase 2 — backend start
# ----------------------------------------------------------------------


async def _start_backend(orch: Orchestrator, st: _AttemptState) -> None:
    orch._sync_backend_agent_pid(
        st.running_issue_id, _core()._backend_agent_pid(st.client)
    )
    try:
        await st.client.start()
    finally:
        orch._sync_backend_agent_pid(
            st.running_issue_id, _core()._backend_agent_pid(st.client)
        )
    await st.client.initialize()


# ----------------------------------------------------------------------
# phase 3 — session open
# ----------------------------------------------------------------------


async def _open_session(orch: Orchestrator, st: _AttemptState) -> None:
    st.turn_number = 1
    st.debug = orch._issue_debug.setdefault(st.running_issue_id, _IssueDebug())
    # `cfg.tui.language` is the operator-chosen language for
    # both TUI chrome AND artefact docs. Resolution already
    # honours `SYMPHONY_LANG` (build_service_config call).
    st.doc_language = st.cfg.tui.language
    # Skill files are read off-loop; dispatch shares the event
    # loop with every other running worker.
    skill_context = await asyncio.to_thread(
        render_skill_block, st.cfg.workflow_path.parent, st.issue.skills
    )
    first_prompt, _ = build_first_turn_prompt(
        prompt_template=st.cfg.prompt_template_for_state(st.issue.state),
        issue=st.issue,
        attempt=st.attempt,
        language=st.doc_language,
        turn_number=st.debug.completed_turn_count + st.turn_number,
        max_turns=st.cfg.agent.max_total_turns,
        max_attempts=st.cfg.agent.max_attempts,
        auto_merge_on_done=st.cfg.agent.auto_merge_on_done,
        token_ema=orch._token_ema_for_state(st.issue.state),
        token_budget=orch._token_budget_for_state(st.cfg, st.issue.state),
        rewind_scope=None,
        compact_issue_context=st.cfg.agent.compact_issue_context,
        full_ticket_path=orch._ticket_prompt_path(st.cfg, st.issue),
        artifacts_dir=orch._prompt_artifacts_dir(st.cfg),
        extra_context=skill_context,
    )
    st.first_prompt = first_prompt
    resumed_checkpoint = False
    checkpoint = st.running.continuation_checkpoint
    if checkpoint is not None:
        try:
            resumed_checkpoint = await st.client.resume_session(
                checkpoint.resume_session_id
            )
        except Exception as exc:
            log.error(
                "session_continuation_resume_error",
                issue_id=st.running_issue_id,
                issue_identifier=st.issue.identifier,
                agent_kind=st.cfg.agent.kind,
                error_type=type(exc).__name__,
            )
            raise SymphonyError(
                "exact session continuation failed before turn start"
            ) from None
        st.running.recovery_session_resumed = resumed_checkpoint
        if resumed_checkpoint:
            st.running.resume_session_id = checkpoint.resume_session_id
            orch._append_run_event(st.running, "session_started", {})
            log.info(
                "session_continuation_resumed",
                issue_id=st.running_issue_id,
                issue_identifier=st.issue.identifier,
                checkpoint_turn=checkpoint.turn,
            )
        else:
            log.info(
                "session_continuation_fresh_fallback",
                issue_id=st.running_issue_id,
                issue_identifier=st.issue.identifier,
                checkpoint_turn=checkpoint.turn,
                agent_kind=st.cfg.agent.kind,
            )
    if not resumed_checkpoint:
        await st.client.start_session(
            initial_prompt=st.first_prompt,
            issue_title=f"{st.issue.identifier}: {st.issue.title}",
        )

    # Track which kanban state the backend is currently
    # operating on. When the issue moves to a new state mid-run
    # we tear the backend down and rebuild it so the next phase
    # starts with a fresh context — shared knowledge flows only
    # through the markdown artefacts under
    # `docs/<identifier>/<stage>/` plus the ticket body.
    st.prev_phase_state = normalize_state(st.issue.state)
    # Canonical-cased mirror of `prev_phase_state`. Trackers
    # like Linear and Jira match state names case-sensitively
    # on writes, so a contract-failure rewind needs the
    # original casing rather than the lowercased form.
    st.prev_phase_state_raw = st.issue.state or ""
    # Minimal state refreshes intentionally omit labels. Retain
    # the last full-body app-release signal until the next full
    # refresh so stage-contracts=off cannot erase the machine gate.
    running_entry = orch._running.get(st.running_issue_id)
    st.known_app_release = (
        running_entry.known_app_release if running_entry is not None else False
    ) or _core()._has_app_release_label(st.issue)


# ----------------------------------------------------------------------
# phase 4 — turn loop
# ----------------------------------------------------------------------


async def _run_turn_loop(orch: Orchestrator, st: _AttemptState) -> _AttemptExit | None:
    while True:
        await _honour_pause_gate(orch, st)

        st.current_state = normalize_state(st.issue.state)
        st.debug = orch._issue_debug.setdefault(st.running_issue_id, _IssueDebug())
        if (
            st.cfg.agent.max_total_turns > 0
            and st.debug.completed_turn_count + st.turn_number
            > st.cfg.agent.max_total_turns
        ):
            log.warning(
                "worker_total_turn_budget_boundary",
                issue_id=st.running_issue_id,
                issue_identifier=st.issue.identifier,
                completed_turns=st.debug.completed_turn_count,
                next_turn=st.turn_number,
                max_total_turns=st.cfg.agent.max_total_turns,
            )
            break
        is_phase_transition = (
            st.turn_number > 1 and st.current_state != st.prev_phase_state
        )

        if is_phase_transition:
            step = await _transition_phase(orch, st)
            if isinstance(step, _AttemptExit):
                return step
            if step is _TurnFlow.STOP:
                break

        running_entry = orch._running.get(st.running_issue_id)
        if running_entry is not None and running_entry.hit_empty_response_loop:
            await orch._escalate_empty_response_loop(
                cfg=st.cfg,
                entry=running_entry,
                issue_id=st.running_issue_id,
                cancel_worker=False,
            )
            break

        is_continuation = (
            (st.running.recovery_session_resumed and st.turn_number == 1)
            or (st.turn_number > 1 and not is_phase_transition)
        )
        prompt = _build_turn_prompt(orch, st, is_continuation)

        turn_exit = await _run_turn(orch, st, prompt, is_continuation)
        if turn_exit is not None:
            return turn_exit

        await _after_turn_hooks(orch, st)
        _checkpoint_completed_turn(orch, st)

        # Record the state the backend just operated on so the
        # next iteration can detect a phase transition against
        # the freshly refreshed state below.
        st.prev_phase_state = st.current_state
        # Take the casing from the turn-start snapshot (`st.issue`), never
        # from the running entry: a poll tick that landed mid-turn may
        # already have refreshed `running.issue` to the *advanced* state,
        # and a contract-failure rewind written with that casing is a
        # silent no-op (the live e2e on 2026-09-06 recorded
        # `verify -> verify` instead of `verify -> in progress`).
        st.prev_phase_state_raw = st.issue.state or ""

        step = await _evaluate_turn_result(orch, st)
        if isinstance(step, _AttemptExit):
            return step
        if step is _TurnFlow.STOP:
            break
        st.turn_number += 1
    return None


async def _honour_pause_gate(orch: Orchestrator, st: _AttemptState) -> None:
    # Operator pause gate — `pause_worker` clears the event,
    # `resume_worker` sets it. Honoured at the turn boundary
    # so we never tear down a turn the model is mid-way
    # through. On resume, re-fetch issue state because the
    # operator may have moved the ticket while it was held.
    pause_event = orch._pause_events.get(st.running_issue_id)
    if pause_event is not None and not pause_event.is_set():
        log.info(
            "worker_paused",
            issue_id=st.running_issue_id,
            identifier=st.issue.identifier,
            turn=st.turn_number,
        )
        await pause_event.wait()
        log.info(
            "worker_resumed",
            issue_id=st.running_issue_id,
            identifier=st.issue.identifier,
            turn=st.turn_number,
        )
        refreshed = await orch._refresh_issue_state(st.cfg, st.running_issue_id)
        if refreshed is not None:
            st.issue = refreshed
            running_entry = orch._running.get(st.running_issue_id)
            if running_entry is not None:
                running_entry.issue = st.issue


async def _transition_phase(
    orch: Orchestrator, st: _AttemptState
) -> _AttemptExit | _TurnFlow:
    """Rebuild the backend for the new kanban state; ``STOP`` when the transition ends the run."""
    try:
        transition = await orch._transition_agent_phase(
            phase_state=_core()._AgentPhaseState(
                issue=st.issue,
                cfg=st.cfg,
                client=st.client,
                first_prompt=st.first_prompt,
                current_state=st.current_state,
                known_app_release=st.known_app_release,
            ),
            running_issue_id=st.running_issue_id,
            base_cfg=st.base_cfg,
            workspace_path=st.workspace.path,
            attempt=st.attempt,
            doc_language=st.doc_language,
            producing_state=st.prev_phase_state,
            producing_state_raw=st.prev_phase_state_raw,
            turn_number=st.turn_number,
        )
        if transition is None:
            return _TurnFlow.STOP
        phase_state = transition.state
        st.issue = phase_state.issue
        st.cfg = phase_state.cfg
        st.client = phase_state.client
        st.first_prompt = phase_state.first_prompt
        st.current_state = phase_state.current_state
        st.known_app_release = phase_state.known_app_release
        is_rewind = transition.is_rewind
        running_entry = orch._running.get(st.running_issue_id)
        log.info(
            "worker_phase_transition",
            issue_id=st.issue.id,
            identifier=st.issue.identifier,
            from_state=st.prev_phase_state,
            to_state=st.current_state,
            turn=st.turn_number,
            attempt=st.attempt,
            is_rewind=is_rewind,
            workspace=str(st.workspace.path),
        )
        if running_entry is not None:
            orch._append_run_event(
                running_entry,
                "phase_transition",
                {
                    "from_state": st.prev_phase_state,
                    "to_state": st.current_state,
                    "turn": st.turn_number,
                    "attempt": st.attempt,
                    "is_rewind": is_rewind,
                },
            )
        orch._record_stats_transition(
            st.issue.identifier, st.prev_phase_state, st.current_state
        )
    except Exception as exc:
        return _AttemptExit("phase_transition_error", str(exc))
    return _TurnFlow.CONTINUE


def _build_turn_prompt(
    orch: Orchestrator, st: _AttemptState, is_continuation: bool
) -> str:
    """Continuation prompt (re-reading ``debug``) or the phase's first prompt."""
    if is_continuation:
        st.debug = orch._issue_debug.setdefault(st.running_issue_id, _IssueDebug())
        return build_continuation_prompt(
            language=st.doc_language,
            turn_number=st.debug.completed_turn_count + st.turn_number,
            max_turns=st.cfg.agent.max_total_turns,
        )
    return st.first_prompt


async def _run_turn(
    orch: Orchestrator, st: _AttemptState, prompt: str, is_continuation: bool
) -> _AttemptExit | None:
    """Turn-start bookkeeping, ``before_run`` (turn > 1), then ``client.run_turn``."""
    running = orch._running.get(st.running_issue_id)
    if running is None:
        log.warning(
            "worker_running_entry_vanished",
            issue_id=st.running_issue_id,
            site="turn_start",
        )
        return _AttemptExit("orphaned", "running entry vanished before turn start")
    st.running = running
    running.turn_count = st.turn_number
    if (
        running.known_app_release
        or running.known_release_cycle_verifier
        or running.known_app_release_finalizer
    ):
        if not orch._heartbeat_run_lease(st.running_issue_id, running):
            return _AttemptExit(
                "release_authority_error",
                "application release lease was lost before agent turn",
            )
        try:
            running.issue = orch._require_running_release_authority(
                cfg=st.cfg,
                entry=running,
            )
            st.issue = running.issue
        except Exception as exc:
            return _AttemptExit("release_authority_error", str(exc))
    # Capture the state THIS turn is starting in. C3 EMA
    # samples need the source state, not the destination
    # the agent flips to mid-turn — without this, every
    # stage's tokens get attributed to the next stage.
    running.state_at_turn_start = (running.issue.state or "").lower()
    # Symmetry with worker_turn_completed — a single line per
    # turn-start so multi-turn runs (especially slow ones
    # like gemini -p where a single turn can take 60-90s)
    # don't look stuck between turns.
    log.info(
        "worker_turn_started",
        issue_id=st.running_issue_id,
        identifier=running.issue.identifier,
        turn=st.turn_number,
        max_turns=st.cfg.agent.max_turns,
        is_continuation=is_continuation,
    )
    orch._append_run_event(
        running,
        "turn_started",
        {
            "turn": st.turn_number,
            "state": running.issue.state,
            "continuation": is_continuation,
        },
    )
    if st.turn_number > 1:
        try:
            await _workspace_manager(orch).before_run(st.workspace.path)
        except Exception as exc:
            return _AttemptExit("before_run_error", str(exc))
    orch._sync_backend_agent_pid(
        st.running_issue_id, _core()._backend_agent_pid(st.client)
    )
    st.after_run_pending = True
    try:
        await st.client.run_turn(prompt=prompt, is_continuation=is_continuation)
    except (
        TurnTimeout,
        TurnFailed,
        TurnCancelled,
        TurnInputRequired,
    ) as exc:
        return _AttemptExit(
            "turn_error",
            str(redact_session_id(str(exc), running.resume_session_id)),
        )
    finally:
        orch._sync_backend_agent_pid(
            st.running_issue_id, _core()._backend_agent_pid(st.client)
        )
    return None


async def _after_turn_hooks(orch: Orchestrator, st: _AttemptState) -> None:
    """Completion log, ``after_run`` hook, artifact collection, commit event."""
    # Synchronous log on the worker's hot path — the
    # listener-side `agent_turn_completed` log fires from
    # `_on_codex_event` via the EVENT_TURN_COMPLETED emit,
    # but reconcile can cancel the worker between the emit
    # and the listener running, swallowing the visibility
    # signal. Logging here guarantees one line per
    # successful turn even when reconcile races us.
    running_entry = orch._running.get(st.running_issue_id)
    if running_entry is not None:
        log.info(
            "worker_turn_completed",
            issue_id=st.running_issue_id,
            identifier=running_entry.issue.identifier,
            turn=st.turn_number,
            input_tokens=running_entry.codex_input_tokens,
            cache_input_tokens=running_entry.codex_cache_input_tokens,
            output_tokens=running_entry.codex_output_tokens,
            total_tokens=running_entry.codex_total_tokens,
        )

    await _workspace_manager(orch).after_run_best_effort(st.workspace.path)
    st.after_run_pending = False
    # Collect before the next loop iteration evaluates the
    # stage contract, so `artifacts.require_for_done` sees
    # this turn's deliverables, and before Done removes the
    # workspace they live in.
    await orch._collect_ticket_artifacts(
        st.cfg,
        identifier=st.issue.identifier,
        workspace_path=st.workspace.path,
        run_id=(running_entry.run_id if running_entry is not None else ""),
        turn=st.turn_number,
    )
    # The hook may commit or amend the turn's changes. Resolve
    # HEAD only after it finishes so the explorer never reports
    # the base/prior-turn commit as this turn's result.
    commit_sha = None
    if (st.workspace.path / ".git").exists():
        commit_sha = await asyncio.to_thread(
            git_inspect.resolve_commit, st.workspace.path, "HEAD"
        )
    if commit_sha:
        running_entry = orch._running.get(st.running_issue_id)
        if running_entry is not None:
            orch._append_run_event(
                running_entry,
                "workspace_updated",
                {"turn": st.turn_number, "commit_sha": commit_sha},
            )


def _checkpoint_completed_turn(orch: Orchestrator, st: _AttemptState) -> None:
    running_entry = orch._running.get(st.running_issue_id)
    registry = orch._run_registry
    if (
        st.cfg.agent.crash_continuation
        and registry is not None
        and running_entry is not None
        and running_entry.run_id
        and running_entry.resume_session_id
        and running_entry.last_completed_turn_event == st.turn_number
        and not running_entry.known_app_release
        and not running_entry.known_release_cycle_verifier
        and not running_entry.known_app_release_finalizer
    ):
        checkpoint_turn = st.debug.completed_turn_count + st.turn_number
        checkpoint_registry = cast(RunRegistry, registry)
        checkpoint_run_id = running_entry.run_id
        checkpoint_session_id = running_entry.resume_session_id
        checkpoint_state = running_entry.issue.state
        orch._registry_guard(
            "checkpoint_completed_turn",
            lambda: checkpoint_registry.checkpoint_completed_turn(
                issue_id=st.running_issue_id,
                run_id=checkpoint_run_id,
                resume_session_id=checkpoint_session_id,
                state=checkpoint_state,
                turn=checkpoint_turn,
            ),
            False,
        )


async def _evaluate_turn_result(
    orch: Orchestrator, st: _AttemptState
) -> _AttemptExit | _TurnFlow:
    """Refresh the ticket, apply the release guards, and decide whether to loop."""
    # Refresh issue state.
    refreshed = await orch._refresh_issue_state(st.cfg, st.running_issue_id)
    if refreshed is None:
        return _AttemptExit("issue_state_refresh_failed", "could not refresh issue state")
    st.issue = refreshed
    running = orch._running.get(st.running_issue_id)
    if running is None:
        log.warning(
            "worker_running_entry_vanished",
            issue_id=st.running_issue_id,
            site="post_refresh",
        )
        return _AttemptExit("orphaned", "running entry vanished after issue refresh")
    st.running = running
    running.issue = st.issue
    state = normalize_state(st.issue.state)
    active = {s.lower() for s in st.cfg.tracker.active_states}
    release_rewound = False
    if running.known_app_release_finalizer and state != st.prev_phase_state:
        try:
            finalizer_identifier = running.release_gate_finalizer or st.issue.identifier
            finalizer_gate = cast(
                ReleaseGate | None,
                orch._release_registry_call(
                    st.cfg,
                    "read_finalizer_gate_after_turn",
                    lambda registry: registry.get_release_gate(finalizer_identifier),
                ),
            )
            if finalizer_gate is None:
                raise SymphonyError(
                    "application release finalizer authority disappeared",
                    finalizer=st.issue.identifier,
                )
            st.issue = orch._guard_release_finalizer(
                cfg=st.cfg,
                issue=st.issue,
                gate=finalizer_gate,
                rewind_state=(st.prev_phase_state_raw or st.prev_phase_state),
                expected_run_id=running.run_id,
                require_run_authority=True,
            )
        except Exception as exc:
            try:
                st.issue = await orch._rewind_app_release_transition(
                    cfg=st.cfg,
                    issue=st.issue,
                    producing_state=(st.prev_phase_state_raw or st.prev_phase_state),
                    note_body=(
                        "Final delivery was stopped because the "
                        f"host-owned release approval is invalid: {exc}"
                    ),
                )
                running.issue = st.issue
            except Exception as rewind_exc:
                log.error(
                    "release_finalizer_rewind_failed",
                    issue_id=st.issue.id,
                    identifier=st.issue.identifier,
                    gate_error=str(exc),
                    rewind_error=str(rewind_exc),
                )
            return _AttemptExit("phase_transition_error", str(exc))
        if state in active:
            running.release_finalizer_rewind_state = st.issue.state
    if (
        state != st.prev_phase_state
        and st.prev_phase_state == "verify"
        and not _is_rewind_transition(
            st.prev_phase_state,
            state,
            st.cfg.tracker.active_states,
        )
    ):
        try:
            issue, release_rewound = await orch._enforce_app_release_transition(
                cfg=st.cfg,
                issue=st.issue,
                workspace_path=st.workspace.path,
                producing_state=(st.prev_phase_state_raw or st.prev_phase_state),
                known_app_release=st.known_app_release,
                running_entry=running,
            )
        except Exception as exc:
            return _AttemptExit("phase_transition_error", str(exc))
        st.issue = issue
        st.known_app_release = st.known_app_release or _core()._has_app_release_label(
            st.issue
        )
        running.issue = st.issue
        state = normalize_state(st.issue.state)
    if running.release_verifier_handoff_complete:
        return _TurnFlow.STOP
    if release_rewound:
        st.debug.rewind_count += 1
        if (
            st.cfg.agent.max_attempts > 0
            and st.debug.rewind_count > st.cfg.agent.max_attempts
        ):
            rewind_target = _release_failure_target_state(st.cfg)
            if rewind_target:
                await asyncio.to_thread(
                    orch._tracker_call_update_state,
                    st.cfg,
                    st.issue,
                    rewind_target,
                )
                st.issue = replace(st.issue, state=rewind_target)
                running.issue = st.issue
            else:
                running.release_gate_exhausted = True
            log.warning(
                "rewind_budget_exceeded",
                issue_id=st.issue.id,
                identifier=st.issue.identifier,
                from_state=st.prev_phase_state,
                to_state=state,
                rewind_count=st.debug.rewind_count,
                max_attempts=st.cfg.agent.max_attempts,
                target_state=rewind_target or "(none)",
            )
        return _TurnFlow.STOP
    if state not in active:
        return _TurnFlow.STOP
    state_turn_count = _core()._update_state_turn_counter(st.debug, state)
    max_state_turns = orch._max_state_turns_for_state(st.cfg, state)
    if max_state_turns > 0 and state_turn_count >= max_state_turns:
        running.hit_no_stage_change = True
        log.warning(
            "no_stage_change_watchdog",
            issue_id=st.running_issue_id,
            issue_identifier=running.issue.identifier,
            state=running.issue.state,
            state_turn_count=state_turn_count,
            effective_max_state_turns=max_state_turns,
            global_max_state_turns=st.cfg.agent.max_state_turns,
        )
        return _TurnFlow.STOP
    if st.turn_number >= st.cfg.agent.max_turns:
        # Per-attempt ceiling reached without a terminal
        # transition. Mark explicitly so `_on_worker_exit`
        # doesn't auto-schedule a continuation — the ticket
        # waits for operator action instead of looping
        # silently against the ceiling.
        running.hit_max_turns = True
        log.warning(
            "worker_max_turns_exhausted",
            issue_id=st.running_issue_id,
            issue_identifier=running.issue.identifier,
            turns=st.turn_number,
            max_turns=st.cfg.agent.max_turns,
        )
        return _TurnFlow.STOP
    return _TurnFlow.CONTINUE


# ----------------------------------------------------------------------
# phase 5 — cleanup
# ----------------------------------------------------------------------


async def _cleanup_backend(orch: Orchestrator, st: _AttemptState) -> None:
    # Defensive: a phase transition may have left `client`
    # pointing to a half-initialized backend, or to one whose
    # earlier `stop()` already failed. Either way, exiting the
    # worker without after_run_best_effort would leak workspace
    # state, so swallow stop() errors here too.
    try:
        await st.client.stop()
    except Exception as stop_exc:
        running = orch._running.get(st.running_issue_id)
        if running is not None:
            running.backend_cleanup_unconfirmed = True
        log.warning(
            "worker_final_stop_failed",
            issue_id=st.issue.id,
            identifier=st.issue.identifier,
            error=str(stop_exc),
        )
    else:
        running = orch._running.get(st.running_issue_id)
        if running is not None and running.backend_cleanup_unconfirmed:
            log.warning(
                "worker_final_stop_cleanup_unconfirmed",
                issue_id=st.issue.id,
                identifier=st.issue.identifier,
                pid=running.agent_pgid,
            )
        else:
            orch._sync_backend_agent_pid(st.running_issue_id, None)
    if st.after_run_pending:
        await _workspace_manager(orch).after_run_best_effort(st.workspace.path)
    # Salvage deliverables written before an abnormal exit (turn
    # timeout, TurnFailed, stall eviction). The per-turn call runs
    # only on the success path, and the workspace is torn down at
    # Done, so without this the file is gone for good — worst
    # under `artifacts.require_for_done`, where the deliverable
    # turn is the long, timeout-prone one. Unshielded and
    # best-effort, exactly like the `after_run` hook above.
    entry_for_run = orch._running.get(st.running_issue_id)
    await orch._collect_ticket_artifacts(
        st.cfg,
        identifier=st.issue.identifier,
        workspace_path=st.workspace.path,
        run_id=entry_for_run.run_id if entry_for_run else "",
        turn=None,  # salvage pass: the turn it came from is unknown
    )
