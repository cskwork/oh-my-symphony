"""Coverage contracts for terminal UI behavior."""
# ruff: noqa: F405

from tests.coverage_cases._surfaces_support import *  # noqa: F403


def test_tui_helpers_render_user_visible_fallbacks_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _attention_label(None) == ""
    assert _first_meaningful_line("# heading\n```\n---") == ""
    assert _compact_rate_limits({"window": {}, "used": []}) == "n/a"

    text = Text()
    _append_attention_meta(text, None, include_due_at=True)
    _append_attention_meta(
        text,
        {
            "kind": "retry",
            "message": "backend unavailable",
            "severity": "unexpected",
            "due_at": "soon",
        },
        include_due_at=True,
    )
    assert text.plain == "! retry: backend unavailable  due soon"

    token_text = Text()
    _append_token_meta(
        token_text,
        _CardStatus(input_tokens=1_000, output_tokens=250, tokens=1_250),
        dim=True,
    )
    assert token_text.plain == "in=1,000 / out=250 / total=1,250"

    assert _stage_position("Todo", None) is None
    cfg = SimpleNamespace(tracker=SimpleNamespace(active_states=[]))
    assert _stage_position("Todo", cfg) is None  # type: ignore[arg-type]

    class Tracker:
        def __init__(self) -> None:
            self.closed = False

        def fetch_candidate_issues(self) -> list[Issue]:
            return [_issue()]

        def fetch_issues_by_states(self, states: Any) -> list[Issue]:
            assert states == ("Done",)
            return [_issue(state="Done")]

        def close(self) -> None:
            self.closed = True

    trackers: list[Tracker] = []

    def build(_cfg: Any) -> Tracker:
        tracker = Tracker()
        trackers.append(tracker)
        return tracker

    monkeypatch.setattr("symphony.tui.helpers.build_tracker_client", build)
    cfg = SimpleNamespace(tracker=SimpleNamespace(terminal_states=("Done",)))
    assert _fetch_candidates(cfg)[0].identifier == "ISSUE-1"  # type: ignore[arg-type]
    assert _fetch_terminals(cfg)[0].state == "Done"  # type: ignore[arg-type]
    assert [tracker.closed for tracker in trackers] == [True, True]


def test_tui_detail_and_stats_surfaces_render_edge_states() -> None:
    status = _CardStatus(
        runtime="running",
        turn=3,
        tokens=1_250,
        input_tokens=1_000,
        output_tokens=250,
        last_message="Deployment finished",
        attention={"label": "Review", "severity": "info"},
    )
    screen = TicketDetailScreen(_issue(), status, "en")
    meta = screen._meta_text().plain
    assert "P2" in meta
    assert "#release #ui" in meta
    assert "in=1,000 / out=250 / total=1,250" in meta
    assert "Deployment finished" in meta

    assert _fmt_seconds(None) == "-"
    assert _fmt_seconds(30) == "30s"
    assert _fmt_seconds(90) == "1.5m"
    assert _fmt_seconds(7_200) == "2.0h"

    stats = StatsScreen(
        {
            "by_state": [
                {
                    "state": "todo",
                    "total_tokens": 100,
                    "turns": 2,
                    "runs": 1,
                    "avg_run_seconds": 30,
                    "avg_dwell_seconds": 90,
                }
            ],
            "by_agent": [
                {"agent": "codex", "total_tokens": 100, "turns": 2, "runs": 1}
            ],
            "by_day": [{"date": "2026-08-24", "total": 100, "turns": 2, "done": 1}],
        },
        {"todo": "Todo"},
    )
    assert stats._state_table().row_count == 1
    assert stats._display_state("TODO") == "Todo"
    assert stats._agent_table().row_count == 1
    assert stats._day_table().row_count == 1


@pytest.mark.asyncio
async def test_tui_new_issue_modal_validates_and_submits_visible_form() -> None:
    screen = NewIssueScreen(["Todo", "Done"], ["codex"])
    app = _ScreenHost(screen)
    async with app.run_test() as pilot:
        screen.action_submit()
        assert app.screen is screen
        screen.query_one("#ni-title", Input).value = "Ship release"
        screen.query_one("#ni-description", TextArea).text = "Visible behavior"
        screen.query_one("#ni-labels", Input).value = "release, ui"
        screen.on_input_submitted(cast(Any, SimpleNamespace()))
        await pilot.pause()
    assert app.result == {
        "title": "Ship release",
        "description": "Visible behavior",
        "state": "Todo",
        "priority": None,
        "agent_kind": "",
        "labels": ["release", "ui"],
    }


