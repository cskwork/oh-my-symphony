"""Black-box RED contract for the closed AIDT delivery profile."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from symphony.aidt_delivery import (
    ACTIVE_DELIVERY_STATES,
    TERMINAL_DELIVERY_STATES,
    AidtDeliveryFailure,
    DenyAllEvidenceProducerAuthority,
    DenyAllIssuePlanApprovalAuthority,
    EvidenceProducerCandidate,
    IssuePlanApprovalCandidate,
    issue_revision_from_card,
    load_aidt_delivery_settings,
)
from symphony.jira_intake import build_source_snapshot
from symphony.trackers.jira import JiraInboxIssue
from tests.aidt_routing_support import (
    routing_config,
    service_config,
    service_definition,
)


ACTIVE = (
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
TERMINAL = ("Human Review", "Done", "Blocked", "Cancelled")


def _config(tmp_path: Path, delivery: object = None) -> Any:
    board = (tmp_path / "board").resolve()
    board.mkdir(exist_ok=True)
    raw = routing_config(tmp_path.resolve(), [service_definition()])
    raw["aidt_worktree"] = {"enabled": True}
    if delivery is not None:
        raw["aidt_delivery"] = delivery
    config = service_config(board, raw)
    return replace(
        config,
        workflow_path=(tmp_path / "WORKFLOW.md").resolve(),
        workspace_root=(tmp_path / "workspaces").resolve(),
        workspace_reuse_policy="preserve",
        tracker=replace(
            config.tracker,
            active_states=ACTIVE,
            terminal_states=TERMINAL,
        ),
        hooks=SimpleNamespace(
            after_create=None,
            before_run=None,
            after_run=None,
            before_remove=None,
            after_done=None,
        ),
        agent=SimpleNamespace(
            kind="codex",
            auto_commit_on_done=False,
            auto_merge_on_done=False,
        ),
    )


def test_missing_and_closed_disabled_profile_are_inert(tmp_path: Path) -> None:
    assert load_aidt_delivery_settings(_config(tmp_path)) is None
    assert load_aidt_delivery_settings(_config(tmp_path, {"enabled": False})) is None
    assert not (tmp_path / ".symphony").exists()


def test_enabled_profile_exposes_exact_identity_policy_and_no_io(tmp_path: Path) -> None:
    settings = load_aidt_delivery_settings(
        _config(tmp_path, {"enabled": True, "environment": "fixture"})
    )

    assert settings is not None
    assert ACTIVE_DELIVERY_STATES == ACTIVE
    assert TERMINAL_DELIVERY_STATES == TERMINAL
    assert settings.active_states == ACTIVE
    assert settings.terminal_states == TERMINAL
    assert settings.non_dispatchable_states == frozenset({"Plan Approval"})
    assert settings.environment == "fixture"
    assert settings.state_db_path == tmp_path / ".symphony" / "state.db"
    assert len(settings.workflow_identity) == 64
    assert len(settings.policy_identity) == 64
    assert not settings.state_db_path.exists()
    with pytest.raises(FrozenInstanceError):
        setattr(settings, "environment", "aidt-dev")


@pytest.mark.parametrize(
    "delivery",
    [
        {},
        {"enabled": 1},
        {"enabled": True},
        {"enabled": True, "environment": "dev"},
        {"enabled": True, "environment": "AIDT-DEV"},
        {"enabled": True, "environment": " aidt-dev"},
        {"enabled": True, "environment": "aidt-dev", "unknown": True},
        {"enabled": False, "environment": "fixture"},
        ["enabled", True],
    ],
)
def test_profile_is_exact_and_default_deny(delivery: object, tmp_path: Path) -> None:
    with pytest.raises(AidtDeliveryFailure, match="profile_invalid") as raised:
        load_aidt_delivery_settings(_config(tmp_path, delivery))
    assert "aidt-dev" not in repr(raised.value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cfg: object.__setattr__(cfg.tracker, "kind", "linear"),
        lambda cfg: object.__setattr__(cfg, "workflow_path", Path("WORKFLOW.md")),
        lambda cfg: object.__setattr__(cfg.tracker, "board_root", Path("board")),
        lambda cfg: object.__setattr__(cfg, "workspace_root", Path("workspaces")),
        lambda cfg: object.__setattr__(cfg, "workspace_reuse_policy", "refresh"),
        lambda cfg: cfg.raw.__setitem__("aidt_routing", {"enabled": False}),
        lambda cfg: cfg.raw.__setitem__("aidt_worktree", {"enabled": False}),
        lambda cfg: setattr(cfg.hooks, "before_run", "git fetch"),
        lambda cfg: setattr(cfg.agent, "auto_commit_on_done", True),
        lambda cfg: setattr(cfg.agent, "auto_merge_on_done", True),
        lambda cfg: object.__setattr__(
            cfg.tracker, "active_states", (*ACTIVE[:-1], "Unknown")
        ),
        lambda cfg: object.__setattr__(
            cfg.tracker, "terminal_states", (*TERMINAL[:-1], "Archive")
        ),
    ],
)
def test_enabled_profile_rejects_every_generic_or_mutable_seam(
    mutate: Any, tmp_path: Path
) -> None:
    config = _config(tmp_path, {"enabled": True, "environment": "aidt-dev"})
    mutate(config)

    with pytest.raises(AidtDeliveryFailure, match="profile_invalid"):
        load_aidt_delivery_settings(config)
    assert not (tmp_path / ".symphony").exists()


def test_production_authorities_deny_exact_frozen_candidates() -> None:
    approval = IssuePlanApprovalCandidate(
        coordinator="A20-1188",
        child="A20-1188--viewer-api",
        issue_revision="a" * 64,
        plan_hash="b" * 64,
        purpose="issue_plan",
        decision="approved",
    )
    evidence = EvidenceProducerCandidate(
        child="A20-1188--viewer-api",
        producer="routing_observer",
        purpose="route",
    )

    assert DenyAllIssuePlanApprovalAuthority().verify(approval) is False
    assert DenyAllEvidenceProducerAuthority().verify(evidence) is False
    with pytest.raises(FrozenInstanceError):
        setattr(approval, "purpose", "infrastructure")


@pytest.mark.parametrize("bad_revision", ["A" * 64, "a" * 63, "g" * 64, 1, None])
def test_issue_revision_is_only_lowercase_sha256_from_source(
    bad_revision: object,
) -> None:
    card = {"source": {"revision": bad_revision}, "updated_at": "ignored", "notes": "ignored"}
    with pytest.raises(AidtDeliveryFailure, match="issue_revision_invalid") as raised:
        issue_revision_from_card(card)
    assert "ignored" not in repr(raised.value)


def test_issue_revision_comes_from_frontier_001_not_card_metadata() -> None:
    source = build_source_snapshot(
        JiraInboxIssue(
            key="A20-1188",
            summary="Route viewer API",
            description="Change GET /v-api/learning",
            issue_type="Task",
            updated="2026-07-20T03:34:56Z",
        )
    )
    card = {"source": source, "updated_at": "forged", "notes": "a"}

    assert issue_revision_from_card(card) == source["revision"]
    assert issue_revision_from_card({**card, "updated_at": "changed", "notes": "b"}) == source[
        "revision"
    ]
