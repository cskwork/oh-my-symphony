"""Release gates, finalizer guards, and release-transition authority for ``Orchestrator``.

Each method is a module-level function whose first parameter is
``self: Orchestrator``. ``core.Orchestrator`` binds a delegate for every name in
``symphony.orchestrator.release_seam.AUTHORITY_METHODS`` that calls the
function here when this package is installed, so ``self.<name>(...)`` calls and
``Orchestrator.<name>`` patches behave as before the split.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, cast

from symphony.errors import SymphonyError
from symphony.issue import Issue, normalize_state
from symphony.workflow import ServiceConfig
from symphony.release.contracts import (
    ReleaseValidationResult,
    release_workspace_target_errors,
    resolve_target_release_identity,
)
from symphony.release.cycle import (
    ReleaseCycleService,
    ReleaseCycleWriteResult,
    has_active_verify_lane as _has_active_release_verify_lane,
    has_release_finalizer_lane as _has_release_finalizer_lane,
    initial_release_gate_fingerprint as _initial_release_gate_fingerprint,
    is_release_success_state as _is_release_success_state,
    is_release_evidence_issue as _is_release_evidence_issue,
    is_release_finalizer as _is_release_finalizer,
    release_ticket_version_token as _release_ticket_version_token,
    release_verifier_state as _release_verifier_state,
)
from symphony.orchestrator.entries import RunningEntry
from symphony.orchestrator.run_registry import ReleaseEvidenceIdentity, ReleaseGate, RunRegistry
from symphony.release import transition as release_transition
from symphony.orchestrator.core_support import _has_app_release_label
from symphony.orchestrator.release_seam import _ReleaseDispatchAuthority
from symphony.logging import get_logger

if TYPE_CHECKING:
    from symphony.orchestrator.core import Orchestrator

log = get_logger()


class _ReleaseTransitionAuthorityLost(SymphonyError):
    """A stale release worker must exit without mutating its replacement."""


_APP_RELEASE_FILE_TRACKER_ONLY = (
    "app-release verifier execution requires tracker.kind=file; remote labeled "
    "tickets are refused before agent execution"
)


def _pending_release_gate(
    *, issue: Issue, finalizer: str, contract_sha256: str
) -> ReleaseGate:
    return ReleaseGate(
        finalizer_identifier=finalizer,
        verifier_issue_id=issue.id,
        verifier_identifier=issue.identifier,
        expected_contract_sha256=contract_sha256,
        cycle_fingerprint=_initial_release_gate_fingerprint(
            verifier_identifier=issue.identifier,
            finalizer_identifier=finalizer,
            contract_sha256=contract_sha256,
        ),
        approved_fingerprint=None,
        status="pending",
        target_branch=None,
        approved_target_sha=None,
        verifier_run_id=None,
        finalizer_run_id=None,
        finalizer_completed_at=None,
        finalizer_completion_token=None,
        updated_at=datetime.now(timezone.utc),
    )


def _persist_pending_release_gate(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    gate: ReleaseGate,
    operation: str,
    invalidating_finalizer_run_id: str | None = None,
) -> ReleaseGate:
    """Replace approval with PENDING and prove the authoritative tuple."""
    written = cast(
        ReleaseGate,
        self._release_registry_call(
            cfg,
            operation,
            lambda registry: registry.replace_pending_release_gate(
                gate,
                invalidating_finalizer_run_id=(invalidating_finalizer_run_id),
            ),
        ),
    )
    persisted = cast(
        ReleaseGate | None,
        self._release_registry_call(
            cfg,
            f"{operation}_readback",
            lambda registry: registry.get_release_gate(gate.finalizer_identifier),
        ),
    )
    expected = (
        gate.finalizer_identifier,
        gate.verifier_issue_id,
        gate.verifier_identifier,
        gate.expected_contract_sha256,
        gate.cycle_fingerprint,
        written.generation,
        "pending",
    )
    actual = (
        (
            persisted.finalizer_identifier,
            persisted.verifier_issue_id,
            persisted.verifier_identifier,
            persisted.expected_contract_sha256,
            persisted.cycle_fingerprint,
            persisted.generation,
            persisted.status,
        )
        if persisted is not None
        else None
    )
    if actual != expected:
        raise SymphonyError(
            "application release authority was not durably persisted",
            verifier=gate.verifier_identifier,
            finalizer=gate.finalizer_identifier,
        )
    if not written.generation:
        raise SymphonyError(
            "application release authority lacks a durable cycle generation",
            verifier=gate.verifier_identifier,
        )
    return cast(ReleaseGate, persisted)


def _create_initial_release_gate(
    self: Orchestrator, cfg: ServiceConfig, issue: Issue
) -> tuple[Issue, ReleaseGate]:
    if not _has_active_release_verify_lane(cfg) or not _has_release_finalizer_lane(
        cfg
    ):
        raise SymphonyError(
            "app-release requires active Verify and finalizer lanes before dispatch"
        )
    identity = resolve_target_release_identity(
        repository_root=cfg.workflow_path.parent,
        configured_target_branch=cfg.agent.auto_merge_target_branch,
    )
    if identity.errors:
        raise SymphonyError(
            "cannot bind initial application release verifier",
            errors=list(identity.errors),
        )
    finalizer = self._tracker_call_fetch_issue_full_by_id(
        cfg, identity.finalizer_ticket
    )
    if finalizer is None or not _is_release_finalizer(finalizer):
        raise SymphonyError(
            "release contract finalizer is missing or lacks app-release-finalizer",
            verifier=issue.identifier,
            finalizer=identity.finalizer_ticket,
        )
    blocker_ids = {
        blocker.identifier or blocker.id
        for blocker in finalizer.blocked_by
        if blocker.identifier or blocker.id
    }
    if issue.identifier not in blocker_ids:
        raise SymphonyError(
            "release finalizer must be blocked by its verifier before dispatch",
            verifier=issue.identifier,
            finalizer=identity.finalizer_ticket,
        )
    gate = self._pending_release_gate(
        issue=issue,
        finalizer=identity.finalizer_ticket,
        contract_sha256=identity.contract_sha256,
    )
    persisted = self._persist_pending_release_gate(
        cfg=cfg,
        gate=gate,
        operation="replace_initial_pending_gate",
    )
    if normalize_state(finalizer.state) in {
        normalize_state(state) for state in cfg.tracker.terminal_states
    }:
        ReleaseCycleService(cfg).reopen_after_target_change(
            finalizer=finalizer,
            gate=persisted,
            expected_contract_sha256=persisted.expected_contract_sha256,
            reason="initial release verification has not yet been approved",
            finalizer_state=next(
                (
                    state
                    for state in reversed(cfg.tracker.active_states)
                    if normalize_state(state) != "verify"
                ),
                None,
            ),
        )
    restored = ReleaseCycleService(cfg).restore_verifier_gate_labels(
        issue=issue,
        gate=persisted,
        verifier_state=_release_verifier_state(cfg),
    )
    return restored, persisted


def _reopen_stale_release_gate(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    finalizer: Issue,
    gate: ReleaseGate,
    reason: str,
    current_contract_sha256: str = "",
    finalizer_state: str | None = None,
    invalidating_finalizer_run_id: str | None = None,
) -> None:
    expected_hash = current_contract_sha256 or gate.expected_contract_sha256
    pending = replace(
        gate,
        expected_contract_sha256=expected_hash,
        cycle_fingerprint=_initial_release_gate_fingerprint(
            verifier_identifier=gate.verifier_identifier,
            finalizer_identifier=gate.finalizer_identifier,
            contract_sha256=expected_hash,
        ),
        approved_fingerprint=None,
        status="pending",
        target_branch=None,
        approved_target_sha=None,
        verifier_run_id=None,
        updated_at=datetime.now(timezone.utc),
    )
    pending = self._persist_pending_release_gate(
        cfg=cfg,
        gate=pending,
        operation="invalidate_stale_approval",
        invalidating_finalizer_run_id=invalidating_finalizer_run_id,
    )
    ReleaseCycleService(cfg).reopen_after_target_change(
        finalizer=finalizer,
        gate=pending,
        expected_contract_sha256=expected_hash,
        reason=reason,
        finalizer_state=finalizer_state,
    )


def _guard_release_finalizer(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    gate: ReleaseGate,
    rewind_state: str | None = None,
    expected_run_id: str | None = None,
    allow_active_run: bool = True,
    require_run_authority: bool = False,
) -> Issue:
    persisted_issue = self._tracker_call_fetch_issue_full_by_id(
        cfg, issue.identifier
    )
    if persisted_issue is None:
        raise SymphonyError(
            "application release finalizer could not be read",
            finalizer=issue.identifier,
        )
    issue = persisted_issue
    terminal_states = {
        normalize_state(state) for state in cfg.tracker.terminal_states
    }
    if normalize_state(
        issue.state
    ) in terminal_states and not _is_release_success_state(cfg, issue.state):
        raise SymphonyError(
            "application release finalizer is in a non-success terminal state",
            finalizer=issue.identifier,
            state=issue.state,
        )
    if gate.status != "approved":
        raise SymphonyError(
            "application release finalizer is waiting for GREEN verification",
            finalizer=issue.identifier,
            verifier=gate.verifier_identifier,
            status=gate.status,
        )
    blocker_ids = {
        blocker.identifier or blocker.id
        for blocker in issue.blocked_by
        if blocker.identifier or blocker.id
    }
    if gate.verifier_identifier not in blocker_ids:
        raise SymphonyError(
            "application release finalizer is not bound to its approved verifier",
            finalizer=issue.identifier,
            verifier=gate.verifier_identifier,
        )
    verifier = self._tracker_call_fetch_issue_full_by_id(
        cfg, gate.verifier_identifier
    )
    if (
        verifier is None
        or verifier.id != gate.verifier_issue_id
        or not _is_release_success_state(cfg, verifier.state)
    ):
        raise SymphonyError(
            "application release finalizer verifier is not successfully terminal",
            finalizer=issue.identifier,
            verifier=gate.verifier_identifier,
            verifier_state=verifier.state if verifier is not None else "missing",
        )
    peer_active = bool(
        self._release_registry_call(
            cfg,
            "check_verifier_lease",
            lambda registry: registry.has_active_lease(gate.verifier_issue_id),
        )
    )
    if peer_active:
        raise SymphonyError(
            "application release verifier is still in flight",
            verifier=gate.verifier_identifier,
        )
    if require_run_authority:
        if expected_run_id is not None and gate.finalizer_run_id != expected_run_id:
            raise SymphonyError(
                "application release finalizer run is not bound to this gate cycle",
                finalizer=issue.identifier,
                expected_run_id=expected_run_id,
                bound_run_id=gate.finalizer_run_id or "missing",
            )
        authorized = bool(
            self._release_registry_call(
                cfg,
                "check_finalizer_run_authority",
                lambda registry: registry.release_finalizer_run_is_authorized(
                    gate=gate,
                    finalizer_issue_id=issue.id,
                    allow_active=allow_active_run,
                ),
            )
        )
        if not authorized:
            raise SymphonyError(
                "application release finalizer lacks exact run completion authority",
                finalizer=issue.identifier,
                run_id=gate.finalizer_run_id or "missing",
            )
    identity = resolve_target_release_identity(
        repository_root=cfg.workflow_path.parent,
        configured_target_branch=cfg.agent.auto_merge_target_branch,
    )
    mismatches = list(identity.errors)
    if gate.target_branch != identity.target_branch:
        mismatches.append("approved target branch changed")
    if gate.approved_target_sha != identity.target_sha:
        mismatches.append("approved target SHA changed")
    if gate.expected_contract_sha256 != identity.contract_sha256:
        mismatches.append("approved release contract changed")
    if gate.finalizer_identifier != identity.finalizer_ticket:
        mismatches.append("release contract finalizer changed")
    if gate.finalizer_completed_at is not None:
        completion_token = _release_ticket_version_token(cfg, issue.identifier)
        if not gate.finalizer_completion_token:
            mismatches.append(
                "completed finalizer lacks a host-observed board transition token"
            )
        elif gate.finalizer_completion_token != completion_token:
            mismatches.append(
                "completed finalizer board transition changed after delivery"
            )
    if mismatches:
        reason = "; ".join(dict.fromkeys(mismatches))
        self._reopen_stale_release_gate(
            cfg=cfg,
            finalizer=issue,
            gate=gate,
            reason=reason,
            current_contract_sha256=identity.contract_sha256,
            finalizer_state=rewind_state,
            invalidating_finalizer_run_id=(
                expected_run_id if require_run_authority else None
            ),
        )
        raise SymphonyError(
            "application release approval became stale; fresh verification required",
            finalizer=issue.identifier,
            reason=reason,
        )
    return issue


def _mark_release_finalizer_completed(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    gate: ReleaseGate,
    completion_token: str,
    rewind_state: str | None,
) -> ReleaseGate:
    current_issue = self._tracker_call_fetch_issue_full_by_id(cfg, issue.identifier)
    current_token = _release_ticket_version_token(cfg, issue.identifier)
    if (
        current_issue is None
        or current_issue.id != issue.id
        or not _is_release_success_state(cfg, current_issue.state)
        or current_token != completion_token
    ):
        self._invalidate_release_finalizer_version(
            cfg=cfg,
            issue=current_issue or issue,
            gate=gate,
            rewind_state=rewind_state,
            reason=(
                "finalizer ticket changed between terminal approval and "
                "completion persistence"
            ),
        )
        raise SymphonyError(
            "application release finalizer changed before completion proof",
            finalizer=issue.identifier,
        )
    completed = bool(
        self._release_registry_call(
            cfg,
            "mark_finalizer_completed",
            lambda registry: registry.mark_release_finalizer_completed(
                gate=gate,
                finalizer_issue_id=issue.id,
                completion_token=completion_token,
            ),
        )
    )
    persisted = cast(
        ReleaseGate | None,
        self._release_registry_call(
            cfg,
            "read_finalizer_completion",
            lambda registry: registry.get_release_gate(gate.finalizer_identifier),
        ),
    )
    if (
        not completed
        or persisted is None
        or persisted.generation != gate.generation
        or persisted.finalizer_run_id != gate.finalizer_run_id
        or persisted.finalizer_completed_at is None
        or persisted.finalizer_completion_token != completion_token
    ):
        raise SymphonyError(
            "application release finalizer completion proof could not be persisted",
            finalizer=issue.identifier,
        )
    confirmed_issue = self._tracker_call_fetch_issue_full_by_id(
        cfg, issue.identifier
    )
    confirmed_token = _release_ticket_version_token(cfg, issue.identifier)
    if (
        confirmed_issue is None
        or confirmed_issue.id != issue.id
        or not _is_release_success_state(cfg, confirmed_issue.state)
        or confirmed_token != completion_token
    ):
        self._invalidate_release_finalizer_version(
            cfg=cfg,
            issue=confirmed_issue or issue,
            gate=persisted,
            rewind_state=rewind_state,
            reason=(
                "finalizer ticket changed while completion proof was "
                "being persisted"
            ),
        )
        raise SymphonyError(
            "application release finalizer changed during completion proof",
            finalizer=issue.identifier,
        )
    return persisted


def _invalidate_release_finalizer_version(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    gate: ReleaseGate,
    rewind_state: str | None,
    reason: str,
) -> None:
    identity = resolve_target_release_identity(
        repository_root=cfg.workflow_path.parent,
        configured_target_branch=cfg.agent.auto_merge_target_branch,
    )
    self._reopen_stale_release_gate(
        cfg=cfg,
        finalizer=issue,
        gate=gate,
        reason=reason,
        current_contract_sha256=(
            identity.contract_sha256
            if not identity.errors
            else gate.expected_contract_sha256
        ),
        finalizer_state=rewind_state,
        invalidating_finalizer_run_id=gate.finalizer_run_id,
    )


def _guard_release_finalizer_with_version(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    gate: ReleaseGate,
    rewind_state: str | None,
    expected_run_id: str | None,
    require_run_authority: bool,
) -> tuple[Issue, str]:
    """Guard one finalizer and pin the exact board version it approved."""
    before_token = _release_ticket_version_token(cfg, issue.identifier)
    guarded = self._guard_release_finalizer(
        cfg=cfg,
        issue=issue,
        gate=gate,
        rewind_state=rewind_state,
        expected_run_id=expected_run_id,
        require_run_authority=require_run_authority,
    )
    after_token = _release_ticket_version_token(cfg, guarded.identifier)
    if before_token != after_token:
        self._invalidate_release_finalizer_version(
            cfg=cfg,
            issue=guarded,
            gate=gate,
            rewind_state=rewind_state,
            reason=(
                "finalizer ticket changed while terminal authority was "
                "being checked"
            ),
        )
        raise SymphonyError(
            "application release finalizer changed during terminal guard",
            finalizer=guarded.identifier,
        )
    return guarded, after_token


def _require_running_release_authority(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    entry: RunningEntry,
    workspace_path: Path | None = None,
) -> Issue:
    """Revalidate the exact host-owned gate generation for one live run."""
    if not (
        entry.known_app_release
        or entry.known_release_cycle_verifier
        or entry.known_app_release_finalizer
    ):
        return entry.issue
    if not entry.run_id or not entry.release_gate_finalizer:
        raise SymphonyError(
            "application release run lacks cached host authority",
            identifier=entry.issue.identifier,
        )
    gate = cast(
        ReleaseGate | None,
        self._release_registry_call(
            cfg,
            "revalidate_running_gate",
            lambda registry: registry.get_release_gate(
                entry.release_gate_finalizer
            ),
        ),
    )
    if gate is None:
        raise SymphonyError(
            "application release gate disappeared during execution",
            identifier=entry.issue.identifier,
        )
    expected = (
        entry.release_gate_finalizer,
        entry.release_gate_expected_contract_sha256,
        entry.release_gate_cycle_fingerprint,
        entry.release_gate_generation,
    )
    actual = (
        gate.finalizer_identifier,
        gate.expected_contract_sha256,
        gate.cycle_fingerprint,
        gate.generation,
    )
    if actual != expected or not gate.generation:
        raise SymphonyError(
            "application release gate generation changed during execution",
            identifier=entry.issue.identifier,
        )
    if entry.known_app_release_finalizer:
        if gate.finalizer_run_id != entry.run_id:
            raise SymphonyError(
                "application release finalizer lost its exact run binding",
                finalizer=entry.issue.identifier,
            )
        guarded = self._guard_release_finalizer(
            cfg=cfg,
            issue=entry.issue,
            gate=gate,
            expected_run_id=entry.run_id,
            require_run_authority=True,
        )
        if workspace_path is not None:
            if not gate.approved_target_sha:
                raise SymphonyError(
                    "application release finalizer has no approved target SHA"
                )
            workspace_errors = release_workspace_target_errors(
                workspace_root=workspace_path,
                repository_root=cfg.workflow_path.parent,
                target_sha=gate.approved_target_sha,
                board_root=cfg.tracker.board_root,
                allowed_roots=(PurePosixPath("docs") / entry.issue.identifier,),
                role="finalizer",
            )
            if workspace_errors:
                raise SymphonyError(
                    "application release finalizer workspace is stale",
                    errors=list(workspace_errors),
                )
        return guarded
    if (
        gate.verifier_issue_id != entry.issue.id
        or gate.verifier_identifier != entry.issue.identifier
        or gate.verifier_run_id != entry.run_id
    ):
        raise SymphonyError(
            "application release verifier lost its exact run binding",
            verifier=entry.issue.identifier,
        )
    authorized = bool(
        self._release_registry_call(
            cfg,
            "check_verifier_run_authority",
            lambda registry: registry.release_verifier_run_is_authorized(
                gate=gate,
                verifier_issue_id=entry.issue.id,
            ),
        )
    )
    if not authorized:
        raise SymphonyError(
            "application release verifier run is no longer authorized",
            verifier=entry.issue.identifier,
        )
    return entry.issue


def _require_release_transition_verifier_authority(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    entry: RunningEntry,
) -> None:
    """Fence transition writes behind this verifier's exact live run."""
    if not self._heartbeat_run_lease(issue.id, entry):
        raise _ReleaseTransitionAuthorityLost(
            "application release verifier lost its active run lease before "
            "transition enforcement",
            verifier=issue.identifier,
            run_id=entry.run_id,
        )
    try:
        self._require_running_release_authority(
            cfg=cfg,
            entry=entry,
        )
    except SymphonyError as exc:
        raise _ReleaseTransitionAuthorityLost(
            "application release verifier no longer owns the exact gate "
            "generation and role",
            verifier=issue.identifier,
            run_id=entry.run_id,
            reason=str(exc),
        ) from exc