@pytest.mark.asyncio
async def test_tui_new_issue_modal_cancel_button_returns_no_form() -> None:
    screen = NewIssueScreen(["Todo"], ["codex"])
    app = _ScreenHost(screen)
    async with app.run_test() as pilot:
        screen.on_button_pressed(
            SimpleNamespace(button=screen.query_one("#ni-cancel"))  # type: ignore[arg-type]
        )
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_tui_new_issue_modal_keyboard_cancel_returns_no_form() -> None:
    screen = NewIssueScreen(["Todo"], ["codex"])
    app = _ScreenHost(screen)
    async with app.run_test() as pilot:
        screen.action_cancel()
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_tui_edit_modal_round_trips_operator_changes() -> None:
    issue = _issue(agent_kind="codex")
    screen = EditIssueScreen(issue, [], ["codex", "claude"])
    app = _ScreenHost(screen)
    async with app.run_test() as pilot:
        assert screen.query_one("#ei-title", Input).value == issue.title
        screen.query_one("#ei-title", Input).value = "Updated title"
        screen.query_one("#ei-description", TextArea).text = "Updated description"
        screen.query_one("#ei-priority", Select).value = 3
        screen.query_one("#ei-labels", Input).value = "safe, release"
        screen.action_submit()
        await pilot.pause()
    assert app.result == {
        "title": "Updated title",
        "description": "Updated description",
        "state": "Todo",
        "priority": 3,
        "agent_kind": "codex",
        "labels": ["safe", "release"],
    }


@pytest.mark.asyncio
async def test_tui_edit_modal_cancel_button_returns_no_changes() -> None:
    screen = EditIssueScreen(_issue(), ["Todo"], ["codex"])
    app = _ScreenHost(screen)
    async with app.run_test() as pilot:
        screen.on_button_pressed(
            SimpleNamespace(button=screen.query_one("#ei-cancel"))  # type: ignore[arg-type]
        )
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_tui_edit_modal_keyboard_submit_validates_before_button_save() -> None:
    screen = EditIssueScreen(_issue(), ["Todo"], ["codex"])
    app = _ScreenHost(screen)
    async with app.run_test() as pilot:
        screen.query_one("#ei-title", Input).value = ""
        screen.on_input_submitted(cast(Any, SimpleNamespace()))
        assert app.screen is screen
        screen.query_one("#ei-title", Input).value = "Saved"
        screen.on_button_pressed(
            cast(Any, SimpleNamespace(button=screen.query_one("#ei-save")))
        )
        await pilot.pause()
    assert app.result["title"] == "Saved"


@pytest.mark.asyncio
async def test_tui_edit_modal_keyboard_cancel_returns_no_changes() -> None:
    screen = EditIssueScreen(_issue(), ["Todo"], ["codex"])
    app = _ScreenHost(screen)
    async with app.run_test() as pilot:
        screen.action_cancel()
        await pilot.pause()
    assert app.result is None


@pytest.mark.parametrize(
    ("issue", "status", "visible"),
    [
        (
            _issue(),
            _CardStatus(
                runtime="running",
                paused=True,
                agent_kind="codex",
                turn=2,
                attempt_kind="continuation",
                attempt_turn=3,
                last_event="tool completed",
                last_event_at=datetime.now(timezone.utc) - timedelta(seconds=120),
                input_tokens=10,
                output_tokens=5,
                tokens=15,
                attention={"label": "Review", "message": "needed"},
            ),
            ("paused", "codex", "continuation 3", "tool completed", "Review"),
        ),
        (
            _issue(),
            _CardStatus(runtime="retrying", attempt=2, error="backend timeout"),
            ("retry #2", "backend timeout"),
        ),
        (_issue(state="Document", labels=()), _CardStatus(), ("S to skip Document",)),
        (_issue(priority=None), _CardStatus(), ("#release", "#ui")),
        (
            _issue(priority=None, labels=()),
            _CardStatus(runtime="completed", input_tokens=1, output_tokens=2, tokens=3),
            ("✓", "in=1 / out=2 / total=3"),
        ),
    ],
)
def test_tui_rich_card_exposes_operator_runtime_context(
    issue: Issue, status: _CardStatus, visible: tuple[str, ...]
) -> None:
    card = IssueCard.__new__(IssueCard)
    card._issue = issue  # type: ignore[attr-defined]
    card._status = status  # type: ignore[attr-defined]
    card._stage_pos = (2, 4)  # type: ignore[attr-defined]
    card._language = "en"  # type: ignore[attr-defined]
    rendered = card._render_rich().plain
    for expected in visible:
        assert expected in rendered


