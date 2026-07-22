"""Persisted recovery proofs rebuilt from bounded Git observations."""

from __future__ import annotations

import inspect
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from symphony.aidt_worktree import (
    PreparedRecoveryProof,
    ReadyRecoveryProof,
    RemovedRecoveryProof,
    GitCommandResult,
    add_worktree,
    default_binary_runner,
    observe_repository_identity,
    observe_repository_state,
    prove_prepared_recovery,
    prove_ready_recovery,
    prove_removed_recovery,
    remove_worktree,
    validate_create_delta,
    validate_remove_delta,
)
from symphony.aidt_worktree.contract import AidtWorktreeFailure


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _repository(tmp_path: Path) -> tuple[Path, str]:
    checkout = tmp_path / "service"
    checkout.mkdir()
    _git(checkout, "init", "-b", "aidt-prd")
    _git(checkout, "config", "user.name", "Fixture")
    _git(checkout, "config", "user.email", "fixture@example.test")
    (checkout / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (checkout / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(checkout, "add", ".gitignore", "tracked.txt")
    _git(checkout, "commit", "-m", "base")
    revision = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    _git(checkout, "remote", "add", "origin", "https://fixture.invalid/repository.git")
    _git(checkout, "update-ref", "refs/remotes/origin/aidt-prd", revision)
    return checkout.resolve(), revision


def _forge_snapshot(snapshot: object, **changes: object) -> object:
    forged = object.__new__(type(snapshot))
    for name in snapshot.__dataclass_fields__:  # type: ignore[attr-defined]
        object.__setattr__(forged, name, changes.get(name, getattr(snapshot, name)))
    return forged


def _target_fixture(tmp_path: Path, *, phase: str = "s2") -> SimpleNamespace:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "workspaces" / "A20-1188--viewer-api").resolve()
    workspace.parent.mkdir()
    s1 = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:01Z"
    )
    add_worktree(identity, branch, workspace, revision)
    state = observe_repository_state(
        identity, phase, "b" * 64, branch, workspace, "2026-07-21T01:02:02Z"
    )
    return SimpleNamespace(
        checkout=checkout,
        revision=revision,
        identity=identity,
        branch=branch,
        workspace=workspace,
        s1=s1,
        state=state,
    )


def _unrelated_commit(checkout: Path, revision: str) -> str:
    tree = _git(checkout, "rev-parse", f"{revision}^{{tree}}").stdout.decode().strip()
    return _git(checkout, "commit-tree", tree, "-m", "unrelated").stdout.decode().strip()


def _assert_failure(category: str, operation: Any) -> None:
    with pytest.raises(AidtWorktreeFailure) as failure:
        operation()
    assert failure.value.category == category


def _collection_race_runner(checkout: Path, revision: str) -> Any:
    changed = False

    def runner(argv: tuple[str, ...], *args: Any) -> GitCommandResult:
        nonlocal changed
        result = default_binary_runner(argv, *args)
        listing = "for-each-ref" in argv and "%(objectname)" in " ".join(argv)
        if listing and not changed:
            _git(checkout, "update-ref", "refs/heads/fix/A20-9999", revision)
            changed = True
        return result

    return runner


def _ticket_race_runner(workspace: Path) -> Any:
    changed = False

    def runner(argv: tuple[str, ...], cwd: Path, *args: Any) -> GitCommandResult:
        nonlocal changed
        result = default_binary_runner(argv, cwd, *args)
        if cwd == workspace and "status" in argv and not changed:
            (workspace / "raced.txt").write_text("changed\n", encoding="utf-8")
            changed = True
        return result

    return runner


def _path_race_runner(checkout: Path, workspace: Path) -> Any:
    armed = False
    changed = False

    def runner(
        argv: tuple[str, ...], cwd: Path, *args: Any
    ) -> GitCommandResult:
        nonlocal armed, changed
        listing = "for-each-ref" in argv and "%(objectname)" in " ".join(argv)
        if armed and not changed:
            workspace.write_text("raced\n", encoding="utf-8")
            changed = True
        result = default_binary_runner(argv, cwd, *args)
        if cwd == checkout and listing and not armed:
            armed = True
        return result

    return runner


def _ready_path_race_runner(workspace: Path, moved: Path) -> Any:
    changed = False

    def runner(
        argv: tuple[str, ...], cwd: Path, *args: Any
    ) -> GitCommandResult:
        nonlocal changed
        result = default_binary_runner(argv, cwd, *args)
        if cwd == workspace and "merge-base" in argv and not changed:
            workspace.rename(moved)
            workspace.write_text("raced\n", encoding="utf-8")
            changed = True
        return result

    return runner


def _closing_path_race_runner(
    checkout: Path, workspace: Path, *, existing: bool
) -> Any:
    listings = 0

    def runner(
        argv: tuple[str, ...], cwd: Path, *args: Any
    ) -> GitCommandResult:
        nonlocal listings
        result = default_binary_runner(argv, cwd, *args)
        listing = "for-each-ref" in argv and "%(objectname)" in " ".join(argv)
        if cwd == checkout and listing:
            listings += 1
            if listings == 2:
                if existing:
                    workspace.rename(workspace.with_name("moved-ticket"))
                workspace.write_text("raced\n", encoding="utf-8")
        return result

    return runner


