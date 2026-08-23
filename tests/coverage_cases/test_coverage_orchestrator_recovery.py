"""Coverage contracts for artifacts, recovery, and eligibility."""
# ruff: noqa: F405

from tests.coverage_cases._orchestrator_support import *  # noqa: F403


def test_artifact_configuration_controls_commit_and_prompt_projection(
    tmp_path: Path,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    disabled = _artifact_cfg(tmp_path, enabled=False)
    enabled = _artifact_cfg(tmp_path)
    assert orchestrator._artifact_commit_excludes(None) == ()
    assert orchestrator._artifact_commit_excludes(disabled) == ()
    assert orchestrator._artifact_commit_excludes(enabled) == (".deliverables",)
    subject._artifact_store = None
    assert orchestrator._prompt_artifacts_dir(enabled) == ""
    subject._artifact_store = SimpleNamespace()
    assert orchestrator._prompt_artifacts_dir(disabled) == ""
    assert orchestrator._prompt_artifacts_dir(enabled) == ".deliverables"


@pytest.mark.asyncio
async def test_artifact_collection_is_best_effort_for_scan_and_projection_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _artifact_cfg(tmp_path)

    class Store:
        root = tmp_path / "store"

        def __init__(self) -> None:
            self.mode = "raise"

        def collect_from_workspace(self, *_args: Any, **_kwargs: Any) -> CollectResult:
            if self.mode == "raise":
                raise OSError("scan failed")
            if self.mode == "empty":
                return CollectResult(
                    skipped=[
                        ("same.pdf", "duplicate"),
                        (".hidden", "hidden"),
                        ("large.zip", "too_large"),
                    ]
                )
            return CollectResult(collected=[_artifact_record()])

        def list_for(self, _identifier: str) -> list[ArtifactRecord]:
            raise OSError("index unavailable")

    store = Store()
    subject._artifact_store = store
    for mode in ("raise", "empty", "collected"):
        store.mode = mode
        await orchestrator._collect_ticket_artifacts(
            cfg,
            identifier="APP-1",
            workspace_path=tmp_path / "workspace",
            run_id="run-1",
            turn=2,
        )

    projected: list[tuple[str, str]] = []
    store.list_for = lambda _identifier: [_artifact_record()]  # type: ignore[method-assign]
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_upsert_artifacts_section",
        lambda _cfg, identifier, body: projected.append((identifier, body)),
    )
    await orchestrator._collect_ticket_artifacts(
        cfg,
        identifier="APP-1",
        workspace_path=tmp_path / "workspace",
        run_id="run-1",
        turn=2,
    )
    assert projected and "Release report" in projected[0][1]


def test_artifact_markdown_handles_file_links_remote_trackers_and_relpath_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _artifact_cfg(tmp_path)
    cfg.tracker.board_root.mkdir()
    subject._artifact_store = SimpleNamespace(root=tmp_path / "store")
    record = _artifact_record()

    body = orchestrator._artifact_section_body(cfg, "APP-1", [record])
    assert "[Release report](<" in body
    assert "Verified output" in body

    cfg.tracker.kind = "linear"
    body = orchestrator._artifact_section_body(cfg, "APP-1", [record])
    assert "[Release report]" not in body

    cfg.tracker.kind = "file"
    monkeypatch.setattr(
        core_module.os.path,
        "relpath",
        lambda *_a: (_ for _ in ()).throw(ValueError("different drives")),
    )
    body = orchestrator._artifact_section_body(cfg, "APP-1", [record])
    assert body.startswith("- Release report")
    subject._artifact_store = None
    assert orchestrator._artifact_section_body(cfg, "APP-1", [record]) == ""