@pytest.mark.asyncio
async def test_tui_widgets_update_runtime_and_detail_contracts(tmp_path: Path) -> None:
    issue = _issue(state="Document")
    status = _CardStatus(
        runtime="running",
        last_event_at=datetime.now(timezone.utc) - timedelta(seconds=120),
    )
    cfg = SimpleNamespace(
        tui=SimpleNamespace(language="en"),
        agent=SimpleNamespace(kind="codex"),
        tracker=SimpleNamespace(kind="file"),
        workflow_path=tmp_path / "WORKFLOW.md",
    )

    class WidgetHost(App[None]):
        def compose(self):
            yield IssueCard(issue, status, "en")
            yield StatsBar()
            yield DetailPane()
            yield Lane("Todo", "blue", None)

    app = WidgetHost()
    async with app.run_test() as pilot:
        card = app.query_one(IssueCard)
        assert card.issue is issue
        assert card.status is status
        card.set_stage_pos((1, 2))
        assert card.stage_pos == (1, 2)
        assert "silent" in card._render_compact().plain
        card.update_status(_CardStatus())
        assert "S skip" in card._render_compact().plain
        card.on_click()
        await pilot.pause()
        assert app.focused is card
        assert app.query_one(Lane).card_count == 0

        stats = app.query_one(StatsBar)
        stats.update_from(
            cfg,  # type: ignore[arg-type]
            {
                "counts": {"running": 1, "retrying": 0},
                "running": [{"issue_id": "ISSUE-1", "paused": False}],
                "codex_totals": {},
                "rate_limits": {"remaining": 10},
            },
        )

        pane = app.query_one(DetailPane)
        pane.show_for(
            issue,
            _CardStatus(
                runtime="retrying",
                agent_kind="codex",
                attempt=2,
                error="timeout",
                tokens=5,
                attention={"label": "Review", "message": "inspect"},
                last_message="last output",
            ),
            (1, 2),
        )
        await pilot.pause()
        assert "P2" in str(pane._meta.content)
        assert "last output" in str(pane._body.content)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_CardStatus(runtime="running", paused=True, agent_kind="codex"), "⏸"),
        (_CardStatus(runtime="running"), "●"),
        (_CardStatus(runtime="completed"), "✓"),
        (_CardStatus(runtime="idle", tokens=10), "10t"),
    ],
)
def test_tui_compact_card_exposes_runtime_state(
    status: _CardStatus, expected: str
) -> None:
    card = IssueCard.__new__(IssueCard)
    card._issue = _issue()  # type: ignore[attr-defined]
    card._status = status  # type: ignore[attr-defined]
    card._stage_pos = (1, 3)  # type: ignore[attr-defined]
    assert expected in card._render_compact().plain


def test_tui_rich_card_exposes_blockers_and_safe_description_preview() -> None:
    card = IssueCard.__new__(IssueCard)
    card._issue = _issue(  # type: ignore[attr-defined]
        priority=None,
        labels=(),
        blocked_by=(BlockerRef(id="blocker", identifier="BLOCK-1", state="Todo"),),
    )
    card._status = _CardStatus(last_message="Waiting")  # type: ignore[attr-defined]
    card._stage_pos = None  # type: ignore[attr-defined]
    card._language = "en"  # type: ignore[attr-defined]
    rendered = card._render_rich().plain
    assert "blocked by BLOCK-1" in rendered
    assert "User-visible summary" in rendered
    assert "Waiting" in rendered


