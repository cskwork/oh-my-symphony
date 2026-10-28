"""Stable PM domain, port, and JSON response contracts.

The browser, core services, and optional exchange/OpenSpec adapters share
these immutable shapes. Persistence belongs to core services; ports return
data only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence, runtime_checkable

PlanningState = Literal[
    "Draft", "Validated", "NeedsReview", "Approved", "Stale", "Archived"
]
FindingSeverity = Literal["info", "warning", "error", "blocking"]
SemanticKind = Literal["requirement", "scenario", "storyboard", "contract", "decision"]
OperationState = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "reconcile",
]


class PmError(Exception):
    """Stable PM error suitable for the shared JSON error wrapper."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def as_json(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": dict(self.details),
            }
        }


@dataclass(frozen=True)
class PmFinding:
    code: str
    message: str
    severity: FindingSeverity = "error"
    document_id: str | None = None
    item_id: str | None = None
    field: str | None = None
    path: tuple[str, ...] = ()
    fix: str | None = None


@dataclass(frozen=True)
class PmRequestContext:
    project_id: str
    change_id: str
    expected_revision: str | None = None
    client_operation_id: str | None = None

    @classmethod
    def from_json(
        cls,
        body: dict[str, Any],
        *,
        change_id: str | None = None,
        require_revision: bool = False,
        require_operation: bool = False,
    ) -> "PmRequestContext":
        project_id = _required_text(body, "project_id")
        resolved_change = change_id or _required_text(body, "change_id")
        expected = _optional_text(body.get("expected_revision"))
        operation = _optional_text(body.get("client_operation_id"))
        if require_revision and expected is None:
            raise PmError("revision_conflict", "expected_revision is required", status=409)
        if require_operation and operation is None:
            raise PmError(
                "invalid_operation", "client_operation_id is required", status=400
            )
        return cls(project_id, resolved_change, expected, operation)


@dataclass(frozen=True)
class PmResult:
    project_id: str
    change_id: str
    previous_revision: str | None
    revision: str
    changed: tuple[str, ...] = ()
    findings: tuple[PmFinding, ...] = ()
    impacted_ids: tuple[str, ...] = ()
    approval_invalidated: bool = False
    operation_id: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "change_id": self.change_id,
            "previous_revision": self.previous_revision,
            "revision": self.revision,
            "changed": list(self.changed),
            "findings": [_json(item) for item in self.findings],
            "impacted_ids": list(self.impacted_ids),
            "approval_invalidated": self.approval_invalidated,
            "operation_id": self.operation_id,
        }


@dataclass(frozen=True)
class DocumentView:
    document_id: str
    path: str
    source: str
    content_hash: str
    state: str
    editable: bool
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class SemanticItem:
    semantic_id: str
    kind: SemanticKind
    title: str
    document_id: str
    path: str
    line: int
    retired: bool = False


@dataclass(frozen=True)
class StoryboardFrame:
    storyboard_id: str
    title: str
    order: int
    document_id: str
    screen_route: str = ""
    actor_goal: str = ""
    initial_state: str = ""
    ui: str = ""
    user_action: str = ""
    system_response: str = ""
    success: str = ""
    alternate: str = ""
    failure: str = ""
    requirement_refs: tuple[str, ...] = ()
    design_refs: tuple[str, ...] = ()
    legacy_combined: bool = False


@dataclass(frozen=True)
class PlanningSection:
    section_id: str
    document_id: str
    label: str


@dataclass(frozen=True)
class OwnedDecision:
    decision_id: str
    title: str
    owner: str
    impact: Literal["low", "medium", "high"]
    status: Literal["unresolved", "accepted"]
    reason: str = ""


@dataclass(frozen=True)
class ChangeView:
    project_id: str
    change_id: str
    locator: str
    revision: str
    scope_hash: str
    state: PlanningState
    base_git_sha: str
    schema_hash: str
    documents: tuple[DocumentView, ...] = ()
    findings: tuple[PmFinding, ...] = ()
    approval_id: str | None = None
    next_action: str = "validate"
    semantic_index: tuple[SemanticItem, ...] = ()
    decisions: tuple[OwnedDecision, ...] = ()
    affected_capabilities: tuple[str, ...] = ()
    storyboard_frames: tuple[StoryboardFrame, ...] = ()
    planning_sections: tuple[PlanningSection, ...] = ()


@dataclass(frozen=True)
class WbsDependency:
    task_id: str
    kind: Literal["requires_code", "order_only"]
    justification: str | None = None


@dataclass(frozen=True)
class VerificationProcedureSelection:
    procedure_id: str
    kind: Literal["native", "manual"]


