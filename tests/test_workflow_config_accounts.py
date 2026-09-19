import pytest

from symphony.errors import ConfigValidationError
from symphony.workflow.builder import _validated_accounts
from symphony.workflow.config import AgentAccount


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
        {"codex": "notalist"},
    ],
)
def test_rejects_invalid_shapes(raw):
    with pytest.raises(ConfigValidationError):
        _validated_accounts(raw)