def _prepare_release_dispatch(
    self: Orchestrator, issue: Issue, cfg: ServiceConfig
) -> _ReleaseDispatchAuthority:
    app_label = _has_app_release_label(issue)
    finalizer_label = _is_release_finalizer(issue)
    if cfg.tracker.kind != "file":
        if app_label or finalizer_label:
            raise SymphonyError(
                _APP_RELEASE_FILE_TRACKER_ONLY,
                tracker_kind=cfg.tracker.kind,
            )
        return _ReleaseDispatchAuthority(issue=issue)

    try:
        gate_for_verifier = cast(
            ReleaseGate | None,
            self._release_registry_call(
                cfg,
                "find_verifier_gate",
                lambda registry: registry.get_release_gate_for_verifier(
                    issue.identifier
                ),
            ),
        )
        gate_for_finalizer = cast(
            ReleaseGate | None,
            self._release_registry_call(
                cfg,
                "find_finalizer_gate",
                lambda registry: registry.get_release_gate(issue.identifier),
            ),
        )
        evidence_identity = cast(
            ReleaseEvidenceIdentity | None,
            self._release_registry_call(
                cfg,
                "find_release_evidence_identity",
                lambda registry: registry.get_release_evidence_identity(
                    issue.identifier
                ),
            ),
        )
    except SymphonyError:
        identity = resolve_target_release_identity(
            repository_root=cfg.workflow_path.parent,
            configured_target_branch=cfg.agent.auto_merge_target_branch,
        )
        if app_label or finalizer_label or not identity.errors:
            raise
        return _ReleaseDispatchAuthority(issue=issue)

    if evidence_identity is not None and gate_for_verifier is None:
        if evidence_identity.retired:
            raise SymphonyError(
                "historical release verifier is evidence-only and cannot be "
                "redispatched",
                verifier=issue.identifier,
                finalizer=evidence_identity.finalizer_identifier,
            )
        raise SymphonyError(
            "current release verifier identity has no host-owned gate",
            verifier=issue.identifier,
            finalizer=evidence_identity.finalizer_identifier,
        )

    if (
        app_label
        or finalizer_label
        or gate_for_verifier is not None
        or gate_for_finalizer is not None
    ) and (
        not _has_active_release_verify_lane(cfg)
        or not _has_release_finalizer_lane(cfg)
    ):
        raise SymphonyError(
            "app-release requires active Verify and finalizer lanes before dispatch"
        )

    if gate_for_finalizer is not None or finalizer_label:
        if gate_for_finalizer is None:
            raise SymphonyError(
                "application release finalizer has no host-owned authority",
                finalizer=issue.identifier,
            )
        guarded = self._guard_release_finalizer(
            cfg=cfg,
            issue=issue,
            gate=gate_for_finalizer,
        )
        return _ReleaseDispatchAuthority(
            issue=guarded,
            gate=gate_for_finalizer,
            finalizer=True,
        )

    if gate_for_verifier is None and app_label:
        if normalize_state(issue.state) == "verify":
            issue, gate_for_verifier = self._create_initial_release_gate(cfg, issue)
        else:
            raise SymphonyError(
                "app-release ticket outside Verify has no host-owned authority",
                verifier=issue.identifier,
                state=issue.state,
            )
    elif gate_for_verifier is not None:
        verifier_state: str | None = _release_verifier_state(cfg)
        if gate_for_verifier.status == "pending":
            pending_finalizer = self._tracker_call_fetch_issue_full_by_id(
                cfg, gate_for_verifier.finalizer_identifier
            )
            if pending_finalizer is None:
                raise SymphonyError(
                    "release finalizer disappeared while verifier was pending",
                    finalizer=gate_for_verifier.finalizer_identifier,
                )
            if normalize_state(pending_finalizer.state) in {
                normalize_state(state) for state in cfg.tracker.terminal_states
            }:
                ReleaseCycleService(cfg).reopen_after_target_change(
                    finalizer=pending_finalizer,
                    gate=gate_for_verifier,
                    expected_contract_sha256=(
                        gate_for_verifier.expected_contract_sha256
                    ),
                    reason=(
                        "release finalizer became terminal before its verifier "
                        "received durable GREEN approval"
                    ),
                    finalizer_state=next(
                        (
                            state
                            for state in reversed(cfg.tracker.active_states)
                            if normalize_state(state) != "verify"
                        ),
                        None,
                    ),
                )
        if gate_for_verifier.status == "approved" and normalize_state(
            issue.state
        ) in {normalize_state(state) for state in cfg.tracker.active_states}:
            finalizer = self._tracker_call_fetch_issue_full_by_id(
                cfg, gate_for_verifier.finalizer_identifier
            )
            if finalizer is None:
                raise SymphonyError(
                    "release finalizer disappeared while reopening verifier",
                    finalizer=gate_for_verifier.finalizer_identifier,
                )
            self._reopen_stale_release_gate(
                cfg=cfg,
                finalizer=finalizer,
                gate=gate_for_verifier,
                reason=(
                    "approved verifier requires a new host-bound run after "
                    f"returning in active state {issue.state}"
                ),
                finalizer_state=next(
                    (
                        state
                        for state in reversed(cfg.tracker.active_states)
                        if normalize_state(state) != "verify"
                    ),
                    None,
                ),
            )
            gate_for_verifier = cast(
                ReleaseGate | None,
                self._release_registry_call(
                    cfg,
                    "read_reopened_verifier_gate",
                    lambda registry: registry.get_release_gate_for_verifier(
                        issue.identifier
                    ),
                ),
            )
            if gate_for_verifier is None:
                raise SymphonyError(
                    "reopened release verifier authority disappeared",
                    verifier=issue.identifier,
                )
        elif gate_for_verifier.status == "approved":
            verifier_state = None
        issue = ReleaseCycleService(cfg).restore_verifier_gate_labels(
            issue=issue,
            gate=gate_for_verifier,
            verifier_state=verifier_state,
        )

    app_release = app_label or gate_for_verifier is not None
    return _ReleaseDispatchAuthority(
        issue=issue,
        gate=gate_for_verifier,
        app_release=app_release,
        cycle_verifier=gate_for_verifier is not None,
    )


