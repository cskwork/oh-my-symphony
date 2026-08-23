"""Machine-enforced application release-contract validation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from pathlib import PurePosixPath

import pytest
import yaml

from symphony.orchestrator import release_contracts as release_contracts_module
from symphony.orchestrator.release_contracts import (
    release_workspace_target_errors,
    resolve_target_release_identity,
    validate_release_contract,
)
from symphony.utils.git_inspect import read_commit_blob, resolve_local_branch_commit
from tests._win_skips import (
    requires_symlink_privilege,
    symlink_privilege_available,
)


_KINDS = (
    "feature",
    "control",
    "visual",
    "responsive",
    "accessibility",
    "reliability",
)
_RUNNER_SOURCE = b"#!/usr/bin/env python3\nprint('release runner')\n"
_RUNNER_COMMAND = "python tools/release_runner.py"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.test")
    # Pin the repo to the line-ending behavior CI runs with: a host-wide
    # `core.autocrlf=true` (common on Windows) rewrites blobs/worktrees and
    # breaks the byte-exact contract comparisons these tests assert on.
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "branch", "symphony/APP-1")
    _write_valid_release(repo)
    return repo


def _contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "target_branch": "main",
        "finalizer_ticket": "APP-FINAL",
        "implementation_tickets": ["APP-1"],
        "launch": {
            "command": "npm run dev",
            "ready_url": "http://127.0.0.1:3000",
        },
        "runner": {
            "command": _RUNNER_COMMAND,
            "sources": [
                {
                    "path": "tools/release_runner.py",
                    "sha256": hashlib.sha256(_RUNNER_SOURCE).hexdigest(),
                }
            ],
        },
        "viewports": {
            "desktop": {"width": 1440, "height": 900},
            "tablet": {"width": 768, "height": 1024},
            "mobile": {"width": 390, "height": 844},
        },
        "checks": [
            {
                "id": f"{kind}-check",
                "kind": kind,
                "description": f"{kind} behavior is ready",
                "repair_group": f"{kind}-group",
                "required_viewports": ["desktop", "tablet", "mobile"],
            }
            for kind in _KINDS
        ],
    }


def _write_valid_release(
    repo: Path,
    *,
    contract: dict[str, object] | None = None,
    evidence_mutator=None,
) -> tuple[dict[str, object], dict[str, object]]:
    contract = contract or _contract()
    runner_source = repo / "tools" / "release_runner.py"
    runner_source.parent.mkdir(parents=True, exist_ok=True)
    runner_source.write_bytes(_RUNNER_SOURCE)
    contract_path = repo / "release-contract.yaml"
    contract_path.write_text(
        yaml.safe_dump(contract, sort_keys=False), encoding="utf-8"
    )
    _git(repo, "add", "release-contract.yaml", "tools/release_runner.py")
    if _git(repo, "status", "--short", "--", "release-contract.yaml", "tools/release_runner.py"):
        _git(repo, "commit", "-m", "release contract authority")
    verifier_root = repo / "docs" / "VERIFY-1"
    qa_root = verifier_root / "qa"
    qa_root.mkdir(parents=True, exist_ok=True)
    target_sha = _git(repo, "rev-parse", "main")
    native_checks = [
        {"id": f"{kind}-check", "status": "PASS"} for kind in _KINDS
    ]
    native = qa_root / "native-results.json"
    native.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verifier_ticket": "VERIFY-1",
                "contract_sha256": _sha(contract_path),
                "target_branch": "main",
                "target_sha": target_sha,
                "checks": native_checks,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    artifact = qa_root / "browser.png"
    artifact.write_bytes(b"not-a-real-png-but-non-empty")
    evidence: dict[str, object] = {
        "schema_version": 1,
        "verifier_ticket": "VERIFY-1",
        "contract_sha256": _sha(contract_path),
        "target_branch": "main",
        "target_sha": target_sha,
        "runner": {
            "name": "native-browser-suite",
            "command": _RUNNER_COMMAND,
            "exit_code": 0,
            "results_path": "docs/VERIFY-1/qa/native-results.json",
            "results_sha256": _sha(native),
        },
        "checks": [
            {
                "id": f"{kind}-check",
                "status": "PASS",
                "expected": f"{kind} works",
                "actual": f"{kind} works",
                "repro": f"run the {kind} scenario",
                "viewports": ["desktop", "tablet", "mobile"],
                "artifacts": [
                    {
                        "path": "docs/VERIFY-1/qa/browser.png",
                        "sha256": _sha(artifact),
                    }
                ],
            }
            for kind in _KINDS
        ],
        "console_errors": [],
        "failed_requests": [],
    }
    if evidence_mutator is not None:
        evidence_mutator(evidence)
    evidence_path = qa_root / "release-evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return contract, evidence


def _validate(repo: Path):
    return validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )


def _workspace_with_board_entry(
    release_repo: Path, tmp_path: Path
) -> tuple[Path, Path, str]:
    board_root = release_repo / "kanban"
    board_root.mkdir()
    workspace = tmp_path / "workspace"
    _git(
        release_repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "symphony/VERIFY-WORKSPACE",
        str(workspace),
        "main",
    )
    exclude = release_repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(
        exclude.read_text(encoding="utf-8") + "\nkanban\n",
        encoding="utf-8",
    )
    target_sha = _git(workspace, "rev-parse", "HEAD")
    return workspace, board_root, target_sha


def _workspace_target_errors(
    *, workspace: Path, board_root: Path | None, target_sha: str, repository_root: Path
) -> tuple[str, ...]:
    return release_workspace_target_errors(
        workspace_root=workspace,
        repository_root=repository_root,
        target_sha=target_sha,
        board_root=board_root,
    )


@requires_symlink_privilege
def test_configured_host_board_symlink_is_control_data(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )
    (workspace / "kanban").symlink_to(board_root, target_is_directory=True)

    assert _workspace_target_errors(
        workspace=workspace,
        board_root=board_root,
        target_sha=target_sha,
        repository_root=release_repo,
    ) == ()


@requires_symlink_privilege
def test_copied_workspace_accepts_exact_external_host_board_mount(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )
    (workspace / "kanban").symlink_to(board_root, target_is_directory=True)

    errors = release_workspace_target_errors(
        workspace_root=workspace,
        repository_root=workspace,
        target_sha=target_sha,
        board_root=board_root,
        board_mount=PurePosixPath("kanban"),
    )

    assert errors == ()


def test_configured_host_board_requires_workspace_mount(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )

    errors = _workspace_target_errors(
        workspace=workspace,
        board_root=board_root,
        target_sha=target_sha,
        repository_root=release_repo,
    )

    assert any("workspace board mount is missing" in error for error in errors)


def test_configured_host_board_must_exist(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )
    board_root.rmdir()

    errors = _workspace_target_errors(
        workspace=workspace,
        board_root=board_root,
        target_sha=target_sha,
        repository_root=release_repo,
    )

    assert any("configured host board root does not exist" in error for error in errors)


def test_same_name_real_product_directory_is_not_control_data(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )
    del board_root
    (workspace / "kanban").mkdir()
    (workspace / "kanban" / "product.js").write_text(
        "unapproved product change\n", encoding="utf-8"
    )

    errors = _workspace_target_errors(
        workspace=workspace,
        board_root=release_repo / "kanban",
        target_sha=target_sha,
        repository_root=release_repo,
    )

    assert errors
    assert any("kanban" in error for error in errors)


@requires_symlink_privilege
def test_wrong_target_board_symlink_is_not_control_data(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )
    wrong_board = release_repo / "other-board"
    wrong_board.mkdir()
    (workspace / "kanban").symlink_to(wrong_board, target_is_directory=True)

    errors = _workspace_target_errors(
        workspace=workspace,
        board_root=board_root,
        target_sha=target_sha,
        repository_root=release_repo,
    )

    assert errors
    assert any("kanban" in error for error in errors)


def test_configured_real_board_directory_is_not_control_data(
    release_repo: Path,
) -> None:
    board_root = release_repo / "kanban"
    board_root.mkdir()
    (board_root / "product.js").write_text(
        "unapproved product change\n", encoding="utf-8"
    )
    target_sha = _git(release_repo, "rev-parse", "HEAD")

    errors = _workspace_target_errors(
        workspace=release_repo,
        board_root=board_root,
        target_sha=target_sha,
        repository_root=release_repo,
    )

    assert errors
    assert any("kanban" in error for error in errors)


@requires_symlink_privilege
def test_external_configured_board_symlink_is_not_control_data(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, _board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )
    external_board = tmp_path / "kanban"
    external_board.mkdir()
    (workspace / "kanban").symlink_to(external_board, target_is_directory=True)

    errors = _workspace_target_errors(
        workspace=workspace,
        board_root=external_board,
        target_sha=target_sha,
        repository_root=release_repo,
    )

    assert errors
    assert any("inside repository root" in error for error in errors)


@requires_symlink_privilege
def test_external_board_does_not_accept_stale_workspace_kanban_mount(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, _board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )
    external_board = tmp_path / "host-board"
    external_board.mkdir()
    (workspace / "kanban").symlink_to(
        release_repo / "kanban", target_is_directory=True
    )

    errors = _workspace_target_errors(
        workspace=workspace,
        board_root=external_board,
        target_sha=target_sha,
        repository_root=release_repo,
    )

    assert any("inside repository root" in error for error in errors)


def test_default_target_validation_still_rejects_product_changes(
    release_repo: Path, tmp_path: Path
) -> None:
    workspace, board_root, target_sha = _workspace_with_board_entry(
        release_repo, tmp_path
    )
    del board_root
    (workspace / "product.js").write_text(
        "unapproved product change\n", encoding="utf-8"
    )

    errors = _workspace_target_errors(
        workspace=workspace,
        board_root=None,
        target_sha=target_sha,
        repository_root=release_repo,
    )

    assert errors


def _mutate_native(repo: Path, mutate) -> None:
    native_path = repo / "docs" / "VERIFY-1" / "qa" / "native-results.json"
    evidence_path = repo / "docs" / "VERIFY-1" / "qa" / "release-evidence.json"
    mutate(native_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["runner"]["results_sha256"] = _sha(native_path)
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")


def test_current_target_with_exact_coverage_and_artifacts_passes(
    release_repo: Path,
) -> None:
    result = _validate(release_repo)

    assert result.passed is True
    assert result.evidence_errors == ()
    assert result.repairable_failures == ()
    assert result.target_branch == "main"
    assert result.target_sha == _git(release_repo, "rev-parse", "main")
    assert len(result.contract_sha256) == 64
    assert len(result.fingerprint) == 64


def test_historical_green_cannot_approve_a_new_target(release_repo: Path) -> None:
    old_sha = _git(release_repo, "rev-parse", "main")
    (release_repo / "app.txt").write_text("v2\n", encoding="utf-8")
    _git(release_repo, "add", "app.txt")
    _git(release_repo, "commit", "-m", "new target")

    result = _validate(release_repo)

    assert result.passed is False
    assert old_sha != result.target_sha
    assert any("target_sha" in error for error in result.evidence_errors)


def test_stale_verifier_workspace_cannot_claim_current_target(
    release_repo: Path,
) -> None:
    target_before = _git(release_repo, "rev-parse", "main")
    _git(release_repo, "branch", "symphony/VERIFY-STALE", target_before)
    (release_repo / "app.txt").write_text("v2\n", encoding="utf-8")
    _git(release_repo, "add", "app.txt")
    _git(release_repo, "commit", "-m", "new target product commit")
    target_after = _git(release_repo, "rev-parse", "main")
    _git(release_repo, "switch", "symphony/VERIFY-STALE")

    qa = release_repo / "docs" / "VERIFY-1" / "qa"
    native_path = qa / "native-results.json"
    native = json.loads(native_path.read_text(encoding="utf-8"))
    native["target_sha"] = target_after
    native_path.write_text(json.dumps(native), encoding="utf-8")
    evidence_path = qa / "release-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["target_sha"] = target_after
    evidence["runner"]["results_sha256"] = _sha(native_path)
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = _validate(release_repo)

    assert not result.passed
    assert any(
        "workspace HEAD must contain" in error for error in result.evidence_errors
    )


def test_verifier_workspace_cannot_modify_product_outside_evidence_root(
    release_repo: Path,
) -> None:
    (release_repo / "app.txt").write_text("unapproved verifier change\n", encoding="utf-8")

    result = _validate(release_repo)

    assert not result.passed
    assert any(
        "differs from the approved target outside docs/VERIFY-1" in error
        and "app.txt" in error
        for error in result.evidence_errors
    )


def test_wrong_verifier_is_rejected(release_repo: Path) -> None:
    result = validate_release_contract(
        workspace_root=release_repo,
        repository_root=release_repo,
        verifier_ticket="VERIFY-2",
        configured_target_branch="main",
    )

    assert result.passed is False
    assert any("release-evidence.json" in error for error in result.evidence_errors)


def test_target_and_contract_mismatches_are_evidence_errors(
    release_repo: Path,
) -> None:
    def mutate(evidence: dict[str, object]) -> None:
        evidence["target_branch"] = "release"
        evidence["contract_sha256"] = "0" * 64

    _write_valid_release(release_repo, evidence_mutator=mutate)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("target_branch" in error for error in result.evidence_errors)
    assert any("contract_sha256" in error for error in result.evidence_errors)


def test_contract_target_must_match_workflow_target(release_repo: Path) -> None:
    contract = _contract()
    contract["target_branch"] = "release"
    _write_valid_release(release_repo, contract=contract)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("configured target branch" in error for error in result.evidence_errors)


def test_workspace_contract_must_equal_exact_target_blob(release_repo: Path) -> None:
    contract_path = release_repo / "release-contract.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    contract["launch"]["command"] = "npm run mutated"
    contract_path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    contract_hash = _sha(contract_path)
    evidence_path = release_repo / "docs" / "VERIFY-1" / "qa" / "release-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["contract_sha256"] = contract_hash
    native_path = release_repo / evidence["runner"]["results_path"]
    native = json.loads(native_path.read_text(encoding="utf-8"))
    native["contract_sha256"] = contract_hash
    native_path.write_text(json.dumps(native), encoding="utf-8")
    evidence["runner"]["results_sha256"] = _sha(native_path)
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = _validate(release_repo)

    assert result.passed is False
    assert any("exact target" in error for error in result.evidence_errors)


@requires_symlink_privilege
def test_workspace_contract_symlink_cannot_escape_repository(
    release_repo: Path, tmp_path: Path
) -> None:
    contract_path = release_repo / "release-contract.yaml"
    raw = contract_path.read_bytes()
    outside = tmp_path / "outside-contract.yaml"
    outside.write_bytes(raw)
    contract_path.unlink()
    contract_path.symlink_to(outside)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("contained" in error or "unsafe" in error for error in result.evidence_errors)


@requires_symlink_privilege
@pytest.mark.parametrize("mode", ["verifier-root", "evidence-file"])
def test_verifier_evidence_paths_cannot_escape_workspace(
    release_repo: Path, tmp_path: Path, mode: str
) -> None:
    verifier_root = release_repo / "docs" / "VERIFY-1"
    if mode == "verifier-root":
        outside = tmp_path / "outside-verifier"
        verifier_root.rename(outside)
        verifier_root.symlink_to(outside, target_is_directory=True)
    else:
        evidence_path = verifier_root / "qa" / "release-evidence.json"
        outside = tmp_path / "outside-evidence.json"
        outside.write_bytes(evidence_path.read_bytes())
        evidence_path.unlink()
        evidence_path.symlink_to(outside)

    result = _validate(release_repo)

    assert result.passed is False
    assert any(
        "evidence" in error and "contained" in error
        for error in result.evidence_errors
    )


@requires_symlink_privilege
def test_exact_commit_blob_reader_rejects_symlinks_and_unsafe_paths(
    release_repo: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-runner.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    symlink = release_repo / "tools" / "escaped-runner.py"
    symlink.symlink_to(outside)
    _git(release_repo, "add", "tools/escaped-runner.py")
    _git(release_repo, "commit", "-m", "add unsafe runner symlink")
    target_sha = _git(release_repo, "rev-parse", "main")

    assert read_commit_blob(release_repo, target_sha, "tools/escaped-runner.py") is None
    assert read_commit_blob(release_repo, target_sha, "../release-contract.yaml") is None
    assert read_commit_blob(
        release_repo, target_sha, "release-contract.yaml"
    ) == (release_repo / "release-contract.yaml").read_bytes()


def test_release_target_must_be_an_actual_local_branch(release_repo: Path) -> None:
    current_sha = _git(release_repo, "rev-parse", "main")

    assert resolve_local_branch_commit(release_repo, "main") == current_sha
    assert resolve_local_branch_commit(release_repo, current_sha) is None
    assert resolve_local_branch_commit(release_repo, "main@{1}") is None

    result = validate_release_contract(
        workspace_root=release_repo,
        repository_root=release_repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch=current_sha,
    )
    assert result.target_sha == ""
    assert any("local branch" in error for error in result.evidence_errors)


@pytest.mark.parametrize(
    "mode",
    [
        "workspace-mismatch",
        pytest.param(
            "symlink",
            marks=pytest.mark.skipif(
                not symlink_privilege_available(),
                reason="symlink privilege not available on this host",
            ),
        ),
        "command",
    ],
)
def test_runner_authority_is_bound_to_target_and_contract(
    release_repo: Path, tmp_path: Path, mode: str
) -> None:
    source = release_repo / "tools" / "release_runner.py"
    evidence_path = release_repo / "docs" / "VERIFY-1" / "qa" / "release-evidence.json"
    if mode == "workspace-mismatch":
        source.write_text("print('mutated')\n", encoding="utf-8")
    elif mode == "symlink":
        outside = tmp_path / "outside-runner.py"
        outside.write_bytes(_RUNNER_SOURCE)
        source.unlink()
        source.symlink_to(outside)
    else:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["runner"]["command"] = "python other.py"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = _validate(release_repo)

    assert result.passed is False
    assert any("runner" in error for error in result.evidence_errors)


@pytest.mark.parametrize(
    "mode", ["missing", "empty-sources", "unsafe-source", "source-hash"]
)
def test_contract_runner_schema_is_required_and_strict(
    release_repo: Path, mode: str
) -> None:
    contract = _contract()
    runner = contract["runner"]
    assert isinstance(runner, dict)
    if mode == "missing":
        contract.pop("runner")
    elif mode == "empty-sources":
        runner["sources"] = []
    elif mode == "unsafe-source":
        runner["sources"][0]["path"] = "../outside.py"
    else:
        runner["sources"][0]["sha256"] = "0" * 64
    _write_valid_release(release_repo, contract=contract)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("runner" in error for error in result.evidence_errors)


@pytest.mark.parametrize("section", ["launch", "viewport"])
def test_contract_nested_mappings_reject_unknown_fields(
    release_repo: Path, section: str
) -> None:
    contract = _contract()
    if section == "launch":
        contract["launch"]["unexpected"] = True
    else:
        contract["viewports"]["desktop"]["unexpected"] = True
    _write_valid_release(release_repo, contract=contract)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("unknown field" in error for error in result.evidence_errors)


def test_unmerged_implementation_branch_is_repairable(release_repo: Path) -> None:
    _git(release_repo, "switch", "-c", "symphony/APP-2")
    (release_repo / "unmerged.txt").write_text("pending\n", encoding="utf-8")
    _git(release_repo, "add", "unmerged.txt")
    _git(release_repo, "commit", "-m", "unmerged implementation")
    _git(release_repo, "switch", "main")
    contract = _contract()
    contract["implementation_tickets"] = ["APP-1", "APP-2"]
    _write_valid_release(release_repo, contract=contract)

    result = _validate(release_repo)

    assert result.evidence_errors == ()
    assert any(
        failure.check_id == "ancestry:APP-2"
        for failure in result.repairable_failures
    )


@pytest.mark.parametrize("mode", ["missing", "duplicate", "failed"])
def test_check_coverage_is_exact_and_every_status_must_pass(
    release_repo: Path, mode: str
) -> None:
    def mutate(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        if mode == "missing":
            checks.pop()
        elif mode == "duplicate":
            checks.append(dict(checks[0]))
        else:
            checks[0]["status"] = "FAIL"
            checks[0]["actual"] = "button is inert"
            evidence["runner"]["exit_code"] = 1

    _write_valid_release(release_repo, evidence_mutator=mutate)
    if mode == "failed":
        def sync_failed_status(path: Path) -> None:
            native = json.loads(path.read_text(encoding="utf-8"))
            native["checks"][0]["status"] = "FAIL"
            path.write_text(json.dumps(native), encoding="utf-8")

        _mutate_native(release_repo, sync_failed_status)

    result = _validate(release_repo)

    assert result.passed is False
    if mode == "failed":
        assert result.evidence_errors == ()
        assert result.repairable_failures[0].check_id == "feature-check"
    else:
        assert result.evidence_errors


def test_contract_requires_every_kind_and_known_required_viewports(
    release_repo: Path,
) -> None:
    contract = _contract()
    checks = contract["checks"]
    assert isinstance(checks, list)
    checks.pop()
    checks[0]["required_viewports"] = ["desktop", "television"]
    _write_valid_release(release_repo, contract=contract)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("reliability" in error for error in result.evidence_errors)
    assert any("television" in error for error in result.evidence_errors)


def test_duplicate_and_unsafe_check_ids_are_rejected(release_repo: Path) -> None:
    contract = _contract()
    checks = contract["checks"]
    assert isinstance(checks, list)
    checks[1]["id"] = checks[0]["id"]
    checks[2]["id"] = "../escape"
    _write_valid_release(release_repo, contract=contract)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("duplicate" in error for error in result.evidence_errors)
    assert any("unsafe" in error for error in result.evidence_errors)


def test_runner_nonzero_with_all_pass_is_an_evidence_contradiction(
    release_repo: Path,
) -> None:
    def mutate(evidence: dict[str, object]) -> None:
        runner = evidence["runner"]
        assert isinstance(runner, dict)
        runner["exit_code"] = 1

    _write_valid_release(release_repo, evidence_mutator=mutate)

    result = _validate(release_repo)

    assert any("exit_code" in error for error in result.evidence_errors)
    assert not any(
        failure.check_id == "runner:exit-code" for failure in result.repairable_failures
    )


@pytest.mark.parametrize("exit_code", [0, 1])
def test_failed_product_check_requires_nonzero_exit_without_derivative_repair(
    release_repo: Path, exit_code: int
) -> None:
    def mutate_evidence(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "FAIL"
        checks[0]["actual"] = "control is inert"
        evidence["runner"]["exit_code"] = exit_code

    _write_valid_release(release_repo, evidence_mutator=mutate_evidence)

    def mutate_native(path: Path) -> None:
        native = json.loads(path.read_text(encoding="utf-8"))
        native["checks"][0]["status"] = "FAIL"
        path.write_text(json.dumps(native), encoding="utf-8")

    _mutate_native(release_repo, mutate_native)
    result = _validate(release_repo)

    if exit_code == 0:
        assert any("exit_code" in error for error in result.evidence_errors)
    else:
        assert result.evidence_errors == ()
        assert {failure.check_id for failure in result.repairable_failures} == {
            "feature-check"
        }


def test_each_check_must_cite_at_least_one_artifact(release_repo: Path) -> None:
    def mutate(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["artifacts"] = []

    _write_valid_release(release_repo, evidence_mutator=mutate)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("at least one artifact" in error for error in result.evidence_errors)


@pytest.mark.parametrize("mode", ["not-json", "wrong-target", "check-mismatch"])
def test_native_results_are_structurally_bound_to_the_release(
    release_repo: Path, mode: str
) -> None:
    def mutate(path: Path) -> None:
        if mode == "not-json":
            path.write_text("dummy runner output\n", encoding="utf-8")
            return
        native = json.loads(path.read_text(encoding="utf-8"))
        if mode == "wrong-target":
            native["target_sha"] = "0" * 40
        else:
            native["checks"].pop()
        path.write_text(json.dumps(native), encoding="utf-8")

    _mutate_native(release_repo, mutate)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("native runner results" in error for error in result.evidence_errors)


@pytest.mark.parametrize(
    "location",
    ["native-object", "native-check", "evidence-check", "artifact"],
)
def test_nested_release_shapes_reject_unknown_fields(
    release_repo: Path, location: str
) -> None:
    if location.startswith("native"):
        def mutate_native(path: Path) -> None:
            native = json.loads(path.read_text(encoding="utf-8"))
            if location == "native-object":
                native["verdict"] = "GREEN"
            else:
                native["checks"][0]["details"] = "unbound"
            path.write_text(json.dumps(native), encoding="utf-8")

        _mutate_native(release_repo, mutate_native)
    else:
        def mutate_evidence(evidence: dict[str, object]) -> None:
            checks = evidence["checks"]
            assert isinstance(checks, list)
            if location == "evidence-check":
                checks[0]["verdict"] = "GREEN"
            else:
                checks[0]["artifacts"][0]["label"] = "not-in-schema"

        _write_valid_release(release_repo, evidence_mutator=mutate_evidence)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("unknown field" in error for error in result.evidence_errors)


def test_check_statuses_are_only_pass_or_fail(release_repo: Path) -> None:
    def mutate_evidence(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["status"] = "GREEN"

    _write_valid_release(release_repo, evidence_mutator=mutate_evidence)

    def mutate_native(path: Path) -> None:
        native = json.loads(path.read_text(encoding="utf-8"))
        native["checks"][0]["status"] = "GREEN"
        path.write_text(json.dumps(native), encoding="utf-8")

    _mutate_native(release_repo, mutate_native)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("PASS or FAIL" in error for error in result.evidence_errors)


def test_evidence_viewports_cannot_be_duplicated(release_repo: Path) -> None:
    def mutate(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        checks[0]["viewports"].append("desktop")

    _write_valid_release(release_repo, evidence_mutator=mutate)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("duplicate viewport" in error for error in result.evidence_errors)


@pytest.mark.parametrize("layer", ["contract", "evidence", "native"])
def test_schema_version_bool_is_rejected(release_repo: Path, layer: str) -> None:
    if layer == "contract":
        contract = _contract()
        contract["schema_version"] = True
        _write_valid_release(release_repo, contract=contract)
    elif layer == "evidence":
        def mutate(evidence: dict[str, object]) -> None:
            evidence["schema_version"] = True

        _write_valid_release(release_repo, evidence_mutator=mutate)
    else:
        def mutate_native(path: Path) -> None:
            native = json.loads(path.read_text(encoding="utf-8"))
            native["schema_version"] = True
            path.write_text(json.dumps(native), encoding="utf-8")

        _mutate_native(release_repo, mutate_native)

    result = _validate(release_repo)

    assert result.passed is False
    assert any("schema_version" in error for error in result.evidence_errors)


@pytest.mark.parametrize("field", ["console_errors", "failed_requests"])
def test_console_and_network_failures_are_repairable(
    release_repo: Path, field: str
) -> None:
    def mutate(evidence: dict[str, object]) -> None:
        evidence[field] = [
            {
                "expected": "no runtime failure",
                "actual": f"unexpected {field}",
                "repro": "load the application",
            }
        ]

    _write_valid_release(release_repo, evidence_mutator=mutate)

    result = _validate(release_repo)

    assert result.evidence_errors == ()
    assert any(field in failure.check_id for failure in result.repairable_failures)


@pytest.mark.parametrize("mode", ["traversal", "missing", "empty", "hash"])
def test_artifacts_must_be_safe_regular_nonempty_and_hash_matched(
    release_repo: Path, mode: str
) -> None:
    if mode == "traversal":
        outside = release_repo / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")

    def mutate(evidence: dict[str, object]) -> None:
        checks = evidence["checks"]
        assert isinstance(checks, list)
        artifact = checks[0]["artifacts"][0]
        if mode == "traversal":
            artifact["path"] = "docs/VERIFY-1/qa/../../../outside.txt"
            artifact["sha256"] = _sha(release_repo / "outside.txt")
        elif mode == "missing":
            artifact["path"] = "docs/VERIFY-1/qa/missing.png"
        elif mode == "empty":
            empty = release_repo / "docs" / "VERIFY-1" / "qa" / "empty.png"
            empty.write_bytes(b"")
            artifact["path"] = "docs/VERIFY-1/qa/empty.png"
            artifact["sha256"] = _sha(empty)
        else:
            artifact["sha256"] = "f" * 64

    _write_valid_release(release_repo, evidence_mutator=mutate)

    result = _validate(release_repo)

    assert result.passed is False
    assert result.evidence_errors
    assert any("artifact" in error for error in result.evidence_errors)


@pytest.mark.parametrize("location", ["root", "nested"])
def test_duplicate_yaml_mapping_keys_are_rejected_everywhere(
    release_repo: Path, location: str
) -> None:
    contract_path = release_repo / "release-contract.yaml"
    raw = contract_path.read_text(encoding="utf-8")
    if location == "root":
        raw += "target_branch: shadow\n"
    else:
        raw = raw.replace(
            "  ready_url: http://127.0.0.1:3000\n",
            "  ready_url: http://127.0.0.1:3000\n  command: shadow\n",
            1,
        )
    contract_path.write_text(raw, encoding="utf-8")
    _git(release_repo, "add", "release-contract.yaml")
    _git(release_repo, "commit", "-m", f"duplicate {location} contract key")

    identity = resolve_target_release_identity(
        repository_root=release_repo,
        configured_target_branch="main",
    )
    result = _validate(release_repo)

    assert any("duplicate" in error for error in identity.errors)
    assert any("duplicate" in error for error in result.evidence_errors)


@pytest.mark.parametrize(
    "location",
    ["evidence-root", "evidence-nested", "native-root", "native-nested"],
)
def test_duplicate_json_mapping_keys_are_rejected_at_every_depth(
    release_repo: Path, location: str
) -> None:
    qa_root = release_repo / "docs" / "VERIFY-1" / "qa"
    evidence_path = qa_root / "release-evidence.json"
    native_path = qa_root / "native-results.json"
    if location.startswith("evidence"):
        raw = evidence_path.read_text(encoding="utf-8")
        if location == "evidence-root":
            raw = raw.replace(
                '  "target_branch": "main",\n',
                '  "target_branch": "main",\n  "target_branch": "shadow",\n',
                1,
            )
        else:
            raw = raw.replace(
                '    "name": "native-browser-suite",\n',
                '    "name": "native-browser-suite",\n    "name": "shadow",\n',
                1,
            )
        evidence_path.write_text(raw, encoding="utf-8")
    else:
        raw = native_path.read_text(encoding="utf-8")
        if location == "native-root":
            raw = raw.replace(
                '  "target_branch": "main",\n',
                '  "target_branch": "main",\n  "target_branch": "shadow",\n',
                1,
            )
        else:
            raw = raw.replace(
                '      "status": "PASS"\n',
                '      "status": "PASS",\n      "status": "FAIL"\n',
                1,
            )
        native_path.write_text(raw, encoding="utf-8")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["runner"]["results_sha256"] = _sha(native_path)
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = _validate(release_repo)

    assert result.passed is False
    assert any("duplicate" in error for error in result.evidence_errors)


@pytest.mark.parametrize("path_kind", ["manifest", "native-results", "artifact"])
def test_release_evidence_files_must_not_be_git_ignored(
    release_repo: Path, path_kind: str
) -> None:
    ignored_path = {
        "manifest": "docs/VERIFY-1/qa/release-evidence.json",
        "native-results": "docs/VERIFY-1/qa/native-results.json",
        "artifact": "docs/VERIFY-1/qa/browser.png",
    }[path_kind]
    (release_repo / ".gitignore").write_text(ignored_path + "\n", encoding="utf-8")
    _git(release_repo, "add", ".gitignore")
    _git(release_repo, "commit", "-m", f"ignore {path_kind}")
    _write_valid_release(release_repo)

    result = _validate(release_repo)

    assert result.passed is False
    assert any(
        "Git-stageable" in error and ignored_path in error
        for error in result.evidence_errors
    )


@pytest.mark.parametrize("runtime_root", ["dist", "build", "target", ".next"])
def test_ignored_runtime_output_cannot_influence_exact_target_release(
    release_repo: Path, runtime_root: str
) -> None:
    (release_repo / ".gitignore").write_text(
        f"{runtime_root}/\n", encoding="utf-8"
    )
    _git(release_repo, "add", ".gitignore")
    _git(release_repo, "commit", "-m", "ignore runtime distribution")
    _write_valid_release(release_repo)
    runtime_file = release_repo / runtime_root / "app.js"
    runtime_file.parent.mkdir()
    runtime_file.write_text("window.falseGreen = true;\n", encoding="utf-8")

    result = _validate(release_repo)

    assert result.passed is False
    assert any(
        "differs from the approved target" in error and runtime_root in error
        for error in result.evidence_errors
    )


def test_unknown_git_evidence_status_fails_closed(
    release_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        release_contracts_module,
        "is_git_stageable_path",
        lambda _root, _path: None,
    )

    result = _validate(release_repo)

    assert result.passed is False
    assert any(
        "Git-stageable status could not be determined" in error
        for error in result.evidence_errors
    )


def test_ignored_symphony_control_and_dependency_paths_are_infrastructure(
    release_repo: Path,
) -> None:
    infrastructure_paths = (
        "kanban/.locks/claim.lock",
        ".locks/claim.lock",
        "log/worker.log",
        ".symphony/state.db",
        ".oneshot/prompt.md",
        "node_modules/pkg/index.js",
        ".venv/lib/python/site.py",
        "venv/lib/python/site.py",
        ".tox/py/bin/python",
        ".nox/tests/bin/python",
        "pkg/__pycache__/module.pyc",
        ".pytest_cache/state",
        ".ruff_cache/state",
        ".mypy_cache/state",
        ".pyright/state",
        ".cache/tool/state",
    )
    (release_repo / ".gitignore").write_text(
        "\n".join(infrastructure_paths) + "\n", encoding="utf-8"
    )
    _git(release_repo, "add", ".gitignore")
    _git(release_repo, "commit", "-m", "ignore Symphony infrastructure")
    _write_valid_release(release_repo)
    for relative in infrastructure_paths:
        path = release_repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("runtime-only\n", encoding="utf-8")

    result = _validate(release_repo)

    assert result.passed is True


def test_tracked_dependency_path_still_belongs_to_exact_target(
    release_repo: Path,
) -> None:
    dependency = release_repo / "node_modules" / "pkg" / "index.js"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("module.exports = 'target';\n", encoding="utf-8")
    _git(release_repo, "add", "-f", "node_modules/pkg/index.js")
    _git(release_repo, "commit", "-m", "track runtime dependency")
    _write_valid_release(release_repo)
    dependency.write_text("module.exports = 'workspace';\n", encoding="utf-8")

    result = _validate(release_repo)

    assert result.passed is False
    assert any(
        "node_modules/pkg/index.js" in error for error in result.evidence_errors
    )


def test_unreadable_artifact_is_a_release_error_not_an_exception(
    release_repo: Path,
) -> None:
    artifact = release_repo / "docs" / "VERIFY-1" / "qa" / "browser.png"
    artifact.chmod(0)
    try:
        try:
            artifact.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("platform privileges do not enforce unreadable mode bits")

        result = _validate(release_repo)
    finally:
        artifact.chmod(0o600)

    assert result.passed is False
    assert any(
        "artifact cannot be read" in error for error in result.evidence_errors
    )
