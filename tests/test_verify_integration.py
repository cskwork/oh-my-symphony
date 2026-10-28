"""Deterministic gates observe real worker edits through the file tracker."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import symphony.orchestrator.agent_phase as agent_phase_mod
from symphony.stats import StatsStore
from symphony.trackers.file import parse_ticket_file
from symphony.workflow import ServiceConfig

from tests.test_orchestrator_contract_integration import (
    _DOCUMENT_BODY_COMPLETE,
    _IN_PROGRESS_BODY_COMPLETE,
    _IN_PROGRESS_BODY_MISSING_DONE_SIGNALS,
    _has_contract_failure_heading,
    _install_file_tracker_backend,
    _make_file_tracker_config,
    _make_issue_from_disk,
    _orch,
    _seed_running_entry,
    _write_initial_ticket,
)


def _prepare(
    root: Path,
    *,
    state: str = "In Progress",
    body: str = _IN_PROGRESS_BODY_COMPLETE,
    command: str = "test -f proof.ok",
    active_states: tuple[str, ...] = ("In Progress", "Verify", "Document"),
    contracts: str = "on",
    timeout_ms: int = 1000,
    max_turns: int = 2,
    max_attempts: int = 3,
) -> tuple[ServiceConfig, Path, Path]:
    ticket = _write_initial_ticket(root / "board", state=state, body=body)
    base = _make_file_tracker_config(
        board_root=ticket.parent,
        active_states=active_states,
        max_turns=max_turns,
        max_attempts=max_attempts,
    )
    cfg = replace(
        base,
        workflow_path=root / "WORKFLOW.md",
        agent=replace(base.agent, stage_contracts=contracts),
        raw={"verify": {"timeout_ms": timeout_ms, "commands": {state: command}}},
    )
    workspace = root / "workspace"
    workspace.mkdir()
    # The existing section contract also requires a nonempty work artifact.
    work = workspace / "docs" / "MT-1" / "work"
    work.mkdir(parents=True)
    (work / "notes.md").write_text("reviewed\n", encoding="utf-8")
    return cfg, ticket, workspace


def _run_attempt(
    cfg: ServiceConfig,
    ticket: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    transitions: list[tuple[str, str]],
):
    front, body = parse_ticket_file(ticket)
    issue = _make_issue_from_disk(front["state"], body)
    backends = _install_file_tracker_backend(
        monkeypatch, ticket_path=ticket, transitions=transitions
    )
    orchestrator = _orch(workspace)
    orchestrator._stats = StatsStore(cfg.workflow_path.parent / ".symphony" / "stats.jsonl")
    _seed_running_entry(orchestrator, issue, workspace)

    asyncio.run(orchestrator._run_agent_attempt(issue, attempt=None, cfg=cfg))
    orchestrator._stats.flush()
    return orchestrator, backends


def _records(cfg: ServiceConfig, filename: str) -> list[dict]:
    path = cfg.workflow_path.parent / ".symphony" / filename
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_worker_forward_edit_rewinds_then_advances_after_proof_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ticket, workspace = _prepare(tmp_path)
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Verify", _IN_PROGRESS_BODY_COMPLETE)])
    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Failure\n" in body
    assert "Command: `test -f proof.ok`" in body
    assert "Exit code: 1" in body
    assert "Duration:" in body
    assert "Output (last 60 lines):" in body
    assert not _has_contract_failure_heading(body)

    trace = _records(cfg, "traces/MT-1.jsonl")
    assert len(trace) == 1
    assert trace[0].items() >= {
        "issue": "MT-1",
        "from": "in progress",
        "to": "verify",
        "command": "test -f proof.ok",
        "exit_code": 1,
        "timed_out": False,
        "passed": False,
    }.items()
    assert isinstance(trace[0]["duration_ms"], int)
    assert trace[0]["ts"]
    print("MT-1: worker In Progress -> Verify; verify exit=1; tracker -> In Progress")

    (workspace / "proof.ok").write_text("ok\n", encoding="utf-8")
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Verify", body)])
    front, _ = parse_ticket_file(ticket)
    assert front["state"] == "Verify"
    trace = _records(cfg, "traces/MT-1.jsonl")
    assert len(trace) == 2
    assert trace[1]["passed"] is True
    assert trace[1]["exit_code"] == 0
    events = [event for event in _records(cfg, "stats.jsonl") if event["type"] == "gate"]
    assert [event["kind"] for event in events] == ["verify_failure", "verify_pass"]
    assert all(event["state"] == "in progress" for event in events)
    print("MT-1: proof.ok created; worker In Progress -> Verify; verify exit=0; tracker -> Verify")


@pytest.mark.parametrize("command, expected", [("exit 9", "Build"), ("true", "Inspect")])
def test_custom_lane_gate_works_with_contracts_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, expected: str
) -> None:
    cfg, ticket, workspace = _prepare(
        tmp_path, state="Build", body="custom lane body", command=command,
        active_states=("Build", "Inspect"), contracts="off",
    )
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Inspect", "custom lane body")])
    front, body = parse_ticket_file(ticket)
    assert front["state"] == expected
    assert ("\n## Verify Failure\n" in body) == (expected == "Build")
    assert not _has_contract_failure_heading(body)
    assert len(_records(cfg, "traces/MT-1.jsonl")) == 1


def test_timeout_rewinds_ticket_and_records_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ticket, workspace = _prepare(tmp_path, command="sleep 30", timeout_ms=50)
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Verify", _IN_PROGRESS_BODY_COMPLETE)])
    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Failure\n" in body
    assert "timed out after 0.05 s" in body
    result = _records(cfg, "traces/MT-1.jsonl")[0]
    assert result["timed_out"] is True
    assert result["passed"] is False


@pytest.mark.parametrize(
    "command, body, failure",
    [
        ("true", _IN_PROGRESS_BODY_MISSING_DONE_SIGNALS, "Contract Failure"),
        ("exit 7", _IN_PROGRESS_BODY_COMPLETE, "Verify Failure"),
    ],
)
def test_both_contract_and_command_must_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    command: str, body: str, failure: str,
) -> None:
    cfg, ticket, workspace = _prepare(tmp_path, command=command, body=body)
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Verify", body)])
    front, final_body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert f"\n## {failure}\n" in final_body
    assert len(_records(cfg, "traces/MT-1.jsonl")) == 1


@pytest.mark.parametrize("command, expected", [("exit 4", "Document"), ("true", "Done")])
def test_done_transition_is_deterministically_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, expected: str
) -> None:
    cfg, ticket, workspace = _prepare(
        tmp_path, state="Document", body=_DOCUMENT_BODY_COMPLETE, command=command
    )
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Done", _DOCUMENT_BODY_COMPLETE)])
    front, body = parse_ticket_file(ticket)
    assert front["state"] == expected
    assert not _has_contract_failure_heading(body)
    assert ("\n## Verify Failure\n" in body) == (expected == "Document")
    assert len(_records(cfg, "traces/MT-1.jsonl")) == 1


def test_repeated_verify_failures_obey_existing_rewind_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ticket, workspace = _prepare(
        tmp_path, command="exit 2", max_turns=10, max_attempts=1
    )
    orchestrator, backends = _run_attempt(
        cfg, ticket, workspace, monkeypatch,
        [("Verify", _IN_PROGRESS_BODY_COMPLETE)] * 10,
    )
    front, _ = parse_ticket_file(ticket)
    assert front["state"] == "Blocked"
    assert orchestrator._issue_debug["MT-1"].rewind_count == 2
    assert len(_records(cfg, "traces/MT-1.jsonl")) == 2
    assert sum(name == "run_turn" for backend in backends for name, _ in backend.calls) == 2


def test_backward_worker_edit_does_not_run_forward_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ticket, workspace = _prepare(
        tmp_path, state="Verify", body="rewind", command="exit 3", contracts="off"
    )
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("In Progress", "rewind")])
    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Failure\n" not in body
    assert not (tmp_path / ".symphony" / "traces" / "MT-1.jsonl").exists()


def test_verify_command_gets_ticket_workspace_and_hook_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = (
        'test "$SYMPHONY_ISSUE_ID" = MT-1 && '
        'test "$SYMPHONY_ISSUE_IDENTIFIER" = MT-1 && '
        'test "$SYMPHONY_FROM_STATE" = "In Progress" && '
        'test "$SYMPHONY_TO_STATE" = Verify && '
        'test "$PWD" = "$SYMPHONY_WORKFLOW_DIR/workspace" && '
        'test -x "$SYMPHONY_PYTHON" && '
        'test "$SYMPHONY_WORKSPACE_ROOT" = "$SYMPHONY_WORKFLOW_DIR/ws"'
    )
    cfg, ticket, workspace = _prepare(tmp_path, command=command)
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Verify", _IN_PROGRESS_BODY_COMPLETE)])
    front, body = parse_ticket_file(ticket)
    assert front["state"] == "Verify", body
    assert _records(cfg, "traces/MT-1.jsonl")[0]["passed"] is True


@pytest.mark.parametrize("exit_code", [0, 4])
async def test_host_verification_is_marked_in_progress_until_gate_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int,
) -> None:
    from symphony.orchestrator import agent_phase
    from symphony.workflow.verify import VerifyResult, verification_in_progress
    from symphony.trackers.file import write_ticket_atomic

    cfg, ticket, workspace = _prepare(
        tmp_path, state="Build", body="custom", command="proof",
        active_states=("Build", "Inspect"), contracts="off",
    )
    issue = _make_issue_from_disk("Build", "custom")
    orchestrator = _orch(workspace)
    _seed_running_entry(orchestrator, issue, workspace)
    front, body = parse_ticket_file(ticket)
    front["state"] = "Inspect"
    write_ticket_atomic(ticket, front, body)
    started, release = threading.Event(), threading.Event()

    def held_command(*args):
        started.set()
        if not release.wait(5):
            raise TimeoutError("test did not release verification")
        return VerifyResult(exit_code, False, 1, "proof output")

    monkeypatch.setattr(agent_phase, "run_verify", held_command)
    task = asyncio.create_task(orchestrator._enforce_stage_contract(
        cfg=cfg, running_issue_id=issue.id, issue=issue,
        producing_state="build", producing_state_raw="Build", current_state="inspect",
        workspace_path=workspace, known_app_release=False,
    ))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        assert verification_in_progress(orchestrator, issue.id)
        assert not task.done()
    finally:
        release.set()
    outcome = await task
    assert outcome.rewound is (exit_code != 0)
    assert not verification_in_progress(orchestrator, issue.id)


def test_trace_write_error_does_not_bypass_failed_gate_rewind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ticket, workspace = _prepare(tmp_path, command="exit 6")

    def trace_unwritable(*_args: object) -> None:
        raise OSError("trace disk unavailable")

    monkeypatch.setattr(agent_phase_mod, "append_verify_trace", trace_unwritable)
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Verify", _IN_PROGRESS_BODY_COMPLETE)])
    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Failure\n" in body
    assert "Exit code: 6" in body
    assert any(
        event.get("kind") == "verify_failure" for event in _records(cfg, "stats.jsonl")
    )


@pytest.mark.parametrize("target", ["Document", "Done"])
@pytest.mark.parametrize("command, gated", [("exit 1", True), ("true", False)])
def test_skipping_a_lane_still_runs_its_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    command: str,
    gated: bool,
) -> None:
    """A worker jumping over Verify must still pass the Verify lane's command."""
    cfg, ticket, workspace = _prepare(tmp_path, contracts="off")
    cfg = replace(cfg, raw={"verify": {"timeout_ms": 1000, "commands": {"Verify": command}}})

    _run_attempt(cfg, ticket, workspace, monkeypatch, [(target, _IN_PROGRESS_BODY_COMPLETE)])

    front, body = parse_ticket_file(ticket)
    assert front["state"] == ("In Progress" if gated else target)
    assert ("\n## Verify Failure\n" in body) == gated
    if gated:
        assert "Gate of the skipped lane `Verify`." in body
    (trace,) = _records(cfg, "traces/MT-1.jsonl")
    assert trace["command"] == command
    assert trace["passed"] is not gated