def _release_verifier_handoff_is_durable(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    registry: RunRegistry,
    identity: ReleaseEvidenceIdentity,
) -> bool:
    """Prove that one retired verifier was replaced and durably relinked."""
    if (
        not identity.retired
        or identity.role != "verifier"
        or not identity.issue_id
        or not identity.identifier
        or not identity.finalizer_identifier
        or not identity.cycle_generation
        or registry.has_active_lease(identity.issue_id)
    ):
        return False
    gate = registry.get_release_gate(identity.finalizer_identifier)
    if (
        gate is None
        or not gate.generation
        or gate.generation == identity.cycle_generation
        or gate.verifier_issue_id == identity.issue_id
        or gate.verifier_identifier == identity.identifier
    ):
        return False
    current_identity = registry.get_release_evidence_identity_by_issue_id(
        gate.verifier_issue_id
    )
    if current_identity is None or (
        current_identity.issue_id,
        current_identity.identifier,
        current_identity.finalizer_identifier,
        current_identity.role,
        current_identity.cycle_generation,
        current_identity.retired,
    ) != (
        gate.verifier_issue_id,
        gate.verifier_identifier,
        gate.finalizer_identifier,
        "verifier",
        gate.generation,
        False,
    ):
        return False
    finalizer = self._tracker_call_fetch_issue_full_by_id(
        cfg, identity.finalizer_identifier
    )
    if finalizer is None or finalizer.identifier != identity.finalizer_identifier:
        return False
    blocker_identifiers = {
        blocker.identifier for blocker in finalizer.blocked_by if blocker.identifier
    }
    return (
        gate.verifier_identifier in blocker_identifiers
        and identity.identifier not in blocker_identifiers
    )


