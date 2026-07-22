"""One-pass runtime composition for deterministic AIDT routing."""

from __future__ import annotations

import re
import stat
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeGuard

from ..issue import Issue
from ..jira_intake import JiraIntakeResult
from ..logging import get_logger
from ..trackers.aidt_routes import apply_route_resolutions
from ..trackers.file import FileBoardTracker, parse_ticket_file
from ..workflow import ServiceConfig, TrackerConfig
from .contract import (
    MAX_CHILDREN,
    MAX_COORDINATORS,
    AidtRoutingFailure,
    AidtRoutingResult,
    RoutingSettings,
    load_routing_settings,
)
from .decision import RouteCardProjection, RouteResolution, resolve_card
from .git_objects import (
    CatalogObservation,
    GitRunner,
    IdentityProbe,
    observe_catalog,
    recheck_catalog,
)


BoardFactory = Callable[[TrackerConfig], FileBoardTracker]
_CARD_KEY = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")
_ROUTE_CHILD = re.compile(
    r"^[A-Z][A-Z0-9]*-[1-9][0-9]*--[a-z0-9]+(?:-[a-z0-9]+)*$"
)
log = get_logger()


def filter_routing_candidates(
    candidates: Iterable[Issue],
    blocked_identifiers: frozenset[str],
    provisionable_child_identifiers: frozenset[str] = frozenset(),
) -> list[Issue]:
    """Preserve unmanaged and nominated child order; block other managed cards."""
    return [
        issue
        for issue in candidates
        if issue.identifier not in blocked_identifiers
        or issue.identifier in provisionable_child_identifiers
    ]


def run_aidt_routing(
    config: ServiceConfig,
    *,
    intake_result: JiraIntakeResult | None = None,
    board_factory: BoardFactory | None = None,
    git_runner: GitRunner | None = None,
    identity_probe: IdentityProbe | None = None,
    now: Callable[[], datetime] | None = None,
    precommit_hook: Callable[[], None] | None = None,
    rename_fault_hook: Callable[[str, int], None] | None = None,
) -> AidtRoutingResult:
    """Run one bounded whole-poll route transaction without leaking causes."""
    blocked: set[str] = set()
    try:
        settings = load_routing_settings(config)
        if settings is None:
            return _disabled_result()
        board = _build_board(config, board_factory)
        _validate_source_mode(settings, intake_result)
        cards, blocked = _scan_managed_cards(board)
        catalog = observe_catalog(
            settings,
            git_runner=git_runner,
            identity_probe=identity_probe,
        )
        return _resolve_and_apply(
            board,
            cards,
            settings,
            catalog,
            blocked,
            git_runner,
            identity_probe,
            now or _utc_now,
            precommit_hook,
            rename_fault_hook,
        )
    except AidtRoutingFailure as failure:
        return _failure_result(failure, blocked)
    except Exception:
        return _failure_result(AidtRoutingFailure("internal_error"), blocked)


def _build_board(
    config: ServiceConfig,
    board_factory: BoardFactory | None,
) -> FileBoardTracker:
    if config.tracker.kind != "file" or config.tracker.board_root is None:
        raise AidtRoutingFailure("config_invalid")
    factory = board_factory or FileBoardTracker
    return factory(config.tracker)


def _validate_source_mode(
    settings: RoutingSettings,
    intake_result: JiraIntakeResult | None,
) -> None:
    if settings.source_mode == "same_tick_jira":
        if intake_result is None or not intake_result.enabled:
            raise AidtRoutingFailure("intake_unavailable")
        return
    if intake_result is None or intake_result.enabled:
        raise AidtRoutingFailure("source_mode_invalid")


def _scan_managed_cards(
    board: FileBoardTracker,
) -> tuple[tuple[dict[str, Any], ...], set[str]]:
    cards: list[dict[str, Any]] = []
    blocked: set[str] = set()
    try:
        paths = sorted(board.board_root.glob("*.md"))
        for path in paths:
            _validate_card_path(board.board_root, path)
            front, _body = parse_ticket_file(path)
            source = front.get("source")
            if not isinstance(source, dict) or source.get("kind") != "jira":
                continue
            cards.append(front)
            blocked.update(_managed_identifiers(front))
            if len(cards) > MAX_COORDINATORS or len(blocked) > MAX_CHILDREN + MAX_COORDINATORS:
                raise AidtRoutingFailure("batch_limit")
    except AidtRoutingFailure:
        raise
    except Exception:
        raise AidtRoutingFailure("internal_error") from None
    return tuple(cards), blocked


