"""Prime Agent clean-exit handling after a terminal file-board update."""

from pathlib import Path

import pytest

import symphony.backends.per_turn as per_turn_module
from symphony.backends import EVENT_TURN_COMPLETED, BackendInit
from symphony.backends.prime_agent import PrimeAgentBackend
from symphony.errors import TurnFailed
from symphony.workflow import build_service_config, parse_workflow_text
from tests.test_backends import _FakeSubprocess, _install_subprocess_double


def _config(tmp_path: Path):
    workflow_path = tmp_path / "WORKFLOW.md"
    workflow_path.write_text(
        "---\n"
        "tracker: {kind: file, board_root: ./board}\n"
        "workspace: {root: ./workspaces}\n"
        "agent: {kind: prime-agent}\n"
        "---\nprompt\n"
    )
    return build_service_config(
        parse_workflow_text(workflow_path.read_text(), workflow_path)
    )


def _write_ticket(path: Path, *, state: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: TASK-1\ntitle: Terminal completion\nstate: {state}\n---\n"
    )


async def _run_empty_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, rc: int):
    config = _config(tmp_path)
    cwd = config.workspace_root / "TASK-1"
    cwd.mkdir(parents=True)
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    _install_subprocess_double(
        monkeypatch,
        per_turn_module,
        [
            _FakeSubprocess(
                stdout_lines=[b'{"type":"session","id":"s1"}\n'], returncode=rc
            )
        ],
    )
    backend = PrimeAgentBackend(
        BackendInit(
            cfg=config,
            cwd=cwd,
            workspace_root=config.workspace_root,
            on_event=collect,
        )
    )
    await backend.start_session(initial_prompt="", issue_title="Terminal completion")
    result = await backend.run_turn(prompt="work", is_continuation=False)
    return result, events


@pytest.mark.asyncio
async def test_clean_empty_exit_completes_when_file_ticket_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _write_ticket(config.tracker.board_root / "TASK-1.md", state="Done")

    result, events = await _run_empty_turn(tmp_path, monkeypatch, rc=0)

    assert result.status == EVENT_TURN_COMPLETED
    completed = [event for event in events if event["event"] == EVENT_TURN_COMPLETED]
    assert completed[-1]["payload"]["synthetic_reason"] == (
        "ticket_terminal_without_agent_end"
    )


@pytest.mark.asyncio
async def test_clean_empty_exit_still_fails_when_file_ticket_is_nonterminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _write_ticket(config.tracker.board_root / "TASK-1.md", state="In Progress")

    with pytest.raises(TurnFailed, match="no agent_end event"):
        await _run_empty_turn(tmp_path, monkeypatch, rc=0)


@pytest.mark.asyncio
async def test_terminal_ticket_does_not_mask_nonzero_prime_agent_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _write_ticket(config.tracker.board_root / "TASK-1.md", state="Done")

    with pytest.raises(TurnFailed, match="exited with code 9"):
        await _run_empty_turn(tmp_path, monkeypatch, rc=9)
