"""Host-executed, revision-bound PM verification receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast, Literal

from symphony.artifacts import ArtifactStore
from symphony.orchestrator.run_registry import RunRegistry
from .models import ManualEvidencePreview, PmError, PmFinding, VerificationReceipt
from .models import AcceptancePreview, AcceptanceRecord, canonical_receipt_set_hash
from .locks import change_authority_lock


def successful_run_provenance(
    run: object, successful_terminal_states: frozenset[str]
) -> bool:
    """Recognize real and legacy successful runs without admitting failed exits."""
    status = str(getattr(run, "status", "")).strip().casefold()
    state = str(getattr(run, "state", "")).strip().casefold()
    commit_sha = getattr(run, "commit_sha", None)
    return (
        status in {"normal", "completed"}
        and state in successful_terminal_states
        and isinstance(commit_sha, str)
        and bool(commit_sha)
        and not getattr(run, "failure_class", None)
        and not getattr(run, "failure_message", None)
    )


class PmVerificationService:
    def __init__(
        self,
        registry: RunRegistry,
        procedures: dict[str, tuple[str, ...]],
        *,
        artifacts: ArtifactStore | None = None,
        manual_procedures: dict[str, Any] | None = None,
        project_root: Path | None = None,
        timeout_seconds: float = 600,
        successful_terminal_states: tuple[str, ...] = ("Done",),
    ) -> None:
        self.registry = registry
        self.procedures = dict(procedures)
        self.artifacts = artifacts
        self.manual_procedures = dict(manual_procedures or {})
        self.project_root = project_root.resolve() if project_root is not None else None
        self.timeout_seconds = timeout_seconds
        self.successful_terminal_states = frozenset(
            state.strip().casefold()
            for state in successful_terminal_states
            if state.strip()
        )

    def run_procedure(
        self, *, operation_id: str, project_id: str,
        ticket_identifier: str, task_revision: str, procedure_id: str,
        run_id: str,
        artifact_refs: tuple[str, ...] = (),
    ) -> VerificationReceipt:
        command = self.procedures.get(procedure_id)
        if not command:
            raise PmError("unknown_procedure", "verification procedure is not preapproved")
        mapping = self.registry.get_pm_managed_ticket(project_id, ticket_identifier)
        if mapping is None or mapping.task_revision != task_revision:
            raise PmError("stale_task_revision", "verification task revision is not current", status=409)
        try:
            run = self.registry.get_run(run_id)
        except KeyError as exc:
            raise PmError("run_not_found", "verification run is unknown", status=404) from exc
        if run.identifier != ticket_identifier or not successful_run_provenance(
            run, self.successful_terminal_states
        ):
            raise PmError(
                "run_provenance_mismatch",
                "verification run is not a successful terminal matching ticket run",
                status=409,
            )
        tested_commit = run.commit_sha
        if not isinstance(tested_commit, str):
            raise PmError(
                "run_provenance_mismatch",
                "verification run has no tested commit",
                status=409,
            )
        source_workspace = run.workspace_path.resolve()
        required = self.registry.get_pm_required_procedures(project_id, ticket_identifier)
        digest = "sha256:" + hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode()
        ).hexdigest()
        policy = _procedure_policy(required.get(procedure_id), procedure_id)
        receipt_digest = digest
        if required:
            if policy is None or policy["kind"] != "native":
                raise PmError("unapproved_procedure", "procedure is not bound to the approved native task snapshot", status=409)
            if policy.get("legacy"):
                if policy["definition_digest"] != digest:
                    raise PmError("unapproved_procedure", "procedure command changed after approval", status=409)
            else:
                if tuple(policy.get("argv", ())) != command:
                    raise PmError("unapproved_procedure", "procedure command changed after approval", status=409)
                receipt_digest = str(policy["definition_digest"])
        self._verify_artifacts(ticket_identifier, run_id, artifact_refs)
        started = datetime.now(timezone.utc).isoformat()
        timed_out = False
        checkout_changed = True
        with tempfile.TemporaryDirectory(prefix="symphony-pm-verify-") as root:
            checkout = Path(root) / "checkout"
            _git_clone(source_workspace, checkout)
            _git(checkout, "checkout", "--detach", tested_commit)
            if _git(checkout, "rev-parse", "HEAD") != tested_commit:
                raise PmError("tested_commit_missing", "isolated checkout does not match tested commit", status=409)
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    list(command), cwd=checkout, stdout=stdout_file, stderr=stderr_file,
                    start_new_session=os.name == "posix",
                )
                try:
                    process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate(process)
                stdout_hash = _hash_file(stdout_file)
                stderr_hash = _hash_file(stderr_file)
            checkout_changed = (
                _git(checkout, "rev-parse", "HEAD") != tested_commit
                or bool(_git(checkout, "status", "--porcelain=v1", "--untracked-files=all"))
            )
        completed = datetime.now(timezone.utc).isoformat()
        result = "passed" if process.returncode == 0 and not timed_out and not checkout_changed else "failed"
        receipt = VerificationReceipt(
            uuid.uuid4().hex, operation_id, run_id, project_id, ticket_identifier,
            task_revision, procedure_id, receipt_digest, tested_commit,
            process.returncode, timed_out, False, stdout_hash, stderr_hash,
            started, completed, artifact_refs, result,
            definition_digest=receipt_digest,
        )
        self.registry.record_pm_verification(
            receipt_id=receipt.receipt_id, project_id=project_id,
            ticket_identifier=ticket_identifier, task_revision=task_revision,
            operation_id=operation_id, run_id=run_id, procedure_id=procedure_id,
            command_digest=receipt_digest, tested_commit=tested_commit,
            return_code=receipt.return_code, timed_out=timed_out, cancelled=False,
            stdout_hash=receipt.stdout_hash, stderr_hash=receipt.stderr_hash,
            started_at=started, completed_at=completed,
            artifact_refs=artifact_refs, result=result,
        )
        return receipt

    def preview_acceptance(
        self, *, project_id: str, ticket_identifier: str,
        task_revision: str, tested_commit: str,
    ) -> AcceptancePreview:
        mapping = self.registry.get_pm_managed_ticket(project_id, ticket_identifier)
        if mapping is None or mapping.task_revision != task_revision:
            raise PmError("stale_task_revision", "acceptance task revision is not current", status=409)
        required = self.registry.get_pm_required_procedures(project_id, ticket_identifier)
        rows = self.registry.list_pm_verification_receipts(
            project_id, ticket_identifier, task_revision, tested_commit
        )
        passing: dict[str, str] = {}
        for row in rows:
            procedure_id = str(row["procedure_id"])
            if _receipt_qualifies(
                row, _procedure_policy(required.get(procedure_id), procedure_id)
            ):
                passing[procedure_id] = str(row["receipt_id"])
        missing = tuple(sorted(set(required) - set(passing)))
        findings: list[PmFinding] = []
        if not required:
            findings.append(PmFinding("verification_policy_missing", "approved task has no required verification procedures", severity="blocking"))
        findings.extend(
            PmFinding("required_procedure_missing", f"required procedure {item} has no passing receipt", severity="blocking")
            for item in missing
        )
        receipt_ids = tuple(passing[item] for item in sorted(passing))
        token = _acceptance_token(
            project_id, ticket_identifier, task_revision, tested_commit,
            tuple(sorted(required)), receipt_ids,
        )
        return AcceptancePreview(
            project_id, ticket_identifier, task_revision, tested_commit,
            tuple(sorted(required)), receipt_ids, missing, not findings, token,
            tuple(findings),
        )

    def accept(
        self, *, preview: AcceptancePreview, confirmation_token: str,
        reviewer_label: str, complete_ticket: Callable[[], None] | None = None,
    ) -> AcceptanceRecord:
        mapping = self.registry.get_pm_managed_ticket(preview.project_id, preview.ticket_identifier)
        if mapping is None:
            raise PmError("stale_task_revision", "managed ticket is unavailable", status=409)
        with change_authority_lock(self.registry, preview.project_id, mapping.change_id):
            if (
                self.registry.has_active_lease(preview.ticket_identifier)
                or self.registry.has_active_pm_dispatch(
                    preview.project_id, preview.ticket_identifier
                )
                or self.registry.has_unresolved_backend_ownership(
                    preview.ticket_identifier
                )
            ):
                raise PmError("native_attempt_active", "stop the active native attempt before accepting manual completion", status=409)
            current = self.preview_acceptance(
                project_id=preview.project_id,
                ticket_identifier=preview.ticket_identifier,
                task_revision=preview.task_revision,
                tested_commit=preview.tested_commit,
            )
            if not current.ready:
                raise PmError("acceptance_not_ready", "required verification is incomplete", status=409)
            if current != preview or confirmation_token != current.confirmation_token:
                raise PmError("confirmation_mismatch", "acceptance preview is stale", status=409)
            accepted_at = datetime.now(timezone.utc).isoformat()
            acceptance_id = uuid.uuid4().hex
            receipt_set_hash = canonical_receipt_set_hash(current.passing_receipt_ids)
            self.registry.record_pm_acceptance(
                acceptance_id=acceptance_id, project_id=current.project_id,
                ticket_identifier=current.ticket_identifier,
                task_revision=current.task_revision, tested_commit=current.tested_commit,
                receipt_ids=current.passing_receipt_ids,
                receipt_set_hash=receipt_set_hash,
                accepted_by=reviewer_label.strip() or "local operator",
                accepted_at=accepted_at,
            )
            if complete_ticket is not None:
                complete_ticket()
        return AcceptanceRecord(
            acceptance_id, current.project_id, current.ticket_identifier,
            current.task_revision, current.tested_commit,
            current.passing_receipt_ids,
            reviewer_label.strip() or "local operator", accepted_at,
        )

    def preview_manual_evidence(self, payload: dict[str, Any]) -> ManualEvidencePreview:
        project_id = str(payload.get("project_id") or "")
        change_id = str(payload.get("change_id") or "")
        ticket = str(payload.get("ticket_identifier") or "")
        revision = str(payload.get("task_revision") or "")
        procedure_id = str(payload.get("procedure_id") or "")
        commit = str(payload.get("tested_commit") or "")
        mapping = self.registry.get_pm_managed_ticket(project_id, ticket)
        findings: list[PmFinding] = []
        if mapping is None or mapping.change_id != change_id or mapping.task_revision != revision:
            findings.append(PmFinding("stale_task_revision", "manual evidence does not match current managed scope", severity="blocking"))
        policy = _procedure_policy(
            self.registry.get_pm_required_procedures(project_id, ticket).get(procedure_id),
            procedure_id,
        )
        if policy is None:
            findings.append(PmFinding("unknown_procedure", "procedure is not approved for this task", severity="blocking"))
        commit_exists = False
        if re.fullmatch(r"[0-9a-f]{40}", commit) and self.project_root is not None:
            try:
                commit_exists = subprocess.run(
                    ["git", "-C", str(self.project_root), "cat-file", "-e", f"{commit}^{{commit}}"],
                    capture_output=True, check=False, timeout=self.timeout_seconds,
                ).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        if not commit_exists:
            findings.append(PmFinding("tested_commit_missing", "tested commit is unavailable", severity="blocking"))
        report_ref = None
        artifacts: list[dict[str, Any]] = []
        if policy and policy["kind"] == "manual":
            if payload.get("result") not in {"passed", "failed"}:
                findings.append(PmFinding("invalid_result", "manual result must be passed or failed", severity="blocking"))
            try:
                report_ref = self._resolve_ref(ticket, commit, payload.get("report_ref"), report=True)
                artifacts = [self._resolve_ref(ticket, commit, item, report=False) for item in payload.get("artifact_refs", [])]
            except PmError as exc:
                findings.append(PmFinding(exc.code, exc.message, severity="blocking"))
            if policy.get("artifacts") == "required" and not artifacts:
                findings.append(PmFinding("artifact_required", "manual procedure requires artifact evidence", severity="blocking"))
        normalized = {
            "project_id": project_id, "change_id": change_id,
            "ticket_identifier": ticket, "task_id": mapping.task_id if mapping else "",
            "task_revision": revision, "tested_commit": commit,
            "procedure_id": procedure_id, "procedure_kind": policy["kind"] if policy else "",
            "definition_digest": policy["definition_digest"] if policy else "",
            "environment_summary": str(payload.get("environment_summary") or "")[:4096],
            "started_at": str(payload.get("started_at") or ""),
            "completed_at": str(payload.get("completed_at") or ""),
            "result": str(payload.get("result") or ""), "report_ref": report_ref,
            "artifact_refs": artifacts,
            "submitted_by": str(payload.get("submitted_by") or "local operator")[:128],
        }
        token = "sha256:" + hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ManualEvidencePreview(not findings, token, tuple(findings), "manual", normalized)

    def confirm_manual_evidence(
        self, preview: ManualEvidencePreview, confirmation_token: str, operation_id: str
    ) -> tuple[VerificationReceipt, bool]:
        current = self.preview_manual_evidence(dict(preview.normalized))
        if (
            not preview.ready
            or not current.ready
            or current.confirmation_token != preview.confirmation_token
            or confirmation_token != current.confirmation_token
        ):
            raise PmError("confirmation_mismatch", "manual evidence preview is stale", status=409)
        value = dict(current.normalized)
        kind = str(value["procedure_kind"])
        return_code: int | None = None
        timed_out = False
        empty_hash = "sha256:" + hashlib.sha256(b"").hexdigest()
        stdout_hash = stderr_hash = empty_hash
        completed_at = str(value["completed_at"])
        result = str(value["result"])
        if kind == "native":
            command = self.procedures.get(str(value["procedure_id"]))
            if command is None or self.project_root is None:
                raise PmError("unknown_procedure", "native procedure is unavailable", status=409)
            return_code, timed_out, stdout_hash, stderr_hash, changed = self._execute_at_commit(
                self.project_root, str(value["tested_commit"]), command
            )
            result = "passed" if return_code == 0 and not timed_out and not changed else "failed"
            completed_at = datetime.now(timezone.utc).isoformat()
        input_hash = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        submitted_at = datetime.now(timezone.utc).isoformat()
        record = {
            "receipt_id": uuid.uuid5(uuid.NAMESPACE_URL, f"symphony-manual:{operation_id}").hex,
            "operation_id": operation_id, "project_id": value["project_id"],
            "change_id": value["change_id"], "ticket_identifier": value["ticket_identifier"],
            "task_id": value["task_id"], "task_revision": value["task_revision"],
            "procedure_id": value["procedure_id"], "procedure_kind": kind,
            "definition_digest": value["definition_digest"], "tested_commit": value["tested_commit"],
            "environment_summary": value["environment_summary"], "return_code": return_code,
            "timed_out": int(timed_out), "cancelled": 0, "stdout_hash": stdout_hash,
            "stderr_hash": stderr_hash, "started_at": value["started_at"],
            "completed_at": completed_at, "submitted_at": submitted_at,
            "submitted_by": value["submitted_by"],
            "report_ref_json": json.dumps(value["report_ref"], sort_keys=True) if value["report_ref"] else None,
            "artifact_refs_json": json.dumps(value["artifact_refs"], sort_keys=True),
            "result": result, "input_hash": input_hash,
        }
        try:
            row, created = self.registry.record_pm_manual_verification(record)
        except ValueError as exc:
            raise PmError("operation_conflict", str(exc), status=409) from exc
        return _manual_receipt(row), created

    def _execute_at_commit(
        self, source: Path, commit: str, command: tuple[str, ...]
    ) -> tuple[int | None, bool, str, str, bool]:
        timed_out = False
        with tempfile.TemporaryDirectory(prefix="symphony-pm-manual-verify-") as root:
            checkout = Path(root) / "checkout"
            _git_clone(source, checkout)
            _git(checkout, "checkout", "--detach", commit)
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(list(command), cwd=checkout, stdout=stdout_file, stderr=stderr_file, start_new_session=os.name == "posix")
                try:
                    process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate(process)
                stdout_hash, stderr_hash = _hash_file(stdout_file), _hash_file(stderr_file)
            changed = bool(_git(checkout, "status", "--porcelain=v1", "--untracked-files=all"))
        return process.returncode, timed_out, stdout_hash, stderr_hash, changed

    def _resolve_ref(
        self, ticket: str, commit: str, value: Any, *, report: bool
    ) -> dict[str, Any]:
        allowed_keys = {"ref_kind", "ref", "media_kind", "sha256", "size_bytes", "download_url"}
        if (
            not isinstance(value, dict)
            or not {"ref_kind", "ref"} <= set(value)
            or set(value) - allowed_keys
        ):
            raise PmError("invalid_evidence_ref", "immutable evidence reference is required", status=409)
        kind, ref = value.get("ref_kind"), str(value.get("ref") or "")
        if kind == "git_blob":
            if self.project_root is None or not ref or ref.startswith("/") or ".." in Path(ref).parts:
                raise PmError("invalid_evidence_ref", "Git evidence path is unsafe", status=409)
            try:
                completed = subprocess.run(
                    ["git", "-C", str(self.project_root), "show", f"{commit}:{ref}"],
                    capture_output=True, check=False, timeout=self.timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PmError("invalid_evidence_ref", "Git evidence blob is unavailable", status=409) from exc
            if completed.returncode != 0 or len(completed.stdout) > 25 * 1024 * 1024:
                raise PmError("invalid_evidence_ref", "Git evidence blob is missing or oversized", status=409)
            data = completed.stdout
        elif kind == "artifact_store" and not report:
            prefix = f"artifact://{ticket}/"
            if self.artifacts is None or not ref.startswith(prefix):
                raise PmError("invalid_evidence_ref", "artifact does not belong to the ticket", status=409)
            name = ref.removeprefix(prefix)
            path, record = self.artifacts.resolve_file(ticket, name), self.artifacts.record_for(ticket, name)
            if path is None or record is None or _sha256_path(path) != record.sha256:
                raise PmError("invalid_evidence_ref", "artifact is missing or changed", status=409)
            data = path.read_bytes()
        else:
            raise PmError("invalid_evidence_ref", "unsupported evidence reference kind", status=409)
        normalized = {"ref_kind": kind, "ref": ref, "media_kind": _media_kind(ref), "sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        for key in ("media_kind", "sha256", "size_bytes"):
            if key in value and value[key] != normalized[key]:
                raise PmError("invalid_evidence_ref", "evidence metadata does not match immutable bytes", status=409)
        return normalized

    def _verify_artifacts(
        self, ticket_identifier: str, run_id: str, artifact_refs: tuple[str, ...]
    ) -> None:
        for ref in artifact_refs:
            prefix = f"artifact://{ticket_identifier}/"
            if not ref.startswith(prefix) or not ref.removeprefix(prefix):
                raise PmError("invalid_artifact_ref", "artifact reference does not belong to the ticket", status=409)
            name = ref.removeprefix(prefix)
            if self.artifacts is None:
                raise PmError("artifact_store_unavailable", "artifact evidence is unavailable", status=409)
            record = self.artifacts.record_for(ticket_identifier, name)
            path = self.artifacts.resolve_file(ticket_identifier, name)
            if record is None or path is None or record.run_id != run_id:
                raise PmError("invalid_artifact_ref", "artifact evidence is missing or has different run provenance", status=409)
            if _sha256_path(path) != record.sha256:
                raise PmError("artifact_tampered", "artifact evidence bytes no longer match the host record", status=409)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise PmError("git_verification_failed", result.stderr.strip() or "Git verification failed", status=409)
    return result.stdout.strip()


def _git_clone(source: Path, target: Path) -> None:
    result = subprocess.run(
        ["git", "clone", "--no-local", "--no-checkout", "--", str(source), str(target)],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PmError("git_verification_failed", result.stderr.strip() or "isolated checkout failed", status=409)


def _acceptance_token(
    project_id: str, ticket_identifier: str, task_revision: str,
    tested_commit: str, required: tuple[str, ...], receipts: tuple[str, ...],
) -> str:
    payload = (project_id, ticket_identifier, task_revision, tested_commit, required, receipts)
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode()
    ).hexdigest()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        if process.poll() is None:
            process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait()


def _hash_file(handle) -> str:
    handle.flush()
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _procedure_policy(value: Any, procedure_id: str) -> dict[str, Any] | None:
    if isinstance(value, str):
        return {"procedure_id": procedure_id, "kind": "native", "definition_digest": value, "legacy": True}
    if isinstance(value, dict) and value.get("kind") in {"native", "manual"} and isinstance(value.get("definition_digest"), str):
        return dict(value)
    return None


def _receipt_qualifies(row: Any, policy: dict[str, Any] | None) -> bool:
    if policy is None or str(row["result"]) != "passed" or bool(row["timed_out"]) or bool(row["cancelled"]):
        return False
    data = dict(row)
    if policy["kind"] == "native":
        digest = data.get("definition_digest") or data.get("command_digest")
        return str(digest) == str(policy["definition_digest"]) and data.get("return_code") == 0
    return (
        str(data.get("procedure_kind")) == "manual"
        and str(data.get("definition_digest")) == str(policy["definition_digest"])
        and data.get("return_code") is None
        and bool(data.get("report_ref_json"))
    )


def _manual_receipt(row: dict[str, Any]) -> VerificationReceipt:
    report = json.loads(str(row["report_ref_json"])) if row.get("report_ref_json") else None
    if isinstance(report, dict):
        report = {
            **report,
            "download_url": f"/api/v1/pm/evidence/{row['receipt_id']}/report",
        }
    artifacts = tuple(
        str(item.get("ref")) if isinstance(item, dict) else str(item)
        for item in json.loads(str(row.get("artifact_refs_json") or "[]"))
    )
    result = cast(Literal["passed", "failed"], str(row["result"]))
    procedure_kind = cast(Literal["native", "manual"], str(row["procedure_kind"]))
    return VerificationReceipt(
        str(row["receipt_id"]), str(row["operation_id"]), None,
        str(row["project_id"]), str(row["ticket_identifier"]),
        str(row["task_revision"]), str(row["procedure_id"]),
        str(row["definition_digest"]), str(row["tested_commit"]),
        row.get("return_code"), bool(row["timed_out"]), bool(row["cancelled"]),
        str(row["stdout_hash"]), str(row["stderr_hash"]),
        str(row["started_at"]), str(row["completed_at"]), artifacts,
        result, provenance_kind="manual",
        procedure_kind=procedure_kind, definition_digest=str(row["definition_digest"]),
        change_id=str(row["change_id"]), task_id=str(row["task_id"]),
        environment_summary=str(row["environment_summary"]),
        submitted_at=str(row["submitted_at"]), submitted_by=str(row["submitted_by"]),
        report_ref=report,
    )


def _media_kind(ref: str) -> str:
    return {
        ".json": "json", ".xml": "junit_xml", ".png": "image",
        ".jpg": "image", ".jpeg": "image", ".webp": "image",
        ".mp4": "video", ".webm": "video",
    }.get(Path(ref).suffix.casefold(), "text")
