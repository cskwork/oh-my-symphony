"""Codex inherits the installed CLI model unless the workflow overrides it."""

from pathlib import Path

import pytest

from symphony.backends import BackendInit
from symphony.backends.codex import CodexAppServerBackend
from symphony.workflow import build_service_config, parse_workflow_text


@pytest.mark.parametrize(
    ("codex_yaml", "expected_model"),
    [
        ("", ""),
        ("codex: {model: null}", ""),
        ("codex: {model: ''}", ""),
        ("codex: {model: configured-model}", "configured-model"),
    ],
)
def test_codex_model_is_only_sent_when_configured(
    tmp_path: Path, codex_yaml: str, expected_model: str
) -> None:
    workflow = parse_workflow_text(
        f"---\ntracker: {{kind: file, board_root: ./board}}\n{codex_yaml}\n---\nPrompt\n",
        tmp_path / "WORKFLOW.md",
    )
    cfg = build_service_config(workflow)

    async def on_event(event: dict) -> None:
        pass

    backend = CodexAppServerBackend(
        BackendInit(cfg=cfg, cwd=tmp_path, workspace_root=tmp_path, on_event=on_event)
    )
    backend._thread_id = "thread-1"
    params = backend._build_turn_params("hello")

    assert cfg.codex.model == expected_model
    if expected_model:
        assert params["model"] == expected_model
    else:
        assert "model" not in params
    assert params["effort"] == cfg.codex.reasoning_effort
