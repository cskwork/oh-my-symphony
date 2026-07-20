"""AIDT-owned whole-poll persistence over the generic file tracker."""

from __future__ import annotations

import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..aidt_routing.contract import (
    MAX_CHILDREN,
    MAX_COORDINATORS,
    MAX_ROUTE_BATCH_BYTES,
    AidtRoutingFailure,
)
from ..aidt_routing.decision import RouteCardProjection, RouteResolution
from .file import (
    FileBoardTracker,
    _CasToken,
    _exclusive_lock,
    _file_mtime_ns,
    parse_ticket_file,
    serialize_ticket,
    write_ticket_atomic,
)


AIDT_ROUTE_START = "<!-- symphony:aidt-route:start -->"
AIDT_ROUTE_END = "<!-- symphony:aidt-route:end -->"
_AIDT_ROUTE_MARKER_RE = re.compile(
    r"<!--\s*symphony\s*:\s*aidt-route\s*:\s*(?:start|end)\s*-->",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AidtRouteBatchResult:
    changed: int
    partial_apply: bool


class AidtPartialApplyError(AidtRoutingFailure):
    """A whole-poll route batch failed after an atomic rename."""

    def __init__(self, identifier: str) -> None:
        super().__init__("partial_apply", f"card:{identifier}")


@dataclass(frozen=True)
class _BoardRow:
    path: Path
    front: dict[str, Any]
    body: str


@dataclass(frozen=True)
class _RoutePlan:
    projection: RouteCardProjection
    path: Path
    token: _CasToken
    front: dict[str, Any]
    body: str
    changed: bool


def apply_route_resolutions(
    board: FileBoardTracker,
    resolutions: Iterable[RouteResolution],
    *,
    precommit_hook: Callable[[], None] | None = None,
    rename_fault_hook: Callable[[str, int], None] | None = None,
) -> AidtRouteBatchResult:
    """Apply one complete poll under all target locks and one precommit seam."""
    projections = _validated_projections(tuple(resolutions))
    if not projections:
        return AidtRouteBatchResult(changed=0, partial_apply=False)
    with ExitStack() as locks:
        for item in projections:
            locks.enter_context(_exclusive_lock(board._ticket_lock_path(item.identifier)))
        _preflight(board, projections)
        if precommit_hook is not None:
            precommit_hook()
        plans = _preflight(board, projections)
        _check_batch_size(plans)
        return _commit(plans, rename_fault_hook)


def _validated_projections(
    resolutions: tuple[RouteResolution, ...],
) -> tuple[RouteCardProjection, ...]:
    if len(resolutions) > MAX_COORDINATORS:
        raise AidtRoutingFailure("batch_limit")
    projections = tuple(item for route in resolutions for item in route.projections)
    coordinators = sum(item.role == "coordinator" for item in projections)
    children = sum(item.role == "child" for item in projections)
    if coordinators != len(resolutions) or children > MAX_CHILDREN:
        raise AidtRoutingFailure("batch_limit")
    _validate_projection_set(projections)
    return tuple(sorted(projections, key=lambda item: item.identifier.casefold()))


def _validate_projection_set(projections: tuple[RouteCardProjection, ...]) -> None:
    seen: set[str] = set()
    for item in projections:
        if not isinstance(item, RouteCardProjection):
            raise AidtRoutingFailure("internal_error")
        folded = item.identifier.casefold()
        if folded in seen:
            raise AidtRoutingFailure("route_collision", f"card:{item.coordinator}")
        seen.add(folded)
        _validate_projection(item)


def _validate_projection(item: RouteCardProjection) -> None:
    valid_role = item.role in {"coordinator", "child"}
    valid_service = (item.role == "coordinator") == (item.service is None)
    if not valid_role or not valid_service:
        raise AidtRoutingFailure("route_collision", f"card:{item.coordinator}")
    if item.role == "coordinator" and item.identifier != item.coordinator:
        raise AidtRoutingFailure("route_collision", f"card:{item.coordinator}")
    if item.role == "child":
        expected = f"{item.coordinator}--{item.service}"
        if item.identifier != expected:
            raise AidtRoutingFailure("route_collision", f"card:{item.coordinator}")
    if _aidt_marker_span(item.marker) != (0, len(item.marker)):
        raise AidtRoutingFailure("route_collision", f"card:{item.coordinator}")


def _preflight(
    board: FileBoardTracker,
    projections: tuple[RouteCardProjection, ...],
) -> list[_RoutePlan]:
    rows = _board_rows(board)
    _validate_child_claims(rows)
    plans: list[_RoutePlan] = []
    for item in projections:
        row = _matching_row(rows, item)
        if row is not None:
            _validate_existing(item, row)
        plans.append(_plan(board, item, row))
    return plans


def _board_rows(board: FileBoardTracker) -> tuple[_BoardRow, ...]:
    rows: list[_BoardRow] = []
    raw_ids: set[str] = set()
    for path in sorted(board.board_root.glob("*.md")):
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or stat.S_ISLNK(file_stat.st_mode):
            raise AidtRoutingFailure("route_collision")
        if not path.resolve(strict=True).is_relative_to(board.board_root):
            raise AidtRoutingFailure("route_collision")
        front, body = parse_ticket_file(path)
        _record_raw_ids(front, raw_ids)
        rows.append(_BoardRow(path, front, body))
    return tuple(rows)


def _record_raw_ids(front: dict[str, Any], seen: set[str]) -> None:
    values = {value for value in (front.get("id"), front.get("identifier")) if isinstance(value, str)}
    if len(values) > 1:
        raise AidtRoutingFailure("route_collision")
    for value in values:
        folded = value.casefold()
        if folded in seen:
            raise AidtRoutingFailure("route_collision")
        seen.add(folded)


def _matching_row(
    rows: tuple[_BoardRow, ...], item: RouteCardProjection
) -> _BoardRow | None:
    target_name = f"{item.identifier}.md".casefold()
    matches = [row for row in rows if _row_matches(row, item.identifier, target_name)]
    if len(matches) > 1:
        raise AidtRoutingFailure("route_collision", f"card:{item.coordinator}")
    return matches[0] if matches else None


def _row_matches(row: _BoardRow, identifier: str, target_name: str) -> bool:
    if row.path.name.casefold() == target_name:
        return True
    return any(
        isinstance(value, str) and value.casefold() == identifier.casefold()
        for value in (row.front.get("id"), row.front.get("identifier"))
    )


def _validate_child_claims(rows: tuple[_BoardRow, ...]) -> None:
    claims: dict[str, str] = {}
    for row in rows:
        routing = row.front.get("routing")
        if not isinstance(routing, dict) or routing.get("role") != "coordinator":
            continue
        coordinator = row.front.get("id")
        if not isinstance(coordinator, str):
            raise AidtRoutingFailure("route_collision")
        child_ids = _claimed_children(routing, coordinator)
        for child_id in child_ids:
            owner = claims.setdefault(child_id.casefold(), coordinator)
            if owner != coordinator:
                raise AidtRoutingFailure("route_collision", f"card:{coordinator}")


def _claimed_children(routing: dict[str, Any], coordinator: str) -> tuple[str, ...]:
    children = routing.get("children", [])
    retained = routing.get("retained_children", [])
    if not isinstance(children, list) or not isinstance(retained, list):
        raise AidtRoutingFailure("route_collision", f"card:{coordinator}")
    values = (*children, *retained)
    if not all(isinstance(item, str) for item in values):
        raise AidtRoutingFailure("route_collision", f"card:{coordinator}")
    return values


def _validate_existing(item: RouteCardProjection, row: _BoardRow) -> None:
    ref = f"card:{item.coordinator}"
    if row.path.name != f"{item.identifier}.md":
        raise AidtRoutingFailure("route_collision", ref)
    if row.front.get("id") != item.identifier or row.front.get("identifier") != item.identifier:
        raise AidtRoutingFailure("route_collision", ref)
    if item.role == "coordinator":
        _validate_coordinator_source(item, row.front.get("source"))
    else:
        _validate_child_source(item, row.front.get("source"))
    routing = row.front.get("routing")
    span = _aidt_marker_span(row.body)
    if isinstance(routing, dict) != (span is not None):
        raise AidtRoutingFailure("route_collision", ref)


def _validate_coordinator_source(item: RouteCardProjection, source: object) -> None:
    ref = f"card:{item.coordinator}"
    if not isinstance(source, dict):
        raise AidtRoutingFailure("route_collision", ref)
    if source.get("kind") != "jira" or source.get("key") != item.identifier:
        raise AidtRoutingFailure("route_collision", ref)
    if source.get("revision") != item.expected_source_revision:
        raise AidtRoutingFailure("source_drift", ref)


def _validate_child_source(item: RouteCardProjection, source: object) -> None:
    ref = f"card:{item.coordinator}"
    if not isinstance(source, dict) or item.source is None:
        raise AidtRoutingFailure("route_collision", ref)
    identity = (
        source.get("kind"),
        source.get("key"),
        source.get("coordinator"),
        source.get("service"),
    )
    expected = ("aidt-route-child", f"{item.coordinator}::{item.service}", item.coordinator, item.service)
    if identity != expected or source != item.source:
        raise AidtRoutingFailure("route_collision", ref)


def _plan(
    board: FileBoardTracker,
    item: RouteCardProjection,
    row: _BoardRow | None,
) -> _RoutePlan:
    if row is None:
        if item.role != "child":
            raise AidtRoutingFailure("route_collision", f"card:{item.coordinator}")
        path = board.board_root / f"{item.identifier}.md"
        front = _new_child(board, item)
        body = ""
        token: _CasToken = (None, None)
    else:
        path, front, body = row.path, row.front, row.body
        token = (front.get("updated_at"), _file_mtime_ns(path))
    desired_front, desired_body = _desired_card(item, front, body)
    changed = desired_front != front or desired_body != body
    return _RoutePlan(item, path, token, desired_front, desired_body, changed)


def _new_child(board: FileBoardTracker, item: RouteCardProjection) -> dict[str, Any]:
    if item.source is None:
        raise AidtRoutingFailure("route_collision", f"card:{item.coordinator}")
    return board._new_ticket_front(
        identifier=item.identifier,
        title=f"{item.coordinator} / {item.service}",
        state=item.desired_state or item.route_owned_states[0],
        priority=None,
        labels=None,
        agent_kind=None,
        skills=None,
    )


def _desired_card(
    item: RouteCardProjection, front: dict[str, Any], body: str
) -> tuple[dict[str, Any], str]:
    desired = dict(front)
    desired["routing"] = item.routing
    if item.source is not None:
        desired["source"] = item.source
    current_state = front.get("state")
    owned = {state.casefold() for state in item.route_owned_states}
    if isinstance(current_state, str) and current_state.casefold() in owned:
        if item.desired_state is not None:
            desired["state"] = item.desired_state
    return desired, _replace_marker(body, item.marker)


def _replace_marker(body: str, marker: str) -> str:
    span = _aidt_marker_span(body)
    if span is not None:
        return body[: span[0]] + marker + body[span[1] :]
    if not body:
        return marker
    return body.rstrip() + "\n\n" + marker


def _aidt_marker_span(body: str) -> tuple[int, int] | None:
    matches = list(_AIDT_ROUTE_MARKER_RE.finditer(body))
    if not matches:
        return None
    exact = len(matches) == 2
    exact = exact and matches[0].group(0) == AIDT_ROUTE_START
    exact = exact and matches[1].group(0) == AIDT_ROUTE_END
    if not exact or matches[0].start() >= matches[1].start():
        raise AidtRoutingFailure("route_collision")
    return matches[0].start(), matches[1].end()


def _check_batch_size(plans: list[_RoutePlan]) -> None:
    total = sum(
        len(serialize_ticket(plan.front, plan.body).encode("utf-8"))
        for plan in plans
    )
    if total > MAX_ROUTE_BATCH_BYTES:
        raise AidtRoutingFailure("batch_limit")


def _commit(
    plans: list[_RoutePlan],
    fault_hook: Callable[[str, int], None] | None,
) -> AidtRouteBatchResult:
    changed = [plan for plan in plans if plan.changed]
    changed.sort(key=_commit_key)
    written = 0
    for plan in changed:
        try:
            _assert_current(plan)
            write_ticket_atomic(plan.path, plan.front, plan.body)
            written += 1
            if fault_hook is not None:
                fault_hook(plan.projection.identifier, written)
        except Exception as exc:
            if written:
                raise AidtPartialApplyError(plan.projection.coordinator) from exc
            raise
    return AidtRouteBatchResult(changed=written, partial_apply=False)


def _commit_key(plan: _RoutePlan) -> tuple[bool, str]:
    return (plan.projection.role == "coordinator", plan.projection.identifier.casefold())


def _assert_current(plan: _RoutePlan) -> None:
    if not plan.path.exists():
        current: _CasToken = (None, None)
    else:
        front, _ = parse_ticket_file(plan.path)
        current = (front.get("updated_at"), _file_mtime_ns(plan.path))
    if current != plan.token:
        ref = f"card:{plan.projection.coordinator}"
        raise AidtRoutingFailure("preflight_changed", ref)
