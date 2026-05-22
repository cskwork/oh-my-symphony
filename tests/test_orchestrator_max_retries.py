"""max_retries cap → escalation (v0.6.7).

Track 3-A: when a worker keeps exiting with a non-normal outcome and the
retry attempt would exceed `agent.max_retries`, the orchestrator stops
scheduling further retries, appends a board-level ## Escalation note to
the ticket, and moves the ticket to a terminal state (`Blocked` by
default, or the first configured terminal state mentioning ``block`` or
``human``).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from symphony.orchestrator import Orchestrator
from symphony.workflow import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    HooksConfig,
    PiConfig,
    PromptConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
    TuiConfig,
    WorkflowState,
)


def _make_config(*, max_retries: int = 3) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=Path("/tmp/ws"),
        tracker=TrackerConfig(
            kind="file",
            endpoint="https://api.linear.app/graphql",
            api_key="tok",
            project_slug="proj",
            active_states=("Todo", "Explore", "In Progress", "Review"),
            terminal_states=("Done", "Cancelled", "Blocked"),
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=1,
            max_turns=5,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
            max_attempts=3,
            max_retries=max_retries,
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
        tui=TuiConfig(language="en", visible_lanes=5),
        prompts=PromptConfig(),
        prompt_template="state={{ issue.state }}",
    )


def _orch(cfg: ServiceConfig) -> Orchestrator:
    """Build a bare orchestrator wired to a fresh event loop.

    `_schedule_retry` uses `self._loop.create_task(...)`, so every
    test that exercises the cap needs a loop attached to the
    orchestrator. Tests run their assertions inside the same loop
    via `loop.run_until_complete` so the escalation task can settle.
    """
    state = WorkflowState(Path("/tmp/no.md"))
    state._config = cfg  # type: ignore[attr-defined]
    o = Orchestrator(state)
    o._loop = asyncio.new_event_loop()
    return o


def test_default_max_retries_is_three() -> None:
    """Default cap of 3 — the documented v0.6.7 value."""
    cfg = _make_config()
    assert cfg.agent.max_retries == 3


def test_zero_max_retries_disables_cap() -> None:
    """0 = legacy behaviour (retry forever)."""
    cfg = _make_config(max_retries=0)
    o = _orch(cfg)

    async def _drive() -> None:
        o._schedule_retry(
            "iss-1",
            identifier="MT-1",
            attempt=999,  # way over any plausible cap
            delay_ms=10_000,
            error="timeout",
            kind="retry",
        )

    o._loop.run_until_complete(_drive())
    # The retry should be in `_retry` (legacy path), not escalated.
    assert "iss-1" in o._retry
    # Cancel the call_later timer so the loop closes cleanly.
    o._retry["iss-1"].timer_handle.cancel()
    o._loop.close()


def test_max_retries_exhausted_triggers_escalation_task(
    monkeypatch,
) -> None:
    """attempt > max_retries → an escalation task is queued instead of a retry."""
    cfg = _make_config(max_retries=3)
    o = _orch(cfg)

    captured: list[tuple] = []

    def _fake_append_note(cfg, issue, heading, body):  # noqa: ANN001
        del cfg, issue
        captured.append(("append_note", heading, body))

    def _fake_update_state(cfg, issue, state):  # noqa: ANN001
        del cfg, issue
        captured.append(("update_state", state))

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_append_note",
        staticmethod(_fake_append_note),
    )
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(_fake_update_state),
    )

    async def _drive() -> None:
        o._schedule_retry(
            "iss-1",
            identifier="MT-1",
            attempt=4,  # 4 > 3 → escalate
            delay_ms=0,
            error="timeout",
            kind="retry",
        )
        # Yield once so the create_task'd escalation coroutine gets to
        # run before we make assertions.
        await asyncio.sleep(0)
        # And once more for the to_thread tracker calls to finish.
        await asyncio.sleep(0)

    o._loop.run_until_complete(_drive())
    # Drain remaining tasks before closing the loop.
    pending = asyncio.all_tasks(o._loop)
    if pending:
        o._loop.run_until_complete(
            asyncio.gather(*pending, return_exceptions=True)
        )
    o._loop.close()

    # No new retry queued — escalation took over.
    assert "iss-1" not in o._retry
    headings = [c for c in captured if c[0] == "append_note"]
    state_writes = [c for c in captured if c[0] == "update_state"]
    assert any("Escalation" in c[1] for c in headings), captured
    assert any("Block" in c[1] for c in state_writes), captured


def test_continuation_kind_is_exempt_from_cap() -> None:
    """Continuations (successful turn/stage handoff) never exhaust the cap."""
    cfg = _make_config(max_retries=3)
    o = _orch(cfg)

    async def _drive() -> None:
        o._schedule_retry(
            "iss-1",
            identifier="MT-1",
            attempt=99,  # would normally exceed cap
            delay_ms=10_000,
            error=None,  # kind resolves to "continuation"
            kind=None,
        )

    o._loop.run_until_complete(_drive())
    # Continuation path: queued in `_retry`, not escalated.
    assert "iss-1" in o._retry
    o._retry["iss-1"].timer_handle.cancel()
    o._loop.close()


def test_escalation_picks_human_terminal_when_named(monkeypatch) -> None:
    """If `terminal_states` contains "Needs Human", prefer it over "Blocked"."""
    cfg = _make_config(max_retries=3)
    cfg = replace(
        cfg,
        tracker=replace(
            cfg.tracker,
            terminal_states=("Done", "Cancelled", "Needs Human", "Blocked"),
        ),
    )
    o = _orch(cfg)

    captured: list[str] = []

    def _fake_update_state(cfg, issue, state):  # noqa: ANN001
        del cfg, issue
        captured.append(state)

    def _fake_append_note(cfg, issue, heading, body):  # noqa: ANN001
        del cfg, issue, heading, body

    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_append_note",
        staticmethod(_fake_append_note),
    )
    monkeypatch.setattr(
        Orchestrator,
        "_tracker_call_update_state",
        staticmethod(_fake_update_state),
    )

    o._loop.run_until_complete(
        o._escalate_max_retries(
            issue_id="iss-1",
            identifier="MT-1",
            attempt=4,
            error="timeout",
        )
    )
    o._loop.close()

    # "Needs Human" comes BEFORE "Blocked" in terminal_states and
    # matches the "human" preference rule, so it should win.
    assert captured == ["Needs Human"], captured
