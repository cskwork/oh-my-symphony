from pathlib import Path

from symphony.factory.sync import sync_wayfinder
from symphony.orchestrator.contracts import evaluate_contract
from symphony.trackers.file import FileBoardTracker, parse_ticket_file
from symphony.workflow import TrackerConfig


def _tracker(root: Path) -> FileBoardTracker:
    return FileBoardTracker(
        TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            active_states=("Ready", "Build", "Verify"),
            terminal_states=("Done", "Blocked"),
            board_root=root,
        )
    )


def _source(path: Path, key: str, blocked_by: str = "") -> None:
    path.write_text(
        f"""---
id: {key}
title: {key}
route: LEGACY
blocked_by: [{blocked_by}]
skills: []
---

## Acceptance criteria

- verified

## Proof

- lifecycle check

## Non-goals

- unrelated work
""",
        encoding="utf-8",
    )


def test_starter_lifecycle_persists_rewind_done_dependency_and_blocked(tmp_path: Path) -> None:
    ticket_dir = tmp_path / "wayfinder/tickets"
    ticket_dir.mkdir(parents=True)
    _source(ticket_dir / "001-base.md", "base")
    _source(ticket_dir / "002-next.md", "next", "base")
    tracker = _tracker(tmp_path / "kanban")
    sync_wayfinder(ticket_dir.parent, tracker, prefix="TASK", all_tickets=True)

    dependent_front, _ = parse_ticket_file(tmp_path / "kanban" / "TASK-2.md")
    assert dependent_front["blocked_by"] == ["TASK-1"]
    dependent = tracker.fetch_issue_full_by_id("TASK-2")
    assert dependent is not None and dependent.blocked_by[0].state == "Ready"

    tracker.transition("TASK-1", "Build")
    tracker.transition("TASK-1", "Verify")
    failed = evaluate_contract("Verify", "## QA Failure\n\nred", "TASK-1", profile="factory")
    assert not failed.passed
    tracker.transition("TASK-1", "Build")
    tracker.transition("TASK-1", "Verify")
    passed = evaluate_contract(
        "Verify",
        "## Acceptance criteria\n\n"
        "- verified\n\n"
        "## Verification\n\n"
        "| criterion | command | result |\n"
        "| --- | --- | --- |\n"
        "| verified | `pytest tests/factory/test_lifecycle.py -q` | pass |",
        "TASK-1",
        profile="factory",
    )
    assert passed.passed
    tracker.transition("TASK-1", "Done")

    reloaded = _tracker(tmp_path / "kanban")
    assert reloaded.fetch_issue_full_by_id("TASK-1").state == "Done"  # type: ignore[union-attr]
    dependent = reloaded.fetch_issue_full_by_id("TASK-2")
    assert dependent is not None and dependent.blocked_by[0].state == "Done"
    reloaded.transition("TASK-2", "Blocked")
    assert _tracker(tmp_path / "kanban").fetch_issue_full_by_id("TASK-2").state == "Blocked"  # type: ignore[union-attr]
