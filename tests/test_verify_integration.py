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


def _forward_edited_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str):
    cfg, ticket, workspace = _prepare(tmp_path, command="exit 3", contracts="off")
    orchestrator = _orch(workspace)
    issue = _make_issue_from_disk("In Progress", _IN_PROGRESS_BODY_COMPLETE)
    _seed_running_entry(orchestrator, issue, workspace)
    entry = orchestrator._running[issue.id]
    entry.state_at_turn_start = "in progress"
    entry.workspace_path = workspace
    ticket.write_text(ticket.read_text().replace("state: In Progress", f"state: {target}", 1))
    monkeypatch.setattr(orchestrator._workflow_state, "current", lambda: cfg)
    return cfg, ticket, workspace, orchestrator, issue


@pytest.mark.parametrize("target", ["Verify", "Done"])
def test_stall_cancel_after_a_forward_edit_still_faces_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A stall cancellation must not let the next dispatch start past the gate."""
    from symphony.orchestrator import worker_exit

    cfg, ticket, workspace, orchestrator, issue = _forward_edited_entry(
        tmp_path, monkeypatch, target
    )

    asyncio.run(worker_exit.handle_worker_exit(orchestrator, issue.id, "cancelled", None))

    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Failure\n" in body
    # The re-dispatch starts from the gated lane, so it cannot reach Done unchecked.
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Done", body)])
    assert parse_ticket_file(ticket)[0]["state"] != "Done"


def test_shutdown_exit_does_not_run_verify_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.orchestrator import worker_exit

    cfg, ticket, workspace, orchestrator, issue = _forward_edited_entry(
        tmp_path, monkeypatch, "Verify"
    )
    calls: list[str] = []

    async def record(cfg, entry):
        calls.append(entry.issue.id)
        return True

    monkeypatch.setattr(orchestrator, "_enforce_factory_exit_contract", record)

    asyncio.run(
        worker_exit.handle_worker_exit(orchestrator, issue.id, "shutdown_interrupted", None)
    )

    assert calls == []


def test_exit_after_reconcile_started_cleanup_is_not_gated_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.orchestrator import worker_exit

    cfg, ticket, workspace, orchestrator, issue = _forward_edited_entry(
        tmp_path, monkeypatch, "Done"
    )
    orchestrator._running[issue.id].workspace_cleanup_started = True
    calls: list[str] = []

    async def record(cfg, entry):
        calls.append(entry.issue.id)
        return True

    monkeypatch.setattr(orchestrator, "_enforce_factory_exit_contract", record)

    asyncio.run(worker_exit.handle_worker_exit(orchestrator, issue.id, "cancelled", None))

    assert calls == []


def test_shutdown_after_a_forward_edit_is_gated_on_the_next_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shutdown skips exit commands, so the restart must gate the lane left."""
    from symphony.orchestrator import worker_exit
    from symphony.workflow.verify import gate_lane_of, remember_gate_lane

    cfg, ticket, workspace, orchestrator, issue = _forward_edited_entry(
        tmp_path, monkeypatch, "Verify"
    )
    # The turn that made the edit started in In Progress and recorded it.
    remember_gate_lane(cfg, "MT-1", "in progress")
    orchestrator._stopping = True

    asyncio.run(
        worker_exit.handle_worker_exit(orchestrator, issue.id, "shutdown_interrupted", None)
    )

    assert parse_ticket_file(ticket)[0]["state"] == "Verify"
    assert gate_lane_of(cfg, "MT-1") == "in progress"

    front, body = parse_ticket_file(ticket)
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Done", body)])

    front, body = parse_ticket_file(ticket)
    assert front["state"] != "Done"
    assert "\n## Verify Failure\n" in body
    traces = _records(cfg, "traces/MT-1.jsonl")
    # The restart gate ran before any new turn: In Progress -> Verify, failed.
    assert (traces[0]["from"], traces[0]["to"], traces[0]["passed"]) == (
        "in progress", "verify", False
    )
    assert not any(trace["passed"] for trace in traces)


