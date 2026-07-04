"""End-to-end contract-validation regression tests against a real tracker.

Background — what these tests guard against
-------------------------------------------

The v0.6.7 release (cfb6de4) added a stage-contract validator that reads
``issue.description`` at every forward phase transition and rewinds the
ticket if the producing stage's required sections are absent.

CI was green (587 passed) — but production runs against the file tracker
surfaced false ``## Contract Failure`` rewinds. Two follow-up patches
landed:

* ``e68c4d7`` (PR #48) — first attempt; insufficient.
* ``365e67b`` (PR #49) — refreshed ``issue`` from the tracker before
  evaluating the contract, plus replaced ``issue.state`` with the
  producing stage's raw casing on rewind.

Both patches were verified against tests that monkeypatched
``Orchestrator._refresh_issue_state`` to return a fully-hydrated body.
That is NOT how the production refresh helper behaves: for *all three*
trackers (file / Linear / Jira), ``fetch_issue_states_by_ids`` returns a
*minimal* Issue with ``description=None``. The pre-release CI scaffold
silently papered over the realistic case.

These tests run the contract path against a real ``FileBoardTracker``
(no monkeypatching of refresh / append / update / build_tracker_client),
so any regression that re-introduces the stale-body class of bug — or
that depends on a phantom hydrated description from
``_refresh_issue_state`` — fails here before merge.

Test taxonomy
-------------

``test_contract_passes_when_disk_has_required_sections``
    Happy path: ticket file already contains In Progress contract sections
    before the agent transitions In Progress -> Verify. Contract must pass;
    no ``## Contract Failure`` note may be appended.

``test_contract_fails_when_disk_missing_sections``
    Sad path: ticket file is missing ``## Done Signals``. Contract must
    fail and the orchestrator must rewind the ticket back to ``In Progress``.

The fake backend used here mutates the ticket file on disk inside
``run_turn`` — the same shape the real agents have (Write tool → file)
— so the orchestrator's subsequent ``_refresh_issue_state`` reads from
the same source the validator depends on.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from symphony import orchestrator as orch_mod
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator, RunningEntry
from symphony.trackers.file import (
    parse_ticket_file,
    write_ticket_atomic,
)
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


# ---------------------------------------------------------------------------
# In Progress and Verify body fixtures
# ---------------------------------------------------------------------------


_CONTRACT_FAILURE_HEADING_RE = re.compile(r"^##\s+Contract\s+Failure\s*$", re.MULTILINE)


def _has_contract_failure_heading(body: str) -> bool:
    """True when a `## Contract Failure` HEADING anchors a line.

    Substring matches inside Plan / Acceptance Tests prose (e.g. the
    fixture bodies in this file) do not count — only a column-zero
    markdown heading does. Mirrors how `FileBoardTracker.append_note`
    persists orchestrator-authored notes.
    """
    return _CONTRACT_FAILURE_HEADING_RE.search(body) is not None


_CONTRACT_WARNING_HEADING_RE = re.compile(r"^##\s+Contract\s+Warning\s*$", re.MULTILINE)


def _has_contract_warning_heading(body: str) -> bool:
    """True when a `## Contract Warning` HEADING anchors a line.

    The soft S2 path appends this note (no rewind) when a stage passes the
    presence + evidence contract but an AC Scorecard row is non-passing.
    """
    return _CONTRACT_WARNING_HEADING_RE.search(body) is not None


_IN_PROGRESS_BODY_COMPLETE = """## Plan

Step 1 — wire the new validator into the forward-transition path.
Step 2 — surface a `## Contract Failure` note when the producing stage
under-produces.

## Acceptance Tests

- existing phase-transition tests stay green
- contract eval honours latest tracker body

## Done Signals

- pytest -q green
- one release-bumping commit landed on `main`

## Implementation

- wired the validator into the phase transition path

## Self-Critique

- checked stale-body and empty-body paths
"""


_IN_PROGRESS_BODY_MISSING_DONE_SIGNALS = """## Plan

Step 1 — wire the new validator into the forward-transition path.

## Acceptance Tests

- existing phase-transition tests stay green

## Implementation

- wired the validator into the phase transition path

## Self-Critique

- checked stale-body and empty-body paths
"""


_VERIFY_BODY_SCORECARD_FAIL = """## Security Audit