@pytest.mark.asyncio
async def test_tui_app_degrades_refresh_and_attention_failures_without_trapping_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = SimpleNamespace(
        tui=SimpleNamespace(visible_lanes=2, language="unsupported"),
        tracker=SimpleNamespace(terminal_states=("Done",)),
    )

    class WorkflowState:
        def current(self) -> Any:
            return cfg

    class Orchestrator:
        def __init__(self) -> None:
            self.snapshot_value: Any = {"counts": {"running": 2, "retrying": 1}}
            self.running = (_issue(id="duplicate", identifier="DUP"),)

        def snapshot(self) -> dict[str, Any]:
            if isinstance(self.snapshot_value, Exception):
                raise self.snapshot_value
            return self.snapshot_value

        def iter_running_issues(self) -> tuple[Issue, ...]:
            return self.running

        def issue_attention(self, _issue_value: Issue) -> Any:
            raise RuntimeError("attention unavailable")

    orchestrator = Orchestrator()
    app = KanbanApp(orchestrator, WorkflowState())  # type: ignore[arg-type]
    duplicate = _issue(id="duplicate", identifier="DUP")
    app._candidates = [duplicate]
    app._terminal_issues = [_issue(id="done", identifier="DONE", state="Done")]
    assert [issue.identifier for issue in app._all_known_issues()] == ["DUP", "DONE"]

    existing = _CardStatus(attention={"label": "Existing"})
    assert app._card_status_for_issue(duplicate, {duplicate.id: existing}) is existing
    fallback = app._card_status_for_issue(duplicate, {})
    assert fallback.attention is None

    assert app._busy_worker_count() == 3
    orchestrator.snapshot_value = RuntimeError("snapshot unavailable")
    assert app._busy_worker_count() == 0
    app._quit_armed = True
    app._disarm_quit()
    assert app._quit_armed is False

    monkeypatch.setattr(
        app,
        "post_message",
        lambda _message: (_ for _ in ()).throw(RuntimeError("closed")),
    )
    await app._on_orchestrator_tick()
    monkeypatch.setattr(
        tui_app,
        "_fetch_tracker_snapshot",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("tracker unavailable")),
    )
    await app._refresh_tracker(cfg)  # type: ignore[arg-type]


def test_tui_app_pagination_language_and_zoom_actions_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = SimpleNamespace(
        tui=SimpleNamespace(visible_lanes=2, language="unsupported"),
        tracker=SimpleNamespace(terminal_states=("Done",)),
    )
    ws = SimpleNamespace(current=lambda: cfg)
    orch = SimpleNamespace(
        snapshot=lambda: {"counts": {}}, iter_running_issues=lambda: ()
    )
    app = KanbanApp(orch, ws)  # type: ignore[arg-type]
    applied: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(app, "_apply_lane_widths", lambda: applied.append("apply"))
    monkeypatch.setattr(app, "_notify_page", lambda **_kwargs: notices.append("page"))
    monkeypatch.setattr(app, "_refresh_runtime", lambda: applied.append("refresh"))
    monkeypatch.setattr(app, "_kick_tracker_refresh", lambda: applied.append("tracker"))
    monkeypatch.setattr(
        app,
        "notify",
        lambda message, **_kwargs: notices.append(str(message)),
    )

    assert app._window_indices() == set()
    assert app._page_count() == 1
    app.action_next_page()
    app.action_prev_page()

    app._lane_order = ["todo", "doing", "review", "done"]
    app._lanes = cast(
        Any,
        {key: SimpleNamespace(display=(key != "todo")) for key in app._lane_order},
    )
    app._window_start = 99
    assert app._window_indices() == {0, 1}
    app.action_zoom_lane(-1)
    assert app._zoomed_lane is None
    app.action_zoom_lane(0)
    assert app._zoomed_lane == "todo"
    app.action_zoom_lane(0)
    assert app._zoomed_lane is None
    app.action_reset_zoom()

    app._zoomed_lane = "todo"
    app.action_next_page()
    assert app._window_start == 2
    assert app._zoomed_lane is None
    app.action_next_page()
    assert app._window_start == 0
    app.action_prev_page()
    assert app._window_start == 2
    app.action_prev_page()
    assert app._window_start == 0

    app._window_size = len(app._lane_order)
    app.action_grow_window()
    app._window_size = 1
    app.action_shrink_window()

    app._window_size = 2
    app.action_grow_window()
    assert app._window_size == 3
    app.action_shrink_window()
    assert app._window_size == 2

    app.action_refresh()
    app.action_help()
    app._zoomed_lane = "todo"
    app.action_reset_zoom()
    assert app._zoomed_lane is None
    original_density = app._density
    app.action_toggle_density()
    assert app._density != original_density

    app.action_toggle_language()
    assert app._language_override == tui_app.SUPPORTED_LANGUAGES[0]
    assert app._effective_language() == tui_app.SUPPORTED_LANGUAGES[0]
    assert any(note.startswith("language:") for note in notices)