def test_artifact_tracker_projection_closes_optional_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    client = SimpleNamespace(
        upsert_artifacts_section=lambda identifier, body: calls.append(
            (identifier, body)
        ),
        close=lambda: calls.append(("closed", "")),
    )
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    core_module.Orchestrator._tracker_call_upsert_artifacts_section(
        _cfg(), "APP-1", "- report"
    )
    assert calls == [("APP-1", "- report"), ("closed", "")]

    calls.clear()
    client = SimpleNamespace(close=lambda: calls.append(("closed", "")))
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    core_module.Orchestrator._tracker_call_upsert_artifacts_section(
        _cfg(), "APP-1", "- report"
    )
    assert calls == [("closed", "")]


@pytest.mark.asyncio
async def test_artifact_sweep_fails_closed_on_tracker_and_store_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _artifact_cfg(tmp_path)
    store = SimpleNamespace(
        sweep=lambda **_kwargs: (_ for _ in ()).throw(OSError("delete denied"))
    )
    subject._artifact_store = store
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_retained_identifiers",
        lambda _cfg: (_ for _ in ()).throw(OSError("tracker offline")),
    )
    await orchestrator._artifact_sweep(cfg)
    monkeypatch.setattr(
        orchestrator, "_tracker_call_retained_identifiers", lambda _cfg: set()
    )
    await orchestrator._artifact_sweep(cfg)
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_retained_identifiers",
        lambda _cfg: {"APP-1"},
    )
    await orchestrator._artifact_sweep(cfg)


def test_artifact_retention_excludes_archived_tickets_and_closes_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    archived = _issue("APP-2", state="Archive")
    client = SimpleNamespace(
        list_all_identifiers=lambda: ["APP-1", "APP-2"],
        fetch_issues_by_states=lambda _states: [archived],
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    assert core_module.Orchestrator._tracker_call_retained_identifiers(_cfg()) == {
        "APP-1"
    }
    assert closed == [True]

    closed.clear()
    client = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    assert core_module.Orchestrator._tracker_call_retained_identifiers(_cfg()) is None
    assert closed == [True]


def test_artifact_git_exclude_is_anchored_idempotent_and_newline_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _artifact_cfg(tmp_path)
    common = tmp_path / ".git"
    monkeypatch.setattr(core_module.git_inspect, "git_common_dir", lambda _root: common)
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("existing-without-newline", encoding="utf-8")

    orchestrator._ensure_artifact_dir_git_excluded(cfg)
    first = exclude.read_text(encoding="utf-8")
    assert first == "existing-without-newline\n/.deliverables/\n"
    orchestrator._ensure_artifact_dir_git_excluded(cfg)
    assert exclude.read_text(encoding="utf-8") == first

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("denied")),
    )
    orchestrator._ensure_artifact_dir_git_excluded(cfg)