async def _rewind_app_release_transition(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    producing_state: str,
    note_body: str,
) -> Issue:
    """Persist a release-gate rewind on the local file board."""
    return await asyncio.to_thread(
        ReleaseCycleService(cfg).rewind_transition,
        issue=issue,
        producing_state=producing_state,
        note_body=note_body,
    )


def _app_release_transition_lock(self: Orchestrator, issue_id: str) -> asyncio.Lock | None:
    """Return the lock owned by the current live run for ``issue_id``."""
    running = self._running.get(issue_id)
    if running is None:
        return None
    owned = self._app_release_transition_locks.get(issue_id)
    if owned is None or owned[0] is not running:
        lock = asyncio.Lock()
        self._app_release_transition_locks[issue_id] = (running, lock)
        return lock
    return owned[1]


async def _enforce_app_release_transition(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    workspace_path: Path,
    producing_state: str,
    known_app_release: bool,
    running_entry: RunningEntry | None = None,
) -> tuple[Issue, bool]:
    """Fail closed around every post-transition release authority check."""
    authority_entry = running_entry or self._running.get(issue.id)
    transition_lock = (
        self._app_release_transition_lock(issue.id)
        if self._running.get(issue.id) is authority_entry
        else None
    )
    if transition_lock is not None:
        async with transition_lock:
            return await self._enforce_app_release_transition_guarded(
                cfg=cfg,
                issue=issue,
                workspace_path=workspace_path,
                producing_state=producing_state,
                known_app_release=known_app_release,
                running_entry=authority_entry,
            )
    return await self._enforce_app_release_transition_guarded(
        cfg=cfg,
        issue=issue,
        workspace_path=workspace_path,
        producing_state=producing_state,
        known_app_release=known_app_release,
        running_entry=authority_entry,
    )