def test_prepared_recovery_accepts_exact_persisted_s1_absence(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    binding = "b" * 64
    branch = "fix/A20-1188"
    workspace = (tmp_path / "workspaces" / "A20-1188--viewer-api").resolve()
    persisted = observe_repository_state(
        identity, "s1", binding, branch, workspace, "2026-07-21T01:02:03Z"
    ).snapshot

    proof = prove_prepared_recovery(
        identity,
        persisted,
        binding,
        branch,
        workspace,
        "2026-07-21T01:02:04Z",
        runner=default_binary_runner,
    )

    assert type(proof) is PreparedRecoveryProof
    assert proof.state.snapshot.phase == "s1"
    assert proof.state.snapshot.observed_at == "2026-07-21T01:02:04Z"
    assert proof.ticket is None
    assert proof.create_delta_digest is None


def test_prepared_recovery_accepts_completed_clean_add_at_persisted_base(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    binding = "b" * 64
    branch = "fix/A20-1188"
    workspace = (tmp_path / "workspaces" / "A20-1188--viewer-api").resolve()
    workspace.parent.mkdir()
    before = observe_repository_state(
        identity, "s1", binding, branch, workspace, "2026-07-21T01:02:03Z"
    )
    add_worktree(identity, branch, workspace, revision)

    proof = prove_prepared_recovery(
        identity,
        before.snapshot,
        binding,
        branch,
        workspace,
        "2026-07-21T01:02:04Z",
    )

    assert proof.state.snapshot.phase == "s2"
    assert proof.ticket is not None
    assert proof.ticket.head == revision
    assert proof.ticket.clean is True
    assert proof.ticket.no_upstream is True
    assert proof.create_delta_digest == validate_create_delta(before, proof.state)


def test_ready_recovery_resumes_unchanged_clean_worktree(tmp_path: Path) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    binding = "b" * 64
    branch = "fix/A20-1188"
    workspace = (tmp_path / "workspaces" / "A20-1188--viewer-api").resolve()
    workspace.parent.mkdir()
    add_worktree(identity, branch, workspace, revision)
    persisted = observe_repository_state(
        identity, "s2", binding, branch, workspace, "2026-07-21T01:02:03Z"
    ).snapshot

    proof = prove_ready_recovery(
        identity,
        persisted,
        binding,
        branch,
        workspace,
        "2026-07-21T01:02:04Z",
        phase="resume",
    )

    assert type(proof) is ReadyRecoveryProof
    assert proof.state.snapshot.phase == "resume"
    assert proof.state.snapshot.observed_at == "2026-07-21T01:02:04Z"
    assert proof.ticket.head == revision
    assert proof.ticket.clean is True


def test_removed_recovery_accepts_retained_branch_after_physical_remove(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    binding = "b" * 64
    branch = "fix/A20-1188"
    workspace = (tmp_path / "workspaces" / "A20-1188--viewer-api").resolve()
    workspace.parent.mkdir()
    add_worktree(identity, branch, workspace, revision)
    before = observe_repository_state(
        identity,
        "cleanup_pre",
        binding,
        branch,
        workspace,
        "2026-07-21T01:02:03Z",
    )
    remove_worktree(identity, workspace)

    proof = prove_removed_recovery(
        identity,
        before.snapshot,
        revision,
        binding,
        branch,
        workspace,
        "2026-07-21T01:02:04Z",
    )

    assert type(proof) is RemovedRecoveryProof
    assert proof.state.snapshot.phase == "cleanup_post"
    assert proof.state.snapshot.observed_at == "2026-07-21T01:02:04Z"
    assert proof.remove_delta_digest == validate_remove_delta(before, proof.state)


def test_prepared_recovery_rejects_collection_change_inside_proof(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    persisted = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:01Z"
    ).snapshot

    _assert_failure(
        "identity_invalid",
        lambda: prove_prepared_recovery(
            identity, persisted, "b" * 64, branch, workspace,
            "2026-07-21T01:02:02Z",
            runner=_collection_race_runner(checkout, revision),
        ),
    )


@pytest.mark.parametrize("phase", ["resume", "cleanup_pre"])
def test_ready_recovery_rejects_ticket_change_inside_proof(
    tmp_path: Path,
    phase: str,
) -> None:
    fixture = _target_fixture(tmp_path)

    _assert_failure(
        "content_invalid",
        lambda: prove_ready_recovery(
            fixture.identity, fixture.state.snapshot, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
            phase=phase,  # type: ignore[arg-type]
            runner=_ticket_race_runner(fixture.workspace),
        ),
    )


def test_prepared_recovery_rejects_path_change_inside_proof(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    persisted = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:01Z"
    ).snapshot

    _assert_failure(
        "collision",
        lambda: prove_prepared_recovery(
            identity, persisted, "b" * 64, branch, workspace,
            "2026-07-21T01:02:02Z",
            runner=_path_race_runner(checkout, workspace),
        ),
    )


def test_removed_recovery_rejects_path_change_inside_proof(
    tmp_path: Path,
) -> None:
    fixture = _target_fixture(tmp_path, phase="cleanup_pre")
    remove_worktree(fixture.identity, fixture.workspace)

    _assert_failure(
        "collision",
        lambda: prove_removed_recovery(
            fixture.identity, fixture.state.snapshot, fixture.revision,
            "b" * 64, fixture.branch, fixture.workspace,
            "2026-07-21T01:02:05Z",
            runner=_path_race_runner(fixture.checkout, fixture.workspace),
        ),
    )


def test_prepared_completed_recovery_rejects_ticket_change_inside_proof(
    tmp_path: Path,
) -> None:
    fixture = _target_fixture(tmp_path)
    _assert_failure(
        "content_invalid",
        lambda: prove_prepared_recovery(
            fixture.identity, fixture.s1.snapshot, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
            runner=_ticket_race_runner(fixture.workspace),
        ),
    )


@pytest.mark.parametrize("phase", ["resume", "cleanup_pre"])
def test_ready_recovery_rejects_collection_change_inside_proof(
    tmp_path: Path,
    phase: str,
) -> None:
    fixture = _target_fixture(tmp_path)
    _assert_failure(
        "identity_invalid",
        lambda: prove_ready_recovery(
            fixture.identity, fixture.state.snapshot, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
            phase=phase,  # type: ignore[arg-type]
            runner=_collection_race_runner(fixture.checkout, fixture.revision),
        ),
    )


@pytest.mark.parametrize("phase", ["resume", "cleanup_pre"])
def test_ready_recovery_rejects_path_change_inside_proof(
    tmp_path: Path,
    phase: str,
) -> None:
    fixture = _target_fixture(tmp_path)
    _assert_failure(
        "collision",
        lambda: prove_ready_recovery(
            fixture.identity, fixture.state.snapshot, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
            phase=phase,  # type: ignore[arg-type]
            runner=_ready_path_race_runner(
                fixture.workspace, (tmp_path / "moved-ticket").resolve()
            ),
        ),
    )


def test_removed_recovery_rejects_collection_change_inside_proof(
    tmp_path: Path,
) -> None:
    fixture = _target_fixture(tmp_path, phase="cleanup_pre")
    remove_worktree(fixture.identity, fixture.workspace)
    _assert_failure(
        "identity_invalid",
        lambda: prove_removed_recovery(
            fixture.identity, fixture.state.snapshot, fixture.revision,
            "b" * 64, fixture.branch, fixture.workspace,
            "2026-07-21T01:02:05Z",
            runner=_collection_race_runner(fixture.checkout, fixture.revision),
        ),
    )


@pytest.mark.parametrize("operation", ["prepared", "ready", "cleanup_pre", "removed"])
def test_recovery_rejects_path_change_during_closing_collection(
    tmp_path: Path,
    operation: str,
) -> None:
    if operation == "prepared":
        checkout, revision = _repository(tmp_path)
        identity = observe_repository_identity(checkout, "viewer-api")
        branch = "fix/A20-1188"
        workspace = (tmp_path / "ticket").resolve()
        state = observe_repository_state(
            identity, "s1", "b" * 64, branch, workspace,
            "2026-07-21T01:02:01Z",
        )
    else:
        fixture = _target_fixture(
            tmp_path, phase="cleanup_pre" if operation == "removed" else "s2"
        )
        checkout, revision = fixture.checkout, fixture.revision
        identity, branch, workspace, state = (
            fixture.identity, fixture.branch, fixture.workspace, fixture.state
        )
        if operation == "removed":
            remove_worktree(identity, workspace)
    runner = _closing_path_race_runner(
        checkout, workspace, existing=operation in {"ready", "cleanup_pre"}
    )

    def call() -> object:
        if operation in {"ready", "cleanup_pre"}:
            return prove_ready_recovery(
                identity, state.snapshot, "b" * 64, branch, workspace,
                "2026-07-21T01:02:05Z",
                phase="resume" if operation == "ready" else "cleanup_pre",
                runner=runner,
            )
        if operation == "removed":
            return prove_removed_recovery(
                identity, state.snapshot, revision, "b" * 64, branch, workspace,
                "2026-07-21T01:02:05Z", runner=runner,
            )
        return prove_prepared_recovery(
            identity, state.snapshot, "b" * 64, branch, workspace,
            "2026-07-21T01:02:05Z", runner=runner,
        )

    _assert_failure("collision", call)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: PreparedRecoveryProof(None, None, None),  # type: ignore[arg-type]
        lambda: ReadyRecoveryProof(None, None),  # type: ignore[arg-type]
        lambda: RemovedRecoveryProof(None, "a" * 64),  # type: ignore[arg-type]
    ],
)
def test_recovery_result_dtos_totalize_malformed_state_as_protocol_invalid(
    factory: object,
) -> None:
    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        factory()  # type: ignore[operator]


def test_ready_recovery_totalizes_unhashable_phase_as_protocol_invalid(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    add_worktree(identity, branch, workspace, revision)
    persisted = observe_repository_state(
        identity, "s2", "b" * 64, branch, workspace, "2026-07-21T01:02:03Z"
    ).snapshot

    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        prove_ready_recovery(
            identity,
            persisted,
            "b" * 64,
            branch,
            workspace,
            "2026-07-21T01:02:04Z",
            phase=[],  # type: ignore[arg-type]
        )


def test_ready_recovery_rejects_s2_target_not_created_at_fixed_base(
    tmp_path: Path,
) -> None:
    checkout, base = _repository(tmp_path)
    (checkout / "tracked.txt").write_text("descendant\n", encoding="utf-8")
    _git(checkout, "add", "tracked.txt")
    _git(checkout, "commit", "-m", "descendant")
    descendant = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    assert descendant != base
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    add_worktree(identity, branch, workspace, descendant)
    persisted = observe_repository_state(
        identity, "s2", "b" * 64, branch, workspace, "2026-07-21T01:02:03Z"
    ).snapshot

    with pytest.raises(AidtWorktreeFailure, match="base_invalid"):
        prove_ready_recovery(
            identity,
            persisted,
            "b" * 64,
            branch,
            workspace,
            "2026-07-21T01:02:04Z",
            phase="resume",
        )


def test_prepared_recovery_rejects_each_half_null_persisted_target_shape(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    persisted = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:03Z"
    ).snapshot
    malformed = (
        _forge_snapshot(persisted, target_ref_sha=revision),
        _forge_snapshot(persisted, target_registration_digest="a" * 64),
    )

    for snapshot in malformed:
        with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
            prove_prepared_recovery(
                identity,
                snapshot,  # type: ignore[arg-type]
                "b" * 64,
                branch,
                workspace,
                "2026-07-21T01:02:04Z",
            )


def test_prepared_result_totalizes_state_with_malformed_snapshot(
    tmp_path: Path,
) -> None:
    checkout, _revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    state = observe_repository_state(
        identity,
        "s1",
        "b" * 64,
        "fix/A20-1188",
        (tmp_path / "ticket").resolve(),
        "2026-07-21T01:02:03Z",
    )
    malformed = replace(state, snapshot=None)  # type: ignore[arg-type]

    with pytest.raises(AidtWorktreeFailure, match="protocol_invalid"):
        PreparedRecoveryProof(malformed, None, None)


def test_recovery_result_dto_totalizes_nested_collection_and_ticket_values(
    tmp_path: Path,
) -> None:
    fixture = _target_fixture(tmp_path)
    proof = prove_prepared_recovery(
        fixture.identity, fixture.s1.snapshot, "b" * 64, fixture.branch,
        fixture.workspace, "2026-07-21T01:02:03Z",
    )
    assert proof.ticket is not None
    target_ref = next(
        item for item in proof.state.refs
        if item.name == f"refs/heads/{fixture.branch}"
    )
    target_registration = next(
        item for item in proof.state.registrations
        if item.path == fixture.workspace
    )

    def with_ref(value: object) -> Any:
        return tuple(value if item == target_ref else item for item in proof.state.refs)

    def with_registration(value: object) -> Any:
        return tuple(
            value if item == target_registration else item
            for item in proof.state.registrations
        )

    malformed_states = (
        replace(proof.state, refs=None),  # type: ignore[arg-type]
        replace(proof.state, refs=list(proof.state.refs)),  # type: ignore[arg-type]
        replace(proof.state, refs=(object(),)),  # type: ignore[arg-type]
        replace(proof.state, registrations=None),  # type: ignore[arg-type]
        replace(
            proof.state,
            registrations=list(proof.state.registrations),  # type: ignore[arg-type]
        ),
        replace(proof.state, registrations=(object(),)),  # type: ignore[arg-type]
        replace(proof.state, target_branch=[]),  # type: ignore[arg-type]
        replace(proof.state, target_branch="bad"),
        replace(proof.state, target_path=[]),  # type: ignore[arg-type]
        replace(proof.state, target_path=Path("relative")),
        replace(proof.state, target_upstream=[]),  # type: ignore[arg-type]
        replace(proof.state, target_upstream="bad"),
        replace(
            proof.state,
            refs=with_ref(replace(target_ref, name=[])),  # type: ignore[arg-type]
        ),
        replace(proof.state, refs=with_ref(replace(target_ref, name="bad"))),
        replace(proof.state, refs=with_ref(replace(target_ref, sha=[]))),  # type: ignore[arg-type]
        replace(proof.state, refs=with_ref(replace(target_ref, sha="bad"))),
        replace(
            proof.state,
            refs=with_ref(replace(target_ref, upstream=[])),  # type: ignore[arg-type]
        ),
        replace(
            proof.state,
            registrations=with_registration(
                replace(target_registration, path=Path("relative"))
            ),
        ),
        replace(
            proof.state,
            registrations=with_registration(replace(target_registration, head="bad")),
        ),
        replace(
            proof.state,
            registrations=with_registration(replace(target_registration, branch="bad")),
        ),
        replace(
            proof.state,
            registrations=with_registration(
                replace(target_registration, detached=1)  # type: ignore[arg-type]
            ),
        ),
        replace(
            proof.state,
            registrations=with_registration(
                replace(target_registration, locked=1)  # type: ignore[arg-type]
            ),
        ),
        replace(
            proof.state,
            registrations=with_registration(
                replace(target_registration, prunable=1)  # type: ignore[arg-type]
            ),
        ),
        replace(
            proof.state,
            snapshot=_forge_snapshot(proof.state.snapshot, refs_digest="a" * 64),
        ),
        replace(
            proof.state,
            snapshot=_forge_snapshot(proof.state.snapshot, refs_count=2_500),
        ),
        replace(
            proof.state,
            snapshot=_forge_snapshot(proof.state.snapshot, registry_digest="a" * 64),
        ),
        replace(
            proof.state,
            snapshot=_forge_snapshot(proof.state.snapshot, protected_digest="a" * 64),
        ),
        replace(
            proof.state,
            snapshot=_forge_snapshot(proof.state.snapshot, base_ref_sha="a" * 40),
        ),
        replace(
            proof.state,
            snapshot=_forge_snapshot(proof.state.snapshot, target_ref_sha="a" * 40),
        ),
        replace(
            proof.state,
            snapshot=_forge_snapshot(
                proof.state.snapshot, target_registration_digest="a" * 64
            ),
        ),
    )
    for state in malformed_states:
        _assert_failure(
            "protocol_invalid",
            lambda state=state: PreparedRecoveryProof(
                state, proof.ticket, proof.create_delta_digest
            ),
        )

    malformed_tickets = (
        replace(proof.ticket, path=[]),  # type: ignore[arg-type]
        replace(proof.ticket, path=Path("relative")),
        replace(proof.ticket, head=[]),  # type: ignore[arg-type]
        replace(proof.ticket, head="bad"),
        replace(proof.ticket, branch=[]),  # type: ignore[arg-type]
        replace(proof.ticket, branch="bad"),
        replace(proof.ticket, status_digest=[]),  # type: ignore[arg-type]
        replace(proof.ticket, status_digest="A" * 64),
        replace(proof.ticket, status_digest="z" * 64),
        replace(proof.ticket, clean=1),  # type: ignore[arg-type]
        replace(proof.ticket, clean=False),
        replace(proof.ticket, no_upstream=1),  # type: ignore[arg-type]
    )
    for ticket in malformed_tickets:
        _assert_failure(
            "protocol_invalid",
            lambda ticket=ticket: PreparedRecoveryProof(
                proof.state, ticket, proof.create_delta_digest
            ),
        )


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "broken"])
def test_prepared_recovery_rejects_every_path_only_artifact(
    tmp_path: Path,
    kind: str,
) -> None:
    checkout, _revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    persisted = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:03Z"
    ).snapshot
    if kind == "file":
        workspace.write_text("collision\n", encoding="utf-8")
    elif kind == "directory":
        workspace.mkdir()
    elif kind == "symlink":
        workspace.symlink_to(checkout, target_is_directory=True)
    else:
        workspace.symlink_to(tmp_path / "missing", target_is_directory=True)

    _assert_failure(
        "collision",
        lambda: prove_prepared_recovery(
            identity, persisted, "b" * 64, branch, workspace,
            "2026-07-21T01:02:04Z",
        ),
    )