def test_token_ema_load_handles_missing_malformed_and_mixed_values(
    tmp_path: Path,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.workflow_path = tmp_path / "WORKFLOW.md"
    orchestrator._load_token_ema(cfg)
    assert subject._token_ema == {} and subject._token_ema_loaded

    path = orchestrator._token_ema_path(cfg)
    path.parent.mkdir()
    path.write_text("not-json", encoding="utf-8")
    orchestrator._load_token_ema(cfg)
    assert subject._token_ema == {}

    path.write_text(
        json.dumps({"VERIFY": "12.5", "bad": "nan-value", "ok": 4}),
        encoding="utf-8",
    )
    orchestrator._load_token_ema(cfg)
    assert subject._token_ema == {"verify": 12.5, "ok": 4.0}


def test_token_ema_update_aliases_and_persist_failure_are_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._token_ema = {}
    persisted: list[bool] = []
    monkeypatch.setattr(
        orchestrator, "_persist_token_ema", lambda _cfg: persisted.append(True)
    )
    orchestrator._update_token_ema("Verify", 0, _cfg())
    orchestrator._update_token_ema("", 10, _cfg())
    orchestrator._update_token_ema("Verify", 100, _cfg())
    assert subject._token_ema["verify"] > 0 and persisted == [True]

    cfg = _cfg()
    cfg.agent.max_total_tokens_by_state = {"learning": 200}
    cfg.agent.max_total_tokens = 100
    cfg.agent.max_state_turns_by_state = {"learn": 3}
    cfg.agent.max_state_turns = 1
    cfg.agent.token_attention_threshold_by_state = {"learning": 80}
    assert orchestrator._token_budget_for_state(cfg, "Learn") == 200
    assert orchestrator._max_state_turns_for_state(cfg, "Learning") == 3
    assert orchestrator._token_attention_threshold_for_state(cfg, "Learn") == 80

    fake_path = SimpleNamespace(
        parent=SimpleNamespace(
            mkdir=lambda **_kwargs: (_ for _ in ()).throw(OSError("denied"))
        )
    )
    monkeypatch.setattr(orchestrator, "_token_ema_path", lambda _cfg: fake_path)
    core_module.Orchestrator._persist_token_ema(orchestrator, cfg)
    cfg.agent.max_total_tokens_by_state = {"learn": 300}
    cfg.agent.max_state_turns_by_state = {"learning": 4}
    cfg.agent.token_attention_threshold_by_state = {"learn": 90}
    assert orchestrator._token_budget_for_state(cfg, "Learning") == 300
    assert orchestrator._max_state_turns_for_state(cfg, "Learn") == 4
    assert orchestrator._token_attention_threshold_for_state(cfg, "Learning") == 90


def test_ticket_prompt_path_and_dispatch_env_handle_optional_tracker_and_json_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.agent.max_total_tokens_by_state = {}
    cfg.agent.max_total_tokens = 100
    subject._token_ema = {"verify": 42.4}
    issue = _issue(state="Verify", description="## Review Findings\n| a | b |")
    subject._tracker = SimpleNamespace(
        find_path=lambda _identifier: tmp_path / "APP-1.md"
    )
    assert orchestrator._ticket_prompt_path(cfg, issue) == str(tmp_path / "APP-1.md")
    cfg.tracker.kind = "linear"
    assert orchestrator._ticket_prompt_path(cfg, issue) is None
    cfg.tracker.kind = "file"
    subject._tracker = SimpleNamespace()
    assert orchestrator._ticket_prompt_path(cfg, issue) is None

    monkeypatch.setattr(
        core_module.json,
        "dumps",
        lambda *_a, **_k: (_ for _ in ()).throw(TypeError("bad row")),
    )
    orchestrator._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=True)
    assert core_module.os.environ["SYMPHONY_TOKEN_EMA"] == "42"
    assert core_module.os.environ["SYMPHONY_REWIND_SCOPE"] == "[]"
    orchestrator._apply_dispatch_env(issue=issue, cfg=cfg, is_rewind=False)
    assert "SYMPHONY_REWIND_SCOPE" not in core_module.os.environ


@pytest.mark.asyncio
async def test_archive_and_legacy_completion_sweeps_isolate_tracker_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.tracker.archive_after_days = 30
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_terminal_issues",
        lambda _cfg: (_ for _ in ()).throw(OSError("tracker offline")),
    )
    await orchestrator._archive_sweep(cfg)

    cfg.tracker.kind = "linear"
    assert await orchestrator._auto_normalize_legacy_human_review_done(cfg) == 0
    cfg.tracker.kind = "file"
    cfg.tracker.terminal_states = ("Archive",)
    assert await orchestrator._auto_normalize_legacy_human_review_done(cfg) == 0

    cfg.tracker.terminal_states = ("Done", "Human Review")
    issue = _issue(
        state="Human Review", description="Confirm Done", issue_id="legacy-id"
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_terminal_issues", lambda _cfg: [issue]
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_append_note",
        lambda *_a: (_ for _ in ()).throw(OSError("write failed")),
    )
    recorded: list[tuple[str, str]] = []
    subject._record_tracker_error = lambda issue_id, exc: recorded.append(
        (issue_id, str(exc))
    )
    assert await orchestrator._auto_normalize_legacy_human_review_done(cfg) == 0
    assert recorded == [("legacy-id", "write failed")]


