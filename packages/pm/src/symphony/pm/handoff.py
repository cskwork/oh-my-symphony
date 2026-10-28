"""Crash-safe projection of approved executable leaves to native tickets."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from symphony.issue import PlanningRef
from symphony.orchestrator.run_registry import RunRegistry
from symphony.trackers.file import FileBoardTracker
from .documents import PmDocumentRepository
from .locks import change_authority_lock
from .models import (
    HandoffItem,
    HandoffPreview,
    HandoffResult,
    PmError,
    PmRequestContext,
    WbsTask,
)


class PmHandoffService:
    def __init__(
        self,
        repository: PmDocumentRepository,
        registry: RunRegistry,
        tracker: FileBoardTracker,
        *,
        target_state: str = "Todo",
        park_state: str = "Cancelled",
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.tracker = tracker
        self.target_state = target_state
        self.park_state = park_state

    def convert_legacy(
        self,
        ctx: PmRequestContext,
        source_ticket_id: str,
        workspace_path: str,
        locator: str,
        title: str,
        *,
        owner: str,
    ) -> dict[str, Any]:
        source = self.tracker.fetch_issue_full_by_id(source_ticket_id)
        if source is None:
            raise PmError("source_ticket_not_found", "legacy source ticket is missing", status=404)
        if self.registry.has_active_lease(source.id):
            raise PmError("source_ticket_running", "legacy source has an active run", status=409)
        try:
            conversion = self.registry.reserve_pm_legacy_conversion(
                project_id=ctx.project_id, change_id=ctx.change_id,
                source_ticket_identifier=source_ticket_id,
                workspace_path=str(Path(workspace_path).resolve()), locator=locator,
            )
        except ValueError as exc:
            if "active run" in str(exc):
                raise PmError("source_ticket_running", "legacy source has an active run", status=409) from exc
            raise
        marker = f"PM change: `{ctx.change_id}`"
        self.repository.claim_workspace(ctx.change_id, Path(workspace_path), owner)
        try:
            change = self.repository.create_change(change_id=ctx.change_id, locator=locator, title=title or source.title, owner=owner)
        except PmError as exc:
            if exc.code != "change_exists":
                raise
            change = self.repository.get_change(ctx, owner=owner)
        if marker not in (source.description or ""):
            self.tracker.append_note(
                source,
                "Superseded by PM planning",
                f"{marker}\n\nThis legacy executable request is parked. Only approved PM WBS tickets may implement this scope.",
            )
        self.tracker.update_fields(source_ticket_id, state=self.park_state)
        parked = self.tracker.fetch_issue_full_by_id(source_ticket_id)
        if parked is None or parked.state != self.park_state:
            raise PmError("conversion_reconcile", "legacy source was not durably parked", status=409)
        self.registry.set_pm_legacy_conversion_state(
            ctx.project_id, ctx.change_id, "COMPLETE"
        )
        return {"source_ticket": source_ticket_id, "change_id": ctx.change_id, "state": self.park_state, "revision": change.revision, "idempotent": str(conversion["state"]) == "COMPLETE"}

    def preview(self, ctx: PmRequestContext, *, owner: str) -> HandoffPreview:
        self.repository._claim(ctx.change_id, owner=owner)
        change = self.repository.get_change(ctx, owner=owner)
        if ctx.expected_revision is None or change.revision != ctx.expected_revision:
            raise PmError("revision_conflict", "handoff requires the current revision", status=409)
        approval = self.registry.get_active_pm_approval(ctx.project_id, ctx.change_id)
        if approval is None or (
            str(approval["scope_hash"]), str(approval["revision_hash"])
        ) != (change.scope_hash, change.revision):
            raise PmError("approval_required", "current scope is not approved", status=409)
        tasks = tuple(task for task in self.repository.get_wbs(ctx, owner=owner).tasks if task.executable)
        if not tasks:
            raise PmError("empty_plan", "approved plan has no executable tasks", status=409)
        task_ids = {task.task_id for task in tasks}
        identifiers = {task.task_id: _ticket_id(ctx.change_id, task.task_id, _task_revision(task)) for task in tasks}
        items: list[HandoffItem] = []
        findings = []
        for task in tasks:
            missing = [dep.task_id for dep in task.dependencies if dep.task_id not in task_ids]
            if missing:
                raise PmError("dangling_dependency", f"task {task.task_id} has unresolved dependencies", status=409, details={"task_ids": missing})
            revision = _task_revision(task)
            blocked = tuple(identifiers[dep.task_id] for dep in task.dependencies)
            items.append(HandoffItem(task.task_id, revision, identifiers[task.task_id], blocked, task.agent_kind_override, not blocked, "waiting for prerequisites" if blocked else None))
        token = _preview_token(ctx.project_id, ctx.change_id, change.revision, change.scope_hash, items)
        return HandoffPreview(token, ctx.project_id, ctx.change_id, change.revision, change.scope_hash, self.target_state, tuple(items), tuple(findings))

    def publish(
        self,
        ctx: PmRequestContext,
        preview_token: str,
        confirmation_token: str,
        *,
        owner: str,
    ) -> HandoffResult:
        with change_authority_lock(self.registry, ctx.project_id, ctx.change_id):
            return self._publish_locked(
                ctx, preview_token, confirmation_token, owner=owner
            )

    def _publish_locked(
        self,
        ctx: PmRequestContext,
        preview_token: str,
        confirmation_token: str,
        *,
        owner: str,
    ) -> HandoffResult:
        if not ctx.client_operation_id:
            raise PmError("operation_id_required", "client_operation_id is required")
        preview = self.preview(ctx, owner=owner)
        if preview.preview_token != preview_token or confirmation_token != preview_token:
            raise PmError("confirmation_mismatch", "handoff preview is stale", status=409)
        approval = self.registry.get_active_pm_approval(ctx.project_id, ctx.change_id)
        assert approval is not None
        try:
            manifest = json.loads(Path(str(approval["manifest_ref"])).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PmError("approval_manifest_invalid", "approved manifest is unavailable", status=409) from exc
        manifest_procedures = manifest.get("required_procedures", {})
        handoff_id = uuid.uuid5(uuid.NAMESPACE_URL, f"symphony-pm:{ctx.client_operation_id}").hex
        tasks = {task.task_id: task for task in self.repository.get_wbs(ctx, owner=owner).tasks if task.executable}
        item_rows = []
        for item in preview.items:
            ticket_input = _ticket_input(
                tasks[item.task_id], item, ctx.change_id, handoff_id,
                str(approval["approval_id"]), preview.scope_hash,
                preview.target_state,
            )
            required = (
                manifest_procedures.get(item.task_id, {})
                if isinstance(manifest_procedures, dict) else {}
            )
            item_rows.append({
                "task_id": item.task_id,
                "task_revision": item.task_revision,
                "deterministic_ticket_id": item.deterministic_ticket_id,
                "content_hash": _input_hash(ticket_input),
                "input_hash": _full_input_hash(ticket_input),
                "input_json": json.dumps(ticket_input, sort_keys=True, separators=(",", ":")),
                "required_procedures_json": json.dumps(required, sort_keys=True, separators=(",", ":")),
            })
        created = self.registry.create_pm_handoff_batch(
            handoff_id=handoff_id, operation_id=ctx.client_operation_id,
            project_id=ctx.project_id, change_id=ctx.change_id,
            scope_hash=preview.scope_hash, approval_id=str(approval["approval_id"]),
            snapshot_id=str(approval["approval_id"]), items=item_rows,
        )
        inputs_by_task = {
            str(item["task_id"]): json.loads(str(item["input_json"]))
            for item in item_rows
        }
        if not created:
            batch = self.registry.get_pm_handoff_by_operation(ctx.client_operation_id)
            if batch is None:
                raise PmError("handoff_reconcile", "handoff operation is missing", status=409)
            handoff_id = str(batch["handoff_id"])
        for item in preview.items:
            task = tasks[item.task_id]
            ticket_input = inputs_by_task[item.task_id]
            existing = self.tracker.fetch_issue_full_by_id(item.deterministic_ticket_id)
            expected_ref = PlanningRef(ctx.change_id, task.task_id, item.task_revision, handoff_id, str(approval["approval_id"]))
            if existing is not None:
                if existing.planning_ref != expected_ref:
                    raise PmError("ticket_collision", f"ticket {item.deterministic_ticket_id} is unrelated", status=409)
                continue
            self.tracker.create_validated(
                identifier=item.deterministic_ticket_id,
                title=ticket_input["title"],
                state=ticket_input["state"],
                description=ticket_input["description"],
                agent_kind=ticket_input["agent_kind"],
                blocked_by=list(ticket_input["blocked_by"]),
                request=ticket_input["request"],
                planning_ref=expected_ref,
            )
        for item in preview.items:
            issue = self.tracker.fetch_issue_full_by_id(item.deterministic_ticket_id)
            _assert_issue_input(
                issue, inputs_by_task[item.task_id],
                _input_hash(inputs_by_task[item.task_id]),
            )
        mapping = {item.task_id: item.deterministic_ticket_id for item in preview.items}
        self.registry.publish_pm_handoff(handoff_id, ticket_identifiers=mapping)
        return HandoffResult(ctx.client_operation_id, handoff_id, "PUBLISHED", ctx.project_id, ctx.change_id, preview.revision, tuple(mapping.values()))

    def reconcile(self, operation_id: str) -> HandoffResult:
        batch = self.registry.get_pm_handoff_by_operation(operation_id)
        if batch is None:
            raise PmError("operation_not_found", "handoff operation is unknown", status=404)
        with change_authority_lock(
            self.registry, str(batch["project_id"]), str(batch["change_id"])
        ):
            return self._reconcile_locked(operation_id, batch)

    def _reconcile_locked(
        self, operation_id: str, batch: dict[str, Any]
    ) -> HandoffResult:
        items = self.registry.get_pm_handoff_items(str(batch["handoff_id"]))
        ctx = PmRequestContext(str(batch["project_id"]), str(batch["change_id"]))
        change = self.repository.get_change(ctx)
        if change.scope_hash != str(batch["scope_hash"]):
            raise PmError("handoff_reconcile", "planning scope changed after staging", status=409)
        identifiers: dict[str, str] = {}
        for item in items:
            identifier = str(item["deterministic_ticket_id"])
            issue = self.tracker.fetch_issue_full_by_id(identifier)
            try:
                ticket_input = json.loads(str(item["input_json"]))
            except json.JSONDecodeError as exc:
                raise PmError("handoff_reconcile", "immutable handoff input is corrupt", status=409) from exc
            if not isinstance(ticket_input, dict) or _full_input_hash(ticket_input) != str(item["input_hash"]):
                raise PmError("handoff_reconcile", "immutable handoff input hash changed", status=409)
            if issue is None:
                ref = PlanningRef(**ticket_input["planning_ref"])
                self.tracker.create_validated(
                    identifier=identifier, title=ticket_input["title"],
                    state=ticket_input["state"], description=ticket_input["description"],
                    agent_kind=ticket_input["agent_kind"],
                    blocked_by=list(ticket_input["blocked_by"]),
                    request=ticket_input["request"], planning_ref=ref,
                )
                issue = self.tracker.fetch_issue_full_by_id(identifier)
            try:
                _assert_issue_input(issue, ticket_input, str(item["content_hash"]))
            except PmError:
                raise PmError("handoff_reconcile", f"ticket {identifier} is missing or mismatched", status=409)
            identifiers[str(item["task_id"])] = identifier
        self.registry.publish_pm_handoff(str(batch["handoff_id"]), ticket_identifiers=identifiers)
        return HandoffResult(operation_id, str(batch["handoff_id"]), "PUBLISHED", str(batch["project_id"]), str(batch["change_id"]), "", tuple(identifiers.values()))


def _task_revision(task: WbsTask) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(asdict(task), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _ticket_input(
    task: WbsTask, item: HandoffItem, change_id: str, handoff_id: str,
    snapshot_id: str, scope_hash: str, target_state: str,
) -> dict[str, Any]:
    return {
        "identifier": item.deterministic_ticket_id,
        "title": task.title,
        "state": target_state,
        "description": _ticket_body(task, scope_hash).strip(),
        "agent_kind": task.agent_kind_override,
        "request": f"PM:{change_id}:{snapshot_id}",
        "blocked_by": list(item.blocked_by),
        "dependency_kinds": {
            identifier: dependency.kind
            for dependency, identifier in zip(task.dependencies, item.blocked_by, strict=True)
        },
        "planning_ref": {
            "change_id": change_id, "task_id": task.task_id,
            "task_revision": item.task_revision, "handoff_id": handoff_id,
            "snapshot_id": snapshot_id,
        },
    }


def _input_hash(value: dict[str, Any]) -> str:
    scope = {key: value[key] for key in ("title", "description", "agent_kind", "request", "blocked_by", "planning_ref")}
    return "sha256:" + hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _full_input_hash(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _assert_issue_input(issue: Any, expected: dict[str, Any], content_hash: str) -> None:
    if issue is None or _input_hash(expected) != content_hash:
        raise PmError("ticket_scope_mismatch", "managed ticket content changed", status=409)
    actual_blocked = [item.identifier or item.id for item in issue.blocked_by]
    actual_ref = asdict(issue.planning_ref) if issue.planning_ref is not None else None
    if (
        issue.title != expected["title"]
        or issue.state != expected["state"]
        or (issue.description or "").strip() != expected["description"]
        or issue.agent_kind != expected["agent_kind"]
        or issue.request != expected["request"]
        or actual_blocked != expected["blocked_by"]
        or actual_ref != expected["planning_ref"]
    ):
        raise PmError("ticket_scope_mismatch", "managed ticket content changed", status=409)


def _ticket_id(change_id: str, task_id: str, revision: str) -> str:
    value = hashlib.sha256(f"{change_id}:{task_id}:{revision}".encode()).hexdigest()[:12].upper()
    return f"PM-{value}"


def _preview_token(project_id: str, change_id: str, revision: str, scope_hash: str, items: list[HandoffItem]) -> str:
    payload = (project_id, change_id, revision, scope_hash, [asdict(item) for item in items])
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _ticket_body(task: WbsTask, scope_hash: str) -> str:
    return (
        f"## Goal\n\n{task.description or task.title}\n\n"
        "## Scope and exclusions\n\n"
        f"{task.description}\n\n"
        "## Acceptance and verification\n\n"
        + "\n".join(f"- {item}" for item in task.acceptance)
        + "\n\n"
        + "\n".join(f"- {item}" for item in task.verification)
        + f"\n\n## Integration impact\n\n{task.integration_impact}\n\n"
        f"## Planning snapshot\n\n- Scope: `{scope_hash}`\n- Task: `{task.task_id}`\n"
    )
