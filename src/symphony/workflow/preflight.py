"""SPEC §6.3 — dispatch preflight: refuse to start workers on broken config.

Builder defaults make a ServiceConfig *constructible* even when many
fields are blank; `validate_for_dispatch` is the second gate that runs
just before workers spin up and surfaces "this tracker can't actually
talk to the upstream" errors as typed exceptions the orchestrator can
log and surface to the operator.
"""

from __future__ import annotations

from ..errors import (
    ConfigValidationError,
    MissingTrackerApiKey,
    MissingTrackerEmail,
    MissingTrackerEndpoint,
    MissingTrackerProjectSlug,
    UnsupportedTrackerKind,
)
from .config import ServiceConfig
from .constants import SUPPORTED_TRACKER_KINDS


def stage_turn_budget_error(config: ServiceConfig) -> str | None:
    active_states = [state for state in config.tracker.active_states if state]
    required_turns = len(active_states)
    if required_turns <= 1:
        return None
    if config.agent.max_turns >= required_turns:
        return None
    states = ", ".join(active_states)
    return (
        f"agent.max_turns={config.agent.max_turns} cannot cover "
        f"{required_turns} active states ({states}). Set agent.max_turns >= "
        f"{required_turns}, or reduce active_states for a single-stage harness."
    )


def validate_for_dispatch(config: ServiceConfig) -> None:
    if not config.tracker.kind:
        raise UnsupportedTrackerKind("tracker.kind is required")
    if config.tracker.kind not in SUPPORTED_TRACKER_KINDS:
        raise UnsupportedTrackerKind(
            "tracker kind not supported", kind=config.tracker.kind
        )
    if config.tracker.kind == "linear":
        if not config.tracker.api_key:
            raise MissingTrackerApiKey(
                "tracker.api_key missing or empty after $VAR resolution"
            )
        if not config.tracker.project_slug:
            raise MissingTrackerProjectSlug(
                "tracker.project_slug required for linear tracker"
            )
    if config.tracker.kind == "file":
        if config.tracker.board_root is None:
            raise ConfigValidationError(
                "tracker.board_root is required when tracker.kind=file"
            )
    if config.tracker.kind == "jira":
        if not config.tracker.endpoint:
            raise MissingTrackerEndpoint(
                "tracker.endpoint required for jira tracker "
                "(e.g., https://your-domain.atlassian.net)"
            )
        if not config.tracker.email:
            raise MissingTrackerEmail(
                "tracker.email missing or empty after $VAR resolution"
            )
        if not config.tracker.api_key:
            raise MissingTrackerApiKey(
                "tracker.api_key missing or empty after $VAR resolution"
            )
        if not config.tracker.project_slug:
            raise MissingTrackerProjectSlug(
                "tracker.project_slug required for jira tracker (the project key, e.g., PROJ)"
            )
    kind = config.agent.kind
    if kind == "codex":
        if not config.codex.command.strip():
            raise ConfigValidationError("codex.command must be non-empty")
    elif kind == "claude":
        if not config.claude.command.strip():
            raise ConfigValidationError("claude.command must be non-empty")
    elif kind == "gemini":
        if not config.gemini.command.strip():
            raise ConfigValidationError("gemini.command must be non-empty")
    elif kind == "opencode":
        if not config.opencode.command.strip():
            raise ConfigValidationError("opencode.command must be non-empty")
    elif kind == "pi":
        if not config.pi.command.strip():
            raise ConfigValidationError("pi.command must be non-empty")
    budget_error = stage_turn_budget_error(config)
    if budget_error is not None:
        raise ConfigValidationError(budget_error)
