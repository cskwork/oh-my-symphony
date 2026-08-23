"""Coverage contracts for parsing, schema, and durable registry contracts."""
# ruff: noqa: F405

from tests.coverage_cases._orchestrator_support import *  # noqa: F403


def test_diagnostic_payload_normalizes_failure_values_and_redacts_secrets() -> None:
    payload = json.loads(
        diagnostics.event_payload_json(
            "run_completed",
            {
                "status": "failed",
                "failure_message": "password='correct-horse-battery-staple'",
                "input_tokens": "not-a-number",
                "cache_input_tokens": -3,
                "output_tokens": diagnostics.MAX_DIAGNOSTIC_COUNTER + 100,
                "total_tokens": None,
                "commit_sha": "not a sha",
                "untrusted_extra": "must be dropped",
            },
        )
    )

    assert payload == {
        "cache_input_tokens": 0,
        "commit_sha": None,
        "failure_message": f"password={diagnostics.REDACTED}",
        "input_tokens": 0,
        "output_tokens": diagnostics.MAX_DIAGNOSTIC_COUNTER,
        "status": "failed",
    }


def test_diagnostic_payload_bounds_stderr_and_normalizes_boolean_fields() -> None:
    lines = [f"line-{index}" for index in range(9)] + ["token=abcdefghijk"]
    failed = diagnostics.normalize_event_payload(
        "turn_failed", {"turn": 2, "reason": "boom", "stderr_lines": "\n".join(lines)}
    )
    transitioned = diagnostics.normalize_event_payload(
        "phase_transition",
        {"from_state": "Verify", "to_state": "Build", "is_rewind": 1},
    )

    assert len(failed["stderr_lines"]) == 8
    assert failed["stderr_lines"][-1] == f"token={diagnostics.REDACTED}"
    assert transitioned["is_rewind"] is True
    assert diagnostics.normalize_event_payload("session_started", None) == {}
    with pytest.raises(ValueError, match="unsupported diagnostic event type"):
        diagnostics.normalize_event_payload("raw_backend_frame", {})


def test_diagnostic_payload_shrinks_nested_strings_deterministically() -> None:
    payload = {"outer": [{"message": "x" * 10_000}], "other": "short"}

    bounded = diagnostics._bound_payload(payload)

    encoded = json.dumps(bounded, sort_keys=True, separators=(",", ":")).encode()
    assert len(encoded) <= diagnostics.MAX_EVENT_PAYLOAD_BYTES
    assert bounded["outer"][0]["message"].endswith("…[truncated]")


def test_diagnostic_payload_returns_marker_when_content_cannot_be_shrunk() -> None:
    assert diagnostics._normalize_value("reason", None) is None
    assert diagnostics._bound_payload({"numbers": list(range(2_000))}) == {
        "truncated": True
    }
    assert diagnostics._bound_payload({"values": ["x"] * 2_000}) == {"truncated": True}


def test_scheduler_accepts_default_policy_and_rejects_non_text_policy() -> None:
    assert scheduler.normalize_scheduling_policy(None) == "fifo"
    assert scheduler.normalize_scheduling_policy(" DAG ") == "dag"
    with pytest.raises(ValueError, match="unsupported scheduling policy"):
        scheduler.normalize_scheduling_policy(3)
    assert scheduler.analyze_dependencies([]) == scheduler.DependencyAnalysis({}, {})


def test_scheduler_serializes_request_groups_without_colliding_with_ticket_ids() -> (
    None
):
    grouped = _issue("TASK-2", request="REQUEST-1")
    first = _issue("TASK-1", request="REQUEST-1")
    standalone = _issue("REQUEST-1")

    result = scheduler.group_issues_by_request([standalone, grouped, first])

    assert result == (
        scheduler.RequestGroup(
            key=scheduler.RequestGroupKey("ticket", "REQUEST-1"),
            request=None,
            issue_identifiers=("REQUEST-1",),
        ),
        scheduler.RequestGroup(
            key=scheduler.RequestGroupKey("request", "REQUEST-1"),
            request="REQUEST-1",
            issue_identifiers=("TASK-1", "TASK-2"),
        ),
    )


def test_scheduler_resolves_tracker_blocker_aliases_and_ignores_unknown_edges() -> None:
    blocker_by_id = _issue("BLOCK-A", issue_id="opaque-a")
    blocker_by_identifier = _issue("BLOCK-B", issue_id="opaque-b")
    dependent = _issue(
        "DEPENDENT",
        blocked_by=(
            BlockerRef(id=None, identifier="opaque-a", state="Todo"),
            BlockerRef(id="BLOCK-B", identifier=None, state="Todo"),
            BlockerRef(id="opaque-b", identifier=None, state="Todo"),
            BlockerRef(id="missing", identifier="also-missing", state="Todo"),
            BlockerRef(id=None, identifier=None, state="Todo"),
        ),
    )

    waves = scheduler.dependency_waves(
        [dependent, blocker_by_identifier, blocker_by_id]
    )

    assert waves == {"opaque-a": 0, "opaque-b": 0, "id-DEPENDENT": 1}
    assert scheduler.dependency_cycle_nodes(
        {"A", "B", "C"},
        {("A", "A"), ("A", "B"), ("B", "A"), ("outside", "A")},
    ) == {"A", "B"}


