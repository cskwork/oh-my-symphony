"""Pure structured-source scoring and route projections."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .contract import (
    MAX_EVIDENCE_RECORDS,
    AidtRoutingFailure,
    RoutingService,
    RoutingSettings,
    canonical_fingerprint,
)
from .git_objects import CatalogObservation, ObservedService


ROUTE_SCHEMA = "aidt-route-object-v2"
MAX_SOURCE_TEXT_BYTES = 65_536
MAX_SOURCE_COMPONENTS = 64
_SOURCE_KEYS = {
    "schema",
    "kind",
    "key",
    "summary",
    "description",
    "components",
    "status",
    "priority",
    "issue_type",
    "updated",
    "url",
    "parent",
    "revision",
}
_PARENT_KEYS = {"key", "summary", "description", "components"}
_SOURCE_TEXT_KEYS = (
    "schema",
    "kind",
    "key",
    "summary",
    "description",
    "status",
    "issue_type",
    "updated",
    "url",
    "revision",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RouteCardProjection:
    identifier: str
    coordinator: str
    service: str | None
    role: str
    routing: dict[str, Any]
    source: dict[str, str] | None
    desired_state: str | None
    route_owned_states: tuple[str, ...]
    expected_source_revision: str | None
    marker: str


@dataclass(frozen=True)
class RouteResolution:
    coordinator: RouteCardProjection
    children: tuple[RouteCardProjection, ...]
    retained: tuple[RouteCardProjection, ...]
    routed: bool

    @property
    def projections(self) -> tuple[RouteCardProjection, ...]:
        return (*self.children, *self.retained, self.coordinator)

    @property
    def blocked_identifiers(self) -> frozenset[str]:
        return frozenset(item.identifier for item in self.projections)


@dataclass(frozen=True)
class _Evidence:
    service: str
    category: str
    source: str
    anchor: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "service": self.service,
            "category": self.category,
            "source": self.source,
            "anchor": self.anchor,
        }


@dataclass(frozen=True)
class _Candidate:
    observed: ObservedService
    confidence: int
    evidence: tuple[_Evidence, ...]
    authoritative: frozenset[str]
    explicit_tokens: frozenset[str]


def resolve_card(
    frontmatter: Mapping[str, object],
    settings: RoutingSettings,
    catalog: CatalogObservation,
    *,
    now: Callable[[], datetime],
) -> RouteResolution:
    """Resolve one Jira coordinator without filesystem or tracker access."""
    identifier = _coordinator_identifier(frontmatter)
    source = _validate_source(frontmatter.get("source"), identifier)
    candidates = tuple(_candidate(item, source) for item in catalog.services)
    passing = _passing(candidates, settings.minimum_confidence)
    conflict = _component_conflict(candidates)
    selected = _selected_candidates(passing, conflict)
    old_children = _existing_children(frontmatter, identifier)
    return _project_resolution(
        frontmatter,
        source,
        settings,
        catalog,
        candidates,
        selected,
        old_children,
        now,
    )


def _coordinator_identifier(frontmatter: Mapping[str, object]) -> str:
    identifier = frontmatter.get("id") or frontmatter.get("identifier")
    if not isinstance(identifier, str):
        raise AidtRoutingFailure("source_invalid")
    if frontmatter.get("id") != identifier or frontmatter.get("identifier") != identifier:
        raise AidtRoutingFailure("source_invalid", f"card:{identifier}")
    return identifier


def _validate_source(value: object, identifier: str) -> Mapping[str, Any]:
    ref = f"card:{identifier}"
    if not isinstance(value, dict) or set(value) != _SOURCE_KEYS:
        raise AidtRoutingFailure("source_invalid", ref)
    if any(not isinstance(value[key], str) for key in _SOURCE_TEXT_KEYS):
        raise AidtRoutingFailure("source_invalid", ref)
    if value["kind"] != "jira" or value["key"] != identifier:
        raise AidtRoutingFailure("source_invalid", ref)
    _validate_source_strings(value, ref)
    _components(value["components"], ref)
    _validate_parent(value["parent"], ref)
    _validate_source_revision(value, ref)
    return value


def _validate_source_strings(source: Mapping[str, Any], ref: str) -> None:
    for key in _SOURCE_TEXT_KEYS:
        text = source[key]
        if len(text.encode("utf-8")) > MAX_SOURCE_TEXT_BYTES or _has_control(text):
            raise AidtRoutingFailure("source_invalid", ref)
    priority = source["priority"]
    if priority is not None and not isinstance(priority, str):
        raise AidtRoutingFailure("source_invalid", ref)
    if isinstance(priority, str) and _has_control(priority):
        raise AidtRoutingFailure("source_invalid", ref)
    try:
        updated = datetime.fromisoformat(source["updated"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise AidtRoutingFailure("source_invalid", ref) from exc
    if updated.tzinfo is None:
        raise AidtRoutingFailure("source_invalid", ref)


def _components(value: object, ref: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_SOURCE_COMPONENTS:
        raise AidtRoutingFailure("source_invalid", ref)
    if any(not isinstance(item, str) or not item for item in value):
        raise AidtRoutingFailure("source_invalid", ref)
    if any(_has_control(item) for item in value):
        raise AidtRoutingFailure("source_invalid", ref)
    normalized = [_normalized(item) for item in value]
    if len(set(normalized)) != len(normalized):
        raise AidtRoutingFailure("source_invalid", ref)
    return tuple(value)


def _validate_parent(value: object, ref: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != _PARENT_KEYS:
        raise AidtRoutingFailure("source_invalid", ref)
    for key in ("key", "summary", "description"):
        text = value.get(key)
        if not isinstance(text, str) or _has_control(text):
            raise AidtRoutingFailure("source_invalid", ref)
        if len(text.encode("utf-8")) > MAX_SOURCE_TEXT_BYTES:
            raise AidtRoutingFailure("source_invalid", ref)
    _components(value["components"], ref)


def _validate_source_revision(source: Mapping[str, Any], ref: str) -> None:
    revision = source["revision"]
    if _SHA256.fullmatch(revision) is None:
        raise AidtRoutingFailure("source_invalid", ref)
    semantic = {key: source[key] for key in source if key != "revision"}
    expected = canonical_fingerprint(source["schema"], semantic)
    if revision != expected:
        raise AidtRoutingFailure("source_invalid", ref)


def _has_control(value: str) -> bool:
    return any(ord(char) == 127 or ord(char) < 32 and char not in "\t\n\r" for char in value)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _source_text(source: Mapping[str, Any]) -> str:
    values = [source["summary"], source["description"]]
    parent = source.get("parent")
    if isinstance(parent, dict):
        values.extend([parent["summary"], parent["description"]])
    return "\n".join(values)


def _candidate(observed: ObservedService, source: Mapping[str, Any]) -> _Candidate:
    found: dict[str, _Evidence] = {}
    explicit: set[str] = set()
    service = observed.service
    text = _source_text(source)
    components = _components(source["components"], "source")
    _add_component(found, explicit, service, components)
    _add_context(found, service, observed.contents, text)
    _add_code(found, explicit, service, observed.contents, source, text)
    _add_domain(found, service, observed.contents, text)
    _add_supporting(found, service, text)
    ordered = tuple(sorted(found.values(), key=_evidence_key))
    authoritative = frozenset(found) & {"component", "context", "code"}
    confidence = min(100, sum(_weight(item) for item in found))
    return _Candidate(observed, confidence, ordered, authoritative, frozenset(explicit))


def _add_component(
    found: dict[str, _Evidence],
    explicit: set[str],
    service: RoutingService,
    components: tuple[str, ...],
) -> None:
    aliases = {_normalized(alias) for alias in service.component_aliases}
    matches = sorted(component for component in components if _normalized(component) in aliases)
    if not matches:
        return
    found["component"] = _Evidence(service.id, "component", "components")
    explicit.add(f"component:{_normalized(matches[0])}")


def _add_context(
    found: dict[str, _Evidence],
    service: RoutingService,
    contents: Mapping[str, str],
    text: str,
) -> None:
    for anchor in service.context_anchors:
        if anchor.literal in contents[anchor.file] and anchor.literal in text:
            found["context"] = _Evidence(service.id, "context", "source", anchor.id)
            return


def _add_code(
    found: dict[str, _Evidence],
    explicit: set[str],
    service: RoutingService,
    contents: Mapping[str, str],
    source: Mapping[str, Any],
    text: str,
) -> None:
    for anchor in service.route_anchors:
        contract = f"{anchor.method} {anchor.endpoint}"
        owned = all(symbol in contents[anchor.file] for symbol in anchor.symbols)
        if owned and contract in text:
            found["code"] = _Evidence(service.id, "code", "source", anchor.id)
            explicit.add(f"code:{contract}")
            if _parent_contains(source, contract):
                found["parent"] = _Evidence(service.id, "parent", "parent", anchor.id)
            return


def _add_domain(
    found: dict[str, _Evidence],
    service: RoutingService,
    contents: Mapping[str, str],
    text: str,
) -> None:
    folded_text = text.casefold()
    for anchor in service.domain_anchors:
        owned = contents[anchor.file].casefold()
        if all(term.casefold() in owned and term.casefold() in folded_text for term in anchor.terms):
            found["domain"] = _Evidence(service.id, "domain", "source", anchor.id)
            return


def _add_supporting(
    found: dict[str, _Evidence], service: RoutingService, text: str
) -> None:
    if sum(_weight(category) for category in found) >= 90:
        return
    folded = text.casefold()
    if service.kind.casefold() in folded:
        found["kind"] = _Evidence(service.id, "kind", "source")
    keywords = (service.id, *service.component_aliases)
    if any(value.casefold() in folded for value in keywords):
        found["supporting"] = _Evidence(service.id, "supporting", "source")


def _parent_contains(source: Mapping[str, Any], contract: str) -> bool:
    parent = source.get("parent")
    if not isinstance(parent, dict):
        return False
    return contract in f"{parent['summary']}\n{parent['description']}"


def _weight(category: str) -> int:
    return {
        "component": 45,
        "context": 30,
        "code": 35,
        "domain": 15,
        "parent": 10,
        "kind": 5,
        "supporting": 5,
    }.get(category, 0)


def _evidence_key(item: _Evidence) -> tuple[str, str, str, str]:
    return (item.service, item.category, _normalized(item.source), item.anchor)


def _passing(candidates: tuple[_Candidate, ...], minimum: int) -> tuple[_Candidate, ...]:
    return tuple(
        item
        for item in candidates
        if item.confidence >= minimum
        and len(item.authoritative) >= 2
        and bool(item.authoritative & {"component", "code"})
    )


def _component_conflict(candidates: tuple[_Candidate, ...]) -> bool:
    component_ids = {
        item.observed.service.id for item in candidates if "component" in item.authoritative
    }
    direct_ids = {
        item.observed.service.id
        for item in candidates
        if item.authoritative & {"context", "code"}
    }
    return bool(component_ids and direct_ids - component_ids)


def _selected_candidates(
    passing: tuple[_Candidate, ...], conflict: bool
) -> tuple[_Candidate, ...]:
    if conflict or not passing:
        return ()
    used: set[str] = set()
    for candidate in passing:
        if not candidate.explicit_tokens or used & candidate.explicit_tokens:
            return ()
        used.update(candidate.explicit_tokens)
    return passing


def _existing_children(
    frontmatter: Mapping[str, object], identifier: str
) -> tuple[str, ...]:
    routing = frontmatter.get("routing")
    if not isinstance(routing, dict):
        return ()
    values = [*routing.get("children", []), *routing.get("retained_children", [])]
    if not all(isinstance(item, str) for item in values):
        raise AidtRoutingFailure("route_collision", f"card:{identifier}")
    return tuple(sorted(set(values), key=str.casefold))


def _project_resolution(
    frontmatter: Mapping[str, object],
    source: Mapping[str, Any],
    settings: RoutingSettings,
    catalog: CatalogObservation,
    candidates: tuple[_Candidate, ...],
    selected: tuple[_Candidate, ...],
    old_children: tuple[str, ...],
    now: Callable[[], datetime],
) -> RouteResolution:
    routing = _coordinator_routing(
        source, settings, catalog, candidates, selected, old_children, frontmatter, now
    )
    routed = bool(selected) and not routing["retained_children"]
    coordinator = _coordinator_projection(source, settings, routing, routed)
    children = tuple(_child_projection(source, settings, routing, item) for item in selected)
    desired = {item.identifier for item in children}
    retained = tuple(
        _stale_projection(source, settings, routing, child_id)
        for child_id in old_children
        if child_id not in desired
    )
    return RouteResolution(coordinator, children, retained, routed)


def _coordinator_routing(
    source: Mapping[str, Any],
    settings: RoutingSettings,
    catalog: CatalogObservation,
    candidates: tuple[_Candidate, ...],
    selected: tuple[_Candidate, ...],
    old_children: tuple[str, ...],
    frontmatter: Mapping[str, object],
    now: Callable[[], datetime],
) -> dict[str, Any]:
    desired = [f"{source['key']}--{item.observed.service.id}" for item in selected]
    retained = sorted(set(old_children) - set(desired), key=str.casefold)
    semantic = _semantic_decision(candidates, selected, retained)
    revisions = _service_map(catalog.services, "checkout_revision")
    bindings = _service_map(catalog.services, "repository_binding_digest")
    refs = _service_map(catalog.services, "revision_ref")
    fingerprint = _route_fingerprint(source, settings, catalog, semantic, revisions, refs)
    binding_digest = canonical_fingerprint("aidt-route-binding-v1", bindings)
    existing = frontmatter.get("routing")
    decided_at = _decision_time(existing, fingerprint, binding_digest, now)
    return _routing_payload(
        source,
        settings,
        candidates,
        selected,
        revisions,
        bindings,
        refs,
        desired,
        retained,
        fingerprint,
        binding_digest,
        decided_at,
    )


def _semantic_decision(
    candidates: tuple[_Candidate, ...],
    selected: tuple[_Candidate, ...],
    retained: list[str],
) -> dict[str, Any]:
    return {
        "decision": "routed" if selected and not retained else "review",
        "services": [item.observed.service.id for item in selected],
        "retained_children": retained,
        "candidates": [
            {"service": item.observed.service.id, "confidence": item.confidence}
            for item in candidates
            if item.evidence
        ],
    }


def _service_map(
    services: Iterable[ObservedService], attribute: str
) -> dict[str, str]:
    return {
        item.service.id: str(getattr(item, attribute))
        for item in sorted(services, key=lambda value: value.service.id)
    }


def _route_fingerprint(
    source: Mapping[str, Any],
    settings: RoutingSettings,
    catalog: CatalogObservation,
    semantic: Mapping[str, Any],
    revisions: Mapping[str, str],
    refs: Mapping[str, str],
) -> str:
    return canonical_fingerprint(
        ROUTE_SCHEMA,
        {
            "source_revision": source["revision"],
            "catalog_revision": settings.catalog_revision,
            "trust_schema": catalog.trust_schema,
            "revision_refs": refs,
            "checkout_revisions": revisions,
            "decision": semantic,
        },
    )


def _decision_time(
    existing: object,
    fingerprint: str,
    binding_digest: str,
    now: Callable[[], datetime],
) -> str:
    if isinstance(existing, dict):
        same_schema = existing.get("schema") == ROUTE_SCHEMA
        same = same_schema and existing.get("fingerprint") == fingerprint
        same_binding = existing.get("repository_binding_digest") == binding_digest
        current = existing.get("decided_at")
        if same and same_binding and isinstance(current, str):
            return current
    instant = now()
    if instant.tzinfo is None:
        raise AidtRoutingFailure("internal_error")
    normalized = instant.astimezone(timezone.utc).isoformat(timespec="seconds")
    return normalized.replace("+00:00", "Z")


def _routing_payload(
    source: Mapping[str, Any],
    settings: RoutingSettings,
    candidates: tuple[_Candidate, ...],
    selected: tuple[_Candidate, ...],
    revisions: dict[str, str],
    bindings: dict[str, str],
    refs: dict[str, str],
    desired: list[str],
    retained: list[str],
    fingerprint: str,
    binding_digest: str,
    decided_at: str,
) -> dict[str, Any]:
    primary = selected[0] if len(selected) == 1 else None
    evidence = _combined_evidence(candidates)
    return {
        "schema": ROUTE_SCHEMA,
        "role": "coordinator",
        "status": (
            "pending_fresh_base_equality" if selected and not retained else "review"
        ),
        "fingerprint": fingerprint,
        "repository_binding_digest": binding_digest,
        "source_revision": source["revision"],
        "catalog_revision": settings.catalog_revision,
        "checkout_refs": refs,
        "checkout_revisions": revisions,
        "repository_bindings": bindings,
        "service": primary.observed.service.id if primary else None,
        "kind": primary.observed.service.kind if primary else None,
        "checkout": primary.observed.service.checkout if primary else None,
        "checkout_ref": primary.observed.revision_ref if primary else None,
        "checkout_revision": primary.observed.checkout_revision if primary else None,
        "branch_prefix": _branch_prefix(primary.observed.service, source) if primary else None,
        "confidence": primary.confidence if primary else 0,
        "evidence": evidence,
        "candidates": _candidate_summaries(candidates),
        "supporting_services": _supporting_services(candidates, selected),
        "children": desired,
        "retained_children": retained,
        "recheck_requirements": _recheck_requirements(
            candidates, selected, settings, retained
        ),
        "decided_at": decided_at,
    }


def _combined_evidence(candidates: tuple[_Candidate, ...]) -> list[dict[str, str]]:
    evidence = [item for candidate in candidates for item in candidate.evidence]
    return [item.as_dict() for item in sorted(evidence, key=_evidence_key)[:MAX_EVIDENCE_RECORDS]]


def _candidate_summaries(candidates: tuple[_Candidate, ...]) -> list[dict[str, object]]:
    return [
        {"service": item.observed.service.id, "confidence": item.confidence}
        for item in candidates
        if item.evidence
    ]


def _recheck_requirements(
    candidates: tuple[_Candidate, ...],
    selected: tuple[_Candidate, ...],
    settings: RoutingSettings,
    retained: list[str],
) -> list[str]:
    if retained:
        return ["retained_children"]
    if selected:
        return ["fresh_base_equality"]
    if _component_conflict(candidates):
        return ["component_conflict"]
    passing = _passing(candidates, settings.minimum_confidence)
    if len(passing) > 1:
        return ["disjoint_explicit_change_anchors"]
    return ["authoritative route evidence"]


def _supporting_services(
    candidates: tuple[_Candidate, ...], selected: tuple[_Candidate, ...]
) -> list[str]:
    selected_ids = {item.observed.service.id for item in selected}
    return sorted(
        item.observed.service.id
        for item in candidates
        if item.evidence and item.observed.service.id not in selected_ids
    )


def _branch_prefix(service: RoutingService, source: Mapping[str, Any]) -> str:
    family = "fix" if source["issue_type"].casefold() == "bug" else "feat"
    prefix = f"{family}/{source['key']}"
    return f"csk-{prefix}" if service.kind == "frontend" else prefix


def _owned_states(settings: RoutingSettings) -> tuple[str, ...]:
    return (settings.ready_state, settings.review_state, settings.coordinator_state)


def _coordinator_projection(
    source: Mapping[str, Any],
    settings: RoutingSettings,
    routing: dict[str, Any],
    routed: bool,
) -> RouteCardProjection:
    desired_state = settings.review_state
    if routed:
        desired_state = settings.coordinator_state if len(routing["children"]) > 1 else settings.ready_state
    return RouteCardProjection(
        str(source["key"]),
        str(source["key"]),
        None,
        "coordinator",
        routing,
        None,
        desired_state,
        _owned_states(settings),
        str(source["revision"]),
        _route_marker(routing["status"], routing["children"]),
    )


def _child_projection(
    source: Mapping[str, Any],
    settings: RoutingSettings,
    coordinator: Mapping[str, Any],
    candidate: _Candidate,
) -> RouteCardProjection:
    service = candidate.observed.service
    identifier = f"{source['key']}--{service.id}"
    routing = _child_routing(source, settings, coordinator, candidate)
    return RouteCardProjection(
        identifier,
        str(source["key"]),
        service.id,
        "child",
        routing,
        _child_source(str(source["key"]), service.id),
        settings.ready_state,
        _owned_states(settings),
        None,
        _route_marker("pending_fresh_base_equality", [service.id]),
    )


def _child_routing(
    source: Mapping[str, Any],
    settings: RoutingSettings,
    coordinator: Mapping[str, Any],
    candidate: _Candidate,
) -> dict[str, Any]:
    observed = candidate.observed
    service = observed.service
    return {
        "schema": ROUTE_SCHEMA,
        "role": "child",
        "status": "pending_fresh_base_equality",
        "fingerprint": coordinator["fingerprint"],
        "coordinator_fingerprint": coordinator["fingerprint"],
        "repository_binding_digest": observed.repository_binding_digest,
        "coordinator": source["key"],
        "service": service.id,
        "kind": service.kind,
        "checkout": service.checkout,
        "checkout_ref": observed.revision_ref,
        "checkout_revision": observed.checkout_revision,
        "source_revision": source["revision"],
        "catalog_revision": settings.catalog_revision,
        "branch_prefix": _branch_prefix(service, source),
        "confidence": candidate.confidence,
        "evidence": [item.as_dict() for item in candidate.evidence],
        "recheck_requirements": ["fresh_base_equality"],
    }


def _stale_projection(
    source: Mapping[str, Any],
    settings: RoutingSettings,
    coordinator: Mapping[str, Any],
    child_id: str,
) -> RouteCardProjection:
    prefix = f"{source['key']}--"
    if not child_id.startswith(prefix) or not child_id.removeprefix(prefix):
        raise AidtRoutingFailure("route_collision", f"card:{source['key']}")
    service = child_id.removeprefix(prefix)
    routing = {
        "schema": ROUTE_SCHEMA,
        "role": "child",
        "status": "stale",
        "fingerprint": coordinator["fingerprint"],
        "coordinator_fingerprint": coordinator["fingerprint"],
        "coordinator": source["key"],
        "service": service,
        "source_revision": source["revision"],
        "catalog_revision": settings.catalog_revision,
        "recheck_requirements": ["reroute"],
    }
    return RouteCardProjection(
        child_id,
        str(source["key"]),
        service,
        "child",
        routing,
        _child_source(str(source["key"]), service),
        None,
        _owned_states(settings),
        None,
        _route_marker("stale", [service]),
    )


def _child_source(coordinator: str, service: str) -> dict[str, str]:
    return {
        "kind": "aidt-route-child",
        "key": f"{coordinator}::{service}",
        "coordinator": coordinator,
        "service": service,
    }


def _route_marker(status: str, services: Iterable[str]) -> str:
    values = ", ".join(sorted(services)) or "human review required"
    return (
        "<!-- symphony:aidt-route:start -->\n"
        f"Route status: {status}\n"
        f"Services: {values}\n"
        "<!-- symphony:aidt-route:end -->"
    )
