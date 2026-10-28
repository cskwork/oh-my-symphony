"""Revision-bound PM approval manifests and planning-only Git snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from symphony.orchestrator.run_registry import RunRegistry
from .documents import (
    _CHANGE_ID, PmDocumentRepository, _atomic_write, _locked_change, _resolve_change_root,
)
from .models import ApprovalManifest, PmError, PmRequestContext, ValidationReport
from .yaml_safe import load_unique_yaml


class PmApprovalService:
    def __init__(
        self,
        repository: PmDocumentRepository,
        registry: RunRegistry,
        external_validator: Callable[[Path, str], ValidationReport] | None = None,
        verification_procedures: dict[str, tuple[str, ...]] | None = None,
        manual_verification_procedures: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.external_validator = external_validator
        self.verification_procedures = dict(verification_procedures or {})
        self.manual_verification_procedures = dict(manual_verification_procedures or {})

    def confirmation_token(self, ctx: PmRequestContext, *, owner: str) -> str:
        change = self.repository.get_change(ctx, owner=owner)
        return _confirmation(change.project_id, change.change_id, change.revision, change.scope_hash)

    def approve(
        self,
        ctx: PmRequestContext,
        confirmation_token: str,
        reviewer_label: str,
        *,
        owner: str,
    ) -> ApprovalManifest:
        if ctx.expected_revision is None:
            raise PmError("revision_conflict", "expected_revision is required", status=409)
        claim = self.repository._claim(ctx.change_id, owner=owner)
        with _locked_change(self.registry, ctx.project_id, ctx.change_id, claim.workspace_path) as (root, _):
            current_claim = self.repository._claim(ctx.change_id, owner=owner)
            if current_claim.fence != claim.fence or current_claim.state != "ACTIVE":
                raise PmError("scope_locked", "planning workspace is not ACTIVE", status=409)
            change = self.repository.get_change(ctx, owner=owner)
            if change.revision != ctx.expected_revision:
                raise PmError("revision_conflict", "planning revision changed", status=409)
            expected = _confirmation(change.project_id, change.change_id, change.revision, change.scope_hash)
            if confirmation_token != expected:
                raise PmError("confirmation_mismatch", "approval confirmation is stale", status=409)
            report = self.repository.validate(ctx, owner=owner)
            if not report.valid:
                unresolved = [
                    item for item in report.findings
                    if item.code == "unresolved_reference"
                ]
                if unresolved:
                    raise PmError(
                        "unresolved_reference",
                        "task planning references are not defined",
                        status=409,
                        details={
                            "references": sorted({
                                item.path[0] for item in unresolved if item.path
                            })
                        },
                    )
                raise PmError("validation_failed", "planning change has blocking findings", status=409, details={"findings": [item.code for item in report.findings]})
            if self.external_validator is None:
                raise PmError("capability_unavailable", "OpenSpec validation is required for approval", status=503)
            external = self.external_validator(current_claim.workspace_path, change.locator)
            if not external.valid:
                raise PmError("openspec_validation_failed", "OpenSpec validation has blocking findings", status=409, details={"findings": [item.code for item in external.findings]})
            unrelated = _unrelated_dirty(
                claim.workspace_path, root,
                registry=self.registry, project_id=ctx.project_id, owner=owner,
            )
            if unrelated:
                raise PmError("dirty_workspace", "planning worktree has unrelated changes", status=409, details={"paths": unrelated})
            approval_id = uuid.uuid4().hex
            approved_at = datetime.now(timezone.utc).isoformat()
            file_hashes = {document.path: document.content_hash for document in change.documents}
            baseline_hashes = _tree_hashes(claim.workspace_path / "openspec" / "specs")
            validation_digest = "sha256:" + hashlib.sha256(
                json.dumps([asdict(item) for item in report.findings], sort_keys=True).encode()
            ).hexdigest()
            tasks = tuple(
                task for task in self.repository.get_wbs(ctx, owner=owner).tasks
                if task.executable
            )
            required_procedures = {
                task.task_id: self._procedure_policy(task)
                for task in tasks
            }
            manifest = ApprovalManifest(
                approval_id, change.project_id, change.change_id, change.revision,
                change.scope_hash, claim.base_git_sha, change.schema_hash,
                file_hashes, baseline_hashes, validation_digest,
                reviewer_label.strip() or "local operator", approved_at,
            required_procedures,
            )
            manifest_path = claim.workspace_path / ".symphony" / "pm" / "manifests" / f"{approval_id}.json"
            _atomic_write(manifest_path, json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            relative_root = root.relative_to(claim.workspace_path)
            relative_manifest = manifest_path.relative_to(claim.workspace_path)
            snapshot_paths = [
                str(relative_root / ".openspec.yaml"),
                str(relative_root / "symphony.yaml"),
                *(str(relative_root / document.path) for document in change.documents),
                str(relative_manifest),
            ]
            for candidate in (
                claim.workspace_path / "openspec" / "config.yaml",
                claim.workspace_path / "openspec" / "schemas" / "symphony-pm",
                claim.workspace_path / "openspec" / "specs",
            ):
                if candidate.exists():
                    snapshot_paths.append(str(candidate.relative_to(claim.workspace_path)))
            try:
                _git(claim.workspace_path, "add", "--", *snapshot_paths)
                _git(claim.workspace_path, "commit", "-m", f"Approve PM plan {change.locator} ({approval_id[:12]})")
            except PmError:
                subprocess.run(["git", "-C", str(claim.workspace_path), "restore", "--staged", "--", *snapshot_paths], check=False, capture_output=True)
                manifest_path.unlink(missing_ok=True)
                raise
            snapshot_commit = _git(claim.workspace_path, "rev-parse", "HEAD")
            self.registry.record_pm_approval(
                approval_id=approval_id, project_id=change.project_id,
                change_id=change.change_id, scope_hash=change.scope_hash,
                revision_hash=change.revision, manifest_ref=str(manifest_path),
                reviewer_label=manifest.reviewer_label, approved_at=approved_at,
                snapshot_commit=snapshot_commit,
            )
        return manifest

    def _procedure_policy(self, task: Any) -> dict[str, Any]:
        if task.verification_policy is None:
            # Compatibility with the already-created native-only v9 candidates.
            return {
                procedure_id: _command_digest(command)
                for procedure_id, command in sorted(self.verification_procedures.items())
            }
        result: dict[str, Any] = {}
        for selected in task.verification_policy:
            if selected.kind == "native":
                command = self.verification_procedures.get(selected.procedure_id)
                if command is None:
                    raise PmError("unknown_verification_procedure", f"unknown native procedure {selected.procedure_id}", status=409)
                definition = {
                    "procedure_id": selected.procedure_id,
                    "kind": "native",
                    "argv": list(command),
                }
            else:
                manual = self.manual_verification_procedures.get(selected.procedure_id)
                if manual is None:
                    raise PmError("unknown_verification_procedure", f"unknown manual procedure {selected.procedure_id}", status=409)
                definition = {
                    "procedure_id": selected.procedure_id,
                    "kind": "manual",
                    "instructions": str(manual.instructions),
                    "report": str(manual.report),
                    "artifacts": str(manual.artifacts),
                }
                if manual.label:
                    definition["label"] = str(manual.label)
            definition["definition_digest"] = _definition_digest(definition)
            result[selected.procedure_id] = definition
        return result


def _confirmation(project_id: str, change_id: str, revision: str, scope_hash: str) -> str:
    return "sha256:" + hashlib.sha256(f"{project_id}\0{change_id}\0{revision}\0{scope_hash}".encode()).hexdigest()


def _unrelated_dirty(
    workspace: Path,
    change_root: Path,
    *,
    registry: RunRegistry | None = None,
    project_id: str = "",
    owner: str | None = None,
) -> list[str]:
    result = _git(workspace, "status", "--porcelain=v1", "--untracked-files=all")
    relative_root = str(change_root.relative_to(workspace))
    allowed_change_files = {
        f"{relative_root}/.openspec.yaml",
        f"{relative_root}/symphony.yaml",
        f"{relative_root}/proposal.md",
        f"{relative_root}/ux.md",
        f"{relative_root}/design.md",
        f"{relative_root}/tasks.md",
        f"{relative_root}/.symphony-pm.lock",
    }
    external_file = "openspec/config.yaml"
    external_prefixes = ("openspec/schemas/", "openspec/specs/")
    entries = [(line[:2], line[3:]) for line in result.splitlines() if len(line) > 3]
    unrelated: list[str] = []
    for status, path in entries:
        if path in allowed_change_files or path == external_file or path.startswith(external_prefixes):
            continue
        if (
            status == "??" and registry is not None and owner is not None
            and _claimed_sibling_lock(
                workspace, change_root, path,
                registry=registry, project_id=project_id, owner=owner,
            )
        ):
            continue
        relative = path.removeprefix(relative_root + "/")
        pieces = relative.split("/")
        if len(pieces) == 3 and pieces[0] == "specs" and pieces[2] == "spec.md":
            continue
        unrelated.append(path)
    return unrelated


_SIBLING_LOCK = re.compile(r"openspec/changes/([a-z0-9]+(?:-[a-z0-9]+)*)/\.symphony-pm\.lock")


def _claimed_sibling_lock(
    workspace: Path,
    change_root: Path,
    path: str,
    *,
    registry: RunRegistry,
    project_id: str,
    owner: str,
) -> bool:
    """True only for another change's empty internal lock proven to share this claim.

    A previous change in the same planning worktree leaves its runtime lock
    behind (``_change_lock`` never unlinks it). The lookup is read-only: no
    recovery, locking or write. Every unproven or failing predicate returns
    False, so the path stays unrelated and approval keeps its 409.
    """
    match = _SIBLING_LOCK.fullmatch(path)
    if match is None or match.group(1) == "archive":
        return False
    directory = workspace / "openspec" / "changes" / match.group(1)
    identity_path = directory / "symphony.yaml"
    lock = directory / ".symphony-pm.lock"
    components = (workspace / "openspec", workspace / "openspec" / "changes", directory, identity_path, lock)

    def lock_identity() -> tuple[int, ...] | None:
        if any(item.is_symlink() for item in components):
            return None
        info = os.lstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_size != 0 or info.st_nlink != 1:
            return None
        if not stat.S_ISREG(os.lstat(identity_path).st_mode):
            return None
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_nlink)

    try:
        before = lock_identity()
        if before is None or directory.resolve(strict=True) == change_root.resolve():
            return False
        identity = load_unique_yaml(identity_path.read_text(encoding="utf-8"))
        change_id = identity.get("change_id") if isinstance(identity, dict) else None
        if not isinstance(change_id, str) or not _CHANGE_ID.fullmatch(change_id):
            return False
        record = registry.get_pm_workspace(project_id, change_id)
        if (
            record is None
            or record.owner != owner
            or record.state != "ACTIVE"
            or Path(record.workspace_path).resolve(strict=True) != workspace.resolve(strict=True)
        ):
            return False
        resolved, _ = _resolve_change_root(workspace, change_id, recover=False)
        if resolved != directory.resolve(strict=True):
            return False
        return lock_identity() == before
    except Exception:
        # Registry, filesystem, YAML or resolver failure is never proof.
        return False


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()
    }


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", *(["--no-optional-locks"] if args and args[0] == "status" else []), "-C", str(path), *args], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise PmError("git_snapshot_failed", result.stderr.strip() or "Git command failed", status=409)
    return result.stdout.rstrip("\r\n")


def _command_digest(command: tuple[str, ...]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(command, separators=(",", ":")).encode()
    ).hexdigest()


def _definition_digest(definition: dict[str, Any]) -> str:
    authoritative = {key: value for key, value in definition.items() if key not in {"label", "definition_digest"}}
    return "sha256:" + hashlib.sha256(
        json.dumps(authoritative, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