def test_turns_record_their_gated_lane_and_handled_exits_forget_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony.orchestrator import worker_exit
    from symphony.workflow.verify import gate_lane_of

    cfg, ticket, workspace = _prepare(tmp_path, command="true", contracts="off")
    seen: list[str | None] = []
    real_run_turn = agent_phase_mod.agent_attempt._run_turn

    async def spy(orch, st, prompt, is_continuation):
        seen.append(gate_lane_of(cfg, "MT-1"))
        return await real_run_turn(orch, st, prompt, is_continuation)

    monkeypatch.setattr(agent_phase_mod.agent_attempt, "_run_turn", spy)
    orchestrator, _ = _run_attempt(
        cfg, ticket, workspace, monkeypatch, [("Verify", _IN_PROGRESS_BODY_COMPLETE)]
    )

    # Turn 1 starts in the gated lane; turn 2 starts in Verify, which has no gate.
    assert seen == ["in progress", None]
    # The gate passed on the forward move, so nothing is left to re-check.
    assert gate_lane_of(cfg, "MT-1") is None
    assert parse_ticket_file(ticket)[0]["state"] == "Verify"

    from symphony.workflow.verify import remember_gate_lane

    remember_gate_lane(cfg, "MT-1", "in progress")
    issue = _make_issue_from_disk("Verify", _IN_PROGRESS_BODY_COMPLETE)
    _seed_running_entry(orchestrator, issue, workspace)
    monkeypatch.setattr(orchestrator._workflow_state, "current", lambda: cfg)
    asyncio.run(worker_exit.handle_worker_exit(orchestrator, issue.id, "normal", None))
    assert gate_lane_of(cfg, "MT-1") is None


def test_a_rewound_lane_is_recorded_again_after_an_in_run_pass(tmp_path: Path) -> None:
    """A passed gate clears the record; a later turn back in the lane rewrites it."""
    from types import SimpleNamespace

    from symphony.orchestrator import attempt
    from symphony.workflow.verify import gate_lane_of, remember_gate_lane

    cfg, _, _ = _prepare(tmp_path, command="exit 3", contracts="off")
    st = SimpleNamespace(
        cfg=cfg,
        issue=_make_issue_from_disk("In Progress", "x"),
        current_state="in progress",
    )
    asyncio.run(attempt._remember_turn_lane(st))
    assert gate_lane_of(cfg, "MT-1") == "in progress"
    remember_gate_lane(cfg, "MT-1", None)  # the crossed gates passed
    st.current_state = "verify"
    asyncio.run(attempt._remember_turn_lane(st))
    st.current_state = "in progress"  # rewound back into the gated lane
    asyncio.run(attempt._remember_turn_lane(st))
    assert gate_lane_of(cfg, "MT-1") == "in progress"


def test_shutdown_after_a_move_to_done_returns_the_card_to_its_gated_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Done is never dispatched again, so the skipped gate needs the card back."""
    from symphony.orchestrator import worker_exit
    from symphony.workflow.verify import gate_lane_of

    cfg, ticket, workspace, orchestrator, issue = _forward_edited_entry(
        tmp_path, monkeypatch, "Done"
    )
    orchestrator._stopping = True

    asyncio.run(
        worker_exit.handle_worker_exit(orchestrator, issue.id, "shutdown_interrupted", None)
    )

    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Pending\n" in body
    assert gate_lane_of(cfg, "MT-1") == "in progress"
    # The next forward move runs the gate it skipped.
    _run_attempt(cfg, ticket, workspace, monkeypatch, [("Done", body)])
    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Failure\n" in body


def test_startup_returns_a_crashed_done_move_to_its_gated_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host crash skips the exit handler; startup reads the record instead."""
    from symphony.orchestrator import retry_reconcile
    from symphony.workflow.verify import remember_gate_lane

    cfg, ticket, workspace = _prepare(tmp_path, command="exit 3", contracts="off")
    ticket.write_text(ticket.read_text().replace("state: In Progress", "state: Done", 1))
    remember_gate_lane(cfg, "MT-1", "in progress")
    orchestrator = _orch(workspace)
    front, body = parse_ticket_file(ticket)
    done = _make_issue_from_disk("Done", body)

    assert asyncio.run(retry_reconcile._return_unverified_done(orchestrator, cfg, done))

    front, body = parse_ticket_file(ticket)
    assert front["state"] == "In Progress"
    assert "\n## Verify Pending\n" in body