@pytest.mark.parametrize(
    ("case", "category"),
    [
        ("branch", "collision"),
        ("registration", "collision"),
        ("remote", "collision"),
        ("wrong_sha", "collision"),
        ("upstream", "collision"),
        ("detached", "collision"),
        ("locked", "collision"),
        ("prunable", "collision"),
        ("dirty_tracked", "content_invalid"),
        ("dirty_untracked", "content_invalid"),
        ("dirty_ignored", "content_invalid"),
        ("unrelated_ref", "identity_invalid"),
        ("unrelated_registration", "identity_invalid"),
        ("protected", "identity_invalid"),
        ("root", "content_invalid"),
        ("fixed", "base_invalid"),
    ],
)
def test_prepared_recovery_rejects_collision_content_and_projection_drift(
    tmp_path: Path,
    case: str,
    category: str,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    persisted = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:03Z"
    ).snapshot
    other = _unrelated_commit(checkout, revision)
    if case in {"branch", "remote", "unrelated_ref", "fixed"}:
        name = {
            "branch": f"refs/heads/{branch}",
            "remote": f"refs/remotes/backup/{branch}",
            "unrelated_ref": "refs/heads/fix/A20-9999",
            "fixed": "refs/remotes/origin/aidt-prd",
        }[case]
        _git(checkout, "update-ref", name, other if case == "fixed" else revision)
    elif case == "detached":
        _git(checkout, "worktree", "add", "--detach", str(workspace), revision)
        _git(checkout, "update-ref", f"refs/heads/{branch}", revision)
    elif case in {"unrelated_registration", "protected"}:
        other_branch = "release/proof" if case == "protected" else "fix/A20-9999"
        _git(checkout, "worktree", "add", "-b", other_branch, str(tmp_path / case), revision)
    else:
        add_worktree(identity, branch, workspace, other if case == "wrong_sha" else revision)
        if case == "registration":
            _git(checkout, "update-ref", "-d", f"refs/heads/{branch}")
        elif case == "upstream":
            _git(checkout, "branch", "--set-upstream-to=origin/aidt-prd", branch)
        elif case == "locked":
            _git(checkout, "worktree", "lock", str(workspace))
        elif case == "prunable":
            shutil.rmtree(workspace)
        elif case == "dirty_tracked":
            (workspace / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        elif case == "dirty_untracked":
            (workspace / "new.txt").write_text("dirty\n", encoding="utf-8")
        elif case == "dirty_ignored":
            (workspace / "ignored").mkdir()
            (workspace / "ignored" / "data.txt").write_text("dirty\n", encoding="utf-8")
    if case == "root":
        (checkout / "tracked.txt").write_text("root drift\n", encoding="utf-8")

    _assert_failure(
        category,
        lambda: prove_prepared_recovery(
            identity, persisted, "b" * 64, branch, workspace,
            "2026-07-21T01:02:04Z",
        ),
    )


@pytest.mark.parametrize(
    ("case", "phase"),
    [
        ("clean", "resume"),
        ("dirty_tracked", "resume"),
        ("dirty_untracked", "resume"),
        ("dirty_ignored", "resume"),
        ("descendant", "resume"),
        ("dirty_descendant", "resume"),
        ("clean", "cleanup_pre"),
        ("descendant", "cleanup_pre"),
    ],
)
def test_ready_recovery_accepts_resume_and_cleanup_shapes(
    tmp_path: Path,
    case: str,
    phase: str,
) -> None:
    fixture = _target_fixture(tmp_path)
    if case in {"descendant", "dirty_descendant"}:
        (fixture.workspace / "tracked.txt").write_text("descendant\n", encoding="utf-8")
        _git(fixture.workspace, "add", "tracked.txt")
        _git(fixture.workspace, "commit", "-m", "descendant")
    if case == "dirty_tracked":
        (fixture.workspace / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    elif case in {"dirty_untracked", "dirty_descendant"}:
        (fixture.workspace / "new.txt").write_text("dirty\n", encoding="utf-8")
    elif case == "dirty_ignored":
        (fixture.workspace / "ignored").mkdir()
        (fixture.workspace / "ignored" / "data.txt").write_text("dirty\n", encoding="utf-8")

    proof = prove_ready_recovery(
        fixture.identity,
        fixture.state.snapshot,
        "b" * 64,
        fixture.branch,
        fixture.workspace,
        "2026-07-21T01:02:05Z",
        phase=phase,  # type: ignore[arg-type]
    )

    assert proof.state.snapshot.phase == phase
    assert proof.ticket.clean is (case in {"clean", "descendant"})


@pytest.mark.parametrize("phase", ["resume", "cleanup_pre"])
@pytest.mark.parametrize(
    ("case", "category"),
    [
        ("remote", "collision"),
        ("upstream", "collision"),
        ("locked", "collision"),
        ("detached", "collision"),
        ("prunable", "collision"),
        ("path_mismatch", "collision"),
        ("branch_mismatch", "collision"),
        ("unrelated_ref", "identity_invalid"),
        ("unrelated_registration", "identity_invalid"),
        ("protected", "identity_invalid"),
        ("root_head", "identity_invalid"),
        ("root_symbolic", "identity_invalid"),
        ("root", "content_invalid"),
        ("fixed", "base_invalid"),
        ("nonancestor", "base_invalid"),
    ],
)
def test_ready_recovery_rejects_target_and_unrelated_drift_in_both_modes(
    tmp_path: Path,
    phase: str,
    case: str,
    category: str,
) -> None:
    fixture = _target_fixture(tmp_path)
    other = _unrelated_commit(fixture.checkout, fixture.revision)
    if case == "remote":
        _git(fixture.checkout, "update-ref", f"refs/remotes/backup/{fixture.branch}", fixture.revision)
    elif case == "upstream":
        _git(fixture.checkout, "branch", "--set-upstream-to=origin/aidt-prd", fixture.branch)
    elif case == "locked":
        _git(fixture.checkout, "worktree", "lock", str(fixture.workspace))
    elif case == "detached":
        _git(fixture.workspace, "checkout", "--detach")
    elif case == "prunable":
        shutil.rmtree(fixture.workspace)
    elif case == "path_mismatch":
        _git(
            fixture.checkout, "worktree", "move", str(fixture.workspace),
            str(tmp_path / "moved-ticket"),
        )
    elif case == "branch_mismatch":
        _git(fixture.workspace, "branch", "-m", "fix/A20-9999")
    elif case == "unrelated_ref":
        _git(fixture.checkout, "update-ref", "refs/heads/fix/A20-9999", fixture.revision)
    elif case in {"unrelated_registration", "protected"}:
        other_branch = "release/proof" if case == "protected" else "fix/A20-9999"
        _git(
            fixture.checkout, "worktree", "add", "-b", other_branch,
            str(tmp_path / case), fixture.revision,
        )
    elif case == "root_head":
        (fixture.checkout / "tracked.txt").write_text("next\n", encoding="utf-8")
        _git(fixture.checkout, "add", "tracked.txt")
        _git(fixture.checkout, "commit", "-m", "root head drift")
    elif case == "root_symbolic":
        _git(fixture.checkout, "checkout", "--detach")
    elif case == "root":
        (fixture.checkout / "tracked.txt").write_text("root drift\n", encoding="utf-8")
    elif case == "fixed":
        _git(fixture.checkout, "update-ref", "refs/remotes/origin/aidt-prd", other)
    else:
        _git(fixture.workspace, "reset", "--hard", other)

    _assert_failure(
        category,
        lambda: prove_ready_recovery(
            fixture.identity, fixture.state.snapshot, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
            phase=phase,  # type: ignore[arg-type]
        ),
    )


@pytest.mark.parametrize("phase", ["resume", "cleanup_pre"])
@pytest.mark.parametrize("component", ["ref", "registration", "ticket"])
def test_ready_recovery_rejects_each_target_head_disagreement(
    tmp_path: Path,
    phase: str,
    component: str,
) -> None:
    fixture = _target_fixture(tmp_path)
    other = _unrelated_commit(fixture.checkout, fixture.revision)

    def runner(
        argv: tuple[str, ...], cwd: Path, *args: Any
    ) -> GitCommandResult:
        result = default_binary_runner(argv, cwd, *args)
        output = result.stdout
        if component == "ref" and cwd == fixture.checkout and "%(objectname)" in " ".join(argv):
            before = f"refs/heads/{fixture.branch}\t{fixture.revision}\t".encode()
            output = output.replace(before, f"refs/heads/{fixture.branch}\t{other}\t".encode())
        elif component == "registration" and cwd == fixture.checkout and "worktree" in argv:
            before = f"worktree {fixture.workspace}\0HEAD {fixture.revision}".encode()
            output = output.replace(before, f"worktree {fixture.workspace}\0HEAD {other}".encode())
        elif component == "ticket" and cwd == fixture.workspace and "HEAD^{commit}" in argv:
            output = f"{other}\n".encode()
        return replace(result, stdout=output)

    _assert_failure(
        "collision",
        lambda: prove_ready_recovery(
            fixture.identity, fixture.state.snapshot, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
            phase=phase,  # type: ignore[arg-type]
            runner=runner,
        ),
    )


@pytest.mark.parametrize("case", ["tracked", "untracked", "ignored"])
def test_cleanup_entry_rejects_every_dirty_ticket_shape(
    tmp_path: Path,
    case: str,
) -> None:
    fixture = _target_fixture(tmp_path)
    path = fixture.workspace / ("tracked.txt" if case == "tracked" else "new.txt")
    if case == "ignored":
        (fixture.workspace / "ignored").mkdir()
        path = fixture.workspace / "ignored" / "data.txt"
    path.write_text("dirty\n", encoding="utf-8")
    _assert_failure(
        "content_invalid",
        lambda: prove_ready_recovery(
            fixture.identity, fixture.state.snapshot, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
            phase="cleanup_pre",
        ),
    )


@pytest.mark.parametrize("phase", ["resume", "cleanup_pre"])
def test_ready_recovery_rejects_same_status_ignored_root_content_change(
    tmp_path: Path,
    phase: str,
) -> None:
    fixture = _target_fixture(tmp_path)
    ignored = fixture.checkout / "ignored"
    ignored.mkdir()
    content = ignored / "data.txt"
    content.write_text("same-a\n", encoding="utf-8")
    persisted = observe_repository_state(
        fixture.identity, "s2", "b" * 64, fixture.branch,
        fixture.workspace, "2026-07-21T01:02:03Z",
    ).snapshot
    content.write_text("same-b\n", encoding="utf-8")

    _assert_failure(
        "content_invalid",
        lambda: prove_ready_recovery(
            fixture.identity, persisted, "b" * 64, fixture.branch,
            fixture.workspace, "2026-07-21T01:02:05Z",
            phase=phase,  # type: ignore[arg-type]
        ),
    )


@pytest.mark.parametrize(
    ("case", "category"),
    [
        ("registration", "collision"),
        ("mismatched_registration", "collision"),
        ("branch_absent", "collision"),
        ("branch_wrong", "collision"),
        ("upstream", "collision"),
        ("path_file", "collision"),
        ("path_directory", "collision"),
        ("path_symlink", "collision"),
        ("path_broken", "collision"),
        ("remote", "collision"),
        ("unrelated_ref", "identity_invalid"),
        ("unrelated_registration", "identity_invalid"),
        ("protected", "identity_invalid"),
        ("root_head", "identity_invalid"),
        ("root_symbolic", "identity_invalid"),
        ("root", "content_invalid"),
        ("fixed", "base_invalid"),
        ("retained_mismatch", "base_invalid"),
    ],
)
def test_removed_recovery_rejects_incomplete_and_drifted_shapes(
    tmp_path: Path,
    case: str,
    category: str,
) -> None:
    fixture = _target_fixture(tmp_path, phase="cleanup_pre")
    if case != "registration":
        remove_worktree(fixture.identity, fixture.workspace)
    other = _unrelated_commit(fixture.checkout, fixture.revision)
    retained = other if case == "retained_mismatch" else fixture.revision
    if case == "branch_absent":
        _git(fixture.checkout, "update-ref", "-d", f"refs/heads/{fixture.branch}")
    elif case == "branch_wrong":
        _git(fixture.checkout, "update-ref", f"refs/heads/{fixture.branch}", other)
    elif case == "upstream":
        _git(fixture.checkout, "branch", "--set-upstream-to=origin/aidt-prd", fixture.branch)
    elif case.startswith("path_"):
        kind = case.removeprefix("path_")
        if kind == "file":
            fixture.workspace.write_text("collision\n", encoding="utf-8")
        elif kind == "directory":
            fixture.workspace.mkdir()
        elif kind == "symlink":
            fixture.workspace.symlink_to(fixture.checkout, target_is_directory=True)
        else:
            fixture.workspace.symlink_to(tmp_path / "missing", target_is_directory=True)
    elif case == "remote":
        _git(fixture.checkout, "update-ref", f"refs/remotes/backup/{fixture.branch}", fixture.revision)
    elif case == "unrelated_ref":
        _git(fixture.checkout, "update-ref", "refs/heads/fix/A20-9999", fixture.revision)
    elif case == "mismatched_registration":
        _git(
            fixture.checkout, "worktree", "add", "-b", "fix/A20-9999",
            str(fixture.workspace), fixture.revision,
        )
    elif case in {"unrelated_registration", "protected"}:
        other_branch = "release/proof" if case == "protected" else "fix/A20-9999"
        _git(
            fixture.checkout, "worktree", "add", "-b", other_branch,
            str(tmp_path / case), fixture.revision,
        )
    elif case == "root_head":
        (fixture.checkout / "tracked.txt").write_text("next\n", encoding="utf-8")
        _git(fixture.checkout, "add", "tracked.txt")
        _git(fixture.checkout, "commit", "-m", "root head drift")
    elif case == "root_symbolic":
        _git(fixture.checkout, "checkout", "--detach")
    elif case == "root":
        (fixture.checkout / "tracked.txt").write_text("root drift\n", encoding="utf-8")
    elif case == "fixed":
        _git(fixture.checkout, "update-ref", "refs/remotes/origin/aidt-prd", other)

    _assert_failure(
        category,
        lambda: prove_removed_recovery(
            fixture.identity, fixture.state.snapshot, retained, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
        ),
    )


def test_all_recovery_proofs_issue_no_mutation_or_network_command(
    tmp_path: Path,
) -> None:
    checkout, revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    s1 = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:01Z"
    )
    requests: list[tuple[str, ...]] = []
    forbidden = {"fetch", "add", "remove", "checkout", "reset", "rebase", "prune"}

    def spy(argv: tuple[str, ...], *args: Any) -> GitCommandResult:
        requests.append(argv)
        assert forbidden.isdisjoint(argv)
        return default_binary_runner(argv, *args)

    prove_prepared_recovery(
        identity, s1.snapshot, "b" * 64, branch, workspace,
        "2026-07-21T01:02:02Z", runner=spy,
    )
    add_worktree(identity, branch, workspace, revision)
    s2 = observe_repository_state(
        identity, "s2", "b" * 64, branch, workspace, "2026-07-21T01:02:03Z"
    )
    prove_ready_recovery(
        identity, s2.snapshot, "b" * 64, branch, workspace,
        "2026-07-21T01:02:04Z", phase="cleanup_pre", runner=spy,
    )
    cleanup = observe_repository_state(
        identity, "cleanup_pre", "b" * 64, branch, workspace,
        "2026-07-21T01:02:05Z",
    )
    remove_worktree(identity, workspace)
    prove_removed_recovery(
        identity, cleanup.snapshot, revision, "b" * 64, branch, workspace,
        "2026-07-21T01:02:06Z", runner=spy,
    )
    assert requests


def test_removed_recovery_rejects_already_complete_persisted_phase(
    tmp_path: Path,
) -> None:
    fixture = _target_fixture(tmp_path, phase="cleanup_pre")
    remove_worktree(fixture.identity, fixture.workspace)
    complete = observe_repository_state(
        fixture.identity, "cleanup_post", "b" * 64, fixture.branch,
        fixture.workspace, "2026-07-21T01:02:04Z",
    ).snapshot

    _assert_failure(
        "protocol_invalid",
        lambda: prove_removed_recovery(
            fixture.identity, complete, fixture.revision, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:05Z",
        ),
    )


def test_recovery_proofs_propagate_runner_cap_failure(tmp_path: Path) -> None:
    checkout, _revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    persisted = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:01Z"
    ).snapshot

    def overflowing(*_args: Any) -> GitCommandResult:
        return GitCommandResult(0, b"", b"", stdout_overflow=True)

    _assert_failure(
        "cap_exceeded",
        lambda: prove_prepared_recovery(
            identity, persisted, "b" * 64, branch, workspace,
            "2026-07-21T01:02:02Z", runner=overflowing,
        ),
    )


