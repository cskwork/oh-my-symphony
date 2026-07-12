from pathlib import Path

import pytest

from symphony.factory.sync import sync_wayfinder
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


def _ticket(path: Path, key: str, title: str, blocked_by: tuple[str, ...] = ()) -> None:
    deps = "[" + ", ".join(blocked_by) + "]"
    path.write_text(
        f"""---
id: {key}
title: {title}
route: LEGACY
blocked_by: {deps}
skills: [superqa]
---

## Acceptance criteria

- The slice is independently verified.

## Proof commands

- `pytest`

## Non-goals

- Unrelated work.
""",
        encoding="utf-8",
    )


def test_sync_allocates_in_order_maps_dependencies_and_is_idempotent(tmp_path: Path) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    _ticket(tickets / "001-foundation.md", "foundation", "Foundation")
    _ticket(tickets / "002-feature.md", "feature", "Feature", ("foundation",))
    tracker = _tracker(tmp_path / "kanban")

    first = sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    before_second_sync = {
        path.name: path.read_bytes() for path in (tmp_path / "kanban").glob("TASK-*.md")
    }
    second = sync_wayfinder(tickets.parent, tracker, prefix="TASK")

    assert [(item.identifier, item.created) for item in first] == [
        ("TASK-1", True),
        ("TASK-2", True),
    ]
    assert all(not item.created for item in second)
    feature_front, feature_body = parse_ticket_file(tmp_path / "kanban" / "TASK-2.md")
    assert feature_front["skills"] == ["supergoal", "superqa"]
    assert feature_front["blocked_by"] == ["TASK-1"]
    assert "## Dependencies\n\n- TASK-1" in feature_body
    assert len(list((tmp_path / "kanban").glob("TASK-*.md"))) == 2
    assert before_second_sync == {
        path.name: path.read_bytes() for path in (tmp_path / "kanban").glob("TASK-*.md")
    }


def test_sync_frontier_only_imports_only_currently_unblocked_tickets(
    tmp_path: Path,
) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    _ticket(tickets / "001-foundation.md", "foundation", "Foundation")
    _ticket(tickets / "002-feature.md", "feature", "Feature", ("foundation",))
    tracker = _tracker(tmp_path / "kanban")

    results = sync_wayfinder(
        tickets.parent, tracker, prefix="TASK", all_tickets=False
    )

    assert [item.key for item in results] == ["foundation"]
    assert [path.name for path in (tmp_path / "kanban").glob("TASK-*.md")] == [
        "TASK-1.md"
    ]


def test_sync_refuses_to_overwrite_ready_ticket_with_corrupt_managed_boundary(
    tmp_path: Path,
) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    _ticket(tickets / "001-feature.md", "feature", "Feature")
    tracker = _tracker(tmp_path / "kanban")
    sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    path = tmp_path / "kanban" / "TASK-1.md"
    original = path.read_text(encoding="utf-8").replace(
        "<!-- /symphony-factory-managed -->", "operator-owned content"
    )
    path.write_text(original, encoding="utf-8")

    try:
        sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    except ValueError as exc:
        assert "managed end marker is missing" in str(exc)
    else:
        raise AssertionError("corrupt managed boundary was overwritten")
    assert path.read_text(encoding="utf-8") == original


def test_sync_refuses_ready_ticket_with_edited_managed_content(tmp_path: Path) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    source = tickets / "001-feature.md"
    _ticket(source, "feature", "Feature")
    tracker = _tracker(tmp_path / "kanban")
    sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    path = tmp_path / "kanban" / "TASK-1.md"
    original = path.read_text(encoding="utf-8").replace(
        "The slice is independently verified.", "human edited managed text"
    )
    path.write_text(original, encoding="utf-8")
    _ticket(source, "feature", "Renamed feature")

    with pytest.raises(ValueError, match="managed fields or region were edited"):
        sync_wayfinder(tickets.parent, tracker, prefix="TASK")

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("title", "Operator title"),
        ("labels", ["human"]),
        ("skills", ["supergoal"]),
        ("blocked_by", ["TASK-99"]),
    ),
)
def test_sync_refuses_ready_ticket_with_edited_managed_frontmatter(
    field: str, replacement: object, tmp_path: Path
) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    source = tickets / "001-feature.md"
    _ticket(source, "feature", "Feature")
    tracker = _tracker(tmp_path / "kanban")
    sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    path = tmp_path / "kanban" / "TASK-1.md"
    front, body = parse_ticket_file(path)
    front[field] = replacement
    from symphony.trackers.file import write_ticket_atomic

    write_ticket_atomic(path, front, body)
    original = path.read_bytes()
    _ticket(source, "feature", "Refreshed feature")

    with pytest.raises(ValueError, match="managed fields or region were edited"):
        sync_wayfinder(tickets.parent, tracker, prefix="TASK")

    assert path.read_bytes() == original


