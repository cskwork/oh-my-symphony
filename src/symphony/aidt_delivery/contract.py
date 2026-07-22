"""Closed, side-effect-free contract for AIDT delivery authorization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeGuard

from ..workflow import ServiceConfig


AIDT_DELIVERY_SCHEMA = "aidt-delivery-v1"
ACTIVE_DELIVERY_STATES = (
    "Intake",
    "Route",
    "Plan",
    "Plan Approval",
    "Worktree",
    "Build",
    "Review",
    "Local QA",
    "Commit",
    "Merge",
    "Deploy",
    "Dev QA",
    "Learn",
)
TERMINAL_DELIVERY_STATES = ("Human Review", "Done", "Blocked", "Cancelled")

_PROFILE_KEYS = frozenset({"enabled", "environment"})
_ENVIRONMENTS = frozenset({"fixture", "aidt-dev"})
_FAILURES = frozenset(
    {"profile_invalid", "issue_revision_invalid", "authority_invalid", "internal_error"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COORDINATOR = re.compile(r"^A20-[1-9][0-9]*$")
_CHILD = re.compile(r"^A20-[1-9][0-9]*--[a-z0-9]+(?:-[a-z0-9]+)*$")


class AidtDeliveryFailure(Exception):
    """Bounded delivery-contract failure safe for logs and projections."""

    def __init__(self, category: str) -> None:
        self.category = category if category in _FAILURES else "internal_error"
        super().__init__(self.category)


@dataclass(frozen=True)
class AidtDeliverySettings:
    enabled: bool
    environment: str
    active_states: tuple[str, ...]
    terminal_states: tuple[str, ...]
    non_dispatchable_states: frozenset[str]
    workflow_identity: str
    policy_identity: str
    state_db_path: Path


@dataclass(frozen=True)
class IssuePlanApprovalCandidate:
    coordinator: str
    child: str
    issue_revision: str
    plan_hash: str
    purpose: str
    decision: str

    def __post_init__(self) -> None:
        valid = (
            type(self.coordinator) is str
            and _COORDINATOR.fullmatch(self.coordinator) is not None
            and type(self.child) is str
            and _CHILD.fullmatch(self.child) is not None
            and self.child.startswith(f"{self.coordinator}--")
            and _is_sha256(self.issue_revision)
            and _is_sha256(self.plan_hash)
            and self.purpose == "issue_plan"
            and self.decision in {"approved", "rejected"}
        )
        if not valid:
            raise AidtDeliveryFailure("authority_invalid")


@dataclass(frozen=True)
class EvidenceProducerCandidate:
    child: str
    producer: str
    purpose: str

    def __post_init__(self) -> None:
        valid = (
            type(self.child) is str
            and _CHILD.fullmatch(self.child) is not None
            and type(self.producer) is str
            and self.producer in {"intake_observer", "routing_observer", "worktree_observer"}
            and type(self.purpose) is str
            and self.purpose in {"intake", "route", "worktree"}
        )
        if not valid:
            raise AidtDeliveryFailure("authority_invalid")


class IssuePlanApprovalAuthority(Protocol):
    def verify(self, candidate: IssuePlanApprovalCandidate) -> bool: ...


class EvidenceProducerAuthority(Protocol):
    def verify(self, candidate: EvidenceProducerCandidate) -> bool: ...


class DenyAllIssuePlanApprovalAuthority:
    def verify(self, candidate: IssuePlanApprovalCandidate) -> bool:
        if type(candidate) is not IssuePlanApprovalCandidate:
            raise AidtDeliveryFailure("authority_invalid")
        return False


class DenyAllEvidenceProducerAuthority:
    def verify(self, candidate: EvidenceProducerCandidate) -> bool:
        if type(candidate) is not EvidenceProducerCandidate:
            raise AidtDeliveryFailure("authority_invalid")
        return False


def load_aidt_delivery_settings(config: ServiceConfig) -> AidtDeliverySettings | None:
    """Validate the closed default-off profile without performing I/O."""
    raw = config.raw.get("aidt_delivery")
    if raw is None:
        return None
    if type(raw) is not dict or type(raw.get("enabled")) is not bool:
        raise AidtDeliveryFailure("profile_invalid")
    if raw["enabled"] is False:
        if set(raw) != {"enabled"}:
            raise AidtDeliveryFailure("profile_invalid")
        return None
    if set(raw) != _PROFILE_KEYS:
        raise AidtDeliveryFailure("profile_invalid")
    environment = raw.get("environment")
    if type(environment) is not str or environment not in _ENVIRONMENTS:
        raise AidtDeliveryFailure("profile_invalid")
    _validate_enabled_profile(config)
    workflow_path = config.workflow_path
    workflow_identity = _digest(
        "aidt-delivery-workflow-v1",
        str(workflow_path),
        str(config.tracker.board_root),
        str(config.workspace_root),
    )
    policy_identity = _digest(
        AIDT_DELIVERY_SCHEMA,
        workflow_identity,
        environment,
        *ACTIVE_DELIVERY_STATES,
        *TERMINAL_DELIVERY_STATES,
    )
    return AidtDeliverySettings(
        enabled=True,
        environment=environment,
        active_states=ACTIVE_DELIVERY_STATES,
        terminal_states=TERMINAL_DELIVERY_STATES,
        non_dispatchable_states=frozenset({"Plan Approval"}),
        workflow_identity=workflow_identity,
        policy_identity=policy_identity,
        state_db_path=workflow_path.parent / ".symphony" / "state.db",
    )


def issue_revision_from_card(card: object) -> str:
    """Read only Frontier-001's canonical Jira source revision."""
    if type(card) is not dict:
        raise AidtDeliveryFailure("issue_revision_invalid")
    source = card.get("source")
    if type(source) is not dict:
        raise AidtDeliveryFailure("issue_revision_invalid")
    revision = source.get("revision")
    if not _is_sha256(revision):
        raise AidtDeliveryFailure("issue_revision_invalid")
    return revision


def _validate_enabled_profile(config: ServiceConfig) -> None:
    paths = (config.workflow_path, config.tracker.board_root, config.workspace_root)
    hooks = config.hooks
    hook_values = (
        hooks.after_create,
        hooks.before_run,
        hooks.after_run,
        hooks.before_remove,
        getattr(hooks, "after_done", None),
    )
    valid = (
        all(isinstance(path, Path) and path.is_absolute() for path in paths)
        and config.tracker.kind == "file"
        and config.tracker.active_states == ACTIVE_DELIVERY_STATES
        and config.tracker.terminal_states == TERMINAL_DELIVERY_STATES
        and config.workspace_reuse_policy == "preserve"
        and all(value is None for value in hook_values)
        and getattr(config.agent, "auto_commit_on_done", None) is False
        and getattr(config.agent, "auto_merge_on_done", None) is False
        and _enabled_block(config.raw.get("aidt_routing"))
        and _enabled_block(config.raw.get("aidt_worktree"))
    )
    if not valid:
        raise AidtDeliveryFailure("profile_invalid")


def _enabled_block(value: Any) -> bool:
    return type(value) is dict and value.get("enabled") is True


def _is_sha256(value: object) -> TypeGuard[str]:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _digest(*parts: str) -> str:
    encoded = json.dumps(
        parts, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
