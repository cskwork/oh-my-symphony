"""Read-only execution limits preserve guard counters and unknown values."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_orchestrator_dispatch import _make_config, _orch, _issue, _install_running_entry
from symphony.orchestrator.entries import _IssueDebug


def setup_budget():
    cfg = _make_config()
    cfg = replace(cfg, agent=replace(cfg.agent, max_turns=5, max_total_turns=20,
        max_total_tokens=1000, max_retries=3, max_attempts=2, max_reopens=2))
    orch = _orch()
    orch._workflow_state._config = cfg
    issue = _issue("BUDGET-1")
    return orch, cfg, issue


async def test_live_budgets_use_guard_counters_and_inclusive_token_total():
    orch, cfg, issue = setup_budget()
    entry = _install_running_entry(orch, issue)
    entry.turn_count = 2
    entry.attempt_kind = "retry"
    entry.retry_attempt = 2
    entry.codex_state_total_tokens = 600
    entry.last_reported_total_tokens = 600
    orch._issue_debug[issue.id] = _IssueDebug(completed_turn_count=4, rewind_count=1)
    orch._run_registry = SimpleNamespace(recent_runs=lambda *a, **kw: [SimpleNamespace(state="Done")])
    result = await orch.issue_budget_snapshot(issue)
    rows = {row["name"]: row for row in result["items"]}
    assert {name: row["remaining"] for name, row in rows.items()} == {
        "attempt_turns": 3, "total_turns": 14, "state_tokens": 400,
        "retries": 1, "rewinds": 1, "reopens": 1,
    }
    assert issue.id in orch._running  # A projection never stops or dispatches work.


async def test_unknown_usage_and_disabled_caps_are_distinct():
    orch, cfg, issue = setup_budget()
    result = await orch.issue_budget_snapshot(issue)
    assert all(row["status"] == "unknown" and row["remaining"] is None for row in result["items"])
    orch._workflow_state._config = replace(cfg, agent=replace(cfg.agent,
        max_total_turns=0, max_total_tokens=0, max_retries=0, max_attempts=0, max_reopens=0))
    result = await orch.issue_budget_snapshot(replace(issue, description="## Reopen Approved\nApproved."))
    rows = {row["name"]: row for row in result["items"]}
    assert rows["attempt_turns"]["status"] == "unknown"
    assert all(row["status"] == "disabled" and row["remaining"] is None
               for name, row in rows.items() if name != "attempt_turns")


@pytest.mark.parametrize("done_runs,running,approved,remaining", [
    (0, False, False, 2), (1, False, False, 2), (1, True, False, 1),
    (3, False, False, 0), (3, False, True, 1), (200, False, False, None),
])
async def test_reopen_counts_exclude_initial_completion_and_reject_truncated_history(
    done_runs, running, approved, remaining
):
    orch, cfg, issue = setup_budget()
    orch._run_registry = SimpleNamespace(recent_runs=lambda *a, **kw: [SimpleNamespace(state="Done")] * done_runs)
    if running:
        _install_running_entry(orch, issue)
    if approved:
        issue = replace(issue, description="## Reopen Approved\nOne extra cycle.")
    result = await orch.issue_budget_snapshot(issue)
    row = next(row for row in result["items"] if row["name"] == "reopens")
    assert row["remaining"] == remaining
    assert row["status"] == ("unknown" if remaining is None else "exhausted" if remaining == 0 else "remaining")


async def test_reached_retry_and_rewind_caps_do_not_stop_current_work():
    orch, cfg, issue = setup_budget()
    entry = _install_running_entry(orch, issue)
    entry.attempt_kind = "retry"
    entry.retry_attempt = cfg.agent.max_retries
    orch._issue_debug[issue.id] = _IssueDebug(rewind_count=cfg.agent.max_attempts)
    result = await orch.issue_budget_snapshot(issue)
    rows = {row["name"]: row for row in result["items"]}
    assert rows["retries"]["remaining"] == rows["rewinds"]["remaining"] == 0
    assert rows["state_tokens"]["status"] == "unknown"  # No token notification yet.
    assert issue.id in orch._running and not orch._turn_budget_exhausted


async def test_budget_read_does_not_double_count_a_worker_finishing_during_history_read():
    import asyncio
    import threading
    orch, cfg, issue = setup_budget()
    entry = _install_running_entry(orch, issue)
    entry.turn_count = 2
    orch._issue_debug[issue.id] = _IssueDebug(completed_turn_count=4)
    started, release = threading.Event(), threading.Event()
    def history(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return [SimpleNamespace(state="Done")]
    orch._run_registry = SimpleNamespace(recent_runs=history)
    task = asyncio.create_task(orch.issue_budget_snapshot(issue))
    assert await asyncio.to_thread(started.wait, 5)
    orch._running.pop(issue.id)
    orch._issue_debug[issue.id].completed_turn_count += entry.turn_count
    release.set()
    rows = {row["name"]: row for row in (await task)["items"]}
    assert rows["total_turns"]["used"] == 6
    assert rows["reopens"]["used"] is None