async def _enforce_app_release_transition_guarded(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    workspace_path: Path,
    producing_state: str,
    known_app_release: bool,
    running_entry: RunningEntry | None,
) -> tuple[Issue, bool]:
    """Run one serialized release decision and persist a safe rewind on error."""
    try:
        return await self._enforce_app_release_transition_inner(
            cfg=cfg,
            issue=issue,
            workspace_path=workspace_path,
            producing_state=producing_state,
            known_app_release=known_app_release,
            running_entry=running_entry,
        )
    except _ReleaseTransitionAuthorityLost:
        # The board and current gate now belong to a newer run. Rewinding
        # here would let the stale worker mutate (or retire) its peer's
        # release cycle, so ownership loss is a side-effect-free abort.
        raise
    except Exception as exc:
        try:
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=producing_state,
                note_body=(
                    "Release authority failed closed before delivery.\n\n"
                    f"Infrastructure error: {exc}"
                ),
            )
        except Exception as rewind_exc:
            log.error(
                "app_release_authority_rewind_failed",
                issue_id=issue.id,
                identifier=issue.identifier,
                authority_error=str(exc),
                rewind_error=str(rewind_exc),
            )
            raise SymphonyError(
                "application release authority failed and its Verify rewind "
                "could not be persisted",
                authority_error=str(exc),
                rewind_error=str(rewind_exc),
            ) from rewind_exc
        log.error(
            "app_release_authority_rewound",
            issue_id=issue.id,
            identifier=issue.identifier,
            error=str(exc),
        )
        return rewound, True