| check | verdict | evidence |
|--------|--------|----------|
| secrets | pass | qa/security.md |
| input-validation | pass | qa/security.md |
| injection | pass | qa/security.md |
| xss | pass | qa/security.md |
| csrf | pass | qa/security.md |
| authz | pass | qa/security.md |
| rate-limit | pass | qa/security.md |

## Review

diff matches the plan

## QA Evidence

- booted the service and replayed the acceptance payloads

## AC Scorecard

| signal | source | result | evidence |
|--------|--------|--------|----------|
| happy path returns 200 | curl | pass | qa/happy.log |
| edge case rejects bad input | curl | fail | qa/edge.log |

## Merge Status

merged to main with --no-ff
"""


# ---------------------------------------------------------------------------
# Test doubles — fake backend that drives the ticket file during run_turn
# ---------------------------------------------------------------------------


@dataclass
class _TicketMutatingBackend:
    """Fake backend whose ``run_turn`` mutates the ticket file on disk.

    Simulates the real-agent contract:

    * the agent owns the ticket markdown body (via Write tool)
    * the agent owns the state transition (via tracker tool)
    * the orchestrator sees both changes only through the file tracker

    A scripted ``transitions`` list drives the per-turn behaviour. Each
    entry is ``(new_state, body_after_turn)``. After the script is
    exhausted, ``run_turn`` is a no-op so the orchestrator can drain its
    bookkeeping loop and exit via ``max_turns``.

    NOTE: ``transitions`` is shared across every backend instance built
    inside one ``_run_agent_attempt`` call. The orchestrator rebuilds
    the backend on every phase transition (so the next stage starts with
    a fresh context), and a fresh per-instance transition list would
    cause turn 2 of the rebuilt backend to replay turn 1's mutation —
    blowing away an in-between ``## Contract Failure`` note. The factory
    wires every instance to the same list so the script advances
    monotonically across rebuilds, matching real agent behaviour.
    """

    ticket_path: Path
    transitions: list[tuple[str, str]]
    init_id: int = 0
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def start(self) -> None:
        self.calls.append(("start", {}))

    async def initialize(self) -> None:
        self.calls.append(("initialize", {}))

    async def start_session(
        self, *, initial_prompt: str, issue_title: str
    ) -> None:
        self.session_id = f"fake-session-{self.init_id}"
        self.calls.append(
            (
                "start_session",
                {"initial_prompt": initial_prompt, "issue_title": issue_title},
            )
        )

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> None:
        self.calls.append(
            ("run_turn", {"prompt": prompt, "is_continuation": is_continuation})
        )
        # Pop the next scripted transition and apply it to the ticket file
        # before the orchestrator's post-turn _refresh_issue_state runs.
        if not self.transitions:
            return
        new_state, body = self.transitions.pop(0)
        front, _ = parse_ticket_file(self.ticket_path)
        front["state"] = new_state
        front["updated_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        write_ticket_atomic(self.ticket_path, front, body)

    async def stop(self) -> None:
        self.calls.append(("stop", {}))


class _FakeWorkspace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.workspace_key = "fake"
        self.created_now = True


class _FakeWorkspaceManager:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.after_run_paths: list[Path] = []

    def path_for(self, identifier: str) -> Path:
        del identifier
        return self._path

    async def create_or_reuse(self, identifier: str) -> _FakeWorkspace:
        del identifier
        return _FakeWorkspace(self._path)

    async def before_run(self, path: Path) -> None:
        del path

    async def after_run_best_effort(self, path: Path) -> None:
        self.after_run_paths.append(path)


# ---------------------------------------------------------------------------
# Fixtures — real file tracker + matching ServiceConfig
# ---------------------------------------------------------------------------


def _write_initial_ticket(board_root: Path, *, state: str, body: str) -> Path:
    """Seed ``MT-1.md`` on disk in the layout FileBoardTracker expects."""
    board_root.mkdir(parents=True, exist_ok=True)
    front = {
        "id": "MT-1",
        "identifier": "MT-1",
        "title": "phase transition fixture",
        "state": state,
        "priority": 2,
        "created_at": "2026-01-01T00:00:00Z",
    }
    path = board_root / "MT-1.md"
    write_ticket_atomic(path, front, body)
    return path


def _make_file_tracker_config(
    *,
    board_root: Path,
    active_states: tuple[str, ...],
    max_turns: int = 3,
    max_attempts: int = 3,
) -> ServiceConfig:
    template = (
        "issue={{ issue.identifier }} state={{ issue.state }} "
        "rewind={{ is_rewind }}"
    )
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=30_000,
        workspace_root=board_root.parent / "ws",
        tracker=TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            active_states=active_states,
            terminal_states=("Done", "Cancelled", "Blocked"),
            board_root=board_root,
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=AgentConfig(
            kind="codex",
            max_concurrent_agents=1,
            max_turns=max_turns,
            max_retry_backoff_ms=300_000,
            max_concurrent_agents_by_state={},
            max_attempts=max_attempts,
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
        prompt_template=template,
    )


def _make_issue_from_disk(state: str, body: str) -> Issue:
    """Build the in-memory Issue the orchestrator starts the worker with.

    Matches what ``fetch_candidate_issues`` returns for a freshly picked-up
    ticket: full body + correct state. The test then mutates the disk file
    independently to simulate the agent's work.
    """
    return Issue(
        id="MT-1",
        identifier="MT-1",
        title="phase transition fixture",
        description=body,
        priority=2,
        state=state,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _orch(workspace_path: Path) -> Orchestrator:
    state = WorkflowState(Path("/tmp/no.md"))
    o = Orchestrator(state)
    o._workspace_manager = _FakeWorkspaceManager(workspace_path)  # type: ignore[assignment]
    return o


def _seed_running_entry(
    o: Orchestrator, issue: Issue, workspace_path: Path
) -> None:
    o._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=workspace_path,
    )


def _install_file_tracker_backend(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ticket_path: Path,
    transitions: list[tuple[str, str]],
) -> list[_TicketMutatingBackend]:
    instances: list[_TicketMutatingBackend] = []
    # Share one mutable script across every backend instance built in
    # this test. See ``_TicketMutatingBackend`` docstring for the why.
    shared_transitions = list(transitions)

    def _factory(init: Any) -> _TicketMutatingBackend:
        backend = _TicketMutatingBackend(
            ticket_path=ticket_path,
            transitions=shared_transitions,
            init_id=len(instances),
        )
        backend.calls.append(("factory", {"agent_kind": init.cfg.agent.kind}))
        instances.append(backend)
        return backend

    monkeypatch.setattr(orch_mod, "build_backend", _factory)
    return instances


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_contract_passes_when_disk_has_required_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path against a real FileBoardTracker.

    The ticket on disk already has all In Progress contract sections before the
    agent transitions to Verify. Contract evaluation MUST read the disk
    body and pass; no rewind, no ``## Contract Failure`` note.

    The 0.6.7 release shipped a path where ``_refresh_issue_state``
    returns a minimal Issue with ``description=None``. If that bug
    re-enters, the contract evaluates against an empty body, fails, and
    appends a ``## Contract Failure`` note — caught here as a string
    match on the persisted ticket file.
    """
    board_root = tmp_path / "board"
    ticket_path = _write_initial_ticket(
        board_root, state="In Progress", body=_IN_PROGRESS_BODY_COMPLETE
    )
    cfg = _make_file_tracker_config(
        board_root=board_root,
            active_states=("In Progress", "Verify", "Learn"),
            max_turns=2,
        )

    _install_file_tracker_backend(
        monkeypatch,
        ticket_path=ticket_path,
        transitions=[("Verify", _IN_PROGRESS_BODY_COMPLETE)],
    )

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "docs" / "MT-1" / "work").mkdir(parents=True)
    (workspace_path / "docs" / "MT-1" / "work" / "notes.md").write_text("ok")
    o = _orch(workspace_path)
    issue = _make_issue_from_disk("In Progress", _IN_PROGRESS_BODY_COMPLETE)
    _seed_running_entry(o, issue, workspace_path)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    final_front, final_body = parse_ticket_file(ticket_path)
    # The orchestrator writes a `## Contract Failure` HEADING at column 0,
    # so anchor the substring search to the start of a line. The fixture
    # bodies above intentionally mention the phrase in prose to verify
    # this anchoring guard.
    assert not _has_contract_failure_heading(final_body), (
        "False ## Contract Failure heading appended despite ticket "
        f"containing all required Plan sections. Body was:\n{final_body}"
    )
    assert final_front["state"] != "In Progress", (
        "Contract guard rewound the ticket back to In Progress even though every "
        "required section is present on disk."
    )


