import textwrap
from pathlib import Path

import pytest

from symphony.errors import ConfigValidationError
from symphony.workflow import ServiceConfig, build_service_config, load_workflow
from symphony.workflow.builder import _validated_accounts
from symphony.workflow.config import AgentAccount


def _write_workflow(tmp_path: Path, body: str) -> Path:
    """Drop a YAML frontmatter workflow file at tmp_path/WORKFLOW.md."""
    path = tmp_path / "WORKFLOW.md"
    path.write_text(body)
    return path


def _build_cfg(tmp_path: Path, frontmatter: str) -> ServiceConfig:
    text = "---\n" + textwrap.dedent(frontmatter).lstrip() + "---\nbody"
    path = _write_workflow(tmp_path, text)
    return build_service_config(load_workflow(path))


def test_absent_accounts_is_empty():
    assert _validated_accounts(None) == {}


def test_parses_ordered_accounts_per_kind():
    out = _validated_accounts(
        {"codex": [
            {"id": "primary", "env": {"SYMPHONY_CODEX_HOME": "/a"}},
            {"id": "secondary", "env": {"SYMPHONY_CODEX_HOME": "/b"}},
        ]}
    )
    assert out == {
        "codex": (
            AgentAccount(id="primary", env={"SYMPHONY_CODEX_HOME": "/a"}),
            AgentAccount(id="secondary", env={"SYMPHONY_CODEX_HOME": "/b"}),
        )
    }


def test_empty_list_disables_the_kind():
    assert _validated_accounts({"codex": []}) == {}


@pytest.mark.parametrize(
    "raw",
    [
        {"nosuchkind": [{"id": "a", "env": {}}]},
        {"codex": [{"id": "a", "env": {}}, {"id": "a", "env": {}}]},
        {"codex": [{"id": "", "env": {}}]},
        {"codex": [{"id": "bad id!", "env": {}}]},
        {"codex": [{"id": "a", "env": {"K": 1}}]},
        {"codex": [{"id": "a", "env": []}]},
        {"codex": "notalist"},
    ],
)
def test_rejects_invalid_shapes(raw):
    with pytest.raises(ConfigValidationError):
        _validated_accounts(raw)


def test_build_service_config_no_accounts(tmp_path: Path) -> None:
    """End-to-end: workflow with NO agent.accounts produces defaults."""
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    assert cfg.agent.accounts == {}
    assert cfg.agent.account_quota_cooldown_ms == 3_600_000


def test_build_service_config_with_accounts(tmp_path: Path) -> None:
    """End-to-end: workflow with agent.accounts parses and reaches AgentConfig."""
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./kanban }
        agent:
          kind: codex
          accounts:
            codex:
              - id: prod
                env:
                  SYMPHONY_CODEX_HOME: /var/codex/prod
              - id: staging
                env:
                  SYMPHONY_CODEX_HOME: /var/codex/staging
          account_quota_cooldown_ms: 7_200_000
        codex: { command: codex app-server }
        """,
    )
    assert len(cfg.agent.accounts) == 1
    assert "codex" in cfg.agent.accounts
    codex_accounts = cfg.agent.accounts["codex"]
    assert len(codex_accounts) == 2
    assert codex_accounts[0].id == "prod"
    assert codex_accounts[0].env == {"SYMPHONY_CODEX_HOME": "/var/codex/prod"}
    assert codex_accounts[1].id == "staging"
    assert codex_accounts[1].env == {"SYMPHONY_CODEX_HOME": "/var/codex/staging"}
    assert cfg.agent.account_quota_cooldown_ms == 7_200_000
