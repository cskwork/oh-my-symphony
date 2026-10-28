"""Pinned, fail-closed OpenSpec 1.14.2 adapter."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
from datetime import date
from pathlib import Path
from typing import Any

from .models import (
    ArchiveCandidate,
    ArchiveResult,
    OpenSpecProfile,
    OpenSpecStatus,
    OpenSpecTasks,
    PmError,
    PmFinding,
    ValidationReport,
)

SUPPORTED_VERSION = "1.14.2"
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
BUNDLE_ROOT = (Path(__file__).parent / "resources" / "openspec-cli").resolve()
BUNDLE_CLI = BUNDLE_ROOT / "lib" / "openspec" / "cli" / "openspec.mjs"
_NODE_CACHE: dict[str, tuple[str, str]] = {}


def resolve_node() -> tuple[str, str]:
    """Resolve the host Node outside the project; cache only supported versions."""
    executable = shutil.which("node")
    if executable in _NODE_CACHE:
        return _NODE_CACHE[executable]
    found = "none"
    if executable is not None:
        try:
            result = subprocess.run(
                [executable, "-p", "JSON.stringify([process.execPath, process.version])"],
                cwd=BUNDLE_ROOT, capture_output=True, text=True, timeout=5,
            )
            payload = json.loads(result.stdout)
            if isinstance(payload, list) and len(payload) == 2:
                exec_path, version = payload
                if isinstance(version, str):
                    found = version
                    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
                    if (
                        result.returncode == 0
                        and isinstance(exec_path, str)
                        and Path(exec_path).is_absolute()
                        and match is not None
                        and tuple(map(int, match.groups())) >= (20, 19, 0)
                    ):
                        resolved = (exec_path, version)
                        _NODE_CACHE[executable] = resolved
                        return resolved
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    raise PmError(
        "capability_unavailable",
        f"Bundled OpenSpec needs Node.js 20.19.0 or newer on PATH (found: {found})",
        status=503,
    )


class OpenSpecAdapter:
    def __init__(self, executable: str | None = None, timeout_seconds: float = 30.0):
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._profiles: dict[str, tuple[str, OpenSpecProfile]] = {}

    def capabilities(self, project_root: Path) -> OpenSpecProfile:
        key = str(project_root.resolve())
        schema = project_root / "openspec" / "schemas" / "symphony-pm" / "schema.yaml"
        cache_key = _file_hash(schema)
        cached = self._profiles.get(key)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        try:
            version = self._run(project_root, "--version", json_output=False).strip()
            if version != SUPPORTED_VERSION:
                profile = OpenSpecProfile(
                    False,
                    version or None,
                    False,
                    reason=f"OpenSpec {SUPPORTED_VERSION} required",
                )
            else:
                payload = self._run_json(
                    project_root, "schema", "validate", "symphony-pm", "--json"
                )
                profile = OpenSpecProfile(
                    bool(payload.get("valid")),
                    version,
                    bool(payload.get("valid")),
                    (
                        "status",
                        "instructions",
                        "validate",
                        "schema validate",
                        "archive",
                    ),
                    None if payload.get("valid") else "symphony-pm schema is invalid",
                )
        except PmError as exc:
            profile = OpenSpecProfile(False, None, False, reason=exc.message)
        self._profiles[key] = (cache_key, profile)
        return profile

    def status(self, project_root: Path, change_locator: str) -> OpenSpecStatus:
        self._require_profile(project_root)
        payload = self._run_json(
            project_root, "status", "--change", change_locator, "--json"
        )
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            raise PmError("capability_unavailable", "unexpected OpenSpec status shape")
        return OpenSpecStatus(
            tuple(item for item in artifacts if isinstance(item, dict)),
            bool(payload.get("isPlanningComplete")),
        )

    def apply_instructions(
        self, project_root: Path, change_locator: str
    ) -> OpenSpecTasks:
        self._require_profile(project_root)
        payload = self._run_json(
            project_root,
            "instructions",
            "apply",
            "--change",
            change_locator,
            "--json",
        )
        progress = payload.get("progress")
        tasks = payload.get("tasks")
        if not isinstance(progress, dict) or not isinstance(tasks, list):
            raise PmError(
                "capability_unavailable", "unexpected OpenSpec apply-instructions shape"
            )
        return OpenSpecTasks(
            _integer(progress.get("total")),
            _integer(progress.get("complete")),
            _integer(progress.get("remaining")),
            tuple(item for item in tasks if isinstance(item, dict)),
        )

    def validate(
        self, project_root: Path, change_locator: str
    ) -> ValidationReport:
        self._require_profile(project_root)
        payload = self._run_json(
            project_root,
            "validate",
            change_locator,
            "--type",
            "change",
            "--strict",
            "--json",
            "--no-interactive",
            allow_error=True,
        )
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise PmError("capability_unavailable", "unexpected OpenSpec validation shape")
        item = items[0]
        if str(item.get("id") or "") != change_locator:
            raise PmError("capability_unavailable", "OpenSpec validated a different change")
        valid = item.get("valid") is True
        findings: list[PmFinding] = []
        issues = item.get("issues")
        if not isinstance(issues, list):
            raise PmError("capability_unavailable", "unexpected OpenSpec validation issues")
        for issue in issues:
            if isinstance(issue, dict):
                findings.append(
                    PmFinding(
                        str(issue.get("code") or "openspec_validation"),
                        str(issue.get("message") or "OpenSpec validation failed"),
                        severity="error" if valid else "blocking",
                    )
                )
        return ValidationReport(valid, tuple(findings), "openspec", SUPPORTED_VERSION)

    def archive_preview(
        self, project_root: Path, change_locator: str
    ) -> ArchiveCandidate:
        self._require_profile(project_root)
        root = (project_root / "openspec" / "changes" / change_locator).resolve()
        if not root.exists() or project_root.resolve() not in root.parents:
            raise PmError("change_not_found", "OpenSpec change is missing", status=404)
        identity = root / "symphony.yaml"
        change_id = change_locator
        if identity.exists():
            try:
                raw = json.loads(json.dumps(_load_yaml(identity)))
                if isinstance(raw.get("change_id"), str):
                    change_id = raw["change_id"]
            except (OSError, ValueError):
                pass
        archive_name = (
            change_locator
            if re.match(r"^\d{4}-\d{2}-\d{2}-", change_locator)
            else f"{date.today().isoformat()}-{change_locator}"
        )
        targets = (str(project_root / "openspec" / "changes" / "archive" / archive_name),)
        return ArchiveCandidate(
            project_id=str(project_root.resolve()),
            change_id=change_id,
            revision=_tree_hash(root),
            controlled_workspace=str(project_root.resolve()),
            source_paths=tuple(str(path) for path in sorted(root.rglob("*")) if path.is_file()),
            target_paths=targets,
        )

    def archive(
        self, candidate: ArchiveCandidate, controlled_workspace: Path
    ) -> ArchiveResult:
        workspace = controlled_workspace.resolve()
        if str(workspace) != candidate.controlled_workspace:
            raise PmError("wrong_project", "archive workspace changed", status=409)
        expected = Path(candidate.target_paths[0]).resolve()
        if not re.match(r"^\d{4}-\d{2}-\d{2}-.+", expected.name):
            raise PmError("invalid_request", "archive preview target is invalid")
        locator, source = _archive_source(workspace, expected.name, candidate.revision)
        if not source.exists() or _tree_hash(source) != candidate.revision:
            raise PmError("revision_conflict", "archive preview is stale", status=409)
        payload = self._run_json(
            workspace, "archive", locator, "--yes", "--json"
        )
        archive = payload.get("archive")
        if not isinstance(archive, dict):
            raise PmError("archive_failed", "OpenSpec archive result is missing")
        archived_as = str(archive.get("archivedAs") or "")
        raw_path = archive.get("path")
        if not isinstance(raw_path, str) or not archived_as:
            raise PmError("archive_failed", "OpenSpec archive destination is missing")
        archived = Path(raw_path).resolve()
        archive_root = (workspace / "openspec" / "changes" / "archive").resolve()
        if (
            archive_root not in archived.parents
            or archived.name != archived_as
            or archived != expected
            or not archived.exists()
        ):
            raise PmError("archive_failed", "OpenSpec archive destination is invalid")
        return ArchiveResult(
            str(archived),
            tuple(str(path) for path in sorted((workspace / "openspec" / "specs").glob("**/spec.md"))),
            payload,
        )

    def _require_profile(self, project_root: Path) -> None:
        profile = self.capabilities(project_root)
        if not profile.available:
            raise PmError(
                "capability_unavailable",
                profile.reason or "OpenSpec is unavailable",
                status=503,
            )

    def _run_json(
        self, project_root: Path, *args: str, allow_error: bool = False
    ) -> dict[str, Any]:
        output = self._run(project_root, *args, allow_nonzero=allow_error)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise PmError(
                "capability_unavailable", "OpenSpec stdout is not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise PmError("capability_unavailable", "OpenSpec JSON must be an object")
        return payload

    def _run(
        self, project_root: Path, *args: str, json_output: bool = True,
        allow_nonzero: bool = False,
    ) -> str:
        del json_output
        command = (
            [self.executable, *args] if self.executable is not None
            else [resolve_node()[0], str(BUNDLE_CLI), *args]
        )
        env = {**os.environ, "OPENSPEC_TELEMETRY": "0"}
        try:
            process = subprocess.Popen(
                command, cwd=project_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
        except FileNotFoundError as exc:
            raise PmError("capability_unavailable", f"OpenSpec failed: {exc}", status=503) from exc
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        overflow = threading.Event()

        def read_bounded(pipe: Any, target: bytearray) -> None:
            while True:
                chunk = pipe.read(64 * 1024)
                if not chunk:
                    return
                if len(target) + len(chunk) > MAX_OUTPUT_BYTES:
                    overflow.set()
                    _terminate(process)
                    return
                target.extend(chunk)

        assert process.stdout is not None and process.stderr is not None
        readers = (
            threading.Thread(target=read_bounded, args=(process.stdout, stdout_buffer), daemon=True),
            threading.Thread(target=read_bounded, args=(process.stderr, stderr_buffer), daemon=True),
        )
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _terminate(process)
            for reader in readers:
                reader.join(timeout=1)
            raise PmError("capability_unavailable", f"OpenSpec failed: {exc}", status=503) from exc
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            _terminate(process)
            for reader in readers:
                reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            raise PmError(
                "capability_unavailable",
                "OpenSpec process cleanup could not be confirmed",
                status=503,
            )
        if overflow.is_set():
            raise PmError("capability_unavailable", "OpenSpec output exceeded limit")
        stdout = bytes(stdout_buffer).decode("utf-8", errors="replace").strip()
        stderr = bytes(stderr_buffer).decode("utf-8", errors="replace").strip()
        if process.returncode != 0 and not allow_nonzero:
            raise PmError(
                "openspec_failed",
                stderr or stdout or f"OpenSpec exited {process.returncode}",
                details={"exit_code": process.returncode},
            )
        return stdout


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except PermissionError:
                if process.poll() is None:
                    process.terminate()
        elif process.poll() is None:
            process.terminate()
        if process.poll() is None:
            process.wait(timeout=1)
    except (PermissionError, ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except PermissionError:
                    if process.poll() is None:
                        process.kill()
            elif process.poll() is None:
                process.kill()
            if process.poll() is None:
                process.wait(timeout=1)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


def _archive_source(
    workspace: Path, archive_name: str, revision: str
) -> tuple[str, Path]:
    changes = workspace / "openspec" / "changes"
    stripped = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", archive_name, count=1)
    for locator in dict.fromkeys((archive_name, stripped)):
        source = changes / locator
        if source.exists() and _tree_hash(source) == revision:
            return locator, source
    raise PmError("revision_conflict", "archive preview is stale", status=409)


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PmError("capability_unavailable", "OpenSpec progress value is invalid")
    return value


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("identity must be a mapping")
    return value
