"""Worker-side dispatch attestation for provisionable AIDT route children."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from symphony.aidt_routing import (
    AidtRouteDispatchContract,
    AidtRoutingFailure,
    AidtRoutingResult,
    filter_routing_candidates,
    load_route_dispatch_contract,
)
from symphony.aidt_routing.contract import load_routing_settings
from symphony.aidt_routing.decision import (
    RouteCardProjection,
    RouteResolution,
    resolve_card,
)
from symphony.aidt_routing.runtime import _success_result
from symphony.issue import Issue
from symphony.jira_intake import build_source_snapshot
from symphony.trackers.aidt_routes import apply_route_resolutions
from symphony.trackers.file import (
    FileBoardTracker,
    parse_ticket_file,
    write_ticket_atomic,
)
from symphony.trackers.jira import JiraInboxIssue
from symphony.workflow import ServiceConfig

from .aidt_routing_support import (
    catalog_observation,
    routing_config,
    service_config,
    service_definition,
)


def _issue(identifier: str) -> Issue:
    return Issue(identifier, identifier, identifier, "", 1, "Ready")


def test_dispatch_facade_is_lazy_and_does_not_pull_git_observer() -> None:
    script = """
import sys
import symphony.aidt_routing
assert "symphony.aidt_routing.dispatch" not in sys.modules
assert "symphony.aidt_routing.git_objects" not in sys.modules
from symphony.aidt_routing import AidtRouteDispatchContract
assert "symphony.aidt_routing.dispatch" in sys.modules
assert "symphony.aidt_routing.git_objects" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def _source(issue_type: str = "Bug") -> dict[str, object]:
    return build_source_snapshot(
        JiraInboxIssue(
            key="A20-1188",
            summary="GET /v-api/learning routeSymbol",
            description="viewer-api math learning center change",
            issue_type=issue_type,
            components=("viewer-api",),
            status="Ready",
            priority="High",
            updated="2026-07-20T00:00:00Z",
            url="https://example.atlassian.net/browse/A20-1188",
        )
    )


def _routed_board(
    tmp_path: Path,
    *,
    issue_type: str = "Bug",
    kind: str = "backend",
) -> tuple[ServiceConfig, FileBoardTracker]:
    aidt_root = tmp_path / "aidt"
    aidt_root.mkdir()
    service = service_definition(
        routes=[
            {
                "id": "learning-route",
                "file": "src/Route.java",
                "method": "GET",
                "endpoint": "/v-api/learning",
                "symbols": ["routeSymbol"],
            }
        ],
        domains=[
            {
                "id": "learning-domain",
                "file": "src/Domain.java",
                "terms": ["math learning center"],
            }
        ],
    )
    service["kind"] = kind
    config = service_config(
        tmp_path / "board",
        routing_config(aidt_root, [service]),
    )
    settings = load_routing_settings(config)
    assert settings is not None
    catalog = catalog_observation(
        settings,
        contents_by_service={
            "viewer-api": {
                "src/Route.java": "GET /v-api/learning routeSymbol",
                "src/Domain.java": "math learning center",
            }
        },
        revisions={"viewer-api": "a" * 40},
        bindings={"viewer-api": "b" * 64},
    )
    source = _source(issue_type)
    resolution = resolve_card(
        {"id": "A20-1188", "identifier": "A20-1188", "source": source},
        settings,
        catalog,
        now=lambda: datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
    )
    board = FileBoardTracker(config.tracker)
    write_ticket_atomic(
        board.board_root / "A20-1188.md",
        {
            "id": "A20-1188",
            "identifier": "A20-1188",
            "title": "coordinator",
            "state": "Ready",
            "source": source,
        },
        "coordinator notes",
    )
    apply_route_resolutions(board, [resolution])
    return config, board


def test_result_and_filter_release_only_nominated_children_in_order() -> None:
    blocked = frozenset({"A20-1", "A20-1--viewer-api", "A20-2--lms-api"})
    result = AidtRoutingResult(
        True,
        True,
        blocked,
        1,
        0,
        2,
        0,
        "success",
        provisionable_child_identifiers=frozenset({"A20-1--viewer-api"}),
    )
    candidates = [
        _issue("UNMANAGED-1"),
        _issue("A20-1"),
        _issue("A20-1--viewer-api"),
        _issue("A20-2--lms-api"),
    ]

    filtered = filter_routing_candidates(
        candidates,
        result.blocked_identifiers,
        result.provisionable_child_identifiers,
    )

    assert [issue.identifier for issue in filtered] == [
        "UNMANAGED-1",
        "A20-1--viewer-api",
    ]


