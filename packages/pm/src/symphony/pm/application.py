"""Host-owned PM service facade used by HTTP routes."""

from __future__ import annotations

import hashlib
import base64
import csv
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from .approval import PmApprovalService
from .documents import PmDocumentRepository, _atomic_write
from .exchange import WorkbookExchange
from .handoff import PmHandoffService
from .locks import change_authority_lock
from .models import ArchiveCandidate, ArchiveResult, PmError, PmFinding, PmRequestContext, ValidationReport
from .openspec import OpenSpecAdapter


_CAPABILITY_GATES: dict[str, tuple[str, ...]] = {
    "authoring": ("G0", "G1"),
    "exchange": ("G0", "G1", "G2"),
    "assistance": ("G0", "G1", "G3"),
    "handoff": ("G0", "G1", "G4"),
}
_CAPABILITY_PACKAGES: dict[str, dict[str, str]] = {
    "exchange": {"openpyxl": "3.1.5", "defusedxml": "0.7.1"},
}
_PACKAGE_BINDING_CACHE_LOCK = threading.Lock()
# Release evidence binds the installed core and the PM Workspace together.
_BOUND_DISTRIBUTIONS = ("oh-my-symphony", "oh-my-symphony-pm")
_PACKAGE_BINDING_CACHE: tuple[
    tuple[tuple[str, int, int, int, int, int, int], ...],
    tuple[dict[str, str] | None, str | None],
] | None = None