def test_sync_accepts_source_paths_with_spaces(tmp_path: Path) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    _ticket(tickets / "001 user feature.md", "feature", "Feature")
    tracker = _tracker(tmp_path / "kanban")

    first = sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    second = sync_wayfinder(tickets.parent, tracker, prefix="TASK")

    assert [item.created for item in first] == [True]
    assert [item.created for item in second] == [False]


@pytest.mark.parametrize("prefix", ("", "../ESCAPE", "nested/TASK", "TASK 1"))
def test_sync_rejects_unsafe_identifier_prefix(prefix: str, tmp_path: Path) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    _ticket(tickets / "001-feature.md", "feature", "Feature")
    tracker = _tracker(tmp_path / "kanban")

    with pytest.raises(ValueError, match="ticket prefix"):
        sync_wayfinder(tickets.parent, tracker, prefix=prefix)

    assert not list(tmp_path.rglob("ESCAPE-*.md"))
    assert not list((tmp_path / "kanban").glob("*.md"))


def test_sync_restores_refreshed_cards_when_later_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    source = tickets / "001-existing.md"
    _ticket(source, "existing", "Existing")
    tracker = _tracker(tmp_path / "kanban")
    sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    existing = tmp_path / "kanban" / "TASK-1.md"
    before = existing.read_bytes()
    _ticket(source, "existing", "Refreshed")
    _ticket(tickets / "002-new.md", "new", "New")
    monkeypatch.setattr(
        tracker,
        "create_with_next_identifier",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        sync_wayfinder(tickets.parent, tracker, prefix="TASK", all_tickets=True)

    assert existing.read_bytes() == before


def test_sync_refreshes_dependencies_in_managed_frontmatter(tmp_path: Path) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    base = tickets / "001-base.md"
    feature = tickets / "002-feature.md"
    _ticket(base, "base", "Base")
    _ticket(feature, "feature", "Feature")
    tracker = _tracker(tmp_path / "kanban")
    sync_wayfinder(tickets.parent, tracker, prefix="TASK", all_tickets=True)
    _ticket(feature, "feature", "Feature", ("base",))

    sync_wayfinder(tickets.parent, tracker, prefix="TASK", all_tickets=True)

    front, _ = parse_ticket_file(tmp_path / "kanban" / "TASK-2.md")
    assert front["blocked_by"] == ["TASK-1"]


def test_sync_preserves_state_and_worker_notes_on_source_update(tmp_path: Path) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    source = tickets / "001-feature.md"
    _ticket(source, "feature", "Feature")
    tracker = _tracker(tmp_path / "kanban")
    sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    tracker.transition("TASK-1", "Build")
    issue = tracker.fetch_issue_full_by_id("TASK-1")
    assert issue is not None
    tracker.append_note(issue, "Implementation", "worker note")
    _ticket(source, "feature", "Renamed feature")

    sync_wayfinder(tickets.parent, tracker, prefix="TASK")

    front, body = parse_ticket_file(tmp_path / "kanban" / "TASK-1.md")
    assert front["state"] == "Build"
    assert front["title"] == "Feature"
    assert "worker note" in body


def test_sync_validates_whole_graph_before_writing(tmp_path: Path) -> None:
    tickets = tmp_path / "wayfinder" / "tickets"
    tickets.mkdir(parents=True)
    _ticket(tickets / "001-feature.md", "feature", "Feature", ("missing",))
    tracker = _tracker(tmp_path / "kanban")

    try:
        sync_wayfinder(tickets.parent, tracker, prefix="TASK")
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("invalid graph was accepted")
    assert not list((tmp_path / "kanban").glob("TASK-*.md"))
