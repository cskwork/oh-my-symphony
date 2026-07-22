"""Stable public contract for deterministic AIDT routing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contract import (
    MAX_ALIASES_PER_SERVICE,
    MAX_ANCHORS_PER_CATEGORY,
    MAX_EVIDENCE_RECORDS,
    MAX_SERVICES,
    MAX_VALUE_BYTES,
    AidtRoutingFailure,
    AidtRoutingResult,
    canonical_fingerprint,
    load_routing_settings,
)

if TYPE_CHECKING:
    from .dispatch import AidtRouteDispatchContract, load_route_dispatch_contract
    from .git_objects import (
        AIDT_REPOSITORY_BINDING_SCHEMA,
        observe_service_binding,
    )
    from .runtime import filter_routing_candidates, run_aidt_routing

_RUNTIME_EXPORTS = frozenset({"filter_routing_candidates", "run_aidt_routing"})
_DISPATCH_EXPORTS = frozenset(
    {"AidtRouteDispatchContract", "load_route_dispatch_contract"}
)
_GIT_EXPORTS = frozenset(
    {"AIDT_REPOSITORY_BINDING_SCHEMA", "observe_service_binding"}
)

__all__ = [
    "MAX_ALIASES_PER_SERVICE",
    "MAX_ANCHORS_PER_CATEGORY",
    "MAX_EVIDENCE_RECORDS",
    "MAX_SERVICES",
    "MAX_VALUE_BYTES",
    "AidtRoutingFailure",
    "AidtRoutingResult",
    "AidtRouteDispatchContract",
    "AIDT_REPOSITORY_BINDING_SCHEMA",
    "canonical_fingerprint",
    "filter_routing_candidates",
    "load_routing_settings",
    "load_route_dispatch_contract",
    "observe_service_binding",
    "run_aidt_routing",
]


def __getattr__(name: str) -> object:
    if name in _DISPATCH_EXPORTS:
        from .dispatch import AidtRouteDispatchContract, load_route_dispatch_contract

        exports = {
            "AidtRouteDispatchContract": AidtRouteDispatchContract,
            "load_route_dispatch_contract": load_route_dispatch_contract,
        }
        globals().update(exports)
        return exports[name]
    if name in _GIT_EXPORTS:
        from .git_objects import (
            AIDT_REPOSITORY_BINDING_SCHEMA,
            observe_service_binding,
        )

        exports = {
            "AIDT_REPOSITORY_BINDING_SCHEMA": AIDT_REPOSITORY_BINDING_SCHEMA,
            "observe_service_binding": observe_service_binding,
        }
        globals().update(exports)
        return exports[name]
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from .runtime import filter_routing_candidates, run_aidt_routing

    exports = {
        "filter_routing_candidates": filter_routing_candidates,
        "run_aidt_routing": run_aidt_routing,
    }
    globals().update(exports)
    return exports[name]
