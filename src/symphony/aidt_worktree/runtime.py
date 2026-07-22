"""Process-lifetime admission facade for routed AIDT worktrees."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from ..aidt_routing.contract import AidtRoutingFailure
from ..aidt_routing.dispatch import AidtRouteDispatchContract, load_route_dispatch_contract
from ..workflow import ServiceConfig
from .contract import (
    MAX_INT,
    AidtWorktreeFailure,
    AidtWorktreeSettings,
    CompletionAuthorization,
    DelegateDisposition,
    DelegateResult,
    StableMetadataPaths,
    StableWorktreePaths,
    contained_workspace_path,
    load_aidt_worktree_settings,
    stable_worktree_paths,
)

if TYPE_CHECKING:
    from .manifest import AttemptAdmission, AttemptRecord
    from .provisioner import (
        ActiveCompletionLease,
        AidtProvisioningAdmission,
        AidtRunGuard,
        PreparedAidtWorktree,
    )


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_FATAL_CATEGORIES = frozenset({"clock_invalid", "durability_failed", "persistence_failed"})


class _Provisioner(Protocol):
    def prepare(self, admission: AidtProvisioningAdmission) -> PreparedAidtWorktree: ...

    def attest_before_run(self, guard: AidtRunGuard) -> None: ...

    def cleanup(
        self,
        identifier: str,
        workspace_path: Path,
        *,
        authorization: CompletionAuthorization | None,
        lease: ActiveCompletionLease | None,
    ) -> DelegateResult[None]: ...


class ProvisionerFactory(Protocol):
    def __call__(
        self,
        config: ServiceConfig,
        settings: AidtWorktreeSettings,
        *,
        clock: Callable[[], datetime],
    ) -> _Provisioner: ...


@dataclass(frozen=True, repr=False)
class AidtWorktreeGeneration:
    revision: int
    config: ServiceConfig
    settings: AidtWorktreeSettings | None
    workflow_generation: str | None

    def __post_init__(self) -> None:
        valid = (
            type(self.revision) is int
            and 1 <= self.revision <= MAX_INT
            and type(self.config) is ServiceConfig
            and (self.settings is None or type(self.settings) is AidtWorktreeSettings)
        )
        if not valid:
            raise AidtWorktreeFailure("internal_error")
        if self.settings is None:
            if self.workflow_generation is not None:
                raise AidtWorktreeFailure("internal_error")
            return
        enabled = self.settings.enabled is True
        generation = self.workflow_generation
        if not enabled or type(generation) is not str:
            raise AidtWorktreeFailure("internal_error")
        if generation != self.settings.workflow_generation:
            raise AidtWorktreeFailure("internal_error")

    def __repr__(self) -> str:
        return f"AidtWorktreeGeneration(revision={self.revision!r}, enabled={self.settings is not None!r})"


@dataclass(frozen=True)
class AidtWorktreeHealth:
    enabled: bool
    status: Literal["disabled", "ready", "degraded", "fatal"]
    workflow_generation: str | None
    create_count: int
    resume_count: int
    failure_count: int
    consecutive_failures: int
    last_category: str | None
    last_ref: str | None
    last_success_at: str | None

    def __post_init__(self) -> None:
        counts = (self.create_count, self.resume_count, self.failure_count, self.consecutive_failures)
        valid = type(self.enabled) is bool and type(self.status) is str
        valid = valid and self.status in {"disabled", "ready", "degraded", "fatal"}
        valid = valid and all(type(value) is int and 0 <= value <= MAX_INT for value in counts)
        valid = valid and _valid_health_generation(self.enabled, self.workflow_generation)
        valid = valid and _valid_health_detail(self.last_category, self.last_ref, self.last_success_at)
        if not valid:
            raise AidtWorktreeFailure("internal_error")


def _valid_health_generation(enabled: bool, generation: str | None) -> bool:
    if enabled:
        return type(generation) is str and _DIGEST.fullmatch(generation) is not None
    return generation is None


def _valid_health_detail(category: object, ref: object, success: object) -> bool:
    if category is not None:
        if type(category) is not str or AidtWorktreeFailure(category).category != category:
            return False
    if ref is not None:
        if type(ref) is not str or AidtWorktreeFailure("internal_error", ref).ref != ref:
            return False
    return _valid_health_timestamp(success)


def _valid_health_timestamp(value: object) -> bool:
    if value is None:
        return True
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def activate_registry(paths: StableMetadataPaths, workflow_identity: str, now: str) -> Any:
    from .manifest import activate_registry as operation

    return operation(paths, workflow_identity, now)


def advisory_lock(path: Path, *, timeout_seconds: float = 5.0) -> Any:
    from .manifest import advisory_lock as operation

    return operation(path, timeout_seconds=timeout_seconds)


def admit_attempt(*args: Any, **kwargs: Any) -> AttemptAdmission:
    from .manifest import admit_attempt as operation

    return operation(*args, **kwargs)


def initial_attempt_record(*args: Any, **kwargs: Any) -> AttemptRecord:
    from .manifest import initial_attempt_record as operation

    return operation(*args, **kwargs)


def next_failure_record(*args: Any, **kwargs: Any) -> AttemptRecord:
    from .manifest import next_failure_record as operation

    return operation(*args, **kwargs)


def persist_attempt(*args: Any, **kwargs: Any) -> Any:
    from .manifest import persist_attempt as operation

    return operation(*args, **kwargs)


def read_attempt(*args: Any, **kwargs: Any) -> AttemptRecord:
    from .manifest import read_attempt as operation

    return operation(*args, **kwargs)


def read_optional_attempt(*args: Any, **kwargs: Any) -> AttemptRecord | None:
    from .manifest import read_optional_attempt as operation

    return operation(*args, **kwargs)


def read_optional_manifest(*args: Any, **kwargs: Any) -> Any:
    from .manifest import read_optional_manifest as operation

    return operation(*args, **kwargs)


def read_optional_ownership(*args: Any, **kwargs: Any) -> Any:
    from .manifest import read_optional_ownership as operation

    return operation(*args, **kwargs)


def registry_recognizes_identifier(*args: Any, **kwargs: Any) -> bool:
    from .manifest import registry_recognizes_identifier as operation

    return operation(*args, **kwargs)


def registry_recognizes_path(*args: Any, **kwargs: Any) -> bool:
    from .manifest import registry_recognizes_path as operation

    return operation(*args, **kwargs)


class AidtWorktreeRuntime:
    """Publish one runtime generation and keep owned delegates fail-closed."""

    def __init__(
        self,
        workflow_path: Path,
        *,
        clock: Callable[[], datetime],
        provisioner_factory: ProvisionerFactory | None = None,
    ) -> None:
        self._workflow_path = _canonical_input_path(workflow_path)
        self._metadata = _lexical_metadata_paths(self._workflow_path)
        self._clock = clock
        self._factory = provisioner_factory
        self._publish_lock = Lock()
        self._lock = RLock()
        self._generation: AidtWorktreeGeneration | None = None
        self._material_key: object = None
        self._provisioner: _Provisioner | None = None
        self._recognized_identifiers: set[str] = set()
        self._issued_admissions: dict[str, tuple[AidtWorktreeGeneration, AidtProvisioningAdmission]] = {}
        self._issued_guards: dict[str, tuple[AidtWorktreeGeneration, AidtRunGuard]] = {}
        self._admission_open = False
        self._rejected_category: str | None = None
        self._fatal_category: str | None = None
        self._create_count = self._resume_count = 0
        self._failure_count = self._consecutive_failures = 0
        self._last_category: str | None = None
        self._last_ref: str | None = None
        self._last_success_at: str | None = None
        self._status: Literal["disabled", "ready", "degraded", "fatal"] = "disabled"

    def publish(self, config: ServiceConfig) -> AidtWorktreeGeneration:
        settings, key = self._validated_publication(config)
        with self._publish_lock:
            existing = self._equivalent_publication(key)
            if existing is not None:
                return existing
            self._raise_if_fatal()
            provisioner = self._build_provisioner(config, settings)
            return self._install_generation(config, settings, key, provisioner)

    def reject_reload(self, category: str = "profile_invalid") -> None:
        failure = AidtWorktreeFailure(category)
        with self._lock:
            if self._fatal_category is not None:
                return
            self._admission_open = False
            self._rejected_category = failure.category
            self._clear_capabilities_locked()
            self._record_failure_locked(failure)

    def path_for(
        self, generation: AidtWorktreeGeneration, identifier: str
    ) -> DelegateResult[Path]:
        paths = self._paths_for(identifier)
        if paths is None:
            return DelegateResult.unmanaged()
        durable = self._durable_path(paths, identifier)
        if isinstance(durable, DelegateResult):
            return durable
        if durable is not None:
            return DelegateResult.handled(durable)
        route = self._route_result(generation.config, identifier)
        if isinstance(route, DelegateResult):
            return route
        gate = self._gate(generation)
        if gate is not None:
            return gate
        try:
            return DelegateResult.handled(contained_workspace_path(generation.config.workspace_root, identifier))
        except Exception as exc:
            return self._error_result(exc, identifier)

    def admit_candidate(
        self, generation: AidtWorktreeGeneration, identifier: str
    ) -> DelegateResult[AidtProvisioningAdmission]:
        paths = self._paths_for(identifier)
        if paths is None:
            return DelegateResult.unmanaged()
        route = self._route_result(generation.config, identifier, paths)
        if isinstance(route, DelegateResult):
            return route
        gate = self._gate(generation)
        if gate is not None:
            return gate
        try:
            result = self._admit(paths, generation, route)
        except Exception as exc:
            return self._error_result(exc, identifier)
        if result.disposition is not DelegateDisposition.HANDLED:
            return result
        if not self._issue_admission(generation, result.value):
            return self._scope_changed(identifier)
        return result

    def create_or_reuse(
        self,
        generation: AidtWorktreeGeneration,
        admission: AidtProvisioningAdmission,
    ) -> DelegateResult[PreparedAidtWorktree]:
        identifier = getattr(admission, "identifier", "")
        recognized = self._recognize_for_delegate(generation, identifier)
        if recognized is not None:
            return recognized
        gate = self._gate(generation)
        if gate is not None:
            return gate
        try:
            provisioner = self._consume_admission(generation, admission)
            prepared = provisioner.prepare(admission)
            issued = self._complete_prepare(generation, admission, prepared)
            now = self._clock_now()
            self._record_success_time(generation, now)
        except Exception as exc:
            return self._error_result(exc, identifier)
        if not issued or self._postcheck(generation) is not None:
            return self._scope_changed(identifier)
        return DelegateResult.handled(prepared)

    def before_run(
        self, generation: AidtWorktreeGeneration, guard: AidtRunGuard
    ) -> DelegateResult[None]:
        identifier = getattr(guard, "identifier", "")
        recognized = self._recognize_for_delegate(generation, identifier)
        if recognized is not None:
            return recognized
        gate = self._gate(generation)
        if gate is not None:
            return gate
        try:
            provisioner = self._require_issued_guard(generation, guard)
            provisioner.attest_before_run(guard)
        except Exception as exc:
            return self._error_result(exc, identifier)
        if self._postcheck(generation) is not None:
            return self._scope_changed(identifier)
        return DelegateResult.handled()

    def remove(
        self,
        generation: AidtWorktreeGeneration,
        path: Path,
        *,
        identifier: str | None = None,
        authorization: CompletionAuthorization | None = None,
        lease: ActiveCompletionLease | None = None,
    ) -> DelegateResult[None]:
        owned = self._recognize_removal(generation, path, identifier)
        if isinstance(owned, DelegateResult):
            return owned
        gate = self._gate(generation)
        if gate is not None:
            return gate
        try:
            result = self._current_provisioner().cleanup(
                owned, path, authorization=authorization, lease=lease
            )
        except Exception as exc:
            return self._error_result(exc, owned)
        if result.disposition is DelegateDisposition.OWNED_ERROR:
            self._record_failure(AidtWorktreeFailure(result.category, owned))
        return result

    def health_snapshot(self) -> AidtWorktreeHealth:
        with self._lock:
            generation = self._generation
            enabled = generation is not None and generation.settings is not None
            workflow_generation = generation.workflow_generation if generation is not None else None
            return AidtWorktreeHealth(
                enabled, self._status, workflow_generation, self._create_count,
                self._resume_count, self._failure_count, self._consecutive_failures,
                self._last_category, self._last_ref, self._last_success_at,
            )

    def _validated_publication(
        self, config: ServiceConfig
    ) -> tuple[AidtWorktreeSettings | None, object]:
        if type(config) is not ServiceConfig or config.workflow_path != self._workflow_path:
            raise AidtWorktreeFailure("profile_invalid")
        settings = load_aidt_worktree_settings(config)
        if settings is not None and settings.workflow_path != self._workflow_path:
            raise AidtWorktreeFailure("profile_invalid")
        return settings, _material_key(config, settings)

    def _equivalent_publication(self, key: object) -> AidtWorktreeGeneration | None:
        with self._lock:
            if self._generation is None or self._material_key != key:
                return None
            if self._fatal_category is None:
                self._admission_open = self._generation.settings is not None
                self._rejected_category = None
                self._clear_health_locked()
            return self._generation

    def _build_provisioner(
        self, config: ServiceConfig, settings: AidtWorktreeSettings | None
    ) -> _Provisioner | None:
        if settings is None:
            return None
        try:
            now = self._clock_now()
            activate_registry(settings.paths, settings.workflow_identity, _format_time(now))
            factory = self._factory or _default_provisioner_factory()
            return factory(config, settings, clock=self._clock)
        except AidtWorktreeFailure as exc:
            if exc.category in _FATAL_CATEGORIES:
                self._record_failure(exc)
            raise
        except Exception as exc:
            raise AidtWorktreeFailure("internal_error") from exc

    def _install_generation(
        self,
        config: ServiceConfig,
        settings: AidtWorktreeSettings | None,
        key: object,
        provisioner: _Provisioner | None,
    ) -> AidtWorktreeGeneration:
        with self._lock:
            revision = 1 if self._generation is None else self._generation.revision + 1
            if revision > MAX_INT:
                raise AidtWorktreeFailure("internal_error")
            generation = AidtWorktreeGeneration(
                revision, config, settings, settings.workflow_generation if settings else None
            )
            self._generation = generation
            self._material_key = key
            self._provisioner = provisioner
            self._admission_open = settings is not None
            self._rejected_category = None
            self._clear_capabilities_locked()
            self._clear_health_locked()
            return generation

    def _route_result(
        self,
        config: ServiceConfig,
        identifier: str,
        paths: StableWorktreePaths | None = None,
    ) -> AidtRouteDispatchContract | DelegateResult[Any]:
        try:
            route = load_route_dispatch_contract(config, identifier)
            if route is not None:
                with self._lock:
                    self._recognized_identifiers.add(identifier)
                return route
            if paths is not None and registry_recognizes_identifier(paths.root, identifier):
                return self._error_result(AidtWorktreeFailure("card_invalid", identifier), identifier)
            return DelegateResult.unmanaged()
        except AidtRoutingFailure:
            return self._error_result(AidtWorktreeFailure("card_invalid", identifier), identifier)
        except Exception as exc:
            return self._error_result(exc, identifier)

    def _admit(
        self,
        paths: StableWorktreePaths,
        generation: AidtWorktreeGeneration,
        route: AidtRouteDispatchContract,
    ) -> DelegateResult[AidtProvisioningAdmission]:
        now = self._clock_now()
        record = read_optional_attempt(paths.attempt)
        if record is None:
            record = initial_attempt_record(
                route.identifier, route.route_pair_digest, generation.workflow_generation, now
            )
            with advisory_lock(paths.manifest_lock):
                persist_attempt(paths.attempt, record, expected_revision=None)
        admission = admit_attempt(
            paths, record.record_revision, route.route_pair_digest,
            generation.workflow_generation, now, scope_attested=True,
        )
        return self._map_admission(paths, generation, route, admission, now)

    def _map_admission(
        self,
        paths: StableWorktreePaths,
        generation: AidtWorktreeGeneration,
        route: AidtRouteDispatchContract,
        admission: AttemptAdmission,
        now: datetime,
    ) -> DelegateResult[AidtProvisioningAdmission]:
        if not admission.admitted:
            category = _preserved_admission_category(admission)
            return DelegateResult.owned_preserved(category)
        if admission.action == "resume" and not self._ready_evidence(paths, generation, route, admission.record, now):
            return self._error_result(AidtWorktreeFailure("registry_invalid", route.identifier), route.identifier)
        from .provisioner import AidtProvisioningAdmission

        workflow_generation = cast(str, generation.workflow_generation)
        action = cast(Literal["provision", "resume"], admission.action)
        value = AidtProvisioningAdmission(
            route.identifier, workflow_generation, route.route_pair_digest,
            admission.record.record_revision, action,
        )
        return DelegateResult.handled(value)

    def _ready_evidence(
        self,
        paths: StableWorktreePaths,
        generation: AidtWorktreeGeneration,
        route: AidtRouteDispatchContract,
        expected: AttemptRecord,
        now: datetime,
    ) -> bool:
        with advisory_lock(paths.manifest_lock):
            manifest = read_optional_manifest(paths.manifest)
            owner = read_optional_ownership(paths.ownership)
            attempt = read_attempt(paths.attempt)
            if _ready_records_align(paths, generation, route, manifest, owner, attempt, expected):
                return True
            failed = next_failure_record(
                attempt, "registry_invalid", attempt.mutation_phase,
                attempt.manifest_revision, now,
            )
            persist_attempt(paths.attempt, failed, expected_revision=attempt.record_revision)
            return False

    def _durable_path(
        self, paths: StableWorktreePaths, identifier: str
    ) -> Path | DelegateResult[Path] | None:
        try:
            if not registry_recognizes_identifier(paths.root, identifier):
                return None
            manifest = read_optional_manifest(paths.manifest)
            owner = read_optional_ownership(paths.ownership)
            if not _durable_records_align(paths, identifier, manifest, owner):
                raise AidtWorktreeFailure("registry_invalid", identifier)
            return Path(owner.workspace_path)
        except Exception:
            return self._error_result(AidtWorktreeFailure("registry_invalid", identifier), identifier)

    def _recognize_for_delegate(
        self, generation: AidtWorktreeGeneration, identifier: str
    ) -> DelegateResult[Any] | None:
        paths = self._paths_for(identifier)
        if paths is None:
            return DelegateResult.unmanaged()
        with self._lock:
            known = identifier in self._recognized_identifiers
            closed = self._fatal_category is not None or generation is not self._generation
            closed = closed or not self._admission_open
        if known and closed:
            return None
        route = self._route_result(generation.config, identifier, paths)
        with self._lock:
            fatal = self._fatal_category is not None
        if fatal and isinstance(route, DelegateResult):
            if route.disposition is DelegateDisposition.OWNED_ERROR:
                return None
        return route if isinstance(route, DelegateResult) else None

    def _recognize_removal(
        self, generation: AidtWorktreeGeneration, path: Path, identifier: str | None
    ) -> str | DelegateResult[None]:
        if identifier is not None:
            recognized = self._recognize_for_delegate(generation, identifier)
            if recognized is not None:
                if recognized.disposition is DelegateDisposition.UNMANAGED:
                    return self._reverse_unmanaged_removal(path, identifier)
                return recognized
            return self._validate_removal_path(generation, identifier, path)
        try:
            if registry_recognizes_path(self._metadata.root, path):
                return self._identifier_for_recorded_path(path)
            return DelegateResult.unmanaged()
        except Exception as exc:
            return self._error_result(exc)

    def _reverse_unmanaged_removal(
        self, path: Path, identifier: str
    ) -> DelegateResult[None]:
        try:
            owned = registry_recognizes_path(self._metadata.root, path)
        except Exception as exc:
            return self._error_result(exc, identifier)
        if not owned:
            return DelegateResult.unmanaged()
        failure = AidtWorktreeFailure("path_invalid", identifier)
        return self._error_result(failure, identifier)

    def _identifier_for_recorded_path(self, path: Path) -> str | DelegateResult[None]:
        try:
            from .manifest import discover_registry

            discovery = discover_registry(self._metadata.root)
            matches = [key for key, value in discovery.ownership.items() if Path(value.workspace_path) == path]
            if len(matches) == 1:
                return matches[0]
        except Exception as exc:
            return self._error_result(exc)
        return self._error_result(AidtWorktreeFailure("registry_invalid"))

    def _validate_removal_path(
        self, generation: AidtWorktreeGeneration, identifier: str, supplied: Path
    ) -> str | DelegateResult[None]:
        paths = self._paths_for(identifier)
        if paths is None:
            return self._error_result(AidtWorktreeFailure("path_invalid", identifier), identifier)
        try:
            current = contained_workspace_path(generation.config.workspace_root, identifier)
            durable = self._recorded_removal_path(paths, identifier)
        except Exception as exc:
            return self._error_result(exc, identifier)
        accepted = {current} if durable is None else {durable}
        if not isinstance(supplied, Path) or supplied not in accepted:
            return self._error_result(AidtWorktreeFailure("path_invalid", identifier), identifier)
        return identifier

    def _recorded_removal_path(
        self, paths: StableWorktreePaths, identifier: str
    ) -> Path | None:
        manifest = read_optional_manifest(paths.manifest)
        owner = read_optional_ownership(paths.ownership)
        if manifest is None and owner is None:
            return None
        if not _durable_records_align(paths, identifier, manifest, owner):
            raise AidtWorktreeFailure("registry_invalid", identifier)
        return Path(owner.workspace_path)

    def _gate(self, generation: AidtWorktreeGeneration) -> DelegateResult[Any] | None:
        with self._lock:
            if self._fatal_category is not None:
                return DelegateResult.owned_error(self._fatal_category)
            if generation is not self._generation:
                failure = AidtWorktreeFailure("scope_changed")
                self._record_failure_locked(failure)
                return DelegateResult.owned_error(failure.category)
            if not self._admission_open:
                category = self._rejected_category or "profile_invalid"
                return DelegateResult.owned_preserved(category)
            if generation.settings is None or self._provisioner is None:
                return DelegateResult.owned_preserved("profile_invalid")
        return None

    def _postcheck(self, generation: AidtWorktreeGeneration) -> object | None:
        with self._lock:
            valid = (
                self._fatal_category is None
                and generation is self._generation
                and self._admission_open
            )
            return None if valid else object()

    def _scope_changed(self, identifier: str) -> DelegateResult[Any]:
        return self._error_result(AidtWorktreeFailure("scope_changed", identifier), identifier)

    def _error_result(self, exc: BaseException, ref: object = None) -> DelegateResult[Any]:
        if isinstance(exc, AidtWorktreeFailure):
            failure = exc
        elif isinstance(exc, AidtRoutingFailure):
            failure = AidtWorktreeFailure("card_invalid", ref)
        else:
            failure = AidtWorktreeFailure("internal_error", ref)
        self._record_failure(failure)
        return DelegateResult.owned_error(failure.category)

    def _record_failure(self, failure: AidtWorktreeFailure) -> None:
        with self._lock:
            self._record_failure_locked(failure)

    def _record_failure_locked(self, failure: AidtWorktreeFailure) -> None:
        if self._fatal_category is not None:
            return
        self._failure_count += 1
        self._consecutive_failures += 1
        self._last_category = failure.category
        self._last_ref = failure.ref
        if failure.category in _FATAL_CATEGORIES:
            self._fatal_category = failure.category
            self._admission_open = False
            self._clear_capabilities_locked()
            self._status = "fatal"
        else:
            self._status = "degraded"

    def _record_success_time(
        self, generation: AidtWorktreeGeneration, now: datetime
    ) -> None:
        with self._lock:
            self._last_success_at = _format_time(now)
            current = generation is self._generation and self._admission_open
            if current and self._fatal_category is None:
                self._clear_health_locked()

    def _clear_health_locked(self) -> None:
        self._consecutive_failures = 0
        self._last_category = None
        self._last_ref = None
        enabled = self._generation is not None and self._generation.settings is not None
        self._status = "ready" if enabled else "disabled"

    def _paths_for(self, identifier: object) -> StableWorktreePaths | None:
        try:
            return stable_worktree_paths(self._workflow_path, identifier)
        except AidtWorktreeFailure:
            return None

    def _issue_admission(
        self, generation: AidtWorktreeGeneration, admission: object
    ) -> bool:
        from .provisioner import AidtProvisioningAdmission

        if type(admission) is not AidtProvisioningAdmission:
            return False
        with self._lock:
            if not self._current_open_locked(generation):
                return False
            self._issued_admissions[admission.identifier] = (generation, admission)
            return True

    def _consume_admission(
        self, generation: AidtWorktreeGeneration, admission: AidtProvisioningAdmission
    ) -> _Provisioner:
        from .provisioner import AidtProvisioningAdmission

        if type(admission) is not AidtProvisioningAdmission:
            raise AidtWorktreeFailure("scope_changed", getattr(admission, "identifier", None))
        with self._lock:
            issued = self._issued_admissions.get(admission.identifier)
            if issued is None or issued[0] is not generation or issued[1] is not admission:
                raise AidtWorktreeFailure("scope_changed", getattr(admission, "identifier", None))
            if not self._current_open_locked(generation):
                raise AidtWorktreeFailure("scope_changed", getattr(admission, "identifier", None))
            self._issued_admissions.pop(admission.identifier)
            provisioner = self._provisioner
            if provisioner is None:
                raise AidtWorktreeFailure("scope_changed", admission.identifier)
            return provisioner

    def _complete_prepare(
        self,
        generation: AidtWorktreeGeneration,
        admission: AidtProvisioningAdmission,
        prepared: object,
    ) -> bool:
        from .provisioner import PreparedAidtWorktree

        with self._lock:
            self._increment_action_locked(admission.action)
            if not _prepared_matches(admission, prepared):
                raise AidtWorktreeFailure("internal_error", admission.identifier)
            if not self._current_open_locked(generation):
                return False
            exact = cast(PreparedAidtWorktree, prepared)
            self._issued_guards[exact.guard.identifier] = (generation, exact.guard)
            return True

    def _require_issued_guard(
        self, generation: AidtWorktreeGeneration, guard: AidtRunGuard
    ) -> _Provisioner:
        from .provisioner import AidtRunGuard

        if type(guard) is not AidtRunGuard:
            raise AidtWorktreeFailure("scope_changed", getattr(guard, "identifier", None))
        with self._lock:
            issued = self._issued_guards.get(guard.identifier)
            if issued is None or issued[0] is not generation or issued[1] is not guard:
                raise AidtWorktreeFailure("scope_changed", getattr(guard, "identifier", None))
            if not self._current_open_locked(generation):
                raise AidtWorktreeFailure("scope_changed", getattr(guard, "identifier", None))
            provisioner = self._provisioner
            if provisioner is None:
                raise AidtWorktreeFailure("scope_changed", guard.identifier)
            return provisioner

    def _increment_action_locked(self, action: str) -> None:
        if action == "provision":
            self._create_count += 1
        elif action == "resume":
            self._resume_count += 1
        else:
            raise AidtWorktreeFailure("internal_error")

    def _current_open_locked(self, generation: AidtWorktreeGeneration) -> bool:
        return (
            self._fatal_category is None
            and generation is self._generation
            and self._admission_open
            and self._provisioner is not None
        )

    def _clear_capabilities_locked(self) -> None:
        self._issued_admissions.clear()
        self._issued_guards.clear()

    def _current_provisioner(self) -> _Provisioner:
        with self._lock:
            if self._provisioner is None:
                raise AidtWorktreeFailure("profile_invalid")
            return self._provisioner

    def _clock_now(self) -> datetime:
        try:
            now = self._clock()
            valid = type(now) is datetime and now.tzinfo is not None
            valid = valid and now.utcoffset() == timedelta(0)
            if not valid:
                raise AidtWorktreeFailure("clock_invalid")
            return now.astimezone(timezone.utc).replace(microsecond=0)
        except AidtWorktreeFailure:
            raise
        except Exception as exc:
            raise AidtWorktreeFailure("clock_invalid") from exc

    def _raise_if_fatal(self) -> None:
        with self._lock:
            if self._fatal_category is not None:
                raise AidtWorktreeFailure(self._fatal_category)


def _canonical_input_path(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise AidtWorktreeFailure("path_invalid")
    try:
        normalized = Path(os.path.normpath(str(value)))
    except (TypeError, ValueError) as exc:
        raise AidtWorktreeFailure("path_invalid") from exc
    if "\x00" in str(normalized):
        raise AidtWorktreeFailure("path_invalid")
    return normalized


def _lexical_metadata_paths(workflow_path: Path) -> StableMetadataPaths:
    encoded = b"aidt-workflow-identity-v1\0" + str(workflow_path).encode("utf-8")
    identity = hashlib.sha256(encoded).hexdigest()
    root = workflow_path.parent / ".symphony" / "aidt-worktrees-v1"
    return StableMetadataPaths(
        identity,
        root,
        root / "ACTIVATED.json",
        root / "manifests",
        root / "ownership",
        root / "attempts",
        root / "locks",
    )


def _material_key(config: ServiceConfig, settings: AidtWorktreeSettings | None) -> object:
    enabled = settings is not None
    setting_values = None if settings is None else (
        settings.workflow_identity, settings.board_identity, settings.workflow_generation,
        str(settings.workflow_path), str(settings.board_root), str(settings.workspace_root),
    )
    return (
        enabled, setting_values, str(config.workflow_path), str(config.workspace_root),
        config.tracker.kind, str(config.tracker.board_root), config.workspace_reuse_policy,
        getattr(config.agent, "auto_commit_on_done", None),
        getattr(config.agent, "auto_merge_on_done", None),
        _freeze(config.raw.get("aidt_routing")),
    )


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, Path):
        return ("path", str(value))
    if value is None or type(value) in {str, int, bool, float}:
        return value
    raise AidtWorktreeFailure("profile_invalid")


def _default_provisioner_factory() -> ProvisionerFactory:
    from .provisioner import AidtWorktreeProvisioner

    return cast(ProvisionerFactory, AidtWorktreeProvisioner)


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _preserved_admission_category(admission: AttemptAdmission) -> str:
    if admission.action == "backoff":
        return "attempt_backoff"
    if admission.action == "scope_reset":
        return "scope_changed"
    return admission.record.category


def _prepared_matches(admission: AidtProvisioningAdmission, prepared: object) -> bool:
    from .provisioner import PreparedAidtWorktree

    if type(prepared) is not PreparedAidtWorktree:
        return False
    guard = prepared.guard
    result = prepared.result
    admission_scope = (
        admission.identifier,
        admission.workflow_generation,
        admission.route_pair_digest,
        admission.attempt_record_revision,
    )
    guard_scope = (
        guard.identifier,
        guard.workflow_generation,
        guard.route_pair_digest,
        guard.attempt_record_revision,
    )
    result_scope = (result.workspace_path, result.manifest_revision)
    guard_result = (guard.workspace_path, guard.manifest_revision)
    return admission_scope == guard_scope and result_scope == guard_result


def _ready_records_align(
    paths: StableWorktreePaths,
    generation: AidtWorktreeGeneration,
    route: AidtRouteDispatchContract,
    manifest: Any,
    owner: Any,
    attempt: AttemptRecord,
    expected: AttemptRecord,
) -> bool:
    if manifest is None or owner is None or attempt != expected:
        return False
    settings = generation.settings
    if settings is None:
        return False
    workspace = contained_workspace_path(generation.config.workspace_root, route.identifier)
    manifest_values = (
        manifest.state, manifest.identifier, manifest.workflow_identity,
        manifest.route_pair_digest, Path(manifest.workspace_path), manifest.manifest_revision,
    )
    expected_manifest = (
        "ready", route.identifier, settings.workflow_identity,
        route.route_pair_digest, workspace, attempt.manifest_revision,
    )
    owner_values = (
        owner.identifier, owner.service, Path(owner.workspace_path), Path(owner.manifest_path),
        owner.route_pair_digest, owner.manifest_revision, owner.tombstone,
    )
    expected_owner = (
        route.identifier, route.service, workspace, paths.manifest,
        route.route_pair_digest, manifest.manifest_revision, False,
    )
    attempt_scope = (attempt.identifier, attempt.route_pair_digest, attempt.workflow_generation)
    expected_scope = (route.identifier, route.route_pair_digest, generation.workflow_generation)
    return manifest_values == expected_manifest and owner_values == expected_owner and attempt_scope == expected_scope


def _durable_records_align(
    paths: StableWorktreePaths, identifier: str, manifest: Any, owner: Any
) -> bool:
    if manifest is None or owner is None:
        return False
    workspace = Path(owner.workspace_path)
    manifest_values = (
        manifest.identifier, manifest.workflow_identity, Path(manifest.workspace_path),
        manifest.route_pair_digest, manifest.manifest_revision,
    )
    owner_values = (
        owner.identifier, owner.workspace_path, owner.route_pair_digest, owner.manifest_revision,
    )
    state_pair = (manifest.state == "removed", owner.tombstone)
    return (
        manifest_values == (
            identifier, paths.workflow_identity, workspace,
            owner.route_pair_digest, owner.manifest_revision,
        )
        and owner_values == (
            identifier, manifest.workspace_path, manifest.route_pair_digest,
            manifest.manifest_revision,
        )
        and Path(owner.manifest_path) == paths.manifest
        and state_pair in {(False, False), (True, True)}
        and workspace.is_absolute()
    )
