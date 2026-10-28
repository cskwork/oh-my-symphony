"""Fail-closed PM ticket recognition and actual-spawn fencing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable

from symphony.issue import Issue
from symphony.orchestrator.run_registry import RunRegistry
from symphony.trackers.file import without_orchestrator_artifacts
from .models import DispatchPermit, PmError, PmMembership
from .documents import _resolve_change_root, _scope_hash


class PmAuthority:
    def __init__(
        self,
        registry: RunRegistry,
        lock_root: Path,
        successful_states: set[str] | None = None,
    ) -> None:
        self.registry = registry
        del lock_root
        self.lock_root = (registry.path.parent / "pm-locks").resolve()
        self.successful_states = {
            state.casefold() for state in (successful_states or {"done", "document"})
        }

    def recognize(self, issue: Issue, project_id: str) -> PmMembership:
        try:
            mapping = self.registry.get_pm_managed_ticket(project_id, issue.identifier)
        except Exception as exc:
            if issue.planning_ref is not None:
                return PmMembership(True, False, project_id, issue.identifier, reason=f"registry unavailable: {exc}")
            raise
        if mapping is None:
            try:
                fallback = self.registry.get_pm_fallback_ticket(project_id, issue.identifier)
            except Exception as exc:
                return PmMembership(
                    True, False, project_id, issue.identifier,
                    reason=f"durable PM fallback authority is unavailable: {exc}",
                )
            if fallback is not None:
                return PmMembership(
                    True, False, project_id, issue.identifier,
                    change_id=str(fallback.get("change_id") or "") or None,
                    task_id=str(fallback.get("task_id") or "") or None,
                    task_revision=str(fallback.get("task_revision") or "") or None,
                    handoff_id=str(fallback.get("handoff_id") or "") or None,
                    reason="durable PM fallback identity requires registry recovery",
                )
            conversion = self.registry.get_pm_legacy_conversion_by_source(
                project_id, issue.identifier
            )
            if conversion is not None:
                return PmMembership(
                    True, False, project_id, issue.identifier,
                    change_id=str(conversion["change_id"]),
                    reason="legacy executable scope was superseded by PM planning",
                )
            if issue.planning_ref is None and issue.planning_ref_state == "absent":
                return PmMembership(False, True, project_id, issue.identifier)
            return PmMembership(True, False, project_id, issue.identifier, reason="PM registry mapping is missing")
        batch = self.registry.get_pm_handoff_batch(mapping.handoff_id)
        if batch is None:
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, reason="PM handoff snapshot is missing")
        scope_hash = str(batch["scope_hash"])
        approval = self.registry.get_pm_approval(str(batch["approval_id"]))
        active_approval = self.registry.get_active_pm_approval(project_id, mapping.change_id)
        ticket_scope = self.registry.get_pm_managed_ticket_scope(
            project_id, issue.identifier
        )
        if mapping.state != "PUBLISHED" or str(batch["state"]) != "PUBLISHED":
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "PM handoff is not published")
        if self.registry.get_accepted_pm_commit(project_id, issue.identifier) is not None:
            return PmMembership(
                True, False, project_id, issue.identifier,
                mapping.change_id, mapping.task_id, mapping.task_revision,
                mapping.handoff_id, scope_hash,
                "human-accepted work is pending controller completion",
            )
        if (
            approval is None
            or active_approval is None
            or str(active_approval["approval_id"]) != str(batch["approval_id"])
            or str(approval["scope_hash"]) != scope_hash
            or str(batch["snapshot_id"]) != str(approval["approval_id"])
            or ticket_scope is None
        ):
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "PM approval or snapshot authority is missing")
        workspace = self.registry.get_pm_workspace(project_id, mapping.change_id)
        if workspace is None or workspace.state not in {"ACTIVE", "EXTERNAL_EDIT"}:
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "current planning workspace authority is unavailable")
        root: Path | None = None
        try:
            root, _ = _resolve_change_root(workspace.workspace_path, mapping.change_id)
            current_scope = _scope_hash(root, workspace.workspace_path)
        except PmError:
            current_scope = ""
        if current_scope != scope_hash:
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "current planning scope differs from the approved snapshot")
        assert root is not None
        ref = issue.planning_ref
        if ref is None:
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "ticket PM provenance is missing")
        if (
            ref.change_id, ref.task_id, ref.task_revision, ref.handoff_id
        ) != (
            mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id
        ):
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "ticket PM provenance conflicts with durable mapping")
        if str(ticket_scope["content_hash"]) != _issue_scope_hash(issue):
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "ticket scope differs from the approved snapshot")
        unresolved = [
            blocker.identifier or blocker.id or "unknown"
            for blocker in issue.blocked_by
            if (blocker.state or "").casefold() not in self.successful_states
        ]
        if unresolved:
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, f"required predecessors are not verified: {', '.join(unresolved)}")
        try:
            manifest_path = Path(str(approval["manifest_ref"]))
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
        except (OSError, json.JSONDecodeError):
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "approval manifest is unavailable")
        if (
            not isinstance(manifest, dict)
            or manifest.get("approval_id") != approval["approval_id"]
            or manifest.get("scope_hash") != scope_hash
            or manifest.get("revision") != approval["revision_hash"]
            or "sha256:" + hashlib.sha256(manifest_bytes).hexdigest() != approval["manifest_hash"]
        ):
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "approval manifest does not match durable authority")
        file_hashes = manifest.get("file_hashes")
        baseline_hashes = manifest.get("baseline_hashes")
        if not isinstance(file_hashes, dict) or not isinstance(baseline_hashes, dict):
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "approval manifest input hashes are incomplete")
        change_prefix = str(root.relative_to(workspace.workspace_path))
        inputs = [
            (f"{change_prefix}/{relative}", expected_hash)
            for relative, expected_hash in file_hashes.items()
        ] + [
            (f"openspec/specs/{relative}", expected_hash)
            for relative, expected_hash in baseline_hashes.items()
        ]
        snapshot_commit = str(approval.get("snapshot_commit") or "")
        for relative, expected_hash in inputs:
            actual = _git_blob_hash(workspace.workspace_path, snapshot_commit, relative)
            if actual != expected_hash:
                return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "approved snapshot input bytes are incomplete")
        manifest_required = manifest.get("required_procedures", {})
        required = self.registry.get_pm_required_procedures(project_id, issue.identifier)
        if not isinstance(manifest_required, dict) or manifest_required.get(mapping.task_id, {}) != required:
            return PmMembership(True, False, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash, "verification policy differs from the approved snapshot")
        return PmMembership(True, True, project_id, issue.identifier, mapping.change_id, mapping.task_id, mapping.task_revision, mapping.handoff_id, scope_hash)

    def reserve_dispatch(self, issue: Issue, project_id: str) -> DispatchPermit | None:
        membership = self.recognize(issue, project_id)
        if not membership.managed:
            return None
        if not membership.runnable:
            raise PmError("pm_dispatch_denied", membership.reason or "PM ticket is not runnable", status=409)
        assert membership.handoff_id and membership.task_revision and membership.scope_hash
        record = self.registry.reserve_pm_dispatch_permit(
            project_id=project_id,
            ticket_identifier=issue.identifier,
            handoff_id=membership.handoff_id,
            task_revision=membership.task_revision,
            issue_hash=_issue_hash(issue),
            scope_hash=membership.scope_hash,
        )
        return DispatchPermit(record.permit_id, record.project_id, record.ticket_identifier, record.handoff_id, record.task_revision, record.issue_hash, record.scope_hash, record.fence, record.state)

    def launch_guard(
        self,
        permit: DispatchPermit,
        issue_loader: Callable[[], Awaitable[Issue | None]],
        run_id: str,
        workspace_path: Path | None = None,
    ):
        @asynccontextmanager
        async def guard():
            mapping = self.registry.get_pm_managed_ticket(
                permit.project_id, permit.ticket_identifier
            )
            change_id = mapping.change_id if mapping is not None else permit.ticket_identifier
            handle = await asyncio.to_thread(
                self._acquire_named_lock, permit.project_id, change_id
            )
            try:
                issue = await issue_loader()
                if issue is None or issue.identifier != permit.ticket_identifier:
                    self._revoke(permit)
                    raise PmError("pm_dispatch_denied", "managed ticket disappeared before launch", status=409)
                membership = self.recognize(issue, permit.project_id)
                if (
                    not membership.runnable
                    or membership.handoff_id != permit.handoff_id
                    or membership.task_revision != permit.task_revision
                    or membership.scope_hash != permit.scope_hash
                    or _issue_hash(issue) != permit.issue_hash
                ):
                    self._revoke(permit)
                    raise PmError("pm_dispatch_denied", "managed ticket authority drifted before launch", status=409)
                self._verify_ancestry(issue, workspace_path, permit.project_id)
                if not self.registry.transition_pm_dispatch_permit(
                    permit.permit_id, fence=permit.fence,
                    from_states=("RESERVED", "RUNNING"), to_state="STARTING",
                ):
                    raise PmError("pm_dispatch_denied", "dispatch permit is stale or revoked", status=409)

                async def publish_started(pid: int) -> None:
                    fresh = await issue_loader()
                    if fresh is None or _issue_hash(fresh) != permit.issue_hash:
                        self._revoke(permit)
                        raise PmError("pm_dispatch_denied", "managed ticket changed during process launch", status=409)
                    if not self.registry.transition_pm_dispatch_permit(
                        permit.permit_id, fence=permit.fence,
                        from_states=("STARTING",), to_state="RUNNING",
                        process_pid=pid, run_id=run_id,
                    ):
                        raise PmError("pm_dispatch_denied", "dispatch authority was revoked during process launch", status=409)

                yield publish_started
            except BaseException:
                self._revoke(permit)
                raise
            finally:
                await asyncio.to_thread(self._release_lock, handle)

        return guard

    def _verify_ancestry(
        self, issue: Issue, workspace_path: Path | None, project_id: str
    ) -> None:
        if not issue.blocked_by:
            return
        if workspace_path is None:
            raise PmError("pm_dispatch_denied", "successor workspace is unavailable for ancestry proof", status=409)
        kinds: dict[str, str] = {}
        scope = self.registry.get_pm_managed_ticket_scope(project_id, issue.identifier)
        if scope is not None:
            try:
                stored = json.loads(str(scope.get("input_json") or "{}"))
                raw_kinds = stored.get("dependency_kinds", {})
                if isinstance(raw_kinds, dict):
                    kinds = {str(key): str(value) for key, value in raw_kinds.items()}
            except json.JSONDecodeError:
                kinds = {}
        for blocker in issue.blocked_by:
            identifier = blocker.identifier or blocker.id or ""
            if kinds.get(identifier) == "order_only":
                continue
            commit = self.registry.get_accepted_pm_commit(project_id, identifier)
            if not commit:
                raise PmError("pm_dispatch_denied", f"predecessor {identifier} has no human-accepted commit", status=409)
            result = subprocess.run(
                ["git", "-C", str(workspace_path), "merge-base", "--is-ancestor", commit, "HEAD"],
                check=False, capture_output=True,
            )
            if result.returncode != 0:
                raise PmError("pm_dispatch_denied", f"predecessor {identifier} commit is not in the successor base", status=409)

    def revoke(self, project_id: str, ticket_identifier: str) -> int:
        mapping = self.registry.get_pm_managed_ticket(project_id, ticket_identifier)
        change_id = mapping.change_id if mapping is not None else ticket_identifier
        handle = self._acquire_named_lock(project_id, change_id)
        try:
            return self.registry.revoke_pm_authority(
                project_id, ticket_identifier, reason="revoked by PM authority"
            )
        finally:
            self._release_lock(handle)

    def _revoke(self, permit: DispatchPermit) -> None:
        self.registry.revoke_pm_dispatch(permit.project_id, permit.ticket_identifier)

    def _acquire_named_lock(self, project_id: str, change_id: str):
        self.lock_root.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(f"{project_id}:{change_id}".encode()).hexdigest()
        handle = (self.lock_root / f"{name}.lock").open("a+b")
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    @staticmethod
    def _release_lock(handle) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _git_blob_hash(workspace: Path, commit: str, relative: str) -> str:
    if not commit:
        return ""
    result = subprocess.run(
        ["git", "-C", str(workspace), "show", f"{commit}:{relative}"],
        check=False, capture_output=True,
    )
    if result.returncode != 0:
        return ""
    return "sha256:" + hashlib.sha256(result.stdout).hexdigest()


def _issue_hash(issue: Issue) -> str:
    payload = {
        "id": issue.id,
        "identifier": issue.identifier,
        "title": issue.title,
        "description": without_orchestrator_artifacts(issue.description or ""),
        "labels": sorted(issue.labels),
        "blocked_by": sorted((item.identifier or item.id or "", item.state or "") for item in issue.blocked_by),
        "agent_kind": issue.agent_kind,
        "planning_ref": issue.to_template_dict().get("planning_ref"),
    }
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _issue_scope_hash(issue: Issue) -> str:
    ref = issue.planning_ref
    payload = {
        "title": issue.title,
        "description": without_orchestrator_artifacts(issue.description or ""),
        "agent_kind": issue.agent_kind,
        "request": issue.request,
        "blocked_by": [item.identifier for item in issue.blocked_by],
        "planning_ref": (
            {
                "change_id": ref.change_id, "task_id": ref.task_id,
                "task_revision": ref.task_revision, "handoff_id": ref.handoff_id,
                "snapshot_id": ref.snapshot_id,
            }
            if ref else None
        ),
    }
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