def test_recovery_proof_exports_remain_lazy_in_fresh_process() -> None:
    code = """
import sys
import symphony.aidt_worktree as facade
assert 'symphony.aidt_worktree.git_state' not in sys.modules
names = ('PreparedRecoveryProof', 'ReadyRecoveryProof', 'RemovedRecoveryProof',
         'prove_prepared_recovery', 'prove_ready_recovery', 'prove_removed_recovery')
assert all(name in facade.__all__ for name in names)
assert all(getattr(facade, name) is not None for name in names)
assert 'symphony.aidt_worktree.git_state' in sys.modules
"""
    result = subprocess.run(
        (sys.executable, "-c", code),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize(
    ("field", "value", "category"),
    [
        ("root_head", "a" * 40, "identity_invalid"),
        ("root_symbolic_digest", "a" * 64, "identity_invalid"),
        ("root_status_digest", "a" * 64, "content_invalid"),
        ("refs_digest", "a" * 64, "identity_invalid"),
        ("refs_count", 1, "identity_invalid"),
        ("registry_digest", "a" * 64, "identity_invalid"),
        ("registry_count", 0, "identity_invalid"),
        ("root_content_digest", "a" * 64, "content_invalid"),
        ("root_content_count", 9_999, "content_invalid"),
        ("root_content_bytes", 536_870_912, "content_invalid"),
        ("protected_digest", "a" * 64, "identity_invalid"),
        ("protected_count", 2_500, "identity_invalid"),
        ("base_ref_sha", "a" * 40, "base_invalid"),
        ("repository_binding_digest", "a" * 64, "binding_invalid"),
    ],
)
def test_persisted_snapshot_tampering_fails_closed(
    tmp_path: Path,
    field: str,
    value: object,
    category: str,
) -> None:
    checkout, _revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    persisted = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:01Z"
    ).snapshot
    tampered = replace(persisted, **{field: value})
    _assert_failure(
        category,
        lambda: prove_prepared_recovery(
            identity, tampered, "b" * 64, branch, workspace,
            "2026-07-21T01:02:02Z",
        ),
    )


