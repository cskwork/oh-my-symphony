"""Verify-exit release gate, extracted from ``core.Orchestrator``.

``enforce_app_release_transition_inner`` is the body that used to live inline
in ``Orchestrator._enforce_app_release_transition_inner`` (HANDOFF 2026-09-05
item 2, first of the three planned ``core.py`` extractions). It gates a
file-board ticket's forward move out of Verify for ``app-release`` work:
refresh the ticket, bind or recover the host-owned release gate, run the
contract validation, approve the gate on GREEN, or hand a repairable RED to
the release-cycle service that creates repairs and the fresh verifier.

Convention (same as ``worker_exit.py`` / ``attempt.py``): the function takes
the ``Orchestrator`` instance explicitly as its first parameter (``orch``);
all state stays on the orchestrator. ``core`` imports this module at load
time, so the reverse reference is late-bound through ``_core()``, which is
also how the names tests patch on ``symphony.orchestrator.core``
(``validate_release_contract``, ``resolve_target_release_identity``) keep
their documented seam.

Behaviour contract: pure extraction. Side-effect ordering, log event names
and fields, registry calls, rewind notes, and exception handling are the
same as the pre-split method.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..errors import SymphonyError
from ..issue import Issue
from ..logging import get_logger
from ..workflow import ServiceConfig
from .entries import RunningEntry
from .release_cycle import ReleaseCycleService
from .run_registry import (
    ReleaseEvidenceIdentity,
    ReleaseGate,
    RunRegistry,
    registry_path_for_workflow,
)

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()


def _core():
    """Late import of ``symphony.orchestrator.core``.

    ``core`` imports this module, so the import has to wait until call time.
    The helpers and the release-contract functions are read from ``core``'s
    globals so tests keep patching them in one place.
    """
    from . import core

    return core


async def enforce_app_release_transition_inner(
    orch: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    workspace_path: Path,
    producing_state: str,
    known_app_release: bool,
    running_entry: RunningEntry | None,
) -> tuple[Issue, bool]:
    """Gate a local-file-board transition out of Verify.

    Returns ``(issue, rewound)``. The full refresh and machine gate are
    independent of the prose stage-contract mode. Remote adapters do not
    expose the atomic create/update lifecycle API and are rejected before
    any release-gate write.
    """
    refreshed = await orch._refresh_issue_full(cfg, issue.id)
    if refreshed is not None:
        issue = refreshed
    # Capture the entry before waiting for the per-run lock. Worker exit
    # may remove it from `_running` immediately after a peer persists the
    # decision, but the waiting caller still belongs to that exact run.
    running = running_entry or orch._running.get(issue.id)
    enforce_bound_verifier_authority = bool(
        running is not None
        and running.release_authority_resolved
        and running.known_release_cycle_verifier
    )
    known_app_release = (
        known_app_release
        or (running.known_app_release if running is not None else False)
        or _core()._has_app_release_label(issue)
    )
    if not known_app_release:
        return issue, False
    if cfg.tracker.kind != "file":
        raise SymphonyError(
            "app-release contracts require tracker.kind=file until an adapter "
            "provides atomic repair-cycle create/update support",
            tracker_kind=cfg.tracker.kind,
        )

    gate = cast(
        ReleaseGate | None,
        orch._release_registry_call(
            cfg,
            "read_verifier_gate_for_transition",
            lambda registry: registry.get_release_gate_for_verifier(
                issue.identifier
            ),
        ),
    )
    if gate is None:
        evidence_identity = cast(
            ReleaseEvidenceIdentity | None,
            orch._release_registry_call(
                cfg,
                "read_retired_verifier_after_transition",
                lambda registry: registry.get_release_evidence_identity(
                    issue.identifier
                ),
            ),
        )
        if (
            running is not None
            and running.known_release_cycle_verifier
            and evidence_identity is not None
            and evidence_identity.retired
            and evidence_identity.issue_id == issue.id
            and evidence_identity.finalizer_identifier
            == running.release_gate_finalizer
            and evidence_identity.cycle_generation
            == running.release_gate_generation
        ):
            # A serialized peer already replaced this verifier with the
            # next PENDING cycle. The old issue is now immutable evidence;
            # recovering a gate for it would duplicate repairs/verifiers.
            log.info(
                "app_release_red_transition_already_reconciled",
                identifier=issue.identifier,
                finalizer=evidence_identity.finalizer_identifier,
                generation=evidence_identity.cycle_generation,
            )
            return issue, False
        if enforce_bound_verifier_authority:
            assert running is not None
            orch._require_release_transition_verifier_authority(
                cfg=cfg,
                issue=issue,
                entry=running,
            )
        # Defensive compatibility for a run that was already in flight
        # when the host upgraded. New dispatches always persist this row
        # before their lease is acquired.
        identity = _core().resolve_target_release_identity(
            repository_root=cfg.workflow_path.parent,
            configured_target_branch=cfg.agent.auto_merge_target_branch,
        )
        if identity.errors:
            rewound = await orch._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=producing_state,
                note_body=(
                    "Release validation could not establish host-owned "
                    "authority before the transition.\n\nEvidence errors:\n- "
                    + "\n- ".join(identity.errors)
                ),
            )
            return rewound, True
        gate = orch._persist_pending_release_gate(
            cfg=cfg,
            gate=orch._pending_release_gate(
                issue=issue,
                finalizer=identity.finalizer_ticket,
                contract_sha256=identity.contract_sha256,
            ),
            operation="recover_inflight_pending_gate",
        )
    elif enforce_bound_verifier_authority:
        assert running is not None
        orch._require_release_transition_verifier_authority(
            cfg=cfg,
            issue=issue,
            entry=running,
        )
    if running is not None and not enforce_bound_verifier_authority:
        running.known_app_release = True
        running.known_release_cycle_verifier = True
        running.release_gate_finalizer = gate.finalizer_identifier
        running.release_gate_expected_contract_sha256 = (
            gate.expected_contract_sha256
        )
        running.release_gate_cycle_fingerprint = gate.cycle_fingerprint
        running.release_gate_generation = gate.generation

    validation = await asyncio.to_thread(
        _core().validate_release_contract,
        workspace_root=workspace_path,
        repository_root=cfg.workflow_path.parent,
        verifier_ticket=issue.identifier,
        configured_target_branch=cfg.agent.auto_merge_target_branch,
        board_root=cfg.tracker.board_root,
    )
    if enforce_bound_verifier_authority:
        assert running is not None
        orch._require_release_transition_verifier_authority(
            cfg=cfg,
            issue=issue,
            entry=running,
        )
    binding_errors: list[str] = []
    approved_for_current_run = (
        gate.status == "approved"
        and running is not None
        and bool(running.run_id)
        and gate.verifier_run_id == running.run_id
        and gate.approved_fingerprint == validation.fingerprint
        and gate.target_branch == validation.target_branch
        and gate.approved_target_sha == validation.target_sha
    )
    if gate.status != "pending" and not approved_for_current_run:
        binding_errors.append("release verifier authority is not pending")
    if gate.verifier_issue_id != issue.id:
        binding_errors.append("release verifier issue id does not match authority")
    if gate.verifier_identifier != issue.identifier:
        binding_errors.append(
            "release verifier identifier does not match authority"
        )
    if gate.expected_contract_sha256 != validation.contract_sha256:
        binding_errors.append(
            "host-owned expected contract hash does not match the current release contract"
        )
    if gate.finalizer_identifier != validation.finalizer_ticket:
        binding_errors.append(
            "host-owned finalizer binding does not match the release contract"
        )
    if binding_errors:
        if (
            gate.status == "pending"
            and validation.contract_sha256
            and gate.finalizer_identifier == validation.finalizer_ticket
            and gate.expected_contract_sha256 != validation.contract_sha256
        ):
            refreshed_pending = orch._persist_pending_release_gate(
                cfg=cfg,
                gate=orch._pending_release_gate(
                    issue=issue,
                    finalizer=gate.finalizer_identifier,
                    contract_sha256=validation.contract_sha256,
                ),
                operation="refresh_drifted_pending_release_contract",
            )
            ReleaseCycleService(cfg).restore_verifier_gate_labels(
                issue=issue,
                gate=refreshed_pending,
                verifier_state=_core()._release_verifier_state(cfg),
            )
            binding_errors.append(
                "host authority was rebound to the new contract; a fresh "
                "verifier run is required"
            )
        metadata = (
            f"\n\nContract SHA-256: "
            f"`{validation.contract_sha256 or '(unavailable)'}`\n"
            f"Target SHA: `{validation.target_sha or '(unavailable)'}`\n"
            f"Release fingerprint: `{validation.fingerprint}`"
        )
        note_text = validation.note_text
        if validation.evidence_errors:
            note_text += "\n- " + "\n- ".join(binding_errors)
        else:
            note_text = (
                "Release validation did not pass.\n\nEvidence errors:\n- "
                + "\n- ".join(binding_errors)
            )
        rewound = await orch._rewind_app_release_transition(
            cfg=cfg,
            issue=issue,
            producing_state=producing_state,
            note_body=note_text + metadata,
        )
        return rewound, True
    if validation.passed:
        if approved_for_current_run:
            log.info(
                "app_release_gate_already_approved",
                identifier=issue.identifier,
                target_branch=validation.target_branch,
                target_sha=validation.target_sha,
                contract_sha256=validation.contract_sha256,
            )
            return issue, False
        if running is None or not running.run_id:
            rewound = await orch._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=producing_state,
                note_body=(
                    "Release validation passed, but no active host run lease "
                    "was available to bind the approval."
                ),
            )
            return rewound, True
        approved = bool(
            orch._release_registry_call(
                cfg,
                "approve_release_gate",
                lambda registry: registry.approve_release_gate(
                    finalizer_identifier=gate.finalizer_identifier,
                    verifier_issue_id=gate.verifier_issue_id,
                    verifier_identifier=gate.verifier_identifier,
                    expected_contract_sha256=gate.expected_contract_sha256,
                    expected_cycle_fingerprint=gate.cycle_fingerprint,
                    expected_generation=gate.generation,
                    approved_fingerprint=validation.fingerprint,
                    target_branch=validation.target_branch,
                    target_sha=validation.target_sha,
                    verifier_run_id=running.run_id,
                ),
            )
        )
        approved_gate = cast(
            ReleaseGate | None,
            orch._release_registry_call(
                cfg,
                "read_approved_release_gate",
                lambda registry: registry.get_release_gate(
                    gate.finalizer_identifier
                ),
            ),
        )
        if (
            not approved
            or approved_gate is None
            or approved_gate.status != "approved"
            or approved_gate.approved_fingerprint != validation.fingerprint
            or approved_gate.approved_target_sha != validation.target_sha
            or approved_gate.target_branch != validation.target_branch
            or approved_gate.verifier_run_id != running.run_id
        ):
            rewound = await orch._rewind_app_release_transition(
                cfg=cfg,
                issue=issue,
                producing_state=producing_state,
                note_body=(
                    "Release validation passed, but the host-owned GREEN "
                    "approval could not be durably persisted."
                ),
            )
            return rewound, True
        log.info(
            "app_release_gate_passed",
            identifier=issue.identifier,
            target_branch=validation.target_branch,
            target_sha=validation.target_sha,
            contract_sha256=validation.contract_sha256,
        )
        return issue, False

    metadata = (
        f"\n\nContract SHA-256: `{validation.contract_sha256 or '(unavailable)'}`\n"
        f"Target SHA: `{validation.target_sha or '(unavailable)'}`\n"
        f"Release fingerprint: `{validation.fingerprint}`"
    )
    if validation.evidence_errors:
        rewound = await orch._rewind_app_release_transition(
            cfg=cfg,
            issue=issue,
            producing_state=producing_state,
            note_body=validation.note_text + metadata,
        )
        return rewound, True

    registry_path = registry_path_for_workflow(cfg.workflow_path)

    def persist_fresh_pending_gate(verifier: Issue) -> None:
        pending = replace(
            orch._pending_release_gate(
                issue=verifier,
                finalizer=validation.finalizer_ticket,
                contract_sha256=validation.contract_sha256,
            ),
            cycle_fingerprint=validation.fingerprint,
        )
        registry = RunRegistry(registry_path)
        try:
            registry.replace_pending_release_gate(pending)
            persisted = registry.get_release_gate(validation.finalizer_ticket)
            expected = (
                pending.finalizer_identifier,
                pending.verifier_issue_id,
                pending.verifier_identifier,
                pending.expected_contract_sha256,
                pending.cycle_fingerprint,
                "pending",
            )
            actual = (
                (
                    persisted.finalizer_identifier,
                    persisted.verifier_issue_id,
                    persisted.verifier_identifier,
                    persisted.expected_contract_sha256,
                    persisted.cycle_fingerprint,
                    persisted.status,
                )
                if persisted is not None
                else None
            )
            if actual != expected:
                raise SymphonyError(
                    "fresh release verifier authority was not persisted before relink",
                    verifier=verifier.identifier,
                    finalizer=validation.finalizer_ticket,
                )
        finally:
            registry.close()

    lifecycle = await asyncio.to_thread(
        orch._tracker_call_reconcile_release_cycle,
        cfg,
        issue,
        validation,
        issue.agent_kind or cfg.agent.kind,
        before_finalizer_relink=persist_fresh_pending_gate,
    )
    if not lifecycle.passed:
        rewound = await orch._rewind_app_release_transition(
            cfg=cfg,
            issue=issue,
            producing_state=producing_state,
            note_body=(
                validation.note_text
                + metadata
                + "\n\nRepair-cycle write failed closed: "
                + lifecycle.error
            ),
        )
        return rewound, True

    if running is not None:
        retired_identity = cast(
            ReleaseEvidenceIdentity | None,
            orch._release_registry_call(
                cfg,
                "read_completed_verifier_handoff",
                lambda registry: registry.get_release_evidence_identity_by_issue_id(
                    issue.id
                ),
            ),
        )
        if retired_identity is None or (
            retired_identity.issue_id,
            retired_identity.identifier,
            retired_identity.finalizer_identifier,
            retired_identity.role,
            retired_identity.cycle_generation,
            retired_identity.retired,
        ) != (
            issue.id,
            issue.identifier,
            running.release_gate_finalizer,
            "verifier",
            running.release_gate_generation,
            True,
        ):
            raise SymphonyError(
                "completed release verifier handoff identity could not be proven",
                verifier=issue.identifier,
                finalizer=validation.finalizer_ticket,
            )
        running.release_verifier_handoff_complete = True

    log.warning(
        "app_release_repairs_created",
        identifier=issue.identifier,
        fingerprint=validation.fingerprint,
        repair_identifiers=lifecycle.repair_identifiers,
        verifier_identifier=lifecycle.verifier_identifier,
    )
    return issue, False
