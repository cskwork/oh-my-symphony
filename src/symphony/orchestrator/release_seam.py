"""Core's only door to the optional app release contracts (ADR 0002).

``oh-my-symphony-release`` (``packages/release``) contributes
``symphony.release``. ``Orchestrator`` binds every name in
``AUTHORITY_METHODS`` to a delegate that calls ``symphony.release.authority``
when the package is installed, so behaviour with the package is unchanged.
Without it a ticket with no release identity sees "no release on this board",
and a ticket carrying release labels or durable release authority fails
closed: dispatch is refused and the ticket is parked in ``Blocked`` with the
install hint, so it is never merged, delivered or marked Done through the
release path.

The release vocabulary (labels and lane predicates) lives here so core can
recognize release tickets without the package; the package imports it back.
The release tables, their migrations and the ``_ReleaseRegistry`` storage
mixin stay in core so a ``state.db`` stays valid either way.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable

from .. import component_installed, install_hint
from ..errors import SymphonyError
from ..issue import Issue, normalize_state
from ..logging import get_logger
from ..workflow import ServiceConfig
from .run_registry import ReleaseGate, RunRegistry

if TYPE_CHECKING:
    from .core import Orchestrator

log = get_logger()

RELEASE_PARK_STATE = "Blocked"
# Blocked recovery keeps a source with this heading Blocked until an operator
# acts; only installing the package can resolve it.
RELEASE_PARK_HEADING = "Operator Action"

# ---------------------------------------------------------------------------
# release vocabulary
# ---------------------------------------------------------------------------

_RELEASE_SUCCESS_STATE_NAMES = {
    "complete",
    "completed",
    "delivered",
    "done",
    "passed",
    "released",
    "shipped",
    "succeeded",
    "success",
}


def normalized_label_set(issue: Issue) -> set[str]:
    return {label.strip().lower() for label in issue.labels if label.strip()}


def is_release_cycle_verifier(issue: Issue) -> bool:
    labels = normalized_label_set(issue)
    return "release-cycle-verifier" in labels


def is_release_finalizer(issue: Issue) -> bool:
    return "app-release-finalizer" in normalized_label_set(issue)


def is_release_evidence_issue(issue: Issue) -> bool:
    labels = normalized_label_set(issue)
    return (
        "app-release" in labels
        or "app-release-finalizer" in labels
        or is_release_cycle_verifier(issue)
    )


def has_active_verify_lane(cfg: ServiceConfig) -> bool:
    return any(normalize_state(state) == "verify" for state in cfg.tracker.active_states)


def has_release_finalizer_lane(cfg: ServiceConfig) -> bool:
    return any(normalize_state(state) != "verify" for state in cfg.tracker.active_states)


def release_failure_target_state(cfg: ServiceConfig) -> str:
    """Explicit failure lane for release evidence; never fall back to Done."""
    normalized = [
        (state, normalize_state(state)) for state in cfg.tracker.terminal_states
    ]
    for keyword in ("block", "human", "review", "fail", "reject"):
        for state, lowered in normalized:
            if keyword in lowered:
                return state
    return ""


def is_release_success_state(cfg: ServiceConfig, state: str) -> bool:
    """Allow only explicit success terminals to authorize final delivery."""
    normalized = normalize_state(state)
    terminals = {
        normalize_state(candidate) for candidate in cfg.tracker.terminal_states
    }
    return normalized in terminals and normalized in _RELEASE_SUCCESS_STATE_NAMES


def has_release_success_terminal(cfg: ServiceConfig) -> bool:
    return any(
        is_release_success_state(cfg, state) for state in cfg.tracker.terminal_states
    )


def release_verifier_state(cfg: ServiceConfig) -> str:
    states = {normalize_state(state): state for state in cfg.tracker.active_states}
    verifier_state = states.get("verify")
    if verifier_state is None:
        raise SymphonyError(
            "app-release requires an active Verify lane; fresh verification "
            "cannot fall back to an implementation lane"
        )
    return verifier_state


@dataclass(frozen=True)
class _ReleaseDispatchAuthority:
    """Monotonic release role resolved before a worker lease is acquired."""

    issue: Issue
    gate: ReleaseGate | None = None
    app_release: bool = False
    cycle_verifier: bool = False
    finalizer: bool = False


# ---------------------------------------------------------------------------
# registry access shared by release and ordinary paths (bound on Orchestrator)
# ---------------------------------------------------------------------------


def _release_registry_required(self: Orchestrator, cfg: ServiceConfig) -> RunRegistry:
    """Return the release authority store or fail closed.

        Ordinary lease bookkeeping intentionally degrades when SQLite is
        unavailable. Application release authority cannot: a missing read
        must never turn a pending verifier or finalizer into a normal ticket.

        Opens without the OS-level reclaim (see
        `_ensure_run_registry_open_only`) — this fallback can run on the
        event loop, and dead-owner cleanup is covered by the per-tick
        off-loop reclaim pass.
        """
    self._ensure_run_registry_open_only(cfg)
    registry = self._run_registry
    if registry is None:
        raise SymphonyError(
            "application release authority registry is unavailable",
            workflow=str(cfg.workflow_path),
        )
    return registry


def _release_registry_call(
    self: Orchestrator,
    cfg: ServiceConfig,
    op: str,
    fn: Callable[[RunRegistry], Any],
) -> Any:
    registry = self._release_registry_required(cfg)
    try:
        result = fn(registry)
    except Exception as exc:
        self._registry_error_count += 1
        self._last_registry_error = f"release_{op}: {exc}"
        log.error("release_registry_error", op=op, error=str(exc))
        raise SymphonyError(
            "application release authority operation failed",
            operation=op,
            error=str(exc),
        ) from exc
    self._last_registry_error = None
    return result


# ---------------------------------------------------------------------------
# fail-closed behaviour without the package
# ---------------------------------------------------------------------------


def _release_identity_known(self: Orchestrator, cfg: ServiceConfig, issue: Issue) -> bool:
    """Whether *issue* carries release labels or durable release authority."""
    if is_release_evidence_issue(issue):
        return True
    if cfg.tracker.kind != "file":
        return False
    try:
        return bool(
            self._release_registry_call(
                cfg,
                "find_release_identity_without_component",
                lambda registry: (
                    registry.get_release_gate_for_verifier(issue.identifier) is not None
                    or registry.get_release_gate(issue.identifier) is not None
                    or registry.get_release_evidence_identity(issue.identifier)
                    is not None
                ),
            )
        )
    except SymphonyError:
        # Authority cannot be read; a board with a release contract may hold
        # gates for this ticket, so it must not run as an ordinary one.
        return (cfg.workflow_path.parent / "release-contract.yaml").exists()


def _refuse_without_component(*_args: Any, **_kwargs: Any) -> Any:
    raise SymphonyError(install_hint("release"))


def _prepare_release_dispatch_without_component(
    self: Orchestrator, issue: Issue, cfg: ServiceConfig
) -> _ReleaseDispatchAuthority:
    if _release_identity_known(self, cfg, issue):
        raise SymphonyError(install_hint("release"), identifier=issue.identifier)
    return _ReleaseDispatchAuthority(issue=issue)


async def _startup_release_terminal_guard_without_component(
    self: Orchestrator,
    cfg: ServiceConfig,
    issue: Issue,
    *,
    owned_cleanup_run_id: str | None = None,
) -> tuple[Issue, bool, bool]:
    if cfg.tracker.kind != "file" or not _release_identity_known(self, cfg, issue):
        return issue, False, False
    log.error(
        "startup_release_cleanup_refused",
        identifier=issue.identifier,
        error=install_hint("release"),
    )
    return issue, True, True


async def _enforce_app_release_transition_without_component(
    self: Orchestrator,
    *,
    cfg: ServiceConfig,
    issue: Issue,
    workspace_path: Any,
    producing_state: str,
    known_app_release: bool,
    running_entry: Any = None,
) -> tuple[Issue, bool]:
    refreshed = await self._refresh_issue_full(cfg, issue.id)
    if refreshed is not None:
        issue = refreshed
    running = running_entry or self._running.get(issue.id)
    if not (
        known_app_release
        or (running is not None and running.known_app_release)
        or is_release_evidence_issue(issue)
    ):
        return issue, False
    issue = await _park_release_ticket(
        self,
        cfg,
        issue,
        f"`{issue.identifier}` left `{producing_state}` with app release labels, "
        "but this host cannot verify app release contracts.",
    )
    if running is not None:
        running.issue = issue
    raise SymphonyError(install_hint("release"), identifier=issue.identifier)


async def _park_release_ticket(
    self: Orchestrator, cfg: ServiceConfig, issue: Issue, reason: str
) -> Issue:
    body = (
        f"App release contracts are missing. {reason} Symphony moved it to "
        f"`{RELEASE_PARK_STATE}` so it cannot be merged or delivered.\n\n"
        f"{install_hint('release')}\n\n"
        "After installing it, restart Symphony and move the ticket back to its lane."
    )
    try:
        await asyncio.to_thread(
            self._tracker_call_update_state, cfg, issue, RELEASE_PARK_STATE
        )
        await asyncio.to_thread(
            self._tracker_call_append_note, cfg, issue, RELEASE_PARK_HEADING, body
        )
    except Exception as exc:
        log.warning(
            "release_ticket_park_failed", identifier=issue.identifier, error=str(exc)
        )
        return issue
    log.warning("release_ticket_parked", identifier=issue.identifier)
    return replace(issue, state=RELEASE_PARK_STATE)


async def _hold_release_ticket_without_component(
    self: Orchestrator, issue: Issue, cfg: ServiceConfig
) -> bool:
    """Park a release ticket before dispatch when the package is absent."""
    if component_installed("release") or not _release_identity_known(self, cfg, issue):
        return False
    await _park_release_ticket(
        self,
        cfg,
        issue,
        f"`{issue.identifier}` carries app release identity, but this host "
        "cannot enforce app release contracts.",
    )
    # Same as a conflict block: keep it out of this tick even if the tracker
    # write failed; dispatch authority refuses it on every later attempt.
    self._claimed.add(issue.id)
    return True


# ---------------------------------------------------------------------------
# delegates bound on Orchestrator
# ---------------------------------------------------------------------------

AUTHORITY_METHODS = (
    "_pending_release_gate",
    "_persist_pending_release_gate",
    "_create_initial_release_gate",
    "_reopen_stale_release_gate",
    "_guard_release_finalizer",
    "_mark_release_finalizer_completed",
    "_invalidate_release_finalizer_version",
    "_guard_release_finalizer_with_version",
    "_require_running_release_authority",
    "_require_release_transition_verifier_authority",
    "_prepare_release_dispatch",
    "_release_verifier_handoff_is_durable",
    "_rewind_app_release_transition",
    "_app_release_transition_lock",
    "_enforce_app_release_transition",
    "_enforce_app_release_transition_guarded",
    "_enforce_app_release_transition_inner",
    "_startup_release_terminal_guard",
    "_tracker_call_reconcile_release_cycle",
)

# Every other method is reachable only for a run that already holds release
# authority, which nothing grants without the package.
_WITHOUT_COMPONENT: dict[str, Callable[..., Any]] = {
    "_prepare_release_dispatch": _prepare_release_dispatch_without_component,
    "_startup_release_terminal_guard": _startup_release_terminal_guard_without_component,
    "_enforce_app_release_transition": _enforce_app_release_transition_without_component,
    "_release_verifier_handoff_is_durable": lambda *_args, **_kwargs: False,
}


def authority_method(name: str) -> Callable[..., Any]:
    """Delegate *name* to ``symphony.release.authority`` or its fallback.

    Resolved per call, like ``pm_installed()``, so a missing package is
    honoured without re-importing core.
    """
    fallback = _WITHOUT_COMPONENT.get(name, _refuse_without_component)

    def method(*args: Any, **kwargs: Any) -> Any:
        if component_installed("release"):
            from ..release import authority  # pyright: ignore[reportMissingImports]

            return getattr(authority, name)(*args, **kwargs)
        return fallback(*args, **kwargs)

    method.__name__ = method.__qualname__ = name
    return method