@pytest.mark.parametrize(
    ("field", "boundary", "category"),
    [
        ("root_content_count", 10_000, "content_invalid"),
        ("root_content_count", 10_001, "content_invalid"),
        ("root_content_bytes", 536_870_912, "content_invalid"),
        ("root_content_bytes", 536_870_913, "content_invalid"),
        ("registry_count", 2_500, "identity_invalid"),
        ("registry_count", 2_501, "identity_invalid"),
        ("protected_count", 2_500, "identity_invalid"),
        ("protected_count", 2_501, "identity_invalid"),
        ("refs_count", 2_500, "identity_invalid"),
        ("refs_count", 2_501, "identity_invalid"),
    ],
)
def test_persisted_snapshot_count_boundaries_fail_when_not_observed(
    tmp_path: Path,
    field: str,
    boundary: int,
    category: str,
) -> None:
    checkout, _revision = _repository(tmp_path)
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    persisted = observe_repository_state(
        identity, "s1", "b" * 64, branch, workspace, "2026-07-21T01:02:01Z"
    ).snapshot
    forged = _forge_snapshot(persisted, **{field: boundary})

    _assert_failure(
        category,
        lambda: prove_prepared_recovery(
            identity, forged,  # type: ignore[arg-type]
            "b" * 64, branch, workspace,
            "2026-07-21T01:02:02Z",
        ),
    )