def test_resolve_symphony_cli_uses_launcher_sibling_path_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = tmp_path / "symphony.exe"
    launcher.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    assert helpers.resolve_symphony_cli() == str(launcher.resolve())

    launcher.unlink()
    interpreter = tmp_path / "python.exe"
    sibling = tmp_path / "symphony.exe"
    sibling.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(sys, "executable", str(interpreter))
    monkeypatch.setattr(sys, "platform", "win32")
    assert helpers.resolve_symphony_cli() == str(sibling)

    sibling.unlink()
    discovered = tmp_path / "bin" / "symphony"
    discovered.parent.mkdir()
    discovered.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr(helpers.shutil, "which", lambda _name: str(discovered))
    assert helpers.resolve_symphony_cli() == str(discovered.resolve())

    monkeypatch.setattr(helpers.shutil, "which", lambda _name: None)
    assert helpers.resolve_symphony_cli() == f"{interpreter} -m symphony.cli.main"


def test_board_hook_environment_only_mounts_in_repo_board(tmp_path: Path) -> None:
    workflow = tmp_path / "repo" / "WORKFLOW.md"
    inside = workflow.parent / "boards" / "team"
    inside.mkdir(parents=True)
    cfg = _cfg()
    cfg.workflow_path = workflow
    cfg.tracker.board_root = inside
    cfg.agent.feature_base_branch = "dev"
    cfg.agent.auto_merge_target_branch = "main"

    assert helpers.board_root_name_for_hooks(cfg) == "boards/team"
    assert helpers._branch_hook_env(cfg) == {
        "SYMPHONY_FEATURE_BASE_BRANCH": "dev",
        "SYMPHONY_MERGE_TARGET_BRANCH": "main",
        "SYMPHONY_BOARD_ROOT": str(inside.resolve()),
        "SYMPHONY_BOARD_ROOT_NAME": "boards/team",
    }

    cfg.tracker.board_root = tmp_path / "external"
    assert helpers.board_root_name_for_hooks(cfg) is None
    cfg.tracker.board_root = None
    assert helpers.board_root_name_for_hooks(cfg) is None


@pytest.mark.asyncio
async def test_branch_merge_probe_fails_closed_at_each_git_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0),
        ]
    )
    monkeypatch.setattr(helpers.subprocess, "run", lambda *_a, **_k: next(responses))

    assert not await helpers._branch_already_merged_into_target(
        tmp_path, branch="missing", target_branch="main"
    )
    assert not await helpers._branch_already_merged_into_target(
        tmp_path, branch="feature", target_branch="missing"
    )
    assert await helpers._branch_already_merged_into_target(
        tmp_path, branch="feature", target_branch="main"
    )

    monkeypatch.setattr(
        helpers.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(OSError())
    )
    assert not await helpers._branch_already_merged_into_target(
        tmp_path, branch="feature", target_branch=""
    )


def test_ticket_agent_choice_is_normalized_validated_and_applied() -> None:
    cfg = _AgentChoiceConfig(_AgentChoice("codex"))
    assert helpers._requested_agent_kind(_issue(agent_kind="  ")) is None
    assert (
        helpers._config_for_issue_agent(cast(Any, cfg), _issue(agent_kind="CODEX"))
        == cfg
    )
    assert (
        helpers._config_for_issue_agent(cast(Any, cfg), _issue()).agent.kind == "claude"
    )
    with pytest.raises(ConfigValidationError, match="ticket agent.kind"):
        helpers._config_for_issue_agent(
            cast(Any, cfg), _issue(agent_kind="unknown-agent")
        )


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"auto": False}, False),
        ({"kind": "linear"}, False),
        ({"state": "Verify"}, False),
        ({"active": ("Todo", "Verify")}, False),
        ({"blocked": True}, False),
        ({"labels": ("Bug",)}, False),
        ({"description": ""}, False),
        ({"description": "Blocked by: APP-2\nAcceptance: works"}, False),
        ({"description": "Needs triage\nAcceptance: works"}, False),
        ({"description": "Acceptance criteria: output is visible"}, True),
    ],
)
def test_auto_triage_requires_an_actionable_unblocked_file_ticket(
    change: dict[str, Any], expected: bool
) -> None:
    cfg = _cfg()
    cfg.agent.auto_triage_actionable_todo = change.get("auto", True)
    cfg.tracker.kind = change.get("kind", "file")
    cfg.tracker.active_states = change.get("active", ("Todo", "In Progress"))
    issue = _issue(
        state=change.get("state", "Todo"),
        labels=change.get("labels", ()),
        description=change.get("description", "Acceptance criteria: works"),
        blocked_by=(BlockerRef("APP-2", "APP-2", "Todo"),)
        if change.get("blocked")
        else (),
    )
    assert helpers._is_auto_triage_todo_candidate(issue, cfg) is expected


