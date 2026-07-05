"""Continuous-improvement heartbeat scheduler (plan §4).

Focused tests for the scheduler seam on the Orchestrator: due-math, single
in-flight guard, turn budget, idle-board postponement, runner-exception
isolation, plus the durable FileLease abstraction (fakes + a real tmpdir).
"""

from __future__ import annotations

import asyncio
import dataclasses
import textwrap
import time
from pathlib import Path

import pytest

from symphony.continuous_improvement import (
    FileLease,
    ImprovementRunResult,
    Lease,
    lease_path_for,
)
from symphony.orchestrator import Orchestrator
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    ContinuousImprovementConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    ServerConfig,
    ServiceConfig,
    WorkflowState,
)


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


def _make_config(
    *,
    ci: ContinuousImprovementConfig | None = None,
) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=Path("/tmp/ws"),
        tracker=TrackerConfigStub(),  # type: ignore[arg-type]
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=5,
            max_turns=20,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
            auto_triage_actionable_todo=True,
        ),
        codex=CodexConfig(
            command="codex app-server",
            approval_policy=None,
            thread_sandbox=None,
            turn_sandbox_policy=None,
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
        claude=ClaudeConfig(
            command="claude -p",
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
        gemini=GeminiConfig(
            command='gemini -p ""',
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
        ),
        pi=PiConfig(
            command='pi --mode json -p ""',
            turn_timeout_ms=3_600_000,
            read_timeout_ms=5_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
        server=ServerConfig(port=None),
        prompt_template="hi",
        continuous_improvement=ci or ContinuousImprovementConfig(),
    )


# TrackerConfig has many required fields; the scheduler never touches it, so a
# lightweight stand-in keeps the config builder terse.
from symphony.workflow import TrackerConfig  # noqa: E402


def TrackerConfigStub() -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="proj",
        active_states=("Todo", "In Progress"),
        terminal_states=("Done",),
        board_root=Path("/tmp/board"),
    )


def _orch(**kwargs) -> Orchestrator:
    return Orchestrator(WorkflowState(Path("/tmp/no.md")), **kwargs)


def _enabled_ci(**overrides: object) -> ContinuousImprovementConfig:
    base = ContinuousImprovementConfig(enabled=True, interval_ms=60_000, max_turns=48)
    return dataclasses.replace(base, **overrides)


class _FakeRunner:
    """Records calls; returns a canned result or raises."""

    def __init__(self, *, result: ImprovementRunResult | None = None, boom=None):
        self.calls = 0
        self.phases: list[str] = []
        self._result = result or ImprovementRunResult(tickets_created=2)
        self._boom = boom

    async def __call__(self, cfg, workflow_dir, report_phase) -> ImprovementRunResult:
        self.calls += 1
        report_phase("checking")
        self.phases.append("checking")
        if self._boom is not None:
            raise self._boom
        return self._result


class _FakeLease:
    def __init__(self, *, grant: bool = True) -> None:
        self._grant = grant
        self.acquired = 0
        self.released = 0

    def acquire(self) -> bool:
        if self._grant:
            self.acquired += 1
        return self._grant

    def refresh(self) -> None:  # pragma: no cover - unused in skeleton
        pass

    def release(self) -> None:
        self.released += 1


class _FlippingLease:
    def __init__(self) -> None:
        self.calls = 0
        self.released = 0

    def acquire(self) -> bool:
        self.calls += 1
        return self.calls > 1

    def refresh(self) -> None:  # pragma: no cover - unused in scheduler tests
        pass

    def release(self) -> None:
        self.released += 1


async def _drain_improvement(orch: Orchestrator) -> None:
    task = orch._improvement_task
    if task is not None:
        await task


# --------------------------------------------------------------------------
# scheduling: disabled / not-due / due / single-in-flight
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_config_schedules_nothing() -> None:
    orch = _orch(improvement_runner=_FakeRunner(), improvement_lease=_FakeLease())
    cfg = _make_config(ci=ContinuousImprovementConfig(enabled=False))

    orch._maybe_schedule_continuous_improvement(cfg)

    assert orch._improvement_task is None
    status = orch.continuous_improvement_status()
    assert status["enabled"] is False
    assert status["in_flight"] is False


def test_status_merges_current_config_before_first_tick(tmp_path: Path) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
            agent:
              kind: codex
            continuous_improvement:
              enabled: true
              interval_ms: 60000
              max_turns: 2
              agent_kind: claude
            ---

            Prompt.
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "kanban").mkdir()
    state = WorkflowState(workflow)
    cfg, err = state.reload()
    assert err is None and cfg is not None
    orch = Orchestrator(state)

    status = orch.continuous_improvement_status()

    assert status["enabled"] is True
    assert status["interval_ms"] == 60_000
    assert status["max_turns"] == 2
    assert status["agent_kind"] == "claude"