def test_target_field_and_cleanup_phase_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _target_fixture(tmp_path, phase="cleanup_pre")
    wrong_digest = replace(
        fixture.state.snapshot, target_registration_digest="a" * 64
    )
    _assert_failure(
        "identity_invalid",
        lambda: prove_removed_recovery(
            fixture.identity, wrong_digest, fixture.revision, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:03Z",
        ),
    )
    wrong_phase = replace(fixture.state.snapshot, phase="s2")
    _assert_failure(
        "protocol_invalid",
        lambda: prove_removed_recovery(
            fixture.identity, wrong_phase, fixture.revision, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:03Z",
        ),
    )
    half_null = _forge_snapshot(
        fixture.state.snapshot, target_registration_digest=None
    )
    _assert_failure(
        "protocol_invalid",
        lambda: prove_removed_recovery(
            fixture.identity, half_null,  # type: ignore[arg-type]
            fixture.revision, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:03Z",
        ),
    )


@pytest.mark.parametrize("change", ["add", "delete", "change", "rename"])
def test_ready_projection_rejects_any_unrelated_ref_change(
    tmp_path: Path,
    change: str,
) -> None:
    checkout, revision = _repository(tmp_path)
    seed = "refs/heads/fix/A20-7777"
    _git(checkout, "update-ref", seed, revision)
    fixture = _target_fixture_from_repository(tmp_path, checkout, revision)
    other = _unrelated_commit(checkout, revision)
    if change == "add":
        _git(checkout, "update-ref", "refs/heads/fix/A20-8888", revision)
    elif change == "delete":
        _git(checkout, "update-ref", "-d", seed)
    elif change == "change":
        _git(checkout, "update-ref", seed, other)
    else:
        _git(checkout, "update-ref", "refs/heads/fix/A20-8888", revision)
        _git(checkout, "update-ref", "-d", seed)
    _assert_ready_projection_failure(fixture)


