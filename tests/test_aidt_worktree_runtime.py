"""Executable RED contract for process-lifetime AIDT worktree ownership."""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from threading import Barrier, Event

import pytest

from symphony.aidt_routing.contract import AidtRoutingFailure
from symphony.aidt_routing.dispatch import AidtRouteDispatchContract
import symphony.aidt_worktree.manifest as manifest_module
from symphony.aidt_worktree.contract import (
    AIDT_WORKTREE_BASE_REF,
    AIDT_WORKTREE_OWNERSHIP_SCHEMA,
    AIDT_WORKTREE_SCHEMA,
    AidtWorktreeFailure,
    AidtWorktreeResult,
    DelegateDisposition,
    DelegateResult,
    load_aidt_worktree_settings,
    stable_worktree_paths,
)
from symphony.aidt_worktree.manifest import (
    AidtWorktreeManifest,
    AttemptRecord,
    OwnershipRecord,
    PostProof,
    PreProof,
    RemovalProof,
    RepositorySnapshot,
    RouteScope,
    activate_registry,
    advance_attempt_phase,
    admit_attempt,
    initial_attempt_record,
    next_failure_record,
    persist_attempt,
    persist_manifest,
    persist_ownership,
    read_attempt,
    ready_attempt_record,
)
from symphony.aidt_worktree.provisioner import (
    AidtProvisioningAdmission,
    AidtRunGuard,
    PreparedAidtWorktree,
)
from symphony.workflow import ServiceConfig

from tests.aidt_routing_support import routing_config, service_config, service_definition


IDENTIFIER = "A20-1188--viewer-api"
UNMANAGED_ID = "LOCAL-1"
NOW = datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc)
NOW_TEXT = "2026-07-21T01:02:03Z"
SHA1 = "a" * 40
DIGEST = "b" * 64
FORBIDDEN_GIT_EXPORTS = frozenset(
    {
        "BinaryRunner", "BindingObserver", "FETCH_ARGV", "FetchResult",
        "GitCommandResult", "PreparedRecoveryProof", "ReadyRecoveryProof",
        "RefRecord", "RemovedRecoveryProof", "RepositoryIdentity", "RepositoryState",
        "StatusEntry", "TargetArtifactDisposition", "TicketWorktreeState",
        "WorktreeRegistration", "add_worktree", "base_is_ancestor",
        "canonical_origin_digest", "classify_target_artifacts", "default_binary_runner",
        "fetch_production_base", "git_environment", "observe_repository_identity",
        "observe_repository_state", "observe_ticket_worktree", "parse_ref_listing",
        "parse_status_porcelain_v2", "parse_worktree_porcelain",
        "prove_prepared_recovery", "prove_ready_recovery", "prove_removed_recovery",
        "remove_worktree", "validate_create_delta", "validate_fetch_delta",
        "validate_remove_delta", "verify_service_binding",
    }
)
FORBIDDEN_NETWORK_ROOTS = frozenset({"aiohttp", "httpx", "requests", "socket", "urllib"})


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value
        self.calls = 0
        self.error: BaseException | None = None

    def __call__(self) -> datetime:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.value


class RouteLoader:
    def __init__(self, route: AidtRouteDispatchContract) -> None:
        self.values: dict[str, object] = {route.identifier: route}
        self.calls: list[str] = []

    def __call__(self, _config: ServiceConfig, identifier: str) -> object:
        self.calls.append(identifier)
        value = self.values.get(identifier)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeProvisioner:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.prepare_calls: list[AidtProvisioningAdmission] = []
        self.attest_calls: list[AidtRunGuard] = []
        self.cleanup_calls: list[tuple[str, Path]] = []
        self.prepare_error: BaseException | None = None
        self.attest_error: BaseException | None = None
        self.cleanup_error: BaseException | None = None
        self.created_now: bool | None = None
        self.prepare_hook: Callable[[], None] | None = None
        self.attest_hook: Callable[[], None] | None = None
        self.cleanup_result: DelegateResult[None] = DelegateResult.owned_preserved(
            "authorization_invalid"
        )

    def prepare(self, admission: AidtProvisioningAdmission) -> PreparedAidtWorktree:
        self.prepare_calls.append(admission)
        if self.prepare_hook is not None:
            self.prepare_hook()
        if self.prepare_error is not None:
            raise self.prepare_error
        path = (self.workspace_root / admission.identifier).resolve()
        created_now = admission.action == "provision"
        if self.created_now is not None:
            created_now = self.created_now
        result = AidtWorktreeResult(path, created_now, 2)
        guard = AidtRunGuard(
            admission.identifier,
            admission.workflow_generation,
            admission.route_pair_digest,
            admission.attempt_record_revision,
            2,
            path,
        )
        return PreparedAidtWorktree(result, guard)

    def attest_before_run(self, guard: AidtRunGuard) -> None:
        self.attest_calls.append(guard)
        if self.attest_hook is not None:
            self.attest_hook()
        if self.attest_error is not None:
            raise self.attest_error

    def cleanup(self, identifier: str, path: Path, **_kwargs: object) -> DelegateResult[None]:
        self.cleanup_calls.append((identifier, path))
        if self.cleanup_error is not None:
            raise self.cleanup_error
        return self.cleanup_result


class FakeFactory:
    def __init__(self, provisioner: FakeProvisioner) -> None:
        self.provisioner = provisioner
        self.calls: list[tuple[ServiceConfig, object, Callable[[], datetime]]] = []
        self.error: BaseException | None = None

    def __call__(
        self,
        config: ServiceConfig,
        settings: object,
        *,
        clock: Callable[[], datetime],
    ) -> FakeProvisioner:
        self.calls.append((config, settings, clock))
        if self.error is not None:
            raise self.error
        return self.provisioner


@dataclass
class Harness:
    module: Any
    config: ServiceConfig
    route: AidtRouteDispatchContract
    loader: RouteLoader
    provisioner: FakeProvisioner
    factory: FakeFactory
    clock: MutableClock
    runtime: Any
    generation: Any


def _runtime_module() -> Any:
    try:
        return importlib.import_module("symphony.aidt_worktree.runtime")
    except ModuleNotFoundError as exc:
        if exc.name != "symphony.aidt_worktree.runtime":
            raise
        pytest.fail("runtime module is intentionally absent in the RED slice", pytrace=False)


def _config(root: Path, *, enabled: bool, workspace: str = "workspaces") -> ServiceConfig:
    board = (root / "board").resolve()
    board.mkdir(parents=True, exist_ok=True)
    raw = routing_config((root / "aidt").resolve(), [service_definition()])
    raw["aidt_worktree"] = {"enabled": enabled}
    config = service_config(board, raw)
    return replace(
        config,
        workflow_path=(root / "WORKFLOW.md").resolve(),
        workspace_root=(root / workspace).resolve(),
        workspace_reuse_policy="preserve",
        hooks=SimpleNamespace(
            after_create=None,
            before_run=None,
            after_run=None,
            before_remove=None,
            after_done=None,
        ),
        agent=SimpleNamespace(
            kind="codex", auto_commit_on_done=False, auto_merge_on_done=False
        ),
    )