@pytest.mark.asyncio
async def test_tui_unfocused_actions_and_scrolls_are_safe_noops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = SimpleNamespace(
        tui=SimpleNamespace(visible_lanes=2, language="en"),
        agent=SimpleNamespace(kind="codex"),
        tracker=SimpleNamespace(kind="linear"),
        poll_interval_ms=30_000,
    )
    ws = SimpleNamespace(current=lambda: cfg)
    orch = SimpleNamespace(
        snapshot=lambda: {"counts": {}},
        iter_running_issues=lambda: (),
        issue_attention=lambda _issue: None,
        add_observer=lambda _observer: None,
    )

    class ActionHost(KanbanApp):
        def compose(self):
            yield Static("empty board")

        def on_mount(self) -> None:
            return None

    app = ActionHost(orch, ws)  # type: ignore[arg-type]
    notices: list[str] = []
    async with app.run_test() as pilot:
        monkeypatch.setattr(
            app, "notify", lambda message, **_kwargs: notices.append(str(message))
        )
        app.action_archive_focused()
        app.action_confirm_done_focused()
        app.action_skip_document_focused()
        app.action_edit_focused()
        app.action_toggle_pause_focused()
        assert notices.count("focus a card first") == 5

        monkeypatch.setattr(app, "_refresh_runtime", lambda: notices.append("refresh"))
        app.on__refresh_now(cast(Any, _RefreshNow()))
        app._detail_visible = True
        app._refresh_detail_pane()
        assert app._find_card_by_id("missing") is None
        app.action_focus_detail()

        app.on_input_submitted(
            cast(Any, SimpleNamespace(input=SimpleNamespace(id="other")))
        )
        app.action_scroll_down()
        app.action_scroll_up()
        app.action_page_down()
        app.action_page_up()
        app.action_scroll_top()
        app.action_scroll_bottom()
        assert app._focused_scroll() is None
        await pilot.pause()

    app._ws = cast(Any, SimpleNamespace(current=lambda: None))
    app._kick_tracker_refresh()
    app._refresh_runtime()


@pytest.mark.asyncio
async def test_tui_mutation_workers_report_success_rejection_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        tracker=SimpleNamespace(archive_state="Archived"),
        tui=SimpleNamespace(visible_lanes=2),
    )
    outcomes: list[Any] = [
        RuntimeError("skip failed"),
        (False, "not eligible"),
        (True, "skipped"),
    ]

    async def skip_document(_identifier: str) -> tuple[bool, str]:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    app = KanbanApp(
        cast(Any, SimpleNamespace(skip_document=skip_document)),
        cast(Any, SimpleNamespace(current=lambda: cfg)),
    )
    notices: list[str] = []
    refreshes: list[bool] = []
    monkeypatch.setattr(
        app, "notify", lambda message, **_kwargs: notices.append(str(message))
    )
    monkeypatch.setattr(app, "_kick_tracker_refresh", lambda: refreshes.append(True))
    issue = _issue(identifier="MUTATE-1")

    monkeypatch.setattr(
        app,
        "_call_update_state",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("tracker failed")),
    )
    await app._archive_issue(cfg, issue)  # type: ignore[arg-type]
    await app._confirm_done_issue(cfg, issue)  # type: ignore[arg-type]
    assert "archive failed: tracker failed" in notices
    assert "confirm failed: tracker failed" in notices

    monkeypatch.setattr(app, "_call_update_state", lambda *_args: None)
    await app._archive_issue(cfg, issue)  # type: ignore[arg-type]
    await app._confirm_done_issue(cfg, issue)  # type: ignore[arg-type]
    assert "archived MUTATE-1" in notices
    assert "confirmed MUTATE-1 as Done" in notices

    await app._skip_document_issue(issue)
    await app._skip_document_issue(issue)
    await app._skip_document_issue(issue)
    assert "skip failed: skip failed" in notices
    assert "not eligible" in notices
    assert "skipped" in notices
    assert len(refreshes) == 3