@dataclass(frozen=True)
class WbsTask:
    task_id: str
    parent_id: str | None
    order: int
    kind: str
    title: str
    description: str = ""
    priority: str | None = None
    human_owner: str | None = None
    agent_kind_override: str | None = None
    dependencies: tuple[WbsDependency, ...] = ()
    requirement_refs: tuple[str, ...] = ()
    storyboard_refs: tuple[str, ...] = ()
    design_refs: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    verification_policy: tuple[VerificationProcedureSelection, ...] | None = None
    integration_impact: str = ""
    risk: str = ""
    notes: str = ""
    target_date: str | None = None
    estimate: str | None = None
    cancelled: bool = False
    cancellation_reason: str | None = None
    successor_refs: tuple[str, ...] = ()
    executable: bool = True


@dataclass(frozen=True)
class WbsView:
    project_id: str
    change_id: str
    revision: str
    tasks: tuple[WbsTask, ...]
    runtime_projection: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: tuple[PmFinding, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    findings: tuple[PmFinding, ...] = ()
    source: str = "pm"
    profile: str | None = None


@dataclass(frozen=True)
class VerificationReport:
    valid: bool
    tested_commit: str
    covered_task_ids: tuple[str, ...]
    missing_task_ids: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    findings: tuple[PmFinding, ...] = ()


@dataclass(frozen=True)
class VerificationReceipt:
    receipt_id: str
    operation_id: str
    run_id: str | None
    project_id: str
    ticket_identifier: str
    task_revision: str
    procedure_id: str
    command_digest: str
    tested_commit: str
    return_code: int | None
    timed_out: bool
    cancelled: bool
    stdout_hash: str
    stderr_hash: str
    started_at: str
    completed_at: str
    artifact_refs: tuple[str, ...]
    result: Literal["passed", "failed"]
    provenance_kind: Literal["native", "manual"] = "native"
    procedure_kind: Literal["native", "manual"] = "native"
    definition_digest: str = ""
    change_id: str = ""
    task_id: str = ""
    environment_summary: str = ""
    submitted_at: str = ""
    submitted_by: str = ""
    report_ref: dict[str, Any] | None = None


@dataclass(frozen=True)
class ManualEvidencePreview:
    ready: bool
    confirmation_token: str
    findings: tuple[PmFinding, ...]
    provenance_kind: Literal["manual"]
    normalized: dict[str, Any]


@dataclass(frozen=True)
class CoverageState:
    satisfied: bool
    qualified_count: int
    total_count: int
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverageTaskLink:
    task_id: str
    task_revision: str
    ticket_identifier: str | None
    implemented: bool
    implementation_commit: str | None
    tested: bool
    test_provenance: Literal["host_executed", "manual_reported", "mixed"] | None
    receipt_ids: tuple[str, ...]
    accepted: bool
    acceptance_id: str | None


@dataclass(frozen=True)
class CoverageEntity:
    semantic_id: str
    kind: Literal["requirement", "scenario"]
    title: str
    document_id: str
    required_task_ids: tuple[str, ...]
    states: dict[str, CoverageState]
    task_links: tuple[CoverageTaskLink, ...]
    waivers: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CoverageReport:
    project_id: str
    change_id: str
    revision: str
    scope_hash: str
    entities: tuple[CoverageEntity, ...]
    findings: tuple[PmFinding, ...] = ()


@dataclass(frozen=True)
class AcceptancePreview:
    project_id: str
    ticket_identifier: str
    task_revision: str
    tested_commit: str
    required_procedure_ids: tuple[str, ...]
    passing_receipt_ids: tuple[str, ...]
    missing_procedure_ids: tuple[str, ...]
    ready: bool
    confirmation_token: str
    findings: tuple[PmFinding, ...] = ()


@dataclass(frozen=True)
class AcceptanceRecord:
    acceptance_id: str
    project_id: str
    ticket_identifier: str
    task_revision: str
    tested_commit: str
    receipt_ids: tuple[str, ...]
    accepted_by: str
    accepted_at: str
    state: Literal["ACCEPTED", "SUPERSEDED"] = "ACCEPTED"


def canonical_receipt_set_hash(receipt_ids: Sequence[str]) -> str:
    """Stable authority hash independent of caller or procedure order."""
    return "sha256:" + hashlib.sha256(
        json.dumps(sorted(receipt_ids), separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class WorkspaceClaim:
    project_id: str
    change_id: str
    path: str
    base_git_sha: str
    owner: str
    fence: int
    state: str
    dirty_paths: tuple[str, ...] = ()

    @property
    def workspace_path(self) -> Path:
        return Path(self.path)


@dataclass(frozen=True)
class PmMembership:
    managed: bool
    runnable: bool
    project_id: str
    ticket_identifier: str
    change_id: str | None = None
    task_id: str | None = None
    task_revision: str | None = None
    handoff_id: str | None = None
    scope_hash: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class DispatchPermit:
    permit_id: str
    project_id: str
    ticket_identifier: str
    handoff_id: str
    task_revision: str
    issue_hash: str
    scope_hash: str
    fence: int
    state: str


@dataclass(frozen=True)
class ApprovalManifest:
    approval_id: str
    project_id: str
    change_id: str
    revision: str
    scope_hash: str
    base_git_sha: str
    schema_hash: str
    file_hashes: dict[str, str]
    baseline_hashes: dict[str, str]
    validation_digest: str
    reviewer_label: str
    approved_at: str
    required_procedures: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class HandoffItem:
    task_id: str
    task_revision: str
    deterministic_ticket_id: str
    blocked_by: tuple[str, ...] = ()
    agent_kind: str | None = None
    runnable: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class HandoffPreview:
    preview_token: str
    project_id: str
    change_id: str
    revision: str
    scope_hash: str
    target_state: str
    items: tuple[HandoffItem, ...]
    findings: tuple[PmFinding, ...] = ()


@dataclass(frozen=True)
class HandoffResult:
    operation_id: str
    handoff_id: str
    state: str
    project_id: str
    change_id: str
    revision: str
    ticket_identifiers: tuple[str, ...] = ()
    findings: tuple[PmFinding, ...] = ()


@dataclass(frozen=True)
class OperationView:
    operation_id: str
    kind: str
    state: OperationState
    project_id: str
    change_id: str
    expected_revision: str | None
    result: dict[str, Any] | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class OpenSpecProfile:
    available: bool
    version: str | None
    schema_supported: bool
    commands: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class OpenSpecStatus:
    artifacts: tuple[dict[str, Any], ...]
    planning_complete: bool


@dataclass(frozen=True)
class OpenSpecTasks:
    total: int
    complete: int
    remaining: int
    tasks: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ArchiveCandidate:
    project_id: str
    change_id: str
    revision: str
    controlled_workspace: str
    source_paths: tuple[str, ...]
    target_paths: tuple[str, ...]
    findings: tuple[PmFinding, ...] = ()


@dataclass(frozen=True)
class ArchiveResult:
    archived_path: str
    baseline_paths: tuple[str, ...]
    output: dict[str, Any]


@dataclass(frozen=True)
class ExportPayload:
    filename: str
    content_type: str
    data: bytes
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ImportFieldChange:
    item_id: str
    field: str
    base: Any
    current: Any
    imported: Any
    action: str


@dataclass(frozen=True)
class ImportCandidate:
    file_hash: str
    filename: str
    base_revision: str
    current_revision: str
    preview_digest: str
    changes: tuple[ImportFieldChange, ...] = ()
    conflicts: tuple[ImportFieldChange, ...] = ()
    assigned_ids: dict[str, str] = field(default_factory=dict)
    findings: tuple[PmFinding, ...] = ()


@runtime_checkable
class OpenSpecPort(Protocol):
    def capabilities(self, project_root: Path) -> OpenSpecProfile: ...

    def status(self, project_root: Path, change_locator: str) -> OpenSpecStatus: ...

    def apply_instructions(
        self, project_root: Path, change_locator: str
    ) -> OpenSpecTasks: ...

    def validate(
        self, project_root: Path, change_locator: str
    ) -> ValidationReport: ...

    def archive_preview(
        self, project_root: Path, change_locator: str
    ) -> ArchiveCandidate: ...

    def archive(
        self, candidate: ArchiveCandidate, controlled_workspace: Path
    ) -> ArchiveResult: ...


@runtime_checkable
class ExchangePort(Protocol):
    def export(
        self, change_snapshot: ChangeView, runtime_projection: dict[str, Any]
    ) -> ExportPayload: ...

    def preview_import(
        self,
        file_bytes: bytes,
        trusted_export_snapshot: ChangeView,
        current_change: ChangeView,
        *,
        filename: str = "workbook.xlsx",
    ) -> ImportCandidate: ...

    def render_csv(
        self, change_snapshot: ChangeView, *, excel_utf8: bool
    ) -> bytes: ...


def _json(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_json(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _required_text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PmError("invalid_request", f"{key} is required")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PmError("invalid_request", "optional text fields must be strings")
    text = value.strip()
    return text or None
