"""Whole-poll persistence contract for AIDT route projections."""

from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

import symphony.trackers.aidt_routes as storage_module
from symphony.aidt_routing.contract import MAX_CHILDREN, MAX_COORDINATORS, AidtRoutingFailure
from symphony.aidt_routing.decision import RouteCardProjection, RouteResolution
from symphony.trackers.aidt_routes import AidtPartialApplyError, apply_route_resolutions
from symphony.trackers.file import FileBoardTracker, parse_ticket_file, write_ticket_atomic

from .aidt_routing_support import service_config


ROUTE_START = "<!-- symphony:aidt-route:start -->"
ROUTE_END = "<!-- symphony:aidt-route:end -->"


def _marker(status: str) -> str:
    return f"{ROUTE_START}\nRoute status: {status}\n{ROUTE_END}"


def _resolution() -> RouteResolution:
    child = RouteCardProjection(
        identifier="A20-1188--viewer-api",
        coordinator="A20-1188",
        service="viewer-api",
        role="child",
        routing={
            "schema": "aidt-route-object-v2",
            "role": "child",
            "status": "pending_fresh_base_equality",
            "fingerprint": "route-fingerprint",
            "coordinator": "A20-1188",
            "service": "viewer-api",
        },
        source={
            "kind": "aidt-route-child",
            "key": "A20-1188::viewer-api",
            "coordinator": "A20-1188",
            "service": "viewer-api",
        },
        desired_state="Ready",
        route_owned_states=("Ready", "Human Review", "Coordinating"),
        expected_source_revision=None,
        marker=_marker("pending_fresh_base_equality"),
    )
    coordinator = RouteCardProjection(
        identifier="A20-1188",
        coordinator="A20-1188",
        service=None,
        role="coordinator",
        routing={
            "schema": "aidt-route-object-v2",
            "role": "coordinator",
            "status": "pending_fresh_base_equality",
            "fingerprint": "route-fingerprint",
            "children": ["A20-1188--viewer-api"],
            "retained_children": [],
        },
        source=None,
        desired_state="Ready",
        route_owned_states=("Ready", "Human Review", "Coordinating"),
        expected_source_revision="source-revision",
        marker=_marker("pending_fresh_base_equality"),
    )
    return RouteResolution(coordinator, (child,), (), True)


def _multi_resolution() -> RouteResolution:
    single = _resolution()
    second = replace(
        single.children[0],
        identifier="A20-1188--lms-api",
        service="lms-api",
        routing={
            **single.children[0].routing,
            "service": "lms-api",
        },
        source={
            "kind": "aidt-route-child",
            "key": "A20-1188::lms-api",
            "coordinator": "A20-1188",
            "service": "lms-api",
        },
    )
    coordinator = replace(
        single.coordinator,
        routing={
            **single.coordinator.routing,
            "children": ["A20-1188--lms-api", "A20-1188--viewer-api"],
        },
        desired_state="Coordinating",
    )
    return RouteResolution(coordinator, (second, *single.children), (), True)


def _board(tmp_path: Path) -> tuple[FileBoardTracker, Path]:
    board_root = tmp_path / "board"
    config = service_config(board_root, {})
    board = FileBoardTracker(config.tracker)
    coordinator = board_root / "A20-1188.md"
    write_ticket_atomic(
        coordinator,
        {
            "id": "A20-1188",
            "identifier": "A20-1188",
            "title": "local title",
            "state": "Ready",
            "priority": 2,
            "url": "local://route",
            "updated_at": "2026-01-02T00:00:00Z",
            "local_flag": "keep",
            "source": {
                "kind": "jira",
                "key": "A20-1188",
                "revision": "source-revision",
            },
        },
        "local preface\n\n<!-- symphony:jira-source:start -->\n> inert\n"
        "<!-- symphony:jira-source:end -->\n\nlocal notes",
    )
    return board, coordinator


def test_whole_poll_writes_children_before_coordinator_and_equal_poll_is_stable(
    tmp_path: Path,
) -> None:
    board, coordinator_path = _board(tmp_path)
    rename_order: list[str] = []

    def after_rename(identifier: str, index: int) -> None:
        rename_order.append(identifier)
        if index == 1:
            assert (board.board_root / "A20-1188--viewer-api.md").exists()
            assert "routing" not in parse_ticket_file(coordinator_path)[0]

    result = apply_route_resolutions(
        board,
        [_resolution()],
        rename_fault_hook=after_rename,
    )

    assert result.changed == 2
    assert rename_order == ["A20-1188--viewer-api", "A20-1188"]
    coordinator, body = parse_ticket_file(coordinator_path)
    for key, value in {
        "title": "local title",
        "priority": 2,
        "url": "local://route",
        "updated_at": "2026-01-02T00:00:00Z",
        "local_flag": "keep",
    }.items():
        assert coordinator[key] == value
    assert "local preface" in body and "local notes" in body

    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in board.board_root.glob("*.md")
    }
    second = apply_route_resolutions(board, [_resolution()])
    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in board.board_root.glob("*.md")
    }
    assert second.changed == 0
    assert after == before


