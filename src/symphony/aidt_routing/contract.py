"""Closed, side-effect-free contract for AIDT routing."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..workflow import ServiceConfig


MAX_SERVICES = 64
MAX_ALIASES_PER_SERVICE = 32
MAX_ANCHORS_PER_CATEGORY = 16
MAX_EVIDENCE_RECORDS = 32
MAX_VALUE_BYTES = 256
MAX_ID_BYTES = 48
MAX_GIT_PATHS_PER_SERVICE = 64
MAX_SERVICE_OBJECT_BYTES = 4_194_304
MAX_OBSERVATION_BYTES = 16_777_216
MAX_COORDINATORS = 500
MAX_CHILDREN = 2_000
MAX_ROUTE_BATCH_BYTES = 10_485_760

CATALOG_SCHEMA = "aidt-catalog-object-v2"

_SERVICE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CARD_KEY = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")
_ROUTING_KEYS = {
    "enabled",
    "source_mode",
    "aidt_root",
    "minimum_confidence",
    "states",
    "services",
}
_STATE_KEYS = {"ready", "review", "coordinator"}
_SERVICE_KEYS = {
    "id",
    "checkout",
    "kind",
    "enabled",
    "markers",
    "component_aliases",
    "context_anchors",
    "route_anchors",
    "domain_anchors",
}
_FAILURE_CATEGORIES = frozenset(
    {
        "config_invalid",
        "source_mode_invalid",
        "intake_unavailable",
        "catalog_invalid",
        "repository_invalid",
        "repository_changed",
        "git_timeout",
        "git_output_limit",
        "git_command_failed",
        "git_protocol_invalid",
        "git_object_invalid",
        "revision_changed",
        "source_invalid",
        "source_drift",
        "route_collision",
        "batch_limit",
        "preflight_changed",
        "partial_apply",
        "workflow_reload_error",
        "internal_error",
    }
)
_SERVICE_REF_CATEGORIES = frozenset(
    {
        "repository_invalid",
        "repository_changed",
        "git_timeout",
        "git_output_limit",
        "git_command_failed",
        "git_protocol_invalid",
        "git_object_invalid",
        "revision_changed",
    }
)
_CARD_REF_CATEGORIES = frozenset(
    {
        "source_invalid",
        "source_drift",
        "route_collision",
        "preflight_changed",
        "partial_apply",
    }
)
_RESULT_STATUSES = frozenset({"disabled", "success", "review", "failure"})


class AidtRoutingFailure(Exception):
    """Sanitized routing failure safe for public results and logs."""

    def __init__(self, category: str, identifier: str | None = None) -> None:
        safe_category = (
            category
            if type(category) is str and category in _FAILURE_CATEGORIES
            else "internal_error"
        )
        self.category = safe_category
        self.identifier = _allowed_ref(safe_category, identifier)
        super().__init__(safe_category)


@dataclass(frozen=True)
class ContextAnchor:
    id: str
    file: str
    literal: str


@dataclass(frozen=True)
class RouteAnchor:
    id: str
    file: str
    method: str
    endpoint: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class DomainAnchor:
    id: str
    file: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class RoutingService:
    id: str
    checkout: str
    kind: str
    enabled: bool
    markers: tuple[str, ...]
    component_aliases: tuple[str, ...]
    context_anchors: tuple[ContextAnchor, ...]
    route_anchors: tuple[RouteAnchor, ...]
    domain_anchors: tuple[DomainAnchor, ...]


@dataclass(frozen=True)
class RoutingSettings:
    source_mode: str
    aidt_root: Path
    minimum_confidence: int
    ready_state: str
    review_state: str
    coordinator_state: str
    services: tuple[RoutingService, ...]
    catalog_revision: str


@dataclass(frozen=True, repr=False)
class AidtRoutingResult:
    enabled: bool
    global_allow_dispatch: bool
    blocked_identifiers: frozenset[str]
    routed_count: int
    review_count: int
    child_count: int
    failure_count: int
    status: str
    error_category: str | None = None
    error_ref: str | None = None
    provisionable_child_identifiers: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not _valid_result(self):
            _normalize_result_failure(self)

    def __repr__(self) -> str:
        fields = (
            f"enabled={self.enabled!r}",
            f"global_allow_dispatch={self.global_allow_dispatch!r}",
            f"blocked_count={len(self.blocked_identifiers)!r}",
            f"routed_count={self.routed_count!r}",
            f"review_count={self.review_count!r}",
            f"child_count={self.child_count!r}",
            f"failure_count={self.failure_count!r}",
            f"status={self.status!r}",
            f"error_category={self.error_category!r}",
            f"error_ref={self.error_ref!r}",
            f"provisionable_child_count={len(self.provisionable_child_identifiers)!r}",
        )
        return f"AidtRoutingResult({', '.join(fields)})"


def _valid_result(result: AidtRoutingResult) -> bool:
    if type(result.enabled) is not bool or type(result.global_allow_dispatch) is not bool:
        return False
    if not _valid_blocked_identifiers(result.blocked_identifiers):
        return False
    counts = (
        _valid_count(result.routed_count, MAX_COORDINATORS),
        _valid_count(result.review_count, MAX_COORDINATORS),
        _valid_count(result.child_count, MAX_CHILDREN),
        _valid_count(result.failure_count, MAX_COORDINATORS),
    )
    if not all(counts):
        return False
    if type(result.status) is not str or result.status not in _RESULT_STATUSES:
        return False
    if not _valid_provisionable_identifiers(result):
        return False
    return _valid_error_pair(result.error_category, result.error_ref)


def _valid_count(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _valid_blocked_identifiers(value: object) -> bool:
    if type(value) is not frozenset:
        return False
    if len(value) > MAX_COORDINATORS + MAX_CHILDREN:
        return False
    return all(
        type(identifier) is str and _valid_blocked_identifier(identifier)
        for identifier in value
    )


def _valid_provisionable_identifiers(result: AidtRoutingResult) -> bool:
    value = result.provisionable_child_identifiers
    if type(value) is not frozenset or len(value) > MAX_CHILDREN:
        return False
    if not all(type(item) is str and _valid_child_identifier(item) for item in value):
        return False
    if not value.issubset(result.blocked_identifiers):
        return False
    allowed = result.enabled and result.global_allow_dispatch
    return not value or allowed and result.status in {"success", "review"}


def _valid_child_identifier(value: str) -> bool:
    parts = value.split("--")
    return len(parts) == 2 and _valid_card_key(parts[0]) and _valid_id(parts[1])


def _valid_blocked_identifier(value: str) -> bool:
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        return False
    parts = value.split("--")
    if len(parts) == 1:
        return _valid_card_key(parts[0])
    if len(parts) != 2:
        return False
    return _valid_card_key(parts[0]) and _valid_id(parts[1])


def _valid_error_pair(category: object, ref: object) -> bool:
    if category is None:
        return ref is None
    if type(category) is not str or category not in _FAILURE_CATEGORIES:
        return False
    if ref is None:
        return True
    return type(ref) is str and _allowed_ref(category, ref) == ref


def _normalize_result_failure(result: AidtRoutingResult) -> None:
    object.__setattr__(result, "enabled", True)
    object.__setattr__(result, "global_allow_dispatch", False)
    object.__setattr__(result, "blocked_identifiers", frozenset())
    object.__setattr__(result, "routed_count", 0)
    object.__setattr__(result, "review_count", 0)
    object.__setattr__(result, "child_count", 0)
    object.__setattr__(result, "failure_count", 1)
    object.__setattr__(result, "status", "failure")
    object.__setattr__(result, "error_category", "internal_error")
    object.__setattr__(result, "error_ref", None)
    object.__setattr__(result, "provisionable_child_identifiers", frozenset())


def canonical_fingerprint(schema: str, value: object) -> str:
    """Hash version-tagged canonical JSON without accepting NaN values."""
    encoded = json.dumps(
        {"schema": schema, "value": value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_routing_settings(config: ServiceConfig) -> RoutingSettings | None:
    """Parse the feature-local schema while preserving a disabled early return."""
    value = config.raw.get("aidt_routing")
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise _config_failure()
    if value["enabled"] is False:
        return None
    raw = _mapping(value, _ROUTING_KEYS)
    _validate_top_level(raw)
    root = Path(_bounded_text(raw["aidt_root"]))
    if not root.is_absolute():
        raise _config_failure()
    states = _mapping(raw["states"], _STATE_KEYS)
    services = _parse_services(raw["services"])
    return _settings(raw, root, states, services)


def _allowed_ref(category: object, value: object) -> str | None:
    if value is None or type(category) is not str or type(value) is not str:
        return None
    if category in _SERVICE_REF_CATEGORIES and _valid_service_ref(value):
        return value
    if category in _CARD_REF_CATEGORIES and _valid_card_ref(value):
        return value
    return None


def _valid_service_ref(value: str) -> bool:
    prefix = "service:"
    return value.startswith(prefix) and _valid_id(value[len(prefix) :])


def _valid_card_ref(value: str) -> bool:
    prefix = "card:"
    return value.startswith(prefix) and _valid_card_key(value[len(prefix) :])


def _valid_card_key(value: str) -> bool:
    return (
        0 < len(value.encode("utf-8")) <= MAX_VALUE_BYTES
        and _CARD_KEY.fullmatch(value) is not None
    )


def _valid_id(value: str) -> bool:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return 0 < len(encoded) <= MAX_ID_BYTES and _SERVICE_ID.fullmatch(value) is not None


def _config_failure() -> AidtRoutingFailure:
    return AidtRoutingFailure("config_invalid")


def _mapping(value: Any, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _config_failure()
    return value


def _bounded_text(value: Any, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise _config_failure()
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES or _has_control(value):
        raise _config_failure()
    if identifier and not _valid_id(value):
        raise _config_failure()
    return value


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _unique(values: Iterable[str]) -> None:
    seen: set[str] = set()
    for value in values:
        key = _normalized(value)
        if key in seen:
            raise _config_failure()
        seen.add(key)


def _git_path(value: Any, *, segment: bool = False) -> str:
    text = _bounded_text(value)
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        raise _config_failure() from None
    parts = text.split("/")
    invalid = (
        "\\" in text
        or text.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(text).is_absolute()
    )
    if invalid or (segment and len(parts) != 1):
        raise _config_failure()
    return text


def _bounded_list(value: Any, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise _config_failure()
    return value


def _text_list(value: Any, maximum: int) -> tuple[str, ...]:
    items = tuple(_bounded_text(item) for item in _bounded_list(value, maximum))
    _unique(items)
    return items


def _context_anchor(value: Any) -> ContextAnchor:
    raw = _mapping(value, {"id", "file", "literal"})
    return ContextAnchor(
        id=_bounded_text(raw["id"], identifier=True),
        file=_git_path(raw["file"]),
        literal=_bounded_text(raw["literal"]),
    )


def _route_anchor(value: Any) -> RouteAnchor:
    raw = _mapping(value, {"id", "file", "method", "endpoint", "symbols"})
    symbols = _text_list(raw["symbols"], MAX_ANCHORS_PER_CATEGORY)
    if not symbols:
        raise _config_failure()
    return RouteAnchor(
        id=_bounded_text(raw["id"], identifier=True),
        file=_git_path(raw["file"]),
        method=_bounded_text(raw["method"]),
        endpoint=_bounded_text(raw["endpoint"]),
        symbols=symbols,
    )


def _domain_anchor(value: Any) -> DomainAnchor:
    raw = _mapping(value, {"id", "file", "terms"})
    terms = _text_list(raw["terms"], MAX_ANCHORS_PER_CATEGORY)
    if not terms:
        raise _config_failure()
    return DomainAnchor(
        id=_bounded_text(raw["id"], identifier=True),
        file=_git_path(raw["file"]),
        terms=terms,
    )


def _anchors(value: Any, parser: Callable[[Any], Any]) -> tuple[Any, ...]:
    anchors = tuple(
        parser(item) for item in _bounded_list(value, MAX_ANCHORS_PER_CATEGORY)
    )
    _unique(anchor.id for anchor in anchors)
    return anchors


def _service(value: Any) -> RoutingService:
    raw = _mapping(value, _SERVICE_KEYS)
    if type(raw["enabled"]) is not bool:
        raise _config_failure()
    kind = _bounded_text(raw["kind"])
    if kind not in {"backend", "frontend"}:
        raise _config_failure()
    markers = tuple(
        _git_path(item)
        for item in _bounded_list(raw["markers"], MAX_ANCHORS_PER_CATEGORY)
    )
    aliases = _text_list(raw["component_aliases"], MAX_ALIASES_PER_SERVICE)
    if not markers or not aliases:
        raise _config_failure()
    _unique(markers)
    return RoutingService(
        id=_bounded_text(raw["id"], identifier=True),
        checkout=_git_path(raw["checkout"], segment=True),
        kind=kind,
        enabled=raw["enabled"],
        markers=markers,
        component_aliases=aliases,
        context_anchors=_anchors(raw["context_anchors"], _context_anchor),
        route_anchors=_anchors(raw["route_anchors"], _route_anchor),
        domain_anchors=_anchors(raw["domain_anchors"], _domain_anchor),
    )


def _parse_services(value: Any) -> tuple[RoutingService, ...]:
    services = tuple(_service(item) for item in _bounded_list(value, MAX_SERVICES))
    _validate_catalog_collisions(services)
    return tuple(sorted(services, key=lambda item: item.id))


def _validate_catalog_collisions(services: tuple[RoutingService, ...]) -> None:
    _unique(item.id for item in services)
    _unique(item.checkout for item in services)
    owners: dict[str, str] = {}
    for service in services:
        for value in (service.id, *service.component_aliases):
            key = _normalized(value)
            owner = owners.setdefault(key, service.id)
            if owner != service.id:
                raise _config_failure()


def _validate_top_level(raw: Mapping[str, Any]) -> None:
    if raw["source_mode"] not in {"same_tick_jira", "static_snapshot"}:
        raise _config_failure()
    confidence = raw["minimum_confidence"]
    if type(confidence) is not int or confidence != 90:
        raise _config_failure()


def _settings(
    raw: Mapping[str, Any],
    root: Path,
    states: Mapping[str, Any],
    services: tuple[RoutingService, ...],
) -> RoutingSettings:
    state_values = {key: _bounded_text(states[key]) for key in _STATE_KEYS}
    catalog_revision = canonical_fingerprint(
        CATALOG_SCHEMA,
        [_catalog_service(service) for service in services],
    )
    return RoutingSettings(
        source_mode=raw["source_mode"],
        aidt_root=root,
        minimum_confidence=90,
        ready_state=state_values["ready"],
        review_state=state_values["review"],
        coordinator_state=state_values["coordinator"],
        services=services,
        catalog_revision=catalog_revision,
    )


def _catalog_service(service: RoutingService) -> dict[str, Any]:
    return {
        "id": service.id,
        "checkout": service.checkout,
        "kind": service.kind,
        "enabled": service.enabled,
        "markers": sorted(service.markers),
        "component_aliases": sorted(_normalized(item) for item in service.component_aliases),
        "context_anchors": sorted(
            (_context_value(item) for item in service.context_anchors),
            key=lambda item: item["id"],
        ),
        "route_anchors": sorted(
            (_route_value(item) for item in service.route_anchors),
            key=lambda item: item["id"],
        ),
        "domain_anchors": sorted(
            (_domain_value(item) for item in service.domain_anchors),
            key=lambda item: item["id"],
        ),
    }


def _context_value(anchor: ContextAnchor) -> dict[str, Any]:
    return {"id": anchor.id, "file": anchor.file, "literal": anchor.literal}


def _route_value(anchor: RouteAnchor) -> dict[str, Any]:
    return {
        "id": anchor.id,
        "file": anchor.file,
        "method": anchor.method,
        "endpoint": anchor.endpoint,
        "symbols": sorted(anchor.symbols),
    }


def _domain_value(anchor: DomainAnchor) -> dict[str, Any]:
    return {
        "id": anchor.id,
        "file": anchor.file,
        "terms": sorted(anchor.terms, key=_normalized),
    }
