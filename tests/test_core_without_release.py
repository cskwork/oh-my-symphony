"""Core fails closed for app release work when the release distribution is absent.

`oh-my-symphony-release` (packages/release) contributes `symphony.release`.
These tests make that package unimportable in-process; CI also runs a
core-only wheel smoke.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from aiohttp.test_utils import TestClient, TestServer

import symphony
import symphony.orchestrator.core as core_mod
from symphony.cli import main as symphony_main
from symphony.cli.doctor import check_app_release_contract
from symphony.errors import SymphonyError
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator
from symphony.orchestrator.blocked_recovery import _blocked_rca_requires_operator_intervention
from symphony.orchestrator.release_seam import RELEASE_PARK_HEADING
from symphony.orchestrator.run_registry import ReleaseGate, RunRegistry
from symphony.server import build_app
from symphony.trackers.file import FileBoardTracker, parse_ticket_file, write_ticket_atomic
from symphony.workflow import WorkflowState
from tests.test_orchestrator_contract_integration import (
    _make_file_tracker_config,
    _orch,
    _seed_running_entry,
)
from tests.test_webapi import _StubOrchestrator, board_dir  # noqa: F401  (fixture)

INSTALL_COMMAND = "python -m pip install ./packages/release"


@pytest.fixture()
def without_release(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(sys.modules):
        if name == "symphony.release" or name.startswith("symphony.release."):
            monkeypatch.delitem(sys.modules, name)
    # A None entry makes both `import symphony.release` and find_spec report absence.
    monkeypatch.setitem(sys.modules, "symphony.release", None)
    assert not symphony.component_installed("release")


def test_release_is_reported_installed_in_the_dev_environment() -> None:
    assert symphony.component_installed("release")


def test_symphony_release_check_prints_install_hint_and_exits_2(
    without_release: None, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = symphony_main(["release", "check", "--ticket", "V-1", "--workspace", "."])
    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert len(err.strip().splitlines()) == 1
    assert INSTALL_COMMAND in err


# ---------------------------------------------------------------------------
# file board fixtures
# ---------------------------------------------------------------------------


def _board(tmp_path: Path):
    board = tmp_path / "kanban"
    cfg = _make_file_tracker_config(
        board_root=board, active_states=("Todo", "Build", "Verify"), max_turns=2
    )
    cfg = replace(
        cfg,
        workflow_path=tmp_path / "WORKFLOW.md",
        agent=replace(
            cfg.agent,
            stage_contracts="off",
            auto_commit_on_done=False,
            auto_merge_on_done=False,
        ),
    )
    tracker = FileBoardTracker(cfg.tracker)
    tracker.create(
        identifier="APP-1", title="Release verifier", state="Verify",
        labels=["app-release"], description="Verify the release.\n",
    )
    tracker.create(
        identifier="GATED-1", title="Verifier with durable authority", state="Verify",
        description="Labels were removed.\n",
    )
    tracker.create(
        identifier="ORD-1", title="Ordinary work", state="Verify",
        description="Nothing to release.\n",
    )
    tracker.close()
    orch = _orch(tmp_path / "ws")
    orch._ensure_run_registry_open_only(cfg)
    return orch, cfg


def _ticket(cfg, identifier: str) -> Issue:
    tracker = FileBoardTracker(cfg.tracker)
    try:
        issue = tracker.fetch_issue_full_by_id(identifier)
    finally:
        tracker.close()
    assert issue is not None
    return issue


def _seed_durable_gate(registry: RunRegistry, issue: Issue) -> None:
    registry.replace_pending_release_gate(
        ReleaseGate(
            finalizer_identifier="APP-FINAL",
            verifier_issue_id=issue.id,
            verifier_identifier=issue.identifier,
            expected_contract_sha256="a" * 64,
            cycle_fingerprint="b" * 64,
            approved_fingerprint=None,
            status="pending",
            target_branch=None,
            approved_target_sha=None,
            verifier_run_id=None,
            updated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
    )


def test_ordinary_ticket_gets_no_release_authority(
    without_release: None, tmp_path: Path
) -> None:
    orch, cfg = _board(tmp_path)
    issue = _ticket(cfg, "ORD-1")
    authority = orch._prepare_release_dispatch(issue, cfg)
    assert authority.issue is issue
    assert (authority.gate, authority.app_release, authority.finalizer) == (None, False, False)
    assert asyncio.run(orch._hold_release_ticket_without_component(issue, cfg)) is False
    assert asyncio.run(orch._startup_release_terminal_guard(cfg, issue)) == (issue, False, False)
    assert _ticket(cfg, "ORD-1").state == "Verify"


@pytest.mark.parametrize("identifier", ["APP-1", "GATED-1"])
def test_release_ticket_is_refused_and_parked_with_the_hint(
    without_release: None, tmp_path: Path, identifier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch, cfg = _board(tmp_path)
    assert orch._run_registry is not None
    _seed_durable_gate(orch._run_registry, _ticket(cfg, "GATED-1"))
    issue = _ticket(cfg, identifier)
    leases: list[str] = []
    monkeypatch.setattr(orch, "_try_acquire_run_lease", lambda **kw: leases.append("lease"))

    with pytest.raises(SymphonyError, match="packages/release"):
        orch._prepare_release_dispatch(issue, cfg)
    assert orch._dispatch(issue, cfg, attempt=None) is False
    assert leases == []

    assert asyncio.run(orch._hold_release_ticket_without_component(issue, cfg)) is True
    parked = _ticket(cfg, identifier)
    assert parked.state == "Blocked"
    assert f"## {RELEASE_PARK_HEADING}" in (parked.description or "")
    assert INSTALL_COMMAND in (parked.description or "")
    assert issue.id in orch._claimed
    # Blocked recovery must not reopen it: only installing the package helps.
    assert _blocked_rca_requires_operator_intervention(parked)
    # Terminal cleanup keeps the workspace of a release ticket.
    assert asyncio.run(orch._startup_release_terminal_guard(cfg, parked))[2] is True


def test_release_hold_is_a_no_op_with_the_package(tmp_path: Path) -> None:
    orch, cfg = _board(tmp_path)
    issue = _ticket(cfg, "APP-1")
    assert asyncio.run(orch._hold_release_ticket_without_component(issue, cfg)) is False
    assert _ticket(cfg, "APP-1").state == "Verify"


@dataclass
class _LabelingBackend:
    """Agent that labels its ticket for app release and moves it to Done."""

    ticket_path: Path
    turns: int = 0

    async def start(self) -> None: ...
    async def initialize(self) -> None: ...

    async def start_session(self, *, initial_prompt: str, issue_title: str) -> None:
        self.session_id = "labeling-session"

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> None:
        self.turns += 1
        if self.turns > 1:
            return
        front, body = parse_ticket_file(self.ticket_path)
        front["state"] = "Done"
        front["labels"] = ["app-release"]
        write_ticket_atomic(self.ticket_path, front, body)

    async def stop(self) -> None: ...


def test_ticket_labeled_mid_run_is_parked_never_delivered(
    without_release: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch, cfg = _board(tmp_path)
    issue = _ticket(cfg, "ORD-1")
    ticket_path = cfg.tracker.board_root / "ORD-1.md"
    monkeypatch.setattr(core_mod, "build_backend", lambda _init: _LabelingBackend(ticket_path))
    delivered: list[Any] = []

    async def no_delivery(*args: Any, **kwargs: Any) -> Any:
        delivered.append((args, kwargs))
        raise AssertionError("release ticket must not be delivered without the package")

    monkeypatch.setattr(core_mod, "commit_workspace_on_done", no_delivery)
    monkeypatch.setattr(core_mod, "auto_merge_on_done_best_effort", no_delivery)
    _seed_running_entry(orch, issue, tmp_path / "ws")

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    parked = _ticket(cfg, "ORD-1")
    assert parked.state == "Blocked"
    assert INSTALL_COMMAND in (parked.description or "")
    assert delivered == []


# ---------------------------------------------------------------------------
# doctor and web
# ---------------------------------------------------------------------------


def _workflow(tmp_path: Path) -> WorkflowState:
    path = tmp_path / "WORKFLOW.md"
    path.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "  active_states: [Todo, Build, Verify]\n"
        "  terminal_states: [Done, Blocked]\n"
        "---\nWork on {{ issue.identifier }}.\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban").mkdir()
    return WorkflowState(path)


def test_doctor_passes_a_default_board_without_the_package(
    without_release: None, tmp_path: Path
) -> None:
    cfg, err = _workflow(tmp_path).reload()
    assert err is None and cfg is not None
    result = check_app_release_contract(cfg)
    assert result.status == "pass"


def test_doctor_fails_a_release_board_without_the_package(
    without_release: None, tmp_path: Path
) -> None:
    cfg, err = _workflow(tmp_path).reload()
    assert err is None and cfg is not None
    tracker = FileBoardTracker(cfg.tracker)
    tracker.create(identifier="APP-1", title="Release", state="Verify", labels=["app-release"])
    tracker.close()
    result = check_app_release_contract(cfg)
    assert result.status == "fail"
    assert INSTALL_COMMAND in result.message


async def test_board_reports_release_component_absent(
    without_release: None, board_dir: Path  # noqa: F811
) -> None:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    client = TestClient(TestServer(build_app(cast(Orchestrator, _StubOrchestrator(state)))))
    await client.start_server()
    try:
        board = await client.get("/api/v1/board")
        assert board.status == 200
        assert (await board.json())["board"]["components"]["release"] is False
    finally:
        await client.close()


def test_orchestrator_and_cli_paths_do_not_import_release(tmp_path: Path) -> None:
    import subprocess

    _workflow(tmp_path)
    code = (
        "import sys\n"
        "import symphony.orchestrator, symphony.webapi, symphony.cli.main, symphony.cli.doctor\n"
        "from symphony.workflow import WorkflowState\n"
        f"cfg, err = WorkflowState({str(tmp_path / 'WORKFLOW.md')!r}).reload()\n"
        "assert err is None, err\n"
        "print(sorted(name for name in sys.modules if name.startswith('symphony.release')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