class PmApplication:
    def __init__(
        self,
        repository: PmDocumentRepository,
        approval: PmApprovalService,
        handoff: PmHandoffService,
        openspec: OpenSpecAdapter,
        exchange: WorkbookExchange,
        *,
        project_root: Path,
        flags: dict[str, bool],
        archive_journal_root: Path | None = None,
        development_verification: bool = False,
        demo: bool = False,
    ) -> None:
        self.repository = repository
        self.approval = approval
        self.handoff = handoff
        self.openspec = openspec
        self.exchange = exchange
        self.project_root = project_root.resolve()
        self.flags = flags
        self.development_verification = development_verification
        self.demo = demo
        self.archive_journal_root = (archive_journal_root or self.project_root / ".symphony" / "pm" / "archive-journal").resolve()

    def capabilities(self, *, host_tools: dict[str, Any] | None = None) -> dict[str, Any]:
        readiness = self._readiness()
        health = _runtime_health(self.repository, self.archive_journal_root)
        health["development_verification"] = self.development_verification
        capabilities = {
            name: self._capability_state(name, readiness)
            for name in _CAPABILITY_GATES
        }
        return {
            "project_id": self.repository.project_id,
            "demo": {"active": self.demo},
            "host_tools": host_tools or {},
            "flags": dict(self.flags),
            "effective_flags": {
                name: state["effective"] for name, state in capabilities.items()
            },
            "capabilities": capabilities,
            "openspec": {
                "available": None,
                "version": "1.14.2 required",
                "schema_supported": None,
                "reason": "validated per claimed planning worktree on explicit actions",
            },
            "pins": {
                "openspec": "1.14.2", "openpyxl": "3.1.5",
                "tabulator": "6.6.1", "mermaid": "12.1.0", "dompurify": "3.4.16",
            },
            "gates": readiness["gates"],
            "highest_passed_gate": readiness["highest_passed_gate"],
            "release_evidence": readiness["evidence"],
            "health": health,
        }

    def claim_workspace(self, change_id: str, path: str, owner: str):
        self._require("authoring")
        return self.repository.claim_workspace(change_id, Path(path), owner)

    def create_change(self, change_id: str | None, locator: str, title: str, owner: str):
        self._require("authoring")
        resolved = change_id or uuid.uuid4().hex
        return self.repository.create_change(change_id=resolved, locator=locator, title=title, owner=owner)

    def validate_change(self, ctx: PmRequestContext, owner: str) -> ValidationReport:
        local = self.repository.validate(ctx, owner=owner)
        change = self.repository.get_change(ctx, owner=owner)
        claim = self.repository._claim(ctx.change_id, owner=owner)
        findings = list(local.findings)
        try:
            external = self.openspec.validate(Path(claim.workspace_path), change.locator)
        except PmError as exc:
            findings.append(PmFinding(exc.code, exc.message, severity="blocking"))
        else:
            findings.extend(external.findings)
        return ValidationReport(not any(item.severity == "blocking" for item in findings), tuple(findings), "combined", "1.14.2")

    def preview_archive(self, ctx: PmRequestContext, owner: str) -> dict[str, Any]:
        self._require("handoff")
        change = self.repository.get_change(ctx, owner=owner)
        if ctx.expected_revision is None or change.revision != ctx.expected_revision:
            raise PmError("revision_conflict", "archive requires the current revision", status=409)
        approval = self.repository.registry.get_active_pm_approval(ctx.project_id, ctx.change_id)
        if approval is None or (str(approval["scope_hash"]), str(approval["revision_hash"])) != (change.scope_hash, change.revision):
            raise PmError("approval_required", "archive requires the approved revision", status=409)
        tasks = {task.task_id for task in self.repository.get_wbs(ctx, owner=owner).tasks if task.executable}
        mappings = {
            item.task_id: item for item in self.repository.registry.list_pm_managed_tickets(ctx.project_id)
            if item.change_id == ctx.change_id
        }
        missing = sorted(tasks - set(mappings))
        if missing:
            raise PmError("acceptance_incomplete", "archive scope has tasks without handoff records", status=409, details={"task_ids": missing})
        accepted: dict[str, str] = {}
        for task_id in sorted(tasks):
            mapping = mappings[task_id]
            if mapping.state.casefold() in {"cancelled", "canceled"}:
                continue
            commit = self.repository.registry.get_accepted_pm_commit(ctx.project_id, mapping.ticket_identifier)
            if commit is None:
                raise PmError("acceptance_incomplete", "archive scope has unaccepted work", status=409, details={"task_id": task_id, "ticket_identifier": mapping.ticket_identifier})
            accepted[mapping.ticket_identifier] = commit
        claim = self.repository._claim(ctx.change_id, owner=owner)
        report = self.openspec.validate(Path(claim.workspace_path), change.locator)
        blocking = [item for item in report.findings if item.severity in {"error", "blocking"}]
        if blocking:
            raise PmError("archive_conflict", "OpenSpec validation has unresolved findings", status=409, details={"findings": [item.code for item in blocking]})
        candidate = self.openspec.archive_preview(Path(claim.workspace_path), change.locator)
        if any(item.severity in {"error", "blocking"} for item in candidate.findings):
            raise PmError("archive_conflict", "archive preview has unresolved findings", status=409)
        payload = {"candidate": asdict(candidate), "revision": change.revision, "accepted_commits": accepted}
        token = "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {**payload, "confirmation_token": token}

    def apply_archive(
        self, ctx: PmRequestContext, owner: str, candidate: ArchiveCandidate,
        confirmation_token: str,
    ) -> ArchiveResult:
        if not ctx.client_operation_id or not re.fullmatch(r"[0-9A-Za-z._:-]{8,128}", ctx.client_operation_id):
            raise PmError("invalid_operation", "archive requires a valid client_operation_id")
        journal = self.archive_journal_root / f"{ctx.client_operation_id}.json"
        binding = {
            "operation_id": ctx.client_operation_id, "project_id": ctx.project_id,
            "change_id": ctx.change_id, "revision": ctx.expected_revision,
            "candidate": json.loads(json.dumps(asdict(candidate), sort_keys=True)), "confirmation_token": confirmation_token,
        }
        if journal.exists():
            record = json.loads(journal.read_text(encoding="utf-8"))
            if record.get("binding") != binding:
                raise PmError("invalid_operation", "archive operation id was reused for different input", status=409)
            _require_archive_contained(candidate)
            if record.get("state") == "COMPLETED":
                with change_authority_lock(
                    self.repository.registry, ctx.project_id, ctx.change_id
                ):
                    result = self._validated_completed_archive(
                        ctx, owner, candidate, record
                    )
                    self._release_archived_workspace(ctx, owner, candidate)
                    return result
            with change_authority_lock(
                self.repository.registry, ctx.project_id, ctx.change_id
            ):
                # Ownership is proven before recovery touches any byte.
                self._archive_workspace_record(ctx, owner, candidate)
                target = Path(candidate.target_paths[0]).resolve()
                source_exists = any(Path(item).exists() for item in candidate.source_paths)
                workspace = Path(candidate.controlled_workspace).resolve()
                if _archive_never_started(workspace, target, candidate, record):
                    # Interrupted after the journal but before OpenSpec touched
                    # anything: the prepared journal is stale, so retry afresh
                    # under the same operation id (preview is re-validated below).
                    journal.unlink()
                else:
                    finished = (
                        target.is_dir() and not source_exists
                        and _archive_effects_match(
                            workspace, target, record.get("expected_archive")
                        )
                    )
                    if not finished and not _roll_archive_forward(
                        workspace, target, candidate, record
                    ):
                        raise PmError(
                            "archive_reconcile",
                            "an earlier archive attempt requires reconciliation",
                            status=409,
                        )
                    baseline_paths = tuple(str(item) for item in sorted((workspace / "openspec" / "specs").glob("**/spec.md")))
                    recovered = ArchiveResult(str(target), baseline_paths, {"reconciled": True, "operation_id": ctx.client_operation_id})
                    self._release_archived_workspace(ctx, owner, candidate)
                    _write_json(journal, {**record, "state": "COMPLETED", "result": asdict(recovered)})
                    return recovered
        with change_authority_lock(self.repository.registry, ctx.project_id, ctx.change_id):
            preview = self.preview_archive(ctx, owner)
            if preview["confirmation_token"] != confirmation_token or preview["candidate"] != asdict(candidate):
                raise PmError("confirmation_mismatch", "archive preview is stale", status=409)
            claim = self.repository._claim(ctx.change_id, owner=owner)
            expected_archive = self._simulate_archive(candidate)
            prepared = {
                "state": "PREPARED", "binding": binding,
                "accepted_commits": preview["accepted_commits"],
                "expected_archive": expected_archive,
                "baseline_before": _file_manifest(
                    Path(candidate.controlled_workspace).resolve() / "openspec" / "specs"
                ),
            }
            _require_archive_contained(candidate)
            _write_json(journal, prepared)
            try:
                result = self.openspec.archive(candidate, Path(claim.workspace_path))
                _require_archive_contained(candidate)
                target = Path(candidate.target_paths[0]).resolve()
                if Path(result.archived_path).resolve() != target or not target.exists():
                    raise PmError("archive_failed", "archive destination differs from the reviewed preview", status=409)
                baseline_root = (Path(claim.workspace_path) / "openspec" / "specs").resolve()
                if any(baseline_root not in Path(item).resolve().parents for item in result.baseline_paths):
                    raise PmError("archive_failed", "archive returned a baseline outside the controlled workspace", status=409)
                if not _archive_effects_match(
                    Path(candidate.controlled_workspace).resolve(), target,
                    expected_archive,
                ):
                    raise PmError(
                        "archive_failed",
                        "archive output differs from the prepared transaction",
                        status=409,
                    )
                _write_json(journal, {**prepared, "state": "COMPLETED", "result": asdict(result)})
                self._release_archived_workspace(ctx, owner, candidate)
                return result
            except Exception as exc:
                _write_json(journal, {**prepared, "state": "RECONCILE", "error": getattr(exc, "code", type(exc).__name__)})
                if isinstance(exc, PmError):
                    raise
                raise PmError("archive_reconcile", "archive failed after journal preparation", status=409) from exc

    def _validated_completed_archive(
        self, ctx: PmRequestContext, owner: str, candidate: ArchiveCandidate,
        record: dict[str, Any],
    ) -> ArchiveResult:
        value = record.get("result")
        if not isinstance(value, dict):
            raise PmError(
                "archive_reconcile", "completed archive result is unavailable", status=409
            )
        try:
            result = ArchiveResult(
                str(value["archived_path"]), tuple(value["baseline_paths"]),
                value["output"],
            )
        except (KeyError, TypeError) as exc:
            raise PmError(
                "archive_reconcile", "completed archive result is invalid", status=409
            ) from exc
        target = Path(candidate.target_paths[0]).resolve()
        workspace = Path(candidate.controlled_workspace).resolve()
        if (
            Path(result.archived_path).resolve() != target
            or not target.is_dir()
            or any(Path(item).exists() for item in candidate.source_paths)
            or not _archive_effects_match(
                workspace, target, record.get("expected_archive")
            )
        ):
            raise PmError(
                "archive_reconcile",
                "completed archive bytes or result binding are invalid",
                status=409,
            )
        self._archive_workspace_record(ctx, owner, candidate)
        return result

    def _archive_workspace_record(
        self, ctx: PmRequestContext, owner: str, candidate: ArchiveCandidate,
    ):
        record = self.repository.registry.get_pm_workspace(
            ctx.project_id, ctx.change_id
        )
        if (
            record is None
            or record.owner != owner
            or record.workspace_path.resolve()
            != Path(candidate.controlled_workspace).resolve()
            or record.state not in {"ACTIVE", "RELEASED"}
        ):
            raise PmError(
                "archive_reconcile",
                "archive workspace authority cannot be released",
                status=409,
            )
        return record

    def _release_archived_workspace(
        self, ctx: PmRequestContext, owner: str, candidate: ArchiveCandidate,
    ) -> None:
        record = self._archive_workspace_record(ctx, owner, candidate)
        if record.state == "RELEASED":
            return
        if not self.repository.registry.set_pm_workspace_state(
            ctx.project_id, ctx.change_id, fence=record.fence, state="RELEASED"
        ):
            raise PmError(
                "archive_reconcile", "archive workspace release lost its fence", status=409
            )

    def _simulate_archive(self, candidate: ArchiveCandidate) -> dict[str, Any]:
        workspace = Path(candidate.controlled_workspace).resolve()
        with tempfile.TemporaryDirectory(prefix="symphony-pm-archive-preview-") as raw:
            simulated = Path(raw) / "workspace"
            simulated.mkdir()
            source_openspec = workspace / "openspec"
            if source_openspec.is_dir():
                shutil.copytree(source_openspec, simulated / "openspec")
            def mapped(path: str) -> str:
                resolved = Path(path).resolve()
                try:
                    relative = resolved.relative_to(workspace)
                except ValueError as exc:
                    raise PmError("archive_conflict", "archive path escapes controlled workspace", status=409) from exc
                return str(simulated / relative)
            simulated_candidate = ArchiveCandidate(
                candidate.project_id, candidate.change_id, candidate.revision,
                str(simulated.resolve()),
                tuple(mapped(item) for item in candidate.source_paths),
                tuple(mapped(item) for item in candidate.target_paths),
                candidate.findings,
            )
            result = self.openspec.archive(simulated_candidate, simulated)
            target = Path(simulated_candidate.target_paths[0]).resolve()
            if Path(result.archived_path).resolve() != target or not target.is_dir():
                raise PmError("archive_conflict", "archive simulation did not produce the reviewed target", status=409)
            baseline = simulated / "openspec" / "specs"
            return {
                "archive_manifest": _file_manifest(target),
                "baseline_manifest": _file_manifest(baseline),
                # Exact post-archive baseline text, so recovery can finish an
                # archive whose change directory moved before the baseline
                # was written; each file is re-checked against the manifest.
                "baseline_files": {
                    str(path.relative_to(baseline)): path.read_text(encoding="utf-8")
                    for path in sorted(baseline.rglob("*"))
                    if path.is_file() and not path.is_symlink()
                } if baseline.is_dir() else {},
            }

    def _require(self, flag: str) -> None:
        if not self.flags.get(flag, False):
            raise PmError("capability_disabled", f"PM {flag} capability is disabled", status=503)
        dependency_reasons = self._dependency_reasons(flag)
        if dependency_reasons:
            raise PmError(
                "capability_unavailable",
                f"PM {flag} optional dependencies are unavailable",
                status=503,
                details={
                    "capability": flag,
                    "reasons": dependency_reasons,
                },
            )
        if self.development_verification:
            return
        readiness = self._readiness()
        state = self._capability_state(flag, readiness)
        if state["effective"]:
            return
        raise PmError(
            "capability_not_ready",
            f"PM {flag} requires passed release gates: {', '.join(state['missing_gates'])}",
            status=503,
            details={
                "capability": flag,
                "required_gates": state["required_gates"],
                "missing_gates": state["missing_gates"],
                "reasons": state["gate_reasons"],
            },
        )

    def _readiness(self) -> dict[str, Any]:
        return _release_readiness(
            self.project_root, binding_result=_cached_installed_package_binding()
        )

    @staticmethod
    def _dependency_reasons(flag: str) -> list[str]:
        reasons: list[str] = []
        for package, expected in _CAPABILITY_PACKAGES.get(flag, {}).items():
            try:
                actual = metadata.version(package)
            except metadata.PackageNotFoundError:
                reasons.append(
                    f"Install the pm-exchange extra; {package} {expected} is missing"
                )
            else:
                if actual != expected:
                    reasons.append(
                        f"Install the pm-exchange extra; {package} {actual} does not match required {expected}"
                    )
        return reasons

    def _capability_state(
        self, flag: str, readiness: dict[str, Any]
    ) -> dict[str, Any]:
        requested = bool(self.flags.get(flag, False))
        required = list(_CAPABILITY_GATES.get(flag, ()))
        missing = [
            gate_id for gate_id in required
            if not readiness["gates"][gate_id]["passed"]
        ]
        gate_reasons = {
            gate_id: list(readiness["gates"][gate_id]["reasons"])
            for gate_id in missing
        }
        dependency_reasons = self._dependency_reasons(flag)
        effective = requested and not dependency_reasons and (
            self.development_verification or not missing
        )
        reasons: list[str] = []
        if not requested:
            reasons.append(
                "Off in the demo: it would contact an AI model."
                if self.demo and flag == "assistance"
                else f"PM {flag} is disabled by host configuration"
            )
        elif self.development_verification and not dependency_reasons:
            reasons.append(
                "Enabled only in the host development verification context; "
                "release gates remain unchanged"
            )
        if requested and dependency_reasons:
            reasons.extend(dependency_reasons)
        elif requested and missing:
            reasons.append(
                f"Requires passed release gates: {', '.join(missing)}"
            )
            for gate_id in missing:
                reasons.extend(
                    f"{gate_id}: {reason}" for reason in gate_reasons[gate_id]
                )
        return {
            "requested": requested,
            "effective": effective,
            "required_gates": required,
            "missing_gates": missing,
            "reasons": reasons,
            "gate_reasons": gate_reasons,
            "dependency_reasons": dependency_reasons,
            "development_verification": bool(
                requested and self.development_verification
            ),
        }