def test_crash_after_moving_forward_still_faces_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-cancel exit (crash) after a forward edit runs the exit gate."""
    from symphony.orchestrator import worker_exit

    cfg, ticket, workspace = _prepare(tmp_path, command="exit 3", contracts="off")
    orchestrator = _orch(workspace)
    issue = _make_issue_from_disk("In Progress", _IN_PROGRESS_BODY_COMPLETE)
    _seed_running_entry(orchestrator, issue, workspace)
    entry = orchestrator._running[issue.id]
    entry.state_at_turn_start = "in progress"
    entry.workspace_path = workspace
    front, body = parse_ticket_file(ticket)
    ticket.write_text(ticket.read_text().replace("state: In Progress", "state: Done", 1))
    monkeypatch.setattr(orchestrator._workflow_state, "current", lambda: cfg)

    asyncio.run(worker_exit.handle_worker_exit(orchestrator, issue.id, "error", "boom"))

    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Failure\n" in body


def test_cancelled_exit_leaves_gating_to_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.orchestrator import worker_exit

    cfg, ticket, workspace = _prepare(tmp_path, command="exit 3", contracts="off")
    orchestrator = _orch(workspace)
    issue = _make_issue_from_disk("In Progress", _IN_PROGRESS_BODY_COMPLETE)
    _seed_running_entry(orchestrator, issue, workspace)
    calls: list[str] = []

    async def record(cfg, entry):
        calls.append(entry.issue.id)
        return True

    monkeypatch.setattr(orchestrator, "_enforce_factory_exit_contract", record)
    monkeypatch.setattr(orchestrator._workflow_state, "current", lambda: cfg)

    asyncio.run(worker_exit.handle_worker_exit(orchestrator, issue.id, "cancelled", None))

    assert calls == []
