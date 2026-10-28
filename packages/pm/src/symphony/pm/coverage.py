"""Derived requirement/scenario coverage and explicit non-authoritative waivers."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal

from symphony.orchestrator.run_registry import RunRegistry
from .models import (
    CoverageEntity, CoverageReport, CoverageState, CoverageTaskLink,
    PmError, WbsTask,
)
from .models import canonical_receipt_set_hash
from .verification import _procedure_policy, _receipt_qualifies, successful_run_provenance


class PmCoverageService:
    def __init__(
        self, registry: RunRegistry,
        *, successful_terminal_states: tuple[str, ...] = ("Done",),
    ) -> None:
        self.registry = registry
        self.successful_terminal_states = frozenset(
            item.strip().casefold() for item in successful_terminal_states
            if item.strip()
        )

    def build(self, change: Any, tasks: tuple[WbsTask, ...]) -> CoverageReport:
        mappings = {
            row.task_id: row for row in self.registry.list_pm_managed_tickets(change.project_id)
            if row.change_id == change.change_id
        }
        ticket_ids = [row.ticket_identifier for row in mappings.values()]
        runs: dict[str, list[dict[str, Any]]] = {ticket: [] for ticket in ticket_ids}
        receipts: dict[tuple[str, str], list[dict[str, Any]]] = {}
        acceptances: dict[tuple[str, str], dict[str, Any]] = {}
        required_by_ticket: dict[str, dict[str, Any]] = {}
        if ticket_ids:
            marks = ",".join("?" for _ in ticket_ids)
            conn = self.registry._connect()
            for row in conn.execute(
                f"SELECT * FROM runs WHERE issue_id IN ({marks}) ORDER BY updated_at DESC", ticket_ids
            ).fetchall():
                runs.setdefault(str(row["issue_id"]), []).append(dict(row))
            for table in ("pm_verification_records", "pm_manual_verification_records"):
                for row in conn.execute(
                    f"SELECT * FROM {table} WHERE project_id=? AND ticket_identifier IN ({marks})",
                    (change.project_id, *ticket_ids),
                ).fetchall():
                    receipts.setdefault((str(row["ticket_identifier"]), str(row["task_revision"])), []).append(dict(row))
            for row in conn.execute(
                f"SELECT * FROM pm_acceptances WHERE project_id=? AND ticket_identifier IN ({marks}) AND state='ACCEPTED'",
                (change.project_id, *ticket_ids),
            ).fetchall():
                acceptances[(str(row["ticket_identifier"]), str(row["task_revision"]))] = dict(row)
            for row in conn.execute(
                f"""SELECT m.ticket_identifier, i.required_procedures_json
                FROM pm_managed_tickets m
                JOIN pm_handoff_items i
                  ON i.handoff_id=m.handoff_id AND i.task_id=m.task_id
                 AND i.task_revision=m.task_revision
                WHERE m.project_id=? AND m.ticket_identifier IN ({marks})""",
                (change.project_id, *ticket_ids),
            ).fetchall():
                try:
                    value = json.loads(str(row["required_procedures_json"]))
                except json.JSONDecodeError:
                    value = {}
                required_by_ticket[str(row["ticket_identifier"])] = (
                    value if isinstance(value, dict) else {}
                )
        waiver_rows = self.registry.list_pm_coverage_waivers(change.project_id, change.change_id)
        waivers: dict[str, list[dict[str, Any]]] = {}
        for row in waiver_rows:
            waivers.setdefault(str(row["semantic_id"]), []).append(row)
        linked_tasks: dict[str, list[WbsTask]] = {}
        task_links: dict[str, CoverageTaskLink] = {}
        for task in tasks:
            if not task.executable:
                continue
            for semantic_id in task.requirement_refs:
                linked_tasks.setdefault(semantic_id, []).append(task)
            mapping = mappings.get(task.task_id)
            ticket = mapping.ticket_identifier if mapping else None
            revision = mapping.task_revision if mapping else ""
            task_runs = runs.get(ticket or "", [])
            successful = next((
                row for row in task_runs
                if successful_run_provenance(
                    SimpleNamespace(**row), self.successful_terminal_states
                )
            ), None)
            task_receipts = receipts.get((ticket or "", revision), [])
            required = required_by_ticket.get(ticket or "", {})
            by_commit: dict[str, list[dict[str, Any]]] = {}
            for receipt in task_receipts:
                by_commit.setdefault(str(receipt["tested_commit"]), []).append(receipt)
            complete: dict[str, list[dict[str, Any]]] = {}
            for commit, commit_receipts in by_commit.items():
                passing: dict[str, dict[str, Any]] = {}
                for receipt in commit_receipts:
                    procedure_id = str(receipt["procedure_id"])
                    if _receipt_qualifies(
                        receipt,
                        _procedure_policy(required.get(procedure_id), procedure_id),
                    ):
                        passing[procedure_id] = receipt
                if required and set(passing) == set(required):
                    complete[commit] = [passing[item] for item in sorted(passing)]
            accepted = acceptances.get((ticket or "", revision))
            accepted_commit = (
                str(accepted["tested_commit"])
                if accepted is not None and _acceptance_matches(
                    accepted, required,
                    by_commit.get(str(accepted["tested_commit"]), []),
                )
                else None
            )
            verified_commit = accepted_commit or next((
                commit for commit, _rows in sorted(
                    complete.items(),
                    key=lambda item: max(str(row["completed_at"]) for row in item[1]),
                    reverse=True,
                )
            ), None)
            qualified_receipts = complete.get(verified_commit or "", [])
            kinds = {
                "manual_reported" if row.get("procedure_kind") == "manual"
                else "host_executed"
                for row in qualified_receipts
            }
            provenance: Literal["host_executed", "manual_reported", "mixed"] | None
            if len(kinds) > 1:
                provenance = "mixed"
            elif "manual_reported" in kinds:
                provenance = "manual_reported"
            elif "host_executed" in kinds:
                provenance = "host_executed"
            else:
                provenance = None
            implementation_commit = (
                verified_commit or (str(successful["commit_sha"]) if successful else None)
            )
            task_links[task.task_id] = CoverageTaskLink(
                task.task_id, revision, ticket, implementation_commit is not None,
                implementation_commit, verified_commit is not None, provenance,
                tuple(str(row["receipt_id"]) for row in qualified_receipts),
                accepted_commit is not None,
                str(accepted["acceptance_id"]) if accepted_commit and accepted else None,
            )
        entities: list[CoverageEntity] = []
        for semantic in change.semantic_index:
            if semantic.kind not in {"requirement", "scenario"}:
                continue
            linked = linked_tasks.get(semantic.semantic_id, [])
            links = [task_links[task.task_id] for task in linked]
            total = len(links)
            def aggregate(name: str, predicate: Any) -> CoverageState:
                qualifying = [link for link in links if predicate(link)]
                evidence: list[str] = []
                for link in qualifying:
                    if name == "implemented" and link.implementation_commit:
                        evidence.append(f"commit:{link.implementation_commit}")
                    elif name == "tested":
                        evidence.extend(f"receipt:{item}" for item in link.receipt_ids)
                    elif name == "accepted" and link.acceptance_id:
                        evidence.append(f"acceptance:{link.acceptance_id}")
                return CoverageState(total > 0 and len(qualifying) == total, len(qualifying), total, tuple(evidence))
            active_waivers = [row for row in waivers.get(semantic.semantic_id, []) if row["state"] == "ACTIVE" and row["revision"] == change.revision and row["scope_hash"] == change.scope_hash]
            states = {
                "specified": CoverageState(True, 1, 1, (semantic.semantic_id,)),
                "linked": CoverageState(total > 0, total, total, tuple(task.task_id for task in linked)),
                "implemented": aggregate("implemented", lambda link: link.implemented),
                "tested": aggregate("tested", lambda link: link.tested),
                "accepted": aggregate("accepted", lambda link: link.accepted),
                "waived": CoverageState(bool(active_waivers), len(active_waivers), 1, tuple(str(row["waiver_id"]) for row in active_waivers)),
            }
            entities.append(CoverageEntity(
                semantic.semantic_id, semantic.kind, semantic.title, semantic.document_id,
                tuple(task.task_id for task in linked), states, tuple(links),
                tuple(waivers.get(semantic.semantic_id, [])),
            ))
        return CoverageReport(change.project_id, change.change_id, change.revision, change.scope_hash, tuple(entities))

    def waiver_preview(self, change: Any, semantic_id: str, reason: str, reviewer: str) -> dict[str, Any]:
        if semantic_id not in {item.semantic_id for item in change.semantic_index if item.kind in {"requirement", "scenario"}}:
            raise PmError("semantic_not_found", "coverage entity is unknown", status=404)
        if not reason.strip() or not reviewer.strip():
            raise PmError("invalid_request", "waiver reason and reviewer are required")
        normalized = {"project_id": change.project_id, "change_id": change.change_id, "semantic_id": semantic_id, "reason": reason.strip(), "reviewer": reviewer.strip(), "revision": change.revision, "scope_hash": change.scope_hash}
        token = "sha256:" + hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"ready": True, "confirmation_token": token, "findings": [], "normalized": normalized}

    def create_waiver(self, preview: dict[str, Any], token: str, operation_id: str) -> tuple[dict[str, Any], bool]:
        if token != preview["confirmation_token"]:
            raise PmError("confirmation_mismatch", "waiver preview is stale", status=409)
        record = {**preview["normalized"], "waiver_id": uuid.uuid5(uuid.NAMESPACE_URL, f"symphony-waiver:{operation_id}").hex, "operation_id": operation_id, "created_at": datetime.now(timezone.utc).isoformat()}
        try:
            return self.registry.create_pm_coverage_waiver(record)
        except ValueError as exc:
            raise PmError("operation_conflict", str(exc), status=409) from exc


def _acceptance_matches(
    acceptance: dict[str, Any], required: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> bool:
    try:
        receipt_ids = json.loads(str(acceptance["receipt_ids_json"]))
    except (KeyError, json.JSONDecodeError):
        return False
    if not isinstance(receipt_ids, list):
        return False
    by_id = {str(row["receipt_id"]): row for row in receipts}
    selected = [by_id.get(str(receipt_id)) for receipt_id in receipt_ids]
    if any(row is None for row in selected):
        return False
    observed: set[str] = set()
    for row in selected:
        assert row is not None
        procedure_id = str(row["procedure_id"])
        if not _receipt_qualifies(
            row, _procedure_policy(required.get(procedure_id), procedure_id)
        ):
            return False
        observed.add(procedure_id)
    return (
        observed == set(required)
        and canonical_receipt_set_hash(tuple(str(item) for item in receipt_ids))
        == str(acceptance.get("receipt_set_hash"))
    )