def test_success_result_nominates_only_validated_routed_children() -> None:
    coordinator = RouteCardProjection(
        "A20-1",
        "A20-1",
        None,
        "coordinator",
        {},
        None,
        "Ready",
        ("Ready",),
        "a" * 64,
        "marker",
    )
    child = RouteCardProjection(
        "A20-1--viewer-api",
        "A20-1",
        "viewer-api",
        "child",
        {
            "schema": "aidt-route-object-v2",
            "role": "child",
            "status": "pending_fresh_base_equality",
        },
        {},
        "Ready",
        ("Ready",),
        None,
        "marker",
    )
    resolution = RouteResolution(coordinator, (child,), (), True)

    result = _success_result(
        (resolution,),
        set(resolution.blocked_identifiers),
    )

    assert result.provisionable_child_identifiers == frozenset(
        {"A20-1--viewer-api"}
    )


@pytest.mark.parametrize(
    "provisionable",
    [
        frozenset({"A20-1"}),
        frozenset({"A20-1--viewer-api"}),
        {"A20-1--viewer-api"},
    ],
)
def test_invalid_provisionable_sets_normalize_atomically(
    provisionable: object,
) -> None:
    result = AidtRoutingResult(
        True,
        True,
        frozenset({"A20-1"}),
        1,
        0,
        0,
        0,
        "success",
        provisionable_child_identifiers=provisionable,  # type: ignore[arg-type]
    )

    assert result.status == "failure"
    assert result.global_allow_dispatch is False
    assert result.provisionable_child_identifiers == frozenset()


def test_loader_attests_atomic_route_pair_and_change_kind(tmp_path: Path) -> None:
    config, _board = _routed_board(tmp_path)

    contract = load_route_dispatch_contract(
        config,
        "A20-1188--viewer-api",
    )

    assert isinstance(contract, AidtRouteDispatchContract)
    assert contract.identifier == "A20-1188--viewer-api"
    assert contract.coordinator == "A20-1188"
    assert contract.service == "viewer-api"
    assert contract.issue_type == "bug"
    assert contract.change_kind == "fix"
    assert contract.branch == "fix/A20-1188"
    assert contract.checkout_ref == "refs/remotes/origin/aidt-prd"
    assert contract.checkout_revision == "a" * 40
    assert contract.repository_binding_digest == "b" * 64
    assert len(contract.route_pair_digest) == 64


def test_route_pair_digest_ignores_non_route_body_changes(tmp_path: Path) -> None:
    config, board = _routed_board(tmp_path, issue_type=" Story ", kind="frontend")
    before = load_route_dispatch_contract(config, "A20-1188--viewer-api")
    path = board.board_root / "A20-1188.md"
    front, _body = parse_ticket_file(path)
    write_ticket_atomic(path, front, "new operator notes")

    after = load_route_dispatch_contract(config, "A20-1188--viewer-api")

    assert before is not None and after is not None
    assert after.route_pair_digest == before.route_pair_digest
    assert after.issue_type == "story"
    assert after.change_kind == "feat"
    assert after.branch == "csk-feat/A20-1188"


@pytest.mark.parametrize(
    "drift_seam",
    [
        "after_first_coordinator",
        "after_first_child",
        "after_first_pair",
        "after_second_coordinator",
        "after_second_child",
    ],
)
def test_pair_wide_reread_rejects_coordinator_change(
    tmp_path: Path, drift_seam: str
) -> None:
    config, board = _routed_board(tmp_path)
    changed = False

    def drift(seam: str) -> None:
        nonlocal changed
        if seam != drift_seam or changed:
            return
        changed = True
        path = board.board_root / "A20-1188.md"
        front, body = parse_ticket_file(path)
        routing = dict(front["routing"])
        routing["children"] = []
        front["routing"] = routing
        write_ticket_atomic(path, front, body)

    with pytest.raises(AidtRoutingFailure):
        load_route_dispatch_contract(
            config,
            "A20-1188--viewer-api",
            pair_read_hook=drift,
        )


def test_loader_returns_none_only_for_unmanaged_card(tmp_path: Path) -> None:
    config, board = _routed_board(tmp_path)
    write_ticket_atomic(
        board.board_root / "UNMANAGED-1.md",
        {
            "id": "UNMANAGED-1",
            "identifier": "UNMANAGED-1",
            "title": "ordinary card",
            "state": "Ready",
        },
        "ordinary",
    )
    child = board.board_root / "A20-1188--viewer-api.md"
    child_front, child_body = parse_ticket_file(child)
    child_front["state"] = "Human Review"
    write_ticket_atomic(child, child_front, child_body)

    assert load_route_dispatch_contract(config, "UNMANAGED-1") is None
    with pytest.raises(AidtRoutingFailure):
        load_route_dispatch_contract(config, "A20-1188--viewer-api")