@pytest.mark.asyncio
async def test_tui_focused_action_guards_explain_why_mutation_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = SimpleNamespace(
        tui=SimpleNamespace(visible_lanes=2, language="en"),
        agent=SimpleNamespace(kind="codex"),
        tracker=SimpleNamespace(
            kind="file",
            archive_state="Done",
            active_states=("Todo",),
            terminal_states=("Done",),
        ),
        poll_interval_ms=30_000,
    )

    class WorkflowState:
        current_cfg: Any = cfg

        def current(self) -> Any:
            return self.current_cfg

    class Orchestrator:
        paused = False
        running_id: str | None = None

        def add_observer(self, _observer: Any) -> None:
            return None

        def snapshot(self) -> dict[str, Any]:
            return {"counts": {}, "running": [], "retrying": [], "codex_totals": {}}

        def iter_running_issues(self) -> tuple[Issue, ...]:
            return ()

        def find_running_issue_id(self, _identifier: str) -> str | None:
            return self.running_id

        def is_paused(self, _issue_id: str) -> bool:
            return self.paused

        def resume_worker(self, _issue_id: str) -> bool:
            return False

        def pause_worker(self, _issue_id: str) -> bool:
            return False

    issue = _issue(identifier="FOCUS-1", state="Done")

    class FocusedHost(KanbanApp):
        def compose(self):
            yield IssueCard(issue, _CardStatus(runtime="running"), "en")

    ws = WorkflowState()
    orch = Orchestrator()
    app = FocusedHost(cast(Any, orch), cast(Any, ws))
    notices: list[str] = []
    async with app.run_test() as pilot:
        monkeypatch.setattr(
            app, "notify", lambda message, **_kwargs: notices.append(str(message))
        )
        card = app.query_one(IssueCard)
        card.focus()
        await pilot.pause()

        ws.current_cfg = None
        app.action_archive_focused()
        app.action_confirm_done_focused()
        app.action_edit_focused()
        app.action_new_issue()
        app.action_stats()

        ws.current_cfg = cfg
        app.action_archive_focused()
        card._issue = _issue(identifier="FOCUS-1", state="Todo")
        app.action_skip_document_focused()
        cfg.tracker.kind = "linear"
        app.action_edit_focused()
        cfg.tracker.kind = "file"
        orch.running_id = "running"
        app.action_edit_focused()

        workers: list[str] = []

        def run_worker(awaitable: Any, **kwargs: Any) -> None:
            awaitable.close()
            workers.append(str(kwargs.get("group")))

        monkeypatch.setattr(app, "run_worker", run_worker)
        orch.running_id = None
        card._issue = _issue(identifier="FOCUS-1", state="Document")
        app.action_skip_document_focused()

        captured_callback: list[Any] = []

        def push_screen(_screen: Any, callback: Any = None) -> None:
            captured_callback.append(callback)

        monkeypatch.setattr(app, "push_screen", push_screen)
        card._issue = _issue(identifier="FOCUS-1", state="Todo")
        app.action_edit_focused()
        assert captured_callback and callable(captured_callback[0])
        captured_callback[0]({"title": "Edited"})
        assert workers == ["skip_document", "edit_issue"]

        orch.paused = True
        app.action_toggle_pause_focused()
        orch.paused = False
        app.action_toggle_pause_focused()
        await pilot.pause()

    assert "already archived" in notices
    assert any("only Document cards" in notice for notice in notices)
    assert any("requires tracker.kind: file" in notice for notice in notices)
    assert any("is running; wait before editing" in notice for notice in notices)
    assert "resume had no effect" in notices
    assert "pause had no effect" in notices


def test_tui_tracker_state_update_always_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    class Client:
        def update_state(self, issue: Issue, target: str) -> None:
            calls.append((issue.identifier, target))

        def close(self) -> None:
            calls.append("closed")

    monkeypatch.setattr(tui_app, "build_tracker_client", lambda _cfg: Client())
    cfg = SimpleNamespace()
    KanbanApp._call_update_state(cfg, _issue(identifier="STATE-1"), "Done")  # type: ignore[arg-type]
    assert calls == [("STATE-1", "Done"), "closed"]