@pytest.mark.parametrize("failure_index", [1, 2])
def test_failure_after_each_child_rename_is_partial_and_next_poll_repairs(
    tmp_path: Path,
    failure_index: int,
) -> None:
    board, coordinator_path = _board(tmp_path)
    resolution = _multi_resolution()

    def fail_after_child(_identifier: str, index: int) -> None:
        if index == failure_index:
            raise OSError("injected after rename")

    with pytest.raises(AidtPartialApplyError) as raised:
        apply_route_resolutions(
            board,
            [resolution],
            rename_fault_hook=fail_after_child,
        )

    assert raised.value.category == "partial_apply"
    assert "routing" not in parse_ticket_file(coordinator_path)[0]
    written_children = sorted(board.board_root.glob("A20-1188--*.md"))
    assert len(written_children) == failure_index
    retained = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in written_children
    }

    repaired = apply_route_resolutions(board, [resolution])

    assert repaired.changed == 3 - failure_index
    assert parse_ticket_file(coordinator_path)[0]["routing"]["fingerprint"] == (
        "route-fingerprint"
    )
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in written_children
    } == retained
    assert len(list(board.board_root.glob("A20-1188--*.md"))) == 2


def test_precommit_unmanaged_child_collision_writes_no_route_cards(
    tmp_path: Path,
) -> None:
    board, coordinator_path = _board(tmp_path)
    coordinator_before = coordinator_path.read_bytes()
    collision = board.board_root / "A20-1188--viewer-api.md"

    def create_unmanaged_collision() -> None:
        write_ticket_atomic(
            collision,
            {
                "id": "A20-1188--viewer-api",
                "identifier": "A20-1188--viewer-api",
                "title": "unmanaged local card",
                "state": "Ready",
            },
            "local collision",
        )

    with pytest.raises(AidtRoutingFailure) as raised:
        apply_route_resolutions(
            board,
            [_resolution()],
            precommit_hook=create_unmanaged_collision,
        )

    assert raised.value.category == "route_collision"
    assert coordinator_path.read_bytes() == coordinator_before
    assert parse_ticket_file(collision)[1] == "local collision"


def test_retained_child_is_never_deleted_or_reset_when_route_becomes_review(
    tmp_path: Path,
) -> None:
    board, _ = _board(tmp_path)
    routed = _resolution()
    apply_route_resolutions(board, [routed])
    child_path = board.board_root / "A20-1188--viewer-api.md"
    child, child_body = parse_ticket_file(child_path)
    child["state"] = "In Progress"
    child["local_flag"] = "keep child"
    write_ticket_atomic(child_path, child, child_body + "\n\nlocal child work")
    stale = replace(
        routed.children[0],
        routing={
            **routed.children[0].routing,
            "status": "stale",
            "recheck_requirements": ["reroute"],
        },
        desired_state=None,
        marker=_marker("stale"),
    )
    coordinator = replace(
        routed.coordinator,
        routing={
            **routed.coordinator.routing,
            "status": "review",
            "children": [],
            "retained_children": ["A20-1188--viewer-api"],
        },
        desired_state="Human Review",
        marker=_marker("review"),
    )
    review = RouteResolution(coordinator, (), (stale,), False)

    apply_route_resolutions(board, [review])

    retained, retained_body = parse_ticket_file(child_path)
    assert child_path.exists()
    assert retained["state"] == "In Progress"
    assert retained["local_flag"] == "keep child"
    assert retained["routing"]["status"] == "stale"
    assert "local child work" in retained_body


def _second_resolution() -> RouteResolution:
    first = _resolution()
    child = replace(
        first.children[0],
        identifier="A20-1190--lms-api",
        coordinator="A20-1190",
        service="lms-api",
        routing={
            **first.children[0].routing,
            "coordinator": "A20-1190",
            "service": "lms-api",
        },
        source={
            "kind": "aidt-route-child",
            "key": "A20-1190::lms-api",
            "coordinator": "A20-1190",
            "service": "lms-api",
        },
    )
    coordinator = replace(
        first.coordinator,
        identifier="A20-1190",
        coordinator="A20-1190",
        routing={
            **first.coordinator.routing,
            "children": ["A20-1190--lms-api"],
        },
        expected_source_revision="second-source-revision",
    )
    return RouteResolution(coordinator, (child,), (), True)