@pytest.mark.asyncio
async def test_enabled_but_not_due_schedules_nothing() -> None:
    runner = _FakeRunner()
    orch = _orch(improvement_runner=runner, improvement_lease=_FakeLease())
    cfg = _make_config(ci=_enabled_ci())

    # First observation initialises the due time one interval out.
    orch._maybe_schedule_continuous_improvement(cfg)
    assert orch._improvement_task is None
    assert orch._next_improvement_due_monotonic is not None

    # A second tick before the interval elapses still schedules nothing.
    orch._maybe_schedule_continuous_improvement(cfg)
    assert orch._improvement_task is None
    assert runner.calls == 0


@pytest.mark.asyncio
async def test_due_heartbeat_schedules_one_run() -> None:
    runner = _FakeRunner(result=ImprovementRunResult(tickets_created=3))
    lease = _FakeLease()
    orch = _orch(improvement_runner=runner, improvement_lease=lease)
    cfg = _make_config(ci=_enabled_ci())

    orch._maybe_schedule_continuous_improvement(cfg)  # init due
    orch._next_improvement_due_monotonic = time.monotonic() - 1  # force due
    orch._maybe_schedule_continuous_improvement(cfg)

    assert orch._improvement_task is not None
    await _drain_improvement(orch)

    assert runner.calls == 1
    assert lease.acquired == 1
    assert lease.released == 1
    status = orch.continuous_improvement_status()
    assert status["last_result"] == "passed"
    assert status["tickets_created"] == 3
    assert status["turns_used"] == 1
    assert status["in_flight"] is False


@pytest.mark.asyncio
async def test_second_tick_while_in_flight_does_not_schedule_another() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingRunner:
        calls = 0

        async def __call__(self, cfg, workflow_dir, report_phase):
            type(self).calls += 1
            started.set()
            await release.wait()
            return ImprovementRunResult()

    runner = _BlockingRunner()
    orch = _orch(improvement_runner=runner, improvement_lease=_FakeLease())
    cfg = _make_config(ci=_enabled_ci())

    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    await started.wait()

    # Second due tick while the first run is in flight: no new task.
    first_task = orch._improvement_task
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    assert orch._improvement_task is first_task
    assert runner.calls == 1

    release.set()
    await _drain_improvement(orch)
    assert runner.calls == 1