def _file_manifest(root: Path) -> dict[str, str]:
    if not root.is_dir() or root.is_symlink():
        return {}
    return {
        str(path.relative_to(root)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _require_archive_contained(candidate: ArchiveCandidate) -> None:
    """Refuse an archive whose target or baseline escapes the workspace.

    Checked on the unresolved paths: the target itself, the change directory
    root and the baseline root may not be symlinks, and their real parents
    must stay inside the controlled workspace. Equal bytes elsewhere are not
    containment.
    """
    root = Path(candidate.controlled_workspace).resolve()

    def inside(path: Path) -> bool:
        real = Path(os.path.realpath(path))
        return real == root or root in real.parents

    target = Path(candidate.target_paths[0])
    # The change directory each listed source sits in (openspec/changes/<name>),
    # taken from the unresolved path so an alias symlink is still visible.
    change_dirs = {
        parent for item in candidate.source_paths for parent in Path(item).parents
        if parent.parent.name == "changes" and parent.parent.parent.name == "openspec"
    }
    for path in (
        target,
        target.parent,
        root / "openspec",
        root / "openspec" / "changes",
        root / "openspec" / "specs",
        *sorted(change_dirs),
    ):
        if path.is_symlink() or not inside(path.parent if path is target else path):
            raise PmError(
                "archive_reconcile",
                "archive paths leave the controlled planning workspace",
                status=409,
            )


def _archive_never_started(
    workspace: Path, target: Path, candidate: ArchiveCandidate,
    record: dict[str, Any],
) -> bool:
    before = record.get("baseline_before")
    return (
        record.get("state") in {"PREPARED", "RECONCILE"}
        and isinstance(before, dict)
        and all(Path(item).exists() for item in candidate.source_paths)
        and not target.exists()
        and _file_manifest(workspace / "openspec" / "specs") == before
    )


def _no_symlink_between(root: Path, path: Path) -> bool:
    """True when no component from ``root`` down to ``path`` is a symlink."""
    current = root
    if current.is_symlink():
        return False
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    for part in parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _roll_archive_forward(
    workspace: Path, target: Path, candidate: ArchiveCandidate,
    record: dict[str, Any],
) -> bool:
    """Finish a prepared archive that OpenSpec left part-way.

    OpenSpec writes the merged baseline first and moves the change directory
    last, so an interruption can leave any prefix of that sequence. Recovery
    only completes the prepared transaction: baseline files that are missing
    or still byte-equal to the pre-archive baseline are written from the
    journal (checked against the prepared manifest), and a change directory
    whose bytes equal the prepared archive is moved into place. Any other
    content, a symlink on the path, or an unexpected layout stays with
    explicit reconciliation. Callers hold the change authority lock.
    """
    expected = record.get("expected_archive")
    before = record.get("baseline_before")
    if (
        not isinstance(expected, dict) or not isinstance(before, dict)
        or record.get("state") not in {"PREPARED", "RECONCILE"}
    ):
        return False
    files = expected.get("baseline_files")
    manifest = expected.get("baseline_manifest")
    archive_manifest = expected.get("archive_manifest")
    if (
        not isinstance(files, dict) or not isinstance(manifest, dict)
        or not isinstance(archive_manifest, dict) or not archive_manifest
        or set(files) != set(manifest)
    ):
        return False
    for relative, text in files.items():
        if not isinstance(text, str) or manifest[relative] != "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest():
            return False
    root = workspace.resolve()
    specs = workspace / "openspec" / "specs"
    if not _no_symlink_between(root, specs):
        return False
    # Resolve only to normalise the path spelling; a symlinked change
    # directory resolves outside openspec/changes and is refused below.
    sources = [Path(item).resolve() for item in candidate.source_paths]
    changes = workspace / "openspec" / "changes"
    # The change directory is the single child of openspec/changes that holds
    # every source file the preview listed.
    source_dirs = set()
    for item in sources:
        try:
            source_dirs.add(changes / item.relative_to(changes).parts[0])
        except (ValueError, IndexError):
            return False
    if target.is_dir() and not any(item.exists() for item in sources):
        if _file_manifest(target) != archive_manifest:
            return False
        move_from: Path | None = None
    elif len(source_dirs) == 1:
        source_dir = next(iter(source_dirs))
        listed = {str(item.relative_to(source_dir)) for item in sources}
        if (
            target.exists() or not source_dir.is_dir()
            or not _no_symlink_between(root, source_dir)
            or not _no_symlink_between(root, target.parent)
            or set(_file_manifest(source_dir)) != listed
            or _file_manifest(source_dir) != archive_manifest
        ):
            return False
        move_from = source_dir
    else:
        return False
    current = _file_manifest(specs)
    # Only fill files the interrupted archive had not reached yet: missing,
    # or still byte-equal to the pre-archive baseline. Any other content was
    # written by someone else and is never overwritten.
    if any(
        name not in files and current[name] != before.get(name)
        for name in current
    ) or any(
        name in current and current[name] not in {before.get(name), manifest[name]}
        for name in files
    ):
        return False
    for relative in files:
        destination = specs / relative
        if root not in destination.resolve().parents or not _no_symlink_between(root, destination):
            return False
    for relative, text in files.items():
        if current.get(relative) == manifest[relative]:
            continue
        destination = specs / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination, text)
    if move_from is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(move_from, target)
    return _archive_effects_match(workspace, target, expected)


def _archive_effects_match(
    workspace: Path, target: Path, expected: Any
) -> bool:
    if not isinstance(expected, dict):
        return False
    archive_manifest = expected.get("archive_manifest")
    baseline_manifest = expected.get("baseline_manifest")
    return (
        isinstance(archive_manifest, dict)
        and isinstance(baseline_manifest, dict)
        and bool(archive_manifest)
        and _file_manifest(target) == archive_manifest
        and _file_manifest(workspace / "openspec" / "specs") == baseline_manifest
    )


def _installed_package_binding() -> tuple[dict[str, str] | None, str | None]:
    try:
        owned: dict[str, str] = {}
        version: str | None = None
        for name in _BOUND_DISTRIBUTIONS:
            distribution = metadata.distribution(name)
            version = version or distribution.version
            record = next(
                item for item in (distribution.files or ())
                if str(item).endswith(".dist-info/RECORD")
            )
            record_path = Path(str(distribution.locate_file(record)))
            site_root = Path(str(distribution.locate_file(""))).resolve()
            distribution_owned = 0
            metadata_verified = False
            with record_path.open(newline="", encoding="utf-8") as handle:
                for relative, encoded_hash, encoded_size in csv.reader(handle):
                    is_metadata = relative.endswith(".dist-info/METADATA")
                    if not relative.startswith("symphony/") and not is_metadata:
                        continue
                    candidate = Path(str(distribution.locate_file(relative))).resolve(strict=True)
                    candidate.relative_to(site_root)
                    if not encoded_hash.startswith("sha256="):
                        continue
                    payload = candidate.read_bytes()
                    expected = base64.urlsafe_b64decode(encoded_hash[7:] + "==")
                    actual = hashlib.sha256(payload).digest()
                    if actual != expected or (encoded_size and len(payload) != int(encoded_size)):
                        raise ValueError(f"installed package file differs from RECORD: {relative}")
                    owned.setdefault(relative, hashlib.sha256(payload).hexdigest())
                    distribution_owned += 1
                    metadata_verified = metadata_verified or is_metadata
            if not distribution_owned:
                raise ValueError("installed distribution RECORD has no owned package files")
            if not metadata_verified:
                raise ValueError("installed distribution RECORD has no METADATA binding")
        digest = hashlib.sha256(
            json.dumps(list(owned.items()), separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "package_version": str(version),
            "package_digest": "sha256:" + digest,
            "verified_file_count": str(len(owned)),
        }, None
    except (metadata.PackageNotFoundError, StopIteration, OSError, ValueError) as exc:
        return None, f"installed package binding is unavailable: {type(exc).__name__}: {exc}"


def _installed_package_fingerprint() -> tuple[
    tuple[str, int, int, int, int, int, int], ...
] | None:
    """Cheaply detect changes that require a full installed-package rehash."""
    if os.name == "nt":
        # Windows ctime is creation time, so a same-size edit with restored
        # mtime cannot be detected safely from file metadata alone.
        return None
    try:
        rows: list[tuple[str, int, int, int, int, int, int]] = []
        for name in _BOUND_DISTRIBUTIONS:
            distribution = metadata.distribution(name)
            record = next(
                item for item in (distribution.files or ())
                if str(item).endswith(".dist-info/RECORD")
            )
            metadata_file = next(
                item for item in (distribution.files or ())
                if str(item).endswith(".dist-info/METADATA")
            )
            site_root = Path(str(distribution.locate_file(""))).resolve()
            paths = [record, metadata_file, *(
                item for item in (distribution.files or ())
                if str(item).startswith("symphony/")
            )]
            for relative in paths:
                candidate = Path(str(distribution.locate_file(relative))).resolve(strict=True)
                candidate.relative_to(site_root)
                stat = candidate.stat()
                rows.append((
                    str(relative), stat.st_dev, stat.st_ino, stat.st_mode,
                    stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                ))
        return tuple(rows)
    except (metadata.PackageNotFoundError, StopIteration, OSError, ValueError):
        return None


def _cached_installed_package_binding() -> tuple[
    dict[str, str] | None, str | None
]:
    """Reuse a verified binding only while every owned file is unchanged."""
    global _PACKAGE_BINDING_CACHE
    fingerprint = _installed_package_fingerprint()
    if fingerprint is None:
        return _installed_package_binding()
    with _PACKAGE_BINDING_CACHE_LOCK:
        current = _installed_package_fingerprint()
        if current is None:
            return _installed_package_binding()
        if _PACKAGE_BINDING_CACHE is not None:
            cached_fingerprint, cached_result = _PACKAGE_BINDING_CACHE
            if current == cached_fingerprint:
                return cached_result
        result = _installed_package_binding()
        after = _installed_package_fingerprint()
        if after is not None and current == after:
            _PACKAGE_BINDING_CACHE = (after, result)
        else:
            # A concurrent mutation crossed the hash window. Rehash once more
            # and cache only if the package is stable around that verification.
            result = _installed_package_binding()
            final = _installed_package_fingerprint()
            if after is not None and final == after:
                _PACKAGE_BINDING_CACHE = (after, result)
            else:
                _PACKAGE_BINDING_CACHE = None
                result = (None, "installed package changed during verification")
        return result


def _release_readiness(
    project_root: Path,
    *,
    binding_result: tuple[dict[str, str] | None, str | None] | None = None,
) -> dict[str, Any]:
    gate_ids = ("G0", "G1", "G2", "G3", "G4")
    binding, binding_error = binding_result or _installed_package_binding()
    receipt_path = project_root / ".symphony" / "pm" / "release-evidence.json"
    common_reason = binding_error
    receipt: dict[str, Any] | None = None
    if common_reason is None:
        try:
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError("unsupported receipt schema")
            if value.get("binding") != binding:
                raise ValueError("receipt does not match the installed package")
            receipt = value
        except FileNotFoundError:
            common_reason = "host release evidence has not been evaluated for this installed package"
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            common_reason = f"host release evidence is unavailable: {exc}"

    verified_receipts, receipt_errors = _verified_release_receipts(
        project_root, receipt, binding
    )
    gates: dict[str, dict[str, Any]] = {}
    rows = receipt.get("gates", {}) if receipt is not None else {}
    if not isinstance(rows, dict):
        rows = {}
        common_reason = "host release evidence gate records are invalid"
    for gate_id in gate_ids:
        row = rows.get(gate_id)
        gates[gate_id] = _gate_evidence(
            gate_id, row, binding, common_reason,
            verified_receipts, receipt_errors,
        )

    integration = _gate_evidence(
        "integration",
        receipt.get("integration") if receipt is not None else None,
        binding,
        common_reason,
        verified_receipts,
        receipt_errors,
    )
    prerequisites = [gate_id for gate_id in gate_ids if not gates[gate_id]["passed"]]
    if prerequisites:
        g5_reason = "G5 requires passed " + ", ".join(prerequisites)
        g5_status = "failed" if any(gates[item]["status"] == "failed" for item in prerequisites) else "not_evaluated"
    elif not integration["passed"]:
        g5_reason = "G5 requires a passing final integration and full-suite receipt"
        g5_status = "failed" if integration["status"] == "failed" else "not_evaluated"
    else:
        g5_reason = None
        g5_status = "passed"
    gates["G5"] = {
        "status": g5_status,
        "passed": g5_status == "passed",
        "reasons": [] if g5_reason is None else [g5_reason],
        "evidence_ids": integration["evidence_ids"] if g5_status == "passed" else [],
        "evaluated_at": integration["evaluated_at"] if g5_status == "passed" else None,
        "source_digest": binding["package_digest"] if binding and g5_status == "passed" else None,
        "derived": True,
    }
    highest = next(
        (gate_id for gate_id in reversed((*gate_ids, "G5")) if gates[gate_id]["passed"]),
        None,
    )
    return {
        "gates": gates,
        "highest_passed_gate": highest,
        "evidence": {
            "status": "bound" if receipt is not None else "not_evaluated",
            "binding": binding,
            "reason": common_reason,
            "receipt": ".symphony/pm/release-evidence.json",
        },
    }


def _gate_evidence(
    gate_id: str,
    row: Any,
    binding: dict[str, str] | None,
    common_reason: str | None,
    verified_receipts: dict[str, dict[str, Any]],
    receipt_errors: dict[str, str],
) -> dict[str, Any]:
    if common_reason is not None or binding is None:
        reason = common_reason or "installed package binding is unavailable"
        return {"status": "not_evaluated", "passed": False, "reasons": [reason], "evidence_ids": [], "evaluated_at": None, "source_digest": None}
    if not isinstance(row, dict):
        return {"status": "not_evaluated", "passed": False, "reasons": [f"{gate_id} has no host evidence receipt"], "evidence_ids": [], "evaluated_at": None, "source_digest": None}
    status = str(row.get("status") or "")
    evidence_ids = row.get("evidence_ids")
    evaluated_at = row.get("evaluated_at")
    source_digest = row.get("source_digest")
    reasons = row.get("reasons") or []
    valid = (
        status in {"passed", "failed"}
        and isinstance(evidence_ids, list)
        and all(isinstance(item, str) and item for item in evidence_ids)
        and bool(evidence_ids)
        and isinstance(evaluated_at, str) and bool(evaluated_at)
        and source_digest == binding["package_digest"]
        and isinstance(reasons, list) and all(isinstance(item, str) for item in reasons)
        and _valid_timestamp(evaluated_at)
    )
    if not valid:
        return {"status": "not_evaluated", "passed": False, "reasons": [f"{gate_id} evidence is incomplete or bound to different source"], "evidence_ids": [], "evaluated_at": None, "source_digest": None}
    assert isinstance(evidence_ids, list)
    invalid_ids = [
        item for item in evidence_ids
        if item not in verified_receipts
        or verified_receipts[item].get("source_digest") != binding["package_digest"]
        or (status == "passed" and not verified_receipts[item].get("passed"))
    ]
    if invalid_ids:
        detail = "; ".join(receipt_errors.get(item, f"unverified receipt {item}") for item in invalid_ids)
        return {"status": "not_evaluated", "passed": False, "reasons": [detail], "evidence_ids": [], "evaluated_at": None, "source_digest": None}
    return {"status": status, "passed": status == "passed", "reasons": reasons, "evidence_ids": evidence_ids, "evaluated_at": evaluated_at, "source_digest": source_digest}


def _verified_release_receipts(
    project_root: Path,
    release: dict[str, Any] | None,
    binding: dict[str, str] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    verified: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    if release is None or binding is None:
        return verified, errors
    rows = release.get("receipts")
    if not isinstance(rows, dict):
        return verified, {"*": "release evidence has no receipt inventory"}
    receipt_root = (project_root / ".symphony" / "pm" / "release-receipts").resolve()
    for receipt_id, descriptor in rows.items():
        try:
            if not isinstance(receipt_id, str) or not isinstance(descriptor, dict):
                raise ValueError("invalid receipt descriptor")
            name = descriptor.get("file")
            expected_hash = descriptor.get("sha256")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+\.json", name):
                raise ValueError("invalid receipt file name")
            path = (receipt_root / name).resolve(strict=True)
            path.relative_to(receipt_root)
            if path.is_symlink():
                raise ValueError("receipt is a symlink")
            raw = path.read_bytes()
            if expected_hash != "sha256:" + hashlib.sha256(raw).hexdigest():
                raise ValueError("receipt hash mismatch")
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError("invalid receipt schema")
            completed_at = value.get("completed_at")
            if (
                value.get("source_digest") != binding["package_digest"]
                or not _valid_timestamp(completed_at)
                or not isinstance(value.get("command"), list)
                or not value.get("command")
                or not all(isinstance(item, str) and item for item in value["command"])
                or not isinstance(value.get("return_code"), int)
                or value.get("result") not in {"passed", "failed"}
            ):
                raise ValueError("receipt provenance or command result is invalid")
            passed = value["result"] == "passed" and value["return_code"] == 0
            verified[receipt_id] = {
                "passed": passed,
                "source_digest": value["source_digest"],
                "completed_at": completed_at,
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors[str(receipt_id)] = f"receipt {receipt_id} is unavailable: {exc}"
    return verified, errors


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _runtime_health(repository: PmDocumentRepository, journal_root: Path) -> dict[str, Any]:
    health: dict[str, Any] = {}
    try:
        required_tables = {"pm_manual_verification_records", "pm_coverage_waivers", "pm_planning_reserved_ids"}
        present = {
            str(row["name"]) for row in repository.registry._connect().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing_tables = sorted(required_tables - present)
        health["registry"] = {
            "status": "healthy" if repository.registry.schema_is_current() and not missing_tables else "incomplete",
            "schema_version": repository.registry.schema_version(),
            "missing_tables": missing_tables,
        }
        rows = repository.registry._connect().execute(
            "SELECT state, COUNT(*) AS count FROM pm_workspaces WHERE project_id = ? GROUP BY state",
            (repository.project_id,),
        ).fetchall()
        health["workspaces"] = {
            "status": "healthy",
            "states": {str(row["state"]): int(row["count"]) for row in rows},
        }
    except Exception as exc:
        health["registry"] = {"status": "unavailable", "reason": type(exc).__name__}
        health["workspaces"] = {"status": "unavailable", "reason": "registry unavailable"}
    try:
        if journal_root.exists() and not journal_root.is_dir():
            raise OSError("journal root is not a directory")
        health["journals"] = {
            "status": "healthy" if journal_root.is_dir() else "not_initialized",
            "reconcile_count": len(list(journal_root.glob("*.json"))) if journal_root.is_dir() else 0,
        }
    except OSError as exc:
        health["journals"] = {"status": "unavailable", "reason": str(exc)}
    return health


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