def _route(identifier: str = IDENTIFIER, *, pair: str = "1" * 64) -> AidtRouteDispatchContract:
    coordinator, service = identifier.split("--", 1)
    return AidtRouteDispatchContract(
        identifier=identifier,
        coordinator=coordinator,
        service=service,
        kind="backend",
        checkout=service,
        checkout_ref="refs/remotes/origin/aidt-prd",
        checkout_revision="a" * 40,
        repository_binding_digest="2" * 64,
        route_fingerprint="3" * 64,
        coordinator_fingerprint="4" * 64,
        source_revision="5" * 64,
        catalog_revision="6" * 64,
        route_pair_digest=pair,
        issue_type="bug",
        change_kind="fix",
        branch=f"fix/{coordinator}",
        confidence=100,
    )


def _harness(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    workspace: str = "workspaces",
) -> Harness:
    harness = _unpublished_harness(
        root, monkeypatch, enabled=enabled, workspace=workspace
    )
    harness.generation = harness.runtime.publish(harness.config)
    return harness


def _unpublished_harness(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    workspace: str = "workspaces",
    clock: MutableClock | None = None,
) -> Harness:
    root.mkdir(parents=True, exist_ok=True)
    module = _runtime_module()
    config = _config(root, enabled=enabled, workspace=workspace)
    route = _route()
    loader = RouteLoader(route)
    monkeypatch.setattr(module, "load_route_dispatch_contract", loader)
    provisioner = FakeProvisioner(config.workspace_root)
    factory = FakeFactory(provisioner)
    clock = clock or MutableClock()
    runtime = module.AidtWorktreeRuntime(
        config.workflow_path, clock=clock, provisioner_factory=factory
    )
    return Harness(
        module, config, route, loader, provisioner, factory, clock, runtime, None
    )


def _admission(
    generation: Any,
    route: AidtRouteDispatchContract,
    *,
    action: str = "provision",
    revision: int = 2,
) -> AidtProvisioningAdmission:
    return AidtProvisioningAdmission(
        route.identifier,
        generation.workflow_generation,
        route.route_pair_digest,
        revision,
        action,  # type: ignore[arg-type]
    )


def _guard(generation: Any, route: AidtRouteDispatchContract) -> AidtRunGuard:
    path = (generation.config.workspace_root / route.identifier).resolve()
    return AidtRunGuard(
        route.identifier,
        generation.workflow_generation,
        route.route_pair_digest,
        2,
        2,
        path,
    )


def _issue_admission(
    harness: Harness, generation: Any, route: AidtRouteDispatchContract
) -> AidtProvisioningAdmission:
    harness.loader.values[route.identifier] = route
    issued = harness.runtime.admit_candidate(generation, route.identifier)
    _assert_result(issued, DelegateDisposition.HANDLED)
    return issued.value


def _issue_guard(
    harness: Harness, generation: Any, route: AidtRouteDispatchContract
) -> AidtRunGuard:
    issued = _issue_admission(harness, generation, route)
    prepared = harness.runtime.create_or_reuse(generation, issued)
    _assert_result(prepared, DelegateDisposition.HANDLED)
    return prepared.value.guard


def _assert_result(
    result: DelegateResult[Any],
    disposition: DelegateDisposition,
    category: str | None = None,
) -> None:
    assert result.disposition is disposition
    assert result.category == category