def test_time_and_terminal_helpers_preserve_configured_lane_names() -> None:
    assert helpers._to_iso(None) is None
    assert helpers._to_iso(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05Z"
    assert (
        helpers._to_iso(
            datetime(2026, 1, 2, 12, 4, 5, tzinfo=timezone(timedelta(hours=9)))
        )
        == "2026-01-02T03:04:05Z"
    )

    cfg = _cfg(terminal=("Operator Review", "Rejected"))
    assert helpers._human_review_target_state(cfg) == "Operator Review"
    assert helpers._rewind_budget_target_state(cfg) == "Operator Review"
    assert helpers._max_turns_exhausted_target_state(cfg) == ""
    cfg.agent.budget_exhausted_state = "Needs Decision"
    assert helpers._max_turns_exhausted_target_state(cfg) == "Needs Decision"
    cfg.agent.budget_exhausted_state = ""
    cfg.tracker.terminal_states = ("Done", "BLOCKED")
    assert helpers._max_turns_exhausted_target_state(cfg) == "BLOCKED"


def test_notification_transition_emits_domain_event_and_swallows_adapter_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notification_cfg = SimpleNamespace(has_any=lambda: True)
    cfg = _cfg()
    cfg.notifications = notification_cfg
    captured: list[Any] = []
    monkeypatch.setattr(
        helpers, "dispatch_notification", lambda _cfg, event: captured.append(event)
    )
    issue = _issue(state="Verify")

    helpers._notify_state_transition(cfg, issue, "Done")

    assert captured[0].identifier == "APP-1"
    assert captured[0].prev_state == "Verify"
    assert captured[0].next_state == "Done"
    monkeypatch.setattr(
        helpers,
        "dispatch_notification",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    helpers._notify_state_transition(cfg, issue, "Done")


@pytest.mark.asyncio
async def test_task_debug_reports_live_and_cancelled_tasks() -> None:
    gate = asyncio.Event()

    async def wait_for_gate() -> None:
        await gate.wait()

    task = asyncio.create_task(wait_for_gate(), name="coverage-waiter")
    await asyncio.sleep(0)
    live = helpers._task_debug(task)
    assert live is not None and live["name"] == "coverage-waiter"
    assert live["done"] is False and live["stack"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancelled = helpers._task_debug(task)
    assert cancelled is not None and cancelled["cancelled"] is True


def test_contract_result_omits_empty_notes_and_renders_warning_bullets() -> None:
    result = contracts.ContractResult(passed=True)
    assert result.note == ""
    assert result.warning_note == ""
    warned = replace(result, warnings=["scorecard row failed"])
    assert warned.warning_note == (
        "## Contract Warning\n- [contract-warn] scorecard row failed"
    )


def test_verify_contract_requires_an_outcome_section() -> None:
    result = contracts.evaluate_contract(
        producing_state="Verify",
        ticket_body="""
## Security Audit
| check | verdict | evidence |
| --- | --- | --- |
| auth | n/a | n/a |
## QA Evidence
evidence
## AC Scorecard
| signal | result |
| --- | --- |
| smoke | pass |
## Merge Status
not merged
""",
        identifier="APP-1",
    )
    assert result.passed is False
    assert "## Review" in result.missing


def test_done_contract_reports_each_empty_required_artifact_directory(
    tmp_path: Path,
) -> None:
    result = contracts.evaluate_contract(
        producing_state="Done",
        ticket_body="""
## As-Is -> To-Be Report
before to after
## Documentation
documented
""",
        identifier="APP-1",
        docs_root=tmp_path,
    )
    assert any("qa" in message for message in result.missing)
    assert any("work" in message for message in result.missing)


def test_contract_artifact_checks_reject_hidden_symlink_and_iteration_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    files = tmp_path / "files"
    files.mkdir()
    empty_directory = tmp_path / "empty"
    empty_directory.mkdir()
    (files / ".temporary").write_text("partial", encoding="utf-8")
    assert not contracts._has_collected_artifact(files)
    assert not contracts._directory_has_files(tmp_path / "missing")
    assert not contracts._directory_has_files(empty_directory)

    original_iterdir = Path.iterdir

    def fail_only_target(path: Path):
        if path == files:
            raise OSError("unreadable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_only_target)
    assert not contracts._has_collected_artifact(files)


def test_contract_markdown_parsers_fail_closed_on_absent_and_malformed_tables() -> None:
    assert contracts._section_body_text("", "## Review") == ""
    assert contracts._section_body_text("## Plan\ntext", "## Review") == ""
    assert contracts._markdown_table_header("## Review\nprose", "## Review") == ()
    assert contracts._parse_markdown_table_rows("## Plan\ntext", "## Review") == []
    assert not contracts._security_has_fail_verdict(
        "## Security Audit\n| header |\n| --- |\n| only-one-cell |"
    )
    assert contracts._scorecard_all_pass(
        "## AC Scorecard\n| signal | result |\n| --- | --- |\n| short |"
    ) == (True, [])


def test_contract_evidence_parsers_handle_empty_rows_na_and_markdown_links() -> None:
    assert (
        contracts._row_evidence_failures(
            "## QA Evidence",
            contracts.MarkdownTableRow(row=1, cells=()),
            Path("docs"),
            "APP-1",
            "expected",
        )
        == []
    )
    assert not contracts._security_row_skips_evidence(("only",))
    assert (
        contracts._extract_cited_path("[proof](qa/result.log:12:3)") == "qa/result.log"
    )
    assert contracts._extract_cited_path("none") is None
    assert contracts._extract_cited_path("   ") is None
    assert contracts._extract_cited_path("none:12") is None
    assert contracts._extract_cited_path("placeholder") is None


def test_orchestrator_markdown_parsing_ignores_empty_sections_and_malformed_rows() -> (
    None
):
    assert parsing._section_body("", parsing._TOUCHED_FILES_HEADING_RE) is None
    assert parsing._parse_touched_files(
        "## Touched Files\nnot a bullet\n- `src/valid.py`"
    ) == {"src/valid.py"}
    rows = parsing._parse_findings_rows(
        "## Review Findings\n-    \n- HIGH: `src/valid.py:12` — fix it"
    )
    assert rows == [
        {
            "severity": "HIGH",
            "file": "src/valid.py",
            "line": 12,
            "fix": "` — fix it",
        }
    ]


def test_add_column_tolerates_only_the_sqlite_duplicate_column_race() -> None:
    migrations._add_column_if_missing(
        cast(Any, _AlterConnection("duplicate column name: value")),
        "runs",
        "value",
        "TEXT",
    )
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        migrations._add_column_if_missing(
            cast(Any, _AlterConnection("database is locked")),
            "runs",
            "value",
            "TEXT",
        )


def test_migration_four_adds_finalizer_binding_to_legacy_gate() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE release_gates (finalizer_identifier TEXT, "
        "verifier_identifier TEXT)"
    )
    migrations._migrate_004_release_finalizer_run_binding(conn)
    assert "finalizer_run_id" in migrations._table_columns(conn, "release_gates")
    conn.close()


def test_continuation_migration_bootstraps_an_empty_runs_schema() -> None:
    conn = sqlite3.connect(":memory:")
    migrations._migrate_008_durable_continuation(conn)
    columns = migrations._table_columns(conn, "runs")
    assert {"resume_session_id", "checkpoint_state", "continued_from_run_id"} <= columns
    conn.close()


def test_failed_migration_rolls_back_without_recording_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = sqlite3.connect(tmp_path / "state.db", isolation_level=None)

    def fail(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(
        migrations, "MIGRATIONS", (migrations.Migration(1, "failure", fail),)
    )
    with pytest.raises(RuntimeError, match="migration failed"):
        migrations.apply_migrations(conn, tmp_path / "state.db")
    assert migrations.current_schema_version(conn) == 0
    conn.close()


def test_existing_schema_has_no_pending_migrations(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path, isolation_level=None)
    assert migrations.apply_migrations(conn, path) == list(
        range(1, migrations.LATEST_SCHEMA_VERSION + 1)
    )
    assert migrations.apply_migrations(conn, path) == []
    conn.close()


def test_checkpoint_rejects_non_datetime_and_invalid_private_fields() -> None:
    with pytest.raises(ValueError, match="checkpointed_at must be a datetime"):
        ContinuationCheckpoint("session", "Verify", 1, "today")  # type: ignore[arg-type]
    for values, message in (
        (("", "Verify", 1), "non-empty string"),
        (("session\n", "Verify", 1), "control characters"),
        (("session", "", 1), "checkpoint state"),
        (("session", "x" * 300, 1), "checkpoint bound"),
        (("session", "Verify", True), "must be an integer"),
        (("session", "Verify", 0), "supported range"),
    ):
        with pytest.raises(ValueError, match=message):
            run_registry._validate_checkpoint_fields(
                resume_session_id=values[0], state=values[1], turn=values[2]
            )


@pytest.mark.parametrize(
    ("handle", "last_error", "exit_ok", "exit_code", "expected"),
    [
        (0, 5, True, 259, True),
        (0, 87, True, 259, False),
        (42, 0, False, 259, True),
        (42, 0, True, 259, True),
        (42, 0, True, 0, False),
    ],
)
def test_win32_liveness_probe_honors_access_and_exit_state(
    monkeypatch: pytest.MonkeyPatch,
    handle: int,
    last_error: int,
    exit_ok: bool,
    exit_code: int,
    expected: bool,
) -> None:
    kernel = _FakeKernel(handle=handle, exit_ok=exit_ok, exit_code=exit_code)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error, raising=False)
    assert run_registry._pid_alive_win32(1234) is expected
    assert kernel.closed == ([42] if handle else [])


def test_registry_schema_flags_authority_and_missing_run_behaviors(
    tmp_path: Path,
) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    assert registry.schema_is_current()
    assert not registry.has_release_authority()
    assert not registry.invalidate_release_gate("missing")
    assert not registry.bind_release_verifier_run(
        gate=_gate("approved"), verifier_run_id="x"
    )
    with pytest.raises(KeyError, match="missing"):
        registry.get_run("missing")
    with pytest.raises(KeyError, match="missing"):
        registry.run_detail("missing")
    assert not registry._run_is_live_locked("missing", NOW)
    registry.reserve_release_cycle_item(
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="cycle",
        item_role="repair",
        item_key="group",
        identifier="QUALITY-1",
        now=NOW,
    )
    assert registry.has_release_authority()
    registry.close()


def test_registry_acquire_rolls_back_when_owning_transaction_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue(state="Verify")
    monkeypatch.setattr(
        registry,
        "_append_attempt_event_best_effort_locked",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("event failure")),
    )
    with pytest.raises(RuntimeError, match="event failure"):
        registry.acquire_run(
            issue,
            workspace_path=tmp_path / "workspace",
            attempt=None,
            attempt_kind="initial",
            agent_kind="codex",
            now=NOW,
        )
    assert not registry.has_active_lease(issue.id, now=NOW)
    registry.close()


def test_registry_continuation_discovery_rejects_corrupt_checkpoint(
    tmp_path: Path,
) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue(state="Verify")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "workspace",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    assert run_id is not None
    assert registry.checkpoint_completed_turn(
        issue_id=issue.id,
        run_id=run_id,
        resume_session_id="session-private",
        state="Verify",
        turn=1,
        now=NOW,
    )
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="shutdown_interrupted",
        now=NOW,
    )

    registry._connect().execute(
        "UPDATE runs SET checkpointed_at = 'zzzz' WHERE run_id = ?", (run_id,)
    )
    assert (
        registry.latest_continuation_source(
            issue_id=issue.id, agent_kind="codex", state="Verify"
        )
        is None
    )
    registry.close()


def test_registry_heartbeat_reuses_the_recorded_process_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    issue = _issue(state="Verify")
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "workspace",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    assert run_id is not None
    monkeypatch.setattr(run_registry, "process_identity", lambda _pid: "birth-1")
    assert registry.heartbeat(issue_id=issue.id, run_id=run_id, backend_agent_pid=42)
    monkeypatch.setattr(
        run_registry,
        "process_identity",
        lambda _pid: (_ for _ in ()).throw(AssertionError("must reuse identity")),
    )
    assert registry.heartbeat(issue_id=issue.id, run_id=run_id, backend_agent_pid=42)
    assert registry.get_run(run_id).backend_process_identity == "birth-1"
    registry.close()


def test_registry_transactional_queries_roll_back_and_remain_usable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    monkeypatch.setattr(
        registry,
        "_expire_stale_locked",
        lambda _now: (_ for _ in ()).throw(RuntimeError("expire failed")),
    )
    with pytest.raises(RuntimeError, match="expire failed"):
        registry.has_active_lease("id-APP-1", now=NOW)
    with pytest.raises(RuntimeError, match="expire failed"):
        registry.expire_stale(now=NOW)
    monkeypatch.undo()
    assert registry.list_issue_flags() == []
    registry.close()


def test_registry_rejects_invalid_cycle_item_identity(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    with pytest.raises(ValueError, match="role must be repair or verifier"):
        registry.reserve_release_cycle_item(
            finalizer_identifier="APP-FINAL",
            cycle_fingerprint="cycle",
            item_role="unknown",
            item_key="group",
            identifier="QUALITY-1",
        )
    with pytest.raises(ValueError, match="fields must be non-empty"):
        registry.reserve_release_cycle_item(
            finalizer_identifier="APP-FINAL",
            cycle_fingerprint="",
            item_role="repair",
            item_key="group",
            identifier="QUALITY-1",
        )
    registry.close()


def test_registry_numeric_sha_and_search_normalizers_cover_boundary_inputs() -> None:
    assert run_registry._optional_nonnegative(None) is None
    assert run_registry._nonnegative(None, object()) == 0
    assert run_registry._nonnegative(-2) == 0
    assert run_registry._valid_sha(None) is None
    assert run_registry._valid_sha(" ABCD ") == "abcd"
    assert run_registry._valid_sha("xyz") is None
    assert run_registry._like_pattern(r" A%_B\C ") == r"%a\%\_b\\c%"
    naive = datetime(2026, 8, 24, 12)
    assert run_registry._utc(naive).tzinfo == timezone.utc
    assert run_registry._parse(None) is None


def test_continuation_acquisition_rejects_corrupt_checkpoint_and_unique_race(
    tmp_path: Path,
) -> None:
    registry, issue, run_id = _continuation_registry_source(tmp_path)
    registry._connect().execute(
        "UPDATE runs SET checkpoint_turn = 'bad' WHERE run_id = ?", (run_id,)
    )
    assert (
        registry.acquire_continuation_run(
            issue,
            continued_from_run_id=run_id,
            workspace_path=tmp_path / "next",
            attempt=1,
            attempt_kind="retry",
            agent_kind="codex",
            now=NOW + timedelta(seconds=2),
        )
        is None
    )
    registry.close()

    for message, should_raise in (
        ("UNIQUE constraint failed: runs.continued_from_run_id", False),
        ("NOT NULL constraint failed: runs.issue_id", True),
    ):
        registry, issue, run_id = _continuation_registry_source(
            tmp_path / ("unique" if not should_raise else "other")
        )
        real = registry._connect()
        proxy = _SqlFaultConnection(
            real,
            "INSERT INTO runs",
            error=sqlite3.IntegrityError(message),
        )
        cast(Any, registry)._conn = proxy
        if should_raise:
            with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
                registry.acquire_continuation_run(
                    issue,
                    continued_from_run_id=run_id,
                    workspace_path=tmp_path / "next",
                    attempt=1,
                    attempt_kind="retry",
                    agent_kind="codex",
                    now=NOW + timedelta(seconds=2),
                )
        else:
            assert (
                registry.acquire_continuation_run(
                    issue,
                    continued_from_run_id=run_id,
                    workspace_path=tmp_path / "next",
                    attempt=1,
                    attempt_kind="retry",
                    agent_kind="codex",
                    now=NOW + timedelta(seconds=2),
                )
                is None
            )
        assert proxy.triggered and not proxy.in_transaction
        registry.close()


def test_completion_and_best_effort_events_survive_savepoint_unavailability(
    tmp_path: Path,
) -> None:
    registry, issue, run_id = _active_registry_run(tmp_path, identifier="SAVEPOINT-1")
    real = registry._connect()
    proxy = _SqlFaultConnection(real, "SAVEPOINT run_diagnostic")
    cast(Any, registry)._conn = proxy
    assert registry.complete_run(
        issue_id=issue.id,
        run_id=run_id,
        status="normal",
        now=NOW + timedelta(seconds=1),
    )
    assert registry.get_run(run_id).status == "normal"
    registry.close()


def test_complete_run_rolls_back_authoritative_transaction_failure(
    tmp_path: Path,
) -> None:
    registry, issue, run_id = _active_registry_run(
        tmp_path, identifier="COMPLETE-FAIL-1"
    )
    real = registry._connect()
    proxy = _SqlFaultConnection(
        real, "SELECT * FROM runs WHERE issue_id = ? AND run_id = ?"
    )
    cast(Any, registry)._conn = proxy
    with pytest.raises(sqlite3.OperationalError, match="injected database fault"):
        registry.complete_run(
            issue_id=issue.id,
            run_id=run_id,
            status="normal",
            now=NOW,
        )
    assert proxy.triggered and not proxy.in_transaction
    registry.close()

    registry, _issue_row, run_id = _active_registry_run(
        tmp_path / "event", identifier="EVENT-1"
    )
    real = registry._connect()
    proxy = _SqlFaultConnection(real, "SAVEPOINT run_diagnostic")
    cast(Any, registry)._conn = proxy
    registry._append_attempt_event_best_effort_locked(
        run_id=run_id,
        event_type="run_started",
        now=NOW,
    )
    assert proxy.triggered
    registry.close()


def test_registry_transaction_failures_roll_back_release_and_reclaim_operations(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, Any]] = []

    def add_case(name: str, invoke: Any) -> None:
        cases.append((name, invoke))

    pending = replace(_gate(), generation="generation")
    approved = replace(
        pending,
        status="approved",
        approved_fingerprint="fingerprint",
        target_branch="main",
        approved_target_sha="b" * 40,
        verifier_run_id="verifier-run",
        finalizer_run_id="finalizer-run",
    )
    add_case(
        "bind-verifier",
        lambda registry: registry.bind_release_verifier_run(
            gate=pending, verifier_run_id="verifier-run", now=NOW
        ),
    )
    add_case(
        "bind-finalizer",
        lambda registry: registry.bind_release_finalizer_run(
            gate=approved,
            finalizer_issue_id="id-APP-FINAL",
            finalizer_run_id="finalizer-run",
            now=NOW,
        ),
    )
    add_case(
        "finalizer-authority",
        lambda registry: registry.release_finalizer_run_is_authorized(
            gate=approved, finalizer_issue_id="id-APP-FINAL", now=NOW
        ),
    )
    add_case(
        "verifier-authority",
        lambda registry: registry.release_verifier_run_is_authorized(
            gate=approved, verifier_issue_id="id-VERIFY-1", now=NOW
        ),
    )
    add_case(
        "mark-finalizer",
        lambda registry: registry.mark_release_finalizer_completed(
            gate=approved,
            finalizer_issue_id="id-APP-FINAL",
            completion_token="token",
            now=NOW,
        ),
    )
    add_case(
        "approve",
        lambda registry: registry.approve_release_gate(
            finalizer_identifier="APP-FINAL",
            verifier_issue_id="id-VERIFY-1",
            verifier_identifier="VERIFY-1",
            expected_contract_sha256="a" * 64,
            expected_cycle_fingerprint="cycle-1",
            expected_generation="generation",
            approved_fingerprint="fingerprint",
            target_branch="main",
            target_sha="b" * 40,
            verifier_run_id="verifier-run",
            now=NOW,
        ),
    )
    add_case(
        "reclaim",
        lambda registry: registry.reclaim_dead_owner_leases(now=NOW),
    )
    add_case(
        "finalize-reclaim",
        lambda registry: registry.finalize_reclaimed_lease("run-1", now=NOW),
    )

    for index, (name, invoke) in enumerate(cases):
        registry = RunRegistry(tmp_path / f"fault-{index}.db")
        real = registry._connect()
        if name == "finalize-reclaim":
            needle = "UPDATE runs SET status = 'orphaned'"
        elif name in {"finalizer-authority", "verifier-authority"}:
            needle = "SELECT 1 FROM release_gates AS gate"
        else:
            needle = "SELECT * FROM runs"
        proxy = _SqlFaultConnection(real, needle)
        cast(Any, registry)._conn = proxy
        with pytest.raises(sqlite3.OperationalError, match="injected database fault"):
            invoke(registry)
        assert proxy.triggered and not proxy.in_transaction
        registry.close()


def test_release_binding_false_paths_and_trigger_deleted_readbacks(
    tmp_path: Path,
) -> None:
    registry = RunRegistry(tmp_path / "binding.db")
    pending = replace(_gate(), generation="generation")
    assert not registry.bind_release_verifier_run(
        gate=pending, verifier_run_id="missing", now=NOW
    )
    assert not registry.bind_release_finalizer_run(
        gate=pending,
        finalizer_issue_id="id-APP-FINAL",
        finalizer_run_id="missing",
        now=NOW,
    )
    approved = replace(
        pending,
        status="approved",
        approved_fingerprint="fingerprint",
        target_branch="main",
        approved_target_sha="b" * 40,
        verifier_run_id="verifier-run",
        finalizer_run_id="finalizer-run",
    )
    assert not registry.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id="id-APP-FINAL",
        finalizer_run_id="missing",
        now=NOW,
    )
    assert not registry.mark_release_finalizer_completed(
        gate=approved,
        finalizer_issue_id="id-APP-FINAL",
        completion_token="token",
        now=NOW,
    )
    with pytest.raises(ValueError, match="pending status"):
        registry.replace_pending_release_gate(approved, now=NOW)
    registry.close()


def test_release_binding_rejects_missing_gate_peer_lease_and_foreign_bound_run(
    tmp_path: Path,
) -> None:
    registry, verifier, verifier_run = _active_registry_run(
        tmp_path, identifier="VERIFY-1"
    )
    pending = replace(_gate(), generation="generation")
    assert verifier.id == pending.verifier_issue_id
    assert not registry.bind_release_verifier_run(
        gate=pending, verifier_run_id=verifier_run, now=NOW
    )
    registry.close()

    registry = RunRegistry(tmp_path / "foreign-verifier.db")
    verifier = _issue("VERIFY-1", state="Verify")
    verifier_run = registry.acquire_run(
        verifier,
        workspace_path=tmp_path / "verifier-current",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    foreign = _issue("OTHER-VERIFY", state="Verify")
    foreign_run = registry.acquire_run(
        foreign,
        workspace_path=tmp_path / "verifier-foreign",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    assert verifier_run and foreign_run
    pending = replace(pending, verifier_run_id=foreign_run)
    registry._connect().execute(
        """
        INSERT INTO release_gates (
          finalizer_identifier, verifier_issue_id, verifier_identifier,
          expected_contract_sha256, cycle_fingerprint, approved_fingerprint,
          status, target_branch, approved_target_sha, verifier_run_id,
          finalizer_run_id, generation, updated_at
        ) VALUES (?, ?, ?, ?, ?, NULL, 'pending', NULL, NULL, ?, NULL, ?, ?)
        """,
        (
            pending.finalizer_identifier,
            pending.verifier_issue_id,
            pending.verifier_identifier,
            pending.expected_contract_sha256,
            pending.cycle_fingerprint,
            pending.verifier_run_id,
            pending.generation,
            run_registry._iso(NOW),
        ),
    )
    assert not registry.bind_release_verifier_run(
        gate=pending, verifier_run_id=verifier_run, now=NOW
    )
    registry.close()

    registry = RunRegistry(tmp_path / "peer.db")
    finalizer = _issue("APP-FINAL", state="Document")
    current_finalizer = registry.acquire_run(
        finalizer,
        workspace_path=tmp_path / "finalizer",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    verifier = _issue("VERIFY-1", state="Verify")
    peer_verifier = registry.acquire_run(
        verifier,
        workspace_path=tmp_path / "verifier",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    assert current_finalizer and peer_verifier
    approved = _approved_core_gate(finalizer_run_id=current_finalizer)
    assert not registry.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=current_finalizer,
        now=NOW,
    )
    assert registry.complete_run(
        issue_id=verifier.id,
        run_id=peer_verifier,
        status="normal",
        now=NOW,
    )
    assert not registry.bind_release_finalizer_run(
        gate=approved,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=current_finalizer,
        now=NOW,
    )
    registry.close()

    registry = RunRegistry(tmp_path / "foreign-run.db")
    finalizer = _issue("APP-FINAL", state="Document")
    current_finalizer = registry.acquire_run(
        finalizer,
        workspace_path=tmp_path / "finalizer-current",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    foreign = _issue("OTHER-1", state="Document")
    foreign_run = registry.acquire_run(
        foreign,
        workspace_path=tmp_path / "foreign",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    assert current_finalizer and foreign_run
    gate = _approved_core_gate(
        finalizer_run_id=foreign_run,
        verifier_run_id="verifier-run",
    )
    conn = registry._connect()
    conn.execute(
        """
        INSERT INTO release_gates (
          finalizer_identifier, verifier_issue_id, verifier_identifier,
          expected_contract_sha256, cycle_fingerprint, approved_fingerprint,
          status, target_branch, approved_target_sha, verifier_run_id,
          finalizer_run_id, generation, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?, ?, ?, ?)
        """,
        (
            gate.finalizer_identifier,
            gate.verifier_issue_id,
            gate.verifier_identifier,
            gate.expected_contract_sha256,
            gate.cycle_fingerprint,
            gate.approved_fingerprint,
            gate.target_branch,
            gate.approved_target_sha,
            gate.verifier_run_id,
            gate.finalizer_run_id,
            gate.generation,
            run_registry._iso(NOW),
        ),
    )
    assert not registry.bind_release_finalizer_run(
        gate=gate,
        finalizer_issue_id=finalizer.id,
        finalizer_run_id=current_finalizer,
        now=NOW,
    )
    registry.close()


def test_pending_gate_replacement_rejects_active_finalizer_without_exact_authority(
    tmp_path: Path,
) -> None:
    registry = RunRegistry(tmp_path / "replacement.db")
    pending = replace(_gate(), generation="ignored")
    registry.replace_pending_release_gate(pending, now=NOW)
    finalizer = _issue("APP-FINAL", state="Document")
    run_id = registry.acquire_run(
        finalizer,
        workspace_path=tmp_path / "finalizer",
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        now=NOW,
    )
    assert run_id is not None
    with pytest.raises(RuntimeError, match="must finish"):
        registry.replace_pending_release_gate(pending, now=NOW)
    with pytest.raises(RuntimeError, match="only the exact active"):
        registry.replace_pending_release_gate(
            pending,
            now=NOW,
            invalidating_finalizer_run_id="wrong-run",
        )
    registry.close()

    registry = RunRegistry(tmp_path / "deleted-gate.db")
    registry._connect().execute(
        """
        CREATE TRIGGER delete_inserted_gate AFTER INSERT ON release_gates
        BEGIN
          DELETE FROM release_gates WHERE finalizer_identifier = NEW.finalizer_identifier;
        END
        """
    )
    with pytest.raises(RuntimeError, match="disappeared after replacement"):
        registry.replace_pending_release_gate(pending, now=NOW)
    registry.close()

    registry = RunRegistry(tmp_path / "deleted-item.db")
    registry._connect().execute(
        """
        CREATE TRIGGER delete_inserted_cycle_item AFTER INSERT ON release_cycle_items
        BEGIN
          DELETE FROM release_cycle_items WHERE identifier = NEW.identifier;
        END
        """
    )
    with pytest.raises(RuntimeError, match="disappeared after recording"):
        registry.reserve_release_cycle_item(
            finalizer_identifier="APP-FINAL",
            cycle_fingerprint="cycle",
            item_role="repair",
            item_key="ui",
            identifier="QUALITY-1",
            now=NOW,
        )
    registry.close()


def test_run_summary_exposes_checkpoint_without_private_session_id() -> None:
    record = _record(
        checkpoint_state="Verify",
        checkpoint_turn=3,
        checkpointed_at=NOW,
        continued_from_run_id="run-0",
        branch_name=None,
    )
    summary = run_registry._run_summary(record)
    payload = core_module._run_record_payload(record)
    assert summary["checkpoint"] == {
        "state": "Verify",
        "turn": 3,
        "checkpointed_at": NOW.isoformat(),
    }
    assert summary["branch_name"] == "symphony/APP-1"
    assert payload["continued_from_run_id"] == "run-0"
    assert "resume_session_id" not in json.dumps(payload)
