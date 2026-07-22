"""Executable RED contract for the routed AIDT worktree provisioner."""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import fields, replace
from functools import partial
from pathlib import Path

import pytest

import symphony.aidt_worktree.provisioner as provisioner_module
from symphony.aidt_worktree.contract import (
    AidtWorktreeFailure,
    AidtWorktreeResult,
    DelegateDisposition,
    common_git_lock_path,
)
from symphony.aidt_worktree.git_state import (
    BinaryRunner,
    FetchResult,
    GitCommandResult,
    RepositoryIdentity,
    observe_repository_identity,
)
from symphony.aidt_worktree.manifest import (
    AttemptRecord,
    advisory_lock as public_advisory_lock,
    canonical_json_bytes,
    next_failure_record,
    persist_attempt,
    persist_ownership,
    read_attempt,
    read_manifest,
    read_optional_ownership,
)
from symphony.aidt_worktree.provisioner import (
    ActiveCompletionLease,
    AidtProvisioningAdmission,
    AidtRunGuard,
    AidtWorktreeProvisioner,
    DenyAllCompletionAuthority,
    PreparedAidtWorktree,
)

from tests.aidt_provisioner_support import (
    IDENTIFIER,
    NOW,
    ExactTestAuthority,
    ProvisionerFixture,
    git,
)


class CrashAtSeam(BaseException):
    """Model process death without entering ordinary failure persistence."""

    def __init__(self, seam: str) -> None:
        self.seam = seam


class PartialWriteCrash(BaseException):
    """Model process death after one durable sidecar write."""


LockEvent = tuple[str, Path, Path | None]
LockFactory = Callable[..., AbstractContextManager[None]]


@contextmanager
def _record_ordered_lock(
    real_ordered: LockFactory,
    events: list[LockEvent],
    common_git_lock: Path,
    manifest_lock: Path,
    *,
    timeout_seconds: float = 5.0,
) -> Iterator[None]:
    events.append(("ordered:enter", common_git_lock, manifest_lock))
    try:
        with real_ordered(
            common_git_lock,
            manifest_lock,
            timeout_seconds=timeout_seconds,
        ):
            yield
    finally:
        events.append(("ordered:exit", common_git_lock, manifest_lock))


@contextmanager
def _record_advisory_lock(
    real_advisory: LockFactory,
    events: list[LockEvent],
    path: Path,
    *,
    timeout_seconds: float = 5.0,
) -> Iterator[None]:
    events.append(("manifest-only:enter", path, None))
    try:
        with real_advisory(path, timeout_seconds=timeout_seconds):
            yield
    finally:
        events.append(("manifest-only:exit", path, None))


def _crash_on(expected: str) -> Callable[[str], None]:
    def hook(seam: str) -> None:
        if seam == expected:
            raise CrashAtSeam(seam)

    return hook


def _assert_subsequence(events: list[str], expected: list[str]) -> None:
    position = 0
    for event in events:
        if position < len(expected) and event == expected[position]:
            position += 1
    assert position == len(expected), events


def _ready_fixture(tmp_path: Path) -> tuple[ProvisionerFixture, PreparedAidtWorktree]:
    fixture = ProvisionerFixture.create(tmp_path)
    prepared = fixture.prepare_ready()
    return fixture, prepared


def _cleanup(
    fixture: ProvisionerFixture,
    *,
    authority: object | None = None,
    authorization: object | None = None,
    lease: object | None = None,
    fault_hook: Callable[[str], None] | None = None,
):
    return fixture.provisioner(
        authority=authority,
        fault_hook=fault_hook,
    ).cleanup(
        IDENTIFIER,
        fixture.workspace,
        authorization=authorization,  # type: ignore[arg-type]
        lease=lease,  # type: ignore[arg-type]
    )


def test_static_recovery_boundary_has_no_synthetic_state_or_path_inference() -> None:
    source = inspect.getsource(provisioner_module)
    tree = ast.parse(source)
    direct_probes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"lstat", "exists", "is_dir"}
    }

    assert "_state_from_snapshot" not in source
    assert direct_probes == set()
    for public_name in (
        "prove_prepared_recovery",
        "prove_ready_recovery",
        "prove_removed_recovery",
    ):
        assert public_name in source


def test_static_boundary_uses_only_public_git_names_and_stable_path_types() -> None:
    tree = ast.parse(inspect.getsource(provisioner_module))
    git_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "git_state"
        for alias in node.names
    ]
    path_parameters = [
        argument
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for argument in node.args.args
        if argument.arg == "paths"
    ]

    assert git_imports and all(not name.startswith("_") for name in git_imports)
    assert "StableWorktreePaths" in inspect.getsource(provisioner_module)
    assert path_parameters
    assert all(
        isinstance(argument.annotation, ast.Name)
        and argument.annotation.id == "StableWorktreePaths"
        for argument in path_parameters
    )
    assert "_stable_paths" not in inspect.getsource(provisioner_module)