async def _enforce_app_release_transition_inner(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    workspace_path: Path,
    producing_state: str,
    known_app_release: bool,
    running_entry: RunningEntry | None,
) -> tuple[Issue, bool]:
    """Gate a local-file-board transition out of Verify.

        Body lives in ``release_transition.enforce_app_release_transition_inner``
        (see that module's docstring); this forward keeps the call site and
        the test seam on the orchestrator.
        """
    return await release_transition.enforce_app_release_transition_inner(
        self,
        cfg=cfg,
        issue=issue,
        workspace_path=workspace_path,
        producing_state=producing_state,
        known_app_release=known_app_release,
        running_entry=running_entry,
    )


# ------------------------------------------------------------------
# startup cleanup (§8.6)
# ------------------------------------------------------------------

async def _startup_release_terminal_guard(
    self: Orchestrator,
    cfg: ServiceConfig,
    issue: Issue,
    *,
    owned_cleanup_run_id: str | None = None,
) -> tuple[Issue, bool, bool]:
    """Reconcile terminal release authority even when no workspace remains.

        Returns ``(issue, evidence_only, stopped)``. ``stopped`` means the
        ticket was rewound or registry authority could not be proven, so
        ordinary terminal cleanup must not continue.
        """
    if cfg.tracker.kind != "file":
        return issue, False, False
    try:
        verifier_gate = cast(
            ReleaseGate | None,
            self._release_registry_call(
                cfg,
                "startup_read_verifier_gate",
                lambda registry: registry.get_release_gate_for_verifier(
                    issue.identifier
                ),
            ),
        )
        finalizer_gate = cast(
            ReleaseGate | None,
            self._release_registry_call(
                cfg,
                "startup_read_finalizer_gate",
                lambda registry: registry.get_release_gate(issue.identifier),
            ),
        )
        evidence_identity = cast(
            ReleaseEvidenceIdentity | None,
            self._release_registry_call(
                cfg,
                "startup_read_release_evidence_identity",
                lambda registry: registry.get_release_evidence_identity(
                    issue.identifier
                ),
            ),
        )
    except SymphonyError as exc:
        identity = resolve_target_release_identity(
            repository_root=cfg.workflow_path.parent,
            configured_target_branch=cfg.agent.auto_merge_target_branch,
        )
        if _is_release_evidence_issue(issue) or not identity.errors:
            log.error(
                "startup_release_cleanup_refused",
                identifier=issue.identifier,
                error=str(exc),
            )
            return issue, True, True
        return issue, False, False

    evidence_only = (
        verifier_gate is not None
        or finalizer_gate is not None
        or evidence_identity is not None
        or _is_release_evidence_issue(issue)
    )
    if not evidence_only:
        return issue, False, False
    if owned_cleanup_run_id is not None:
        owns_cleanup = bool(
            self._release_registry_call(
                cfg,
                "startup_heartbeat_release_cleanup",
                lambda registry: registry.heartbeat(
                    issue_id=issue.id,
                    run_id=owned_cleanup_run_id,
                ),
            )
        )
        peer_active = not owns_cleanup
    else:
        peer_active = bool(
            self._release_registry_call(
                cfg,
                "startup_check_release_issue_lease",
                lambda registry: registry.has_active_lease(issue.id),
            )
        )
    if peer_active:
        log.info(
            "startup_release_cleanup_skipped_live_peer",
            identifier=issue.identifier,
        )
        return issue, True, True
    finalizer_rewind_state = next(
        (
            state
            for state in reversed(cfg.tracker.active_states)
            if normalize_state(state) != "verify"
        ),
        _release_verifier_state(cfg),
    )
    if finalizer_gate is not None:
        try:
            guarded = self._guard_release_finalizer(
                cfg=cfg,
                issue=issue,
                gate=finalizer_gate,
                rewind_state=finalizer_rewind_state,
                allow_active_run=False,
                require_run_authority=True,
            )
        except SymphonyError as exc:
            rewound = await self._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=finalizer_rewind_state,
                note_body=(
                    "Startup stopped final delivery because the release "
                    f"approval is invalid: {exc}"
                ),
            )
            return rewound, True, True
        return guarded, True, False
    if _is_release_finalizer(issue):
        rewound = await self._rewind_app_release_transition(
            cfg=cfg,
            issue=issue,
            producing_state=finalizer_rewind_state,
            note_body=(
                "Startup found a terminal release finalizer without "
                "host-owned approval."
            ),
        )
        return rewound, True, True
    if verifier_gate is not None:
        if verifier_gate.status == "approved" and _is_release_success_state(
            cfg, issue.state
        ):
            return issue, True, False
        rewound = await self._rewind_app_release_transition(
            cfg=cfg,
            issue=issue,
            producing_state=_release_verifier_state(cfg),
            note_body=(
                "Startup found a terminal release verifier without a "
                "durable GREEN approval in an explicit success state."
            ),
        )
        return rewound, True, True
    if evidence_identity is not None and evidence_identity.retired:
        return issue, True, False
    if evidence_identity is not None:
        rewound = await self._rewind_app_release_transition(
            cfg=cfg,
            issue=issue,
            producing_state=_release_verifier_state(cfg),
            note_body=(
                "Startup found current release evidence whose host-owned "
                "gate is missing."
            ),
        )
        return rewound, True, True
    if _has_app_release_label(issue):
        rewound = await self._rewind_app_release_transition(
            cfg=cfg,
            issue=issue,
            producing_state=_release_verifier_state(cfg),
            note_body=(
                "Startup found terminal app-release evidence without "
                "host-owned authority."
            ),
        )
        return rewound, True, True
    return issue, True, False


def _tracker_call_reconcile_release_cycle(
    cfg: ServiceConfig,
    source_issue: Issue,
    validation: ReleaseValidationResult,
    source_agent_kind: str,
    *,
    before_finalizer_relink: Callable[[Issue], None] | None = None,
) -> ReleaseCycleWriteResult:
    return ReleaseCycleService(cfg).reconcile(
        source_issue,
        validation,
        source_agent_kind,
        before_finalizer_relink=before_finalizer_relink,
    )