# --------------------------------------------------------------------------
# runner exceptions / idle board / turn budget
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_exception_records_failure_and_survives() -> None:
    runner = _FakeRunner(boom=RuntimeError("check blew up"))
    orch = _orch(improvement_runner=runner, improvement_lease=_FakeLease())
    cfg = _make_config(ci=_enabled_ci())

    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    await _drain_improvement(orch)  # must not raise

    status = orch.continuous_improvement_status()
    assert status["last_result"] == "failed"
    assert "check blew up" in status["last_error"]
    assert status["turns_used"] == 1  # a completed (failed) run consumes a turn

    # The scheduler is unharmed: a subsequent due tick schedules again.
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    assert orch._improvement_task is not None
    await _drain_improvement(orch)
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_require_idle_board_postpones_when_workers_running() -> None:
    runner = _FakeRunner()
    orch = _orch(improvement_runner=runner, improvement_lease=_FakeLease())
    cfg = _make_config(ci=_enabled_ci(require_idle_board=True))

    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1

    # Pretend a normal worker is running.
    orch._running["X-1"] = object()  # type: ignore[assignment]
    orch._maybe_schedule_continuous_improvement(cfg)
    assert orch._improvement_task is None
    assert orch.continuous_improvement_status()["skipped_reason"] == "board_busy"
    assert runner.calls == 0

    # Board idle again -> the still-due heartbeat fires (turn not consumed
    # by the postpone).
    orch._running.clear()
    orch._maybe_schedule_continuous_improvement(cfg)
    assert orch._improvement_task is not None
    await _drain_improvement(orch)
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_require_idle_board_postpones_when_retry_pending() -> None:
    runner = _FakeRunner()
    orch = _orch(improvement_runner=runner, improvement_lease=_FakeLease())
    cfg = _make_config(ci=_enabled_ci(require_idle_board=True))

    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._retry["X-2"] = object()  # type: ignore[assignment]
    orch._maybe_schedule_continuous_improvement(cfg)

    assert orch._improvement_task is None
    assert orch.continuous_improvement_status()["skipped_reason"] == "board_busy"


@pytest.mark.asyncio
async def test_turn_budget_exhaustion_and_reset() -> None:
    runner = _FakeRunner()
    orch = _orch(improvement_runner=runner, improvement_lease=_FakeLease())
    cfg = _make_config(ci=_enabled_ci(max_turns=1))

    # First run consumes the single turn.
    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    await _drain_improvement(orch)
    assert orch._improvement_turns_used == 1

    # Now exhausted: a due tick schedules nothing and records the reason.
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    assert orch._improvement_task is None
    assert (
        orch.continuous_improvement_status()["skipped_reason"] == "max_turns_reached"
    )
    assert runner.calls == 1

    # Reset resumes scheduling and clears the skip reason.
    orch.reset_continuous_improvement_turns()
    assert orch._improvement_turns_used == 0
    assert orch.continuous_improvement_status()["skipped_reason"] is None
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    await _drain_improvement(orch)
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_reset_during_in_flight_still_counts_that_run() -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    class _BlockingRunner:
        async def __call__(self, cfg, workflow_dir, report_phase):
            started.set()
            await release.wait()
            return ImprovementRunResult()

    orch = _orch(improvement_runner=_BlockingRunner(), improvement_lease=_FakeLease())
    cfg = _make_config(ci=_enabled_ci(max_turns=5))

    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    await started.wait()

    orch.reset_continuous_improvement_turns()  # zero mid-run
    assert orch._improvement_turns_used == 0

    release.set()
    await _drain_improvement(orch)
    # The in-flight run still books its own increment on completion.
    assert orch._improvement_turns_used == 1


@pytest.mark.asyncio
async def test_max_turns_zero_never_exhausts() -> None:
    runner = _FakeRunner()
    orch = _orch(improvement_runner=runner, improvement_lease=_FakeLease())
    cfg = _make_config(ci=_enabled_ci(max_turns=0))

    orch._maybe_schedule_continuous_improvement(cfg)
    for _ in range(3):
        orch._next_improvement_due_monotonic = time.monotonic() - 1
        orch._maybe_schedule_continuous_improvement(cfg)
        await _drain_improvement(orch)

    assert runner.calls == 3
    assert orch.continuous_improvement_status()["skipped_reason"] is None


@pytest.mark.asyncio
async def test_lease_held_postpones_without_consuming_turn() -> None:
    runner = _FakeRunner()
    lease = _FakeLease(grant=False)
    orch = _orch(improvement_runner=runner, improvement_lease=lease)
    cfg = _make_config(ci=_enabled_ci())

    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    await _drain_improvement(orch)

    assert runner.calls == 0
    assert orch._improvement_turns_used == 0
    assert orch.continuous_improvement_status()["skipped_reason"] == "lease_held"