def _validate_card_path(root: Path, path: Path) -> None:
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_ISLNK(file_stat.st_mode):
        raise AidtRoutingFailure("route_collision")
    if not path.resolve(strict=True).is_relative_to(root):
        raise AidtRoutingFailure("route_collision")


def _managed_identifiers(front: Mapping[str, Any]) -> set[str]:
    identifiers: set[str] = set()
    identifier = front.get("id")
    if identifier == front.get("identifier") and _valid_card_key(identifier):
        identifiers.add(identifier)
    routing = front.get("routing")
    if not isinstance(routing, dict):
        return identifiers
    for key in ("children", "retained_children"):
        values = routing.get(key, [])
        if not isinstance(values, list):
            raise AidtRoutingFailure("route_collision")
        identifiers.update(item for item in values if _valid_child_id(item))
    return identifiers


def _valid_card_key(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _CARD_KEY.fullmatch(value) is not None


def _valid_child_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _ROUTE_CHILD.fullmatch(value) is not None


def _resolve_and_apply(
    board: FileBoardTracker,
    cards: tuple[dict[str, Any], ...],
    settings: RoutingSettings,
    catalog: CatalogObservation,
    blocked: set[str],
    git_runner: GitRunner | None,
    identity_probe: IdentityProbe | None,
    now: Callable[[], datetime],
    precommit_hook: Callable[[], None] | None,
    rename_fault_hook: Callable[[str, int], None] | None,
) -> AidtRoutingResult:
    resolutions = tuple(
        resolve_card(front, settings, catalog, now=now)
        for front in cards
    )
    for resolution in resolutions:
        blocked.update(resolution.blocked_identifiers)
    apply_route_resolutions(
        board,
        resolutions,
        precommit_hook=_catalog_precommit(
            catalog,
            git_runner,
            identity_probe,
            precommit_hook,
        ),
        rename_fault_hook=rename_fault_hook,
    )
    return _success_result(resolutions, blocked)


def _catalog_precommit(
    catalog: CatalogObservation,
    git_runner: GitRunner | None,
    identity_probe: IdentityProbe | None,
    injected_hook: Callable[[], None] | None,
) -> Callable[[], None]:
    def precommit() -> None:
        if injected_hook is not None:
            injected_hook()
        recheck_catalog(
            catalog,
            git_runner=git_runner,
            identity_probe=identity_probe,
        )

    return precommit


def _success_result(
    resolutions: tuple[RouteResolution, ...],
    blocked: set[str],
) -> AidtRoutingResult:
    routed_count = sum(resolution.routed for resolution in resolutions)
    review_count = len(resolutions) - routed_count
    child_count = sum(
        len(resolution.children) + len(resolution.retained)
        for resolution in resolutions
    )
    status = "review" if review_count else "success"
    provisionable = frozenset(
        child.identifier
        for resolution in resolutions
        if resolution.routed
        for child in resolution.children
        if _provisionable_projection(child)
    )
    return AidtRoutingResult(
        True,
        True,
        frozenset(blocked),
        routed_count,
        review_count,
        child_count,
        0,
        status,
        provisionable_child_identifiers=provisionable,
    )


def _provisionable_projection(value: object) -> TypeGuard[RouteCardProjection]:
    if not isinstance(value, RouteCardProjection) or value.role != "child":
        return False
    routing = value.routing
    return (
        routing.get("schema") == "aidt-route-object-v2"
        and routing.get("role") == "child"
        and routing.get("status") == "pending_fresh_base_equality"
    )


def _disabled_result() -> AidtRoutingResult:
    return AidtRoutingResult(
        False,
        True,
        frozenset(),
        0,
        0,
        0,
        0,
        "disabled",
    )


def _failure_result(
    failure: AidtRoutingFailure,
    blocked: Iterable[str],
) -> AidtRoutingResult:
    result = AidtRoutingResult(
        True,
        False,
        frozenset(blocked),
        0,
        0,
        0,
        1,
        "failure",
        failure.category,
        failure.identifier,
    )
    log.warning(
        "aidt_routing_failure",
        category=result.error_category,
        ref=result.error_ref,
        blocked_count=len(result.blocked_identifiers),
        failure_count=result.failure_count,
    )
    return result


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