def test_tracker_optional_mutations_close_clients_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    client = SimpleNamespace(close=lambda: closed.append("closed"))
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    assert not core_module.Orchestrator._tracker_call_set_agent_kind(
        _cfg(), "APP-1", "codex"
    )
    assert closed == ["closed"]

    updated: list[tuple[str, str]] = []
    client = SimpleNamespace(
        update_fields=lambda identifier, **fields: updated.append(
            (identifier, fields["agent_kind"])
        ),
        close=lambda: closed.append("updated-closed"),
    )
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    assert core_module.Orchestrator._tracker_call_set_agent_kind(
        _cfg(), "APP-1", "claude"
    )
    assert updated == [("APP-1", "claude")]

    client = SimpleNamespace(
        create_with_next_identifier=lambda *_a, **_k: "invalid",
        close=lambda: closed.append("create-closed"),
    )
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    with pytest.raises(SymphonyError, match="invalid created-ticket payload"):
        core_module.Orchestrator._tracker_call_create_blocked_rca_issue(
            _cfg(), _issue(state="Blocked"), "In Progress", "Todo", "codex"
        )


def test_blocked_fix_link_reports_adapter_exception_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    client = SimpleNamespace(
        update_fields=lambda *_a, **_k: None,
        fetch_issue_full_by_id=lambda _identifier: (_ for _ in ()).throw(
            OSError("read failed")
        ),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    assert not core_module.Orchestrator._tracker_call_link_blocked_fix(
        _cfg(), _issue(state="Blocked"), "FIX-1"
    )
    assert closed == [True]

    closed.clear()
    client = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(core_module, "build_tracker_client", lambda _cfg: client)
    assert not core_module.Orchestrator._tracker_call_link_blocked_fix(
        _cfg(), _issue(state="Blocked"), "FIX-1"
    )
    assert (
        core_module.Orchestrator._tracker_call_create_blocked_rca_issue(
            _cfg(), _issue(state="Blocked"), "In Progress", "Todo", "codex"
        )
        is None
    )


@pytest.mark.asyncio
async def test_manual_blocked_recovery_reports_reload_running_unknown_and_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._workflow_state = SimpleNamespace(
        current=lambda: None, reload=lambda: (None, "invalid workflow")
    )
    assert await orchestrator.recover_blocked_issue("APP-1") == (
        False,
        "workflow config unavailable: invalid workflow",
        {},
    )
    cfg = _cfg()
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    monkeypatch.setattr(orchestrator, "find_running_issue_id", lambda _identifier: "id")
    assert "running worker" in (await orchestrator.recover_blocked_issue("APP-1"))[1]
    monkeypatch.setattr(orchestrator, "find_running_issue_id", lambda _identifier: None)
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: None
    )
    assert "unknown issue" in (await orchestrator.recover_blocked_issue("APP-1"))[1]

    issue = _issue("APP-1", state="Blocked")
    checks = iter([None, "id"])
    monkeypatch.setattr(
        orchestrator, "find_running_issue_id", lambda _identifier: next(checks)
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: issue
    )
    assert "started running" in (await orchestrator.recover_blocked_issue("APP-1"))[1]


@pytest.mark.asyncio
async def test_blocked_fix_open_rejects_fix_ticket_invalid_lane_and_missing_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._blocked_rca_creation_lock = asyncio.Lock()
    subject._blocked_rca_source_ids = set()
    cfg = _cfg()
    cfg.agent.kind_for_state = lambda _state, _pin: "codex"
    cfg.agent.kind = "codex"
    fix = _issue("FIX-1", state="Blocked", labels=("blocked-fix",))
    assert (
        "already a fix ticket"
        in (await orchestrator._open_blocked_rca_for_issue(cfg, fix))[1]
    )
    source = _issue("APP-1", state="Blocked")
    assert (
        "target_state must be"
        in (
            await orchestrator._open_blocked_rca_for_issue(
                cfg, source, target_state="Unknown"
            )
        )[1]
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_active_rca_for_source", lambda *_a: None
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_create_blocked_rca_issue", lambda *_a: None
    )
    assert (
        "requires a tracker"
        in (await orchestrator._open_blocked_rca_for_issue(cfg, source))[1]
    )