@pytest.mark.asyncio
async def test_lease_held_retries_next_tick_without_full_interval() -> None:
    runner = _FakeRunner()
    lease = _FlippingLease()
    orch = _orch(improvement_runner=runner, improvement_lease=lease)
    cfg = _make_config(ci=_enabled_ci())

    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    await _drain_improvement(orch)

    assert runner.calls == 0
    assert orch._improvement_turns_used == 0
    assert orch.continuous_improvement_status()["skipped_reason"] == "lease_held"

    orch._maybe_schedule_continuous_improvement(cfg)
    await _drain_improvement(orch)

    assert runner.calls == 1
    assert lease.calls == 2
    assert lease.released == 1
    assert orch.continuous_improvement_status()["skipped_reason"] is None


@pytest.mark.asyncio
async def test_runner_timeout_releases_lease_and_clears_in_flight() -> None:
    started = asyncio.Event()

    class _HungRunner:
        async def __call__(self, cfg, workflow_dir, report_phase):
            started.set()
            await asyncio.Event().wait()
            return ImprovementRunResult()

    lease = _FakeLease()
    orch = _orch(improvement_runner=_HungRunner(), improvement_lease=lease)
    orch._improvement_run_timeout_s = 0.01
    cfg = _make_config(ci=_enabled_ci())

    orch._maybe_schedule_continuous_improvement(cfg)
    orch._next_improvement_due_monotonic = time.monotonic() - 1
    orch._maybe_schedule_continuous_improvement(cfg)
    await started.wait()
    await _drain_improvement(orch)

    status = orch.continuous_improvement_status()
    assert status["last_result"] == "failed"
    assert "timed out" in status["last_error"]
    assert status["in_flight"] is False
    assert status["turns_used"] == 1
    assert lease.released == 1


# --------------------------------------------------------------------------
# durable FileLease (real tmpdir)
# --------------------------------------------------------------------------


def test_file_lease_mutual_exclusion(tmp_path: Path) -> None:
    path = lease_path_for(tmp_path)
    a = FileLease(path)
    b = FileLease(path)

    assert a.acquire() is True
    assert b.acquire() is False  # held by a
    a.release()
    assert b.acquire() is True  # now free
    b.release()


def test_file_lease_steals_stale(tmp_path: Path) -> None:
    path = lease_path_for(tmp_path)
    clock = {"t": 1000.0}
    holder = FileLease(path, ttl_seconds=100.0, now=lambda: clock["t"])
    other = FileLease(path, ttl_seconds=100.0, now=lambda: clock["t"])

    assert holder.acquire() is True
    # Advance past the TTL; the crashed holder never released.
    clock["t"] += 200.0
    assert other.acquire() is True  # stolen


def test_stale_holder_release_does_not_unlink_new_holder(tmp_path: Path) -> None:
    path = lease_path_for(tmp_path)
    clock = {"t": 1000.0}
    stale = FileLease(path, ttl_seconds=100.0, now=lambda: clock["t"])
    assert stale.acquire() is True
    clock["t"] += 200.0
    fresh = FileLease(path, ttl_seconds=100.0, now=lambda: clock["t"])
    assert fresh.acquire() is True

    stale.release()

    assert path.exists()
    blocked = FileLease(path, ttl_seconds=100.0, now=lambda: clock["t"])
    assert blocked.acquire() is False
    fresh.release()


def test_file_lease_stale_steal_allows_one_winner(tmp_path: Path) -> None:
    path = lease_path_for(tmp_path)
    clock = {"t": 1000.0}
    holder = FileLease(path, ttl_seconds=100.0, now=lambda: clock["t"])
    assert holder.acquire() is True
    clock["t"] += 200.0

    first = FileLease(path, ttl_seconds=100.0, now=lambda: clock["t"])
    assert first.acquire() is True

    second = FileLease(path, ttl_seconds=100.0, now=lambda: clock["t"])
    assert second.acquire() is False
    first.release()


def test_file_lease_is_a_lease() -> None:
    assert isinstance(FileLease(Path("/tmp/x.lock")), Lease)