def test_public_facade_exports_exact_provisioner_surface_lazily_in_all_orders() -> None:
    names = (
        "ActiveCompletionLease",
        "AidtProvisioningAdmission",
        "AidtRunGuard",
        "AidtWorktreeProvisioner",
        "CompletionAuthority",
        "DenyAllCompletionAuthority",
        "PreparedAidtWorktree",
    )
    script = f"""
import itertools
import subprocess
import sys

names = {names!r}
import symphony.aidt_worktree as facade
assert "symphony.aidt_worktree.provisioner" not in sys.modules
assert "symphony.aidt_worktree.git_state" not in sys.modules
assert "symphony.aidt_worktree.manifest" not in sys.modules
assert set(names).issubset(facade.__all__)
try:
    facade.not_a_frozen_export
except AttributeError:
    pass
else:
    raise AssertionError("unknown export accepted")
for name in names:
    assert getattr(facade, name).__module__ == "symphony.aidt_worktree.provisioner"
for order in (names, tuple(reversed(names)), names[::2] + names[1::2]):
    code = "import symphony.aidt_worktree as f;" + ";".join(
        f"getattr(f, {{item!r}})" for item in order
    )
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert child.returncode == 0, child.stderr
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr


def test_new_create_orders_pair_binding_prepared_add_verify_ready(tmp_path: Path) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    admission = fixture.initial_admission()

    def durable_hook(seam: str) -> None:
        fixture.events.append(f"fault:{seam}")
        if seam == "after_prepared_fsync_before_add":
            manifest = read_manifest(fixture.paths.manifest)
            ownership = read_optional_ownership(fixture.paths.ownership)
            attempt = read_attempt(fixture.paths.attempt)
            assert manifest.state == "prepared" and manifest.manifest_revision == 1
            assert ownership is not None and ownership.manifest_revision == 1
            assert attempt.mutation_phase == "prepared" and attempt.manifest_revision == 1
            fixture.events.append("durable:prepared")
        if seam == "after_verification_before_ready_fsync":
            assert read_manifest(fixture.paths.manifest).state == "prepared"
            assert read_attempt(fixture.paths.attempt).mutation_phase == "added"
            fixture.events.append("proof:s2")

    prepared = fixture.provisioner(fault_hook=durable_hook).prepare(admission)
    manifest = read_manifest(fixture.paths.manifest)
    ownership = read_optional_ownership(fixture.paths.ownership)
    attempt = read_attempt(fixture.paths.attempt)

    assert prepared.result == AidtWorktreeResult(fixture.workspace, True, 2)
    assert prepared.guard.workspace_path == fixture.workspace
    assert manifest.state == "ready" and manifest.manifest_revision == 2
    assert ownership is not None and ownership.manifest_revision == 2
    assert attempt.disposition == "ready" and attempt.manifest_revision == 2
    assert fixture.paths.manifest.read_bytes() == canonical_json_bytes(manifest)
    assert fixture.paths.ownership.read_bytes() == canonical_json_bytes(ownership)
    assert fixture.paths.attempt.read_bytes() == canonical_json_bytes(attempt)
    _assert_subsequence(
        fixture.events,
        [
            "route",
            "route",
            "git:fetch",
            "binding",
            "fault:after_forced_fetch_before_prepared",
            "route",
            "fault:after_prepared_fsync_before_add",
            "durable:prepared",
            "binding",
            "git:add",
            "fault:after_add_before_verification",
            "fault:after_verification_before_ready_fsync",
            "proof:s2",
        ],
    )
    fixture.runner.assert_exact_fetch_contract()
    fixture.runner.assert_no_forbidden_commands()


@pytest.mark.parametrize("drift", ["pair", "binding"])
def test_post_fetch_pair_or_binding_drift_blocks_before_prepared(
    tmp_path: Path, drift: str
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    admission = fixture.initial_admission()
    route_calls = 0
    binding_calls = 0

    def route_loader(_config: object, _identifier: str):
        nonlocal route_calls
        route_calls += 1
        if drift == "pair" and route_calls >= 3:
            return replace(fixture.route, route_pair_digest="9" * 64)
        return fixture.route

    def binding_observer(_settings: object, _service: str):
        nonlocal binding_calls
        binding_calls += 1
        digest = (
            "8" * 64
            if drift == "binding" and binding_calls >= 1
            else fixture.route.repository_binding_digest
        )
        observed = fixture.observe_binding(_settings, _service)
        return replace(observed, repository_binding_digest=digest)

    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner(
            route_loader=route_loader,  # type: ignore[arg-type]
            binding_observer=binding_observer,
        ).prepare(admission)

    assert not fixture.paths.manifest.exists()
    assert fixture.runner.count("add") == 0
    fixture.runner.assert_exact_fetch_contract()
    fixture.runner.assert_no_forbidden_commands()


def test_second_binding_recheck_drift_blocks_before_add(tmp_path: Path) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    calls = 0

    def binding_observer(_settings: object, _service: str):
        nonlocal calls
        calls += 1
        digest = "8" * 64 if calls >= 2 else fixture.route.repository_binding_digest
        observed = fixture.observe_binding(_settings, _service)
        return replace(observed, repository_binding_digest=digest)

    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner(binding_observer=binding_observer).prepare(
            fixture.initial_admission()
        )

    assert calls == 2
    assert fixture.runner.count("add") == 0
    assert read_manifest(fixture.paths.manifest).state == "prepared"


def test_prepared_absent_recovery_adds_once_without_fetch(tmp_path: Path) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    with pytest.raises(CrashAtSeam):
        fixture.provisioner(
            fault_hook=_crash_on("after_prepared_fsync_before_add")
        ).prepare(fixture.initial_admission())
    fetches = fixture.runner.count("fetch")

    recovered = fixture.provisioner().prepare(fixture.current_admission())

    assert recovered.result.created_now is True
    assert read_manifest(fixture.paths.manifest).state == "ready"
    assert fixture.runner.count("fetch") == fetches
    assert fixture.runner.count("add") == 1


@pytest.mark.parametrize(
    "seam",
    ["after_add_before_verification", "after_verification_before_ready_fsync"],
)
def test_prepared_exact_recovery_finalizes_without_fetch_or_add(
    tmp_path: Path, seam: str
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    with pytest.raises(CrashAtSeam):
        fixture.provisioner(fault_hook=_crash_on(seam)).prepare(
            fixture.initial_admission()
        )
    counts = (fixture.runner.count("fetch"), fixture.runner.count("add"))

    recovered = fixture.provisioner().prepare(fixture.current_admission())

    assert recovered.result.created_now is False
    assert read_manifest(fixture.paths.manifest).state == "ready"
    assert (fixture.runner.count("fetch"), fixture.runner.count("add")) == counts


@pytest.mark.parametrize("artifact", ["branch", "path", "remote", "detached_registration"])
def test_prepared_mixed_artifacts_are_manual_and_preserved(
    tmp_path: Path, artifact: str
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    with pytest.raises(CrashAtSeam):
        fixture.provisioner(
            fault_hook=_crash_on("after_prepared_fsync_before_add")
        ).prepare(fixture.initial_admission())
    manifest_bytes = fixture.paths.manifest.read_bytes()
    if artifact == "branch":
        git(fixture.checkout, "update-ref", f"refs/heads/{fixture.route.branch}", fixture.revision)
    elif artifact == "path":
        fixture.workspace.mkdir()
    elif artifact == "remote":
        git(
            fixture.checkout,
            "update-ref",
            f"refs/remotes/team/origin/{fixture.route.branch}",
            fixture.revision,
        )
    else:
        git(fixture.checkout, "worktree", "add", "--detach", str(fixture.workspace), fixture.revision)

    with pytest.raises(AidtWorktreeFailure) as failure:
        fixture.provisioner().prepare(fixture.current_admission())

    assert failure.value.category == "collision"
    assert fixture.paths.manifest.read_bytes() == manifest_bytes
    assert read_attempt(fixture.paths.attempt).disposition == "manual"
    assert fixture.runner.count("add") == 0
    fixture.runner.assert_no_forbidden_commands()


def test_ready_resume_allows_ticket_commits_and_dirty_work_without_mutation(
    tmp_path: Path,
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    (fixture.workspace / "descendant.txt").write_text("descendant\n", encoding="utf-8")
    git(fixture.workspace, "add", "descendant.txt")
    git(fixture.workspace, "commit", "-m", "descendant")
    (fixture.workspace / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    counts = tuple(fixture.runner.count(item) for item in ("fetch", "add", "remove"))

    resumed = fixture.provisioner().prepare(fixture.current_admission())

    assert resumed.result.created_now is False
    assert resumed.result.manifest_revision == 2
    assert tuple(fixture.runner.count(item) for item in ("fetch", "add", "remove")) == counts
    assert read_attempt(fixture.paths.attempt).disposition == "ready"


def test_ready_resume_rejects_conflicting_ownership_without_sidecar_mutation(
    tmp_path: Path,
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    owner = read_optional_ownership(fixture.paths.ownership)
    assert owner is not None
    conflicting = replace(
        owner,
        record_revision=owner.record_revision + 1,
        service="writer-api",
        route_pair_digest="9" * 64,
    )
    persist_ownership(
        fixture.paths.ownership,
        conflicting,
        expected_revision=owner.record_revision,
    )
    before = (
        fixture.paths.manifest.read_bytes(),
        fixture.paths.ownership.read_bytes(),
        fixture.paths.attempt.read_bytes(),
    )

    with pytest.raises(AidtWorktreeFailure) as failure:
        fixture.provisioner().prepare(fixture.current_admission())

    assert failure.value.category == "registry_invalid"
    assert (
        fixture.paths.manifest.read_bytes(),
        fixture.paths.ownership.read_bytes(),
        fixture.paths.attempt.read_bytes(),
    ) == before


def test_ready_partial_predecessor_waits_for_git_proof_before_sidecar_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    real_persist_manifest = provisioner_module.persist_manifest

    def crash_after_ready_manifest(path: Path, record: object, **kwargs: object):
        result = real_persist_manifest(path, record, **kwargs)  # type: ignore[arg-type]
        if getattr(record, "state", None) == "ready":
            raise PartialWriteCrash()
        return result

    monkeypatch.setattr(
        provisioner_module,
        "persist_manifest",
        crash_after_ready_manifest,
    )
    with pytest.raises(PartialWriteCrash):
        fixture.provisioner().prepare(fixture.initial_admission())
    monkeypatch.setattr(provisioner_module, "persist_manifest", real_persist_manifest)
    assert read_manifest(fixture.paths.manifest).state == "ready"

    git(fixture.checkout, "update-ref", "refs/heads/feat/A20-9999", fixture.revision)
    before = (
        fixture.paths.ownership.read_bytes(),
        fixture.paths.attempt.read_bytes(),
    )

    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner().prepare(fixture.current_admission())

    after = (
        fixture.paths.ownership.read_bytes(),
        fixture.paths.attempt.read_bytes(),
    )
    assert after == before, "ready sidecars changed before the Git proof succeeded"


@pytest.mark.parametrize("drift", ["unrelated_ref", "unrelated_registration"])
def test_ready_resume_rejects_unrelated_state_drift_before_backend(
    tmp_path: Path, drift: str
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    if drift == "unrelated_ref":
        git(fixture.checkout, "update-ref", "refs/heads/feat/A20-9999", fixture.revision)
    else:
        other = (tmp_path / "unrelated-worktree").resolve()
        git(fixture.checkout, "worktree", "add", "--detach", str(other), fixture.revision)
    before = len(fixture.runner.requests)

    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner().prepare(fixture.current_admission())

    assert fixture.runner.count("fetch") == 1
    assert fixture.runner.count("add") == 1
    assert all("remove" not in argv for argv in fixture.runner.requests[before:])


def test_before_run_rechecks_pair_binding_attempt_and_git_identity(tmp_path: Path) -> None:
    fixture, prepared = _ready_fixture(tmp_path)
    provisioner = fixture.provisioner()
    baseline = tuple(fixture.runner.count(item) for item in ("fetch", "add", "remove"))

    provisioner.attest_before_run(prepared.guard)
    provisioner.attest_before_run(prepared.guard)
    forged_path = replace(prepared.guard, workspace_path=(tmp_path / "forged-secret").resolve())
    with pytest.raises(AidtWorktreeFailure):
        provisioner.attest_before_run(forged_path)

    assert tuple(fixture.runner.count(item) for item in ("fetch", "add", "remove")) == baseline


@pytest.mark.parametrize("barrier", ["route", "binding"])
def test_locked_before_run_barrier_rereads_route_and_binding(
    tmp_path: Path, barrier: str
) -> None:
    fixture, prepared = _ready_fixture(tmp_path)
    current_route = fixture.route
    drifted = False

    def route_loader(_config: object, _identifier: str):
        return current_route

    def command_hook(_argv: tuple[str, ...]) -> None:
        nonlocal current_route, drifted
        if barrier == "route" and not drifted:
            current_route = replace(current_route, route_fingerprint="9" * 64)
            drifted = True

    binding_calls = 0

    def binding_observer(_settings: object, _service: str):
        nonlocal binding_calls
        binding_calls += 1
        digest = (
            "8" * 64
            if barrier == "binding"
            else fixture.route.repository_binding_digest
        )
        observed = fixture.observe_binding(_settings, _service)
        return replace(observed, repository_binding_digest=digest)

    fixture.runner.command_hook = command_hook
    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner(
            route_loader=route_loader,  # type: ignore[arg-type]
            binding_observer=binding_observer,
        ).attest_before_run(prepared.guard)

    assert barrier != "route" or drifted
    assert barrier != "binding" or binding_calls >= 1


def test_admission_and_guard_fields_are_exact_sealed_capabilities(tmp_path: Path) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    admitted = fixture.initial_admission()
    current = read_attempt(fixture.paths.attempt)
    manual = next_failure_record(current, "collision", "none", None, NOW)
    persist_attempt(
        fixture.paths.attempt,
        manual,
        expected_revision=current.record_revision,
    )
    forged = replace(admitted, attempt_record_revision=manual.record_revision)

    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner().prepare(forged)
    with pytest.raises(AidtWorktreeFailure):
        PreparedAidtWorktree(object(), object())  # type: ignore[arg-type]

    assert fixture.runner.count("fetch") == 0
    assert fixture.runner.count("add") == 0


def test_non_due_backoff_and_swapped_same_revision_attempt_never_reach_git(
    tmp_path: Path,
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    admitted = fixture.initial_admission()
    current = read_attempt(fixture.paths.attempt)
    waiting = next_failure_record(current, "fetch_timeout", "none", None, NOW)
    persist_attempt(fixture.paths.attempt, waiting, expected_revision=current.record_revision)
    non_due = replace(admitted, attempt_record_revision=waiting.record_revision)

    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner().prepare(non_due)
    assert fixture.runner.count("fetch") == 0

    forged = replace(
        waiting,
        record_revision=waiting.record_revision + 1,
        identifier="A20-1189--viewer-api",
    )
    persist_attempt(
        fixture.paths.attempt,
        forged,
        expected_revision=waiting.record_revision,
    )
    swapped = replace(non_due, attempt_record_revision=forged.record_revision)
    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner().prepare(swapped)
    assert fixture.runner.count("fetch") == 0


def test_attempt_phase_ready_and_failure_records_follow_durable_order(tmp_path: Path) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    phases: list[tuple[str, int | None]] = []

    def hook(seam: str) -> None:
        if seam in {
            "after_prepared_fsync_before_add",
            "after_verification_before_ready_fsync",
        }:
            attempt = read_attempt(fixture.paths.attempt)
            phases.append((attempt.mutation_phase, attempt.manifest_revision))

    fixture.provisioner(fault_hook=hook).prepare(fixture.initial_admission())
    final = read_attempt(fixture.paths.attempt)

    assert phases == [("prepared", 1), ("added", 1)]
    assert (final.disposition, final.mutation_phase, final.manifest_revision) == (
        "ready",
        "added",
        2,
    )

    pre_intent = ProvisionerFixture.create(tmp_path / "pre-intent")
    pre_intent.runner.overrides[0] = GitCommandResult(1, b"", b"failed")
    with pytest.raises(AidtWorktreeFailure):
        pre_intent.provisioner().prepare(pre_intent.initial_admission())
    failed = read_attempt(pre_intent.paths.attempt)
    assert (failed.disposition, failed.mutation_phase) == ("backoff", "none")


def test_cleanup_is_deny_all_without_verified_authority_and_active_lease(
    tmp_path: Path,
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    authorization = fixture.authorization()
    lease = fixture.lease()
    cases = [
        (None, None),
        (authorization, None),
        (None, lease),
        (authorization, replace(lease, active=False)),
        (authorization, replace(lease, competing_owner=True)),
    ]
    for token, current_lease in cases:
        result = _cleanup(
            fixture,
            authority=DenyAllCompletionAuthority(),
            authorization=token,
            lease=current_lease,
        )
        assert result.disposition is DelegateDisposition.OWNED_PRESERVED
        assert result.category == "authorization_invalid"

    assert fixture.workspace.is_dir()
    assert read_manifest(fixture.paths.manifest).state == "ready"
    assert fixture.runner.count("remove") == 0


def test_authorized_cleanup_writes_removing_then_plain_remove_then_removed(
    tmp_path: Path,
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    authority = ExactTestAuthority()
    observed_states: list[str] = []

    def hook(seam: str) -> None:
        if seam == "after_removing_fsync_before_remove":
            observed_states.append(read_manifest(fixture.paths.manifest).state)
            assert read_attempt(fixture.paths.attempt).mutation_phase == "removing"
        if seam == "after_physical_remove_before_removed_fsync":
            assert not fixture.workspace.exists()

    result = _cleanup(
        fixture,
        authority=authority,
        authorization=fixture.authorization(),
        lease=fixture.lease(),
        fault_hook=hook,
    )
    manifest = read_manifest(fixture.paths.manifest)
    ownership = read_optional_ownership(fixture.paths.ownership)

    assert result.disposition is DelegateDisposition.HANDLED
    assert observed_states == ["removing"]
    assert manifest.state == "removed" and manifest.manifest_revision == 4
    assert manifest.removal_proof is not None
    assert manifest.removal_proof.post_snapshot is not None
    assert ownership is not None and ownership.tombstone is True
    assert fixture.runner.count("remove") == 1
    assert all("--force" not in argv for argv in fixture.runner.requests)
    assert git(fixture.checkout, "show-ref", "--verify", f"refs/heads/{fixture.route.branch}").returncode == 0


@pytest.mark.parametrize(
    ("authorization_change", "lease_change"),
    [
        ({"ready_manifest_revision": 1}, {}),
        ({"ready_manifest_revision": 3}, {}),
        ({"workflow_generation": "8" * 64}, {}),
        ({"route_pair_digest": "7" * 64}, {}),
        ({"authorization_digest": "6" * 64}, {}),
        ({"issued_at": "2026-07-20T01:02:03Z"}, {}),
        ({}, {"active": False}),
        ({}, {"competing_owner": True}),
        ({}, {"run_id": "4" * 32}),
        ({"attempt_kind": "retry"}, {}),
    ],
)
def test_removing_recovery_rejects_every_wrong_authority_or_lease_field(
    tmp_path: Path,
    authorization_change: dict[str, object],
    lease_change: dict[str, object],
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    with pytest.raises(CrashAtSeam):
        _cleanup(
            fixture,
            authority=ExactTestAuthority(),
            authorization=fixture.authorization(),
            lease=fixture.lease(),
            fault_hook=_crash_on("after_removing_fsync_before_remove"),
        )
    removes = fixture.runner.count("remove")
    token = fixture.authorization(**authorization_change)
    lease_values = dict(lease_change)
    if "run_id" in lease_values:
        lease_values.setdefault("issue_id", IDENTIFIER)
    current_lease = fixture.lease(**lease_values)

    result = _cleanup(
        fixture,
        authority=ExactTestAuthority(),
        authorization=token,
        lease=current_lease,
    )

    assert result.disposition is DelegateDisposition.OWNED_PRESERVED
    assert read_manifest(fixture.paths.manifest).state == "removing"
    assert fixture.runner.count("remove") == removes


def test_removing_recovery_requires_fresh_authority_only_for_destructive_retry(
    tmp_path: Path,
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    with pytest.raises(CrashAtSeam):
        _cleanup(
            fixture,
            authority=ExactTestAuthority(),
            authorization=fixture.authorization(),
            lease=fixture.lease(),
            fault_hook=_crash_on("after_removing_fsync_before_remove"),
        )

    denied = _cleanup(fixture, authority=ExactTestAuthority())
    assert denied.disposition is DelegateDisposition.OWNED_PRESERVED
    assert fixture.runner.count("remove") == 0

    handled = _cleanup(
        fixture,
        authority=ExactTestAuthority(),
        authorization=fixture.authorization(),
        lease=fixture.lease(),
    )
    assert handled.disposition is DelegateDisposition.HANDLED
    assert read_manifest(fixture.paths.manifest).state == "removed"
    assert fixture.runner.count("remove") == 1


def test_branch_retained_proof_only_finalization_needs_no_fresh_authority(
    tmp_path: Path,
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    with pytest.raises(CrashAtSeam):
        _cleanup(
            fixture,
            authority=ExactTestAuthority(),
            authorization=fixture.authorization(),
            lease=fixture.lease(),
            fault_hook=_crash_on("after_physical_remove_before_removed_fsync"),
        )
    removes = fixture.runner.count("remove")

    result = _cleanup(fixture, authority=ExactTestAuthority())

    assert result.disposition is DelegateDisposition.HANDLED
    assert read_manifest(fixture.paths.manifest).state == "removed"
    assert fixture.runner.count("remove") == removes
    assert git(fixture.checkout, "show-ref", "--verify", f"refs/heads/{fixture.route.branch}").returncode == 0


@pytest.mark.parametrize("barrier", ["route", "binding"])
def test_cleanup_rechecks_route_and_binding_inside_locked_barrier(
    tmp_path: Path, barrier: str
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    current_route = fixture.route
    changed = False

    def loader(_config: object, _identifier: str):
        return current_route

    def command_hook(_argv: tuple[str, ...]) -> None:
        nonlocal current_route, changed
        if barrier == "route" and not changed:
            current_route = replace(current_route, route_fingerprint="9" * 64)
            changed = True

    def observer(_settings: object, _service: str):
        digest = (
            "8" * 64
            if barrier == "binding"
            else fixture.route.repository_binding_digest
        )
        observed = fixture.observe_binding(_settings, _service)
        return replace(observed, repository_binding_digest=digest)

    fixture.runner.command_hook = command_hook
    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner(
            route_loader=loader,  # type: ignore[arg-type]
            binding_observer=observer,
            authority=ExactTestAuthority(),
        ).cleanup(
            IDENTIFIER,
            fixture.workspace,
            authorization=fixture.authorization(),
            lease=fixture.lease(),
        )

    assert fixture.runner.count("remove") == 0
    assert fixture.workspace.is_dir()
    assert read_manifest(fixture.paths.manifest).state == "ready"


@pytest.mark.parametrize(
    "seam",
    [
        "after_forced_fetch_before_prepared",
        "after_prepared_fsync_before_add",
        "after_add_before_verification",
        "after_verification_before_ready_fsync",
    ],
)
def test_all_create_crash_seams_restart_through_public_prepare(
    tmp_path: Path, seam: str
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    with pytest.raises(CrashAtSeam) as crash:
        fixture.provisioner(fault_hook=_crash_on(seam)).prepare(
            fixture.initial_admission()
        )
    assert crash.value.seam == seam
    before = (fixture.runner.count("fetch"), fixture.runner.count("add"))

    recovered = fixture.provisioner().prepare(fixture.current_admission())

    assert recovered.result.manifest_revision == 2
    assert read_manifest(fixture.paths.manifest).state == "ready"
    expected_fetches = before[0] + int(seam == "after_forced_fetch_before_prepared")
    assert fixture.runner.count("fetch") == expected_fetches
    assert fixture.runner.count("add") == 1
    fixture.runner.assert_no_forbidden_commands()


@pytest.mark.parametrize(
    "seam",
    ["after_removing_fsync_before_remove", "after_physical_remove_before_removed_fsync"],
)
def test_all_cleanup_crash_seams_restart_through_public_cleanup(
    tmp_path: Path, seam: str
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    with pytest.raises(CrashAtSeam):
        _cleanup(
            fixture,
            authority=ExactTestAuthority(),
            authorization=fixture.authorization(),
            lease=fixture.lease(),
            fault_hook=_crash_on(seam),
        )
    removes = fixture.runner.count("remove")

    result = _cleanup(
        fixture,
        authority=ExactTestAuthority(),
        authorization=(
            fixture.authorization()
            if seam == "after_removing_fsync_before_remove"
            else None
        ),
        lease=fixture.lease() if seam == "after_removing_fsync_before_remove" else None,
    )

    assert result.disposition is DelegateDisposition.HANDLED
    assert read_manifest(fixture.paths.manifest).state == "removed"
    assert fixture.runner.count("remove") == removes + int(seam.endswith("before_remove"))
    fixture.runner.assert_no_forbidden_commands()


@pytest.mark.parametrize(
    ("transition", "record_kind"),
    [
        ("prepared", "manifest"),
        ("prepared", "ownership"),
        ("prepared", "attempt"),
        ("ready", "manifest"),
        ("ready", "ownership"),
        ("ready", "attempt"),
        ("removing", "manifest"),
        ("removing", "ownership"),
        ("removing", "attempt"),
        ("removed", "manifest"),
        ("removed", "ownership"),
    ],
)
def test_every_individual_multi_file_partial_write_restart_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
    record_kind: str,
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    original_manifest = provisioner_module.persist_manifest
    original_ownership = provisioner_module.persist_ownership
    original_attempt = provisioner_module.persist_attempt

    def crash_manifest(path: Path, record: object, **kwargs: object):
        result = original_manifest(path, record, **kwargs)  # type: ignore[arg-type]
        if record_kind == "manifest" and getattr(record, "state", None) == transition:
            raise PartialWriteCrash()
        return result

    def crash_ownership(path: Path, record: object, **kwargs: object):
        result = original_ownership(path, record, **kwargs)  # type: ignore[arg-type]
        matching = (
            transition == "prepared" and getattr(record, "manifest_revision", None) == 1
            or transition == "ready" and getattr(record, "manifest_revision", None) == 2
            or transition == "removing" and getattr(record, "manifest_revision", None) == 3
            or transition == "removed" and getattr(record, "tombstone", None) is True
        )
        if record_kind == "ownership" and matching:
            raise PartialWriteCrash()
        return result

    def crash_attempt(path: Path, record: AttemptRecord, **kwargs: object):
        result = original_attempt(path, record, **kwargs)  # type: ignore[arg-type]
        matching = (
            transition == "prepared" and record.mutation_phase == "prepared"
            or transition == "ready" and record.disposition == "ready"
            or transition == "removing" and record.mutation_phase == "removing"
        )
        if record_kind == "attempt" and matching:
            raise PartialWriteCrash()
        return result

    monkeypatch.setattr(provisioner_module, "persist_manifest", crash_manifest)
    monkeypatch.setattr(provisioner_module, "persist_ownership", crash_ownership)
    monkeypatch.setattr(provisioner_module, "persist_attempt", crash_attempt)
    if transition in {"removing", "removed"}:
        fixture.prepare_ready()

    with pytest.raises(PartialWriteCrash):
        if transition in {"prepared", "ready"}:
            fixture.provisioner().prepare(fixture.initial_admission())
        else:
            _cleanup(
                fixture,
                authority=ExactTestAuthority(),
                authorization=fixture.authorization(),
                lease=fixture.lease(),
            )
    monkeypatch.setattr(provisioner_module, "persist_manifest", original_manifest)
    monkeypatch.setattr(provisioner_module, "persist_ownership", original_ownership)
    monkeypatch.setattr(provisioner_module, "persist_attempt", original_attempt)

    if transition in {"prepared", "ready"}:
        fixture.provisioner().prepare(fixture.current_admission())
        expected = "ready"
    else:
        _cleanup(
            fixture,
            authority=ExactTestAuthority(),
            authorization=fixture.authorization(),
            lease=fixture.lease(),
        )
        expected = "removed"

    manifest = read_manifest(fixture.paths.manifest)
    ownership = read_optional_ownership(fixture.paths.ownership)
    attempt = read_attempt(fixture.paths.attempt)
    assert manifest.state == expected
    assert ownership is not None and ownership.manifest_revision == manifest.manifest_revision
    assert attempt.manifest_revision in {2, 3}
    assert fixture.runner.count("fetch") <= 1
    assert fixture.runner.count("add") <= 1
    assert fixture.runner.count("remove") <= 1


def test_fetch_result_and_registration_proof_are_consumed_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    real_fetch = provisioner_module.fetch_production_base

    def mismatched_fetch(
        identity: RepositoryIdentity,
        expected_origin_digest: str,
        expected_revision: str,
        expected_binding_digest: str,
        observe_binding: Callable[[], object],
        *,
        runner: BinaryRunner | None = None,
    ) -> FetchResult:
        real_fetch(
            identity,
            expected_origin_digest,
            expected_revision,
            expected_binding_digest,
            observe_binding,
            runner=runner,
        )
        return FetchResult("9" * 40, "8" * 64)

    monkeypatch.setattr(provisioner_module, "fetch_production_base", mismatched_fetch)
    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner().prepare(fixture.initial_admission())

    assert not fixture.paths.manifest.exists()
    assert fixture.runner.count("add") == 0
    source = inspect.getsource(provisioner_module)
    assert "target_registration_digest or" not in source


def test_durable_dtos_are_keyword_constructed_and_golden_bytes_stay_exact(
    tmp_path: Path,
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    tree = ast.parse(inspect.getsource(provisioner_module))
    durable_names = {
        "AidtWorktreeManifest",
        "OwnershipRecord",
        "PostProof",
        "RemovalProof",
    }
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in durable_names
    ]

    assert calls
    assert all(not call.args and call.keywords for call in calls)
    manifest = read_manifest(fixture.paths.manifest)
    ownership = read_optional_ownership(fixture.paths.ownership)
    assert ownership is not None
    assert fixture.paths.manifest.read_bytes() == canonical_json_bytes(manifest)
    assert fixture.paths.ownership.read_bytes() == canonical_json_bytes(ownership)
    assert fixture.paths.manifest.read_bytes().endswith(b"\n")


def test_repr_never_exposes_workspace_paths_or_lease_tokens(tmp_path: Path) -> None:
    secret_path = (tmp_path / "HOSTILE-PATH-SENTINEL").resolve()
    token = "d34db33fd34db33fd34db33fd34db33f"
    guard = AidtRunGuard(IDENTIFIER, "a" * 64, "b" * 64, 1, 2, secret_path)
    lease = ActiveCompletionLease(IDENTIFIER, IDENTIFIER, token, "initial", True, False)
    result = AidtWorktreeResult(secret_path, False, 2)
    prepared = PreparedAidtWorktree(result, guard)

    assert str(secret_path) not in repr(guard)
    assert str(secret_path) not in repr(prepared)
    assert token not in repr(lease)


def test_stale_failure_cannot_overwrite_ready_or_open_fabricated_common_lock(
    tmp_path: Path,
) -> None:
    fixture, _prepared = _ready_fixture(tmp_path)
    ready_bytes = fixture.paths.attempt.read_bytes()
    stale = AidtProvisioningAdmission(
        IDENTIFIER,
        fixture.settings.workflow_generation,
        fixture.route.route_pair_digest,
        2,
        "provision",
    )

    with pytest.raises(AidtWorktreeFailure) as failure:
        fixture.provisioner().prepare(stale)

    assert failure.value.category == "scope_changed"
    assert fixture.paths.attempt.read_bytes() == ready_bytes
    assert not (fixture.paths.locks / f"common-git-{'0' * 64}.lock").exists()


def test_failure_after_identity_resolution_reacquires_ordered_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    fixture.runner.overrides[0] = GitCommandResult(1, b"", b"failed")
    identity = observe_repository_identity(
        fixture.checkout,
        fixture.route.service,
        runner=fixture.runner,
    )
    expected_common = common_git_lock_path(
        fixture.paths,
        identity.common_git_identity,
    )
    real_ordered = provisioner_module.ordered_worktree_locks
    lock_events: list[LockEvent] = []
    monkeypatch.setattr(
        provisioner_module,
        "ordered_worktree_locks",
        partial(_record_ordered_lock, real_ordered, lock_events),
    )
    monkeypatch.setattr(
        provisioner_module,
        "advisory_lock",
        partial(_record_advisory_lock, public_advisory_lock, lock_events),
        raising=False,
    )

    with pytest.raises(AidtWorktreeFailure) as failure:
        fixture.provisioner().prepare(fixture.initial_admission())

    assert failure.value.category == "fetch_command_failed"
    assert fixture.events.count("git:fetch") == 1
    expected = [
        ("ordered:enter", expected_common, fixture.paths.manifest_lock),
        ("ordered:exit", expected_common, fixture.paths.manifest_lock),
        ("ordered:enter", expected_common, fixture.paths.manifest_lock),
        ("ordered:exit", expected_common, fixture.paths.manifest_lock),
    ]
    assert lock_events == expected, "failure persistence did not reacquire ordered locks"


def test_every_failure_uses_exact_command_and_no_generic_fallback_spy(
    tmp_path: Path,
) -> None:
    fixture = ProvisionerFixture.create(tmp_path)
    fallback_calls: list[str] = []

    def observer(_settings: object, _service: str):
        observed = fixture.observe_binding(_settings, _service)
        return replace(
            observed,
            repository_binding_digest="8" * 64,
        )

    with pytest.raises(AidtWorktreeFailure):
        fixture.provisioner(binding_observer=observer).prepare(
            fixture.initial_admission()
        )

    fixture.runner.assert_exact_fetch_contract()
    fixture.runner.assert_no_forbidden_commands()
    assert fallback_calls == []
    assert fixture.runner.count("add") == 0
    assert fixture.runner.count("remove") == 0


def test_frozen_dto_field_order_remains_the_exact_public_contract() -> None:
    assert tuple(field.name for field in fields(AidtProvisioningAdmission)) == (
        "identifier",
        "workflow_generation",
        "route_pair_digest",
        "attempt_record_revision",
        "action",
    )
    assert tuple(field.name for field in fields(AidtRunGuard)) == (
        "identifier",
        "workflow_generation",
        "route_pair_digest",
        "attempt_record_revision",
        "manifest_revision",
        "workspace_path",
    )
    assert tuple(field.name for field in fields(PreparedAidtWorktree)) == ("result", "guard")
    assert tuple(field.name for field in fields(ActiveCompletionLease)) == (
        "identifier",
        "issue_id",
        "run_id",
        "attempt_kind",
        "active",
        "competing_owner",
    )
    assert AidtWorktreeProvisioner.prepare
    assert AidtWorktreeProvisioner.attest_before_run
    assert AidtWorktreeProvisioner.cleanup
