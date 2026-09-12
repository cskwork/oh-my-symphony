"""Pure validation for machine-enforced application release contracts.

Workers produce evidence, but the Symphony host independently resolves Git
facts, hashes every cited file, and requires exact coverage before allowing a
release transition.  This module performs no tracker writes; lifecycle wiring
in :mod:`symphony.orchestrator.core` decides how a failed result is handled.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from ..utils.git_inspect import (
    changed_paths_since,
    current_branch,
    is_git_stageable_path,
    is_merged,
    read_commit_blob,
    resolve_local_branch_commit,
)


SCHEMA_VERSION = 1
REQUIRED_CHECK_KINDS = frozenset(
    {
        "feature",
        "control",
        "visual",
        "responsive",
        "accessibility",
        "reliability",
    }
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_CONTRACT_FIELDS = {
    "schema_version",
    "target_branch",
    "finalizer_ticket",
    "implementation_tickets",
    "launch",
    "runner",
    "viewports",
    "checks",
}
_CONTRACT_RUNNER_FIELDS = {"command", "sources"}
_CONTRACT_RUNNER_SOURCE_FIELDS = {"path", "sha256"}
_LAUNCH_FIELDS = {"command", "ready_url"}
_VIEWPORT_FIELDS = {"width", "height"}
_CHECK_FIELDS = {
    "id",
    "kind",
    "description",
    "repair_group",
    "required_viewports",
}
_EVIDENCE_FIELDS = {
    "schema_version",
    "verifier_ticket",
    "contract_sha256",
    "target_branch",
    "target_sha",
    "runner",
    "checks",
    "console_errors",
    "failed_requests",
}
_RUNNER_FIELDS = {
    "name",
    "command",
    "exit_code",
    "results_path",
    "results_sha256",
}
_NATIVE_RESULT_FIELDS = {
    "schema_version",
    "verifier_ticket",
    "contract_sha256",
    "target_branch",
    "target_sha",
    "checks",
}
_NATIVE_RESULT_CHECK_FIELDS = {"id", "status"}
_EVIDENCE_CHECK_FIELDS = {
    "id",
    "status",
    "expected",
    "actual",
    "repro",
    "viewports",
    "artifacts",
}
_ARTIFACT_FIELDS = {"path", "sha256"}
_CHECK_STATUSES = {"PASS", "FAIL"}


class _DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object would otherwise silently overwrite a key."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping depth."""

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.id}",
                node.start_mark,
            )
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"found duplicate key {key!r}")
        result[key] = value
    return result


def _load_strict_yaml(raw: bytes) -> object:
    return yaml.load(raw, Loader=_UniqueKeySafeLoader)


def _load_strict_json(raw: str) -> object:
    return json.loads(raw, object_pairs_hook=_unique_json_object)


@dataclass(frozen=True)
class ReleaseCheck:
    id: str
    kind: str
    description: str
    repair_group: str
    required_viewports: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseRunnerSource:
    path: str
    sha256: str


@dataclass(frozen=True)
class ReleaseContract:
    target_branch: str
    finalizer_ticket: str
    implementation_tickets: tuple[str, ...]
    launch: dict[str, Any]
    runner_command: str
    runner_sources: tuple[ReleaseRunnerSource, ...]
    viewports: tuple[str, ...]
    checks: tuple[ReleaseCheck, ...]


@dataclass(frozen=True)
class RepairableFailure:
    check_id: str
    repair_group: str
    description: str
    expected: str
    actual: str
    repro: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseValidationResult:
    passed: bool
    evidence_errors: tuple[str, ...]
    repairable_failures: tuple[RepairableFailure, ...]
    target_branch: str
    target_sha: str
    contract_sha256: str
    fingerprint: str
    finalizer_ticket: str
    note_text: str


@dataclass(frozen=True)
class TargetReleaseIdentity:
    target_branch: str
    target_sha: str
    contract_sha256: str
    finalizer_ticket: str
    errors: tuple[str, ...] = ()


