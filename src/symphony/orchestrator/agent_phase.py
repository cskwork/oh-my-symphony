"""Agent phase transitions, stage contracts, and backend event accounting for ``Orchestrator``.

Extracted from ``core.Orchestrator`` (LR-3 lean-core split). Each method is a
module-level function whose first parameter is ``self: Orchestrator``;
``core.Orchestrator`` binds it as a class attribute, so ``self.<name>(...)``
calls, ``Orchestrator.<name>`` patches, and behaviour are unchanged.
Collaborators that tests monkeypatch on ``symphony.orchestrator.core`` are read
through the late-bound ``_core()`` handle at call time, never bound here.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..backends import (
    EVENT_AGENT_RETRY,
    EVENT_APPROVAL_DENIED,
    EVENT_COMPACTION,
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_FAILED,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    AgentBackend,
    BackendInit,
    redact_session_id,
)
from ..backends.codex import linear_graphql_tool
from ..factory.wayfinder import factory_proof_commands
from ..issue import Issue, normalize_state
from ..prompt import build_first_turn_prompt
from ..stats import board_health_summary
from ..workflow import ServiceConfig
from ..workflow.presets import guess_lane_preset
from ..workflow.verify import append_verify_trace, run_verify, verification_scope, verify_config
from .constants import EMPTY_TURN_LOOP_THRESHOLD
from .contracts import _build_result, contract_producing_states, evaluate_contract
from .factory_contracts import (
    factory_browser_failures,
    factory_browser_required,
    factory_verification_text,
)
from .entries import RunningEntry, _IssueDebug
from .helpers import (
    _is_successful_terminal_state,
    _canonical_state_label,
    _config_for_issue_agent,
    _is_rewind_transition,
    _rewind_budget_target_state,
    _utc_iso_z,
    _branch_hook_env,
)
from .parsing import _parse_findings_rows
from . import attempt as agent_attempt
from .core_support import _has_app_release_label, _normalize_agent_pid
from ..logging import get_logger

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()


@dataclass(frozen=True)
class _AgentPhaseState:
    issue: Issue
    cfg: ServiceConfig
    client: AgentBackend
    first_prompt: str
    current_state: str
    known_app_release: bool


@dataclass(frozen=True)
class _AgentPhaseTransition:
    state: _AgentPhaseState
    is_rewind: bool


@dataclass(frozen=True)
class _StageContractOutcome:
    """Result of `Orchestrator._enforce_stage_contract`."""

    issue: Issue
    current_state: str
    known_app_release: bool
    rewound: bool


def _backend_agent_pid(backend: AgentBackend) -> int | None:
    return _normalize_agent_pid(getattr(backend, "pid", None))


_BOARD_HEALTH_LANES = frozenset({"document", "learn"})


# ------------------------------------------------------------------
# worker (§16.5)
# ------------------------------------------------------------------

async def run_legacy_stage_loop(
    self: Orchestrator, issue: Issue, attempt: int | None, cfg: ServiceConfig
) -> None:
    """Public entry point for `LegacyStageExecutor`.

        A one-line forward so the execution-mode seam does not have to
        reach into a private method across modules.
        """
    await self._run_agent_attempt(issue, attempt, cfg)


async def _run_agent_attempt(
    self: Orchestrator, issue: Issue, attempt: int | None, cfg: ServiceConfig
) -> None:
    verify_config(cfg)
    return await agent_attempt.run_agent_attempt(self, issue, attempt, cfg)


async def _transition_agent_phase(
    self: Orchestrator,
    *,
    phase_state: _AgentPhaseState,
    running_issue_id: str,
    base_cfg: ServiceConfig,
    workspace_path: Path,
    attempt: int | None,
    doc_language: str,
    producing_state: str,
    producing_state_raw: str,
    turn_number: int,
) -> _AgentPhaseTransition | None:
    issue = phase_state.issue
    cfg = phase_state.cfg
    client = phase_state.client
    first_prompt = phase_state.first_prompt
    current_state = phase_state.current_state
    known_app_release = phase_state.known_app_release
    debug = self._issue_debug.setdefault(running_issue_id, _IssueDebug())
    # Defensive: the raw label must denote `producing_state`. If a caller
    # hands us the advanced state's casing, a contract rewind would write
    # the state the ticket is already in (a silent no-op). Fall back to
    # the workflow's own spelling of the producing state.
    if normalize_state(producing_state_raw) != producing_state:
        producing_state_raw = _canonical_state_label(cfg, producing_state)

    is_rewind = _is_rewind_transition(
        producing_state,
        current_state,
        cfg.tracker.active_states,
    )
    # v0.6.7 — contract validator. When the agent moved forward (not a
    # rewind), check that the producing stage actually wrote the outputs
    # its prompt promised; a failure writes the state back to the
    # producing stage and counts as a forced rewind below. The same
    # method gates the move into Done from the worker loop
    # (`attempt._post_turn_refresh`), where terminal transitions never
    # reach this phase handler.
    if not is_rewind and (
        self._is_factory_profile(cfg)
        or cfg.agent.stage_contracts_enabled(cfg.tracker.active_states)
        or producing_state in verify_config(cfg).commands
    ):
        outcome = await self._enforce_stage_contract(
            cfg=cfg,
            running_issue_id=running_issue_id,
            issue=issue,
            producing_state=producing_state,
            producing_state_raw=producing_state_raw,
            current_state=current_state,
            workspace_path=workspace_path,
            known_app_release=known_app_release,
        )
        issue = outcome.issue
        current_state = outcome.current_state
        known_app_release = outcome.known_app_release
        if outcome.rewound:
            is_rewind = True
    if is_rewind:
        debug.rewind_count += 1
        if (
            cfg.agent.max_attempts > 0
            and debug.rewind_count > cfg.agent.max_attempts
        ):
            rewind_target = _rewind_budget_target_state(cfg)
            if rewind_target:
                await asyncio.to_thread(
                    self._tracker_call_update_state,
                    cfg,
                    issue,
                    rewind_target,
                )
                issue = replace(issue, state=rewind_target)
            running_entry = self._running.get(running_issue_id)
            if running_entry is not None:
                running_entry.issue = issue
            log.warning(
                "rewind_budget_exceeded",
                issue_id=issue.id,
                identifier=issue.identifier,
                from_state=producing_state,
                to_state=current_state,
                rewind_count=debug.rewind_count,
                max_attempts=cfg.agent.max_attempts,
                # F-32: a board with no block/human
                # terminal lane keeps its state; the
                # worker still stops.
                target_state=rewind_target or "(none)",
            )
            return None
    running_entry = self._running.get(running_issue_id)
    if running_entry is not None:
        running_entry.consecutive_empty_turns = 0
        running_entry.hit_empty_response_loop = False
    # F-01: route the *new* lane's backend. The ticket
    # walks several states inside one dispatch, so the
    # kind must be re-resolved from the unrouted config
    # here — not reused from the lane we started in.
    phase_cfg = _config_for_issue_agent(base_cfg, issue)
    if phase_cfg.agent.kind != cfg.agent.kind:
        log.info(
            "stage_backend_rerouted",
            issue_id=issue.id,
            identifier=issue.identifier,
            from_state=producing_state,
            to_state=current_state,
            from_kind=cfg.agent.kind,
            to_kind=phase_cfg.agent.kind,
        )
    cfg = phase_cfg
    if running_entry is not None:
        running_entry.agent_kind = cfg.agent.kind
    client, first_prompt = await self._rebuild_backend_for_phase(
        issue=issue,
        running_issue_id=running_issue_id,
        cfg=cfg,
        workspace_path=workspace_path,
        attempt=attempt,
        doc_language=doc_language,
        old_client=client,
        is_rewind=is_rewind,
        turn_number=debug.completed_turn_count + turn_number,
    )
    running_entry = self._running.get(running_issue_id)
    if running_entry is not None:
        # New backend instance — refresh the
        # `_on_codex_event` reference so the stall
        # predicate keeps routing to the live driver.
        running_entry.client = client
        running_entry.thread_id = None
        running_entry.session_id = None
        running_entry.turn_id = None
        running_entry.resume_session_id = None
        running_entry.last_completed_turn_event = 0
    return _AgentPhaseTransition(
        state=_AgentPhaseState(
            issue=issue,
            cfg=cfg,
            client=client,
            first_prompt=first_prompt,
            current_state=current_state,
            known_app_release=known_app_release,
        ),
        is_rewind=is_rewind,
    )


async def _enforce_stage_contract(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    running_issue_id: str,
    issue: Issue,
    producing_state: str,
    producing_state_raw: str,
    current_state: str,
    workspace_path: Path,
    known_app_release: bool,
    running_entry: RunningEntry | None = None,
) -> _StageContractOutcome:
    # Reconcile must not mistake a quiet host command for a stuck agent or
    # remove a terminal workspace before its verification finishes.
    scope = (
        verification_scope(self, running_issue_id)
        if producing_state in verify_config(cfg).commands else nullcontext()
    )
    with scope:
        return await _evaluate_stage_gates(
            self, cfg=cfg, running_issue_id=running_issue_id, issue=issue,
            producing_state=producing_state, producing_state_raw=producing_state_raw,
            current_state=current_state, workspace_path=workspace_path,
            known_app_release=known_app_release, running_entry=running_entry,
        )


async def _evaluate_stage_gates(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    running_issue_id: str,
    issue: Issue,
    producing_state: str,
    producing_state_raw: str,
    current_state: str,
    workspace_path: Path,
    known_app_release: bool,
    running_entry: RunningEntry | None = None,
) -> _StageContractOutcome:
    """Evaluate the producing lane's stage contract after a forward move.

        Refreshes the full ticket body, evaluates the preset's contract set,
        and on failure appends `## Contract Failure` and writes the tracker
        state back to the producing lane. Returns the (possibly refreshed
        and rewound) issue, its normalized state, and whether it rewound.
        Configured verify commands also gate custom lanes independently of
        preset contracts. Both gates must pass before the move is accepted.
        """
    # Defensive: the raw label must denote `producing_state`; a caller
    # that hands us the advanced state's casing would make the rewind
    # write the state the ticket is already in (a silent no-op).
    if normalize_state(producing_state_raw) != producing_state:
        producing_state_raw = _canonical_state_label(cfg, producing_state)
    # The deep preset carries its own contract set (vault files +
    # verdict lines); anything else gets the default section set.
    factory = self._is_factory_profile(cfg)
    lane_preset = (
        "factory" if factory else guess_lane_preset(cfg.tracker.active_states)
    )
    verification = verify_config(cfg)
    command = verification.commands.get(producing_state)
    contract_enabled = (
        factory or cfg.agent.stage_contracts_enabled(cfg.tracker.active_states)
    ) and producing_state in contract_producing_states(lane_preset)
    if not command and not contract_enabled:
        return _StageContractOutcome(
            issue=issue,
            current_state=current_state,
            known_app_release=known_app_release,
            rewound=False,
        )
    # IMPORTANT: contract eval reads `issue.description`, so we MUST use
    # the full-body refresh — not the minimal `_refresh_issue_state`,
    # which returns description=None for every tracker adapter and
    # would falsely fail every forward transition. See
    # tests/test_orchestrator_contract_integration.py for the
    # regression the v0.6.7 release surfaced.
    refreshed_for_contract = await self._refresh_issue_full(cfg, running_issue_id)
    authority_entry = running_entry or self._running.get(running_issue_id)
    if refreshed_for_contract is not None:
        issue = refreshed_for_contract
        known_app_release = known_app_release or _has_app_release_label(issue)
        running_entry = self._running.get(running_issue_id)
        if running_entry is not None:
            running_entry.issue = issue
        current_state = normalize_state(issue.state)
    # Refresh can observe an operator's concurrent rewind. Never execute a
    # forward gate against a ticket that is already back in its source lane.
    if command and current_state != producing_state and not _is_rewind_transition(
        producing_state, current_state, cfg.tracker.active_states
    ) and (
        current_state in {normalize_state(s) for s in cfg.tracker.active_states}
        or _is_successful_terminal_state(current_state)
    ):
        result = await asyncio.to_thread(
            run_verify, command, workspace_path, verification.timeout_ms,
            {
                **_branch_hook_env(cfg),
                "SYMPHONY_WORKFLOW_DIR": str(cfg.workflow_path.parent),
                "SYMPHONY_WORKSPACE_ROOT": str(cfg.workspace_root),
                "SYMPHONY_PYTHON": sys.executable,
                "SYMPHONY_ISSUE_ID": issue.id,
                "SYMPHONY_ISSUE_IDENTIFIER": issue.identifier,
                "SYMPHONY_FROM_STATE": producing_state_raw or producing_state,
                "SYMPHONY_TO_STATE": issue.state,
            },
        )
        try:
            await asyncio.to_thread(
                append_verify_trace, cfg, issue.identifier, producing_state,
                current_state, command, result,
            )
        except OSError as exc:
            # A trace write failure must not bypass the authoritative rewind.
            log.warning("verify_trace_write_failed", identifier=issue.identifier, error=str(exc))
        self._record_stats_gate(
            issue.identifier, producing_state,
            "verify_pass" if result.passed else "verify_failure", (command,),
        )
        if not result.passed:
            return await _rewind_failed_gate(
                self, cfg=cfg, running_issue_id=running_issue_id, issue=issue,
                producing_state=producing_state,
                producing_state_raw=producing_state_raw,
                current_state=current_state, known_app_release=known_app_release,
                heading="Verify Failure",
                body=result.failure_note(command, verification.timeout_ms),
            )
    if not contract_enabled:
        return _StageContractOutcome(issue, current_state, known_app_release, False)
    contract = evaluate_contract(
        producing_state=producing_state,
        ticket_body=issue.description or "",
        identifier=issue.identifier,
        docs_root=workspace_path / "docs",
        artifact_store_root=(
            self._artifact_store.root
            if (cfg.artifacts.require_for_done and self._artifact_store is not None)
            else None
        ),
        preset=lane_preset,
        request=issue.request,
        advanced_state=current_state,
        app_release=known_app_release,
        profile="factory" if factory else "advanced",
    )
    if (
        factory
        and current_state in {"verify", "done", "completed"}
        and authority_entry is not None
    ):
        if (
            authority_entry.factory_declared_proof is not None
            and tuple(factory_proof_commands(issue.description or ""))
            != authority_entry.factory_declared_proof
        ):
            contract = _build_result(
                producing_state,
                list(contract.missing)
                + [
                    "declared proof commands changed after this worker started; preserve the approved proof"
                ],
            )
    if factory and _is_successful_terminal_state(current_state):
        missing = list(contract.missing)
        if refreshed_for_contract is None:
            missing.append("the authoritative full ticket could not be refreshed")
        if producing_state == "verify":
            if (
                authority_entry is None
                or not authority_entry.factory_verify_completed
            ):
                missing.append("Done requires a completed independent Verify turn")
            elif (
                factory_verification_text(issue.description or "")
                == authority_entry.factory_verification_baseline
            ):
                missing.append(
                    "Verification is unchanged from before this Verify phase; record fresh output and a UTC timestamp"
                )
            browser = factory_browser_required(
                issue.description or "", issue.skills
            ) or bool(authority_entry and authority_entry.factory_browser_required)
            if browser:
                missing.extend(
                    factory_browser_failures(
                        issue.description or "",
                        workspace_path,
                        authority_entry.factory_verify_started_at
                        if authority_entry
                        else 0.0,
                    )
                )
        if missing:
            contract = _build_result(producing_state, missing)
    if not contract.passed:
        log.warning(
            "stage_contract_failed",
            issue_id=issue.id,
            identifier=issue.identifier,
            producing_state=producing_state,
            advanced_to=current_state,
            missing=contract.missing,
        )
        self._record_stats_gate(
            issue.identifier, producing_state, "contract_failure", contract.missing
        )
        return await _rewind_failed_gate(
            self, cfg=cfg, running_issue_id=running_issue_id, issue=issue,
            producing_state=producing_state, producing_state_raw=producing_state_raw,
            current_state=current_state, known_app_release=known_app_release,
            heading=contract.note_heading, body=contract.note_body,
        )
    if contract.warnings:
        # Soft S2 advisories (e.g. a non-passing AC Scorecard row):
        # surface as a ticket note without rewinding.
        log.warning(
            "stage_contract_warn",
            issue_id=issue.id,
            identifier=issue.identifier,
            producing_state=producing_state,
            advanced_to=current_state,
            warnings=contract.warnings,
        )
        await asyncio.to_thread(
            self._tracker_call_append_note,
            cfg,
            issue,
            "Contract Warning",
            contract.warning_note.split("\n", 1)[1],
        )
    return _StageContractOutcome(
        issue=issue,
        current_state=current_state,
        known_app_release=known_app_release,
        rewound=False,
    )


async def _rewind_failed_gate(
    self: Orchestrator, *, cfg: ServiceConfig, running_issue_id: str,
    issue: Issue, producing_state: str, producing_state_raw: str,
    current_state: str, known_app_release: bool, heading: str, body: str,
) -> _StageContractOutcome:
    """Use the existing contract rewind path and budgets for every hard gate."""
    log.warning(
        "stage_gate_failed", identifier=issue.identifier,
        producing_state=producing_state, advanced_to=current_state, gate=heading,
    )
    await asyncio.to_thread(self._tracker_call_append_note, cfg, issue, heading, body)
    await asyncio.to_thread(
        self._tracker_call_update_state, cfg, issue, producing_state_raw or producing_state,
    )
    refreshed = await self._refresh_issue_full(cfg, running_issue_id)
    issue = replace(refreshed or issue, state=producing_state_raw or producing_state)
    running_entry = self._running.get(running_issue_id)
    if running_entry is not None:
        running_entry.issue = issue
    return _StageContractOutcome(issue, normalize_state(issue.state), known_app_release, True)


def _reset_phase_accounting(
    entry: RunningEntry, state: str, debug: _IssueDebug
) -> None:
    """Start one backend phase without erasing ticket-lifetime totals."""
    entry.last_reported_input_tokens = 0
    entry.last_reported_cache_input_tokens = 0
    entry.last_reported_output_tokens = 0
    entry.last_reported_total_tokens = 0
    entry.codex_state_input_tokens = 0
    entry.codex_state_cache_input_tokens = 0
    entry.codex_state_output_tokens = 0
    entry.codex_state_total_tokens = 0
    entry.last_ema_state_total_tokens = 0
    entry.hit_token_budget = False
    entry.token_budget_cap = 0
    entry.state_at_turn_start = normalize_state(state)
    debug.state_turn_state = normalize_state(state)
    debug.state_turn_count = 0


async def _rebuild_backend_for_phase(
    self: Orchestrator,
    *,
    issue: Issue,
    running_issue_id: str,
    cfg: ServiceConfig,
    workspace_path: Path,
    attempt: int | None,
    doc_language: str,
    old_client: AgentBackend,
    is_rewind: bool,
    turn_number: int,
) -> tuple[AgentBackend, str]:
    """Tear down `old_client` and rebuild a fresh-context backend.

        Returns `(new_client, new_first_prompt)` so the worker loop can
        rebind both. Phase-local usage resets before the new backend can
        emit startup telemetry; ticket-lifetime counters are preserved.
        """
    try:
        await old_client.stop()
    except asyncio.CancelledError:
        running = self._running.get(running_issue_id)
        if running is not None:
            running.backend_cleanup_unconfirmed = True
        log.warning(
            "phase_transition_old_stop_cancelled",
            issue_id=issue.id,
            identifier=issue.identifier,
        )
        raise
    except Exception as stop_exc:
        running = self._running.get(running_issue_id)
        if running is not None:
            running.backend_cleanup_unconfirmed = True
        log.warning(
            "phase_transition_old_stop_failed",
            issue_id=issue.id,
            identifier=issue.identifier,
            error=str(stop_exc),
        )
        raise
    else:
        self._sync_backend_agent_pid(running_issue_id, None)
    running = self._running.get(running_issue_id)
    if running is not None:
        debug = self._issue_debug.setdefault(running_issue_id, _IssueDebug())
        self._reset_phase_accounting(running, issue.state, debug)
        self._start_factory_verify_phase(cfg, issue, running)
    tools: list[Any] = []
    if cfg.tracker.kind == "linear" and cfg.agent.kind == "codex":
        tools.append(linear_graphql_tool())
    # Per-dispatch env rides in BackendInit so the new backend's
    # subprocess sees it. Forward phase transitions omit
    # SYMPHONY_REWIND_SCOPE; rewinds carry the JSON of the latest
    # finding rows.
    new_client = self._build_agent_backend(
        BackendInit(
            cfg=cfg,
            cwd=workspace_path,
            workspace_root=cfg.workspace_root,
            on_event=lambda ev, issue_id=running_issue_id: self._on_codex_event(
                issue_id, ev
            ),
            on_process_started=lambda pid, issue_id=running_issue_id: (
                self._sync_backend_agent_pid(issue_id, pid)
            ),
            on_process_identified=lambda pid, identity, issue_id=running_issue_id: (
                self._sync_backend_agent_pid(
                    issue_id,
                    pid,
                    backend_process_identity=identity,
                    identity_captured=True,
                )
            ),
            client_tools=tools,
            env=self._dispatch_env_for(issue=issue, cfg=cfg, is_rewind=is_rewind),
            process_launch_guard=self._pm_process_launch_guard(
                running_issue_id, issue, cfg, workspace_path
            ),
            _require_durable_process=True,
        )
    )
    try:
        self._sync_backend_agent_pid(
            running_issue_id, _backend_agent_pid(new_client)
        )
        try:
            await new_client.start()
        finally:
            self._sync_backend_agent_pid(
                running_issue_id, _backend_agent_pid(new_client)
            )
        await new_client.initialize()
        skill_context = await asyncio.to_thread(
            self._render_stage_skills, cfg, issue, workspace_path
        )
        board_health = ""
        if self._board_health_wanted(issue.state):
            board_health = await asyncio.to_thread(
                self._board_health_for_prompt, cfg, issue.state
            )
        first_prompt, _ = build_first_turn_prompt(
            prompt_template=cfg.prompt_template_for_state(issue.state),
            issue=issue,
            attempt=attempt,
            language=doc_language,
            turn_number=turn_number,
            max_turns=cfg.agent.max_total_turns,
            max_attempts=cfg.agent.max_attempts,
            is_rewind=is_rewind,
            auto_merge_on_done=cfg.agent.auto_merge_on_done,
            token_ema=self._token_ema_for_state(issue.state),
            token_budget=self._token_budget_for_state(cfg, issue.state),
            rewind_scope=(
                _parse_findings_rows(issue.description) if is_rewind else None
            ),
            compact_issue_context=cfg.agent.compact_issue_context,
            full_ticket_path=self._ticket_prompt_path(cfg, issue, workspace_path),
            artifacts_dir=self._prompt_artifacts_dir(cfg),
            extra_context=skill_context,
            board_health=board_health,
        )
        await new_client.start_session(
            initial_prompt=first_prompt,
            issue_title=f"{issue.identifier}: {issue.title}",
        )
    except BaseException:
        try:
            await new_client.stop()
        except asyncio.CancelledError:
            running = self._running.get(running_issue_id)
            if running is not None:
                running.backend_cleanup_unconfirmed = True
            log.warning(
                "phase_transition_new_stop_cancelled",
                issue_id=issue.id,
                identifier=issue.identifier,
            )
            raise
        except Exception as stop_exc:
            running = self._running.get(running_issue_id)
            if running is not None:
                running.backend_cleanup_unconfirmed = True
            log.warning(
                "phase_transition_new_stop_failed",
                issue_id=issue.id,
                identifier=issue.identifier,
                error=str(stop_exc),
            )
        else:
            self._sync_backend_agent_pid(running_issue_id, None)
        raise
    return new_client, first_prompt


async def _refresh_issue_state(
    self: Orchestrator, cfg: ServiceConfig, issue_id: str
) -> Issue | None:
    try:
        results = await asyncio.to_thread(
            self._tracker_call_states_by_ids, cfg, [issue_id]
        )
    except Exception as exc:
        log.warning("issue_state_refresh_failed", issue_id=issue_id, error=str(exc))
        self._record_tracker_error(issue_id, exc)
        return None
    for issue in results:
        if issue.id == issue_id:
            self._clear_tracker_error(issue_id)
            return issue
    return None


async def _refresh_issue_full(
    self: Orchestrator, cfg: ServiceConfig, issue_id: str
) -> Issue | None:
    """Refresh an issue with its full body (description) from the tracker.

        `_refresh_issue_state` returns the *minimal* Issue payload — fast
        but strips description. The stage-contract validator (v0.6.7+)
        needs the live body to evaluate required-section presence, so the
        forward-transition path uses this helper instead. Returns None on
        transport failure or missing id; callers must keep the prior
        in-memory issue in that case (do NOT replace it with None).
        """
    try:
        issue = await asyncio.to_thread(
            self._tracker_call_full_by_id, cfg, issue_id
        )
        self._clear_tracker_error(issue_id)
        return issue
    except Exception as exc:
        log.warning("issue_full_refresh_failed", issue_id=issue_id, error=str(exc))
        self._record_tracker_error(issue_id, exc)
        return None


async def _persist_budget_exhausted_state(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    entry: RunningEntry,
    issue_id: str,
    target_state: str,
    budget_kind: str,
    state_turn_limit: int | None = None,
) -> bool:
    if not target_state:
        return False
    if budget_kind == "tokens":
        budget_detail = (
            f"({entry.codex_state_total_tokens}/"
            f"{entry.token_budget_cap or cfg.agent.max_total_tokens})"
        )
    elif budget_kind == "max_turns":
        budget_detail = f"(max_turns={cfg.agent.max_turns}/attempt)"
    elif budget_kind == "empty_response_loop":
        budget_detail = (
            f"(consecutive_empty_turns={entry.consecutive_empty_turns}, "
            f"threshold={EMPTY_TURN_LOOP_THRESHOLD})"
        )
    elif budget_kind == "no_stage_change":
        debug = self._issue_debug.get(issue_id)
        count = debug.state_turn_count if debug is not None else 0
        state_name = entry.issue.state
        if not state_name and debug is not None:
            state_name = debug.state_turn_state
        limit = (
            state_turn_limit
            if state_turn_limit is not None
            else self._max_state_turns_for_state(cfg, state_name)
        )
        budget_detail = f"(state_turns={count}, effective_max_state_turns={limit})"
    else:
        budget_detail = f"(max_total_turns={cfg.agent.max_total_turns})"
    note_body = (
        f"{budget_kind} budget exceeded {budget_detail} while state stayed "
        f"{entry.issue.state}. Symphony moved this ticket to {target_state} "
        f"to prevent automatic re-dispatch."
    )
    try:
        await asyncio.to_thread(
            self._tracker_call_update_state,
            cfg,
            entry.issue,
            target_state,
        )
        await asyncio.to_thread(
            self._tracker_call_append_note,
            cfg,
            entry.issue,
            "Budget Exceeded",
            note_body,
        )
        log.info(
            "budget_exhausted_persisted",
            issue_id=issue_id,
            issue_identifier=entry.issue.identifier,
            target_state=target_state,
            budget_kind=budget_kind,
        )
        self._clear_tracker_error(issue_id)
        return True
    except Exception as persist_exc:
        # Lenient: the in-memory guard still prevents another dispatch in
        # this process; the log explains why restart persistence failed.
        log.warning(
            "budget_exhausted_persist_failed",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
            target_state=target_state,
            budget_kind=budget_kind,
            error=str(persist_exc),
        )
        self._record_tracker_error(issue_id, persist_exc)
        return False


async def _escalate_empty_response_loop(
    self: Orchestrator,
    *,
    cfg: ServiceConfig | None,
    entry: RunningEntry,
    issue_id: str,
    cancel_worker: bool,
) -> None:
    log.warning(
        "empty_response_loop",
        issue_id=issue_id,
        identifier=entry.issue.identifier,
        consecutive_empty_turns=entry.consecutive_empty_turns,
        threshold=EMPTY_TURN_LOOP_THRESHOLD,
    )
    debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
    debug.last_error = (
        f"empty_response_loop after "
        f"{entry.consecutive_empty_turns} consecutive empty turns"
    )
    if cfg is not None:
        await self._persist_budget_exhausted_state(
            cfg=cfg,
            entry=entry,
            issue_id=issue_id,
            target_state=cfg.agent.budget_exhausted_state,
            budget_kind="empty_response_loop",
        )
    # G2 — auto-pause the ticket so dispatch + retry both refuse to
    # restart it even when `budget_exhausted_state` is unset (the
    # persist branch above is a no-op then). The pause survives worker
    # exit via `_paused_issue_ids` and is the same gate the operator's
    # manual pause uses, so resume_worker() lifts it.
    if issue_id not in self._paused_issue_ids:
        pause_reason = (
            f"empty_response_loop: {entry.consecutive_empty_turns} "
            f"consecutive empty turns "
            f"(threshold {EMPTY_TURN_LOOP_THRESHOLD}); resume via "
            "resume_worker after inspecting the ticket"
        )
        self._paused_issue_ids.add(issue_id)
        self._pause_reasons[issue_id] = pause_reason
        self._set_issue_flags(
            issue_id,
            paused=True,
            pause_reason=pause_reason,
        )
        pause_event = self._pause_events.get(issue_id)
        if pause_event is None:
            pause_event = asyncio.Event()
            self._pause_events[issue_id] = pause_event
        pause_event.clear()
        log.info(
            "empty_response_loop_auto_paused",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
        )
    entry.hit_empty_response_loop = False
    if cancel_worker and entry.worker_task is not None:
        entry.worker_task.cancel()
    entry.cancelled_at = datetime.now(timezone.utc)


# ------------------------------------------------------------------
# codex events
# ------------------------------------------------------------------

def _token_cap_for_entry(cfg: ServiceConfig | None, entry: RunningEntry) -> int:
    if cfg is None:
        return 0
    state = normalize_state(entry.state_at_turn_start or entry.issue.state)
    by_state = cfg.agent.max_total_tokens_by_state
    cap = by_state.get(state)
    if cap is None and state == "learn":
        cap = by_state.get("learning")
    if cap is None and state == "learning":
        cap = by_state.get("learn")
    return cap if cap is not None else cfg.agent.max_total_tokens


def _preview_from_payload(payload: dict[str, Any]) -> str:
    for key in ("message", "lastMessage", "text", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    def assistant_message_preview(message: Any) -> str:
        """Extract text only from an assistant message.

            Pi and Prime wrap response text in a message object.  The same
            stream also contains user messages and tool results, so requiring
            an assistant role here keeps those echoes from looking like model
            output to the empty-turn guard.
            """
        if not isinstance(message, dict):
            return ""
        kind = str(message.get("role") or message.get("type") or "").lower()
        if kind != "assistant":
            return ""
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict):
                    text = block.get("text")
                else:
                    text = block
                if isinstance(text, str) and text.strip():
                    return text.strip()
        elif isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        text = message.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        return ""

    # Pi/Prime `message_end` and `turn_end` payloads nest the assistant
    # message under `message`.
    message = payload.get("message")
    preview = assistant_message_preview(message)
    if preview:
        return preview

    # Their terminal `agent_end` payload carries all messages.  Walk
    # backwards so the preview reflects the final assistant response,
    # while skipping user messages and tool-result entries.
    if str(payload.get("type") or "").lower() == "agent_end":
        messages = payload.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                preview = assistant_message_preview(message)
                if preview:
                    return preview

    item = payload.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type") or "").lower()
        text = item.get("text") or item.get("message")
        if isinstance(text, str) and text.strip():
            return text.strip()
        name = item.get("name") or item.get("tool") or item.get("command")
        args = item.get("arguments")
        command = ""
        if isinstance(args, dict):
            raw_cmd = args.get("cmd") or args.get("command")
            if isinstance(raw_cmd, str):
                command = raw_cmd.strip()
        if name and ("tool" in item_type or command):
            suffix = f" {command}" if command else ""
            return f"tool: {name}{suffix}".strip()
    return ""


async def _on_codex_event(self: Orchestrator, issue_id: str, event: dict[str, Any]) -> None:
    entry = self._running.get(issue_id)
    if entry is None:
        return
    ev_name = str(event.get("event") or "")
    if ev_name != EVENT_SESSION_STARTED and entry.resume_session_id:
        sanitized = redact_session_id(event, entry.resume_session_id)
        if isinstance(sanitized, dict):
            event = sanitized
    entry.last_codex_event = ev_name
    ts_text = event.get("timestamp")
    if isinstance(ts_text, str):
        try:
            entry.last_codex_timestamp = datetime.fromisoformat(
                ts_text.replace("Z", "+00:00")
            )
        except ValueError:
            entry.last_codex_timestamp = datetime.now(timezone.utc)
    else:
        entry.last_codex_timestamp = datetime.now(timezone.utc)
    raw_pid = (
        event.get("agent_pid")
        if "agent_pid" in event
        else event.get("codex_app_server_pid")
    )
    pid = _normalize_agent_pid(raw_pid)
    if pid is not None:
        self._sync_backend_agent_pid(issue_id, pid)
    payload = event.get("payload") or {}
    if isinstance(payload, dict):
        msg = self._preview_from_payload(payload)
        if msg:
            entry.last_codex_message = msg[:400]
            # G2 — per-turn buffer tracks any preview that arrived during
            # this turn. Cleared on EVENT_TURN_COMPLETED after the
            # empty-loop check so the next turn starts fresh.
            entry.current_turn_message = msg[:400]
        if ev_name == EVENT_APPROVAL_DENIED:
            command = str(payload.get("command") or "")
            reason = str(payload.get("reason") or "approval denied")
            debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
            if command:
                debug.last_error = f"approval denied: {reason} ({command})"
            else:
                debug.last_error = f"approval denied: {reason}"
            self._append_run_event(
                entry, "approval_denied", {"reason": reason}
            )
            log.warning(
                "approval_denied",
                issue_id=issue_id,
                identifier=entry.issue.identifier,
                command=command,
                reason=reason,
            )
    # Token deltas (§13.5).
    usage = event.get("usage") or {}
    delta_out = 0
    if isinstance(usage, dict):
        _, delta_out = self._apply_token_totals(entry, usage)
    # Hard token-budget cap. Catches the runaway-reasoning case the
    # stall predicate can't see: codex completes each turn but
    # accumulates 1.6M tokens per turn (history re-send) and burns
    # through dozens of megatokens before max_turns ends the attempt.
    # 0 = disabled (legacy default). On breach: cancel the worker and
    # record the reason so the operator finds out without log-diving.
    cfg = self._workflow_state.current()
    cap = self._token_cap_for_entry(cfg, entry)
    budget_state = entry.state_at_turn_start or entry.issue.state
    if (
        cap > 0
        and entry.cancelled_at is None
        and entry.codex_state_total_tokens >= cap
    ):
        log.warning(
            "token_budget_exceeded",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
            state_total_tokens=entry.codex_state_total_tokens,
            total_tokens=entry.codex_total_tokens,
            cap=cap,
            state=budget_state,
        )
        debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
        debug.last_error = (
            f"token budget exceeded "
            f"({entry.codex_state_total_tokens}/{cap} in {budget_state}) "
            "— worker cancelled"
        )
        entry.hit_token_budget = True
        entry.token_budget_cap = cap
        if entry.worker_task is not None:
            entry.worker_task.cancel()
        entry.cancelled_at = datetime.now(timezone.utc)
    # Progress predicate — see RunningEntry.last_progress_timestamp.
    # `EVENT_OTHER_MESSAGE` is a catch-all that the claude backend fires
    # for both `assistant` (real model output) and `user` (tool_result
    # echo) stream-json messages. Treating every one as progress lets
    # the 5-min stall threshold get reset by tool_result echoes alone,
    # so a turn that produces no model output for 18 min still looks
    # alive. Filter: lifecycle events count, OUTPUT token movement
    # counts, and `EVENT_OTHER_MESSAGE` counts only when the payload's
    # `type` is `assistant` (matches claude_code stream-json shape;
    # harmless for other backends that don't set `type`).
    #
    # NOTE on `delta_out` (not `delta_total`): codex app-server attaches
    # `_latest_usage` to every emitted event, including catch-all
    # `EVENT_OTHER_MESSAGE` frames between turns. Codex inflates
    # `input_tokens` by re-sending conversation history each turn, so
    # `delta_total > 0` is true even when the model has produced no
    # output — that masked a real 18-turn / 30M-token reasoning loop
    # (IB-006, 2026-05-16). `output_tokens` only advances when the
    # model actually emits content, which is the signal we need.
    is_progress = ev_name != EVENT_OTHER_MESSAGE
    if not is_progress and isinstance(payload, dict):
        # Delegate the catch-all OTHER_MESSAGE filter to the backend so
        # per-driver echo shapes (claude stream-json `user`/tool_result
        # frames, codex preview items, future backends with their own
        # keepalive types) live next to the code that knows their wire
        # protocol. Claude and codex both narrow to `type=="assistant"`;
        # pi/gemini inherit the conservative `BaseAgentBackend` default
        # of always-True. When the backend reference isn't published
        # yet (e.g. unit tests poking `_on_codex_event` directly without
        # a build_backend call), apply the historical inline filter so
        # existing invariants hold.
        backend = entry.client
        if backend is None:
            is_progress = payload.get("type") == "assistant"
        else:
            is_progress = backend.is_progress_event(payload)
    if delta_out > 0:
        is_progress = True
    if is_progress:
        entry.last_progress_timestamp = entry.last_codex_timestamp
        self._heartbeat_run_lease(
            issue_id,
            entry,
            progress=entry.last_progress_timestamp,
        )
    # Rate limits.
    rl = event.get("rate_limits")
    if isinstance(rl, dict):
        self._latest_rate_limits = rl
    # Update session id when known. The backend reports a single session
    # identifier; this orchestrator stores it as `thread_id` for legacy
    # snapshot-shape stability and mirrors it as `session_id`. Codex
    # additionally exposes per-turn ids; when present they suffix the
    # session id so consumers can distinguish turns. Non-Codex backends
    # never set `turn_id`, so the suffix is silently skipped for them.
    if ev_name == EVENT_SESSION_STARTED:
        sid = (
            (
                payload.get("session_id")
                or payload.get("thread_id")
                or payload.get("threadId")
            )
            if isinstance(payload, dict)
            else None
        )
        if sid:
            entry.thread_id = str(sid)
            entry.session_id = entry.thread_id
            entry.resume_session_id = entry.thread_id
        log.info(
            "agent_session_started",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
        )
        self._append_run_event(entry, "session_started")
    if ev_name == EVENT_TURN_COMPLETED:
        entry.last_completed_turn_event = max(
            entry.last_completed_turn_event, entry.turn_count
        )
        turn_id = payload.get("turnId") or payload.get("turn_id")
        if turn_id and entry.thread_id:
            entry.turn_id = str(turn_id)
            entry.session_id = f"{entry.thread_id}-{entry.turn_id}"
        log.info(
            "agent_turn_completed",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
            turn=entry.turn_count,
            input_tokens=entry.codex_input_tokens,
            cache_input_tokens=entry.codex_cache_input_tokens,
            output_tokens=entry.codex_output_tokens,
            total_tokens=entry.codex_total_tokens,
            last_message=(entry.last_codex_message or "")[:160],
        )
        self._append_run_event(
            entry,
            "turn_completed",
            {
                "turn": entry.turn_count,
                "input_tokens": entry.codex_input_tokens,
                "cache_input_tokens": entry.codex_cache_input_tokens,
                "output_tokens": entry.codex_output_tokens,
                "total_tokens": entry.codex_total_tokens,
            },
        )
        self._record_token_attention_for_turn(entry, cfg)
        self._record_stats_turn(entry)
        # C3 — adaptive token budget. Sample = per-turn state-local
        # total tokens. `_update_token_ema` no-ops on non-positive
        # samples, so a turn with zero token movement (e.g. an event
        # that fires before any usage is reported) is skipped
        # silently rather than dragging the EMA toward zero.
        turn_sample = max(
            entry.codex_state_total_tokens - entry.last_ema_state_total_tokens,
            0,
        )
        if turn_sample > 0:
            # Prefer the source state (captured at worker_turn_started)
            # so a stage that flipped the ticket mid-turn still has its
            # cost attributed correctly. Fall back to current state for
            # event-injection unit tests that bypass turn_started.
            target_state = entry.state_at_turn_start or entry.issue.state
            self._update_token_ema(target_state, turn_sample, cfg)
            entry.last_ema_state_total_tokens = entry.codex_state_total_tokens
        # G2 — empty-response loop guard. A turn whose `current_turn_message`
        # stayed empty produced no fresh preview text. Counter resets on a
        # turn with real preview; crossing the threshold cancels the worker
        # and persists via the existing budget-exhausted plumbing.
        if entry.current_turn_message.strip():
            entry.consecutive_empty_turns = 0
            entry.hit_empty_response_loop = False
        else:
            entry.consecutive_empty_turns += 1
        entry.current_turn_message = ""
        if (
            entry.consecutive_empty_turns >= EMPTY_TURN_LOOP_THRESHOLD
            and entry.cancelled_at is None
        ):
            if entry.state_at_turn_start:
                entry.hit_empty_response_loop = True
                log.warning(
                    "empty_response_loop_pending",
                    issue_id=issue_id,
                    identifier=entry.issue.identifier,
                    consecutive_empty_turns=entry.consecutive_empty_turns,
                    threshold=EMPTY_TURN_LOOP_THRESHOLD,
                    state_at_turn_start=entry.state_at_turn_start,
                )
            else:
                await self._escalate_empty_response_loop(
                    cfg=cfg,
                    entry=entry,
                    issue_id=issue_id,
                    cancel_worker=True,
                )
    if ev_name == EVENT_TURN_FAILED:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        stderr_tail = (
            payload.get("stderr_tail") if isinstance(payload, dict) else None
        )
        log.warning(
            "agent_turn_failed",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
            turn=entry.turn_count,
            reason=str(reason) if reason else "",
            stderr_tail=stderr_tail if isinstance(stderr_tail, list) else None,
        )
        self._append_run_event(
            entry,
            "turn_failed",
            {
                "turn": entry.turn_count,
                "reason": reason,
                "stderr_lines": stderr_tail,
            },
        )
    if ev_name == EVENT_COMPACTION:
        phase = payload.get("phase") if isinstance(payload, dict) else None
        compaction_reason = (
            payload.get("reason") if isinstance(payload, dict) else None
        )
        tokens_before = (
            payload.get("tokens_before") if isinstance(payload, dict) else None
        )
        log.info(
            "agent_compaction",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
            phase=str(phase) if phase else "",
            reason=str(compaction_reason or ""),
            tokens_before=tokens_before,
        )
        self._append_run_event(
            entry,
            "compaction",
            {
                "phase": phase,
                "reason": compaction_reason,
                "tokens_before": tokens_before,
            },
        )
    if ev_name == EVENT_AGENT_RETRY:
        phase = payload.get("phase") if isinstance(payload, dict) else None
        retry_attempt = payload.get("attempt") if isinstance(payload, dict) else None
        retry_error = (
            payload.get("error") or payload.get("final_error")
            if isinstance(payload, dict)
            else None
        )
        log.info(
            "agent_internal_retry",
            issue_id=issue_id,
            identifier=entry.issue.identifier,
            phase=str(phase) if phase else "",
            attempt=retry_attempt,
            error=str(retry_error or ""),
        )
        self._append_run_event(
            entry,
            "retry",
            {
                "phase": phase,
                "attempt": retry_attempt,
                "error": retry_error,
            },
        )

    # Track recent events.
    debug = self._issue_debug.setdefault(issue_id, _IssueDebug())
    debug.recent_events.append(
        {
            "at": ts_text or _utc_iso_z(),
            "event": ev_name,
            "message": entry.last_codex_message,
        }
    )
    if len(debug.recent_events) > 50:
        debug.recent_events = debug.recent_events[-50:]


def _apply_token_totals(
    self: Orchestrator, entry: RunningEntry, totals: dict[str, Any]
) -> tuple[int, int]:
    in_tok = int(totals.get("input_tokens") or 0)
    cache_tok = int(totals.get("cache_input_tokens") or 0)
    out_tok = int(totals.get("output_tokens") or 0)
    tot_tok = int(totals.get("total_tokens") or (in_tok + cache_tok + out_tok))
    # §13.5 — track deltas from last reported absolute totals.
    delta_in = max(in_tok - entry.last_reported_input_tokens, 0)
    delta_cache = max(cache_tok - entry.last_reported_cache_input_tokens, 0)
    delta_out = max(out_tok - entry.last_reported_output_tokens, 0)
    delta_total = max(tot_tok - entry.last_reported_total_tokens, 0)
    entry.last_reported_input_tokens = in_tok
    entry.last_reported_cache_input_tokens = cache_tok
    entry.last_reported_output_tokens = out_tok
    entry.last_reported_total_tokens = tot_tok
    entry.codex_input_tokens += delta_in
    entry.codex_cache_input_tokens += delta_cache
    entry.codex_output_tokens += delta_out
    entry.codex_total_tokens += delta_total
    entry.codex_state_input_tokens += delta_in
    entry.codex_state_cache_input_tokens += delta_cache
    entry.codex_state_output_tokens += delta_out
    entry.codex_state_total_tokens += delta_total
    self._totals.input_tokens += delta_in
    self._totals.cache_input_tokens += delta_cache
    self._totals.output_tokens += delta_out
    self._totals.total_tokens += delta_total
    return delta_total, delta_out


# ------------------------------------------------------------------
# run-stats recording (stats.jsonl — feeds the stats page / TUI screen)
# ------------------------------------------------------------------

def _record_stats_turn(self: Orchestrator, entry: RunningEntry) -> None:
    if self._stats is None:
        return
    delta_in = entry.codex_input_tokens - entry.stats_input_tokens
    delta_cache = entry.codex_cache_input_tokens - entry.stats_cache_input_tokens
    delta_out = entry.codex_output_tokens - entry.stats_output_tokens
    delta_total = entry.codex_total_tokens - entry.stats_total_tokens
    entry.stats_input_tokens = entry.codex_input_tokens
    entry.stats_cache_input_tokens = entry.codex_cache_input_tokens
    entry.stats_output_tokens = entry.codex_output_tokens
    entry.stats_total_tokens = entry.codex_total_tokens
    self._stats.record_turn(
        issue=entry.issue.identifier,
        state=entry.state_at_turn_start or normalize_state(entry.issue.state),
        agent=self._entry_agent_kind(entry),
        input_tokens=max(delta_in, 0),
        cache_tokens=max(delta_cache, 0),
        output_tokens=max(delta_out, 0),
        total_tokens=max(delta_total, 0),
    )


def _record_stats_transition(
    self: Orchestrator, identifier: str, from_state: str, to_state: str
) -> None:
    if self._stats is None:
        return
    self._stats.record_transition(
        issue=identifier,
        from_state=normalize_state(from_state),
        to_state=normalize_state(to_state),
    )


def _record_stats_gate(
    self: Orchestrator, identifier: str, state: str, kind: str, items: list[str] | tuple[str, ...] = ()
) -> None:
    """A gate decision (contract failure / reopen hold / backend fallback)."""
    if self._stats is None:
        return
    self._stats.record_gate(
        issue=identifier, state=normalize_state(state), kind=kind, items=items
    )


def _board_health_wanted(self: Orchestrator, state: str | None) -> bool:
    """Cheap synchronous pre-check so non-Document lanes never pay a thread hop.

        `_open_session` runs on the worker's first turn; an unconditional
        `asyncio.to_thread` there changed the dispatch timing enough to
        break the eager-task dispatch test, so only the write-back lane
        crosses into a thread to aggregate `stats.jsonl`.
        """
    return self._stats is not None and normalize_state(state) in _BOARD_HEALTH_LANES


def _board_health_for_prompt(self: Orchestrator, cfg: ServiceConfig, state: str | None) -> str:
    """`{{ board_health }}` for the write-back lane; "" elsewhere.

        Only the Document lane (and its legacy `learn` name) receives the
        summary: it is the lane that turns repeat failures into wiki
        lessons, and every other lane would just pay tokens for it.
        """
    stats = self._stats
    if stats is None or not self._board_health_wanted(state):
        return ""
    try:
        terminal = {s.lower() for s in cfg.tracker.terminal_states}
        done_states = {"done"} if "done" in terminal else terminal
        aggregate = stats.aggregate(
            30, done_states, active_states=cfg.tracker.active_states
        )
        return board_health_summary(aggregate)
    except Exception as exc:
        log.warning("board_health_failed", error=str(exc))
        return ""