@pytest.mark.asyncio
async def test_auto_blocked_recovery_handles_capacity_partial_hydration_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.agent.auto_recover_blocked = True
    monkeypatch.setattr(orchestrator, "_available_slots", lambda _cfg: 0)
    assert await orchestrator._auto_recover_blocked_sources(cfg) == 0

    monkeypatch.setattr(orchestrator, "_available_slots", lambda _cfg: 3)
    partial = _issue("APP-1", state="Blocked", description=None)
    already_fix = _issue("FIX-1", state="Blocked", labels=("blocked-fix",))
    failed = _issue("APP-2", state="Blocked")
    subject._blocked_rca_source_ids = set()
    subject._history_recovery_attempted = {already_fix.id}
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_terminal_issues",
        lambda _cfg: [partial, already_fix, failed],
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_fetch_issue_full_by_id",
        lambda *_a: replace(partial, description="hydrated"),
    )

    async def recover(_cfg: Any, issue: Issue) -> bool:
        if issue.identifier == "APP-1":
            raise OSError("history probe failed")
        return False

    monkeypatch.setattr(orchestrator, "_recover_blocked_history_gate", recover)

    async def open_fix(_cfg: Any, issue: Issue, **_kwargs: Any) -> Any:
        raise OSError(f"create failed for {issue.identifier}")

    monkeypatch.setattr(orchestrator, "_open_blocked_rca_for_issue", open_fix)
    recorded: list[str] = []
    subject._record_tracker_error = lambda issue_id, _exc: recorded.append(issue_id)
    assert await orchestrator._auto_recover_blocked_sources(cfg) == 0
    assert partial.id in recorded and failed.id in recorded


@pytest.mark.asyncio
async def test_auto_blocked_recovery_stops_after_filling_available_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.agent.auto_recover_blocked = True
    first = _issue("APP-1", state="Blocked")
    second = _issue("APP-2", state="Blocked")
    subject._blocked_rca_source_ids = set()
    subject._history_recovery_attempted = set()
    monkeypatch.setattr(orchestrator, "_available_slots", lambda _cfg: 1)
    monkeypatch.setattr(
        orchestrator, "_tracker_call_terminal_issues", lambda _cfg: [first, second]
    )
    monkeypatch.setattr(
        orchestrator,
        "_recover_blocked_history_gate",
        lambda *_a: asyncio.sleep(0, result=False),
    )
    opened: list[str] = []

    async def open_fix(_cfg: Any, issue: Issue, **_kwargs: Any) -> Any:
        opened.append(issue.identifier)
        return True, "opened", {"identifier": issue.identifier}

    monkeypatch.setattr(orchestrator, "_open_blocked_rca_for_issue", open_fix)
    monkeypatch.setattr(orchestrator, "_clear_tracker_error", lambda _issue_id: None)

    assert await orchestrator._auto_recover_blocked_sources(cfg) == 1
    assert opened == ["APP-1"]


