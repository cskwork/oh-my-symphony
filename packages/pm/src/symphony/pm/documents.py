"""Project-bound planning documents, revisions, CAS, and workspace claims."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, cast
from contextlib import contextmanager, nullcontext

import yaml

from symphony.orchestrator.run_registry import RunRegistry
from .models import (
    ChangeView,
    DocumentView,
    PmError,
    PmFinding,
    PmRequestContext,
    PmResult,
    SemanticKind,
    ValidationReport,
    WbsTask,
    WbsView,
    WorkspaceClaim,
)
from .semantic import (
    IdeaSemantics,
    SemanticIndex,
    append_semantic_block,
    build_semantic_index,
    planning_sections,
    set_storyboard_fields,
    storyboard_frames,
    mint_semantic_id,
    semantic_items,
    validate_idea,
    validate_task_references,
)
from .wbs import (
    FORMAT_MARKER,
    ParsedWbs,
    parse_wbs,
    patch_task_field,
    patch_task_fields,
    render_task_block,
    validate_wbs_limits,
)
from .yaml_safe import load_unique_yaml
from .locks import change_authority_lock

_LOCATOR = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CHANGE_ID = re.compile(r"^[0-9a-f]{32}$")
_FIXED_DOCUMENTS = {
    "proposal": "proposal.md",
    "ux": "ux.md",
    "design": "design.md",
    "tasks": "tasks.md",
}


class PmDocumentRepository:
    def __init__(
        self,
        *,
        project_id: str,
        project_root: Path,
        registry: RunRegistry,
        planning_workspace_root: Path,
        max_nodes: int = 5_000,
        max_edges: int = 20_000,
        max_text_chars: int = 16_384,
    ) -> None:
        self.project_id = project_id
        self.project_root = project_root.resolve()
        self.registry = registry
        self.planning_workspace_root = planning_workspace_root.resolve()
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self.max_text_chars = max_text_chars

    def claim_workspace(
        self, change_id: str, requested_path: Path, owner: str,
        *, replace_existing: bool = False,
    ) -> WorkspaceClaim:
        requested = requested_path.resolve()
        if requested == self.project_root or self.project_root in requested.parents:
            raise PmError(
                "protected_checkout",
                "planning workspace must be separate from the protected project checkout",
                status=409,
            )
        if requested != self.planning_workspace_root and self.planning_workspace_root not in requested.parents:
            raise PmError(
                "unsafe_workspace",
                "planning workspace is outside the configured PM workspace root",
                status=400,
            )
        if not (requested / ".git").exists():
            raise PmError(
                "workspace_unavailable",
                "planning workspace is not an attached Git worktree",
                status=409,
            )
        project_common = _git_path(self.project_root, "--git-common-dir")
        requested_common = _git_path(requested, "--git-common-dir")
        if project_common != requested_common:
            raise PmError(
                "unsafe_workspace",
                "planning workspace is not a worktree of the registered project",
                status=409,
            )
        worktrees = _worktree_paths(self.project_root)
        if requested not in worktrees:
            raise PmError(
                "unsafe_workspace",
                "planning workspace is not registered in git worktree list",
                status=409,
            )
        base = _git(requested, "rev-parse", "HEAD")
        try:
            record = self.registry.claim_pm_workspace(
                project_id=self.project_id,
                change_id=change_id,
                workspace_path=requested,
                base_git_sha=base,
                owner=owner,
                replace_existing=replace_existing,
            )
        except RuntimeError as exc:
            raise PmError("workspace_claimed", str(exc), status=409) from exc
        return WorkspaceClaim(
            record.project_id,
            record.change_id,
            str(record.workspace_path),
            record.base_git_sha,
            record.owner,
            record.fence,
            record.state,
            tuple(_dirty_paths(record.workspace_path)),
        )

    def create_change(
        self,
        *,
        change_id: str,
        locator: str,
        title: str,
        owner: str,
    ) -> ChangeView:
        if not _CHANGE_ID.match(change_id) or not _LOCATOR.match(locator):
            raise PmError("invalid_request", "invalid change identity or locator")
        claim = self._claim(change_id, owner=owner)
        changes_root = claim.workspace_path / "openspec" / "changes"
        _require_contained_directory(claim.workspace_path, changes_root, create=True)
        root = changes_root / locator
        if root.exists():
            raise PmError("change_exists", f"change {locator} already exists", status=409)
        _install_openspec_profile(claim.workspace_path)
        root.mkdir(parents=True)
        semantic_ids = {
            kind: mint_semantic_id(kind)
            for kind in ("requirement", "scenario", "storyboard", "contract", "decision")
        }
        identity = {
            "change_id": change_id,
            "planning_format_version": 1,
            "title": title.strip() or locator,
            "semantic_ids": {
                "active": list(semantic_ids.values()),
                "retired": [],
            },
            "work_item_ids": {"active": {}, "retired": {}},
        }
        _atomic_write(root / "symphony.yaml", yaml.safe_dump(identity, sort_keys=False))
        _atomic_write(
            root / ".openspec.yaml",
            yaml.safe_dump({"schema": "symphony-pm", "created": date.today().isoformat()}, sort_keys=False),
        )
        templates = _openspec_resource_root() / "templates"
        _atomic_write(root / "proposal.md", (templates / "proposal.md").read_text(encoding="utf-8").replace("DEC-XXXXXXXX", semantic_ids["decision"]))
        _atomic_write(root / "ux.md", (templates / "ux.md").read_text(encoding="utf-8").replace("SB-XXXXXXXX", semantic_ids["storyboard"]))
        _atomic_write(root / "design.md", (templates / "design.md").read_text(encoding="utf-8").replace("API-XXXXXXXX", semantic_ids["contract"]))
        _atomic_write(root / "tasks.md", (templates / "tasks.md").read_text(encoding="utf-8"))
        first_spec = root / "specs" / "requirements" / "spec.md"
        first_spec.parent.mkdir(parents=True)
        _atomic_write(
            first_spec,
            (templates / "spec.md").read_text(encoding="utf-8")
            .replace("REQ-XXXXXXXX", semantic_ids["requirement"])
            .replace("SC-XXXXXXXX", semantic_ids["scenario"]),
        )
        return self.get_change(
            PmRequestContext(self.project_id, change_id), owner=owner
        )

    def create_spec(
        self,
        ctx: PmRequestContext,
        capability: str,
        title: str,
        *,
        owner: str | None = None,
    ) -> PmResult:
        self._check_project(ctx)
        if not _LOCATOR.fullmatch(capability):
            raise PmError("invalid_request", "invalid capability identifier")
        if ctx.expected_revision is None:
            raise PmError("revision_conflict", "expected_revision is required", status=409)
        claim = self._claim(ctx.change_id, owner=owner)
        if claim.state != "ACTIVE":
            raise PmError("scope_locked", "planning workspace is not ACTIVE", status=409)
        with _locked_change(self.registry, self.project_id, ctx.change_id, claim.workspace_path) as (root, identity):
            current = self._claim(ctx.change_id, owner=owner)
            if current.fence != claim.fence or current.state != "ACTIVE":
                raise PmError("scope_locked", "workspace authority changed", status=409)
            previous = _revision_hash(root)
            if previous != ctx.expected_revision:
                raise PmError(
                    "revision_conflict", "planning revision changed", status=409,
                    details={"current_revision": previous},
                )
            target = _document_path(root, f"spec:{capability}")
            if target.exists():
                raise PmError("document_exists", f"capability {capability} already exists", status=409)
            target.parent.mkdir(parents=True)
            template = (_openspec_resource_root() / "templates" / "spec.md").read_text(encoding="utf-8")
            ledger = _semantic_ledger(identity, _semantic_documents(root))
            reserved = set(ledger["active"]) | set(ledger["retired"])
            requirement_id = mint_semantic_id("requirement", reserved)
            reserved.add(requirement_id)
            scenario_id = mint_semantic_id("scenario", reserved)
            source = (
                template.replace("# Capability specification", f"# {title.strip() or capability}", 1)
                .replace("REQ-XXXXXXXX", requirement_id)
                .replace("SC-XXXXXXXX", scenario_id)
            )
            ledger["active"].extend((requirement_id, scenario_id))
            _atomic_semantic_write(root, target, source, identity, ledger)
            revision = _revision_hash(root)
        return PmResult(
            self.project_id, ctx.change_id, previous, revision,
            changed=(str(target.relative_to(root)),),
            approval_invalidated=True, operation_id=ctx.client_operation_id,
        )

    def list_changes(self, *, owner: str | None = None) -> tuple[ChangeView, ...]:
        # The active workspace is per change, so list from every claim.
        conn = self.registry._connect()  # core-owned boundary; no adapter uses it
        rows = conn.execute(
            "SELECT change_id, owner FROM pm_workspaces WHERE project_id = ? AND state != 'RELEASED'",
            (self.project_id,),
        ).fetchall()
        views: list[ChangeView] = []
        for row in rows:
            if owner is not None and str(row["owner"]) != owner:
                continue
            try:
                views.append(
                    self.get_change(
                        PmRequestContext(self.project_id, str(row["change_id"])),
                        owner=str(row["owner"]), locked=True,
                    )
                )
            except PmError:
                continue
        return tuple(views)

    def get_change(
        self, ctx: PmRequestContext, *, owner: str | None = None,
        locked: bool = False,
    ) -> ChangeView:
        """Read the change; ``locked`` reads its files under a shared lock.

        Callers off the event-loop thread pass ``locked`` so a writer's
        multi-file commit cannot interleave and tear the snapshot.
        """
        self._check_project(ctx)
        with change_authority_lock(self.registry, ctx.project_id, ctx.change_id, shared=True) if locked else nullcontext():
            claim = self._read_claim(ctx.change_id, owner=owner)
            archived = claim.state == "RELEASED"
            root, identity = _resolve_change_root(
                claim.workspace_path, ctx.change_id, archived=archived
            )
            # Archived history is immutable; locking it would write a lock file.
            locked = locked and not archived
            with _change_lock(root, shared=True) if locked else nullcontext():
                if locked:
                    identity = _load_identity(root)
                docs = tuple(self._documents(root, editable=not archived))
                manifest = _managed_manifest(root)
                scope_hash = _scope_hash(
                    root, claim.workspace_path, managed_manifest=manifest
                )
            report, semantic, idea = self._validate_loaded(
                docs, identity, workspace=claim.workspace_path
            )
            revision = _revision_from_manifest(manifest)
            findings = list(report.findings)
            approval = None if archived else self.registry.get_active_pm_approval(
                ctx.project_id, ctx.change_id
            )
            if approval is not None and (
                str(approval["scope_hash"]), str(approval["revision_hash"])
            ) != (scope_hash, revision):
                approval = None
            state = "Archived" if archived else (
                "Validated" if not any(
                    item.severity == "blocking" for item in findings
                ) else "Draft"
            )
            if approval is not None:
                state = "Approved"
            return ChangeView(
                project_id=self.project_id,
                change_id=ctx.change_id,
                locator=root.name,
                revision=revision,
                scope_hash=scope_hash,
                state=state,
                base_git_sha=claim.base_git_sha,
                schema_hash=_hash_bytes(
                    (claim.workspace_path / "openspec" / "schemas" / "symphony-pm" / "schema.yaml").read_bytes()
                )
                if (claim.workspace_path / "openspec" / "schemas" / "symphony-pm" / "schema.yaml").exists()
                else "",
                documents=docs,
                findings=tuple(findings),
                approval_id=str(approval["approval_id"]) if approval is not None else None,
                next_action="history" if archived else (
                    "fix_gaps" if findings else "review"
                ),
                semantic_index=semantic.items,
                decisions=idea.decisions if idea is not None else (),
                affected_capabilities=idea.affected_capabilities if idea is not None else (),
                storyboard_frames=storyboard_frames(
                    next((document.source for document in docs if document.document_id == "ux"), "")
                ),
                planning_sections=planning_sections(docs),
            )

    def get_document(
        self, ctx: PmRequestContext, document_id: str, *, owner: str | None = None
    ) -> DocumentView:
        claim = self._read_claim(ctx.change_id, owner=owner)
        archived = claim.state == "RELEASED"
        root, identity = _resolve_change_root(
            claim.workspace_path, ctx.change_id, archived=archived
        )
        path = _document_path(root, document_id)
        try:
            source = _read_utf8(path)
        except FileNotFoundError as exc:
            raise PmError("document_not_found", f"unknown document {document_id}", status=404) from exc
        editable = not archived
        reason = "archived planning history" if archived else None
        if document_id == "tasks":
            parsed = parse_wbs(source)
            findings = (*parsed.findings, *self._wbs_limit_findings(parsed.tasks))
            if not archived and any(item.severity == "blocking" for item in findings):
                editable = False
                reason = "unsupported or invalid managed WBS"
        return DocumentView(
            document_id,
            str(path.relative_to(root)),
            source,
            _hash_text(source),
            "draft",
            editable,
            reason,
        )

    def patch_document(
        self,
        ctx: PmRequestContext,
        document_id: str,
        operations: Iterable[dict[str, Any]],
        *,
        owner: str | None = None,
    ) -> PmResult:
        self._check_project(ctx)
        claim = self._claim(ctx.change_id, owner=owner)
        if claim.state == "EXTERNAL_EDIT":
            raise PmError("scope_locked", "external edit mode is active", status=409)
        if ctx.expected_revision is None:
            raise PmError(
                "revision_conflict", "expected_revision is required", status=409
            )
        with _locked_change(self.registry, self.project_id, ctx.change_id, claim.workspace_path) as (root, identity):
            current_claim = self._claim(ctx.change_id, owner=owner)
            if current_claim.fence != claim.fence or current_claim.state != "ACTIVE":
                raise PmError("scope_locked", "workspace authority changed", status=409)
            previous = _revision_hash(root)
            if ctx.expected_revision != previous:
                raise PmError(
                    "revision_conflict",
                    "planning revision changed",
                    status=409,
                    details={"current_revision": previous},
                )
            path = _document_path(root, document_id)
            source = _read_utf8(path) if path.exists() else ""
            updated = source
            docs_before = _semantic_documents(root)
            ledger = _semantic_ledger(identity, docs_before)
            work_ledger = None
            work_before = None
            if document_id == "tasks":
                work_before = parse_wbs(source)
                _require_valid_wbs(
                    work_before.findings,
                    self._wbs_limit_findings(work_before.tasks),
                )
            minted: list[str] = []
            for operation in operations:
                if operation.get("op") == "replace_source" and isinstance(
                    operation.get("source"), str
                ):
                    updated = str(operation["source"])
                elif (
                    operation.get("op") == "replace_text"
                    and isinstance(operation.get("start"), int)
                    and isinstance(operation.get("end"), int)
                    and isinstance(operation.get("text"), str)
                ):
                    start, end = int(operation["start"]), int(operation["end"])
                    if not 0 <= start <= end <= len(updated):
                        raise PmError("invalid_request", "text replacement span is invalid")
                    updated = updated[:start] + str(operation["text"]) + updated[end:]
                elif operation.get("op") == "create_semantic":
                    raw_kind = operation.get("kind")
                    title = operation.get("title")
                    if raw_kind not in {"requirement", "scenario", "storyboard", "contract", "decision"} or not isinstance(title, str):
                        raise PmError("invalid_request", "semantic kind and title are required")
                    kind = cast(SemanticKind, raw_kind)
                    _require_semantic_document_kind(document_id, kind)
                    reserved = set(ledger["active"]) | set(ledger["retired"]) | set(minted)
                    semantic_id = mint_semantic_id(kind, reserved)
                    updated = append_semantic_block(updated, kind, semantic_id, title)
                    minted.append(semantic_id)
                    if kind == "requirement":
                        reserved.add(semantic_id)
                        scenario_id = mint_semantic_id("scenario", reserved)
                        updated = append_semantic_block(
                            updated, "scenario", scenario_id, f"{title.strip() or 'New requirement'} succeeds"
                        )
                        minted.append(scenario_id)
                elif operation.get("op") == "set_storyboard_fields":
                    if document_id != "ux" or not isinstance(operation.get("storyboard_id"), str) or not isinstance(operation.get("fields"), dict):
                        raise PmError("invalid_request", "storyboard_id and fields are required for ux")
                    fields = dict(operation["fields"])
                    known = build_semantic_index(docs_before).by_id
                    for field, kinds in (("requirement_refs", {"requirement", "scenario"}), ("design_refs", {"contract"})):
                        refs = fields.get(field, [])
                        if refs and (
                            not isinstance(refs, list)
                            or any(ref not in known or known[ref].kind not in kinds for ref in refs)
                        ):
                            raise PmError("unresolved_reference", f"storyboard {field} contains unknown identities", status=409)
                    updated = set_storyboard_fields(
                        updated, str(operation["storyboard_id"]), fields
                    )
                else:
                    raise PmError("invalid_request", "unknown document operation")
            if document_id == "tasks":
                parsed = parse_wbs(updated)
                _require_valid_wbs(
                    parsed.findings, self._wbs_limit_findings(parsed.tasks)
                )
                assert work_before is not None
                current_work_ledger = _work_item_ledger(identity, work_before.tasks)
                approved_ids = _approved_task_ids(
                    self.registry.get_active_pm_approval(
                        self.project_id, ctx.change_id
                    ),
                    claim.workspace_path, root,
                )
                before_tasks_by_id = {
                    task.task_id: task for task in work_before.tasks
                }
                after_tasks_by_id = {task.task_id: task for task in parsed.tasks}
                bypassed_cancellation = sorted(
                    item_id for item_id in approved_ids
                    if item_id in before_tasks_by_id
                    and (
                        item_id not in after_tasks_by_id
                        or (
                            not before_tasks_by_id[item_id].cancelled
                            and after_tasks_by_id[item_id].cancelled
                        )
                    )
                )
                if bypassed_cancellation:
                    raise PmError(
                        "approved_task_requires_cancellation",
                        "approved work must use the reviewed cancel_task operation",
                        status=409,
                        details={"task_ids": bypassed_cancellation},
                    )
                work_ledger = _reconcile_work_items(
                    current_work_ledger, work_before.tasks, parsed.tasks
                )
            docs_after = _semantic_documents(root, override=(document_id, updated))
            before_index = build_semantic_index(docs_before, retired_ids=ledger["retired"])
            after_index = build_semantic_index(docs_after, retired_ids=ledger["retired"])
            before_ids = {item.semantic_id for item in before_index.items}
            after_ids = {item.semantic_id for item in after_index.items}
            before_kinds = {item.semantic_id: item.kind for item in before_index.items}
            after_kinds = {item.semantic_id: item.kind for item in after_index.items}
            retyped = sorted(
                semantic_id for semantic_id in before_ids & after_ids
                if before_kinds[semantic_id] != after_kinds[semantic_id]
            )
            if retyped:
                raise PmError(
                    "semantic_id_retyped",
                    "semantic identity type cannot change",
                    status=409,
                    details={"identities": retyped},
                )
            identity_findings = [
                item for item in after_index.findings
                if item.code in {
                    "duplicate_semantic_id",
                    "retired_semantic_id_reused",
                    "semantic_id_kind_mismatch",
                    "semantic_kind_wrong_document",
                }
            ]
            if identity_findings:
                raise PmError(
                    "duplicate_id",
                    "semantic identities are duplicated, retyped, or retired",
                    status=409,
                    details={"findings": [item.code for item in identity_findings]},
                )
            introduced = after_ids - before_ids
            inherited_candidates = _baseline_semantic_records(
                claim.workspace_path, document_id
            )
            adopted = {
                semantic_id: record
                for semantic_id in introduced - set(minted)
                if (record := inherited_candidates.get(semantic_id)) is not None
                and _inherited_use_supported(updated, semantic_id, record)
            }
            unauthorized = introduced - set(minted) - set(adopted)
            if unauthorized:
                raise PmError(
                    "semantic_id_replacement",
                    "semantic identities must be created by the server",
                    status=409,
                    details={"identities": sorted(unauthorized)},
                )
            removed = before_ids - after_ids
            active = [item for item in ledger["active"] if item not in removed]
            for item in minted:
                if item not in active:
                    active.append(item)
            retired = list(dict.fromkeys([
                *ledger["retired"],
                *sorted(removed & set(ledger["active"])),
            ]))
            inherited = {
                str(record.get("semantic_id")): dict(record)
                for record in ledger.get("inherited", [])
                if isinstance(record, dict)
                and str(record.get("semantic_id")) in after_ids
            }
            inherited.update(adopted)
            next_ledger = {
                "active": active,
                "retired": retired,
                "inherited": list(inherited.values()),
            }
            identity_for_write = (
                _identity_with_work_items(identity, work_ledger)
                if work_ledger is not None else identity
            )
            if next_ledger != ledger or work_ledger is not None:
                _atomic_semantic_write(
                    root, path, updated, identity_for_write, next_ledger
                )
            else:
                _atomic_write(path, updated)
            revision = _revision_hash(root)
        return PmResult(
            self.project_id,
            ctx.change_id,
            previous,
            revision,
            changed=(str(path.relative_to(root)),),
            impacted_ids=tuple(minted),
            approval_invalidated=revision != previous,
            operation_id=ctx.client_operation_id,
        )

    def patch_wbs(
        self,
        ctx: PmRequestContext,
        operations: Iterable[dict[str, Any]],
        *,
        owner: str | None = None,
    ) -> PmResult:
        operation_list = list(operations)
        self._check_project(ctx)
        if ctx.expected_revision is None:
            raise PmError("revision_conflict", "expected_revision is required", status=409)
        claim = self._claim(ctx.change_id, owner=owner)
        if claim.state != "ACTIVE":
            raise PmError("scope_locked", "planning workspace is not ACTIVE", status=409)
        with _locked_change(self.registry, self.project_id, ctx.change_id, claim.workspace_path) as (root, identity):
            path = root / "tasks.md"
            current = self._claim(ctx.change_id, owner=owner)
            if current.fence != claim.fence or current.state != "ACTIVE":
                raise PmError("scope_locked", "workspace authority changed", status=409)
            previous = _revision_hash(root)
            if previous != ctx.expected_revision:
                raise PmError(
                    "revision_conflict", "planning revision changed", status=409,
                    details={"current_revision": previous},
                )
            source = _read_utf8(path)
            if not operation_list:
                return PmResult(
                    self.project_id, ctx.change_id, previous, previous,
                    changed=(), approval_invalidated=False,
                    operation_id=ctx.client_operation_id,
                )
            before = parse_wbs(source)
            _require_valid_wbs(before.findings, self._wbs_limit_findings(before.tasks))
            ledger = _work_item_ledger(identity, before.tasks)
            approval = self.registry.get_active_pm_approval(
                self.project_id, ctx.change_id
            )
            approved_ids = _approved_task_ids(
                approval, claim.workspace_path, root
            )
            deleting_approved = sorted(
                str(item.get("task_id")) for item in operation_list
                if item.get("op") == "delete_task"
                and str(item.get("task_id")) in approved_ids
            )
            if deleting_approved:
                raise PmError(
                    "approved_task_requires_cancellation",
                    "approved work must be retained with an explicit cancellation record",
                    status=409,
                    details={"task_ids": deleting_approved},
                )
            updated, minted, restored = self._apply_wbs_operations(
                source, operation_list, ledger, parsed=before
            )
            after = parse_wbs(updated)
            _require_valid_wbs(after.findings, self._wbs_limit_findings(after.tasks))
            next_ledger = _reconcile_work_items(
                ledger, before.tasks, after.tasks,
                minted_ids=frozenset(minted), restored_ids=frozenset(restored),
            )
            next_identity = _identity_with_work_items(identity, next_ledger)
            semantic_ledger = _semantic_ledger(
                identity, _semantic_documents(root)
            )
            _atomic_semantic_write(
                root, path, updated, next_identity, semantic_ledger
            )
            revision = _revision_hash(root)
        return PmResult(
            self.project_id, ctx.change_id, previous, revision,
            changed=("tasks.md",), impacted_ids=tuple(minted),
            approval_invalidated=revision != previous,
            operation_id=ctx.client_operation_id,
        )

    def patch_documents(
        self, ctx: PmRequestContext, sources: dict[str, str], *,
        authorized_ids: tuple[str, ...] = (), owner: str | None = None,
    ) -> PmResult:
        """Atomically apply a bounded, already-reviewed multi-document proposal."""
        self._check_project(ctx)
        if ctx.expected_revision is None or not sources or len(sources) > 8:
            raise PmError("invalid_request", "multi-document apply requires revision and bounded sources")
        claim = self._claim(ctx.change_id, owner=owner)
        with _locked_change(self.registry, self.project_id, ctx.change_id, claim.workspace_path) as (root, identity):
            previous = _revision_hash(root)
            if previous != ctx.expected_revision:
                raise PmError("revision_conflict", "planning revision changed", status=409, details={"current_revision": previous})
            current_docs = {document.document_id: document for document in self._documents(root)}
            if any(document_id not in current_docs or not current_docs[document_id].editable for document_id in sources):
                raise PmError("invalid_planning_scope", "proposal target is unavailable or read-only", status=409)
            docs_after = tuple(
                DocumentView(
                    document.document_id, document.path,
                    sources.get(document.document_id, document.source),
                    _hash_text(sources.get(document.document_id, document.source)),
                    document.state, document.editable, document.unsupported_reason,
                )
                for document in current_docs.values()
            )
            semantic_ledger = _semantic_ledger(identity, current_docs.values())
            before_index = build_semantic_index(tuple(current_docs.values()), retired_ids=semantic_ledger["retired"])
            after_index = build_semantic_index(docs_after, retired_ids=semantic_ledger["retired"])
            before_ids = {item.semantic_id for item in before_index.items}
            after_ids = {item.semantic_id for item in after_index.items}
            authorized = set(authorized_ids)
            introduced = after_ids - before_ids
            if introduced - authorized:
                raise PmError("semantic_id_replacement", "proposal contains identities not reserved by the host", status=409, details={"identities": sorted(introduced - authorized)})
            identity_findings = [item for item in after_index.findings if item.code in {"duplicate_semantic_id", "retired_semantic_id_reused", "semantic_id_kind_mismatch", "semantic_kind_wrong_document"}]
            if identity_findings:
                raise PmError("duplicate_id", "proposal semantic identities are invalid", status=409)
            unused_semantic_ids = {
                item for item in authorized
                if item.startswith(("REQ-", "SC-", "SB-", "API-", "DEC-"))
                and item not in introduced
            }
            if unused_semantic_ids:
                raise PmError(
                    "invalid_proposal",
                    "reserved semantic identities must identify new managed blocks",
                    status=409,
                    details={"identities": sorted(unused_semantic_ids)},
                )
            for item in after_index.items:
                if item.semantic_id not in introduced or item.kind != "scenario":
                    continue
                if not any(
                    candidate.kind == "requirement"
                    and candidate.document_id == item.document_id
                    and candidate.line < item.line
                    for candidate in after_index.items
                ):
                    raise PmError(
                        "invalid_proposal",
                        "new scenarios require a preceding requirement parent",
                        status=409,
                        details={"identities": [item.semantic_id]},
                    )
            next_semantic = {
                "active": [item for item in semantic_ledger["active"] if item in after_ids] + sorted(introduced),
                "retired": list(dict.fromkeys([*semantic_ledger["retired"], *sorted((before_ids - after_ids) & set(semantic_ledger["active"]))])),
                "inherited": [item for item in semantic_ledger.get("inherited", []) if item.get("semantic_id") in after_ids],
            }
            next_identity = identity
            after_wbs = None
            if "tasks" in sources:
                before_wbs = parse_wbs(current_docs["tasks"].source)
                after_wbs = parse_wbs(sources["tasks"])
                _require_valid_wbs(after_wbs.findings, self._wbs_limit_findings(after_wbs.tasks))
                approved_ids = _approved_task_ids(
                    self.registry.get_active_pm_approval(
                        self.project_id, ctx.change_id
                    ),
                    claim.workspace_path, root,
                )
                before_tasks = {item.task_id: item for item in before_wbs.tasks}
                after_tasks = {item.task_id: item for item in after_wbs.tasks}
                bypassed_cancellation = sorted(
                    item_id for item_id in approved_ids
                    if item_id in before_tasks
                    and (
                        item_id not in after_tasks
                        or (
                            not before_tasks[item_id].cancelled
                            and after_tasks[item_id].cancelled
                        )
                    )
                )
                if bypassed_cancellation:
                    raise PmError(
                        "approved_task_requires_cancellation",
                        "approved work must use the reviewed cancel_task operation",
                        status=409,
                        details={"task_ids": bypassed_cancellation},
                    )
                work = _reconcile_work_items(
                    _work_item_ledger(identity, before_wbs.tasks), before_wbs.tasks, after_wbs.tasks,
                    minted_ids=frozenset(item for item in authorized if item.startswith(("T-", "G-"))),
                )
                next_identity = _identity_with_work_items(identity, work)
            next_identity_source = _identity_source(next_identity, next_semantic)
            paths = {document_id: _document_path(root, document_id) for document_id in sources}
            _atomic_multi_document_write(root, paths, sources, next_identity_source)
            revision = _revision_hash(root)
            validation, _, _ = self._validate_loaded(
                tuple(self._documents(root)), next_identity,
                workspace=claim.workspace_path,
            )
        return PmResult(
            self.project_id, ctx.change_id, previous, revision,
            changed=tuple(sorted(str(path.relative_to(root)) for path in paths.values())),
            findings=validation.findings,
            impacted_ids=tuple(sorted(authorized)), approval_invalidated=revision != previous,
            operation_id=ctx.client_operation_id,
        )

    def preview_wbs_patch(
        self,
        ctx: PmRequestContext,
        operations: Iterable[dict[str, Any]],
        *,
        owner: str | None = None,
    ) -> dict[str, Any]:
        if ctx.expected_revision is None:
            raise PmError("revision_conflict", "expected_revision is required", status=409)
        claim = self._claim(ctx.change_id, owner=owner)
        root, identity = _resolve_change_root(claim.workspace_path, ctx.change_id)
        current = _revision_hash(root)
        if current != ctx.expected_revision:
            raise PmError(
                "revision_conflict", "planning revision changed", status=409,
                details={"current_revision": current},
            )
        source = _read_utf8(root / "tasks.md")
        operation_list = list(operations)
        if any(item.get("op") != "set_field" for item in operation_list):
            raise PmError("invalid_request", "preview supports set_field operations")
        changes: list[tuple[str, str, Any]] = []
        for operation in operation_list:
            task_id = operation.get("task_id")
            field = operation.get("field")
            if not isinstance(task_id, str) or not isinstance(field, str):
                raise PmError("invalid_request", "preview operation requires task_id and field")
            changes.append((task_id, field, operation.get("value")))
        updated = patch_task_fields(source, changes)
        parsed = parse_wbs(updated)
        findings = (*parsed.findings, *self._wbs_limit_findings(parsed.tasks))
        if any(item.severity == "blocking" for item in findings):
            raise PmError(
                "unsupported_format", "tasks.md edit is invalid", status=409,
                details={"findings": [item.code for item in findings]},
            )
        before = parse_wbs(source)
        next_work_ledger = _reconcile_work_items(
            _work_item_ledger(identity, before.tasks), before.tasks, parsed.tasks
        )
        next_identity = _identity_with_work_items(identity, next_work_ledger)
        identity_source = _identity_source(
            next_identity, _semantic_ledger(identity, _semantic_documents(root))
        )
        manifest = _managed_manifest(root, {
            "tasks.md": updated.encode("utf-8"),
            "symphony.yaml": identity_source.encode("utf-8"),
        })
        return {
            "expected_post_revision": _revision_from_manifest(manifest),
            "expected_manifest": {
                path: _hash_bytes(content) for path, content in manifest.items()
            },
        }

    def managed_manifest(
        self, ctx: PmRequestContext, *, owner: str | None = None
    ) -> dict[str, Any]:
        claim = self._claim(ctx.change_id, owner=owner)
        root, _ = _resolve_change_root(claim.workspace_path, ctx.change_id)
        manifest = _managed_manifest(root)
        return {
            "revision": _revision_from_manifest(manifest),
            "files": {path: _hash_bytes(content) for path, content in manifest.items()},
        }

    def _apply_wbs_operations(
        self, source: str, operation_list: list[dict[str, Any]],
        ledger: dict[str, dict[str, dict[str, str]]],
        *, parsed: ParsedWbs | None = None,
    ) -> tuple[str, list[str], list[str]]:
        minted: list[str] = []
        restored: list[str] = []
        if operation_list and all(operation.get("op") == "set_field" for operation in operation_list):
            changes: list[tuple[str, str, Any]] = []
            for operation in operation_list:
                task_id = operation.get("task_id")
                field = operation.get("field")
                if not isinstance(task_id, str):
                    raise PmError("invalid_request", "task_id is required")
                if not isinstance(field, str):
                    raise PmError("invalid_request", "field is required")
                changes.append((task_id, field, operation.get("value")))
            return patch_task_fields(source, changes, parsed=parsed), minted, restored
        for operation in operation_list:
            op = operation.get("op")
            task_id = operation.get("task_id")
            if op == "create_task" and isinstance(operation.get("task"), dict):
                if task_id is not None or operation["task"].get("task_id"):
                    raise PmError(
                        "server_minted_id_required",
                        "new work item identities are assigned by the server",
                        status=409,
                    )
                task = dict(operation["task"])
                kind = "group" if str(task.get("kind") or "").casefold() == "group" else "task"
                task_id = _mint_work_item_id(kind, ledger, frozenset(minted))
                task["task_id"] = task_id
                source = source.rstrip() + "\n\n" + render_task_block(task) + "\n"
                minted.append(task_id)
                continue
            if not isinstance(task_id, str):
                raise PmError("invalid_request", "task_id is required")
            if op == "set_field":
                field = operation.get("field")
                if not isinstance(field, str):
                    raise PmError("invalid_request", "field is required")
                source = patch_task_field(source, task_id, field, operation.get("value"))
            elif op == "cancel_task":
                source = _cancel_task_source(
                    source, task_id, operation.get("reason"),
                    operation.get("successor_refs", []),
                )
            elif op == "add_task":
                raise PmError(
                    "server_minted_id_required",
                    "add_task with a caller-provided identity is unsupported",
                    status=409,
                )
            elif op == "restore_task" and isinstance(operation.get("task"), dict):
                if any(item.task_id == task_id for item in parse_wbs(source).tasks):
                    raise PmError("duplicate_id", f"task {task_id} already exists", status=409)
                retired = ledger["retired"].get(task_id)
                candidate = dict(operation["task"])
                candidate["task_id"] = task_id
                rendered = render_task_block(candidate)
                parsed_candidate = parse_wbs(f"{FORMAT_MARKER}\n{rendered}\n")
                if len(parsed_candidate.tasks) != 1:
                    raise PmError("invalid_request", "restored work item is invalid")
                fingerprint = _work_item_fingerprint(parsed_candidate.tasks[0])
                kind = _work_item_kind(parsed_candidate.tasks[0])
                if retired != {"kind": kind, "fingerprint": fingerprint}:
                    raise PmError(
                        "retired_id_reused",
                        "retired work item identity can restore only the same entity",
                        status=409,
                    )
                source = source.rstrip() + "\n\n" + rendered + "\n"
                restored.append(task_id)
            elif op == "delete_task":
                parsed = parse_wbs(source)
                block = next((item for item in parsed.blocks if item.item_id == task_id), None)
                if block is None:
                    raise PmError("unknown_task", f"unknown task {task_id}", status=404)
                if any(item.parent_id == task_id or any(dep.task_id == task_id for dep in item.dependencies) for item in parsed.tasks):
                    raise PmError("task_in_use", f"task {task_id} has children or dependents", status=409)
                source = (source[:block.start] + source[block.end:]).replace("\n\n\n", "\n\n")
            else:
                raise PmError("invalid_request", "unsupported WBS operation")
        return source, minted, restored

    def get_wbs(
        self, ctx: PmRequestContext, *, owner: str | None = None
    ) -> WbsView:
        doc = self.get_document(ctx, "tasks", owner=owner)
        parsed = parse_wbs(doc.source)
        return WbsView(
            self.project_id,
            ctx.change_id,
            self.get_change(ctx, owner=owner).revision,
            parsed.tasks,
            findings=(*parsed.findings, *self._wbs_limit_findings(parsed.tasks)),
        )

    def _wbs_limit_findings(self, tasks: Iterable[WbsTask]) -> tuple[PmFinding, ...]:
        return validate_wbs_limits(
            tuple(tasks), max_nodes=self.max_nodes, max_edges=self.max_edges,
            max_text_chars=self.max_text_chars,
        )

    def validate(
        self, ctx: PmRequestContext, *, owner: str | None = None
    ) -> ValidationReport:
        claim = self._claim(ctx.change_id, owner=owner)
        try:
            root, identity = _resolve_change_root(claim.workspace_path, ctx.change_id)
        except PmError as exc:
            return ValidationReport(
                False,
                (PmFinding(exc.code, exc.message, severity="blocking"),),
            )
        docs = tuple(self._documents(root))
        report, _, _ = self._validate_loaded(
            docs, identity, workspace=claim.workspace_path
        )
        return report

    def _validate_loaded(
        self, docs: tuple[DocumentView, ...], identity: dict[str, Any],
        *, workspace: Path | None = None,
    ) -> tuple[ValidationReport, SemanticIndex, IdeaSemantics | None]:
        findings: list[PmFinding] = []
        by_id = {document.document_id: document for document in docs}
        for document_id, relative in _FIXED_DOCUMENTS.items():
            document = by_id.get(document_id)
            if document is None:
                findings.append(
                    PmFinding(
                        "missing_document",
                        f"{relative} is missing",
                        severity="blocking",
                        document_id=document_id,
                    )
                )
                continue
            meaningful = [
                line.strip()
                for line in document.source.splitlines()
                if line.strip()
                and not line.lstrip().startswith("#")
                and "symphony-wbs-format" not in line
            ]
            if not meaningful:
                findings.append(
                    PmFinding(
                        "empty_document",
                        f"{relative} has no reviewable content",
                        severity="blocking",
                        document_id=document_id,
                    )
                )
        ledger = _semantic_ledger(identity, docs)
        semantic = build_semantic_index(docs, retired_ids=ledger["retired"])
        findings.extend(semantic.findings)
        indexed_ids = {item.semantic_id for item in semantic.items}
        inherited_records = {
            str(record.get("semantic_id")): record
            for record in ledger.get("inherited", [])
            if isinstance(record, dict) and isinstance(record.get("semantic_id"), str)
        }
        unknown = indexed_ids - set(ledger["active"]) - set(inherited_records)
        for semantic_id in sorted(unknown):
            findings.append(PmFinding(
                "semantic_id_unregistered",
                f"identity {semantic_id} was not minted for this change",
                severity="blocking", item_id=semantic_id, field="semantic_id",
            ))
        missing = set(ledger["active"]) - indexed_ids
        for semantic_id in sorted(missing):
            findings.append(PmFinding(
                "semantic_identity_missing",
                f"active identity {semantic_id} is missing; restore it or retire it through a managed edit",
                severity="blocking", item_id=semantic_id, field="semantic_id",
            ))
        if workspace is not None:
            for semantic_id, record in inherited_records.items():
                document_id = str(record.get("document_id") or "")
                current = _baseline_semantic_records(workspace, document_id).get(semantic_id)
                if current != record:
                    findings.append(PmFinding(
                        "inherited_semantic_drift",
                        f"accepted baseline identity {semantic_id} no longer matches its provenance",
                        severity="blocking", item_id=semantic_id, field="semantic_id",
                    ))
        proposal = next((document for document in docs if document.document_id == "proposal"), None)
        idea = None
        if proposal is not None:
            idea = validate_idea(proposal.source, semantic)
            findings.extend(idea.findings)
        tasks_document = by_id.get("tasks")
        if tasks_document is not None:
            parsed = parse_wbs(tasks_document.source)
            findings.extend(parsed.findings)
            findings.extend(self._wbs_limit_findings(parsed.tasks))
            if isinstance(identity.get("work_item_ids"), dict):
                work_ledger = _work_item_ledger(identity, ())
                for task in parsed.tasks:
                    record = work_ledger["active"].get(task.task_id)
                    if task.task_id in work_ledger["retired"]:
                        findings.append(PmFinding(
                            "retired_work_item_id_reused",
                            f"retired work item identity {task.task_id} cannot be reused",
                            severity="blocking", document_id="tasks",
                            item_id=task.task_id,
                        ))
                    elif record is None:
                        findings.append(PmFinding(
                            "work_item_id_unregistered",
                            f"work item identity {task.task_id} was not minted for this change",
                            severity="blocking", document_id="tasks",
                            item_id=task.task_id,
                        ))
                    elif record["kind"] != _work_item_kind(task):
                        findings.append(PmFinding(
                            "work_item_id_retyped",
                            f"work item identity {task.task_id} changed type",
                            severity="blocking", document_id="tasks",
                            item_id=task.task_id,
                        ))
                for item_id in sorted(set(work_ledger["active"]) - {task.task_id for task in parsed.tasks}):
                    findings.append(PmFinding(
                        "work_item_identity_missing",
                        f"active work item identity {item_id} is missing",
                        severity="blocking", document_id="tasks", item_id=item_id,
                    ))
            findings.extend(validate_task_references(parsed.tasks, semantic))
            if not any(task.executable for task in parsed.tasks):
                findings.append(
                    PmFinding(
                        "empty_plan",
                        "tasks.md has no executable leaf task",
                        severity="blocking",
                        document_id="tasks",
                    )
                )
        report = ValidationReport(
            not any(item.severity == "blocking" for item in findings),
            tuple(findings),
        )
        return report, semantic, idea

    def begin_external_edit(
        self, change_id: str, *, fence: int, owner: str
    ) -> WorkspaceClaim:
        claim = self._claim(change_id, owner=owner)
        with _locked_change(self.registry, self.project_id, change_id, claim.workspace_path) as (root, identity):
            current = self._claim(change_id, owner=owner)
            parsed = parse_wbs(_read_utf8(root / "tasks.md"))
            _require_valid_wbs(
                parsed.findings, self._wbs_limit_findings(parsed.tasks)
            )
            ledger = _work_item_ledger(identity, parsed.tasks)
            next_identity = _identity_with_work_items(identity, ledger)
            if next_identity != identity:
                _atomic_write(
                    root / "symphony.yaml",
                    yaml.safe_dump(next_identity, sort_keys=False),
                )
            if current.fence != fence or not self.registry.set_pm_workspace_state(
                self.project_id,
                change_id,
                fence=fence,
                state="EXTERNAL_EDIT",
                observed_hashes=_workspace_hashes(claim.workspace_path),
            ):
                raise PmError("revision_conflict", "workspace claim changed", status=409)
        return self._claim(change_id, owner=owner)

    def end_external_edit(
        self, change_id: str, *, fence: int, owner: str
    ) -> ValidationReport:
        claim = self._claim(change_id, owner=owner)
        with _locked_change(self.registry, self.project_id, change_id, claim.workspace_path) as (root, identity):
            current = self.registry.get_pm_workspace(self.project_id, change_id)
            if (
                current is None
                or current.fence != fence
                or current.owner != owner
                or current.state != "EXTERNAL_EDIT"
            ):
                raise PmError("revision_conflict", "workspace claim changed", status=409)
            parsed = parse_wbs(_read_utf8(root / "tasks.md"))
            try:
                next_ledger = _reconcile_external_work_items(
                    _work_item_ledger(identity, ()), parsed.tasks,
                    approved_ids=_approved_task_ids(
                        self.registry.get_active_pm_approval(
                            self.project_id, change_id
                        ),
                        claim.workspace_path, root,
                    ),
                )
            except PmError as exc:
                report = ValidationReport(
                    False,
                    (PmFinding(exc.code, exc.message, severity="blocking"),),
                )
            else:
                next_identity = _identity_with_work_items(identity, next_ledger)
                if next_identity != identity:
                    _atomic_write(
                        root / "symphony.yaml",
                        yaml.safe_dump(next_identity, sort_keys=False),
                    )
                report = self.validate(
                    PmRequestContext(self.project_id, change_id), owner=owner
                )
            target = "ACTIVE" if report.valid else "REPAIR_REQUIRED"
            self.registry.set_pm_workspace_state(
                self.project_id,
                change_id,
                fence=fence,
                state=target,
                observed_hashes=_workspace_hashes(claim.workspace_path),
            )
        return report

    def _claim(self, change_id: str, *, owner: str | None) -> WorkspaceClaim:
        record = self.registry.get_pm_workspace(self.project_id, change_id)
        if record is None:
            raise PmError("workspace_unavailable", "planning workspace is not claimed", status=409)
        if owner is not None and record.owner != owner:
            raise PmError("workspace_claimed", "planning workspace has another owner", status=409)
        if record.state in {"REPAIR_REQUIRED", "RELEASED"}:
            raise PmError("workspace_repair_required", "planning workspace needs repair", status=409)
        return WorkspaceClaim(
            record.project_id,
            record.change_id,
            str(record.workspace_path),
            record.base_git_sha,
            record.owner,
            record.fence,
            record.state,
            tuple(_dirty_paths(record.workspace_path)),
        )

    def _read_claim(self, change_id: str, *, owner: str | None) -> WorkspaceClaim:
        record = self.registry.get_pm_workspace(self.project_id, change_id)
        if record is None:
            raise PmError(
                "workspace_unavailable",
                "planning workspace is not claimed",
                status=409,
            )
        if owner is not None and record.owner != owner:
            raise PmError(
                "workspace_claimed",
                "planning workspace has another owner",
                status=409,
            )
        if record.state == "REPAIR_REQUIRED":
            raise PmError(
                "workspace_repair_required",
                "planning workspace needs repair",
                status=409,
            )
        return WorkspaceClaim(
            record.project_id,
            record.change_id,
            str(record.workspace_path),
            record.base_git_sha,
            record.owner,
            record.fence,
            record.state,
            () if record.state == "RELEASED" else tuple(
                _dirty_paths(record.workspace_path)
            ),
        )

    def _check_project(self, ctx: PmRequestContext) -> None:
        if ctx.project_id != self.project_id:
            raise PmError("wrong_project", "request project does not match", status=409)

    def _documents(
        self, root: Path, *, editable: bool = True
    ) -> Iterable[DocumentView]:
        reason = None if editable else "archived planning history"
        for document_id, relative in _FIXED_DOCUMENTS.items():
            path = _document_path(root, document_id)
            if path.exists():
                source = _read_utf8(path)
                yield DocumentView(
                    document_id, relative, source, _hash_text(source),
                    "draft", editable, reason,
                )
        specs = root / "specs"
        if specs.exists():
            for path in sorted(specs.glob("*/spec.md")):
                if not _LOCATOR.fullmatch(path.parent.name):
                    continue
                document_id = f"spec:{path.parent.name}"
                safe_path = _document_path(root, document_id)
                source = _read_utf8(safe_path)
                yield DocumentView(
                    document_id, str(safe_path.relative_to(root)), source,
                    _hash_text(source), "draft", editable, reason,
                )


@contextmanager
def _locked_change(registry: RunRegistry, project_id: str, change_id: str, workspace: Path):
    with change_authority_lock(registry, project_id, change_id):
        root, identity = _resolve_change_root(workspace, change_id)
        with _change_lock(root):
            yield root, identity


def _resolve_change_root(
    workspace: Path, change_id: str, *, archived: bool = False, recover: bool = True
) -> tuple[Path, dict[str, Any]]:
    changes = workspace / "openspec" / "changes"
    workspace = workspace.resolve()
    if (
        not changes.exists()
        or changes.is_symlink()
        or workspace not in changes.resolve().parents
    ):
        raise PmError("change_not_found", f"unknown change {change_id}", status=404)
    search_root = changes / "archive" if archived else changes
    if (
        not search_root.exists()
        or search_root.is_symlink()
        or (
            search_root.resolve() != changes.resolve()
            and changes.resolve() not in search_root.resolve().parents
        )
    ):
        raise PmError("change_not_found", f"unknown change {change_id}", status=404)
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in search_root.iterdir():
        if not archived and path.name == "archive":
            continue
        identity_path = path / "symphony.yaml"
        resolved = path.resolve()
        if (
            path.is_symlink()
            or not path.is_dir()
            or search_root.resolve() not in resolved.parents
            or identity_path.is_symlink()
            or not identity_path.exists()
        ):
            continue
        try:
            identity = load_unique_yaml(identity_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if isinstance(identity, dict) and identity.get("change_id") == change_id:
            matches.append((resolved, dict(identity)))
    if not matches:
        raise PmError("change_not_found", f"unknown change {change_id}", status=404)
    if len(matches) > 1:
        raise PmError(
            "ambiguous_change_identity",
            f"multiple planning directories claim change {change_id}",
            status=409,
        )
    resolved, identity = matches[0]
    # recover=False is a read-only lookup: no journal recovery or reload.
    if not archived and recover:
        _recover_change_transactions(resolved)
        identity = _load_identity(resolved)
    return resolved, dict(identity)


def _load_identity(root: Path) -> dict[str, Any]:
    try:
        identity = load_unique_yaml((root / "symphony.yaml").read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PmError(
            "unsupported_format", "change identity is malformed", status=409
        ) from exc
    return dict(identity)


def _document_path(root: Path, document_id: str) -> Path:
    if document_id in _FIXED_DOCUMENTS:
        candidate = root / _FIXED_DOCUMENTS[document_id]
    elif document_id.startswith("spec:") and _LOCATOR.match(document_id[5:]):
        candidate = root / "specs" / document_id[5:] / "spec.md"
    else:
        raise PmError("document_not_found", f"unknown document {document_id}", status=404)
    resolved = candidate.resolve()
    if root.resolve() not in resolved.parents:
        raise PmError("unsafe_path", "document path escapes change root")
    return resolved


def _semantic_documents(
    root: Path, override: tuple[str, str] | None = None
) -> tuple[DocumentView, ...]:
    documents: list[DocumentView] = []
    override_id, override_source = override or ("", "")
    candidates: list[tuple[str, Path]] = [
        (document_id, root / relative)
        for document_id, relative in _FIXED_DOCUMENTS.items()
        if document_id != "tasks"
    ]
    specs = root / "specs"
    if specs.exists():
        candidates.extend(
            (f"spec:{path.parent.name}", path)
            for path in sorted(specs.glob("*/spec.md"))
        )
    for document_id, path in candidates:
        if document_id == override_id:
            source = override_source
        elif path.exists():
            source = _read_utf8(path)
        else:
            continue
        documents.append(DocumentView(
            document_id, str(path.relative_to(root)), source,
            _hash_text(source), "draft", True,
        ))
    return tuple(documents)


def _semantic_ledger(
    identity: dict[str, Any], documents: Iterable[DocumentView]
) -> dict[str, Any]:
    raw = identity.get("semantic_ids")
    if not isinstance(raw, dict):
        # Legacy managed changes remain readable. Their existing markers become
        # the conservative in-memory allowlist until an explicit conversion.
        existing = build_semantic_index(tuple(documents)).items
        return {
            "active": list(dict.fromkeys(item.semantic_id for item in existing)),
            "retired": [],
            "inherited": [],
        }
    active = raw.get("active", [])
    retired = raw.get("retired", [])
    inherited = raw.get("inherited", [])
    if not isinstance(active, list) or not all(isinstance(item, str) for item in active):
        active = []
    if not isinstance(retired, list) or not all(isinstance(item, str) for item in retired):
        retired = []
    if not isinstance(inherited, list) or not all(isinstance(item, dict) for item in inherited):
        inherited = []
    return {
        "active": list(dict.fromkeys(active)),
        "retired": list(dict.fromkeys(retired)),
        "inherited": [dict(item) for item in inherited],
    }


def _baseline_semantic_records(
    workspace: Path, document_id: str
) -> dict[str, dict[str, str | None]]:
    if not document_id.startswith("spec:"):
        return {}
    capability = document_id.removeprefix("spec:")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", capability):
        return {}
    path = workspace / "openspec" / "specs" / capability / "spec.md"
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        source = _read_utf8(path)
    except PmError:
        return {}
    items = semantic_items(source, document_id, str(path.relative_to(workspace)))
    parent: str | None = None
    records: dict[str, dict[str, str | None]] = {}
    for item in items:
        if item.kind == "requirement":
            parent = item.semantic_id
        records[item.semantic_id] = {
            "semantic_id": item.semantic_id,
            "kind": item.kind,
            "document_id": document_id,
            "capability": capability,
            "parent_id": parent if item.kind == "scenario" else None,
        }
    return records


def _inherited_use_supported(
    source: str, semantic_id: str, record: dict[str, str | None]
) -> bool:
    items = semantic_items(source, str(record["document_id"]), "spec.md")
    target = next((item for item in items if item.semantic_id == semantic_id), None)
    if target is None or target.kind != record.get("kind"):
        return False
    section = ""
    for line in source.splitlines()[: target.line]:
        match = re.match(r"^##\s+(ADDED|MODIFIED|REMOVED)\s+Requirements\s*$", line.strip(), re.I)
        if match:
            section = match.group(1).upper()
    if section not in {"MODIFIED", "REMOVED"}:
        return False
    if target.kind == "scenario":
        parent: str | None = None
        for item in items:
            if item.line >= target.line:
                break
            if item.kind == "requirement":
                parent = item.semantic_id
        if parent != record.get("parent_id"):
            return False
    return True


def _work_item_kind(task: WbsTask) -> str:
    return "group" if task.task_id.startswith("G-") else "task"


def _work_item_fingerprint(task: WbsTask) -> str:
    payload = json.dumps(
        asdict(task), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _work_item_record(task: WbsTask) -> dict[str, str]:
    return {
        "kind": _work_item_kind(task),
        "fingerprint": _work_item_fingerprint(task),
    }


def _work_item_ledger(
    identity: dict[str, Any], tasks: Iterable[WbsTask]
) -> dict[str, dict[str, dict[str, str]]]:
    current = {task.task_id: _work_item_record(task) for task in tasks}
    raw = identity.get("work_item_ids")
    if not isinstance(raw, dict):
        return {"active": current, "retired": {}}
    result: dict[str, dict[str, dict[str, str]]] = {"active": {}, "retired": {}}
    for state in ("active", "retired"):
        values = raw.get(state)
        if not isinstance(values, dict):
            continue
        for item_id, value in values.items():
            if (
                isinstance(item_id, str)
                and isinstance(value, dict)
                and value.get("kind") in {"task", "group"}
                and isinstance(value.get("fingerprint"), str)
            ):
                result[state][item_id] = {
                    "kind": str(value["kind"]),
                    "fingerprint": str(value["fingerprint"]),
                }
    return result


def _identity_with_work_items(
    identity: dict[str, Any], ledger: dict[str, dict[str, dict[str, str]]]
) -> dict[str, Any]:
    updated = dict(identity)
    updated["work_item_ids"] = {
        "active": dict(sorted(ledger["active"].items())),
        "retired": dict(sorted(ledger["retired"].items())),
    }
    return updated


def _mint_work_item_id(
    kind: str,
    ledger: dict[str, dict[str, dict[str, str]]],
    extra_reserved: frozenset[str] = frozenset(),
) -> str:
    prefix = "G" if kind == "group" else "T"
    reserved = set(ledger["active"]) | set(ledger["retired"]) | set(extra_reserved)
    for _ in range(128):
        candidate = f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
        if candidate not in reserved:
            return candidate
    raise PmError("identity_exhausted", "could not allocate a unique work item identity", status=503)


def _reconcile_work_items(
    ledger: dict[str, dict[str, dict[str, str]]],
    before_tasks: Iterable[WbsTask],
    after_tasks: Iterable[WbsTask],
    *,
    minted_ids: frozenset[str] = frozenset(),
    restored_ids: frozenset[str] = frozenset(),
) -> dict[str, dict[str, dict[str, str]]]:
    before = {task.task_id: task for task in before_tasks}
    after = {task.task_id: task for task in after_tasks}
    active = {key: dict(value) for key, value in ledger["active"].items()}
    retired = {key: dict(value) for key, value in ledger["retired"].items()}
    introduced = set(after) - set(before)
    authorized = set(minted_ids) | set(restored_ids)
    unauthorized = introduced - authorized
    if unauthorized:
        raise PmError(
            "work_item_id_replacement",
            "work item identities must be created by the server",
            status=409,
            details={"identities": sorted(unauthorized)},
        )
    for item_id in set(before) & set(after):
        prior = active.get(item_id) or _work_item_record(before[item_id])
        if prior["kind"] != _work_item_kind(after[item_id]):
            raise PmError(
                "work_item_id_retyped",
                "work item identity type cannot change",
                status=409,
                details={"identities": [item_id]},
            )
        active[item_id] = _work_item_record(after[item_id])
    for item_id in set(before) - set(after):
        record = active.pop(item_id, _work_item_record(before[item_id]))
        record["fingerprint"] = _work_item_fingerprint(before[item_id])
        retired[item_id] = record
    for item_id in minted_ids:
        if item_id in retired or item_id not in after:
            raise PmError("retired_id_reused", "server identity allocation collided", status=409)
        active[item_id] = _work_item_record(after[item_id])
    for item_id in restored_ids:
        expected = retired.get(item_id)
        actual = _work_item_record(after[item_id]) if item_id in after else None
        if actual is None or expected != actual:
            raise PmError(
                "retired_id_reused",
                "retired work item identity can restore only the same entity",
                status=409,
            )
        retired.pop(item_id)
        active[item_id] = actual
    overlap = set(active) & set(retired)
    if overlap:
        raise PmError("duplicate_id", "work item identity ledger overlaps", status=409)
    return {"active": active, "retired": retired}


def _reconcile_external_work_items(
    ledger: dict[str, dict[str, dict[str, str]]],
    current_tasks: Iterable[WbsTask],
    *, approved_ids: frozenset[str] = frozenset(),
) -> dict[str, dict[str, dict[str, str]]]:
    current = {task.task_id: task for task in current_tasks}
    active = {key: dict(value) for key, value in ledger["active"].items()}
    retired = {key: dict(value) for key, value in ledger["retired"].items()}
    introduced = set(current) - set(active)
    if introduced:
        code = "retired_id_reused" if introduced & set(retired) else "work_item_id_replacement"
        raise PmError(
            code,
            "external edits cannot introduce or reuse work item identities",
            status=409,
            details={"identities": sorted(introduced)},
        )
    bypassed_cancellation = sorted(
        item_id for item_id in approved_ids
        if item_id in active
        and (item_id not in current or current[item_id].cancelled)
    )
    if bypassed_cancellation:
        raise PmError(
            "approved_task_requires_cancellation",
            "approved work must use the reviewed cancel_task operation",
            status=409,
            details={"task_ids": bypassed_cancellation},
        )
    for item_id, task in current.items():
        if active[item_id]["kind"] != _work_item_kind(task):
            raise PmError(
                "work_item_id_retyped",
                "work item identity type cannot change",
                status=409,
                details={"identities": [item_id]},
            )
        active[item_id] = _work_item_record(task)
    for item_id in set(active) - set(current):
        retired[item_id] = active.pop(item_id)
    return {"active": active, "retired": retired}


def _require_valid_wbs(*finding_sets: Iterable[PmFinding]) -> None:
    findings = tuple(item for items in finding_sets for item in items)
    if any(item.severity == "blocking" for item in findings):
        raise PmError(
            "unsupported_format", "tasks.md edit is structurally invalid",
            status=409,
            details={"findings": [item.code for item in findings]},
        )


def _approved_task_ids(
    approval: dict[str, Any] | None, workspace: Path, root: Path
) -> frozenset[str]:
    if approval is None:
        return frozenset()
    snapshot = str(approval.get("snapshot_commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", snapshot):
        return frozenset()
    relative = root.relative_to(workspace) / "tasks.md"
    try:
        source = _git(workspace, "show", f"{snapshot}:{relative}")
    except PmError:
        return frozenset()
    return frozenset(task.task_id for task in parse_wbs(source).tasks)


def _cancel_task_source(
    source: str, task_id: str, reason: Any, successor_refs: Any
) -> str:
    parsed = parse_wbs(source)
    tasks = {task.task_id: task for task in parsed.tasks}
    target = tasks.get(task_id)
    if target is None or not target.executable:
        raise PmError("unknown_task", f"unknown executable task {task_id}", status=404)
    if not isinstance(reason, str) or not reason.strip():
        raise PmError(
            "cancellation_reason_required",
            "approved task cancellation requires a reason",
            status=409,
        )
    if not isinstance(successor_refs, list) or not all(
        isinstance(item, str) for item in successor_refs
    ):
        raise PmError("invalid_request", "successor_refs must be a list of task IDs")
    successors = tuple(dict.fromkeys(successor_refs))
    successor_tasks: list[WbsTask] = []
    for successor_id in successors:
        successor = tasks.get(successor_id)
        if successor is None or not successor.executable or successor_id == task_id:
            raise PmError(
                "invalid_cancellation_successor",
                f"successor {successor_id} is not an executable task",
                status=409,
            )
        successor_tasks.append(successor)
    covered = {
        reference for successor in successor_tasks
        for reference in successor.requirement_refs
    }
    missing_coverage = sorted(set(target.requirement_refs) - covered)
    if missing_coverage:
        raise PmError(
            "cancellation_coverage_gap",
            "successors do not preserve the cancelled task requirement coverage",
            status=409,
            details={"requirements": missing_coverage},
        )
    dependents = [
        task for task in parsed.tasks
        if any(dependency.task_id == task_id for dependency in task.dependencies)
    ]
    if dependents and not successors:
        raise PmError(
            "task_in_use",
            "approved task has dependents and requires successor tasks",
            status=409,
        )
    updated = source
    for dependent in dependents:
        rewritten: list[dict[str, Any]] = []
        for dependency in dependent.dependencies:
            if dependency.task_id != task_id:
                rewritten.append(asdict(dependency))
                continue
            for successor_id in successors:
                rewritten.append({
                    "task_id": successor_id,
                    "kind": dependency.kind,
                    "justification": dependency.justification,
                })
        updated = patch_task_field(updated, dependent.task_id, "depends_on", rewritten)
    updated = patch_task_field(
        updated, task_id, "cancellation_reason", reason.strip()
    )
    updated = patch_task_field(updated, task_id, "successors", list(successors))
    updated = patch_task_field(updated, task_id, "cancelled", True)
    block = next(
        item for item in parse_wbs(updated).blocks if item.item_id == task_id
    )
    body = updated[block.body_start:block.body_end]
    line = re.search(
        r"(?m)^(?P<whole>[ \t]*[-*+][ \t]+\[[ xX]\][ \t]+.+)$", body
    )
    if line is None:
        raise PmError("unsupported_format", "cancelled task checkbox is missing", status=409)
    start = block.body_start + line.start("whole")
    end = block.body_start + line.end("whole")
    return updated[:start] + f"- Cancelled: {target.title}" + updated[end:]


def _identity_source(
    identity: dict[str, Any], ledger: dict[str, Any]
) -> str:
    updated = dict(identity)
    updated["semantic_ids"] = {
        "active": list(ledger["active"]),
        "retired": list(ledger["retired"]),
        "inherited": list(ledger.get("inherited", [])),
    }
    return yaml.safe_dump(updated, sort_keys=False)


def _replace_semantic_stage(stage: Path, destination: Path) -> None:
    """Retain destination permissions when publishing or recovering a staged file."""
    try:
        existing = destination.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISREG(existing.st_mode):
            with os.fdopen(os.open(stage, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise OSError("semantic stage is not a regular file")
                mode = stat.S_IMODE(existing.st_mode)
                if hasattr(os, "fchmod"):
                    os.fchmod(handle.fileno(), mode)
                else:
                    os.chmod(stage, mode)
                os.fsync(handle.fileno())
    os.replace(stage, destination)


def _atomic_semantic_write(
    root: Path,
    document_path: Path,
    document_source: str,
    identity: dict[str, Any],
    ledger: dict[str, list[str]],
) -> None:
    """Stage and recover a document + permanent-ID ledger as one intent."""
    document_relative = str(document_path.relative_to(root))
    document_stage = root / ".symphony-semantic-document.next"
    identity_stage = root / ".symphony-semantic-identity.next"
    journal_path = root / ".symphony-semantic-journal.json"
    identity_source = _identity_source(identity, ledger)
    _atomic_write(document_stage, document_source)
    _atomic_write(identity_stage, identity_source)
    journal = {
        "version": 1,
        "document": document_relative,
        "document_hash": _hash_text(document_source),
        "identity_hash": _hash_text(identity_source),
    }
    _atomic_write(journal_path, json.dumps(journal, sort_keys=True) + "\n")
    _replace_semantic_stage(document_stage, document_path)
    _semantic_faultpoint("after_document")
    _replace_semantic_stage(identity_stage, root / "symphony.yaml")
    _semantic_faultpoint("after_identity")
    journal_path.unlink(missing_ok=True)


_TRANSACTION_JOURNALS = (
    ".symphony-semantic-journal.json",
    ".symphony-multi-document-journal.json",
)


def _recover_change_transactions(root: Path) -> None:
    """Roll interrupted document transactions forward under the change lock.

    A journal is only a crash once its writer has released the lock; replaying
    it earlier races the live writer's own stage replaces and journal unlink.
    """
    if not any((root / name).is_file() for name in _TRANSACTION_JOURNALS):
        return
    with _change_lock(root):
        _recover_semantic_transaction(root)
        _recover_multi_document_transaction(root)


def _recover_semantic_transaction(root: Path) -> None:
    journal_path = root / ".symphony-semantic-journal.json"
    if not journal_path.is_file():
        return
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        relative = journal["document"]
        if not isinstance(relative, str):
            raise ValueError("invalid document path")
        document_path = (root / relative).resolve()
        if root.resolve() not in document_path.parents:
            raise ValueError("document path escapes change")
        document_stage = root / ".symphony-semantic-document.next"
        identity_stage = root / ".symphony-semantic-identity.next"
        if document_stage.is_file():
            _replace_semantic_stage(document_stage, document_path)
        if identity_stage.is_file():
            _replace_semantic_stage(identity_stage, root / "symphony.yaml")
        if (
            not document_path.is_file()
            or _hash_bytes(document_path.read_bytes()) != journal.get("document_hash")
            or _hash_bytes((root / "symphony.yaml").read_bytes()) != journal.get("identity_hash")
        ):
            raise ValueError("semantic transaction target does not match journal")
        journal_path.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise PmError(
            "workspace_repair_required",
            "semantic identity transaction needs repair",
            status=409,
        ) from exc


def _atomic_multi_document_write(
    root: Path, paths: dict[str, Path], sources: dict[str, str], identity_source: str
) -> None:
    journal_path = root / ".symphony-multi-document-journal.json"
    journal = {
        "state": "PREPARED",
        "documents": {
            document_id: {"path": str(path.relative_to(root)), "new": sources[document_id]}
            for document_id, path in paths.items()
        },
        "identity_new": identity_source,
    }
    _atomic_write(journal_path, json.dumps(journal, ensure_ascii=False, sort_keys=True))
    for index, (document_id, path) in enumerate(paths.items(), start=1):
        _atomic_write(path, sources[document_id])
        if os.environ.get("SYMPHONY_PM_MULTI_FAULT") == f"after_document_{index}":
            raise RuntimeError(f"multi-document fault after document {index}")
    _atomic_write(root / "symphony.yaml", identity_source)
    if os.environ.get("SYMPHONY_PM_MULTI_FAULT") == "after_identity":
        raise RuntimeError("multi-document fault after identity")
    _atomic_write(journal_path, json.dumps({**journal, "state": "COMPLETED"}, ensure_ascii=False, sort_keys=True))
    journal_path.unlink(missing_ok=True)


def _recover_multi_document_transaction(root: Path) -> None:
    journal_path = root / ".symphony-multi-document-journal.json"
    if not journal_path.is_file():
        return
    try:
        journal = json.loads(_read_utf8(journal_path))
        documents = journal["documents"]
        if not isinstance(documents, dict):
            raise ValueError("invalid multi-document journal")
        for value in documents.values():
            if not isinstance(value, dict):
                raise ValueError("invalid multi-document entry")
            path = _contained_journal_path(root, str(value["path"]))
            _atomic_write(path, str(value["new"]))
        _atomic_write(root / "symphony.yaml", str(journal["identity_new"]))
        journal_path.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise PmError(
            "workspace_repair_required",
            "multi-document proposal transaction needs repair",
            status=409,
        ) from exc


def _contained_journal_path(root: Path, relative: str) -> Path:
    path = root / relative
    resolved = path.resolve(strict=False)
    if root.resolve() not in resolved.parents or path.is_symlink():
        raise ValueError("unsafe journal path")
    return path


def _semantic_faultpoint(phase: str) -> None:
    if os.environ.get("SYMPHONY_PM_SEMANTIC_FAULT") == phase:
        raise RuntimeError(f"semantic faultpoint: {phase}")


def _require_semantic_document_kind(document_id: str, kind: str) -> None:
    allowed = {
        "proposal": {"decision"},
        "ux": {"storyboard"},
        "design": {"contract"},
    }
    permitted = allowed.get(document_id, {"requirement", "scenario"} if document_id.startswith("spec:") else set())
    if kind not in permitted:
        raise PmError(
            "invalid_request",
            f"{kind} cannot be created in {document_id}",
        )


def _openspec_resource_root() -> Path:
    root = Path(__file__).with_name("resources") / "openspec" / "symphony-pm"
    if not (root / "schema.yaml").is_file():
        raise PmError("capability_unavailable", "bundled symphony-pm schema is unavailable", status=503)
    return root


def _install_openspec_profile(workspace: Path) -> None:
    source = _openspec_resource_root()
    target = workspace / "openspec" / "schemas" / "symphony-pm"
    if target.is_symlink():
        raise PmError("unsafe_path", "OpenSpec schema destination is a symlink", status=409)
    for item in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = item.relative_to(source)
        destination = target / relative
        if destination.is_symlink():
            raise PmError("unsafe_path", "OpenSpec schema file is a symlink", status=409)
        payload = item.read_bytes()
        if destination.exists():
            if not destination.is_file() or destination.read_bytes() != payload:
                raise PmError(
                    "openspec_profile_conflict",
                    f"project OpenSpec schema conflicts with bundled profile: {relative}",
                    status=409,
                )
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination, payload.decode("utf-8"))


def _revision_hash(root: Path) -> str:
    return _revision_from_manifest(_managed_manifest(root))


def _managed_manifest(
    root: Path, overrides: dict[str, bytes] | None = None
) -> dict[str, bytes]:
    manifest = {
        str(path.relative_to(root)): path.read_bytes()
        for path in _managed_files(root)
    }
    manifest.update(overrides or {})
    return dict(sorted(manifest.items()))


def _revision_from_manifest(manifest: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative, content in sorted(manifest.items()):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _scope_hash(
    root: Path, workspace: Path,
    *, managed_manifest: dict[str, bytes] | None = None,
) -> str:
    digest = hashlib.sha256()
    managed = managed_manifest or _managed_manifest(root)
    contents: dict[str, bytes] = {
        str((root / relative).relative_to(workspace)): content
        for relative, content in managed.items()
    }
    candidates: list[Path] = []
    for external in (
        workspace / "openspec" / "config.yaml",
        workspace / "openspec" / "specs",
        workspace / "openspec" / "schemas" / "symphony-pm",
    ):
        if external.is_file() and not external.is_symlink():
            candidates.append(external)
        elif external.exists() and not external.is_symlink():
            candidates.extend(
                path
                for path in external.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
    for path in sorted(set(candidates), key=str):
        relative = str(path.relative_to(workspace))
        contents[relative] = path.read_bytes()
    tasks_relative = str((root / "tasks.md").relative_to(workspace))
    for relative, raw_content in sorted(contents.items()):
        content = raw_content
        if relative == tasks_relative:
            text = content.decode("utf-8")
            text = re.sub(
                r"(?m)^(\s*[-*+]\s+\[)[xX](\]\s+)",
                r"\1 \2",
                text,
            )
            content = text.encode("utf-8")
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _managed_files(root: Path) -> list[Path]:
    fixed = {
        ".openspec.yaml", "symphony.yaml", "proposal.md", "ux.md", "design.md", "tasks.md"
    }
    paths = [
        path for path in root.iterdir()
        if path.name in fixed and path.is_file() and not path.is_symlink()
    ]
    specs = root / "specs"
    if specs.exists() and not specs.is_symlink():
        paths.extend(
            path for path in specs.glob("*/spec.md")
            if path.is_file() and not path.is_symlink()
        )
    return sorted(paths, key=lambda item: str(item.relative_to(root)))


def _workspace_hashes(workspace: Path) -> dict[str, str]:
    root = workspace / "openspec"
    if not root.exists():
        return {}
    return {
        str(path.relative_to(workspace)): _hash_bytes(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _dirty_paths(path: Path) -> list[str]:
    output = _git(path, "status", "--porcelain=v1", "--untracked-files=all")
    return [line[3:] for line in output.splitlines() if len(line) > 3]


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *(["--no-optional-locks"] if args and args[0] == "status" else []), "-C", str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PmError(
            "workspace_unavailable",
            result.stderr.strip() or "Git command failed",
            status=409,
        )
    return result.stdout.rstrip("\r\n")


def _git_path(path: Path, argument: str) -> Path:
    raw = _git(path, "rev-parse", argument)
    value = Path(raw)
    return (path / value).resolve() if not value.is_absolute() else value.resolve()


def _worktree_paths(path: Path) -> set[Path]:
    output = _git(path, "worktree", "list", "--porcelain")
    return {
        Path(line.removeprefix("worktree ")).resolve()
        for line in output.splitlines()
        if line.startswith("worktree ")
    }


def _require_contained_directory(
    workspace: Path, directory: Path, *, create: bool
) -> None:
    workspace = workspace.resolve()
    cursor = directory
    missing: list[Path] = []
    while not cursor.exists() and cursor != workspace:
        missing.append(cursor)
        cursor = cursor.parent
    if cursor.is_symlink() or (cursor.resolve() != workspace and workspace not in cursor.resolve().parents):
        raise PmError("unsafe_path", "planning path escapes claimed workspace")
    for path in reversed(missing):
        path.mkdir()
    if directory.is_symlink() or workspace not in directory.resolve().parents:
        raise PmError("unsafe_path", "planning directory is not contained")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        mode = None
    else:
        mode = stat.S_IMODE(existing.st_mode) if stat.S_ISREG(existing.st_mode) else None
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            if mode is not None:
                os.chmod(name, mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _read_utf8(path: Path) -> str:
    """Decode managed source without universal-newline translation."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PmError(
            "unsupported_encoding",
            f"{path.name} must be UTF-8 for managed editing",
            status=409,
        ) from exc


_HELD_CHANGE_LOCKS = threading.local()


@contextmanager
def _change_lock(root: Path, *, shared: bool = False):
    """Hold the change's file lock: exclusive for writers, shared for readers.

    Shared readers (exchange snapshots read off the event loop) do not block
    each other, only writers. msvcrt has no shared mode, so Windows readers
    take the lock exclusively.
    """
    held: dict[Path, bool] = _HELD_CHANGE_LOCKS.__dict__.setdefault("modes", {})
    key = root.resolve()
    if key in held:
        # Same-thread re-entry (approval reads the change it has locked): a
        # second lock on a new file description would wait on this thread.
        if held[key] and not shared:
            raise RuntimeError("a shared change lock cannot be upgraded in place")
        yield
        return
    lock_path = root / ".symphony-pm.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            held[key] = shared
            try:
                yield
            finally:
                del held[key]
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            held[key] = shared
            try:
                yield
            finally:
                del held[key]
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _hash_text(text: str) -> str:
    return _hash_bytes(text.encode("utf-8"))


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()