@pytest.mark.asyncio
async def test_tui_file_issue_create_and_update_surface_failures_and_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        tracker=SimpleNamespace(kind="file"),
        tui=SimpleNamespace(visible_lanes=2),
    )
    app = KanbanApp(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace(current=lambda: cfg)),
    )
    notices: list[str] = []
    refreshes: list[bool] = []
    monkeypatch.setattr(
        app, "notify", lambda message, **_kwargs: notices.append(str(message))
    )
    monkeypatch.setattr(app, "_kick_tracker_refresh", lambda: refreshes.append(True))

    class FailingTracker:
        def __init__(self, _cfg: Any) -> None:
            raise RuntimeError("tracker unavailable")

    monkeypatch.setattr(file_tracker_module, "FileBoardTracker", FailingTracker)
    form = {
        "title": "Created",
        "description": "description",
        "state": "Todo",
        "priority": None,
        "labels": [],
        "agent_kind": "",
    }
    await app._create_issue(cfg, form)  # type: ignore[arg-type]
    assert "create failed: tracker unavailable" in notices

    updates: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []

    class Tracker:
        def __init__(self, _cfg: Any) -> None:
            return None

        def update_fields(self, _identifier: str, **fields: Any) -> None:
            updates.append(fields)

    class Store:
        def record_transition(self, **fields: Any) -> None:
            transitions.append(fields)

    monkeypatch.setattr(file_tracker_module, "FileBoardTracker", Tracker)
    monkeypatch.setattr(stats_module, "stats_store_for", lambda _path: Store())
    issue = _issue(identifier="EDIT-2", state="Todo")
    updated_form = {
        **form,
        "title": "Updated",
        "state": "Done",
        "priority": 2,
        "labels": ["release"],
        "agent_kind": "codex",
    }
    await app._update_issue(cfg, issue, updated_form)  # type: ignore[arg-type]
    assert updates[0]["state"] == "Done"
    assert transitions == [
        {"issue": "EDIT-2", "from_state": "todo", "to_state": "done"}
    ]
    assert "updated EDIT-2" in notices
    assert refreshes == [True]

    class UpdateFailure(Tracker):
        def update_fields(self, _identifier: str, **_fields: Any) -> None:
            raise RuntimeError("write failed")

    monkeypatch.setattr(file_tracker_module, "FileBoardTracker", UpdateFailure)
    await app._update_issue(cfg, issue, updated_form)  # type: ignore[arg-type]
    assert "edit failed: write failed" in notices


@pytest.mark.asyncio
async def test_tui_wrapper_attaches_logs_and_stops_on_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exits: list[bool] = []
    attached: list[Path] = []

    class App:
        def __init__(self, *_args: Any) -> None:
            return None

        async def run_async(self) -> None:
            raise asyncio.CancelledError

        def exit(self) -> None:
            exits.append(True)

    monkeypatch.setattr(tui_app, "KanbanApp", App)
    monkeypatch.setattr(
        tui_app, "attach_file_handler", lambda _logger, path: attached.append(path)
    )
    monkeypatch.setenv("SYMPHONY_LOG_FILE", str(tmp_path / "custom.log"))
    wrapper = KanbanTUI(cast(Any, SimpleNamespace()), cast(Any, SimpleNamespace()))
    with pytest.raises(asyncio.CancelledError):
        await wrapper.run()
    assert exits == [True]
    assert attached == [tmp_path / "custom.log"]

    monkeypatch.delenv("SYMPHONY_LOG_FILE")
    no_config = KanbanTUI(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace(current=lambda: None)),
    )
    no_config._attach_file_log_sink()
    monkeypatch.setattr(
        tui_app,
        "attach_file_handler",
        lambda *_args: (_ for _ in ()).throw(OSError("read only")),
    )
    monkeypatch.setenv("SYMPHONY_LOG_FILE", str(tmp_path / "broken.log"))
    no_config._attach_file_log_sink()


def test_tui_runtime_none_and_attention_none_are_safe_noops() -> None:
    app = KanbanApp(
        cast(Any, SimpleNamespace(issue_attention=lambda _issue: None)),
        cast(Any, SimpleNamespace(current=lambda: None)),
    )
    app._refresh_runtime()
    status = app._card_status_for_issue(_issue(), {})
    assert status.attention is None