@pytest.mark.asyncio
async def test_blocked_resolution_helpers_fail_closed_on_missing_source_and_unproven_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg(terminal=("Done", "Blocked"))
    rca = _issue(
        "FIX-1",
        state="Done",
        labels=("blocked-fix",),
        description=None,
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: None
    )
    assert await orchestrator._resolved_blocked_rca_issue(cfg, rca) is None

    hydrated = replace(rca, description="## Fix Resolution\nfixed")
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: hydrated
    )
    assert await orchestrator._resolved_blocked_rca_issue(cfg, rca) == hydrated
    monkeypatch.setattr(
        orchestrator, "_tracker_call_fetch_issue_full_by_id", lambda *_a: None
    )

    missing_source = replace(rca, description="## Fix Resolution\nfixed")
    assert (
        await orchestrator._source_issue_for_blocked_rca(cfg, missing_source, {})
        is None
    )
    identified = replace(
        rca,
        description="- Identifier: `APP-1`\n\n## Fix Resolution\nfixed",
    )
    assert await orchestrator._source_issue_for_blocked_rca(cfg, identified, {}) is None

    source = _issue("APP-1", state="Done")
    assert not await orchestrator._reopen_source_for_resolved_rca(cfg, rca, source)
    source_fix = _issue("FIX-SOURCE", state="Blocked", labels=("blocked-fix",))
    assert not await orchestrator._reopen_source_for_resolved_rca(cfg, rca, source_fix)

    unresolved_source = _issue(
        "APP-1",
        state="Blocked",
        description="## Blocked Fix\nwaiting",
    )
    resolved_fix = replace(
        rca,
        description="## Blocked Fix Resolved\nfixed",
    )
    moved: list[str] = []

    async def move(_cfg: Any, _fix: Issue, _source: Issue, **kwargs: Any) -> bool:
        moved.append(kwargs["reason_code"])
        return True

    monkeypatch.setattr(orchestrator, "_move_blocked_fix_out_of_done", move)
    assert await orchestrator._hold_unproven_blocked_fix(
        cfg, resolved_fix, unresolved_source
    )
    assert moved == ["missing_source_resolution"]


@pytest.mark.asyncio
async def test_auto_reopen_and_auto_triage_skip_missing_source_and_record_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg(terminal=("Done", "Blocked"))
    rca = _issue("FIX-1", state="Done", labels=("blocked-fix",))
    monkeypatch.setattr(
        orchestrator, "_tracker_call_terminal_issues", lambda _cfg: [rca]
    )

    async def resolved(_cfg: Any, _issue_row: Issue) -> Issue:
        return rca

    async def no_source(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(orchestrator, "_resolved_blocked_rca_issue", resolved)
    monkeypatch.setattr(orchestrator, "_source_issue_for_blocked_rca", no_source)
    assert await orchestrator._auto_reopen_sources_from_resolved_rcas(cfg) == 0

    cfg.agent.auto_triage_actionable_todo = True
    cfg.tracker.kind = "file"
    cfg.tracker.active_states = ("Todo", "In Progress")
    todo = _issue("APP-1", description="Acceptance criteria: works")
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_append_note",
        lambda *_a: (_ for _ in ()).throw(OSError("write failed")),
    )
    recorded: list[str] = []
    subject._record_tracker_error = lambda issue_id, _exc: recorded.append(issue_id)
    assert not await orchestrator._auto_triage_todo_if_actionable(todo, cfg)
    assert recorded == [todo.id]


@pytest.mark.asyncio
async def test_unpublished_history_downgrade_is_best_effort_and_refreshes_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from symphony.workspace import HistoryGateResult

    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    result = HistoryGateResult(
        status="push_failed",
        branch="symphony/APP-1",
        local_sha="a" * 40,
        remote_sha="",
        detail="remote denied",
        failure_kind="auth",
    )
    issue = _issue(state="Done")
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_append_note",
        lambda *_a: (_ for _ in ()).throw(OSError("tracker offline")),
    )
    refreshed: list[bool] = []
    subject.request_refresh = lambda: refreshed.append(True)
    await orchestrator._flag_unpublished_history(_cfg(), issue, result)
    assert refreshed == []

    monkeypatch.setattr(orchestrator, "_tracker_call_append_note", lambda *_a: None)
    monkeypatch.setattr(orchestrator, "_tracker_call_update_state", lambda *_a: None)
    await orchestrator._flag_unpublished_history(_cfg(), issue, result)
    assert refreshed == [True]


