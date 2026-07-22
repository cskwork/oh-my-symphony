"""Atomic, side-effect-free route-pair dispatch attestation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # pyright: ignore[reportMissingModuleSource]

from ..trackers.file import FileBoardTracker, _exclusive_lock
from ..workflow import ServiceConfig
from .contract import (
    MAX_CHILDREN,
    MAX_COORDINATORS,
    AidtRoutingFailure,
    RoutingService,
    RoutingSettings,
    canonical_fingerprint,
    load_routing_settings,
)
ROUTE_SCHEMA = "aidt-route-object-v2"
ROUTE_PAIR_SCHEMA = "aidt-route-pair-v1"
MAX_ROUTE_CARD_BYTES = 1_048_576
_BASE_REF = "refs/remotes/origin/aidt-prd"
_CARD_KEY = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")
_CHILD_ID = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*--[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ISSUE_TYPES = frozenset(
    {"bug", "story", "task", "sub-task", "improvement", "new feature"}
)
_CHILD_SOURCE_KEYS = {"kind", "key", "coordinator", "service"}
_CHILD_ROUTING_KEYS = {
    "schema",
    "role",
    "status",
    "fingerprint",
    "coordinator_fingerprint",
    "repository_binding_digest",
    "coordinator",
    "service",
    "kind",
    "checkout",
    "checkout_ref",
    "checkout_revision",
    "source_revision",
    "catalog_revision",
    "branch_prefix",
    "confidence",
    "evidence",
    "recheck_requirements",
}
_COORDINATOR_ROUTING_KEYS = {
    "schema",
    "role",
    "status",
    "fingerprint",
    "repository_binding_digest",
    "source_revision",
    "catalog_revision",
    "checkout_refs",
    "checkout_revisions",
    "repository_bindings",
    "service",
    "kind",
    "checkout",
    "checkout_ref",
    "checkout_revision",
    "branch_prefix",
    "confidence",
    "evidence",
    "candidates",
    "supporting_services",
    "children",
    "retained_children",
    "recheck_requirements",
    "decided_at",
}

PairReadHook = Callable[[str], None]


@dataclass(frozen=True)
class AidtRouteDispatchContract:
    identifier: str
    coordinator: str
    service: str
    kind: str
    checkout: str
    checkout_ref: str
    checkout_revision: str
    repository_binding_digest: str
    route_fingerprint: str
    coordinator_fingerprint: str
    source_revision: str
    catalog_revision: str
    route_pair_digest: str
    issue_type: str
    change_kind: str
    branch: str
    confidence: int

    @property
    def branch_prefix(self) -> str:
        """Compatibility spelling for the stored route projection."""
        return self.branch

    @property
    def catalog_checkout(self) -> str:
        """Manifest-facing spelling for the catalog checkout segment."""
        return self.checkout


@dataclass(frozen=True)
class _FileObservation:
    identity: tuple[int, ...] | None
    digest: str | None
    data: bytes | None


def load_route_dispatch_contract(
    config: ServiceConfig,
    identifier: str,
    *,
    pair_read_hook: PairReadHook | None = None,
) -> AidtRouteDispatchContract | None:
    """Attest one coordinator/child pair without Git or workspace mutation."""
    board = _board(config)
    if not _valid_child_id(identifier):
        return _load_non_child(board, identifier)
    coordinator = identifier.split("--", 1)[0]
    pair = _read_stable_pair(board, coordinator, identifier, pair_read_hook)
    coordinator_front = _frontmatter(pair[0])
    child_front = _frontmatter(pair[1])
    if not _pair_is_managed(identifier, coordinator_front, child_front):
        return None
    settings = load_routing_settings(config)
    if settings is None or coordinator_front is None or child_front is None:
        raise _failure(coordinator)
    return _attest(settings, identifier, coordinator_front, child_front)


def _board(config: ServiceConfig) -> FileBoardTracker:
    if config.tracker.kind != "file" or config.tracker.board_root is None:
        raise AidtRoutingFailure("config_invalid")
    return FileBoardTracker(config.tracker)


def _load_non_child(
    board: FileBoardTracker, identifier: object
) -> AidtRouteDispatchContract | None:
    if type(identifier) is not str or _CARD_KEY.fullmatch(identifier) is None:
        return None
    path = board.board_root / f"{identifier}.md"
    with _exclusive_lock(board._ticket_lock_path(identifier)):
        _validate_pair_names(board.board_root, (identifier, identifier))
        first = _read_card(path)
        second = _read_card(path)
        _validate_pair_names(board.board_root, (identifier, identifier))
    if first != second:
        raise AidtRoutingFailure("source_drift", f"card:{identifier}")
    front = _frontmatter(second)
    if front is not None and _managed_front(front):
        raise _failure(identifier)
    return None


def _read_stable_pair(
    board: FileBoardTracker,
    coordinator: str,
    child: str,
    hook: PairReadHook | None,
) -> tuple[_FileObservation, _FileObservation]:
    identifiers = sorted(
        (coordinator, child), key=lambda value: (value.casefold(), value)
    )
    paths = (board.board_root / f"{coordinator}.md", board.board_root / f"{child}.md")
    with ExitStack() as locks:
        for value in identifiers:
            locks.enter_context(_exclusive_lock(board._ticket_lock_path(value)))
        _validate_pair_names(board.board_root, (coordinator, child))
        first = _read_pair(paths, hook, "first")
        _call_hook(hook, "after_first_pair")
        second = _read_pair(paths, hook, "second")
        current = tuple(_current_identity(path) for path in paths)
        _validate_pair_names(board.board_root, (coordinator, child))
    if first != second or current != tuple(item.identity for item in second):
        raise _failure(coordinator)
    return second


def _read_pair(
    paths: tuple[Path, Path], hook: PairReadHook | None, cycle: str
) -> tuple[_FileObservation, _FileObservation]:
    coordinator = _read_card(paths[0])
    _call_hook(hook, f"after_{cycle}_coordinator")
    child = _read_card(paths[1])
    _call_hook(hook, f"after_{cycle}_child")
    return coordinator, child


def _validate_pair_names(root: Path, identifiers: tuple[str, str]) -> None:
    expected = {f"{identifier}.md".casefold(): f"{identifier}.md" for identifier in identifiers}
    found: dict[str, list[str]] = {key: [] for key in expected}
    card_count = 0
    try:
        for path in root.iterdir():
            if not path.name.casefold().endswith(".md"):
                continue
            card_count += 1
            if card_count > MAX_CHILDREN + MAX_COORDINATORS:
                raise AidtRoutingFailure("batch_limit")
            folded = path.name.casefold()
            if folded in found:
                found[folded].append(path.name)
    except AidtRoutingFailure:
        raise
    except OSError:
        raise AidtRoutingFailure("route_collision") from None
    for key, names in found.items():
        if names and names != [expected[key]]:
            raise AidtRoutingFailure("route_collision")


def _call_hook(hook: PairReadHook | None, seam: str) -> None:
    if hook is not None:
        hook(seam)


def _read_card(path: Path) -> _FileObservation:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return _FileObservation(None, None, None)
    except OSError:
        raise AidtRoutingFailure("route_collision") from None
    _validate_regular(before)
    data, opened = _open_bounded(path)
    try:
        after = path.lstat()
    except OSError:
        raise AidtRoutingFailure("source_drift") from None
    identities = (_stat_identity(before), _stat_identity(opened), _stat_identity(after))
    if len(set(identities)) != 1:
        raise AidtRoutingFailure("source_drift")
    return _FileObservation(identities[0], hashlib.sha256(data).hexdigest(), data)


def _open_bounded(path: Path) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            _validate_regular(opened)
            data = stream.read(MAX_ROUTE_CARD_BYTES + 1)
    except AidtRoutingFailure:
        raise
    except OSError:
        raise AidtRoutingFailure("route_collision") from None
    if len(data) > MAX_ROUTE_CARD_BYTES:
        raise AidtRoutingFailure("source_invalid")
    return data, opened


def _validate_regular(value: os.stat_result) -> None:
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        raise AidtRoutingFailure("route_collision")
    if value.st_size > MAX_ROUTE_CARD_BYTES:
        raise AidtRoutingFailure("source_invalid")


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _current_identity(path: Path) -> tuple[int, ...] | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise AidtRoutingFailure("source_drift") from None
    _validate_regular(value)
    return _stat_identity(value)


def _frontmatter(observation: _FileObservation) -> dict[str, Any] | None:
    if observation.data is None:
        return None
    try:
        text = observation.data.decode("utf-8", errors="strict")
        lines = text.splitlines()
        end = lines.index("---", 1)
        parsed = yaml.safe_load("\n".join(lines[1:end]))
    except (UnicodeDecodeError, ValueError, yaml.YAMLError):
        raise AidtRoutingFailure("source_invalid") from None
    if not lines or lines[0] != "---" or type(parsed) is not dict:
        raise AidtRoutingFailure("source_invalid")
    return parsed


def _pair_is_managed(
    identifier: str,
    coordinator: dict[str, Any] | None,
    child: dict[str, Any] | None,
) -> bool:
    if child is not None:
        return True
    if coordinator is None:
        return False
    routing = coordinator.get("routing")
    if not isinstance(routing, dict) or routing.get("schema") != ROUTE_SCHEMA:
        return False
    return identifier in routing.get("children", []) or identifier in routing.get(
        "retained_children", []
    )


def _managed_front(front: Mapping[str, Any]) -> bool:
    source = front.get("source")
    routing = front.get("routing")
    return (
        (
            isinstance(source, dict)
            and source.get("kind") in {"jira", "aidt-route-child"}
        )
        or isinstance(routing, dict)
        and routing.get("schema") == ROUTE_SCHEMA
    )


def _attest(
    settings: RoutingSettings,
    identifier: str,
    coordinator_front: dict[str, Any],
    child_front: dict[str, Any],
) -> AidtRouteDispatchContract:
    coordinator = identifier.split("--", 1)[0]
    source, coordinator_route = _coordinator_values(
        settings, coordinator, identifier, coordinator_front
    )
    service, child_route = _child_values(settings, coordinator, identifier, child_front)
    _validate_pair_projection(settings, service, coordinator_route, child_route)
    issue_type, change_kind = _change_kind(source["issue_type"], coordinator)
    branch = _branch(coordinator, service.kind, change_kind)
    if not _valid_stored_branches(coordinator_route, child_route, branch):
        raise _failure(coordinator)
    return _dispatch_contract(
        identifier,
        coordinator,
        service,
        coordinator_route,
        child_route,
        source,
        issue_type,
        change_kind,
        branch,
    )


def _coordinator_values(
    settings: RoutingSettings,
    coordinator: str,
    child: str,
    front: dict[str, Any],
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    from .decision import _validate_source

    if front.get("id") != coordinator or front.get("identifier") != coordinator:
        raise _failure(coordinator)
    source = _validate_source(front.get("source"), coordinator)
    routing = _exact_mapping(
        front.get("routing"), _COORDINATOR_ROUTING_KEYS, coordinator
    )
    children = _children(routing.get("children"), coordinator)
    retained = _children(
        routing.get("retained_children"), coordinator, allow_empty=True
    )
    expected_state = (
        settings.ready_state if len(children) == 1 else settings.coordinator_state
    )
    valid = (
        front.get("state") == expected_state
        and routing.get("schema") == ROUTE_SCHEMA
        and routing.get("role") == "coordinator"
        and routing.get("status") == "pending_fresh_base_equality"
        and child in children
        and child not in retained
        and not retained
        and routing.get("source_revision") == source["revision"]
        and routing.get("catalog_revision") == settings.catalog_revision
        and routing.get("recheck_requirements") == ["fresh_base_equality"]
        and _valid_selected_children(settings, coordinator, children)
    )
    if not valid:
        raise _failure(coordinator)
    return source, routing


def _child_values(
    settings: RoutingSettings,
    coordinator: str,
    identifier: str,
    front: dict[str, Any],
) -> tuple[RoutingService, dict[str, Any]]:
    service_id = identifier.split("--", 1)[1]
    source = _exact_mapping(front.get("source"), _CHILD_SOURCE_KEYS, coordinator)
    expected_source = {
        "kind": "aidt-route-child",
        "key": f"{coordinator}::{service_id}",
        "coordinator": coordinator,
        "service": service_id,
    }
    route = _exact_mapping(front.get("routing"), _CHILD_ROUTING_KEYS, coordinator)
    valid = (
        front.get("id") == identifier
        and front.get("identifier") == identifier
        and front.get("state") == settings.ready_state
        and source == expected_source
        and route.get("schema") == ROUTE_SCHEMA
        and route.get("role") == "child"
        and route.get("status") == "pending_fresh_base_equality"
        and route.get("coordinator") == coordinator
        and route.get("service") == service_id
    )
    if not valid:
        raise _failure(coordinator)
    return _service(settings, service_id, coordinator), route


def _validate_pair_projection(
    settings: RoutingSettings,
    service: RoutingService,
    coordinator: Mapping[str, Any],
    child: Mapping[str, Any],
) -> None:
    ref = coordinator.get("source_revision")
    maps = _coordinator_maps(settings, coordinator)
    values = (
        _sha256(coordinator.get("fingerprint")),
        _sha256(coordinator.get("repository_binding_digest")),
        _sha256(ref),
        coordinator.get("catalog_revision") == settings.catalog_revision,
        child.get("fingerprint") == coordinator.get("fingerprint"),
        child.get("coordinator_fingerprint") == coordinator.get("fingerprint"),
        child.get("source_revision") == ref,
        child.get("catalog_revision") == settings.catalog_revision,
        child.get("checkout_ref") == maps[0][service.id] == _BASE_REF,
        child.get("checkout_revision") == maps[1][service.id],
        child.get("repository_binding_digest") == maps[2][service.id],
        _sha256(child.get("fingerprint")),
        _sha256(child.get("coordinator_fingerprint")),
        _sha256(child.get("repository_binding_digest")),
        child.get("checkout") == service.checkout,
        child.get("kind") == service.kind,
        child.get("recheck_requirements") == ["fresh_base_equality"],
        _valid_confidence(child.get("confidence"), settings.minimum_confidence),
        _valid_primary_projection(service, coordinator, child),
    )
    if not all(values) or not _sha1(child.get("checkout_revision")):
        raise AidtRoutingFailure("source_drift")


def _coordinator_maps(
    settings: RoutingSettings, route: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    expected = {service.id for service in settings.services if service.enabled}
    refs = _string_map(route.get("checkout_refs"), expected)
    revisions = _string_map(route.get("checkout_revisions"), expected)
    bindings = _string_map(route.get("repository_bindings"), expected)
    valid = all(value == _BASE_REF for value in refs.values())
    valid = valid and all(_sha1(value) for value in revisions.values())
    valid = valid and all(_sha256(value) for value in bindings.values())
    digest = canonical_fingerprint("aidt-route-binding-v1", bindings)
    if not valid or route.get("repository_binding_digest") != digest:
        raise AidtRoutingFailure("source_drift")
    return refs, revisions, bindings


def _valid_selected_children(
    settings: RoutingSettings, coordinator: str, children: list[str]
) -> bool:
    prefix = f"{coordinator}--"
    selected = [item.removeprefix(prefix) for item in children]
    enabled = {service.id for service in settings.services if service.enabled}
    return (
        all(item.startswith(prefix) for item in children)
        and len(set(selected)) == len(selected)
        and set(selected).issubset(enabled)
    )


def _valid_stored_branches(
    coordinator: Mapping[str, Any], child: Mapping[str, Any], branch: str
) -> bool:
    children = coordinator["children"]
    if child.get("branch_prefix") != branch:
        return False
    if len(children) == 1:
        return coordinator.get("branch_prefix") == branch
    return coordinator.get("branch_prefix") is None


def _valid_primary_projection(
    service: RoutingService,
    coordinator: Mapping[str, Any],
    child: Mapping[str, Any],
) -> bool:
    if len(coordinator["children"]) != 1:
        fields = ("service", "kind", "checkout", "checkout_ref", "checkout_revision")
        return (
            all(coordinator.get(field) is None for field in fields)
            and coordinator.get("confidence") == 0
        )
    return (
        coordinator.get("service") == service.id
        and coordinator.get("kind") == service.kind
        and coordinator.get("checkout") == service.checkout
        and coordinator.get("checkout_ref") == child.get("checkout_ref")
        and coordinator.get("checkout_revision") == child.get("checkout_revision")
        and coordinator.get("confidence") == child.get("confidence")
    )


def _dispatch_contract(
    identifier: str,
    coordinator: str,
    service: RoutingService,
    coordinator_route: Mapping[str, Any],
    child_route: Mapping[str, Any],
    source: Mapping[str, Any],
    issue_type: str,
    change_kind: str,
    branch: str,
) -> AidtRouteDispatchContract:
    pair_digest = _route_pair_digest(coordinator_route, child_route, source)
    return AidtRouteDispatchContract(
        identifier=identifier,
        coordinator=coordinator,
        service=service.id,
        kind=service.kind,
        checkout=service.checkout,
        checkout_ref=_BASE_REF,
        checkout_revision=str(child_route["checkout_revision"]),
        repository_binding_digest=str(child_route["repository_binding_digest"]),
        route_fingerprint=str(child_route["fingerprint"]),
        coordinator_fingerprint=str(child_route["coordinator_fingerprint"]),
        source_revision=str(child_route["source_revision"]),
        catalog_revision=str(child_route["catalog_revision"]),
        route_pair_digest=pair_digest,
        issue_type=issue_type,
        change_kind=change_kind,
        branch=branch,
        confidence=int(child_route["confidence"]),
    )


def _route_pair_digest(
    coordinator: Mapping[str, Any],
    child: Mapping[str, Any],
    source: Mapping[str, Any],
) -> str:
    coordinator_bytes = _canonical_bytes({"source": source, "routing": coordinator})
    child_source = {
        "kind": "aidt-route-child",
        "key": f"{child['coordinator']}::{child['service']}",
        "coordinator": child["coordinator"],
        "service": child["service"],
    }
    child_bytes = _canonical_bytes({"source": child_source, "routing": child})
    children = _canonical_bytes(sorted(coordinator["children"], key=str.casefold))
    first = hashlib.sha256(coordinator_bytes).hexdigest().encode("ascii")
    second = hashlib.sha256(child_bytes).hexdigest().encode("ascii")
    payload = (
        ROUTE_PAIR_SCHEMA.encode() + b"\0" + first + b"\0" + second + b"\0" + children
    )
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise AidtRoutingFailure("source_invalid") from None


def _change_kind(value: object, coordinator: str) -> tuple[str, str]:
    if type(value) is not str or _has_control(value):
        raise _failure(coordinator)
    issue_type = value.strip().casefold()
    if issue_type not in _ISSUE_TYPES:
        raise _failure(coordinator)
    return issue_type, "fix" if issue_type == "bug" else "feat"


def _branch(coordinator: str, kind: str, change_kind: str) -> str:
    value = f"{change_kind}/{coordinator}"
    return f"csk-{value}" if kind == "frontend" else value


def _service(
    settings: RoutingSettings, service_id: str, coordinator: str
) -> RoutingService:
    matches = [
        item for item in settings.services if item.enabled and item.id == service_id
    ]
    if len(matches) != 1:
        raise _failure(coordinator)
    return matches[0]


def _children(
    value: object, coordinator: str, *, allow_empty: bool = False
) -> list[str]:
    if type(value) is not list or len(value) > MAX_CHILDREN:
        raise _failure(coordinator)
    if not allow_empty and not value:
        raise _failure(coordinator)
    if any(type(item) is not str or not _valid_child_id(item) for item in value):
        raise _failure(coordinator)
    if len({item.casefold() for item in value}) != len(value):
        raise _failure(coordinator)
    return value


def _string_map(value: object, keys: set[str]) -> dict[str, str]:
    if type(value) is not dict or set(value) != keys:
        raise AidtRoutingFailure("source_drift")
    if any(type(item) is not str for item in value.values()):
        raise AidtRoutingFailure("source_drift")
    return value


def _exact_mapping(value: object, keys: set[str], coordinator: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise _failure(coordinator)
    return value


def _valid_child_id(value: object) -> bool:
    return type(value) is str and _CHILD_ID.fullmatch(value) is not None


def _sha1(value: object) -> bool:
    return type(value) is str and _SHA1.fullmatch(value) is not None


def _sha256(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _valid_confidence(value: object, minimum: int) -> bool:
    return type(value) is int and minimum <= value <= 100


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _failure(coordinator: str) -> AidtRoutingFailure:
    ref = f"card:{coordinator}" if _CARD_KEY.fullmatch(coordinator) else None
    return AidtRoutingFailure("source_drift", ref)
