"""Typed file-board service for application release lifecycle writes.

The orchestrator owns timing, run leases, and transition decisions.  This
module owns the cohesive tracker transaction/reconciliation rules so the core
state machine does not also have to describe and rebuild the release DAG.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Callable

from symphony.errors import SymphonyError
from symphony.issue import Issue, normalize_state
from symphony.trackers import build_tracker_client
from symphony.trackers.file import FileBoardTracker
from symphony.workflow import ServiceConfig
from symphony.release.contracts import RepairableFailure, ReleaseValidationResult
from symphony.orchestrator.run_registry import ReleaseGate, RunRegistry, registry_path_for_workflow
# Core owns the release vocabulary so it can recognize release tickets and
# refuse them when this package is absent.
from symphony.orchestrator.release_seam import (
    has_active_verify_lane,
    has_release_finalizer_lane,
    has_release_success_terminal,
    is_release_cycle_verifier,
    is_release_evidence_issue,
    is_release_finalizer,
    is_release_success_state,
    normalized_label_set,
    release_failure_target_state,
    release_verifier_state,
)


_CONTRACT_HASH_LABEL_PREFIX = "release-contract-sha256-"


def _blocker_identifiers(issue: Issue) -> list[str]:
    identifiers: list[str] = []
    for blocker in issue.blocked_by:
        identifier = blocker.identifier or blocker.id
        if identifier:
            identifiers.append(identifier)
    return identifiers


def release_fingerprint_label(fingerprint: str) -> str:
    return f"release-fingerprint-{fingerprint}"


def release_contract_hash_label(contract_sha256: str) -> str:
    return f"{_CONTRACT_HASH_LABEL_PREFIX}{contract_sha256}"


def release_finalizer_label(finalizer_ticket: str) -> str:
    return f"release-finalizer-{finalizer_ticket.lower()}"


def release_group_label(repair_group: str) -> str:
    return f"release-repair-group-{repair_group}"


def _release_cycle_item_identifier(
    *,
    prefix: str,
    finalizer_identifier: str,
    cycle_fingerprint: str,
    item_role: str,
    item_key: str,
) -> str:
    """Stable local-board id used by the host-owned pre-create reservation."""
    payload = "\0".join(
        (
            "release-cycle-item-v1",
            finalizer_identifier,
            cycle_fingerprint,
            item_role,
            item_key,
        )
    )
    suffix = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24].upper()
    return f"{prefix}-{suffix}"


def release_ticket_version_token(cfg: ServiceConfig, identifier: str) -> str:
    """Hash host-observed ticket bytes plus the file replacement generation.

    Content alone cannot distinguish a finalizer that was changed away from
    Done and then written back to identical bytes. The local file tracker uses
    atomic replacement, so device/inode/mtime bind completion proof to the
    exact board transition the host observed.
    """
    client = _file_tracker(cfg)
    try:
        path = client.find_path(identifier)
        if path is None:
            raise SymphonyError(
                "release ticket version token cannot find board ticket",
                identifier=identifier,
            )
        try:
            with path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                payload = stream.read()
                after = os.fstat(stream.fileno())
        except OSError as exc:
            raise SymphonyError(
                "release ticket version token cannot read board ticket",
                identifier=identifier,
                error=str(exc),
            ) from exc
        before_token = (
            before.st_dev,
            before.st_ino,
            before.st_mtime_ns,
            before.st_size,
        )
        after_token = (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_size,
        )
        if before_token != after_token:
            raise SymphonyError(
                "release board ticket changed while completion was observed",
                identifier=identifier,
            )
        digest = hashlib.sha256()
        digest.update(payload)
        digest.update(b"\0")
        digest.update(":".join(str(value) for value in after_token).encode("ascii"))
        return digest.hexdigest()
    finally:
        client.close()


def initial_release_gate_fingerprint(
    *, verifier_identifier: str, finalizer_identifier: str, contract_sha256: str
) -> str:
    payload = "\0".join(
        ("initial-release-gate", verifier_identifier, finalizer_identifier, contract_sha256)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def release_expected_hash_labels(issue: Issue) -> tuple[str, ...]:
    return tuple(
        sorted(
            label
            for label in normalized_label_set(issue)
            if label.startswith(_CONTRACT_HASH_LABEL_PREFIX)
        )
    )










def release_repair_state(cfg: ServiceConfig) -> str:
    states = {normalize_state(state): state for state in cfg.tracker.active_states}
    return (
        states.get("build")
        or states.get("in progress")
        or next(iter(cfg.tracker.active_states), "In Progress")
    )



def group_release_failures(
    failures: tuple[RepairableFailure, ...],
) -> dict[str, tuple[RepairableFailure, ...]]:
    grouped: dict[str, list[RepairableFailure]] = {}
    for failure in failures:
        grouped.setdefault(failure.repair_group, []).append(failure)
    return {group: tuple(items) for group, items in sorted(grouped.items())}


def release_repair_description(
    *,
    source: Issue,
    source_agent_kind: str,
    result: ReleaseValidationResult,
    repair_group: str,
    failures: tuple[RepairableFailure, ...],
) -> str:
    lines = [
        "# Application release quality repair",
        "",
        f"Source verifier: `{source.identifier}`",
        f"Source agent kind: `{source_agent_kind}`",
        f"Request grouping: `{source.request or source.identifier}`",
        f"Repair group: `{repair_group}`",
        f"Release fingerprint: `{result.fingerprint}`",
        f"Contract SHA-256: `{result.contract_sha256}`",
        f"Target branch: `{result.target_branch}`",
        f"Target SHA: `{result.target_sha}`",
        "",
        "Fix every failure below on its implementation branch, merge it into "
        "the target branch, and leave exact reproduction and verification evidence.",
        "",
        "## Failed checks",
    ]
    for failure in failures:
        lines.extend(
            [
                "",
                f"### {failure.check_id}",
                f"- Description: {failure.description}",
                f"- Expected: {failure.expected}",
                f"- Actual: {failure.actual}",
                f"- Repro: `{failure.repro}`",
                "- Evidence: "
                + (
                    ", ".join(f"`{item}`" for item in failure.evidence)
                    or "none cited"
                ),
            ]
        )
    return "\n".join(lines) + "\n"


def release_verifier_description(
    *, source: Issue, result: ReleaseValidationResult
) -> str:
    return (
        "# Fresh application release verification\n\n"
        f"This verifier supersedes historical verifier `{source.identifier}` for "
        f"request `{source.request or source.identifier}`.\n\n"
        f"Release fingerprint: `{result.fingerprint}`\n"
        f"Prior contract SHA-256: `{result.contract_sha256}`\n"
        f"Prior target SHA: `{result.target_sha}`\n"
        f"Release finalizer: `{result.finalizer_ticket}`\n\n"
        "After every blocker is merged, rebase this evidence-only branch onto the "
        f"current tip of `{result.target_branch}` and prove that target SHA is an "
        "ancestor. If synchronization cannot be proved, stay in Verify with an "
        "environment error. Run every check in "
        "`release-contract.yaml` against that new exact SHA using the native runner. "
        "Write only this ticket's `docs/<verifier>/qa/release-evidence.json`, hash "
        "all native results and cited artifacts, then run "
        "`symphony release check WORKFLOW.md --ticket <verifier> --workspace <path>`. "
        "Do not reuse the prior verifier's evidence or any Markdown/ledger GREEN text. "
        "The host snapshots this branch but never merges it into the target.\n"
    )


@dataclass(frozen=True)
class ReleaseCycleWriteResult:
    passed: bool
    repair_identifiers: tuple[str, ...] = ()
    verifier_identifier: str = ""
    error: str = ""


PendingGateWriter = Callable[[Issue], None]


def _file_tracker(cfg: ServiceConfig) -> FileBoardTracker:
    """Return the concrete tracker required by the atomic release protocol."""
    client = build_tracker_client(cfg)
    if isinstance(client, FileBoardTracker):
        return client
    client.close()
    raise SymphonyError(
        "app-release lifecycle writes require the atomic local file tracker",
        tracker_kind=cfg.tracker.kind,
    )


@dataclass(frozen=True)
class ReleaseCycleService:
    cfg: ServiceConfig

    def restore_verifier_gate_labels(
        self,
        *,
        issue: Issue,
        gate: ReleaseGate,
        verifier_state: str | None = None,
    ) -> Issue:
        """Reconcile worker-editable routing labels from host-owned gate state."""
        client = _file_tracker(self.cfg)
        try:
            current = client.fetch_issue_full_by_id(issue.identifier)
            if current is None:
                raise SymphonyError(
                    "release verifier cannot be read for identity reconciliation",
                    identifier=issue.identifier,
                )
            managed_prefixes = (
                _CONTRACT_HASH_LABEL_PREFIX,
                "release-fingerprint-",
                "release-finalizer-",
            )
            preserved = [
                label
                for label in current.labels
                if label.strip().lower()
                not in {"app-release", "release-cycle-verifier"}
                and not label.strip().lower().startswith(managed_prefixes)
            ]
            wanted = list(
                dict.fromkeys(
                    [
                        *preserved,
                        "app-release",
                        "release-cycle-verifier",
                        release_fingerprint_label(gate.cycle_fingerprint),
                        release_contract_hash_label(
                            gate.expected_contract_sha256
                        ),
                        release_finalizer_label(gate.finalizer_identifier),
                    ]
                )
            )
            if list(current.labels) != wanted or (
                verifier_state is not None
                and normalize_state(current.state) != normalize_state(verifier_state)
            ):
                client.update_fields(
                    current.identifier,
                    labels=wanted,
                    state=verifier_state,
                )
            persisted = client.fetch_issue_full_by_id(current.identifier)
            required = {
                "app-release",
                "release-cycle-verifier",
                release_fingerprint_label(gate.cycle_fingerprint),
                release_contract_hash_label(gate.expected_contract_sha256),
                release_finalizer_label(gate.finalizer_identifier),
            }
            if persisted is None or not required.issubset(
                normalized_label_set(persisted)
            ) or (
                verifier_state is not None
                and normalize_state(persisted.state)
                != normalize_state(verifier_state)
            ):
                raise SymphonyError(
                    "release verifier identity labels were not durably reconciled",
                    identifier=issue.identifier,
                )
            return persisted
        finally:
            client.close()

    def rewind_transition(
        self,
        *,
        issue: Issue,
        producing_state: str,
        note_body: str,
    ) -> Issue:
        """Write state and gate note in one atomic file-ticket mutation."""
        if self.cfg.tracker.kind != "file":
            raise SymphonyError(
                "app-release rewinds require the atomic local file tracker",
                tracker_kind=self.cfg.tracker.kind,
            )
        client = _file_tracker(self.cfg)
        try:
            current = client.fetch_issue_full_by_id(issue.identifier)
            if current is None:
                raise SymphonyError(
                    "app-release verifier could not be read before rewind",
                    identifier=issue.identifier,
                )
            clean_body = note_body.strip()
            note = "## App Release Gate Failure"
            if clean_body:
                note += f"\n\n{clean_body}"
            description = "\n\n".join(
                part
                for part in ((current.description or "").strip(), note)
                if part
            )
            client.update_fields(
                current.identifier,
                state=producing_state,
                description=description,
            )
            persisted = client.fetch_issue_full_by_id(current.identifier)
            if (
                persisted is None
                or normalize_state(persisted.state)
                != normalize_state(producing_state)
                or "## App Release Gate Failure" not in (persisted.description or "")
                or (clean_body and clean_body not in (persisted.description or ""))
            ):
                raise SymphonyError(
                    "app-release rewind was not durably persisted",
                    identifier=issue.identifier,
                )
            return persisted
        finally:
            client.close()

    def reopen_after_target_change(
        self,
        *,
        finalizer: Issue,
        gate: ReleaseGate,
        expected_contract_sha256: str,
        reason: str,
        finalizer_state: str | None = None,
    ) -> Issue:
        """Reopen the bound verifier after registry approval is invalidated.

        The registry pending replacement happens before this method is called,
        so a crash between the two file-ticket writes cannot authorize the
        finalizer.
        """
        verifier_state = release_verifier_state(self.cfg)
        client = _file_tracker(self.cfg)
        try:
            verifier = client.fetch_issue_full_by_id(gate.verifier_identifier)
            persisted_finalizer = client.fetch_issue_full_by_id(
                finalizer.identifier
            )
            if verifier is None or persisted_finalizer is None:
                raise SymphonyError(
                    "release gate binding cannot be reopened",
                    verifier=gate.verifier_identifier,
                    finalizer=finalizer.identifier,
                )
            managed_prefixes = (
                _CONTRACT_HASH_LABEL_PREFIX,
                "release-finalizer-",
            )
            preserved = [
                label
                for label in verifier.labels
                if not label.strip().lower().startswith(managed_prefixes)
                and label.strip().lower()
                not in {"app-release", "release-cycle-verifier"}
            ]
            labels = list(
                dict.fromkeys(
                    [
                        *preserved,
                        "app-release",
                        "release-cycle-verifier",
                        release_contract_hash_label(expected_contract_sha256),
                        release_finalizer_label(finalizer.identifier),
                    ]
                )
            )
            note = (
                "## Release Approval Invalidated\n\n"
                f"{reason.strip()}\n\n"
                "Fresh native evidence is required before finalizer dispatch."
            )
            description = "\n\n".join(
                part
                for part in ((verifier.description or "").strip(), note)
                if part
            )
            client.update_fields(
                verifier.identifier,
                state=verifier_state,
                labels=labels,
                description=description,
            )
            blocker_ids = _blocker_identifiers(persisted_finalizer)
            wanted_blockers = list(
                dict.fromkeys([*blocker_ids, verifier.identifier])
            )
            client.update_fields(
                finalizer.identifier,
                blocked_by=wanted_blockers,
                state=finalizer_state,
            )
            reopened = client.fetch_issue_full_by_id(verifier.identifier)
            rebound = client.fetch_issue_full_by_id(finalizer.identifier)
            rebound_ids = (
                set(_blocker_identifiers(rebound))
                if rebound is not None
                else set()
            )
            if (
                reopened is None
                or normalize_state(reopened.state) != normalize_state(verifier_state)
                or release_contract_hash_label(expected_contract_sha256)
                not in normalized_label_set(reopened)
                or verifier.identifier not in rebound_ids
                or (
                    finalizer_state is not None
                    and (
                        rebound is None
                        or normalize_state(rebound.state)
                        != normalize_state(finalizer_state)
                    )
                )
            ):
                raise SymphonyError(
                    "release verifier reopen was not durably persisted",
                    verifier=verifier.identifier,
                    finalizer=finalizer.identifier,
                )
            return reopened
        finally:
            client.close()

    def reconcile(
        self,
        source_issue: Issue,
        validation: ReleaseValidationResult,
        source_agent_kind: str,
        *,
        before_finalizer_relink: PendingGateWriter | None = None,
    ) -> ReleaseCycleWriteResult:
        """Create or reconcile one durable local-file-board repair cycle."""
        if self.cfg.tracker.kind != "file":
            return ReleaseCycleWriteResult(
                passed=False,
                error=(
                    "app-release repair creation is unavailable for tracker "
                    f"kind {self.cfg.tracker.kind!r}; atomic local create/update "
                    "support is required"
                ),
            )
        try:
            verifier_state = release_verifier_state(self.cfg)
        except Exception as exc:
            return ReleaseCycleWriteResult(passed=False, error=str(exc))
        client = _file_tracker(self.cfg)
        registry = RunRegistry(registry_path_for_workflow(self.cfg.workflow_path))
        try:
            create_ticket = client.create
            update_ticket = client.update_fields
            fetch_issue = client.fetch_issue_full_by_id
            fetch_issues = client.fetch_issues_by_states

            states = tuple(
                dict.fromkeys(
                    (*self.cfg.tracker.active_states, *self.cfg.tracker.terminal_states)
                )
            )
            existing = fetch_issues(states)
            fingerprint_label = release_fingerprint_label(validation.fingerprint)
            contract_hash_label = release_contract_hash_label(
                validation.contract_sha256
            )
            finalizer_label = release_finalizer_label(validation.finalizer_ticket)
            grouped = group_release_failures(validation.repairable_failures)
            repair_state = release_repair_state(self.cfg)
            repair_ids: list[str] = []

            for repair_group, failures in grouped.items():
                group_label = release_group_label(repair_group)
                repair_description = release_repair_description(
                    source=source_issue,
                    source_agent_kind=source_agent_kind,
                    result=validation,
                    repair_group=repair_group,
                    failures=failures,
                )
                matches = [
                    candidate
                    for candidate in existing
                    if {
                        fingerprint_label,
                        group_label,
                        "quality-fix",
                    }.issubset(normalized_label_set(candidate))
                ]
                if len(matches) > 1:
                    return ReleaseCycleWriteResult(
                        passed=False,
                        error=(
                            "multiple repair tickets carry fingerprint "
                            f"{validation.fingerprint} and group {repair_group}"
                        ),
                    )
                recorded_repair = registry.get_release_cycle_item(
                    finalizer_identifier=validation.finalizer_ticket,
                    cycle_fingerprint=validation.fingerprint,
                    item_role="repair",
                    item_key=repair_group,
                )
                if recorded_repair is not None:
                    recorded_issue = fetch_issue(recorded_repair.identifier)
                    if recorded_issue is None:
                        try:
                            create_ticket(
                                identifier=recorded_repair.identifier,
                                title=f"Release quality repair: {repair_group}",
                                state=repair_state,
                                priority=source_issue.priority,
                                labels=[
                                    "quality-fix",
                                    fingerprint_label,
                                    group_label,
                                ],
                                description=repair_description,
                                agent_kind=source_agent_kind,
                                request=source_issue.request,
                            )
                        except SymphonyError:
                            # A peer may have created the same reserved id
                            # after our read.  Exact readback below decides
                            # whether the reservation can be resumed.
                            pass
                        recorded_issue = fetch_issue(recorded_repair.identifier)
                    if recorded_issue is None:
                        raise SymphonyError(
                            "reserved release repair ticket could not be created",
                            identifier=recorded_repair.identifier,
                            repair_group=repair_group,
                        )
                    matches = [recorded_issue]
                elif matches:
                    recorded_repair = registry.record_release_cycle_item(
                        finalizer_identifier=validation.finalizer_ticket,
                        cycle_fingerprint=validation.fingerprint,
                        item_role="repair",
                        item_key=repair_group,
                        issue=matches[0],
                    )
                else:
                    identifier = _release_cycle_item_identifier(
                        prefix="QUALITY",
                        finalizer_identifier=validation.finalizer_ticket,
                        cycle_fingerprint=validation.fingerprint,
                        item_role="repair",
                        item_key=repair_group,
                    )
                    if fetch_issue(identifier) is not None:
                        raise SymphonyError(
                            "deterministic release repair id is already unowned",
                            identifier=identifier,
                            repair_group=repair_group,
                        )
                    recorded_repair = registry.reserve_release_cycle_item(
                        finalizer_identifier=validation.finalizer_ticket,
                        cycle_fingerprint=validation.fingerprint,
                        item_role="repair",
                        item_key=repair_group,
                        identifier=identifier,
                    )
                    try:
                        create_ticket(
                            identifier=recorded_repair.identifier,
                            title=f"Release quality repair: {repair_group}",
                            state=repair_state,
                            priority=source_issue.priority,
                            labels=["quality-fix", fingerprint_label, group_label],
                            description=repair_description,
                            agent_kind=source_agent_kind,
                            request=source_issue.request,
                        )
                    except SymphonyError:
                        pass
                    recorded_issue = fetch_issue(recorded_repair.identifier)
                    if recorded_issue is None:
                        raise SymphonyError(
                            "reserved release repair ticket could not be created",
                            identifier=recorded_repair.identifier,
                            repair_group=repair_group,
                        )
                    matches = [recorded_issue]
                required_markers = (
                    validation.fingerprint,
                    validation.contract_sha256,
                    validation.target_sha,
                    *(failure.check_id for failure in failures),
                )
                repair = matches[0]
                registry.record_release_cycle_item(
                    finalizer_identifier=validation.finalizer_ticket,
                    cycle_fingerprint=validation.fingerprint,
                    item_role="repair",
                    item_key=repair_group,
                    issue=repair,
                )
                current_body = repair.description or ""
                if all(marker in current_body for marker in required_markers):
                    wanted_body = current_body
                else:
                    preserved = current_body.strip()
                    wanted_body = repair_description
                    if preserved:
                        wanted_body += (
                            "\n## Reconciled partial content\n\n"
                            + preserved
                            + "\n"
                        )
                wanted_labels = list(
                    dict.fromkeys(
                        [
                            *repair.labels,
                            "quality-fix",
                            fingerprint_label,
                            group_label,
                        ]
                    )
                )
                if (
                    normalize_state(repair.state) != normalize_state(repair_state)
                    or repair.agent_kind != source_agent_kind
                    or (repair.request or "") != (source_issue.request or "")
                    or list(repair.labels) != wanted_labels
                    or current_body != wanted_body
                ):
                    update_ticket(
                        repair.identifier,
                        state=repair_state,
                        priority=source_issue.priority,
                        labels=wanted_labels,
                        description=wanted_body,
                        agent_kind=source_agent_kind,
                        request=source_issue.request or "",
                    )
                identifier = repair.identifier
                persisted = fetch_issue(identifier)
                if persisted is None:
                    raise SymphonyError(
                        "repair ticket could not be read back", identifier=identifier
                    )
                registry.record_release_cycle_item(
                    finalizer_identifier=validation.finalizer_ticket,
                    cycle_fingerprint=validation.fingerprint,
                    item_role="repair",
                    item_key=repair_group,
                    issue=persisted,
                )
                persisted_labels = normalized_label_set(persisted)
                if (
                    normalize_state(persisted.state) != normalize_state(repair_state)
                    or persisted.agent_kind != source_agent_kind
                    or (persisted.request or "") != (source_issue.request or "")
                    or not {
                        "quality-fix",
                        fingerprint_label,
                        group_label,
                    }.issubset(persisted_labels)
                    or not all(
                        marker in (persisted.description or "")
                        for marker in required_markers
                    )
                ):
                    raise SymphonyError(
                        "repair ticket could not be durably reconciled",
                        identifier=identifier,
                    )
                repair_ids.append(identifier)
                if all(item.identifier != persisted.identifier for item in existing):
                    existing.append(persisted)

            verifier_matches = [
                candidate
                for candidate in existing
                if {
                    fingerprint_label,
                    "release-cycle-verifier",
                }.issubset(normalized_label_set(candidate))
            ]
            recorded_verifier = registry.get_release_cycle_item(
                finalizer_identifier=validation.finalizer_ticket,
                cycle_fingerprint=validation.fingerprint,
                item_role="verifier",
                item_key="fresh-verifier",
            )
            if recorded_verifier is not None:
                recorded_issue = fetch_issue(recorded_verifier.identifier)
                if recorded_issue is None:
                    try:
                        create_ticket(
                            identifier=recorded_verifier.identifier,
                            title=(
                                "Fresh release verification after "
                                f"{source_issue.identifier}"
                            ),
                            state=verifier_state,
                            priority=source_issue.priority,
                            labels=[
                                "app-release",
                                "release-cycle-verifier",
                                fingerprint_label,
                                contract_hash_label,
                                finalizer_label,
                            ],
                            description=release_verifier_description(
                                source=source_issue, result=validation
                            ),
                            agent_kind=source_agent_kind,
                            blocked_by=repair_ids,
                            request=source_issue.request,
                        )
                    except SymphonyError:
                        pass
                    recorded_issue = fetch_issue(recorded_verifier.identifier)
                if recorded_issue is None:
                    raise SymphonyError(
                        "reserved fresh release verifier could not be created",
                        identifier=recorded_verifier.identifier,
                    )
                verifier_matches = [recorded_issue]
            if len(verifier_matches) > 1:
                return ReleaseCycleWriteResult(
                    passed=False,
                    repair_identifiers=tuple(repair_ids),
                    error=(
                        "multiple fresh verifier tickets carry fingerprint "
                        f"{validation.fingerprint}"
                    ),
                )
            if verifier_matches and recorded_verifier is None:
                recorded_verifier = registry.record_release_cycle_item(
                    finalizer_identifier=validation.finalizer_ticket,
                    cycle_fingerprint=validation.fingerprint,
                    item_role="verifier",
                    item_key="fresh-verifier",
                    issue=verifier_matches[0],
                )
            elif not verifier_matches:
                identifier = _release_cycle_item_identifier(
                    prefix="RELEASE-VERIFY",
                    finalizer_identifier=validation.finalizer_ticket,
                    cycle_fingerprint=validation.fingerprint,
                    item_role="verifier",
                    item_key="fresh-verifier",
                )
                if fetch_issue(identifier) is not None:
                    raise SymphonyError(
                        "deterministic fresh verifier id is already unowned",
                        identifier=identifier,
                    )
                recorded_verifier = registry.reserve_release_cycle_item(
                    finalizer_identifier=validation.finalizer_ticket,
                    cycle_fingerprint=validation.fingerprint,
                    item_role="verifier",
                    item_key="fresh-verifier",
                    identifier=identifier,
                )
                try:
                    create_ticket(
                        identifier=recorded_verifier.identifier,
                        title=(
                            "Fresh release verification after "
                            f"{source_issue.identifier}"
                        ),
                        state=verifier_state,
                        priority=source_issue.priority,
                        labels=[
                            "app-release",
                            "release-cycle-verifier",
                            fingerprint_label,
                            contract_hash_label,
                            finalizer_label,
                        ],
                        description=release_verifier_description(
                            source=source_issue, result=validation
                        ),
                        agent_kind=source_agent_kind,
                        blocked_by=repair_ids,
                        request=source_issue.request,
                    )
                except SymphonyError:
                    pass
                created_verifier = fetch_issue(recorded_verifier.identifier)
                if created_verifier is None:
                    raise SymphonyError(
                        "reserved fresh release verifier could not be created",
                        identifier=recorded_verifier.identifier,
                    )
                verifier_matches = [created_verifier]

            verifier = verifier_matches[0]
            registry.record_release_cycle_item(
                finalizer_identifier=validation.finalizer_ticket,
                cycle_fingerprint=validation.fingerprint,
                item_role="verifier",
                item_key="fresh-verifier",
                issue=verifier,
            )
            verifier_identifier = verifier.identifier
            current_blockers = _blocker_identifiers(verifier)
            wanted_blockers = list(dict.fromkeys([*current_blockers, *repair_ids]))
            expected_verifier_blockers = wanted_blockers
            managed_label_prefixes = (
                _CONTRACT_HASH_LABEL_PREFIX,
                "release-fingerprint-",
                "release-finalizer-",
            )
            preserved_labels = [
                label
                for label in verifier.labels
                if label.strip().lower()
                not in {"app-release", "release-cycle-verifier"}
                and not label.strip().lower().startswith(managed_label_prefixes)
            ]
            wanted_labels = list(
                dict.fromkeys(
                    [
                        *preserved_labels,
                        "app-release",
                        "release-cycle-verifier",
                        fingerprint_label,
                        contract_hash_label,
                        finalizer_label,
                    ]
                )
            )
            verifier_description = release_verifier_description(
                source=source_issue, result=validation
            )
            required_markers = (
                source_issue.identifier,
                validation.fingerprint,
                validation.contract_sha256,
                validation.target_sha,
                validation.finalizer_ticket,
            )
            current_body = verifier.description or ""
            if all(marker in current_body for marker in required_markers):
                wanted_body = current_body
            else:
                wanted_body = verifier_description
                if current_body.strip():
                    wanted_body += (
                        "\n## Reconciled partial content\n\n"
                        + current_body.strip()
                        + "\n"
                    )
            if (
                normalize_state(verifier.state) != normalize_state(verifier_state)
                or verifier.agent_kind != source_agent_kind
                or list(verifier.labels) != wanted_labels
                or current_blockers != wanted_blockers
                or current_body != wanted_body
                or verifier.request != source_issue.request
            ):
                update_ticket(
                    verifier_identifier,
                    state=verifier_state,
                    priority=source_issue.priority,
                    labels=wanted_labels,
                    description=wanted_body,
                    agent_kind=source_agent_kind,
                    blocked_by=wanted_blockers,
                    request=source_issue.request or "",
                )

            persisted_verifier = fetch_issue(verifier_identifier)
            if persisted_verifier is None:
                raise SymphonyError(
                    "fresh verifier ticket could not be read back",
                    identifier=verifier_identifier,
                )
            registry.record_release_cycle_item(
                finalizer_identifier=validation.finalizer_ticket,
                cycle_fingerprint=validation.fingerprint,
                item_role="verifier",
                item_key="fresh-verifier",
                issue=persisted_verifier,
            )
            persisted_verifier_labels = normalized_label_set(persisted_verifier)
            persisted_verifier_blockers = _blocker_identifiers(
                persisted_verifier
            )
            persisted_verifier_body = persisted_verifier.description or ""
            if (
                normalize_state(persisted_verifier.state)
                != normalize_state(verifier_state)
                or persisted_verifier.agent_kind != source_agent_kind
                or (persisted_verifier.request or "")
                != (source_issue.request or "")
                or not {
                    "app-release",
                    "release-cycle-verifier",
                    fingerprint_label,
                    contract_hash_label,
                    finalizer_label,
                }.issubset(persisted_verifier_labels)
                or persisted_verifier_blockers != expected_verifier_blockers
                or not all(
                    marker in persisted_verifier_body
                    for marker in (
                        source_issue.identifier,
                        validation.fingerprint,
                        validation.contract_sha256,
                        validation.target_sha,
                        validation.finalizer_ticket,
                    )
                )
            ):
                raise SymphonyError(
                    "fresh verifier ticket could not be durably reconciled",
                    identifier=verifier_identifier,
                )

            finalizer = fetch_issue(validation.finalizer_ticket)
            if finalizer is None:
                raise SymphonyError(
                    "release finalizer ticket does not exist",
                    identifier=validation.finalizer_ticket,
                )
            if "app-release-finalizer" not in normalized_label_set(finalizer):
                raise SymphonyError(
                    "release finalizer ticket lacks app-release-finalizer label",
                    identifier=validation.finalizer_ticket,
                )

            # The host-owned pending gate is written before this dependency
            # mutation.  If registry persistence fails, the finalizer remains
            # linked to the historical verifier and cannot race ahead.
            if before_finalizer_relink is not None:
                before_finalizer_relink(persisted_verifier)

            finalizer_blockers = _blocker_identifiers(finalizer)
            superseded_verifiers = {source_issue.identifier}
            superseded_verifiers.update(
                candidate.identifier
                for candidate in existing
                if candidate.identifier != verifier_identifier
                and "release-cycle-verifier" in normalized_label_set(candidate)
                and finalizer_label in normalized_label_set(candidate)
            )
            unrelated_blockers = [
                blocker
                for blocker in finalizer_blockers
                if blocker not in superseded_verifiers
                and blocker != verifier_identifier
            ]
            wanted_finalizer_blockers = list(
                dict.fromkeys([*unrelated_blockers, verifier_identifier])
            )
            if wanted_finalizer_blockers != finalizer_blockers:
                update_ticket(
                    finalizer.identifier,
                    blocked_by=wanted_finalizer_blockers,
                )
            persisted_finalizer = fetch_issue(finalizer.identifier)
            persisted_finalizer_blockers = (
                _blocker_identifiers(persisted_finalizer)
                if persisted_finalizer is not None
                else []
            )
            if persisted_finalizer_blockers != wanted_finalizer_blockers:
                raise SymphonyError(
                    "fresh verifier dependency was not durably relinked to finalizer",
                    finalizer=finalizer.identifier,
                    verifier=verifier_identifier,
                )

            return ReleaseCycleWriteResult(
                passed=True,
                repair_identifiers=tuple(repair_ids),
                verifier_identifier=verifier_identifier,
            )
        except Exception as exc:
            return ReleaseCycleWriteResult(passed=False, error=str(exc))
        finally:
            registry.close()
            client.close()


__all__ = [
    "ReleaseCycleService",
    "ReleaseCycleWriteResult",
    "group_release_failures",
    "has_active_verify_lane",
    "has_release_finalizer_lane",
    "has_release_success_terminal",
    "initial_release_gate_fingerprint",
    "is_release_cycle_verifier",
    "is_release_evidence_issue",
    "is_release_finalizer",
    "is_release_success_state",
    "normalized_label_set",
    "release_contract_hash_label",
    "release_expected_hash_labels",
    "release_failure_target_state",
    "release_finalizer_label",
    "release_fingerprint_label",
    "release_group_label",
    "release_repair_description",
    "release_repair_state",
    "release_ticket_version_token",
    "release_verifier_description",
    "release_verifier_state",
]