def test_eligibility_ownership_and_contract_checks_explain_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = _cfg()
    cfg.continuous_improvement = SimpleNamespace(require_idle_board=False)
    subject._dispatch_state = SimpleNamespace(
        running={},
        claimed=set(),
        retry={},
        turn_budget_exhausted=set(),
    )
    subject._improvement_task = None
    subject._lease_blocked = {}
    subject._last_registry_error = "open: locked"
    subject._run_registry = None
    subject._paused_issue_ids = set()
    subject._pause_reasons = {}
    subject._terminal_persist_pending = set()
    monkeypatch.setattr(orchestrator, "_has_active_run_lease", lambda _issue_id: False)
    issue = _issue()
    decision = orchestrator._eligibility_ownership_decision(
        issue, cfg, owning_retry=False
    )
    assert decision is not None and decision.code == "registry_unavailable"

    retired = SimpleNamespace(retired=True)
    registry = SimpleNamespace(
        get_release_evidence_identity_by_issue_id=lambda _issue_id: retired
    )
    subject._run_registry = registry
    subject._last_registry_error = None
    subject._registry_guard = lambda _op, fn, _default: fn()
    decision = orchestrator._eligibility_ownership_decision(
        issue, cfg, owning_retry=False
    )
    assert decision is not None and decision.code == "historical_release_verifier"

    subject._registry_guard = lambda _op, _fn, default: default
    decision = orchestrator._eligibility_ownership_decision(
        issue, cfg, owning_retry=False
    )
    assert decision is not None and decision.code == "registry_unavailable"

    incomplete = replace(issue, title="")
    decision = orchestrator._eligibility_contract_decision(incomplete, cfg)
    assert decision is not None and decision.code == "incomplete_identity"


@pytest.mark.asyncio
async def test_conflict_precheck_and_blocking_tolerate_both_tracker_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    candidate = _issue(
        "APP-1",
        description="## Touched Files\n- `src/shared.py`",
    )
    other = _issue(
        "APP-2",
        description="## Touched Files\n- `src/shared.py`",
    )
    subject._dispatch_state = SimpleNamespace(
        running={
            candidate.id: SimpleNamespace(issue=candidate),
            other.id: SimpleNamespace(issue=other),
        },
        retry={
            candidate.id: SimpleNamespace(identifier=candidate.identifier),
            "missing-body": SimpleNamespace(identifier="APP-3"),
        },
        claimed=set(),
    )
    assert orchestrator._conflict_blocker(candidate) == (
        "APP-2",
        {"src/shared.py"},
    )

    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_append_note",
        lambda *_a: (_ for _ in ()).throw(OSError("note failed")),
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_update_state",
        lambda *_a: (_ for _ in ()).throw(OSError("state failed")),
    )
    await orchestrator._block_ticket_for_conflict(
        _cfg(), candidate, "APP-2", {"src/shared.py"}
    )
    assert candidate.id in subject._dispatch_state.claimed


@pytest.mark.asyncio
async def test_improvement_agent_requires_config_and_write_contract_discards_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from symphony.continuous_improvement import AgentTask

    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._workflow_state = SimpleNamespace(current=lambda: None)
    task = AgentTask(
        mode="cleanup",
        prompt="inspect",
        cwd=tmp_path,
        output_path=tmp_path / ".symphony" / "proposal.md",
    )
    with pytest.raises(SymphonyError, match="no workflow configuration"):
        await orchestrator._run_improvement_agent(task)

    task.output_path.parent.mkdir()
    task.output_path.write_text("proposal", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("locked")),
    )
    with pytest.raises(SymphonyError, match="outside its contract"):
        orchestrator._enforce_improvement_write_contract(
            task,
            {"src/safe.py": " M"},
            {"src/safe.py": " M", "src/unauthorized.py": "??"},
        )
