"""Fail-closed provisioning state machine for routed AIDT worktrees."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol

from ..aidt_routing import (
    AidtRouteDispatchContract,
    load_route_dispatch_contract,
    observe_service_binding,
)
from ..aidt_routing.contract import RoutingSettings, load_routing_settings
from ..workflow import ServiceConfig
from .contract import (
    AIDT_WORKTREE_BASE_REF,
    AIDT_WORKTREE_OWNERSHIP_SCHEMA,
    AIDT_WORKTREE_SCHEMA,
    AidtWorktreeFailure,
    AidtWorktreeResult,
    AidtWorktreeSettings,
    CompletionAuthorization,
    DelegateResult,
    StableWorktreePaths,
    common_git_lock_path,
    contained_workspace_path,
    stable_worktree_paths,
)
from .git_state import (
    BinaryRunner,
    BindingObserver as GitBindingObserver,
    FetchResult,
    RepositoryIdentity,
    RepositoryState,
    TargetArtifactDisposition,
    TicketWorktreeState,
    add_worktree,
    base_is_ancestor,
    classify_target_artifacts,
    default_binary_runner,
    fetch_production_base,
    observe_repository_identity,
    observe_repository_state,
    observe_ticket_worktree,
    prove_prepared_recovery,
    prove_ready_recovery,
    prove_removed_recovery,
    remove_worktree,
    validate_create_delta,
    validate_fetch_delta,
    validate_remove_delta,
    verify_service_binding,
)
from .manifest import (
    AidtWorktreeManifest,
    AttemptRecord,
    OwnershipRecord,
    PostProof,
    PreProof,
    RemovalProof,
    RepositorySnapshot,
    RouteScope,
    advance_attempt_phase,
    advisory_lock,
    next_failure_record,
    ordered_worktree_locks,
    persist_attempt,
    persist_manifest,
    persist_ownership,
    read_attempt,
    read_optional_manifest,
    read_optional_ownership,
    ready_attempt_record,
)


RouteLoader = Callable[[ServiceConfig, str], AidtRouteDispatchContract | None]
RouteBindingObserver = Callable[[RoutingSettings, str], object]
FaultHook = Callable[[str], None]

_CHILD = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*--[a-z0-9]+(?:-[a-z0-9]+)*$")
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _FailureContext:
    admission: AidtProvisioningAdmission
    paths: StableWorktreePaths
    common_git_lock: Path | None = None


@dataclass(frozen=True)
class AidtProvisioningAdmission:
    identifier: str
    workflow_generation: str
    route_pair_digest: str
    attempt_record_revision: int
    action: Literal["provision", "resume"]

    def __post_init__(self) -> None:
        valid = (
            _valid_child(self.identifier)
            and _valid_digest(self.workflow_generation)
            and _valid_digest(self.route_pair_digest)
            and _valid_revision(self.attempt_record_revision)
            and self.action in {"provision", "resume"}
        )
        if not valid:
            raise AidtWorktreeFailure("internal_error", self.identifier)


@dataclass(frozen=True, repr=False)
class AidtRunGuard:
    identifier: str
    workflow_generation: str
    route_pair_digest: str
    attempt_record_revision: int
    manifest_revision: int
    workspace_path: Path

    def __post_init__(self) -> None:
        valid = (
            _valid_child(self.identifier)
            and _valid_digest(self.workflow_generation)
            and _valid_digest(self.route_pair_digest)
            and _valid_revision(self.attempt_record_revision)
            and _valid_revision(self.manifest_revision)
            and _valid_absolute_path(self.workspace_path)
        )
        if not valid:
            raise AidtWorktreeFailure("internal_error", self.identifier)

    def __repr__(self) -> str:
        return (
            "AidtRunGuard("
            f"identifier={self.identifier!r}, manifest_revision={self.manifest_revision!r})"
        )


@dataclass(frozen=True, repr=False)
class PreparedAidtWorktree:
    result: AidtWorktreeResult
    guard: AidtRunGuard

    def __post_init__(self) -> None:
        if type(self.result) is not AidtWorktreeResult or type(self.guard) is not AidtRunGuard:
            raise AidtWorktreeFailure("internal_error")

    def __repr__(self) -> str:
        return (
            "PreparedAidtWorktree("
            f"created_now={self.result.created_now!r}, "
            f"manifest_revision={self.result.manifest_revision!r}, "
            f"guard={self.guard!r})"
        )


@dataclass(frozen=True, repr=False)
class ActiveCompletionLease:
    identifier: str
    issue_id: str
    run_id: str
    attempt_kind: str
    active: bool
    competing_owner: bool

    def __post_init__(self) -> None:
        valid = (
            _valid_child(self.identifier)
            and self.issue_id == self.identifier
            and type(self.run_id) is str
            and _HEX_32.fullmatch(self.run_id) is not None
            and self.attempt_kind in {"initial", "retry", "reacquired"}
            and type(self.active) is bool
            and type(self.competing_owner) is bool
        )
        if not valid:
            raise AidtWorktreeFailure("authorization_invalid", self.identifier)

    def __repr__(self) -> str:
        return (
            "ActiveCompletionLease("
            f"identifier={self.identifier!r}, attempt_kind={self.attempt_kind!r}, "
            f"active={self.active!r}, competing_owner={self.competing_owner!r})"
        )


class CompletionAuthority(Protocol):
    def verify(
        self,
        authorization: CompletionAuthorization,
        lease: ActiveCompletionLease,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
    ) -> bool: ...


class DenyAllCompletionAuthority:
    def verify(
        self,
        authorization: CompletionAuthorization,
        lease: ActiveCompletionLease,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
    ) -> bool:
        return False


def noop_fault_hook(_seam: str) -> None:
    """Default fault seam used by production."""


class AidtWorktreeProvisioner:
    """Compose route, metadata, and Git proof into one synchronous lifecycle."""

    def __init__(
        self,
        config: ServiceConfig,
        settings: AidtWorktreeSettings,
        *,
        runner: BinaryRunner = default_binary_runner,
        clock: Callable[[], datetime],
        route_loader: RouteLoader = load_route_dispatch_contract,
        binding_observer: RouteBindingObserver = observe_service_binding,
        completion_authority: CompletionAuthority = DenyAllCompletionAuthority(),
        fault_hook: FaultHook = noop_fault_hook,
    ) -> None:
        routing = load_routing_settings(config)
        if routing is None or not settings.enabled:
            raise AidtWorktreeFailure("profile_invalid")
        self._config = config
        self._settings = settings
        self._routing = routing
        self._runner = runner
        self._clock = clock
        self._route_loader = route_loader
        self._binding_observer = binding_observer
        self._completion_authority = completion_authority
        self._fault_hook = fault_hook

    def prepare(self, admission: AidtProvisioningAdmission) -> PreparedAidtWorktree:
        """Create, recover, or resume one exact routed child."""
        context = _failure_context(self._settings, admission)
        try:
            self._require_generation(admission)
            route = self._load_route(admission.identifier, admission.route_pair_digest)
            identity, paths = self._identity_and_paths(route)
            common = common_git_lock_path(paths, identity.common_git_identity)
            context = replace(context, paths=paths, common_git_lock=common)
            return self._prepare(admission, route, identity, paths)
        except Exception as exc:
            failure = _bounded_failure(exc, admission.identifier)
            self._persist_failure(context, failure)
            raise failure from None

    def attest_before_run(self, guard: AidtRunGuard) -> None:
        """Repeat the ready proof immediately before backend construction."""
        if guard.workflow_generation != self._settings.workflow_generation:
            raise AidtWorktreeFailure("scope_changed", guard.identifier)
        route = self._load_route(guard.identifier, guard.route_pair_digest)
        identity, paths = self._identity_and_paths(route)
        common = common_git_lock_path(paths, identity.common_git_identity)
        with ordered_worktree_locks(common, paths.manifest_lock):
            route = self._recheck_route(route)
            manifest = read_optional_manifest(paths.manifest)
            attempt = read_attempt(paths.attempt)
            if manifest is None:
                raise AidtWorktreeFailure("scope_changed", guard.identifier)
            self._require_manifest(route, identity, paths, manifest)
            self._require_aligned_owner(paths, manifest, tombstone=False)
            expected = (
                manifest.identifier,
                self._settings.workflow_generation,
                manifest.route_pair_digest,
                attempt.record_revision,
                manifest.manifest_revision,
                Path(manifest.workspace_path),
            )
            actual = (
                guard.identifier,
                guard.workflow_generation,
                guard.route_pair_digest,
                guard.attempt_record_revision,
                guard.manifest_revision,
                guard.workspace_path,
            )
            if actual != expected:
                raise AidtWorktreeFailure("scope_changed", guard.identifier)
            self._resume_ready(route, identity, manifest, attempt)

    def cleanup(
        self,
        identifier: str,
        workspace_path: Path,
        *,
        authorization: CompletionAuthorization | None = None,
        lease: ActiveCompletionLease | None = None,
    ) -> DelegateResult[None]:
        """Preserve by default; remove only with injected verified authority."""
        try:
            return self._cleanup(identifier, workspace_path, authorization, lease)
        except AidtWorktreeFailure:
            raise
        except Exception:
            raise AidtWorktreeFailure("internal_error", identifier) from None

    def _prepare(
        self,
        admission: AidtProvisioningAdmission,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
    ) -> PreparedAidtWorktree:
        common = common_git_lock_path(paths, identity.common_git_identity)
        with ordered_worktree_locks(common, paths.manifest_lock):
            route = self._recheck_route(route)
            attempt = self._attempt_for_admission(paths.attempt, admission)
            manifest = read_optional_manifest(paths.manifest)
            if manifest is None:
                return self._create_new(route, identity, paths, attempt)
            self._require_manifest(route, identity, paths, manifest)
            if manifest.state == "prepared":
                return self._recover_prepared(route, identity, paths, manifest, attempt)
            if manifest.state == "ready":
                self._prove_ready(route, identity, manifest)
                attempt = self._reconcile_ready_sidecars(paths, manifest, attempt)
                self._require_ready_attempt(route, manifest, attempt)
                return self._prepared_result(manifest, attempt, created_now=False)
            raise AidtWorktreeFailure("collision", admission.identifier)

    def _create_new(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        attempt: AttemptRecord,
    ) -> PreparedAidtWorktree:
        stable = paths
        workspace = contained_workspace_path(self._settings.workspace_root, route.identifier)
        disposition = classify_target_artifacts(
            identity,
            route.branch,
            workspace,
            route.checkout_revision,
            runner=self._runner,
        )
        if disposition is not TargetArtifactDisposition.ABSENT:
            raise AidtWorktreeFailure("collision", route.identifier)
        s0 = self._observe(identity, "s0", route, workspace)
        fetched = self._fetch(identity, route)
        if (
            fetched.base_sha != route.checkout_revision
            or fetched.repository_binding_digest != route.repository_binding_digest
        ):
            raise AidtWorktreeFailure("binding_invalid", route.identifier)
        s1 = self._observe(identity, "s1", route, workspace)
        fetch_delta = validate_fetch_delta(s0, s1)
        self._fault_hook("after_forced_fetch_before_prepared")
        current = self._load_route(route.identifier, route.route_pair_digest)
        _require_same_route(route, current)
        manifest = self._prepared_manifest(current, identity, workspace, s0.snapshot, s1.snapshot, fetch_delta)
        persist_manifest(stable.manifest, manifest, expected_revision=None)
        ownership = self._new_ownership(stable, manifest)
        persist_ownership(stable.ownership, ownership, expected_revision=None)
        attempt = self._advance_attempt(stable.attempt, attempt, "prepared", 1)
        self._fault_hook("after_prepared_fsync_before_add")
        return self._add_and_finalize(
            current, identity, stable, manifest, ownership, attempt, s1, True
        )

    def _recover_prepared(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        attempt: AttemptRecord,
    ) -> PreparedAidtWorktree:
        stable = paths
        workspace = Path(manifest.workspace_path)
        attempt = self._reconcile_prepared_sidecars(stable, manifest, attempt)
        binding = self._verify_binding(route)
        proof = prove_prepared_recovery(
            identity,
            manifest.pre_proof.s1,
            binding,
            manifest.branch,
            workspace,
            self._timestamp(),
            runner=self._runner,
        )
        if proof.ticket is None:
            return self._add_and_finalize(
                route, identity, stable, manifest, None, attempt, proof.state, True
            )
        if proof.create_delta_digest is None:
            raise AidtWorktreeFailure("protocol_invalid", route.identifier)
        return self._finalize_verified(
            stable,
            manifest,
            None,
            attempt,
            proof.state.snapshot,
            proof.create_delta_digest,
            proof.ticket,
            False,
        )

    def _add_and_finalize(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        ownership: OwnershipRecord | None,
        attempt: AttemptRecord,
        before: RepositoryState,
        created_now: bool,
    ) -> PreparedAidtWorktree:
        stable = paths
        self._recheck_route(route)
        self._verify_binding(route)
        add_worktree(
            identity, manifest.branch, Path(manifest.workspace_path), manifest.base_sha, runner=self._runner
        )
        self._fault_hook("after_add_before_verification")
        return self._verify_created(
            route, identity, stable, manifest, ownership, attempt, before, created_now
        )

    def _verify_created(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        ownership: OwnershipRecord | None,
        attempt: AttemptRecord,
        before: RepositoryState,
        created_now: bool,
    ) -> PreparedAidtWorktree:
        stable = paths
        workspace = Path(manifest.workspace_path)
        s2 = self._observe(identity, "s2", route, workspace)
        create_delta = validate_create_delta(before, s2)
        ticket = observe_ticket_worktree(workspace, manifest.branch, runner=self._runner)
        return self._finalize_verified(
            stable, manifest, ownership, attempt, s2.snapshot, create_delta, ticket, created_now
        )

    def _finalize_verified(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        ownership: OwnershipRecord | None,
        attempt: AttemptRecord,
        s2: RepositorySnapshot,
        create_delta: str,
        ticket: TicketWorktreeState,
        created_now: bool,
    ) -> PreparedAidtWorktree:
        workspace = Path(manifest.workspace_path)
        if ticket.head != manifest.base_sha or not ticket.clean or not ticket.no_upstream:
            raise AidtWorktreeFailure("identity_invalid", manifest.identifier)
        if not base_is_ancestor(workspace, manifest.base_sha, ticket.head, runner=self._runner):
            raise AidtWorktreeFailure("base_invalid", manifest.identifier)
        attempt = self._ensure_added_attempt(paths.attempt, attempt, manifest.manifest_revision)
        self._fault_hook("after_verification_before_ready_fsync")
        return self._persist_ready(
            paths, manifest, ownership, attempt, s2, create_delta, ticket.head, created_now
        )

    def _persist_ready(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        ownership: OwnershipRecord | None,
        attempt: AttemptRecord,
        s2: RepositorySnapshot,
        create_delta: str,
        ticket_head: str,
        created_now: bool,
    ) -> PreparedAidtWorktree:
        stable = paths
        registration = s2.target_registration_digest
        if registration is None:
            raise AidtWorktreeFailure("protocol_invalid", manifest.identifier)
        post = PostProof(
            s2=s2,
            create_delta_digest=create_delta,
            ticket_head=ticket_head,
            registration_digest=registration,
            clean_at_create=True,
            no_upstream=True,
        )
        ready = replace(
            manifest, manifest_revision=2, state="ready", post_proof=post, updated_at=self._timestamp()
        )
        persist_manifest(stable.manifest, ready, expected_revision=1)
        current_owner = ownership or read_optional_ownership(stable.ownership)
        if current_owner is None:
            raise AidtWorktreeFailure("registry_invalid", ready.identifier)
        if read_optional_ownership(stable.ownership) != current_owner:
            raise AidtWorktreeFailure("registry_invalid", ready.identifier)
        self._advance_owner(stable, ready, tombstone=False)
        ready_attempt = ready_attempt_record(attempt, 2, self._now())
        persist_attempt(stable.attempt, ready_attempt, expected_revision=attempt.record_revision)
        return self._prepared_result(ready, ready_attempt, created_now=created_now)

    def _reconcile_prepared_sidecars(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        attempt: AttemptRecord,
    ) -> AttemptRecord:
        expected_owner = self._new_ownership(paths, manifest)
        owner = read_optional_ownership(paths.ownership)
        if owner is None:
            persist_ownership(paths.ownership, expected_owner, expected_revision=None)
        elif owner != expected_owner:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)
        if attempt.mutation_phase == "none" and attempt.manifest_revision is None:
            return self._advance_attempt(paths.attempt, attempt, "prepared", 1)
        if attempt.mutation_phase not in {"prepared", "added"} or attempt.manifest_revision != 1:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)
        return attempt

    def _reconcile_ready_sidecars(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        attempt: AttemptRecord,
    ) -> AttemptRecord:
        self._ready_owner(paths, manifest)
        if attempt.disposition == "ready":
            return attempt
        if attempt.mutation_phase != "added" or attempt.manifest_revision != 1:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)
        ready_attempt = ready_attempt_record(attempt, 2, self._now())
        persist_attempt(paths.attempt, ready_attempt, expected_revision=attempt.record_revision)
        return ready_attempt

    def _ready_owner(
        self, paths: StableWorktreePaths, manifest: AidtWorktreeManifest
    ) -> OwnershipRecord:
        owner = read_optional_ownership(paths.ownership)
        if owner is None or owner.manifest_revision not in {1, 2} or owner.tombstone:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)
        if owner.manifest_revision == 1:
            return self._advance_owner(paths, manifest, tombstone=False)
        self._require_owner_revision(paths, manifest, owner, 2, False, aligned=True)
        return owner

    def _resume_ready(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        manifest: AidtWorktreeManifest,
        attempt: AttemptRecord,
    ) -> None:
        self._prove_ready(route, identity, manifest)
        self._require_ready_attempt(route, manifest, attempt)

    def _prove_ready(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        manifest: AidtWorktreeManifest,
    ) -> None:
        self._require_manifest_route(route, identity, manifest)
        binding = self._verify_binding(route)
        workspace = Path(manifest.workspace_path)
        post = manifest.post_proof
        if post is None:
            raise AidtWorktreeFailure("manifest_invalid", route.identifier)
        prove_ready_recovery(
            identity,
            post.s2,
            binding,
            manifest.branch,
            workspace,
            self._timestamp(),
            phase="resume",
            runner=self._runner,
        )

    def _require_ready_attempt(
        self,
        route: AidtRouteDispatchContract,
        manifest: AidtWorktreeManifest,
        attempt: AttemptRecord,
    ) -> None:
        exact = (
            attempt.identifier == route.identifier
            and attempt.route_pair_digest == route.route_pair_digest
            and attempt.workflow_generation == self._settings.workflow_generation
            and attempt.disposition == "ready"
            and attempt.category == "ready"
            and attempt.retry_at is None
            and attempt.mutation_phase == "added"
            and attempt.manifest_revision == manifest.manifest_revision == 2
        )
        if not exact:
            raise AidtWorktreeFailure("scope_changed", route.identifier)

    def _cleanup(
        self,
        identifier: str,
        workspace_path: Path,
        authorization: CompletionAuthorization | None,
        lease: ActiveCompletionLease | None,
    ) -> DelegateResult[None]:
        paths = stable_worktree_paths(self._settings.workflow_path, identifier)
        manifest = read_optional_manifest(paths.manifest)
        if manifest is None:
            raise AidtWorktreeFailure("manifest_invalid", identifier)
        if Path(manifest.workspace_path) != workspace_path:
            raise AidtWorktreeFailure("path_invalid", identifier)
        route = self._load_route(identifier, manifest.route_pair_digest)
        identity, bound_paths = self._identity_and_paths(route)
        if bound_paths != paths:
            raise AidtWorktreeFailure("identity_invalid", identifier)
        common = common_git_lock_path(paths, identity.common_git_identity)
        with ordered_worktree_locks(common, paths.manifest_lock):
            route = self._recheck_route(route)
            manifest = read_optional_manifest(paths.manifest)
            if manifest is None:
                raise AidtWorktreeFailure("manifest_invalid", identifier)
            self._require_manifest(route, identity, paths, manifest)
            binding = self._verify_binding(route)
            if manifest.state == "ready":
                self._require_aligned_owner(paths, manifest, tombstone=False)
                self._require_ready_attempt(route, manifest, read_attempt(paths.attempt))
                if not self._authorized(authorization, lease, manifest, route):
                    return DelegateResult.owned_preserved("authorization_invalid")
                assert authorization is not None
                self._begin_removal(
                    identity, paths, manifest, route, binding, authorization
                )
                return DelegateResult.handled()
            if manifest.state == "removing":
                return self._recover_removing(
                    identity, paths, manifest, route, binding, authorization, lease
                )
            if manifest.state == "removed":
                self._repair_removed(identity, paths, manifest, route, binding)
                return DelegateResult.handled()
            return DelegateResult.owned_preserved("authorization_invalid")

    def _begin_removal(
        self,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
        binding: str,
        authorization: CompletionAuthorization,
    ) -> None:
        stable = paths
        workspace = Path(manifest.workspace_path)
        post = manifest.post_proof
        if post is None:
            raise AidtWorktreeFailure("manifest_invalid", route.identifier)
        proof = prove_ready_recovery(
            identity,
            post.s2,
            binding,
            manifest.branch,
            workspace,
            self._timestamp(),
            phase="cleanup_pre",
            runner=self._runner,
        )
        removal = RemovalProof(
            authority_digest=authorization.authorization_digest,
            pre_snapshot=proof.state.snapshot,
            post_snapshot=None,
            remove_delta_digest=None,
            retained_branch_sha=proof.ticket.head,
        )
        removing = replace(
            manifest,
            manifest_revision=3,
            state="removing",
            removal_proof=removal,
            updated_at=self._timestamp(),
        )
        persist_manifest(stable.manifest, removing, expected_revision=2)
        self._advance_owner(stable, removing, tombstone=False)
        self._advance_cleanup_attempt(stable, 3)
        self._fault_hook("after_removing_fsync_before_remove")
        self._recheck_route(route)
        self._verify_binding(route)
        self._remove_and_finalize(identity, stable, removing, route, proof.state)

    def _recover_removing(
        self,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
        binding: str,
        authorization: CompletionAuthorization | None,
        lease: ActiveCompletionLease | None,
    ) -> DelegateResult[None]:
        stable = paths
        self._reconcile_removing_sidecars(stable, manifest)
        removal = manifest.removal_proof
        if removal is None:
            raise AidtWorktreeFailure("manifest_invalid", route.identifier)
        disposition = classify_target_artifacts(
            identity, manifest.branch, Path(manifest.workspace_path), removal.retained_branch_sha,
            runner=self._runner,
        )
        if disposition is TargetArtifactDisposition.EXACT:
            valid = self._authorized(authorization, lease, manifest, route)
            digest = authorization.authorization_digest if authorization is not None else None
            if not valid or digest != removal.authority_digest:
                return DelegateResult.owned_preserved("authorization_invalid")
            before = self._removing_retry_proof(identity, manifest, route, binding)
            self._recheck_route(route)
            self._verify_binding(route)
            self._remove_and_finalize(identity, stable, manifest, route, before)
            return DelegateResult.handled()
        proof = prove_removed_recovery(
            identity,
            removal.pre_snapshot,
            removal.retained_branch_sha,
            binding,
            manifest.branch,
            Path(manifest.workspace_path),
            self._timestamp(),
            runner=self._runner,
        )
        self._finish_removed(
            stable, manifest, proof.state.snapshot, proof.remove_delta_digest
        )
        return DelegateResult.handled()

    def _remove_and_finalize(
        self,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
        before: RepositoryState,
    ) -> None:
        stable = paths
        removal = manifest.removal_proof
        if removal is None:
            raise AidtWorktreeFailure("manifest_invalid", route.identifier)
        remove_worktree(identity, Path(manifest.workspace_path), runner=self._runner)
        self._fault_hook("after_physical_remove_before_removed_fsync")
        after = self._observe(identity, "cleanup_post", route, Path(manifest.workspace_path))
        delta = validate_remove_delta(before, after)
        self._finish_removed(stable, manifest, after.snapshot, delta)

    def _finish_removed(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        after: RepositorySnapshot,
        delta: str,
    ) -> None:
        stable = paths
        removal = manifest.removal_proof
        if removal is None:
            raise AidtWorktreeFailure("manifest_invalid", manifest.identifier)
        complete = replace(removal, post_snapshot=after, remove_delta_digest=delta)
        removed = replace(
            manifest,
            manifest_revision=4,
            state="removed",
            removal_proof=complete,
            updated_at=self._timestamp(),
        )
        persist_manifest(stable.manifest, removed, expected_revision=3)
        self._advance_owner(stable, removed, tombstone=True)

    def _removing_retry_proof(
        self,
        identity: RepositoryIdentity,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
        binding: str,
    ) -> RepositoryState:
        post = manifest.post_proof
        removal = manifest.removal_proof
        if post is None or removal is None:
            raise AidtWorktreeFailure("manifest_invalid", manifest.identifier)
        proof = prove_ready_recovery(
            identity,
            post.s2,
            binding,
            manifest.branch,
            Path(manifest.workspace_path),
            self._timestamp(),
            phase="cleanup_pre",
            runner=self._runner,
        )
        _require_snapshot_equal(removal.pre_snapshot, proof.state.snapshot)
        if proof.ticket.head != removal.retained_branch_sha:
            raise AidtWorktreeFailure("identity_invalid", manifest.identifier)
        return proof.state

    def _reconcile_removing_sidecars(
        self, paths: StableWorktreePaths, manifest: AidtWorktreeManifest
    ) -> None:
        owner = read_optional_ownership(paths.ownership)
        if owner is None or owner.manifest_revision not in {2, 3} or owner.tombstone:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)
        if owner.manifest_revision == 2:
            self._advance_owner(paths, manifest, tombstone=False)
        else:
            self._require_owner_revision(paths, manifest, owner, 3, False, aligned=True)
        attempt = read_attempt(paths.attempt)
        ready = attempt.mutation_phase == "added" and attempt.manifest_revision == 2
        removing = attempt.mutation_phase == "removing" and attempt.manifest_revision == 3
        if ready:
            self._advance_cleanup_attempt(paths, 3)
        elif not removing:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)

    def _repair_removed(
        self,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
        binding: str,
    ) -> None:
        removal = manifest.removal_proof
        if removal is None or removal.post_snapshot is None or removal.remove_delta_digest is None:
            raise AidtWorktreeFailure("manifest_invalid", manifest.identifier)
        owner = read_optional_ownership(paths.ownership)
        if owner is None or owner.manifest_revision not in {3, 4}:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)
        if owner.manifest_revision == 3:
            self._require_owner_revision(paths, manifest, owner, 3, False, aligned=False)
        else:
            self._require_owner_revision(paths, manifest, owner, 4, True, aligned=True)
        proof = prove_removed_recovery(
            identity,
            removal.pre_snapshot,
            removal.retained_branch_sha,
            binding,
            manifest.branch,
            Path(manifest.workspace_path),
            self._timestamp(),
            runner=self._runner,
        )
        _require_snapshot_equal(removal.post_snapshot, proof.state.snapshot)
        if proof.remove_delta_digest != removal.remove_delta_digest:
            raise AidtWorktreeFailure("identity_invalid", manifest.identifier)
        if owner.manifest_revision == 3 and not owner.tombstone:
            self._advance_owner(paths, manifest, tombstone=True)

    def _authorized(
        self,
        authorization: CompletionAuthorization | None,
        lease: ActiveCompletionLease | None,
        manifest: AidtWorktreeManifest,
        route: AidtRouteDispatchContract,
    ) -> bool:
        if authorization is None or lease is None or not lease.active or lease.competing_owner:
            return False
        pairs = (
            authorization.identifier == manifest.identifier == lease.identifier,
            authorization.issue_id == lease.issue_id,
            authorization.run_id == authorization.owning_lease_token == lease.run_id,
            authorization.attempt_kind == lease.attempt_kind,
            authorization.workflow_generation == self._settings.workflow_generation,
            authorization.route_pair_digest == route.route_pair_digest,
            authorization.ready_manifest_revision == 2,
        )
        if not all(pairs):
            return False
        try:
            return self._completion_authority.verify(authorization, lease, manifest, route) is True
        except Exception:
            return False

    def _identity_and_paths(
        self, route: AidtRouteDispatchContract
    ) -> tuple[RepositoryIdentity, StableWorktreePaths]:
        service = [item for item in self._routing.services if item.enabled and item.id == route.service]
        if len(service) != 1 or service[0].checkout != route.checkout:
            raise AidtWorktreeFailure("catalog_invalid", route.service)
        service_root = (self._routing.aidt_root / route.checkout).resolve(strict=True)
        identity = observe_repository_identity(service_root, route.service, runner=self._runner)
        paths = stable_worktree_paths(self._settings.workflow_path, route.identifier)
        return identity, paths

    def _fetch(
        self, identity: RepositoryIdentity, route: AidtRouteDispatchContract
    ) -> FetchResult:
        return fetch_production_base(
            identity,
            identity.origin_digest,
            route.checkout_revision,
            route.repository_binding_digest,
            self._binding(route),
            runner=self._runner,
        )

    def _verify_binding(self, route: AidtRouteDispatchContract) -> str:
        verify_service_binding(
            route.checkout_revision, route.repository_binding_digest, self._binding(route)
        )
        return route.repository_binding_digest

    def _recheck_route(
        self, expected: AidtRouteDispatchContract
    ) -> AidtRouteDispatchContract:
        current = self._load_route(expected.identifier, expected.route_pair_digest)
        _require_same_route(expected, current)
        return current

    def _binding(self, route: AidtRouteDispatchContract) -> GitBindingObserver:
        return lambda: self._binding_observer(self._routing, route.service)

    def _observe(
        self,
        identity: RepositoryIdentity,
        phase: str,
        route: AidtRouteDispatchContract,
        workspace: Path,
    ) -> RepositoryState:
        return observe_repository_state(
            identity,
            phase,
            route.repository_binding_digest,
            route.branch,
            workspace,
            self._timestamp(),
            runner=self._runner,
        )

    def _load_route(self, identifier: str, expected_pair: str) -> AidtRouteDispatchContract:
        try:
            route = self._route_loader(self._config, identifier)
        except Exception:
            raise AidtWorktreeFailure("card_invalid", identifier) from None
        if route is None or route.route_pair_digest != expected_pair:
            raise AidtWorktreeFailure("scope_changed", identifier)
        return route

    def _attempt_for_admission(
        self, path: Path, admission: AidtProvisioningAdmission
    ) -> AttemptRecord:
        attempt = read_attempt(path)
        exact = (
            attempt.identifier == admission.identifier
            and attempt.record_revision == admission.attempt_record_revision
            and attempt.route_pair_digest == admission.route_pair_digest
            and attempt.workflow_generation == admission.workflow_generation
        )
        action = _attempt_action(attempt)
        if not exact or action != admission.action:
            raise AidtWorktreeFailure("scope_changed", admission.identifier)
        return attempt

    def _prepared_manifest(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        workspace: Path,
        s0: RepositorySnapshot,
        s1: RepositorySnapshot,
        fetch_delta: str,
    ) -> AidtWorktreeManifest:
        timestamp = self._timestamp()
        scope = _route_scope(route)
        return AidtWorktreeManifest(
            schema=AIDT_WORKTREE_SCHEMA,
            manifest_revision=1,
            state="prepared",
            identifier=route.identifier,
            coordinator=route.coordinator,
            service=route.service,
            kind=route.kind,
            workflow_identity=self._settings.workflow_identity,
            board_identity=self._settings.board_identity,
            workspace_root=str(self._settings.workspace_root),
            workspace_path=str(workspace),
            catalog_checkout=route.checkout,
            canonical_service_root=str(identity.service_root),
            common_git_identity=identity.common_git_identity,
            object_format=identity.object_format,
            route_pair_digest=route.route_pair_digest,
            repository_binding_digest=route.repository_binding_digest,
            route_fingerprint=route.route_fingerprint,
            coordinator_fingerprint=route.coordinator_fingerprint,
            source_revision=route.source_revision,
            catalog_revision=route.catalog_revision,
            branch=route.branch,
            base_ref=AIDT_WORKTREE_BASE_REF,
            base_sha=route.checkout_revision,
            route_scope=scope,
            pre_proof=PreProof(s0, s1, fetch_delta),
            post_proof=None,
            removal_proof=None,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _new_ownership(
        self, paths: StableWorktreePaths, manifest: AidtWorktreeManifest
    ) -> OwnershipRecord:
        return self._owner_record(paths, manifest, 1, False, manifest.created_at)

    def _owner_record(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        revision: int,
        tombstone: bool,
        updated_at: str,
    ) -> OwnershipRecord:
        return OwnershipRecord(
            schema=AIDT_WORKTREE_OWNERSHIP_SCHEMA,
            record_revision=revision,
            identifier=manifest.identifier,
            service=manifest.service,
            workspace_root=manifest.workspace_root,
            workspace_path=manifest.workspace_path,
            manifest_path=str(paths.manifest),
            route_pair_digest=manifest.route_pair_digest,
            manifest_revision=revision,
            tombstone=tombstone,
            created_at=manifest.created_at,
            updated_at=updated_at,
        )

    def _require_aligned_owner(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        *,
        tombstone: bool,
    ) -> OwnershipRecord:
        owner = read_optional_ownership(paths.ownership)
        if owner is None:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)
        self._require_owner_revision(
            paths, manifest, owner, manifest.manifest_revision, tombstone, aligned=True
        )
        return owner

    def _require_owner_revision(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        owner: OwnershipRecord,
        revision: int,
        tombstone: bool,
        *,
        aligned: bool,
    ) -> None:
        timestamp = manifest.updated_at if aligned else owner.updated_at
        expected = self._owner_record(paths, manifest, revision, tombstone, timestamp)
        coherent = manifest.created_at <= timestamp <= manifest.updated_at
        if revision == 1:
            coherent = timestamp == manifest.created_at
        if owner != expected or not coherent:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)

    def _advance_attempt(
        self, path: Path, attempt: AttemptRecord, phase: str, revision: int
    ) -> AttemptRecord:
        updated = advance_attempt_phase(attempt, phase, revision, self._now())
        persist_attempt(path, updated, expected_revision=attempt.record_revision)
        return updated

    def _ensure_added_attempt(
        self, path: Path, attempt: AttemptRecord, manifest_revision: int
    ) -> AttemptRecord:
        if attempt.mutation_phase == "added":
            return attempt
        return self._advance_attempt(path, attempt, "added", manifest_revision)

    def _advance_cleanup_attempt(
        self, paths: StableWorktreePaths, revision: int
    ) -> None:
        stable = paths
        current = read_attempt(stable.attempt)
        updated = advance_attempt_phase(current, "removing", revision, self._now())
        persist_attempt(stable.attempt, updated, expected_revision=current.record_revision)

    def _advance_owner(
        self,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
        *,
        tombstone: bool,
    ) -> OwnershipRecord:
        stable = paths
        owner = read_optional_ownership(stable.ownership)
        if owner is None:
            raise AidtWorktreeFailure("registry_invalid", manifest.identifier)
        self._require_owner_revision(
            stable,
            manifest,
            owner,
            manifest.manifest_revision - 1,
            False,
            aligned=False,
        )
        updated = self._owner_record(
            stable, manifest, manifest.manifest_revision, tombstone, manifest.updated_at
        )
        persist_ownership(stable.ownership, updated, expected_revision=owner.record_revision)
        return updated

    def _require_manifest(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        paths: StableWorktreePaths,
        manifest: AidtWorktreeManifest,
    ) -> None:
        stable = paths
        self._require_manifest_route(route, identity, manifest)
        expected_path = contained_workspace_path(self._settings.workspace_root, route.identifier)
        values = (
            manifest.workflow_identity == self._settings.workflow_identity,
            manifest.board_identity == self._settings.board_identity,
            manifest.workspace_root == str(self._settings.workspace_root),
            manifest.workspace_path == str(expected_path),
            str(stable.manifest) == str(self._settings.paths.manifests / f"{route.identifier}.json"),
        )
        if not all(values):
            raise AidtWorktreeFailure("identity_invalid", route.identifier)

    def _require_manifest_route(
        self,
        route: AidtRouteDispatchContract,
        identity: RepositoryIdentity,
        manifest: AidtWorktreeManifest,
    ) -> None:
        expected = (
            _route_scope(route),
            route.branch,
            route.checkout_revision,
            route.repository_binding_digest,
            identity.common_git_identity,
            str(identity.service_root),
            identity.object_format,
        )
        actual = (
            manifest.route_scope,
            manifest.branch,
            manifest.base_sha,
            manifest.repository_binding_digest,
            manifest.common_git_identity,
            manifest.canonical_service_root,
            manifest.object_format,
        )
        if actual != expected:
            raise AidtWorktreeFailure("identity_invalid", route.identifier)

    def _prepared_result(
        self, manifest: AidtWorktreeManifest, attempt: AttemptRecord, *, created_now: bool
    ) -> PreparedAidtWorktree:
        result = AidtWorktreeResult(
            Path(manifest.workspace_path), created_now, manifest.manifest_revision
        )
        guard = AidtRunGuard(
            manifest.identifier,
            self._settings.workflow_generation,
            manifest.route_pair_digest,
            attempt.record_revision,
            manifest.manifest_revision,
            Path(manifest.workspace_path),
        )
        return PreparedAidtWorktree(result, guard)

    def _persist_failure(
        self, context: _FailureContext, failure: AidtWorktreeFailure
    ) -> None:
        try:
            if context.common_git_lock is None:
                with advisory_lock(context.paths.manifest_lock):
                    self._persist_failure_locked(context, failure)
            else:
                with ordered_worktree_locks(
                    context.common_git_lock, context.paths.manifest_lock
                ):
                    self._persist_failure_locked(context, failure)
        except Exception:
            raise AidtWorktreeFailure(
                "persistence_failed", context.admission.identifier
            ) from None

    def _persist_failure_locked(
        self, context: _FailureContext, failure: AidtWorktreeFailure
    ) -> None:
        admission = context.admission
        current = read_attempt(context.paths.attempt)
        same_scope = (
            current.identifier == admission.identifier
            and current.route_pair_digest == admission.route_pair_digest
            and current.workflow_generation == admission.workflow_generation
        )
        if not same_scope or current.disposition in {"manual", "ready"}:
            return
        if _attempt_action(current) is None or not _owns_failure_revision(current, admission):
            return
        manifest = read_optional_manifest(context.paths.manifest)
        phase = current.mutation_phase
        revision = manifest.manifest_revision if manifest is not None else None
        if current.manifest_revision != revision:
            return
        updated = next_failure_record(current, failure.category, phase, revision, self._now())
        persist_attempt(
            context.paths.attempt, updated, expected_revision=current.record_revision
        )

    def _require_generation(self, admission: AidtProvisioningAdmission) -> None:
        if admission.workflow_generation != self._settings.workflow_generation:
            raise AidtWorktreeFailure("scope_changed", admission.identifier)

    def _now(self) -> datetime:
        value = self._clock()
        valid = (
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() == timedelta(0)
            and value.microsecond == 0
        )
        if not valid:
            raise AidtWorktreeFailure("clock_invalid")
        return value.astimezone(timezone.utc)

    def _timestamp(self) -> str:
        return self._now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _route_scope(route: AidtRouteDispatchContract) -> RouteScope:
    return RouteScope(
        route.identifier,
        route.coordinator,
        route.service,
        route.kind,
        route.issue_type,
        route.change_kind,
        route.route_pair_digest,
        route.route_fingerprint,
        route.coordinator_fingerprint,
        route.source_revision,
        route.catalog_revision,
        route.checkout_revision,
        route.repository_binding_digest,
    )


def _require_same_route(
    expected: AidtRouteDispatchContract, actual: AidtRouteDispatchContract
) -> None:
    if expected != actual:
        raise AidtWorktreeFailure("scope_changed", expected.identifier)


def _require_snapshot_equal(expected: RepositorySnapshot, actual: RepositorySnapshot) -> None:
    if replace(actual, observed_at=expected.observed_at) != expected:
        raise AidtWorktreeFailure("identity_invalid")


def _bounded_failure(exc: Exception, identifier: str) -> AidtWorktreeFailure:
    if isinstance(exc, AidtWorktreeFailure):
        return exc
    return AidtWorktreeFailure("internal_error", identifier)


def _failure_context(
    settings: AidtWorktreeSettings, admission: AidtProvisioningAdmission
) -> _FailureContext:
    paths = stable_worktree_paths(settings.workflow_path, admission.identifier)
    return _FailureContext(admission, paths)


def _attempt_action(attempt: AttemptRecord) -> str | None:
    active = (
        attempt.disposition == "backoff"
        and attempt.category
        in {"attempt_backoff", "scope_changed", "lock_timeout", "fetch_timeout", "fetch_command_failed"}
        and attempt.retry_at == attempt.updated_at
        and 1 <= attempt.attempt <= 3
    )
    if active:
        return "provision"
    ready = (
        attempt.disposition == "ready"
        and attempt.category == "ready"
        and attempt.retry_at is None
        and attempt.mutation_phase == "added"
        and attempt.manifest_revision == 2
    )
    return "resume" if ready else None


def _owns_failure_revision(
    attempt: AttemptRecord, admission: AidtProvisioningAdmission
) -> bool:
    delta = attempt.record_revision - admission.attempt_record_revision
    allowed = {
        "none": {0},
        "prepared": {0, 1},
        "added": {0, 1, 2},
    }
    return delta in allowed.get(attempt.mutation_phase, set())


def _valid_child(value: object) -> bool:
    return type(value) is str and _CHILD.fullmatch(value) is not None


def _valid_digest(value: object) -> bool:
    return type(value) is str and _HEX_64.fullmatch(value) is not None


def _valid_revision(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2_147_483_647


def _valid_absolute_path(value: object) -> bool:
    return isinstance(value, Path) and value.is_absolute() and value.resolve(strict=False) == value
