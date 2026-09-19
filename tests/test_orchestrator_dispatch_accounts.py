"""B3 — merging the resolved account overlay into the per-dispatch env."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from symphony.orchestrator import RunningEntry
from symphony.workflow.config import AgentAccount
from tests.test_orchestrator_dispatch import _issue, _make_config, _orch


@pytest.fixture
def orchestrator_and_cfg(tmp_path):
    cfg = _make_config(
        tracker_kind="file",
        workflow_path=tmp_path / "WORKFLOW.md",
        workspace_root=tmp_path / "ws",
    )
    orch = _orch()
    issue = _issue("MT-Q", state="In Progress")
    # The account pools below are keyed on "codex" — pin that down so a
    # future change to `_make_config`'s default kind can't silently break
    # these tests for an unrelated reason.
    assert cfg.agent.kind_for_state(issue.state, issue.agent_kind) == "codex"
    return orch, cfg, issue


@pytest.fixture
def running_entry(orchestrator_and_cfg, tmp_path):
    _orch, _cfg, issue = orchestrator_and_cfg
    return RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path / "ws" / "MT-Q",
        agent_kind=None,
    )


def test_no_pool_leaves_dispatch_env_untouched(orchestrator_and_cfg):
    orch, cfg, issue = orchestrator_and_cfg
    env = orch._dispatch_env_for(issue=issue, cfg=cfg, is_rewind=False)
    assert set(env) == {"SYMPHONY_TOKEN_EMA", "SYMPHONY_TOKEN_BUDGET"}


def test_pool_merges_the_active_account_overlay(orchestrator_and_cfg):
    orch, cfg, issue = orchestrator_and_cfg
    cfg = replace(cfg, agent=replace(
        cfg.agent,
        accounts={"codex": (AgentAccount(id="primary", env={"SYMPHONY_CODEX_HOME": "/a"}),)},
    ))
    env = orch._dispatch_env_for(issue=issue, cfg=cfg, is_rewind=False)
    assert env["SYMPHONY_CODEX_HOME"] == "/a"


def test_pin_selects_the_account(orchestrator_and_cfg):
    orch, cfg, issue = orchestrator_and_cfg
    cfg = replace(cfg, agent=replace(
        cfg.agent,
        accounts={"codex": (
            AgentAccount(id="primary", env={"SYMPHONY_CODEX_HOME": "/a"}),
            AgentAccount(id="secondary", env={"SYMPHONY_CODEX_HOME": "/b"}),
        )},
    ))
    issue = replace(issue, agent_account="secondary")
    env = orch._dispatch_env_for(issue=issue, cfg=cfg, is_rewind=False)
    assert env["SYMPHONY_CODEX_HOME"] == "/b"


def test_overlay_wins_over_computed_env(orchestrator_and_cfg):
    orch, cfg, issue = orchestrator_and_cfg
    cfg = replace(cfg, agent=replace(
        cfg.agent,
        accounts={"codex": (AgentAccount(id="primary", env={"SYMPHONY_TOKEN_BUDGET": "7"}),)},
    ))
    env = orch._dispatch_env_for(issue=issue, cfg=cfg, is_rewind=False)
    assert env["SYMPHONY_TOKEN_BUDGET"] == "7"


def test_dispatch_and_rotation_resolve_the_same_kind(
    orchestrator_and_cfg, running_entry, monkeypatch
):
    orch, cfg, issue = orchestrator_and_cfg
    monkeypatch.setattr(orch._workflow_state, "current", lambda: cfg)
    assert orch._entry_agent_kind(running_entry) == orch._issue_agent_kind(cfg, issue)