def _hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_safe_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(_SAFE_IDENTIFIER.fullmatch(value))
        and ".." not in value
    )


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _is_schema_version_one(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _safe_repo_relative_path(value: object) -> str | None:
    path_text = _nonempty_string(value)
    if (
        path_text is None
        or path_text.startswith(("/", "-"))
        or "\\" in path_text
        or ":" in path_text
        or any(char.isspace() and char != " " for char in path_text)
    ):
        return None
    posix_path = PurePosixPath(path_text)
    if str(posix_path) != path_text or any(
        part in {"", ".", ".."} for part in posix_path.parts
    ):
        return None
    return path_text


def _safe_board_mount(value: object) -> PurePosixPath | None:
    """Normalize a configured workspace mount path without allowing escapes."""
    if isinstance(value, PurePosixPath):
        value = value.as_posix()
    safe = _safe_repo_relative_path(value)
    return PurePosixPath(safe) if safe is not None else None


def _duplicates(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def _mapping(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return value


def _unknown_fields(
    value: dict[str, Any], allowed: set[str], subject: str, errors: list[str]
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append(f"{subject} has unknown field(s): {', '.join(unknown)}")


def _load_contract(
    path: Path,
) -> tuple[ReleaseContract | None, str, bytes, list[str]]:
    errors: list[str] = []
    if not path.is_file():
        return None, "", b"", [f"missing release contract: {path}"]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, "", b"", [f"cannot read release contract: {exc}"]
    contract_hash = _hash_bytes(raw)
    try:
        loaded = _load_strict_yaml(raw)
    except yaml.YAMLError as exc:
        return None, contract_hash, raw, [f"invalid release-contract.yaml: {exc}"]
    data = _mapping(loaded)
    if data is None:
        return None, contract_hash, raw, [
            "release-contract.yaml must contain a mapping"
        ]
    _unknown_fields(data, _CONTRACT_FIELDS, "release contract", errors)
    missing_fields = sorted(_CONTRACT_FIELDS - set(data))
    if missing_fields:
        errors.append(
            f"release contract is missing field(s): {', '.join(missing_fields)}"
        )
    if not _is_schema_version_one(data.get("schema_version")):
        errors.append(f"release contract schema_version must be {SCHEMA_VERSION}")

    target_branch = _nonempty_string(data.get("target_branch")) or ""
    if not target_branch:
        errors.append("release contract target_branch must be a non-empty string")
    elif target_branch.startswith("-") or any(
        char.isspace() or char in "~^:?*[\\" for char in target_branch
    ):
        errors.append("release contract target_branch is unsafe")

    finalizer = _nonempty_string(data.get("finalizer_ticket")) or ""
    if not _is_safe_identifier(finalizer):
        errors.append("release contract finalizer_ticket is unsafe or empty")

    implementations_raw = data.get("implementation_tickets")
    implementations: list[str] = []
    if not isinstance(implementations_raw, list) or not implementations_raw:
        errors.append("release contract implementation_tickets must be a non-empty list")
    else:
        for item in implementations_raw:
            if not _is_safe_identifier(item):
                errors.append(f"release contract has unsafe implementation ticket: {item!r}")
                continue
            implementations.append(item)
        duplicate_implementations = _duplicates(implementations)
        if duplicate_implementations:
            errors.append(
                "release contract has duplicate implementation ticket(s): "
                + ", ".join(duplicate_implementations)
            )

    launch = _mapping(data.get("launch"))
    if not launch:
        errors.append("release contract launch must be a non-empty mapping")
        launch = {}
    else:
        _unknown_fields(launch, _LAUNCH_FIELDS, "contract launch", errors)
        if _nonempty_string(launch.get("command")) is None:
            errors.append("release contract launch.command must be a non-empty string")
        if "ready_url" in launch and _nonempty_string(launch.get("ready_url")) is None:
            errors.append(
                "release contract launch.ready_url must be a non-empty string"
            )

    runner = _mapping(data.get("runner"))
    runner_command = ""
    runner_sources: list[ReleaseRunnerSource] = []
    runner_source_paths: list[str] = []
    if runner is None:
        errors.append("release contract runner must be a mapping")
    else:
        _unknown_fields(runner, _CONTRACT_RUNNER_FIELDS, "contract runner", errors)
        missing_runner = sorted(_CONTRACT_RUNNER_FIELDS - set(runner))
        if missing_runner:
            errors.append(
                "release contract runner is missing field(s): "
                + ", ".join(missing_runner)
            )
        runner_command = _nonempty_string(runner.get("command")) or ""
        if not runner_command:
            errors.append("release contract runner.command must be a non-empty string")
        sources_raw = runner.get("sources")
        if not isinstance(sources_raw, list) or not sources_raw:
            errors.append(
                "release contract runner.sources must be a non-empty list"
            )
        else:
            for index, source_raw in enumerate(sources_raw):
                source = _mapping(source_raw)
                if source is None:
                    errors.append(
                        f"release contract runner source[{index}] must be a mapping"
                    )
                    continue
                _unknown_fields(
                    source,
                    _CONTRACT_RUNNER_SOURCE_FIELDS,
                    f"contract runner source[{index}]",
                    errors,
                )
                missing_source = sorted(
                    _CONTRACT_RUNNER_SOURCE_FIELDS - set(source)
                )
                if missing_source:
                    errors.append(
                        f"release contract runner source[{index}] is missing field(s): "
                        + ", ".join(missing_source)
                    )
                source_path = _safe_repo_relative_path(source.get("path"))
                expected_hash = _nonempty_string(source.get("sha256"))
                if source_path is None:
                    errors.append(
                        f"release contract runner source[{index}] path is unsafe or empty"
                    )
                else:
                    runner_source_paths.append(source_path)
                if expected_hash is None or _SHA256.fullmatch(expected_hash) is None:
                    errors.append(
                        f"release contract runner source[{index}] sha256 must be "
                        "64 hexadecimal characters"
                    )
                if source_path is not None and expected_hash is not None:
                    runner_sources.append(
                        ReleaseRunnerSource(
                            path=source_path,
                            sha256=expected_hash.lower(),
                        )
                    )
            duplicate_sources = _duplicates(runner_source_paths)
            if duplicate_sources:
                errors.append(
                    "release contract runner has duplicate source path(s): "
                    + ", ".join(duplicate_sources)
                )

    viewports_raw = _mapping(data.get("viewports"))
    viewport_names: list[str] = []
    if not viewports_raw:
        errors.append("release contract viewports must be a non-empty mapping")
    else:
        for name, dimensions_raw in viewports_raw.items():
            if not _is_safe_identifier(name):
                errors.append(f"release contract has unsafe viewport identifier: {name!r}")
                continue
            viewport_names.append(name)
            dimensions = _mapping(dimensions_raw)
            if dimensions is None:
                errors.append(f"viewport {name!r} must be a mapping")
                continue
            _unknown_fields(
                dimensions,
                _VIEWPORT_FIELDS,
                f"viewport {name!r}",
                errors,
            )
            for axis in ("width", "height"):
                amount = dimensions.get(axis)
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    errors.append(f"viewport {name!r} {axis} must be a positive integer")

    checks_raw = data.get("checks")
    checks: list[ReleaseCheck] = []
    check_ids: list[str] = []
    if not isinstance(checks_raw, list) or not checks_raw:
        errors.append("release contract checks must be a non-empty list")
    else:
        for index, check_raw in enumerate(checks_raw):
            check = _mapping(check_raw)
            if check is None:
                errors.append(f"release contract check[{index}] must be a mapping")
                continue
            _unknown_fields(check, _CHECK_FIELDS, f"check[{index}]", errors)
            missing = sorted(_CHECK_FIELDS - set(check))
            if missing:
                errors.append(
                    f"release contract check[{index}] is missing field(s): "
                    + ", ".join(missing)
                )
            check_id = _nonempty_string(check.get("id")) or ""
            kind = _nonempty_string(check.get("kind")) or ""
            description = _nonempty_string(check.get("description")) or ""
            repair_group = _nonempty_string(check.get("repair_group")) or ""
            required_raw = check.get("required_viewports")
            required_viewports: list[str] = []
            if not _is_safe_identifier(check_id):
                errors.append(f"release contract check[{index}] has unsafe id")
            else:
                check_ids.append(check_id)
            if kind not in REQUIRED_CHECK_KINDS:
                errors.append(
                    f"release contract check {check_id or index!r} has unknown kind {kind!r}"
                )
            if not description:
                errors.append(
                    f"release contract check {check_id or index!r} description is empty"
                )
            if not _is_safe_identifier(repair_group):
                errors.append(
                    f"release contract check {check_id or index!r} has unsafe repair_group"
                )
            if not isinstance(required_raw, list) or not required_raw:
                errors.append(
                    f"release contract check {check_id or index!r} required_viewports "
                    "must be a non-empty list"
                )
            else:
                for viewport in required_raw:
                    if not isinstance(viewport, str) or viewport not in viewport_names:
                        errors.append(
                            f"release contract check {check_id or index!r} references "
                            f"unknown viewport {viewport!r}"
                        )
                        continue
                    required_viewports.append(viewport)
                duplicates = _duplicates(required_viewports)
                if duplicates:
                    errors.append(
                        f"release contract check {check_id or index!r} has duplicate "
                        f"required viewport(s): {', '.join(duplicates)}"
                    )
            checks.append(
                ReleaseCheck(
                    id=check_id,
                    kind=kind,
                    description=description,
                    repair_group=repair_group,
                    required_viewports=tuple(required_viewports),
                )
            )

    duplicate_checks = _duplicates(check_ids)
    if duplicate_checks:
        errors.append(
            "release contract has duplicate check id(s): "
            + ", ".join(duplicate_checks)
        )
    present_kinds = {check.kind for check in checks}
    missing_kinds = sorted(REQUIRED_CHECK_KINDS - present_kinds)
    if missing_kinds:
        errors.append(
            "release contract is missing required check kind(s): "
            + ", ".join(missing_kinds)
        )

    if errors:
        return None, contract_hash, raw, errors
    return (
        ReleaseContract(
            target_branch=target_branch,
            finalizer_ticket=finalizer,
            implementation_tickets=tuple(implementations),
            launch=launch,
            runner_command=runner_command,
            runner_sources=tuple(runner_sources),
            viewports=tuple(viewport_names),
            checks=tuple(checks),
        ),
        contract_hash,
        raw,
        [],
    )


def _load_evidence(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, [f"missing verifier release-evidence.json: {path}"]
    try:
        loaded = _load_strict_json(path.read_text(encoding="utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKeyError,
    ) as exc:
        return None, [f"invalid verifier release-evidence.json: {exc}"]
    data = _mapping(loaded)
    if data is None:
        return None, ["release-evidence.json must contain an object"]
    errors: list[str] = []
    _unknown_fields(data, _EVIDENCE_FIELDS, "release evidence", errors)
    missing = sorted(_EVIDENCE_FIELDS - set(data))
    if missing:
        errors.append(f"release evidence is missing field(s): {', '.join(missing)}")
    if not _is_schema_version_one(data.get("schema_version")):
        errors.append(f"release evidence schema_version must be {SCHEMA_VERSION}")
    return data, errors


def _validated_artifact(
    *,
    workspace_root: Path,
    verifier_root: Path,
    raw: object,
    subject: str,
    errors: list[str],
) -> str | None:
    item = _mapping(raw)
    if item is None:
        errors.append(f"{subject} artifact must be an object")
        return None
    _unknown_fields(item, _ARTIFACT_FIELDS, f"{subject} artifact", errors)
    path_text = _nonempty_string(item.get("path"))
    expected_hash = _nonempty_string(item.get("sha256"))
    if path_text is None or Path(path_text).is_absolute():
        errors.append(f"{subject} artifact path is unsafe or empty")
        return None
    if expected_hash is None or _SHA256.fullmatch(expected_hash) is None:
        errors.append(f"{subject} artifact sha256 must be 64 hexadecimal characters")
        return None
    try:
        candidate = (workspace_root / path_text).resolve(strict=True)
        candidate.relative_to(verifier_root)
    except (FileNotFoundError, OSError, ValueError):
        errors.append(
            f"{subject} artifact must exist below {verifier_root}: {path_text}"
        )
        return None
    if not candidate.is_file():
        errors.append(f"{subject} artifact is not a regular file: {path_text}")
        return None
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        errors.append(f"{subject} artifact cannot be inspected: {exc}")
        return None
    if size <= 0:
        errors.append(f"{subject} artifact is empty: {path_text}")
        return None
    if not _require_git_stageable_path(
        workspace_root=workspace_root,
        candidate=candidate,
        repo_relative_path=path_text,
        subject=f"{subject} artifact",
        errors=errors,
    ):
        return None
    try:
        actual_hash = _hash_file(candidate)
    except OSError as exc:
        errors.append(f"{subject} artifact cannot be read: {path_text}: {exc}")
        return None
    if actual_hash != expected_hash.lower():
        errors.append(f"{subject} artifact hash mismatch: {path_text}")
        return None
    return path_text


def _entry_text(entry: object, field: str, default: str) -> str:
    if isinstance(entry, dict):
        value = _nonempty_string(entry.get(field))
        if value:
            return value
    if isinstance(entry, str) and entry.strip():
        return entry.strip()
    return default


def _require_git_stageable_path(
    *,
    workspace_root: Path,
    candidate: Path,
    repo_relative_path: str | None = None,
    subject: str,
    errors: list[str],
) -> bool:
    if repo_relative_path is None:
        try:
            relative = candidate.relative_to(workspace_root).as_posix()
        except ValueError:
            errors.append(f"{subject} is not contained in the Git workspace")
            return False
    else:
        relative = repo_relative_path
    stageable = is_git_stageable_path(workspace_root, relative)
    if stageable is None:
        errors.append(
            f"{subject} Git-stageable status could not be determined: {relative}"
        )
        return False
    if not stageable:
        errors.append(
            f"{subject} must be tracked or Git-stageable, but is ignored: {relative}"
        )
        return False
    return True


def _load_native_results(
    *,
    workspace_root: Path,
    path_text: str,
    verifier_ticket: str,
    contract_hash: str,
    target_branch: str,
    target_sha: str | None,
    errors: list[str],
) -> dict[str, str] | None:
    path = workspace_root / path_text
    try:
        loaded = _load_strict_json(path.read_text(encoding="utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKeyError,
    ) as exc:
        errors.append(f"native runner results must be a valid JSON object: {exc}")
        return None
    data = _mapping(loaded)
    if data is None:
        errors.append("native runner results must be a valid JSON object")
        return None
    _unknown_fields(data, _NATIVE_RESULT_FIELDS, "native runner results", errors)
    missing = sorted(_NATIVE_RESULT_FIELDS - set(data))
    if missing:
        errors.append(
            "native runner results are missing binding field(s): "
            + ", ".join(missing)
        )
    if not _is_schema_version_one(data.get("schema_version")):
        errors.append(f"native runner results schema_version must be {SCHEMA_VERSION}")
    if data.get("verifier_ticket") != verifier_ticket:
        errors.append("native runner results verifier_ticket does not match")
    native_contract_hash = _nonempty_string(data.get("contract_sha256"))
    if native_contract_hash is None or native_contract_hash.lower() != contract_hash:
        errors.append("native runner results contract_sha256 does not match")
    if data.get("target_branch") != target_branch:
        errors.append("native runner results target_branch does not match")
    native_target_sha = _nonempty_string(data.get("target_sha"))
    if (
        native_target_sha is None
        or _FULL_SHA.fullmatch(native_target_sha) is None
        or target_sha is None
        or native_target_sha.lower() != target_sha
    ):
        errors.append("native runner results target_sha does not match current target")

    checks_raw = data.get("checks")
    checks: dict[str, str] = {}
    ids: list[str] = []
    if not isinstance(checks_raw, list):
        errors.append("native runner results checks must be a list")
        return None
    for index, raw_check in enumerate(checks_raw):
        check = _mapping(raw_check)
        if check is None:
            errors.append(f"native runner results check[{index}] must be an object")
            continue
        _unknown_fields(
            check,
            _NATIVE_RESULT_CHECK_FIELDS,
            f"native runner results check[{index}]",
            errors,
        )
        check_id = _nonempty_string(check.get("id")) or ""
        status = _nonempty_string(check.get("status")) or ""
        if not _is_safe_identifier(check_id):
            errors.append(f"native runner results check[{index}] has unsafe id")
            continue
        if status not in _CHECK_STATUSES:
            errors.append(
                f"native runner results check {check_id!r} status must be PASS or FAIL"
            )
            continue
        ids.append(check_id)
        checks.setdefault(check_id, status)
    duplicate_ids = _duplicates(ids)
    if duplicate_ids:
        errors.append(
            "native runner results have duplicate check id(s): "
            + ", ".join(duplicate_ids)
        )
    return checks


def _fingerprint(
    verifier_ticket: str,
    contract_hash: str,
    target_sha: str,
    failures: Iterable[RepairableFailure],
) -> str:
    failed_ids = sorted(failure.check_id for failure in failures)
    raw = "\0".join(
        [verifier_ticket, contract_hash, target_sha, *failed_ids]
    ).encode("utf-8")
    return _hash_bytes(raw)


def _note_text(
    *, errors: tuple[str, ...], failures: tuple[RepairableFailure, ...]
) -> str:
    if not errors and not failures:
        return "Release contract validated against the current target commit."
    lines = ["Release validation did not pass."]
    if errors:
        lines.extend(["", "Evidence errors:"])
        lines.extend(f"- {error}" for error in errors)
    if failures:
        lines.extend(["", "Repairable failures:"])
        lines.extend(
            f"- `{failure.check_id}` ({failure.repair_group}): {failure.actual}"
            for failure in failures
        )
    return "\n".join(lines)


def release_workspace_target_errors(
    *,
    workspace_root: Path,
    repository_root: Path,
    target_sha: str,
    board_root: Path | None = None,
    board_mount: PurePosixPath | None = None,
    allowed_roots: tuple[PurePosixPath, ...] = (),
    role: str = "release",
) -> tuple[str, ...]:
    """Prove a worker tree is derived from one exact target commit."""
    workspace_root = workspace_root.resolve()
    repository_root = repository_root.resolve()
    errors: list[str] = []
    if not is_merged(workspace_root, target_sha, "HEAD"):
        errors.append(f"{role} workspace HEAD must contain the approved target commit")
    safe_board_mount = _safe_board_mount(board_mount)
    if board_mount is not None and safe_board_mount is None:
        errors.append(f"{role} configured workspace board mount is unsafe or empty")
    board_root_info = _repository_relative_board_root(
        repository_root=repository_root,
        board_root=board_root,
    )
    board_relative = board_root_info[0] if board_root_info is not None else None
    validated_board_mount: PurePosixPath | None = None
    if board_root_info is not None:
        relative_root, configured_root = board_root_info
        if safe_board_mount is not None and safe_board_mount != relative_root:
            errors.append(
                f"{role} configured workspace board mount must match {relative_root}: "
                f"{safe_board_mount}"
            )
        expected_mount = safe_board_mount or relative_root
        mount_error = _workspace_board_mount_error(
            workspace_root=workspace_root,
            relative_root=expected_mount,
            configured_root=configured_root,
        )
        if mount_error is None:
            validated_board_mount = expected_mount
        else:
            errors.append(f"{role} {mount_error}")
    elif board_root is not None:
        configured_root = _resolved_configured_board_root(
            repository_root=repository_root,
            board_root=board_root,
        )
        if configured_root is None:
            errors.append(
                f"{role} "
                + _configured_board_root_error(
                    repository_root=repository_root,
                    board_root=board_root,
                )
            )
        elif safe_board_mount is None:
            errors.append(
                f"{role} "
                + _configured_board_root_error(
                    repository_root=repository_root,
                    board_root=board_root,
                )
            )
        else:
            mount_error = _workspace_board_mount_error(
                workspace_root=workspace_root,
                relative_root=safe_board_mount,
                configured_root=configured_root,
            )
            if mount_error is None:
                board_relative = safe_board_mount
                validated_board_mount = safe_board_mount
            else:
                errors.append(f"{role} {mount_error}")
    changed_paths = changed_paths_since(
        workspace_root,
        target_sha,
        inspect_roots=(board_relative,)
        if board_relative is not None
        else (),
    )
    if changed_paths is None:
        errors.append(
            f"{role} workspace changes could not be compared with the approved target"
        )
        return tuple(errors)
    control_roots = allowed_roots
    if validated_board_mount is not None:
        control_roots = (*control_roots, validated_board_mount)
    unexpected: list[str] = []
    for changed_path in changed_paths:
        candidate = PurePosixPath(changed_path)
        if not any(_path_is_within(candidate, root) for root in control_roots):
            unexpected.append(changed_path)
    if unexpected:
        allowed = ", ".join(str(root) for root in control_roots) or "no paths"
        errors.append(
            f"{role} workspace differs from the approved target outside {allowed}: "
            + ", ".join(unexpected)
        )
    return tuple(errors)


def _configured_board_root_error(
    *, repository_root: Path, board_root: Path
) -> str:
    """Explain why a configured board cannot be an eligible host board."""
    configured_path = board_root
    if not configured_path.is_absolute():
        configured_path = repository_root / configured_path
    try:
        configured_root = configured_path.resolve(strict=True)
    except FileNotFoundError:
        return f"configured host board root does not exist: {configured_path}"
    except (OSError, RuntimeError):
        return f"configured host board root could not be resolved: {configured_path}"
    if not configured_root.is_dir():
        return f"configured host board root must be a directory: {configured_root}"
    try:
        relative_root = configured_root.relative_to(repository_root)
    except ValueError:
        return (
            "configured host board root must be inside repository root: "
            f"{configured_root}"
        )
    if not relative_root.parts:
        return (
            "configured host board root must identify a directory below "
            f"repository root: {configured_root}"
        )
    return "configured host board root is invalid"


def _workspace_board_mount_error(
    *, workspace_root: Path, relative_root: PurePosixPath, configured_root: Path
) -> str | None:
    """Explain why the workspace board entry is not the configured mount."""
    workspace_entry = workspace_root.joinpath(*relative_root.parts)
    try:
        is_junction = workspace_entry.is_junction()
    except (AttributeError, OSError):
        is_junction = False
    is_link = workspace_entry.is_symlink() or is_junction
    if not workspace_entry.exists() and not is_link:
        return f"workspace board mount is missing at {relative_root}"
    if not is_link:
        return (
            "workspace board mount must be a symlink or directory junction at "
            f"{relative_root}"
        )
    try:
        resolved_entry = workspace_entry.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return f"workspace board mount could not be resolved at {relative_root}"
    if resolved_entry != configured_root:
        return (
            f"workspace board mount at {relative_root} must resolve to "
            f"configured host board {configured_root}; resolved to {resolved_entry}"
        )
    return None


def _repository_relative_board_root(
    *, repository_root: Path, board_root: Path | None
) -> tuple[PurePosixPath, Path] | None:
    """Resolve a configured board root and prove it is inside the repo."""
    configured_root = _resolved_configured_board_root(
        repository_root=repository_root,
        board_root=board_root,
    )
    if configured_root is None:
        return None
    try:
        relative_root = configured_root.relative_to(repository_root)
    except ValueError:
        return None
    if not relative_root.parts:
        return None
    return PurePosixPath(relative_root.as_posix()), configured_root


def _resolved_configured_board_root(
    *, repository_root: Path, board_root: Path | None
) -> Path | None:
    """Resolve an existing configured board directory, even when out-of-tree."""
    if board_root is None:
        return None
    configured_root = board_root
    if not configured_root.is_absolute():
        configured_root = repository_root / configured_root
    try:
        configured_root = configured_root.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    return configured_root if configured_root.is_dir() else None


def _path_is_within(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True



def resolve_configured_target_branch(
    repository_root: Path, configured_target_branch: str
) -> str:
    """Return the branch the release cycle proves.

    An empty ``agent.auto_merge_target_branch`` means "the repository's
    current branch" everywhere else in Symphony (auto-merge, feature-branch
    creation, ``symphony doctor``'s deep-preset contract). The release binder
    used to reject the empty string outright, so a deep board created from
    the shipped example (both keys empty) could never dispatch its verifier.
    Resolve the empty value to the checked-out branch here so every release
    entry point agrees with the merge semantics. A detached HEAD still
    yields ``""`` and fails the usual "resolvable local branch" check.
    """
    target = configured_target_branch.strip()
    if target:
        return target
    return (current_branch(repository_root) or "").strip()

def validate_release_contract(
    *,
    workspace_root: Path,
    repository_root: Path,
    verifier_ticket: str,
    configured_target_branch: str,
    board_root: Path | None = None,
    board_mount: PurePosixPath | None = None,
) -> ReleaseValidationResult:
    """Validate standard release contract/evidence paths without writes."""
    workspace_root = workspace_root.resolve()
    repository_root = repository_root.resolve()
    evidence_errors: list[str] = []
    failures: list[RepairableFailure] = []

    if not _is_safe_identifier(verifier_ticket):
        evidence_errors.append("current verifier ticket identifier is unsafe")
        safe_verifier = "invalid-verifier"
    else:
        safe_verifier = verifier_ticket

    contract_path = workspace_root / "release-contract.yaml"
    try:
        resolved_contract_path = contract_path.resolve(strict=True)
        resolved_contract_path.relative_to(workspace_root)
    except (FileNotFoundError, OSError, ValueError):
        contract = None
        contract_hash = ""
        contract_raw = b""
        contract_errors = [
            "workspace release-contract.yaml must be a regular file contained "
            "in the repository"
        ]
    else:
        contract, contract_hash, contract_raw, contract_errors = _load_contract(
            resolved_contract_path
        )
    evidence_errors.extend(contract_errors)
    target_branch = resolve_configured_target_branch(
        repository_root, configured_target_branch
    )
    target_sha: str | None = None
    if not target_branch or target_branch.startswith("-") or any(
        char.isspace() or char in "~^:?*[\\" for char in target_branch
    ):
        evidence_errors.append("configured target branch is unsafe or empty")
    else:
        target_sha = resolve_local_branch_commit(repository_root, target_branch)
        if target_sha is None:
            evidence_errors.append(
                "configured target_branch must name a resolvable local branch: "
                f"{target_branch}"
            )
    if contract is not None:
        if target_branch != contract.target_branch:
            evidence_errors.append(
                "release contract target_branch does not match the configured target branch"
            )
    if target_sha is not None:
        evidence_errors.extend(
            release_workspace_target_errors(
                workspace_root=workspace_root,
                repository_root=repository_root,
                target_sha=target_sha,
                board_root=board_root,
                board_mount=board_mount,
                allowed_roots=(PurePosixPath("docs") / safe_verifier,),
                role="verifier",
            )
        )
        target_contract_raw = read_commit_blob(
            repository_root, target_sha, "release-contract.yaml"
        )
        if target_contract_raw is None:
            evidence_errors.append(
                "exact target commit is missing a regular release-contract.yaml"
            )
        elif target_contract_raw != contract_raw:
            evidence_errors.append(
                "workspace release-contract.yaml does not byte-match the exact target commit"
            )

    if contract is not None and target_sha is not None:
        for source in contract.runner_sources:
            target_source = read_commit_blob(repository_root, target_sha, source.path)
            if target_source is None:
                evidence_errors.append(
                    f"contract runner source is missing or not a regular file in the "
                    f"exact target commit: {source.path}"
                )
            elif _hash_bytes(target_source) != source.sha256:
                evidence_errors.append(
                    f"contract runner source hash does not match exact target commit: "
                    f"{source.path}"
                )
            try:
                workspace_source = (workspace_root / source.path).resolve(strict=True)
                workspace_source.relative_to(workspace_root)
            except (FileNotFoundError, OSError, ValueError):
                evidence_errors.append(
                    f"contract runner source must be contained in the workspace: "
                    f"{source.path}"
                )
                continue
            if not workspace_source.is_file():
                evidence_errors.append(
                    f"contract runner source is not a regular workspace file: "
                    f"{source.path}"
                )
                continue
            try:
                workspace_hash = _hash_file(workspace_source)
            except OSError as exc:
                evidence_errors.append(
                    f"contract runner source cannot be read from the workspace: "
                    f"{source.path}: {exc}"
                )
            else:
                if workspace_hash != source.sha256:
                    evidence_errors.append(
                        f"contract runner source hash does not match workspace file: "
                        f"{source.path}"
                    )

    verifier_path = workspace_root / "docs" / safe_verifier
    try:
        verifier_root = verifier_path.resolve(strict=True)
        verifier_root.relative_to(workspace_root)
    except (FileNotFoundError, OSError, ValueError):
        verifier_root = verifier_path
        evidence = None
        load_errors = [
            "verifier evidence root for release-evidence.json must exist and be "
            "contained in the workspace: "
            f"{verifier_path}"
        ]
    else:
        evidence_path = verifier_root / "qa" / "release-evidence.json"
        try:
            resolved_evidence_path = evidence_path.resolve(strict=True)
            resolved_evidence_path.relative_to(verifier_root)
            resolved_evidence_path.relative_to(workspace_root)
        except (FileNotFoundError, OSError, ValueError):
            evidence = None
            load_errors = [
                "verifier release-evidence.json must exist and be contained in "
                f"the workspace: {evidence_path}"
            ]
        else:
            _require_git_stageable_path(
                workspace_root=workspace_root,
                candidate=resolved_evidence_path,
                repo_relative_path=evidence_path.relative_to(
                    workspace_root
                ).as_posix(),
                subject="verifier release-evidence.json manifest",
                errors=evidence_errors,
            )
            evidence, load_errors = _load_evidence(resolved_evidence_path)
    evidence_errors.extend(load_errors)

    contract_checks = {check.id: check for check in contract.checks} if contract else {}
    native_checks: dict[str, str] | None = None
    runner_exit_code: int | None = None
    if evidence is not None:
        evidence_verifier = _nonempty_string(evidence.get("verifier_ticket"))
        if evidence_verifier != verifier_ticket:
            evidence_errors.append(
                "release evidence verifier_ticket does not match the current verifier"
            )
        evidence_contract_hash = _nonempty_string(evidence.get("contract_sha256"))
        if (
            evidence_contract_hash is None
            or _SHA256.fullmatch(evidence_contract_hash) is None
            or evidence_contract_hash.lower() != contract_hash
        ):
            evidence_errors.append(
                "release evidence contract_sha256 does not match raw release-contract.yaml"
            )
        evidence_branch = _nonempty_string(evidence.get("target_branch"))
        if evidence_branch != target_branch:
            evidence_errors.append(
                "release evidence target_branch does not match the release contract"
            )
        evidence_target_sha = _nonempty_string(evidence.get("target_sha"))
        if evidence_target_sha is None or _FULL_SHA.fullmatch(evidence_target_sha) is None:
            evidence_errors.append("release evidence target_sha must be a full commit SHA")
        elif target_sha is None or evidence_target_sha.lower() != target_sha:
            evidence_errors.append(
                "release evidence target_sha is stale or does not match the current target"
            )

        runner = _mapping(evidence.get("runner"))
        if runner is None:
            evidence_errors.append("release evidence runner must be an object")
        else:
            _unknown_fields(runner, _RUNNER_FIELDS, "release evidence runner", evidence_errors)
            missing_runner = sorted(_RUNNER_FIELDS - set(runner))
            if missing_runner:
                evidence_errors.append(
                    "release evidence runner is missing field(s): "
                    + ", ".join(missing_runner)
                )
            for field in ("name", "command"):
                if _nonempty_string(runner.get(field)) is None:
                    evidence_errors.append(
                        f"release evidence runner.{field} must be a non-empty string"
                    )
            evidence_runner_command = _nonempty_string(runner.get("command"))
            if (
                contract is not None
                and evidence_runner_command is not None
                and evidence_runner_command != contract.runner_command
            ):
                evidence_errors.append(
                    "release evidence runner.command does not match the release contract"
                )
            results_path = runner.get("results_path")
            results_hash = runner.get("results_sha256")
            validated_results_path = _validated_artifact(
                workspace_root=workspace_root,
                verifier_root=verifier_root,
                raw={"path": results_path, "sha256": results_hash},
                subject="runner results",
                errors=evidence_errors,
            )
            if validated_results_path is not None:
                native_checks = _load_native_results(
                    workspace_root=workspace_root,
                    path_text=validated_results_path,
                    verifier_ticket=verifier_ticket,
                    contract_hash=contract_hash,
                    target_branch=target_branch,
                    target_sha=target_sha,
                    errors=evidence_errors,
                )
            exit_code = runner.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                evidence_errors.append("release evidence runner.exit_code must be an integer")
            else:
                runner_exit_code = exit_code

        evidence_checks_raw = evidence.get("checks")
        evidence_checks: dict[str, dict[str, Any]] = {}
        evidence_check_ids: list[str] = []
        if not isinstance(evidence_checks_raw, list):
            evidence_errors.append("release evidence checks must be a list")
        else:
            for index, raw_check in enumerate(evidence_checks_raw):
                evidence_check = _mapping(raw_check)
                if evidence_check is None:
                    evidence_errors.append(
                        f"release evidence check[{index}] must be an object"
                    )
                    continue
                _unknown_fields(
                    evidence_check,
                    _EVIDENCE_CHECK_FIELDS,
                    f"release evidence check[{index}]",
                    evidence_errors,
                )
                check_id = _nonempty_string(evidence_check.get("id")) or ""
                if not _is_safe_identifier(check_id):
                    evidence_errors.append(
                        f"release evidence check[{index}] has unsafe id"
                    )
                    continue
                evidence_check_ids.append(check_id)
                evidence_checks.setdefault(check_id, evidence_check)
            duplicate_ids = _duplicates(evidence_check_ids)
            if duplicate_ids:
                evidence_errors.append(
                    "release evidence has duplicate check id(s): "
                    + ", ".join(duplicate_ids)
                )

        if contract is not None:
            missing_ids = sorted(set(contract_checks) - set(evidence_checks))
            extra_ids = sorted(set(evidence_checks) - set(contract_checks))
            if missing_ids:
                evidence_errors.append(
                    "release evidence is missing contract check(s): "
                    + ", ".join(missing_ids)
                )
            if extra_ids:
                evidence_errors.append(
                    "release evidence has unknown check(s): " + ", ".join(extra_ids)
                )
        if native_checks is not None:
            missing_native = sorted(set(evidence_checks) - set(native_checks))
            extra_native = sorted(set(native_checks) - set(evidence_checks))
            if missing_native:
                evidence_errors.append(
                    "native runner results are missing release check(s): "
                    + ", ".join(missing_native)
                )
            if extra_native:
                evidence_errors.append(
                    "native runner results have unknown release check(s): "
                    + ", ".join(extra_native)
                )
            for check_id in sorted(set(evidence_checks) & set(native_checks)):
                evidence_status = _nonempty_string(evidence_checks[check_id].get("status"))
                if evidence_status != native_checks[check_id]:
                    evidence_errors.append(
                        f"native runner results status for {check_id!r} does not "
                        "match release evidence"
                    )

        for check_id, evidence_check in evidence_checks.items():
            contract_check = contract_checks.get(check_id)
            if contract_check is None:
                continue
            status = _nonempty_string(evidence_check.get("status"))
            expected = _nonempty_string(evidence_check.get("expected"))
            actual = _nonempty_string(evidence_check.get("actual"))
            repro = _nonempty_string(evidence_check.get("repro"))
            for field, value in (
                ("status", status),
                ("expected", expected),
                ("actual", actual),
                ("repro", repro),
            ):
                if value is None:
                    evidence_errors.append(
                        f"release evidence check {check_id!r} {field} must be non-empty"
                    )
            if status is not None and status not in _CHECK_STATUSES:
                evidence_errors.append(
                    f"release evidence check {check_id!r} status must be PASS or FAIL"
                )
            viewports = evidence_check.get("viewports")
            if not isinstance(viewports, list) or not all(
                isinstance(viewport, str) for viewport in viewports
            ):
                evidence_errors.append(
                    f"release evidence check {check_id!r} viewports must be a string list"
                )
                covered: set[str] = set()
            else:
                covered = set(viewports)
                duplicate_viewports = _duplicates(viewports)
                if duplicate_viewports:
                    evidence_errors.append(
                        f"release evidence check {check_id!r} has duplicate viewport(s): "
                        + ", ".join(duplicate_viewports)
                    )
                unknown_viewports = sorted(covered - set(contract.viewports)) if contract else []
                if unknown_viewports:
                    evidence_errors.append(
                        f"release evidence check {check_id!r} cites unknown viewport(s): "
                        + ", ".join(unknown_viewports)
                    )
            missing_viewports = sorted(set(contract_check.required_viewports) - covered)
            if missing_viewports:
                evidence_errors.append(
                    f"release evidence check {check_id!r} is missing required viewport(s): "
                    + ", ".join(missing_viewports)
                )

            artifacts = evidence_check.get("artifacts")
            artifact_paths: list[str] = []
            if not isinstance(artifacts, list) or not artifacts:
                evidence_errors.append(
                    f"release evidence check {check_id!r} must cite at least one artifact"
                )
            else:
                for artifact_index, artifact in enumerate(artifacts):
                    validated = _validated_artifact(
                        workspace_root=workspace_root,
                        verifier_root=verifier_root,
                        raw=artifact,
                        subject=f"check {check_id!r} artifact[{artifact_index}]",
                        errors=evidence_errors,
                    )
                    if validated:
                        artifact_paths.append(validated)
            if status == "FAIL":
                failures.append(
                    RepairableFailure(
                        check_id=check_id,
                        repair_group=contract_check.repair_group,
                        description=contract_check.description,
                        expected=expected or contract_check.description,
                        actual=actual or f"status {status}",
                        repro=repro or "rerun the contract check",
                        evidence=tuple(artifact_paths),
                    )
                )

        if (
            runner_exit_code is not None
            and contract is not None
            and native_checks is not None
            and set(evidence_checks) == set(contract_checks) == set(native_checks)
            and all(
                _nonempty_string(evidence_checks[check_id].get("status"))
                in _CHECK_STATUSES
                and native_checks[check_id]
                == _nonempty_string(evidence_checks[check_id].get("status"))
                for check_id in contract_checks
            )
        ):
            all_checks_pass = all(
                _nonempty_string(evidence_checks[check_id].get("status")) == "PASS"
                for check_id in contract_checks
            )
            if (runner_exit_code == 0) != all_checks_pass:
                evidence_errors.append(
                    "release evidence runner.exit_code contradicts the exact native "
                    "and evidence check statuses"
                )

        for field, repair_group in (
            ("console_errors", "runtime-console"),
            ("failed_requests", "runtime-network"),
        ):
            entries = evidence.get(field)
            if not isinstance(entries, list):
                evidence_errors.append(f"release evidence {field} must be a list")
                continue
            for index, entry in enumerate(entries):
                failures.append(
                    RepairableFailure(
                        check_id=f"{field}:{index + 1}",
                        repair_group=repair_group,
                        description=f"No unexpected {field.replace('_', ' ')}",
                        expected=_entry_text(entry, "expected", "no unexpected failure"),
                        actual=_entry_text(entry, "actual", str(entry)),
                        repro=_entry_text(entry, "repro", "reload the application"),
                    )
                )

    if contract is not None and target_sha is not None:
        for ticket in contract.implementation_tickets:
            branch = f"symphony/{ticket}"
            branch_sha = resolve_local_branch_commit(repository_root, branch)
            if branch_sha is None:
                failures.append(
                    RepairableFailure(
                        check_id=f"ancestry:{ticket}",
                        repair_group="integration",
                        description=f"Implementation branch {branch} exists and is merged",
                        expected=f"{branch} exists and is an ancestor of {target_branch}",
                        actual=f"{branch} does not resolve to a commit",
                        repro=f"git rev-parse --verify {branch}^{{commit}}",
                        evidence=(f"target_sha={target_sha}",),
                    )
                )
            elif not is_merged(repository_root, branch_sha, target_sha):
                failures.append(
                    RepairableFailure(
                        check_id=f"ancestry:{ticket}",
                        repair_group="integration",
                        description=f"Implementation branch {branch} is merged",
                        expected=f"{branch} is an ancestor of {target_branch}",
                        actual=f"{branch_sha} is not an ancestor of {target_sha}",
                        repro=f"git merge-base --is-ancestor {branch} {target_branch}",
                        evidence=(f"branch_sha={branch_sha}", f"target_sha={target_sha}"),
                    )
                )

    errors_tuple = tuple(dict.fromkeys(evidence_errors))
    failures_tuple = tuple(failures)
    resolved_target_sha = target_sha or ""
    fingerprint = _fingerprint(
        verifier_ticket,
        contract_hash,
        resolved_target_sha,
        failures_tuple,
    )
    return ReleaseValidationResult(
        passed=not errors_tuple and not failures_tuple,
        evidence_errors=errors_tuple,
        repairable_failures=failures_tuple,
        target_branch=target_branch,
        target_sha=resolved_target_sha,
        contract_sha256=contract_hash,
        fingerprint=fingerprint,
        finalizer_ticket=contract.finalizer_ticket if contract is not None else "",
        note_text=_note_text(errors=errors_tuple, failures=failures_tuple),
    )


def inspect_release_contract(
    path: Path, *, configured_target_branch: str = ""
) -> tuple[str, ...]:
    """Return schema/configuration errors without requiring verifier evidence."""
    contract, _contract_hash, _raw, errors = _load_contract(path)
    target_branch = resolve_configured_target_branch(
        path.parent, configured_target_branch
    )
    if contract is not None and target_branch != contract.target_branch:
        errors.append(
            "release contract target_branch does not match the configured target branch"
        )
    return tuple(dict.fromkeys(errors))


def resolve_target_release_identity(
    *, repository_root: Path, configured_target_branch: str
) -> TargetReleaseIdentity:
    """Resolve the raw contract identity from the exact configured branch tip."""
    target_branch = resolve_configured_target_branch(
        repository_root, configured_target_branch
    )
    errors: list[str] = []
    target_sha = resolve_local_branch_commit(repository_root, target_branch) or ""
    if not target_sha:
        errors.append(
            "configured target_branch must name a resolvable local branch: "
            f"{target_branch!r}"
        )
        return TargetReleaseIdentity(target_branch, "", "", "", tuple(errors))
    raw = read_commit_blob(repository_root, target_sha, "release-contract.yaml")
    if raw is None:
        errors.append(
            "exact target commit does not contain a regular release-contract.yaml"
        )
        return TargetReleaseIdentity(target_branch, target_sha, "", "", tuple(errors))
    contract_hash = _hash_bytes(raw)
    try:
        loaded = _load_strict_yaml(raw)
    except yaml.YAMLError as exc:
        errors.append(f"invalid target release-contract.yaml: {exc}")
        loaded = None
    data = _mapping(loaded)
    finalizer = ""
    if data is None:
        errors.append("target release-contract.yaml must contain a mapping")
    else:
        contract_branch = _nonempty_string(data.get("target_branch")) or ""
        if contract_branch != target_branch:
            errors.append(
                "target release contract target_branch does not match configured target"
            )
        finalizer = _nonempty_string(data.get("finalizer_ticket")) or ""
        if not _is_safe_identifier(finalizer):
            errors.append("target release contract finalizer_ticket is unsafe or empty")
    return TargetReleaseIdentity(
        target_branch=target_branch,
        target_sha=target_sha,
        contract_sha256=contract_hash,
        finalizer_ticket=finalizer,
        errors=tuple(dict.fromkeys(errors)),
    )


__all__ = [
    "ReleaseCheck",
    "ReleaseContract",
    "ReleaseRunnerSource",
    "ReleaseValidationResult",
    "RepairableFailure",
    "TargetReleaseIdentity",
    "inspect_release_contract",
    "resolve_target_release_identity",
    "validate_release_contract",
]