def test_whole_poll_holds_all_sorted_locks_then_globally_commits_children_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, _ = _board(tmp_path)
    second_path = board.board_root / "A20-1190.md"
    write_ticket_atomic(
        second_path,
        {
            "id": "A20-1190",
            "identifier": "A20-1190",
            "title": "second coordinator",
            "state": "Ready",
            "source": {
                "kind": "jira",
                "key": "A20-1190",
                "revision": "second-source-revision",
            },
        },
        "second local body",
    )
    entered: list[str] = []
    active: set[str] = set()

    @contextmanager
    def recording_lock(path: Path) -> Iterator[None]:
        identifier = path.stem
        entered.append(identifier)
        active.add(identifier)
        try:
            yield
        finally:
            active.remove(identifier)

    monkeypatch.setattr(storage_module, "_exclusive_lock", recording_lock)
    expected = [
        "A20-1188",
        "A20-1188--viewer-api",
        "A20-1190",
        "A20-1190--lms-api",
    ]

    def precommit() -> None:
        assert entered == expected
        assert active == set(expected)

    rename_order: list[str] = []
    apply_route_resolutions(
        board,
        [_second_resolution(), _resolution()],
        precommit_hook=precommit,
        rename_fault_hook=lambda identifier, _index: rename_order.append(identifier),
    )

    assert rename_order == [
        "A20-1188--viewer-api",
        "A20-1190--lms-api",
        "A20-1188",
        "A20-1190",
    ]


@pytest.mark.parametrize("collision", ["case_path", "reparent"])
def test_existing_child_case_path_and_reparent_collisions_are_zero_write(
    tmp_path: Path,
    collision: str,
) -> None:
    board, coordinator_path = _board(tmp_path)
    child_path = board.board_root / "A20-1188--viewer-api.md"
    source = {
        "kind": "aidt-route-child",
        "key": "A20-999::viewer-api" if collision == "reparent" else "A20-1188::viewer-api",
        "coordinator": "A20-999" if collision == "reparent" else "A20-1188",
        "service": "viewer-api",
    }
    identifier = "A20-1188--viewer-api"
    if collision == "case_path":
        child_path = board.board_root / "a20-1188--viewer-api.md"
    write_ticket_atomic(
        child_path,
        {
            "id": identifier,
            "identifier": identifier,
            "title": "existing child",
            "state": "Ready",
            "source": source,
        },
        "existing child body",
    )
    coordinator_before = coordinator_path.read_bytes()
    child_before = child_path.read_bytes()

    with pytest.raises(AidtRoutingFailure) as raised:
        apply_route_resolutions(board, [_resolution()])

    assert raised.value.category == "route_collision"
    assert coordinator_path.read_bytes() == coordinator_before
    assert child_path.read_bytes() == child_before


def test_source_revision_drift_before_first_rename_writes_nothing(
    tmp_path: Path,
) -> None:
    board, coordinator_path = _board(tmp_path)
    front, body = parse_ticket_file(coordinator_path)
    front["source"] = {
        **front["source"],
        "revision": "newer-source-revision",
    }
    write_ticket_atomic(coordinator_path, front, body + "\n\nconcurrent local note")
    before = coordinator_path.read_bytes()

    with pytest.raises(AidtRoutingFailure) as raised:
        apply_route_resolutions(board, [_resolution()])

    assert raised.value.category == "source_drift"
    assert coordinator_path.read_bytes() == before
    assert not (board.board_root / "A20-1188--viewer-api.md").exists()


def test_precommit_local_note_is_replanned_and_preserved(
    tmp_path: Path,
) -> None:
    board, coordinator_path = _board(tmp_path)

    def add_local_note() -> None:
        front, body = parse_ticket_file(coordinator_path)
        front["local_concurrent"] = "keep"
        write_ticket_atomic(coordinator_path, front, body + "\n\nconcurrent note")

    result = apply_route_resolutions(
        board,
        [_resolution()],
        precommit_hook=add_local_note,
    )

    front, body = parse_ticket_file(coordinator_path)
    assert result.changed == 2
    assert front["local_concurrent"] == "keep"
    assert "concurrent note" in body
    assert front["routing"]["fingerprint"] == "route-fingerprint"


def test_whole_poll_card_and_serialized_byte_caps_fail_before_route_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, coordinator_path = _board(tmp_path)
    base = _resolution()
    too_many_children = tuple(
        replace(
            base.children[0],
            identifier=f"A20-1188--service-{index}",
            service=f"service-{index}",
        )
        for index in range(MAX_CHILDREN + 1)
    )
    over_child_cap = RouteResolution(base.coordinator, too_many_children, (), True)

    with pytest.raises(AidtRoutingFailure) as child_failure:
        apply_route_resolutions(board, [over_child_cap])
    with pytest.raises(AidtRoutingFailure) as coordinator_failure:
        apply_route_resolutions(board, [base] * (MAX_COORDINATORS + 1))

    assert child_failure.value.category == "batch_limit"
    assert coordinator_failure.value.category == "batch_limit"
    before = coordinator_path.read_bytes()
    monkeypatch.setattr(storage_module, "MAX_ROUTE_BATCH_BYTES", 1)
    with pytest.raises(AidtRoutingFailure) as byte_failure:
        apply_route_resolutions(board, [base])
    assert byte_failure.value.category == "batch_limit"
    assert coordinator_path.read_bytes() == before
    assert not (board.board_root / "A20-1188--viewer-api.md").exists()