@pytest.mark.parametrize("change", ["add", "delete", "change", "rename"])
def test_ready_projection_rejects_any_unrelated_registration_change(
    tmp_path: Path,
    change: str,
) -> None:
    checkout, revision = _repository(tmp_path)
    unrelated = (tmp_path / "unrelated").resolve()
    _git(checkout, "worktree", "add", "-b", "fix/A20-7777", str(unrelated), revision)
    fixture = _target_fixture_from_repository(tmp_path, checkout, revision)
    if change == "add":
        _git(checkout, "worktree", "add", "-b", "fix/A20-8888", str(tmp_path / "added"), revision)
    elif change == "delete":
        _git(checkout, "worktree", "remove", str(unrelated))
    elif change == "change":
        (unrelated / "tracked.txt").write_text("changed\n", encoding="utf-8")
        _git(unrelated, "add", "tracked.txt")
        _git(unrelated, "commit", "-m", "changed")
    else:
        _git(checkout, "worktree", "move", str(unrelated), str(tmp_path / "renamed"))
    _assert_ready_projection_failure(fixture)


def _target_fixture_from_repository(
    tmp_path: Path, checkout: Path, revision: str
) -> SimpleNamespace:
    identity = observe_repository_identity(checkout, "viewer-api")
    branch = "fix/A20-1188"
    workspace = (tmp_path / "ticket").resolve()
    add_worktree(identity, branch, workspace, revision)
    state = observe_repository_state(
        identity, "s2", "b" * 64, branch, workspace, "2026-07-21T01:02:01Z"
    )
    return SimpleNamespace(
        identity=identity, branch=branch, workspace=workspace, state=state
    )


