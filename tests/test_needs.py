"""Operator needs are durable projections, never tracker-state authority."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from symphony import needs as needs_module
from symphony.cli import board
from symphony.issue import Issue
from symphony.needs import load_needs, needs_path, refresh_needs
from symphony.notifications import NotificationEvent, NotificationsConfig, SlackConfig
from symphony.notifications.dispatcher import NotificationDispatcher
from symphony.orchestrator import Orchestrator
from symphony.trackers.file import FileBoardTracker
from symphony.workflow import ServiceConfig, WorkflowState
from tests.test_orchestrator_dispatch import _make_config

NOW = datetime(2026, 10, 28, 8, tzinfo=timezone.utc)


def _cfg(tmp_path: Path, *, notify: bool = False) -> ServiceConfig:
    cfg = _make_config(workflow_path=tmp_path / "WORKFLOW.md", tracker_kind="file")
    return replace(
        cfg,
        agent=replace(cfg.agent, budget_exhausted_state="Human Review"),
        tracker=replace(cfg.tracker, board_root=tmp_path / "board",
                        terminal_states=("Done", "Blocked", "Human Review"),
                        archive_after_days=0),
        notifications=NotificationsConfig(slack=SlackConfig("https://invalid.test"))
        if notify else NotificationsConfig(),
    )


def _issue(identifier: str = "T-1", *, state: str = "Blocked", description: str = "") -> Issue:
    return Issue(id=identifier, identifier=identifier, title=identifier,
                 state=state, description=description, priority=None)


def test_create_update_remove_and_preserve_since(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    issues = [_issue(), _issue("T-2", state="Human Review"), _issue("T-3", state="Done")]
    first = refresh_needs(cfg, issues, now=NOW)
    assert first == {
        identifier: {"state": state, "since": "2026-10-28T08:00:00Z",
                     "reason": "unknown", "action": "open the ticket"}
        for identifier, state in (("T-1", "Blocked"), ("T-2", "Human Review"))
    }
    assert needs_path(cfg.workflow_path) == tmp_path / ".symphony/needs.json"
    assert load_needs(cfg.workflow_path) == first
    second = refresh_needs(cfg, [_issue(description="## Blocked\nMissing access")],
                           now=NOW + timedelta(hours=1))
    assert second["T-1"]["since"] == first["T-1"]["since"]
    assert second["T-1"]["reason"] == "Missing access"
    assert "T-2" not in second
    assert refresh_needs(cfg, [_issue(state="Todo")], now=NOW) == {}
    assert load_needs(cfg.workflow_path) == {}


@pytest.mark.parametrize("description,reason,action", [
    ("## Verify Failure\nCommand: `old`\n## Environment Block\nInstall SDK\nDetails",
     "Install SDK", "Details"),
    ("## Contract Failure\nOld failure\n## Verify Failure\nCommand: `pytest -q`\nExit code: 1\n"
     "Duration: 50 ms\n```\n## Blocked\noutput heading is not a blocker\n```",
     "verify command failed: pytest -q", "make this command pass: pytest -q"),
    ("## Blocked\nOld\n## Contract Failure\nMissing acceptance rows\nSecond line",
     "Missing acceptance rows", "Second line"),
    ("## Verify Failure\nCommand: `first`\n## Verify Failure\nCommand: `last`",
     "verify command failed: last", "make this command pass: last"),
    ("## Environment Block\n", "unknown", "open the ticket"),
    ("## Environment Block\nReason: SDK missing\nAction: Install SDK\nExtra details",
     "SDK missing", "Install SDK"),
    ("## Blocked\nReason: no access\nAction: grant access\n"
     "    ## Verify Failure\n    Command: `forged`",
     "no access", "grant access"),
])
def test_latest_failure_section(tmp_path: Path, description: str, reason: str, action: str) -> None:
    need = refresh_needs(_cfg(tmp_path), [_issue(description=description)], now=NOW)["T-1"]
    assert (need["reason"], need["action"]) == (reason, action)


def test_only_configured_budget_state_or_terminal_blocked(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg = replace(cfg, agent=replace(cfg.agent, budget_exhausted_state="Waiting"),
                  tracker=replace(cfg.tracker, terminal_states=("Done",)))
    result = refresh_needs(cfg, [_issue(), _issue("T-2", state="Waiting")], now=NOW)
    assert set(result) == {"T-2"}


def test_notify_group_once_each_utc_day_across_reloads(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path, notify=True)
    events: list[NotificationEvent] = []
    monkeypatch.setattr(needs_module, "dispatch_notification",
                        lambda config, event: events.append(event) or True)
    issues = [_issue("T-2", description="## Environment Block\nMissing SDK"),
              _issue("T-1", description="## Blocked\nMissing SDK"),
              _issue("T-3", description="## Contract Failure\nMissing evidence")]
    refresh_needs(cfg, issues, now=NOW)
    assert len(events) == 2
    assert events[0].identifier == "T-1, T-2"
    assert events[0].reason == "Missing SDK"
    assert "open the ticket" in events[0].title
    # No in-memory cache participates: a fresh config uses persisted sent groups.
    refresh_needs(replace(cfg), issues, now=NOW + timedelta(hours=1))
    assert len(events) == 2
    refresh_needs(cfg, issues, now=NOW + timedelta(days=1))
    assert len(events) == 4
    # Input timezone must not advance the UTC notification date prematurely.
    refresh_needs(cfg, issues,
                  now=(NOW + timedelta(days=1)).astimezone(timezone(timedelta(hours=14))))
    assert len(events) == 4


@pytest.mark.parametrize("raises", [False, True])
def test_notification_failure_logs_and_retries_without_losing_needs(
    tmp_path: Path, monkeypatch, raises: bool,
) -> None:
    cfg = _cfg(tmp_path, notify=True)
    events: list[NotificationEvent] = []
    warnings: list[str] = []

    def fail(config, event):
        events.append(event)
        if raises:
            raise RuntimeError("offline")
        return False

    monkeypatch.setattr(needs_module, "dispatch_notification", fail)
    monkeypatch.setattr(needs_module.log, "warning", lambda name, **kw: warnings.append(name))
    assert refresh_needs(cfg, [_issue()], now=NOW)["T-1"]["reason"] == "unknown"
    refresh_needs(cfg, [_issue()], now=NOW)
    assert len(events) == 2
    assert warnings == ["needs_notification_failed", "needs_notification_failed"]
    assert not (tmp_path / ".symphony/needs-sent.json").exists()


def test_dispatcher_reports_delivery_without_propagating_channel_errors() -> None:
    class Channel:
        def __init__(self, response):
            self.response = response

        def notify(self, event):
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

    event = NotificationEvent("T-1", "help", "", "Blocked", "demo")
    assert NotificationDispatcher([Channel(False)]).dispatch(event) is False
    assert NotificationDispatcher([Channel(RuntimeError("bad")), Channel(True)]).dispatch(event) is True


async def test_each_tick_keeps_one_pending_sweep(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    from symphony.orchestrator import sweeps

    cfg = _cfg(tmp_path)
    orch = Orchestrator(WorkflowState(cfg.workflow_path))
    release = asyncio.Event()
    calls = []

    async def wait_for_release(self, cfg):
        calls.append("started")
        await release.wait()

    monkeypatch.setattr(sweeps, "_needs_sweep", wait_for_release)
    orch._maybe_schedule_needs_sweep(cfg)
    first = orch._needs_sweep_task
    assert first in orch._background_tasks
    await asyncio.sleep(0)
    orch._maybe_schedule_needs_sweep(cfg)
    assert orch._needs_sweep_task is first
    assert calls == ["started"]
    release.set()
    await first
    orch._maybe_schedule_needs_sweep(cfg)
    second = orch._needs_sweep_task
    assert second is not first
    await second


async def test_real_tick_refreshes_needs_with_archive_and_ci_disabled(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.create(identifier="T-1", title="needs help", state="Blocked",
                   description="## Verify Failure\nCommand: `pytest`\nExit code: 1")
    orch = Orchestrator(WorkflowState(cfg.workflow_path))
    monkeypatch.setattr(orch._workflow_state, "reload", lambda: (cfg, None))

    async def noop(*args):
        return None

    async def no_candidates(*args):
        return []

    for name in ("_reconcile_running", "_auto_reopen_sources_from_resolved_rcas",
                 "_auto_recover_blocked_sources", "_ensure_run_registry_async"):
        monkeypatch.setattr(orch, name, noop)
    monkeypatch.setattr(orch, "_fetch_candidates", no_candidates)
    await orch._on_tick()
    await getattr(orch, "_needs_sweep_task")
    assert load_needs(cfg.workflow_path)["T-1"]["action"] == "make this command pass: pytest"
    # A second immediate tick must remove a recovered ticket despite archive throttling.
    tracker.transition("T-1", "Done")
    await orch._on_tick()
    await getattr(orch, "_needs_sweep_task")
    assert load_needs(cfg.workflow_path) == {}
    tracker.close()


async def test_sweep_failure_preserves_previous_snapshot(tmp_path: Path, monkeypatch) -> None:
    from symphony.orchestrator import core, sweeps

    cfg = _cfg(tmp_path)
    previous = refresh_needs(cfg, [_issue()], now=NOW)
    monkeypatch.setattr(core, "build_tracker_client", lambda cfg: (_ for _ in ()).throw(OSError("offline")))
    await sweeps._needs_sweep(Orchestrator(WorkflowState(cfg.workflow_path)), cfg)
    assert load_needs(cfg.workflow_path) == previous


async def test_sweep_reuses_terminal_tracker_read(tmp_path: Path, monkeypatch) -> None:
    from symphony.orchestrator import core, sweeps

    cfg = _cfg(tmp_path)
    orch = Orchestrator(WorkflowState(cfg.workflow_path))
    monkeypatch.setattr(orch, "_tracker_call_terminal_issues", lambda cfg: [_issue()])
    monkeypatch.setattr(core, "build_tracker_client", lambda cfg: pytest.fail("extra tracker client"))
    await sweeps._needs_sweep(orch, cfg)
    assert "T-1" in load_needs(cfg.workflow_path)


def test_board_show_needs_line_only_when_present(tmp_path: Path, capsys) -> None:
    cfg = _cfg(tmp_path)
    tracker = FileBoardTracker(cfg.tracker)
    tracker.create(identifier="T-1", title="help", state="Blocked", description="body")
    options = ["show", "T-1", "--root", str(cfg.tracker.board_root),
               "--workflow", str(cfg.workflow_path)]
    assert board.main(options) == 0
    assert "needs:" not in capsys.readouterr().out
    refresh_needs(cfg, [_issue(description="## Blocked\nMissing access")], now=NOW)
    assert board.main(options) == 0
    assert "needs: Missing access; action: open the ticket (since 2026-10-28T08:00:00Z)" in capsys.readouterr().out
    tracker.close()


def test_alternate_workflow_uses_exact_needs_path(tmp_path: Path) -> None:
    cfg = replace(_cfg(tmp_path), workflow_path=tmp_path / "custom.md")
    refresh_needs(cfg, [_issue()], now=NOW)
    assert (tmp_path / ".symphony/needs.json").exists()
    assert load_needs(cfg.workflow_path)["T-1"]["action"] == "open the ticket"


def test_multiline_verify_command_remains_actionable(tmp_path: Path) -> None:
    from symphony.workflow.verify import VerifyResult

    command = "python -m pytest -q\nruff check ."
    note = VerifyResult(1, False, 5, "failed").failure_note(command, 1000)
    need = refresh_needs(
        _cfg(tmp_path), [_issue(description=f"## Verify Failure\n{note}")], now=NOW,
    )["T-1"]
    assert need["action"] == f"make this command pass: {command}"