def test_tui_paging_filter_focus_and_scroll_navigation_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = SimpleNamespace(tui=SimpleNamespace(visible_lanes=2, language="en"))
    app = KanbanApp(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace(current=lambda: cfg)),
    )
    applied: list[bool] = []
    monkeypatch.setattr(app, "_apply_lane_widths", lambda: applied.append(True))
    monkeypatch.setattr(app, "_notify_page", lambda **_kwargs: None)
    app._lane_order = ["todo", "done"]
    app._window_size = 1
    app._window_start = 0
    app._zoomed_lane = "todo"
    app._lanes = cast(
        Any,
        {
            "todo": SimpleNamespace(display=False),
            "done": SimpleNamespace(display=True),
        },
    )
    app.action_prev_page()
    assert app._zoomed_lane is None

    focused: list[str] = []

    class FakeLane:
        def __init__(self, *, display: bool, empty: bool, cards: list[Any]) -> None:
            self.display = display
            self.is_empty = empty
            self.cards = cards

        def query(self, _kind: Any) -> list[Any]:
            return self.cards

        def focus(self) -> None:
            focused.append("lane")

    app._lanes = cast(
        Any,
        {
            "hidden": FakeLane(display=False, empty=False, cards=[]),
            "visible": FakeLane(display=True, empty=False, cards=[]),
        },
    )
    app.action_focus_board()
    app.on_input_submitted(
        cast(Any, SimpleNamespace(input=SimpleNamespace(id="filter-input")))
    )
    assert focused == ["lane", "lane"]

    class Bar:
        is_open = False

        def set_visible(self, _visible: bool) -> None:
            return None

        def query_one(self, *_args: Any) -> Any:
            raise RuntimeError("input unavailable")

    bar = Bar()
    monkeypatch.setattr(app, "query_one", lambda *_args: bar)
    app.action_open_filter()
    app._zoomed_lane = "todo"
    app.action_escape()
    assert app._zoomed_lane is None
    monkeypatch.setattr(app, "_refresh_runtime", lambda: None)
    app._close_filter()

    class Scroll:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def scroll_home(self, **kwargs: Any) -> None:
            self.calls.append(("home", kwargs))

        def scroll_end(self, **kwargs: Any) -> None:
            self.calls.append(("end", kwargs))

        def scroll_relative(self, **kwargs: Any) -> None:
            self.calls.append(("relative", kwargs))

    scroll = Scroll()
    monkeypatch.setattr(app, "_focused_scroll", lambda: cast(Any, scroll))
    app.action_scroll_top()
    app.action_scroll_bottom()
    app._scroll_focused(4)
    assert [call[0] for call in scroll.calls] == ["home", "end", "relative"]


def test_tui_wrapper_default_log_path_and_exit_failure_are_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attached: list[Path] = []
    cfg = SimpleNamespace(workflow_path=tmp_path / "project" / "WORKFLOW.md")
    wrapper = KanbanTUI(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace(current=lambda: cfg)),
    )
    monkeypatch.delenv("SYMPHONY_LOG_FILE", raising=False)
    monkeypatch.setattr(
        tui_app, "attach_file_handler", lambda _logger, path: attached.append(path)
    )
    wrapper._attach_file_log_sink()
    assert attached == [tmp_path / "project" / "log" / "symphony.log"]
    wrapper._app = cast(
        Any,
        SimpleNamespace(
            exit=lambda: (_ for _ in ()).throw(RuntimeError("already exiting"))
        ),
    )
    wrapper.request_stop()


@pytest.mark.asyncio
async def test_tui_focused_scroll_prefers_ancestor_then_visible_lane_fallback() -> None:
    cfg = SimpleNamespace(
        tui=SimpleNamespace(visible_lanes=2, language="en"),
        agent=SimpleNamespace(kind="codex"),
        tracker=SimpleNamespace(kind="linear"),
        poll_interval_ms=30_000,
    )
    orch = SimpleNamespace(
        add_observer=lambda _observer: None,
        snapshot=lambda: {
            "counts": {},
            "running": [],
            "retrying": [],
            "codex_totals": {},
        },
        iter_running_issues=lambda: (),
    )

    class Host(KanbanApp):
        class Focusable(Static):
            can_focus = True

        def compose(self):
            with VerticalScroll():
                yield self.Focusable("scroll body")

    app = Host(cast(Any, orch), cast(Any, SimpleNamespace(current=lambda: cfg)))
    async with app.run_test() as pilot:
        scroll = app.query_one(VerticalScroll)
        body = app.query_one(Host.Focusable)
        body.focus()
        await pilot.pause()
        assert app._focused_scroll() is scroll
        scroll.focus()
        await pilot.pause()
        assert app._focused_scroll() is scroll

        app.screen.set_focus(None)

        class MissingLane:
            def query_one(self, _kind: Any) -> Any:
                raise RuntimeError("missing")

        class ScrollLane:
            def query_one(self, _kind: Any) -> VerticalScroll:
                return scroll

        app._lanes = cast(Any, {"missing": MissingLane(), "scroll": ScrollLane()})
        assert app._focused_scroll() is scroll