def test_contract_fails_when_disk_missing_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sad path against a real FileBoardTracker.

    The ticket is missing ``## Done Signals`` when the agent transitions
    In Progress -> Verify. The contract MUST fail, the orchestrator MUST append
    a ``## Contract Failure`` note, AND the ticket state MUST revert to
    ``In Progress``. This guards the rewind path against silent breakage in
    addition to the happy path above.
    """
    board_root = tmp_path / "board"
    ticket_path = _write_initial_ticket(
        board_root, state="In Progress", body=_IN_PROGRESS_BODY_MISSING_DONE_SIGNALS
    )
    cfg = _make_file_tracker_config(
        board_root=board_root,
        active_states=("In Progress", "Verify", "Learn"),
        max_turns=2,
    )

    _install_file_tracker_backend(
        monkeypatch,
        ticket_path=ticket_path,
        # Agent moves to Verify without producing Done Signals.
        transitions=[("Verify", _IN_PROGRESS_BODY_MISSING_DONE_SIGNALS)],
    )

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "docs" / "MT-1" / "work").mkdir(parents=True)
    (workspace_path / "docs" / "MT-1" / "work" / "notes.md").write_text("ok")
    o = _orch(workspace_path)
    issue = _make_issue_from_disk("In Progress", _IN_PROGRESS_BODY_MISSING_DONE_SIGNALS)
    _seed_running_entry(o, issue, workspace_path)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    final_front, final_body = parse_ticket_file(ticket_path)
    assert _has_contract_failure_heading(final_body), (
        "Contract guard did not append a ## Contract Failure heading "
        "even though `## Done Signals` was absent. Body was:\n" + final_body
    )
    assert final_front["state"] == "In Progress", (
        "Contract guard did not rewind the ticket back to In Progress after a "
        f"failed contract. Final state was {final_front['state']!r}."
    )


def test_qa_scorecard_fail_warns_without_rewind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Soft S2 warning path end-to-end against a real FileBoardTracker.

    A Verify ticket that passes the presence + evidence contract but carries a
    ``fail`` AC Scorecard row must NOT rewind. The orchestrator's
    ``elif contract.warnings:`` branch appends a ``## Contract Warning``
    note and lets the ticket advance past Verify. The contracts-layer unit
    tests cover ``_scorecard_all_pass``; this guards the orchestrator
    wiring (the core.py branch) against the same stale-body / phantom
    refresh class of bug the rewind tests above guard.
    """
    board_root = tmp_path / "board"
    ticket_path = _write_initial_ticket(
        board_root, state="Verify", body=_VERIFY_BODY_SCORECARD_FAIL
    )
    cfg = _make_file_tracker_config(
        board_root=board_root,
        active_states=("Verify", "Learn"),
        max_turns=2,
    )

    _install_file_tracker_backend(
        monkeypatch,
        ticket_path=ticket_path,
        transitions=[("Learn", _VERIFY_BODY_SCORECARD_FAIL)],
    )

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    qa_dir = workspace_path / "docs" / "MT-1" / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "security.md").write_text("security evidence")
    (qa_dir / "happy.log").write_text("200")
    (qa_dir / "edge.log").write_text("400")
    o = _orch(workspace_path)
    issue = _make_issue_from_disk("Verify", _VERIFY_BODY_SCORECARD_FAIL)
    _seed_running_entry(o, issue, workspace_path)

    asyncio.run(o._run_agent_attempt(issue, attempt=None, cfg=cfg))

    final_front, final_body = parse_ticket_file(ticket_path)
    assert _has_contract_warning_heading(final_body), (
        "Soft scorecard warning did not append a ## Contract Warning "
        "heading. Body was:\n" + final_body
    )
    assert not _has_contract_failure_heading(final_body), (
        "Soft scorecard warning incorrectly escalated to a "
        "## Contract Failure rewind."
    )
    assert final_front["state"] != "Verify", (
        "Soft scorecard warning incorrectly rewound the ticket instead of "
        f"advancing past Verify. Final state was {final_front['state']!r}."
    )