def _assert_ready_projection_failure(fixture: SimpleNamespace) -> None:
    _assert_failure(
        "identity_invalid",
        lambda: prove_ready_recovery(
            fixture.identity, fixture.state.snapshot, "b" * 64,
            fixture.branch, fixture.workspace, "2026-07-21T01:02:02Z",
            phase="resume",
        ),
    )


def test_recovery_result_dtos_reject_cross_field_mismatches(tmp_path: Path) -> None:
    fixture = _target_fixture(tmp_path)
    prepared = prove_prepared_recovery(
        fixture.identity, fixture.s1.snapshot, "b" * 64, fixture.branch,
        fixture.workspace, "2026-07-21T01:02:03Z",
    )
    ready = prove_ready_recovery(
        fixture.identity, fixture.state.snapshot, "b" * 64, fixture.branch,
        fixture.workspace, "2026-07-21T01:02:04Z", phase="resume",
    )
    assert prepared.ticket is not None
    forged_tickets = (
        replace(prepared.ticket, path=tmp_path / "wrong"),
        replace(prepared.ticket, branch="fix/A20-9999"),
        replace(prepared.ticket, head="a" * 40),
        replace(prepared.ticket, clean=False),
        replace(prepared.ticket, no_upstream=False),
    )
    for ticket in forged_tickets:
        _assert_failure(
            "protocol_invalid",
            lambda ticket=ticket: PreparedRecoveryProof(
                prepared.state, ticket, prepared.create_delta_digest
            ),
        )
    _assert_failure(
        "protocol_invalid",
        lambda: ReadyRecoveryProof(ready.state, replace(ready.ticket, head="a" * 40)),
    )
    cleanup = observe_repository_state(
        fixture.identity, "cleanup_pre", "b" * 64, fixture.branch,
        fixture.workspace, "2026-07-21T01:02:05Z",
    )
    remove_worktree(fixture.identity, fixture.workspace)
    removed = prove_removed_recovery(
        fixture.identity, cleanup.snapshot, fixture.revision, "b" * 64,
        fixture.branch, fixture.workspace, "2026-07-21T01:02:06Z",
    )
    _assert_failure(
        "protocol_invalid",
        lambda: RemovedRecoveryProof(replace(removed.state, refs=()), removed.remove_delta_digest),
    )


def test_recovery_public_slice_exports_only_operations_and_uses_observed_state() -> None:
    import symphony.aidt_worktree as facade

    expected = {
        "PreparedRecoveryProof",
        "ReadyRecoveryProof",
        "RemovedRecoveryProof",
        "prove_prepared_recovery",
        "prove_ready_recovery",
        "prove_removed_recovery",
    }
    assert expected <= set(facade.__all__)
    assert not any(name.startswith("_") for name in facade.__all__)
    operations = (
        prove_prepared_recovery,
        prove_ready_recovery,
        prove_removed_recovery,
    )
    source = "\n".join(inspect.getsource(operation) for operation in operations)
    assert "RepositoryState(" not in source
    assert not any(
        f'"{command}"' in source
        for command in ("fetch", "add", "remove", "checkout", "reset", "rebase", "prune")
    )