def _assert_lazy_facade() -> None:
    code = """
import inspect
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import symphony.aidt_worktree as facade
from tests.aidt_routing_support import routing_config, service_config, service_definition
blocked = ('runtime', 'provisioner', 'manifest', 'git_state')
assert all(f'symphony.aidt_worktree.{name}' not in sys.modules for name in blocked)
assert {'AidtWorktreeGeneration', 'AidtWorktreeHealth', 'AidtWorktreeRuntime'} <= set(facade.__all__)
assert facade.AidtWorktreeRuntime.__module__ == 'symphony.aidt_worktree.runtime'
assert 'symphony.aidt_worktree.runtime' in sys.modules
assert 'symphony.aidt_worktree.provisioner' not in sys.modules
assert 'symphony.aidt_worktree.git_state' not in sys.modules
factory = inspect.signature(facade.AidtWorktreeRuntime).parameters['provisioner_factory']
assert factory.default is None
with TemporaryDirectory() as name:
    root = Path(name)
    board = root / 'board'
    board.mkdir()
    raw = routing_config(root / 'aidt', [service_definition()])
    raw['aidt_worktree'] = {'enabled': False}
    config = service_config(board, raw)
    config = replace(config, workflow_path=(root / 'WORKFLOW.md').resolve())
    runtime = facade.AidtWorktreeRuntime(
        config.workflow_path,
        clock=lambda: datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    generation = runtime.publish(config)
    assert generation.settings is None
    assert not (root / '.symphony').exists()
assert all(f'symphony.aidt_worktree.{name}' not in sys.modules for name in blocked[1:])
try:
    facade.not_a_runtime_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown facade name was accepted')
"""
    result = subprocess.run(
        (sys.executable, "-c", code), check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def _runtime_dto_gaps(api: Any, harness: Harness) -> list[str]:
    class Status(str):
        pass

    hostile = (
        ("generation-revision", lambda: api.AidtWorktreeGeneration(True, harness.config, None, None)),
        ("generation-settings", lambda: api.AidtWorktreeGeneration(1, harness.config, object(), None)),
        ("health-status-subclass", lambda: _health_dto(api, Status("disabled"), None)),
        ("health-status-list", lambda: _health_dto(api, ["disabled"], None)),
        ("health-calendar", lambda: _health_dto(api, "disabled", "2026-02-31T01:02:03Z")),
    )
    gaps = []
    for label, construct in hostile:
        try:
            construct()
        except AidtWorktreeFailure as exc:
            if (exc.category, exc.ref) != ("internal_error", None):
                gaps.append(f"{label}:{exc.category}:{exc.ref}")
        except Exception as exc:
            gaps.append(f"{label}:{type(exc).__name__}")
        else:
            gaps.append(f"{label}:accepted")
    return gaps


def _health_dto(api: Any, status: object, success: object) -> Any:
    return api.AidtWorktreeHealth(
        False, status, None, 0, 0, 0, 0, None, None, success
    )


def _assert_constructor_does_not_resolve(
    api: Any, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_path = (root / "constructor" / "WORKFLOW.md").resolve()
    resolve_calls = 0

    def reject_resolve(*_args: object, **_kwargs: object) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        raise AssertionError("constructor resolve probe")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", reject_resolve)
        runtime = api.AidtWorktreeRuntime(workflow_path, clock=lambda: NOW)
    assert runtime.health_snapshot().status == "disabled"
    assert resolve_calls == 0
    assert not (workflow_path.parent / ".symphony").exists()


def _constructor_gap(
    api: Any, root: Path, monkeypatch: pytest.MonkeyPatch
) -> str | None:
    try:
        _assert_constructor_does_not_resolve(api, root, monkeypatch)
    except Exception as exc:
        return f"constructor:{type(exc).__name__}"
    return None


def _nesting_depth(node: ast.AST, current: int = 0) -> int:
    blocks = (
        ast.If, ast.For, ast.AsyncFor, ast.While,
        ast.With, ast.AsyncWith, ast.Try, ast.Match,
    )
    nested = current + 1 if isinstance(node, blocks) else current
    return max([nested, *(_nesting_depth(child, nested) for child in ast.iter_child_nodes(node))])


def _function_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    assert node.end_lineno is not None
    return node.end_lineno - node.lineno + 1


def _static_module_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_module_string(node.left)
        right = _static_module_string(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = [_static_module_string(value) for value in node.values]
        if any(part is None for part in parts):
            return None
        return "".join(part for part in parts if part is not None)
    return None


def _dynamic_import_target(node: ast.Call) -> tuple[bool, str | None]:
    function = node.func
    is_import = isinstance(function, ast.Name) and function.id == "__import__"
    is_import |= isinstance(function, ast.Attribute) and function.attr == "import_module"
    if not is_import:
        return False, None
    if not node.args:
        return True, None
    return True, _static_module_string(node.args[0])


def _forbidden_git_target(value: str) -> bool:
    normalized = value.lstrip(".")
    leaf = normalized.rsplit(".", 1)[-1]
    private_module = normalized == "git_state" or normalized.endswith(".git_state")
    return (
        private_module
        or "symphony.aidt_worktree.git_state" in normalized
        or leaf in FORBIDDEN_GIT_EXPORTS
    )


def _node_import_targets(node: ast.AST) -> tuple[list[str], bool]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names], False
    if isinstance(node, ast.ImportFrom):
        base = "." * node.level + (node.module or "")
        aliases = [f"{base}.{alias.name}" for alias in node.names]
        return [base, *aliases], False
    if isinstance(node, ast.Call):
        is_import, target = _dynamic_import_target(node)
        return ([target] if target is not None else []), is_import and target is None
    if isinstance(node, ast.Constant) and type(node.value) is str:
        normalized = node.value.lstrip(".")
        is_private = normalized == "git_state" or ".git_state" in normalized
        return ([node.value] if is_private else []), False
    return [], False


def _assert_runtime_import_boundary(tree: ast.AST) -> None:
    observations = [_node_import_targets(node) for node in ast.walk(tree)]
    targets = [target for values, _unknown in observations for target in values]
    dynamic_unknown = [unknown for _values, unknown in observations if unknown]
    normalized = [target.lstrip(".") for target in targets]
    network = [
        target
        for target in normalized
        if target.split(".", 1)[0] in FORBIDDEN_NETWORK_ROOTS
        or target == "trackers"
        or target.startswith("trackers.")
        or ".trackers" in target
    ]
    assert dynamic_unknown == []
    assert [target for target in targets if _forbidden_git_target(target)] == []
    assert network == []


def _assert_runtime_structure(module: Any) -> None:
    tree = ast.parse(inspect.getsource(module))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    over_lines = [
        (node.name, _function_lines(node))
        for node in functions
        if _function_lines(node) > 50
    ]
    over_nesting = [
        (node.name, _nesting_depth(node))
        for node in functions
        if _nesting_depth(node) > 4
    ]
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert over_lines == []
    assert over_nesting == []
    _assert_runtime_import_boundary(tree)
    assert not any(name.endswith("workspace") or "orchestrator" in name for name in imports)


def _write_ownership(
    config: ServiceConfig,
    route: AidtRouteDispatchContract,
    *,
    tombstone: bool,
    manifest_revision: int,
    route_pair_digest: str | None = None,
) -> Path:
    settings = load_aidt_worktree_settings(config)
    assert settings is not None
    paths = stable_worktree_paths(config.workflow_path, route.identifier)
    activate_registry(settings.paths, settings.workflow_identity, NOW_TEXT)
    workspace = (config.workspace_root / route.identifier).resolve()
    record = OwnershipRecord(
        AIDT_WORKTREE_OWNERSHIP_SCHEMA,
        1,
        route.identifier,
        route.service,
        str(config.workspace_root),
        str(workspace),
        str(paths.manifest),
        route_pair_digest or route.route_pair_digest,
        manifest_revision,
        tombstone,
        NOW_TEXT,
        NOW_TEXT,
    )
    persist_ownership(paths.ownership, record, expected_revision=None)
    return workspace


def _snapshot(
    route: AidtRouteDispatchContract, phase: str, *, target: bool = False
) -> RepositorySnapshot:
    retained = phase == "cleanup_post"
    return RepositorySnapshot(
        phase=phase,
        observed_at=NOW_TEXT,
        repository_binding_digest=route.repository_binding_digest,
        root_head=SHA1,
        root_symbolic_digest=DIGEST,
        root_status_digest=DIGEST,
        root_content_digest=DIGEST,
        root_content_count=0,
        root_content_bytes=0,
        registry_digest=DIGEST,
        registry_count=0,
        protected_digest=DIGEST,
        protected_count=0,
        refs_digest=DIGEST,
        refs_count=1,
        base_ref_sha=SHA1,
        target_ref_sha=SHA1 if target or retained else None,
        target_registration_digest=DIGEST if target else None,
    )


def _manifest_base(
    config: ServiceConfig, route: AidtRouteDispatchContract
) -> AidtWorktreeManifest:
    settings = load_aidt_worktree_settings(config)
    assert settings is not None
    scope = RouteScope(
        route.identifier, route.coordinator, route.service, route.kind,
        route.issue_type, route.change_kind, route.route_pair_digest,
        route.route_fingerprint, route.coordinator_fingerprint,
        route.source_revision, route.catalog_revision, route.checkout_revision,
        route.repository_binding_digest,
    )
    pre = PreProof(
        _snapshot(route, "s0"), _snapshot(route, "s1"), DIGEST
    )
    service_root = (Path(config.raw["aidt_routing"]["aidt_root"]) / route.checkout).resolve()
    return AidtWorktreeManifest(
        AIDT_WORKTREE_SCHEMA, 1, "prepared", route.identifier, route.coordinator,
        route.service, route.kind, settings.workflow_identity, settings.board_identity,
        str(config.workspace_root), str((config.workspace_root / route.identifier).resolve()),
        route.checkout, str(service_root), "c" * 64, "sha1", route.route_pair_digest,
        route.repository_binding_digest, route.route_fingerprint,
        route.coordinator_fingerprint, route.source_revision, route.catalog_revision,
        route.branch, AIDT_WORKTREE_BASE_REF, route.checkout_revision, scope, pre,
        None, None, NOW_TEXT, NOW_TEXT,
    )


def _persist_manifest_state(
    config: ServiceConfig, route: AidtRouteDispatchContract, state: str
) -> AidtWorktreeManifest:
    settings = load_aidt_worktree_settings(config)
    assert settings is not None
    activate_registry(settings.paths, settings.workflow_identity, NOW_TEXT)
    paths = stable_worktree_paths(config.workflow_path, route.identifier)
    prepared = _manifest_base(config, route)
    persist_manifest(paths.manifest, prepared, expected_revision=None)
    post = PostProof(
        _snapshot(route, "s2", target=True), DIGEST, SHA1, DIGEST, True, True
    )
    ready = replace(prepared, manifest_revision=2, state="ready", post_proof=post)
    persist_manifest(paths.manifest, ready, expected_revision=1)
    if state == "ready":
        return ready
    partial = RemovalProof(
        "7" * 64, _snapshot(route, "cleanup_pre", target=True), None, None, SHA1
    )
    removing = replace(ready, manifest_revision=3, state="removing", removal_proof=partial)
    persist_manifest(paths.manifest, removing, expected_revision=2)
    complete = replace(
        partial,
        post_snapshot=_snapshot(route, "cleanup_post"),
        remove_delta_digest="8" * 64,
    )
    removed = replace(removing, manifest_revision=4, state="removed", removal_proof=complete)
    persist_manifest(paths.manifest, removed, expected_revision=3)
    return removed


def _seed_failure(
    generation: Any,
    route: AidtRouteDispatchContract,
    now: datetime,
    category: str,
) -> AttemptRecord:
    paths = stable_worktree_paths(generation.config.workflow_path, route.identifier)
    initial = initial_attempt_record(
        route.identifier, route.route_pair_digest, generation.workflow_generation, now
    )
    persist_attempt(paths.attempt, initial, expected_revision=None)
    consumed = admit_attempt(
        paths,
        1,
        route.route_pair_digest,
        generation.workflow_generation,
        now,
        scope_attested=True,
    ).record
    failed = next_failure_record(consumed, category, "none", None, now)
    persist_attempt(paths.attempt, failed, expected_revision=consumed.record_revision)
    return failed


def _mark_attempt_ready(
    generation: Any, route: AidtRouteDispatchContract, now: datetime
) -> AttemptRecord:
    paths = stable_worktree_paths(generation.config.workflow_path, route.identifier)
    record = read_attempt(paths.attempt)
    prepared = advance_attempt_phase(record, "prepared", 1, now)
    persist_attempt(paths.attempt, prepared, expected_revision=record.record_revision)
    added = advance_attempt_phase(prepared, "added", 1, now)
    persist_attempt(paths.attempt, added, expected_revision=prepared.record_revision)
    ready = ready_attempt_record(added, 2, now)
    persist_attempt(paths.attempt, ready, expected_revision=added.record_revision)
    return ready


def _mark_ready(generation: Any, route: AidtRouteDispatchContract, now: datetime) -> AttemptRecord:
    manifest = _persist_manifest_state(generation.config, route, "ready")
    _write_ownership(
        generation.config,
        route,
        tombstone=False,
        manifest_revision=manifest.manifest_revision,
    )
    return _mark_attempt_ready(generation, route, now)


class ReadyLockProbe:
    def __init__(self) -> None:
        self.real_lock = manifest_module.advisory_lock
        self.expected: Path | None = None
        self.active: list[Path] = []
        self.lock_paths: list[Path] = []
        self.events: list[tuple[str, bool]] = []

    @contextmanager
    def lock(self, path: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
        self.lock_paths.append(path)
        with self.real_lock(path, timeout_seconds=timeout_seconds):
            self.active.append(path)
            try:
                yield
            finally:
                assert self.active.pop() == path

    def tracked(self, kind: str, function: Any) -> Callable[..., Any]:
        def call(*args: object, **kwargs: object) -> Any:
            is_active = self.expected is not None and self.active == [self.expected]
            self.events.append((kind, is_active))
            return function(*args, **kwargs)

        return call

    def install(self, harness: Harness, patch: pytest.MonkeyPatch) -> None:
        readers = (
            ("manifest", "read_manifest"),
            ("manifest", "read_optional_manifest"),
            ("ownership", "read_ownership"),
            ("ownership", "read_optional_ownership"),
            ("attempt", "read_attempt"),
            ("optional_attempt", "read_optional_attempt"),
            ("persist", "persist_attempt"),
        )
        _patch_manifest_call(harness, patch, "advisory_lock", self.lock)
        for kind, name in readers:
            function = getattr(manifest_module, name)
            _patch_manifest_call(harness, patch, name, self.tracked(kind, function))

    def begin(self, expected: Path) -> tuple[int, int]:
        self.expected = expected
        return len(self.events), len(self.lock_paths)

    def assert_since(self, mark: tuple[int, int]) -> None:
        assert self.expected is not None
        events = self.events[mark[0] :]
        locks = self.lock_paths[mark[1] :]
        assert len(locks) >= 2 and all(path == self.expected for path in locks)
        assert events[:2] == [
            ("optional_attempt", False),
            ("attempt", False),
        ]
        assert [event for event in events if not event[1]] == events[:2]
        active_kinds = {
            "attempt" if kind == "optional_attempt" else kind
            for kind, active in events
            if active
        }
        assert {"manifest", "ownership", "attempt", "persist"} <= active_kinds, events


def _assert_invalid_ready_evidence(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = (
        (_route("A20-1191--viewer-api"), "missing"),
        (_route("A20-1192--viewer-api"), "owner-mismatch"),
    )
    prepare_calls = list(harness.provisioner.prepare_calls)
    probe = ReadyLockProbe()

    for route, shape in cases:
        harness.loader.values[route.identifier] = route
        initial = harness.runtime.admit_candidate(harness.generation, route.identifier)
        _assert_result(initial, DelegateDisposition.HANDLED)
        if shape == "owner-mismatch":
            manifest = _persist_manifest_state(harness.config, route, "ready")
            _write_ownership(
                harness.config,
                route,
                tombstone=False,
                manifest_revision=manifest.manifest_revision,
                route_pair_digest="8" * 64,
            )
        _mark_attempt_ready(harness.generation, route, harness.clock.value)
        paths = stable_worktree_paths(harness.config.workflow_path, route.identifier)
        mark = probe.begin(paths.manifest_lock)
        with monkeypatch.context() as patch:
            probe.install(harness, patch)
            result = harness.runtime.admit_candidate(harness.generation, route.identifier)
        _assert_result(result, DelegateDisposition.OWNED_ERROR, "registry_invalid")
        record = read_attempt(paths.attempt)
        assert record.disposition == "manual" and record.category == "registry_invalid"
        probe.assert_since(mark)
    assert harness.provisioner.prepare_calls == prepare_calls


def _assert_initializer_concurrency(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _harness(root, monkeypatch)
    second = _harness(root, monkeypatch)
    start = Barrier(2)
    reads = Barrier(2)
    real_read = manifest_module.read_optional_attempt

    def simultaneous_read(path: Path) -> AttemptRecord | None:
        record = real_read(path)
        if record is None:
            reads.wait(timeout=5)
        return record

    def contend(harness: Harness) -> DelegateResult[Any]:
        start.wait(timeout=5)
        return harness.runtime.admit_candidate(harness.generation, IDENTIFIER)

    with monkeypatch.context() as patch:
        _patch_manifest_call(first, patch, "read_optional_attempt", simultaneous_read)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(contend, harness) for harness in (first, second)]
            results = [future.result(timeout=10) for future in futures]
    handled = [item for item in results if item.disposition is DelegateDisposition.HANDLED]
    owned = [item for item in results if item.disposition is DelegateDisposition.OWNED_ERROR]
    assert len(handled) == 1
    assert isinstance(handled[0].value, AidtProvisioningAdmission)
    assert handled[0].value.attempt_record_revision == 2
    assert len(owned) == 1 and owned[0].category == "cas_mismatch"
    record = read_attempt(
        stable_worktree_paths(first.config.workflow_path, IDENTIFIER).attempt
    )
    assert (record.record_revision, record.attempt) == (2, 1)
    assert first.provisioner.prepare_calls == second.provisioner.prepare_calls == []


def _assert_publish_races(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create = _harness(root / "create-race", monkeypatch)
    admitted = create.runtime.admit_candidate(create.generation, IDENTIFIER).value
    create.provisioner.created_now = False
    changed = _config(root / "create-race", enabled=True, workspace="workspace-b")
    create.provisioner.prepare_hook = lambda: create.runtime.publish(changed)
    result = create.runtime.create_or_reuse(create.generation, admitted)
    _assert_result(result, DelegateDisposition.OWNED_ERROR, "scope_changed")
    health = create.runtime.health_snapshot()
    assert (health.create_count, health.resume_count) == (1, 0)
    assert (health.failure_count, health.last_category) == (1, "scope_changed")

    barrier = _harness(root / "barrier-race", monkeypatch)
    admitted = barrier.runtime.admit_candidate(barrier.generation, IDENTIFIER).value
    prepared = barrier.runtime.create_or_reuse(barrier.generation, admitted).value
    changed = _config(root / "barrier-race", enabled=True, workspace="workspace-b")
    barrier.provisioner.attest_hook = lambda: barrier.runtime.publish(changed)
    result = barrier.runtime.before_run(barrier.generation, prepared.guard)
    _assert_result(result, DelegateDisposition.OWNED_ERROR, "scope_changed")


def _verify_failed_reload(
    harness: Harness, changed: ServiceConfig, category: str
) -> None:
    old = harness.generation
    before = harness.runtime.health_snapshot().failure_count
    with pytest.raises(AidtWorktreeFailure, match=category):
        harness.runtime.publish(changed)
    health = harness.runtime.health_snapshot()
    assert health.failure_count == before
    assert health.workflow_generation == old.workflow_generation
    harness.runtime.reject_reload(category)
    rejected = harness.runtime.health_snapshot()
    assert rejected.failure_count == before + 1
    assert rejected.workflow_generation == old.workflow_generation
    result = harness.runtime.before_run(old, _guard(old, harness.route))
    _assert_result(result, DelegateDisposition.OWNED_PRESERVED, category)
    assert harness.provisioner.attest_calls == []


def _assert_reload_recovery(harness: Harness) -> None:
    old = harness.generation
    recovered = harness.runtime.publish(harness.config)
    assert recovered is old
    guard = _issue_guard(harness, recovered, harness.route)
    result = harness.runtime.before_run(recovered, guard)
    _assert_result(result, DelegateDisposition.HANDLED)
    assert harness.provisioner.attest_calls == [guard]


def _assert_failed_material_reloads(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = _harness(root / "factory-reload", monkeypatch)
    changed = _config(root / "factory-reload", enabled=True, workspace="workspace-b")
    factory.factory.error = RuntimeError("hostile factory detail")
    _verify_failed_reload(factory, changed, "internal_error")
    factory.factory.error = None
    _assert_reload_recovery(factory)
    assert len(factory.factory.calls) == 2

    activation = _harness(root / "activation-reload", monkeypatch)
    changed = _config(root / "activation-reload", enabled=True, workspace="workspace-b")
    with monkeypatch.context() as patch:
        patch.setattr(
            activation.module, "activate_registry", _raise_failure("registry_invalid")
        )
        _verify_failed_reload(activation, changed, "registry_invalid")
    _assert_reload_recovery(activation)
    assert len(activation.factory.calls) == 1


def _raise_failure(category: str) -> Callable[..., None]:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AidtWorktreeFailure(category, IDENTIFIER)

    return fail


def _patch_manifest_call(
    harness: Harness,
    patch: pytest.MonkeyPatch,
    name: str,
    value: object,
) -> None:
    patch.setattr(manifest_module, name, value)
    if hasattr(harness.module, name):
        patch.setattr(harness.module, name, value)


def _trigger_activation_fatal(root: Path, patch: pytest.MonkeyPatch) -> Harness:
    harness = _unpublished_harness(root, patch)
    patch.setattr(harness.module, "activate_registry", _raise_failure("durability_failed"))
    with pytest.raises(AidtWorktreeFailure, match="durability_failed"):
        harness.runtime.publish(harness.config)
    return harness


def _trigger_initial_fatal(root: Path, patch: pytest.MonkeyPatch) -> Harness:
    harness = _harness(root, patch)
    _patch_manifest_call(
        harness, patch, "persist_attempt", _raise_failure("durability_failed")
    )
    result = harness.runtime.admit_candidate(harness.generation, IDENTIFIER)
    _assert_result(result, DelegateDisposition.OWNED_ERROR, "durability_failed")
    return harness


def _trigger_consume_fatal(root: Path, patch: pytest.MonkeyPatch) -> Harness:
    harness = _harness(root, patch)
    real_persist = manifest_module.persist_attempt
    calls = 0

    def fail_consumption(
        path: Path, record: AttemptRecord, *, expected_revision: int | None
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AidtWorktreeFailure("durability_failed", IDENTIFIER)
        return real_persist(path, record, expected_revision=expected_revision)

    _patch_manifest_call(harness, patch, "persist_attempt", fail_consumption)
    result = harness.runtime.admit_candidate(harness.generation, IDENTIFIER)
    _assert_result(result, DelegateDisposition.OWNED_ERROR, "durability_failed")
    assert calls == 2
    return harness


def _trigger_reset_fatal(root: Path, patch: pytest.MonkeyPatch) -> Harness:
    harness = _harness(root, patch)
    admitted = harness.runtime.admit_candidate(harness.generation, IDENTIFIER)
    _assert_result(admitted, DelegateDisposition.HANDLED)
    harness.loader.values[IDENTIFIER] = _route(pair="9" * 64)
    _patch_manifest_call(
        harness, patch, "persist_attempt", _raise_failure("durability_failed")
    )
    result = harness.runtime.admit_candidate(harness.generation, IDENTIFIER)
    _assert_result(result, DelegateDisposition.OWNED_ERROR, "durability_failed")
    return harness


def _trigger_prepare_fatal(root: Path, patch: pytest.MonkeyPatch) -> Harness:
    harness = _harness(root, patch)
    admission = harness.runtime.admit_candidate(harness.generation, IDENTIFIER).value
    harness.provisioner.prepare_error = AidtWorktreeFailure("persistence_failed", IDENTIFIER)
    result = harness.runtime.create_or_reuse(harness.generation, admission)
    _assert_result(result, DelegateDisposition.OWNED_ERROR, "persistence_failed")
    return harness


def _trigger_invalid_clock_fatal(
    root: Path, patch: pytest.MonkeyPatch, value: datetime
) -> Harness:
    invalid = MutableClock(value)
    harness = _unpublished_harness(root, patch, clock=invalid)
    with pytest.raises(AidtWorktreeFailure, match="clock_invalid"):
        harness.runtime.publish(harness.config)
    harness.clock.value = NOW
    return harness


def _trigger_naive_clock_fatal(root: Path, patch: pytest.MonkeyPatch) -> Harness:
    return _trigger_invalid_clock_fatal(root, patch, datetime(2026, 7, 21, 1, 2, 3))


def _trigger_offset_clock_fatal(root: Path, patch: pytest.MonkeyPatch) -> Harness:
    offset = timezone(timedelta(hours=9))
    return _trigger_invalid_clock_fatal(
        root, patch, datetime(2026, 7, 21, 10, 2, 3, tzinfo=offset)
    )


def _assert_fatal_latched(harness: Harness, category: str) -> None:
    health = harness.runtime.health_snapshot()
    assert health.status == "fatal" and health.failure_count == 1
    if harness.generation is not None:
        assert harness.runtime.publish(harness.config) is harness.generation
    changed = _config(
        harness.config.workflow_path.parent, enabled=True, workspace="fatal-workspace-b"
    )
    with pytest.raises(AidtWorktreeFailure, match=category):
        harness.runtime.publish(changed)
    disabled = _config(harness.config.workflow_path.parent, enabled=False)
    with pytest.raises(AidtWorktreeFailure, match=category):
        harness.runtime.publish(disabled)
    harness.runtime.reject_reload("profile_invalid")
    harness.runtime.reject_reload("card_invalid")
    assert harness.runtime.health_snapshot().failure_count == 1
    if harness.generation is None:
        return
    create = harness.runtime.create_or_reuse(
        harness.generation, _admission(harness.generation, harness.route)
    )
    barrier = harness.runtime.before_run(
        harness.generation, _guard(harness.generation, harness.route)
    )
    _assert_result(create, DelegateDisposition.OWNED_ERROR, category)
    _assert_result(barrier, DelegateDisposition.OWNED_ERROR, category)
    _assert_result(
        harness.runtime.path_for(harness.generation, UNMANAGED_ID),
        DelegateDisposition.UNMANAGED,
    )
    assert harness.runtime.health_snapshot().failure_count == 1


def _assert_health_snapshot_is_memory_only(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = harness.runtime.health_snapshot()
    clock_calls = harness.clock.calls
    prepare_calls = list(harness.provisioner.prepare_calls)
    attest_calls = list(harness.provisioner.attest_calls)
    cleanup_calls = list(harness.provisioner.cleanup_calls)
    harness.clock.error = AssertionError("health clock read")
    harness.provisioner.prepare_error = AssertionError("health provisioner call")
    harness.provisioner.attest_error = AssertionError("health provisioner call")
    harness.provisioner.cleanup_error = AssertionError("health provisioner call")
    harness.loader.values[IDENTIFIER] = AssertionError("health route read")

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("health performed I/O")

    readers = (
        "discover_registry",
        "read_activation",
        "read_optional_attempt",
        "read_optional_manifest",
        "read_optional_ownership",
        "registry_recognizes_identifier",
        "registry_recognizes_path",
    )
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", explode)
        patch.setattr(os, "scandir", explode)
        patch.setattr(Path, "lstat", explode)
        patch.setattr(harness.module, "load_route_dispatch_contract", explode)
        for name in readers:
            patch.setattr(manifest_module, name, explode)
        first = harness.runtime.health_snapshot()
        second = harness.runtime.health_snapshot()
    harness.clock.error = None
    assert first == second == expected
    assert harness.clock.calls == clock_calls
    assert harness.provisioner.prepare_calls == prepare_calls
    assert harness.provisioner.attest_calls == attest_calls
    assert harness.provisioner.cleanup_calls == cleanup_calls


def _post_prepare_clock_gaps(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    gaps = []
    for action in ("provision", "resume"):
        harness = _harness(root / action, monkeypatch)
        admission = harness.runtime.admit_candidate(
            harness.generation, IDENTIFIER
        ).value
        if action == "resume":
            _mark_ready(harness.generation, harness.route, harness.clock.value)
            admission = harness.runtime.admit_candidate(
                harness.generation, IDENTIFIER
            ).value
        assert admission.action == action
        harness.provisioner.prepare_hook = lambda: setattr(
            harness.clock, "error", AssertionError("post-prepare clock")
        )
        result = harness.runtime.create_or_reuse(harness.generation, admission)
        health = harness.runtime.health_snapshot()
        actual = (
            result.disposition, result.category, len(harness.provisioner.prepare_calls),
            health.create_count, health.resume_count, health.failure_count,
            health.status, health.last_category,
        )
        counts = (1, 0) if action == "provision" else (0, 1)
        expected = (
            DelegateDisposition.OWNED_ERROR, "clock_invalid", 1,
            counts[0], counts[1], 1, "fatal", "clock_invalid",
        )
        if actual != expected:
            gaps.append(f"{action}:{actual!r}")
    return gaps


def test_never_enabled_unmanaged_runtime_is_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_lazy_facade()
    api = _runtime_module()
    harness = _harness(tmp_path, monkeypatch, enabled=False)
    assert harness.module is api
    _assert_runtime_structure(api)
    generation_fields = [field.name for field in fields(api.AidtWorktreeGeneration)]
    health_fields = [field.name for field in fields(api.AidtWorktreeHealth)]

    assert generation_fields == ["revision", "config", "settings", "workflow_generation"]
    assert health_fields == [
        "enabled", "status", "workflow_generation", "create_count", "resume_count",
        "failure_count", "consecutive_failures", "last_category", "last_ref",
        "last_success_at",
    ]
    assert harness.generation.settings is None
    assert harness.generation.workflow_generation is None
    assert str(harness.config.workflow_path) not in repr(harness.generation)
    gaps = _runtime_dto_gaps(api, harness)
    constructor_gap = _constructor_gap(api, tmp_path, monkeypatch)
    if constructor_gap is not None:
        gaps.append(constructor_gap)
    assert gaps == []
    with pytest.raises(FrozenInstanceError):
        harness.generation.revision = 2
    _assert_result(
        harness.runtime.path_for(harness.generation, UNMANAGED_ID),
        DelegateDisposition.UNMANAGED,
    )
    _assert_result(
        harness.runtime.admit_candidate(harness.generation, UNMANAGED_ID),
        DelegateDisposition.UNMANAGED,
    )
    _assert_result(
        harness.runtime.remove(harness.generation, tmp_path / "other"),
        DelegateDisposition.UNMANAGED,
    )
    assert harness.factory.calls == []
    assert harness.loader.calls == []
    assert not (tmp_path / ".symphony").exists()


def test_stale_or_failed_reload_generation_cannot_reach_backend_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path / "reload", monkeypatch)
    equivalent = _config(tmp_path / "reload", enabled=True)
    assert harness.runtime.publish(equivalent) is harness.generation
    assert len(harness.factory.calls) == 1
    published_config, published_settings, published_clock = harness.factory.calls[0]
    assert published_config == harness.config
    assert published_settings is harness.generation.settings
    assert published_clock is harness.clock
    assert harness.generation.workflow_generation == harness.generation.settings.workflow_generation
    harness.config.raw["test_only_post_publish_mutation"] = "must-not-drive-equality"
    assert harness.runtime.publish(_config(tmp_path / "reload", enabled=True)) is harness.generation
    assert len(harness.factory.calls) == 1

    old_guard = _issue_guard(harness, harness.generation, harness.route)
    stale_route = _route("A20-1196--viewer-api")
    stale_admission = _issue_admission(harness, harness.generation, stale_route)
    prepare_calls = list(harness.provisioner.prepare_calls)
    changed = _config(tmp_path / "reload", enabled=True, workspace="workspace-b")
    current = harness.runtime.publish(changed)
    assert current.revision == harness.generation.revision + 1
    stale_create = harness.runtime.create_or_reuse(
        harness.generation, stale_admission
    )
    stale_barrier = harness.runtime.before_run(
        harness.generation, old_guard
    )
    _assert_result(stale_create, DelegateDisposition.OWNED_ERROR, "scope_changed")
    _assert_result(stale_barrier, DelegateDisposition.OWNED_ERROR, "scope_changed")
    assert harness.provisioner.prepare_calls == prepare_calls
    assert harness.provisioner.attest_calls == []

    current_guard = _issue_guard(
        harness, current, _route("A20-1197--viewer-api")
    )
    harness.runtime.reject_reload("card_invalid")
    rejected = harness.runtime.before_run(current, current_guard)
    _assert_result(rejected, DelegateDisposition.OWNED_PRESERVED, "card_invalid")
    disabled = harness.runtime.publish(_config(tmp_path / "reload", enabled=False))
    preserved = harness.runtime.before_run(disabled, current_guard)
    _assert_result(preserved, DelegateDisposition.OWNED_PRESERVED, "profile_invalid")
    route_managed = harness.runtime.admit_candidate(disabled, IDENTIFIER)
    _assert_result(route_managed, DelegateDisposition.OWNED_PRESERVED, "profile_invalid")
    _assert_publish_races(tmp_path, monkeypatch)
    _assert_failed_material_reloads(tmp_path, monkeypatch)


def test_disabled_corrupt_missing_and_removed_ownership_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _config(tmp_path / "removed", enabled=True, workspace="workspace-a")
    removed_manifest = _persist_manifest_state(source, _route(), "removed")
    workspace = _write_ownership(
        source, _route(), tombstone=True, manifest_revision=removed_manifest.manifest_revision
    )
    removed = _harness(tmp_path / "removed", monkeypatch, enabled=False, workspace="workspace-b")
    handled = removed.runtime.path_for(removed.generation, IDENTIFIER)
    assert handled == DelegateResult.handled(workspace)

    missing_source = _config(tmp_path / "missing", enabled=True)
    _write_ownership(
        missing_source, _route(), tombstone=False, manifest_revision=2
    )
    missing = _harness(tmp_path / "missing", monkeypatch, enabled=False)
    missing_result = missing.runtime.path_for(missing.generation, IDENTIFIER)
    _assert_result(missing_result, DelegateDisposition.OWNED_ERROR, "registry_invalid")

    corrupt_source = _config(tmp_path / "corrupt", enabled=True)
    settings = load_aidt_worktree_settings(corrupt_source)
    assert settings is not None
    activate_registry(settings.paths, settings.workflow_identity, NOW_TEXT)
    corrupt_path = stable_worktree_paths(corrupt_source.workflow_path, IDENTIFIER).attempt
    corrupt_path.write_bytes(b"not canonical json\n")
    corrupt_path.chmod(0o600)
    corrupt = _harness(tmp_path / "corrupt", monkeypatch, enabled=False)
    corrupt_result = corrupt.runtime.path_for(corrupt.generation, IDENTIFIER)
    _assert_result(corrupt_result, DelegateDisposition.OWNED_ERROR, "registry_invalid")
    _assert_result(
        corrupt.runtime.path_for(corrupt.generation, UNMANAGED_ID),
        DelegateDisposition.UNMANAGED,
    )


def test_admission_handles_initial_manual_backoff_due_scope_reset_and_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    initial = harness.runtime.admit_candidate(harness.generation, IDENTIFIER)
    _assert_result(initial, DelegateDisposition.HANDLED)
    assert initial.value.action == "provision"
    assert initial.value.attempt_record_revision == 2

    manual_route = _route("A20-1189--viewer-api")
    harness.loader.values[manual_route.identifier] = manual_route
    _seed_failure(harness.generation, manual_route, NOW, "collision")
    manual = harness.runtime.admit_candidate(harness.generation, manual_route.identifier)
    _assert_result(manual, DelegateDisposition.OWNED_PRESERVED, "collision")

    retry_route = _route("A20-1190--viewer-api")
    harness.loader.values[retry_route.identifier] = retry_route
    _seed_failure(harness.generation, retry_route, NOW, "fetch_timeout")
    blocked = harness.runtime.admit_candidate(harness.generation, retry_route.identifier)
    _assert_result(blocked, DelegateDisposition.OWNED_PRESERVED, "attempt_backoff")
    harness.clock.value = NOW + timedelta(seconds=31)
    due = harness.runtime.admit_candidate(harness.generation, retry_route.identifier)
    _assert_result(due, DelegateDisposition.HANDLED)
    assert due.value.attempt_record_revision == 4

    replacement = _route(retry_route.identifier, pair="9" * 64)
    harness.loader.values[retry_route.identifier] = replacement
    reset = harness.runtime.admit_candidate(harness.generation, retry_route.identifier)
    _assert_result(reset, DelegateDisposition.OWNED_PRESERVED, "scope_changed")
    readmitted = harness.runtime.admit_candidate(harness.generation, retry_route.identifier)
    _assert_result(readmitted, DelegateDisposition.HANDLED)
    assert readmitted.value.route_pair_digest == replacement.route_pair_digest

    ready = _mark_ready(harness.generation, harness.route, harness.clock.value)
    resumed = harness.runtime.admit_candidate(harness.generation, IDENTIFIER)
    _assert_result(resumed, DelegateDisposition.HANDLED)
    assert resumed.value.action == "resume"
    assert resumed.value.attempt_record_revision == ready.record_revision
    _assert_invalid_ready_evidence(harness, monkeypatch)
    _assert_initializer_concurrency(tmp_path / "concurrent", monkeypatch)


def test_ready_restart_admits_resume_once_without_fetch_or_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _harness(tmp_path, monkeypatch)
    admitted = first.runtime.admit_candidate(first.generation, IDENTIFIER)
    _assert_result(admitted, DelegateDisposition.HANDLED)
    ready = _mark_ready(first.generation, first.route, NOW)

    restarted = _harness(tmp_path, monkeypatch)
    resume = restarted.runtime.admit_candidate(restarted.generation, IDENTIFIER)
    _assert_result(resume, DelegateDisposition.HANDLED)
    assert resume.value.action == "resume"
    assert resume.value.attempt_record_revision == ready.record_revision
    prepared = restarted.runtime.create_or_reuse(restarted.generation, resume.value)
    _assert_result(prepared, DelegateDisposition.HANDLED)
    barrier = restarted.runtime.before_run(restarted.generation, prepared.value.guard)
    _assert_result(barrier, DelegateDisposition.HANDLED)

    assert restarted.provisioner.prepare_calls == [resume.value]
    assert restarted.provisioner.attest_calls == [prepared.value.guard]
    health = restarted.runtime.health_snapshot()
    assert (health.create_count, health.resume_count) == (0, 1)
    assert not hasattr(restarted.provisioner, "runner")


def _assert_issued_capabilities_fail_closed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(root, monkeypatch)
    issued = _issue_admission(harness, harness.generation, harness.route)

    unissued = harness.runtime.before_run(
        harness.generation, _guard(harness.generation, harness.route)
    )
    _assert_result(unissued, DelegateDisposition.OWNED_ERROR, "scope_changed")
    assert harness.provisioner.attest_calls == []

    forged_admission = replace(issued, route_pair_digest="f" * 64)
    rejected = harness.runtime.create_or_reuse(harness.generation, forged_admission)
    _assert_result(rejected, DelegateDisposition.OWNED_ERROR, "scope_changed")
    assert harness.provisioner.prepare_calls == []

    entered = Event()
    release = Event()

    def pause_prepare() -> None:
        entered.set()
        assert release.wait(5)

    harness.provisioner.prepare_hook = pause_prepare
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(harness.runtime.create_or_reuse, harness.generation, issued)
        assert entered.wait(5)
        second = pool.submit(harness.runtime.create_or_reuse, harness.generation, issued)
        release.set()
        attempts = (first.result(), second.result())
    handled = [result for result in attempts if result.disposition is DelegateDisposition.HANDLED]
    errors = [result for result in attempts if result.disposition is DelegateDisposition.OWNED_ERROR]
    assert len(handled) == len(errors) == 1 and errors[0].category == "scope_changed"
    assert harness.provisioner.prepare_calls == [issued]

    forged_guard = replace(handled[0].value.guard, route_pair_digest="f" * 64)
    blocked = harness.runtime.before_run(harness.generation, forged_guard)
    _assert_result(blocked, DelegateDisposition.OWNED_ERROR, "scope_changed")
    assert harness.provisioner.attest_calls == []

    wrong = harness.config.workspace_root / "WRONG"
    refused = harness.runtime.remove(harness.generation, wrong, identifier=IDENTIFIER)
    _assert_result(refused, DelegateDisposition.OWNED_ERROR, "path_invalid")
    assert harness.provisioner.cleanup_calls == []


def _assert_ownership_pair_mismatch_fails_closed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(root, enabled=True)
    owner_route = _route("A20-1193--viewer-api")
    manifest = _persist_manifest_state(config, owner_route, "ready")
    owned_path = _write_ownership(
        config,
        owner_route,
        tombstone=False,
        manifest_revision=manifest.manifest_revision,
    )
    harness = _harness(root, monkeypatch)
    recognized_other = _route("A20-1194--viewer-api")
    harness.loader.values[recognized_other.identifier] = recognized_other
    unknown_other = "A20-1195--viewer-api"

    for identifier in (recognized_other.identifier, unknown_other):
        cleanup_calls = list(harness.provisioner.cleanup_calls)
        result = harness.runtime.remove(
            harness.generation, owned_path, identifier=identifier
        )
        assert harness.provisioner.cleanup_calls == cleanup_calls
        _assert_result(result, DelegateDisposition.OWNED_ERROR, "path_invalid")


def test_delegate_converts_post_recognition_exceptions_to_owned_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    unmanaged = harness.runtime.path_for(harness.generation, UNMANAGED_ID)
    _assert_result(unmanaged, DelegateDisposition.UNMANAGED)

    canonical_unmanaged = "A20-1999--viewer-api"
    loader_mark = len(harness.loader.calls)
    unmanaged_child = harness.runtime.admit_candidate(
        harness.generation, canonical_unmanaged
    )
    _assert_result(unmanaged_child, DelegateDisposition.UNMANAGED)
    assert harness.loader.calls[loader_mark:] == [canonical_unmanaged]

    harness.loader.values[IDENTIFIER] = AidtRoutingFailure("route_collision", IDENTIFIER)
    loader_error = harness.runtime.admit_candidate(harness.generation, IDENTIFIER)
    _assert_result(loader_error, DelegateDisposition.OWNED_ERROR, "card_invalid")
    harness.loader.values[IDENTIFIER] = harness.route

    guard = _issue_guard(harness, harness.generation, harness.route)
    failing_route = _route("A20-1998--viewer-api")
    admission = _issue_admission(harness, harness.generation, failing_route)
    harness.provisioner.prepare_error = RuntimeError("/tmp/secret https://evil TOKEN=x")
    prepare_error = harness.runtime.create_or_reuse(harness.generation, admission)
    _assert_result(prepare_error, DelegateDisposition.OWNED_ERROR, "internal_error")
    harness.provisioner.prepare_error = None

    harness.provisioner.attest_error = AidtWorktreeFailure("scope_changed", IDENTIFIER)
    attest_error = harness.runtime.before_run(harness.generation, guard)
    _assert_result(attest_error, DelegateDisposition.OWNED_ERROR, "scope_changed")
    bounded = harness.runtime.health_snapshot()
    assert (bounded.last_category, bounded.last_ref) == ("scope_changed", IDENTIFIER)
    harness.provisioner.attest_error = None

    path = guard.workspace_path
    preserved = harness.runtime.remove(harness.generation, path, identifier=IDENTIFIER)
    _assert_result(preserved, DelegateDisposition.OWNED_PRESERVED, "authorization_invalid")
    failure_count = harness.runtime.health_snapshot().failure_count
    harness.provisioner.cleanup_error = RuntimeError("hostile cleanup detail")
    cleanup_error = harness.runtime.remove(harness.generation, path, identifier=IDENTIFIER)
    _assert_result(cleanup_error, DelegateDisposition.OWNED_ERROR, "internal_error")
    assert harness.runtime.health_snapshot().failure_count == failure_count + 1
    _assert_issued_capabilities_fail_closed(tmp_path / "capabilities", monkeypatch)
    _assert_ownership_pair_mismatch_fails_closed(
        tmp_path / "ownership-pair", monkeypatch
    )


def test_persistence_failure_opens_fatal_circuit_for_process_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = (
        ("activation", _trigger_activation_fatal, "durability_failed"),
        ("initial", _trigger_initial_fatal, "durability_failed"),
        ("consume", _trigger_consume_fatal, "durability_failed"),
        ("scope-reset", _trigger_reset_fatal, "durability_failed"),
        ("prepare", _trigger_prepare_fatal, "persistence_failed"),
        ("clock-naive", _trigger_naive_clock_fatal, "clock_invalid"),
        ("clock-offset", _trigger_offset_clock_fatal, "clock_invalid"),
    )
    for name, trigger, category in cases:
        with monkeypatch.context() as patch:
            harness = trigger(tmp_path / name, patch)
        _assert_fatal_latched(harness, category)


def test_health_counts_create_resume_failure_and_sanitizes_last_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    assert _post_prepare_clock_gaps(tmp_path / "post-prepare", monkeypatch) == []
    harness = _harness(tmp_path, monkeypatch)
    admission = harness.runtime.admit_candidate(harness.generation, IDENTIFIER).value
    created = harness.runtime.create_or_reuse(harness.generation, admission)
    _assert_result(created, DelegateDisposition.HANDLED)
    first = harness.runtime.health_snapshot()
    assert (first.create_count, first.resume_count, first.last_success_at) == (1, 0, NOW_TEXT)

    hostile = "/tmp/private https://user:pass@example.invalid TOKEN=secret A20-9999"
    harness.provisioner.attest_error = RuntimeError(hostile)
    failed = harness.runtime.before_run(harness.generation, created.value.guard)
    _assert_result(failed, DelegateDisposition.OWNED_ERROR, "internal_error")
    assert harness.runtime.health_snapshot().consecutive_failures == 1
    harness.provisioner.attest_error = None

    ready = _mark_ready(harness.generation, harness.route, NOW)
    resumed = harness.runtime.admit_candidate(harness.generation, IDENTIFIER).value
    assert resumed.action == "resume" and resumed.attempt_record_revision == ready.record_revision
    handled = harness.runtime.create_or_reuse(harness.generation, resumed)
    _assert_result(handled, DelegateDisposition.HANDLED)
    recovered = harness.runtime.health_snapshot()
    assert (recovered.create_count, recovered.resume_count) == (1, 1)
    assert recovered.consecutive_failures == 0
    assert recovered.last_category is None and recovered.status == "ready"

    harness.runtime.reject_reload(hostile)
    degraded = harness.runtime.health_snapshot()
    assert degraded.status == "degraded"
    assert degraded.last_category == "internal_error"
    assert degraded.last_ref is None
    assert degraded.failure_count == 2
    assert hostile not in repr(harness.generation)
    assert hostile not in repr(degraded)
    assert hostile not in caplog.text
    _assert_health_snapshot_is_memory_only(harness, monkeypatch)
