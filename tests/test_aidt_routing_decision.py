"""Pure decision contract for object-backed AIDT routes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from symphony.aidt_routing.contract import canonical_fingerprint, load_routing_settings
from symphony.aidt_routing.decision import resolve_card
from symphony.jira_intake import build_source_snapshot
from symphony.trackers.jira import JiraInboxIssue

from .aidt_routing_support import (
    catalog_observation,
    routing_config,
    service_config,
    service_definition,
)


def _source() -> dict[str, object]:
    return build_source_snapshot(
        JiraInboxIssue(
            key="A20-1188",
            summary="backend GET /v-api/ailearning/{aiLrnNo}",
            description="math learning center",
            issue_type="Bug",
            components=("viewer-api",),
            status="Ready",
            priority="High",
            updated="2026-07-20T00:00:00Z",
            url="https://example.atlassian.net/browse/A20-1188",
        )
    )


def _revised(source: dict[str, object]) -> dict[str, object]:
    updated = dict(source)
    semantic = {key: value for key, value in updated.items() if key != "revision"}
    updated["revision"] = canonical_fingerprint(str(updated["schema"]), semantic)
    return updated


def _settings(tmp_path: Path):
    viewer = service_definition(
        aliases=["viewer-api"],
        routes=[
            {
                "id": "learning-route",
                "file": "src/Controller.java",
                "method": "GET",
                "endpoint": "/v-api/ailearning/{aiLrnNo}",
                "symbols": ["getMathAILearningCenter"],
            }
        ],
        domains=[
            {
                "id": "math-domain",
                "file": "src/Service.java",
                "terms": ["math learning center"],
            }
        ],
    )
    lms = service_definition("lms-api", aliases=["lms-api"])
    config = routing_config(tmp_path / "aidt", [viewer, lms])
    settings = load_routing_settings(service_config(tmp_path / "board", config))
    assert settings is not None
    return settings


def test_a20_1188_routes_only_viewer_api_at_95_with_object_handoff(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    catalog = catalog_observation(
        settings,
        contents_by_service={
            "viewer-api": {
                "pom.xml": "<project />",
                "src/Controller.java": (
                    "GET /v-api/ailearning/{aiLrnNo} "
                    "getMathAILearningCenter"
                ),
                "src/Service.java": "math learning center",
            },
            "lms-api": {"pom.xml": "<project />"},
        },
        revisions={"viewer-api": "a" * 40, "lms-api": "b" * 40},
    )
    fixed_now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    resolution = resolve_card(
        {"id": "A20-1188", "identifier": "A20-1188", "source": _source()},
        settings,
        catalog,
        now=lambda: fixed_now,
    )

    coordinator = resolution.coordinator.routing
    assert resolution.routed is True
    assert coordinator["schema"] == "aidt-route-object-v2"
    assert coordinator["status"] == "pending_fresh_base_equality"
    assert coordinator["service"] == "viewer-api"
    assert coordinator["confidence"] == 95
    assert coordinator["children"] == ["A20-1188--viewer-api"]
    assert coordinator["checkout_revisions"] == {
        "lms-api": "b" * 40,
        "viewer-api": "a" * 40,
    }
    assert set(coordinator["repository_bindings"]) == {"lms-api", "viewer-api"}
    assert resolution.coordinator.routing["decided_at"] == "2026-07-20T12:00:00Z"
    assert [child.service for child in resolution.children] == ["viewer-api"]
    child = resolution.children[0].routing
    assert child["checkout_revision"] == "a" * 40
    assert child["service"] == "viewer-api"
    assert "checkout_revisions" not in child


def test_authoritative_categories_score_once_when_source_repeats_contract(
    tmp_path: Path,
) -> None:
    service = service_definition(
        aliases=["viewer-api"],
        context=[
            {
                "id": "base-path",
                "file": "src/Controller.java",
                "literal": "/v-api/ailearning",
            }
        ],
        routes=[
            {
                "id": "route-one",
                "file": "src/Controller.java",
                "method": "GET",
                "endpoint": "/v-api/ailearning/{aiLrnNo}",
                "symbols": ["getMathAILearningCenter"],
            },
            {
                "id": "route-two",
                "file": "src/Controller.java",
                "method": "GET",
                "endpoint": "/v-api/ailearning/{aiLrnNo}",
                "symbols": ["getMathAILearningCenter"],
            },
        ],
    )
    raw = routing_config(tmp_path / "aidt", [service])
    settings = load_routing_settings(service_config(tmp_path / "board", raw))
    assert settings is not None
    catalog = catalog_observation(
        settings,
        contents_by_service={
            "viewer-api": {
                "pom.xml": "x",
                "src/Controller.java": (
                    "GET /v-api/ailearning/{aiLrnNo} "
                    "getMathAILearningCenter"
                ),
            }
        },
    )
    source = _source()
    source["description"] = (
        "/v-api/ailearning /v-api/ailearning "
        "GET /v-api/ailearning/{aiLrnNo}"
    )
    source = _revised(source)

    result = resolve_card(
        {"id": "A20-1188", "identifier": "A20-1188", "source": source},
        settings,
        catalog,
        now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    categories = [item["category"] for item in result.coordinator.routing["evidence"]]
    assert categories.count("component") == 1
    assert categories.count("context") == 1
    assert categories.count("code") == 1
    assert result.coordinator.routing["confidence"] == 100


def test_keyword_parent_only_and_hostile_body_remain_human_review(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    catalog = catalog_observation(
        settings,
        contents_by_service={
            "viewer-api": {
                "pom.xml": "x",
                "src/Controller.java": (
                    "GET /v-api/ailearning/{aiLrnNo} "
                    "getMathAILearningCenter"
                ),
                "src/Service.java": "math learning center",
            },
            "lms-api": {"pom.xml": "x"},
        },
    )
    source = _source()
    source["summary"] = "viewer-api keyword only"
    source["description"] = "unrelated"
    source["components"] = []
    source["parent"] = {
        "key": "A20-1186",
        "summary": "GET /v-api/ailearning/{aiLrnNo}",
        "description": "consumer contract",
        "components": [],
    }
    source = _revised(source)

    resolution = resolve_card(
        {
            "id": "A20-1188",
            "identifier": "A20-1188",
            "source": source,
            "body": "viewer-api GET /v-api/ailearning/{aiLrnNo}",
        },
        settings,
        catalog,
        now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    routing = resolution.coordinator.routing
    assert resolution.routed is False
    assert routing["status"] == "review"
    assert routing["children"] == []
    assert routing["confidence"] == 0
    assert [item["category"] for item in routing["evidence"]] == [
        "code",
        "parent",
        "supporting",
    ]


def test_structured_component_conflicting_with_unique_code_owner_is_review(
    tmp_path: Path,
) -> None:
    viewer = service_definition(
        "viewer-api",
        aliases=["viewer-api"],
        context=[
            {
                "id": "viewer-context",
                "file": "Viewer.java",
                "literal": "/v-api/owned",
            }
        ],
        routes=[
            {
                "id": "viewer-route",
                "file": "Viewer.java",
                "method": "GET",
                "endpoint": "/v-api/owned",
                "symbols": ["ownedByViewer"],
            }
        ],
    )
    lms = service_definition("lms-api", aliases=["lms-api"])
    raw = routing_config(tmp_path / "aidt", [viewer, lms])
    settings = load_routing_settings(service_config(tmp_path / "board", raw))
    assert settings is not None
    catalog = catalog_observation(
        settings,
        contents_by_service={
            "viewer-api": {
                "pom.xml": "x",
                "Viewer.java": "GET /v-api/owned ownedByViewer",
            },
            "lms-api": {"pom.xml": "x"},
        },
    )
    source = _source()
    source["summary"] = "GET /v-api/owned"
    source["description"] = "/v-api/owned"
    source["components"] = ["lms-api"]
    source = _revised(source)

    result = resolve_card(
        {"id": "A20-1188", "identifier": "A20-1188", "source": source},
        settings,
        catalog,
        now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    assert result.routed is False
    assert result.coordinator.routing["status"] == "review"
    assert result.coordinator.routing["children"] == []
    assert result.coordinator.routing["recheck_requirements"] == [
        "component_conflict"
    ]
    assert result.coordinator.routing["candidates"] == [
        {"service": "lms-api", "confidence": 45},
        {"service": "viewer-api", "confidence": 65},
    ]


def _multi_route(
    tmp_path: Path,
    *,
    shared_endpoint: bool,
):
    endpoints = {
        "viewer-api": "/shared" if shared_endpoint else "/viewer/change",
        "lms-api": "/shared" if shared_endpoint else "/lms/change",
    }
    services = []
    contents: dict[str, dict[str, str]] = {}
    for service_id in ("viewer-api", "lms-api"):
        symbol = f"change{service_id.replace('-', '').title()}"
        domain_term = f"{service_id} domain"
        endpoint = endpoints[service_id]
        services.append(
            service_definition(
                service_id,
                aliases=[service_id],
                routes=[
                    {
                        "id": f"{service_id}-route",
                        "file": "Route.java",
                        "method": "GET",
                        "endpoint": endpoint,
                        "symbols": [symbol],
                    }
                ],
                domains=[
                    {
                        "id": f"{service_id}-domain",
                        "file": "Domain.java",
                        "terms": [domain_term],
                    }
                ],
            )
        )
        contents[service_id] = {
            "pom.xml": "x",
            "Route.java": f"GET {endpoint} {symbol}",
            "Domain.java": domain_term,
        }
    raw = routing_config(tmp_path / "aidt", services)
    settings = load_routing_settings(service_config(tmp_path / "board", raw))
    assert settings is not None
    catalog = catalog_observation(settings, contents_by_service=contents)
    source = _source()
    source["components"] = ["viewer-api", "lms-api"]
    source["summary"] = " ".join(
        f"GET {endpoint}" for endpoint in sorted(set(endpoints.values()))
    )
    source["description"] = "viewer-api domain lms-api domain"
    source = _revised(source)
    return resolve_card(
        {"id": "A20-1188", "identifier": "A20-1188", "source": source},
        settings,
        catalog,
        now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )


def test_multi_service_route_requires_disjoint_explicit_change_anchors(
    tmp_path: Path,
) -> None:
    tied = _multi_route(tmp_path / "tied", shared_endpoint=True)
    disjoint = _multi_route(tmp_path / "disjoint", shared_endpoint=False)

    assert tied.routed is False
    assert tied.coordinator.routing["status"] == "review"
    assert tied.children == ()
    assert tied.coordinator.routing["recheck_requirements"] == [
        "disjoint_explicit_change_anchors"
    ]
    assert disjoint.routed is True
    assert disjoint.coordinator.routing["status"] == "pending_fresh_base_equality"
    assert [item.identifier for item in disjoint.children] == [
        "A20-1188--lms-api",
        "A20-1188--viewer-api",
    ]
    assert all(item.routing["confidence"] == 95 for item in disjoint.children)


def test_equal_semantics_preserve_time_while_source_or_binding_change_recomputes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    contents = {
        "viewer-api": {
            "pom.xml": "x",
            "src/Controller.java": (
                "GET /v-api/ailearning/{aiLrnNo} getMathAILearningCenter"
            ),
            "src/Service.java": "math learning center",
        },
        "lms-api": {"pom.xml": "x"},
    }
    first_catalog = catalog_observation(settings, contents_by_service=contents)
    source = _source()
    first = resolve_card(
        {"id": "A20-1188", "identifier": "A20-1188", "source": source},
        settings,
        first_catalog,
        now=lambda: datetime(2026, 7, 20, 1, tzinfo=timezone.utc),
    )
    existing = {
        "id": "A20-1188",
        "identifier": "A20-1188",
        "source": source,
        "routing": first.coordinator.routing,
    }

    equal = resolve_card(
        existing,
        settings,
        first_catalog,
        now=lambda: datetime(2026, 7, 21, 1, tzinfo=timezone.utc),
    )
    rebound_catalog = catalog_observation(
        settings,
        contents_by_service=contents,
        bindings={"viewer-api": "new-viewer-binding", "lms-api": "new-lms-binding"},
    )
    rebound = resolve_card(
        existing,
        settings,
        rebound_catalog,
        now=lambda: datetime(2026, 7, 21, 1, tzinfo=timezone.utc),
    )
    stale_schema = resolve_card(
        {
            **existing,
            "routing": {**first.coordinator.routing, "schema": "aidt-route-v1"},
        },
        settings,
        first_catalog,
        now=lambda: datetime(2026, 7, 23, 1, tzinfo=timezone.utc),
    )
    changed_source = dict(source)
    changed_source["description"] = "math learning center changed"
    changed_source = _revised(changed_source)
    changed = resolve_card(
        {**existing, "source": changed_source},
        settings,
        first_catalog,
        now=lambda: datetime(2026, 7, 22, 1, tzinfo=timezone.utc),
    )

    assert equal.coordinator.routing == first.coordinator.routing
    assert rebound.coordinator.routing["fingerprint"] == first.coordinator.routing["fingerprint"]
    assert rebound.coordinator.routing["repository_binding_digest"] != (
        first.coordinator.routing["repository_binding_digest"]
    )
    assert rebound.coordinator.routing["decided_at"] == "2026-07-21T01:00:00Z"
    assert changed.coordinator.routing["fingerprint"] != first.coordinator.routing["fingerprint"]
    assert changed.coordinator.routing["decided_at"] == "2026-07-22T01:00:00Z"
    assert stale_schema.coordinator.routing["decided_at"] == "2026-07-23T01:00:00Z"


def test_kind_and_keyword_support_are_capped_once_and_cannot_replace_authority(
    tmp_path: Path,
) -> None:
    viewer = service_definition(
        aliases=["viewer-api"],
        routes=[
            {
                "id": "viewer-route",
                "file": "Viewer.java",
                "method": "GET",
                "endpoint": "/v-api/owned",
                "symbols": ["ownedByViewer"],
            }
        ],
    )
    raw = routing_config(tmp_path / "aidt", [viewer])
    settings = load_routing_settings(service_config(tmp_path / "board", raw))
    assert settings is not None
    catalog = catalog_observation(
        settings,
        contents_by_service={
            "viewer-api": {
                "pom.xml": "x",
                "Viewer.java": "GET /v-api/owned ownedByViewer",
            }
        },
    )
    source = _source()
    source["summary"] = "backend viewer-api viewer-api GET /v-api/owned"
    source["description"] = "backend viewer-api"
    source = _revised(source)

    routed = resolve_card(
        {"id": "A20-1188", "identifier": "A20-1188", "source": source},
        settings,
        catalog,
        now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    categories = [item["category"] for item in routed.coordinator.routing["evidence"]]
    assert routed.coordinator.routing["confidence"] == 90
    assert categories.count("kind") == 1
    assert categories.count("supporting") == 1


def test_existing_unselected_child_is_retained_stale_and_forces_review(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    catalog = catalog_observation(
        settings,
        contents_by_service={
            "viewer-api": {
                "pom.xml": "x",
                "src/Controller.java": (
                    "GET /v-api/ailearning/{aiLrnNo} getMathAILearningCenter"
                ),
                "src/Service.java": "math learning center",
            },
            "lms-api": {"pom.xml": "x"},
        },
    )
    existing = {
        "schema": "aidt-route-object-v2",
        "children": ["A20-1188--viewer-api", "A20-1188--lms-api"],
        "retained_children": [],
    }

    resolution = resolve_card(
        {
            "id": "A20-1188",
            "identifier": "A20-1188",
            "source": _source(),
            "routing": existing,
        },
        settings,
        catalog,
        now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    routing = resolution.coordinator.routing
    assert resolution.routed is False
    assert routing["status"] == "review"
    assert routing["children"] == ["A20-1188--viewer-api"]
    assert routing["retained_children"] == ["A20-1188--lms-api"]
    assert routing["recheck_requirements"] == ["retained_children"]
    assert [item.identifier for item in resolution.children] == [
        "A20-1188--viewer-api"
    ]
    assert [item.routing["status"] for item in resolution.retained] == ["stale"]
    assert resolution.coordinator.desired_state == "Human Review"
