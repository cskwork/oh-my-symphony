"""Coverage contracts for dispatch and worker lifecycle."""
# ruff: noqa: F405

from tests.coverage_cases._orchestrator_support import *  # noqa: F403


@pytest.mark.parametrize(
    ("authority_kind", "cleanup_fails"),
    [
        ("finalizer_missing", False),
        ("pending_missing", False),
        ("approved_missing", False),
        ("gate_changed", False),
        ("finalizer_missing", True),
    ],
)
def test_dispatch_releases_lease_when_release_authority_changes(
    monkeypatch: pytest.MonkeyPatch,
    authority_kind: str,
    cleanup_fails: bool,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("VERIFY-1", state="Verify")
    cfg = _cfg()
    cfg.agent.kind_for_state = lambda _state, _requested: "codex"
    gate = _gate() if authority_kind == "pending_missing" else _approved_core_gate()
    authority = core_module._ReleaseDispatchAuthority(
        issue=issue,
        gate=gate,
        cycle_verifier=authority_kind != "finalizer_missing",
        finalizer=authority_kind == "finalizer_missing",
    )
    cancelled: list[str] = []
    subject._workspace_manager = None
    subject._dispatch_state = SimpleNamespace(
        cancel_pending_retry=lambda issue_id: cancelled.append(issue_id)
    )
    monkeypatch.setattr(
        orchestrator, "_prepare_release_dispatch", lambda *_a: authority
    )
    monkeypatch.setattr(
        orchestrator,
        "_try_acquire_run_lease",
        lambda **_kwargs: core_module._RunLeaseAcquisition("run-1"),
    )
    operations: list[str] = []

    def registry_call(_cfg: Any, operation: str, _callback: Any) -> Any:
        operations.append(operation)
        if operation == "release_failed_acquired_run":
            if cleanup_fails:
                raise sqlite3.OperationalError("cleanup locked")
            return None
        if operation.startswith("bind_release_"):
            return authority_kind not in {"finalizer_missing", "pending_missing"}
        if operation.startswith("read_bound_"):
            return None
        if operation == "reread_release_gate_after_lease":
            if authority_kind == "approved_missing":
                return None
            if authority_kind == "gate_changed":
                return replace(gate, verifier_identifier="VERIFY-OTHER")
        raise AssertionError(operation)

    monkeypatch.setattr(orchestrator, "_release_registry_call", registry_call)

    assert not orchestrator._dispatch(issue, cfg, attempt=None)
    assert cancelled == [issue.id]
    assert operations[-1] == "release_failed_acquired_run"


def test_dispatch_rolls_back_run_when_task_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1")
    cfg = _cfg()
    cfg.agent.kind_for_state = lambda _state, _requested: "codex"
    subject._workspace_manager = None
    events: list[tuple[str, str]] = []
    subject._dispatch_state = SimpleNamespace(
        cancel_pending_retry=lambda _issue_id: None,
        begin_run=lambda issue_id, _entry: events.append(("begin", issue_id)),
        abort_run=lambda issue_id: events.append(("abort", issue_id)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_prepare_release_dispatch",
        lambda *_a: core_module._ReleaseDispatchAuthority(issue=issue),
    )
    monkeypatch.setattr(
        orchestrator,
        "_try_acquire_run_lease",
        lambda **_kwargs: core_module._RunLeaseAcquisition("run-1"),
    )

    class Executor:
        async def execute(self, _context: Any) -> None:
            return None

    monkeypatch.setattr(orchestrator, "_executor_for", lambda _cfg: Executor())
    finished: list[tuple[str, str]] = []
    monkeypatch.setattr(
        orchestrator,
        "_finish_run_lease",
        lambda issue_id, _entry, status: finished.append((issue_id, status)),
    )

    def fail_create_task(coro: Any, **_kwargs: Any) -> None:
        coro.close()
        raise RuntimeError("task scheduler unavailable")

    monkeypatch.setattr(core_module.asyncio, "create_task", fail_create_task)

    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        orchestrator._dispatch(issue, cfg, attempt=None)
    assert events == [("begin", issue.id), ("abort", issue.id)]
    assert finished == [(issue.id, "dispatch_failed")]


@pytest.mark.asyncio
async def test_stop_cancels_retry_timer_and_closes_tracker_pool() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    calls: list[str] = []

    class Lock:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: Any) -> None:
            return None

    subject._tick_task = None
    subject._pause_events = {}
    subject._dispatch_state = SimpleNamespace(
        running={},
        retry={
            "id": SimpleNamespace(
                timer_handle=SimpleNamespace(cancel=lambda: calls.append("timer"))
            )
        },
        turn_budget_exhausted=set(),
    )
    subject._drain_worker_tasks = lambda: asyncio.sleep(0)
    subject._drain_background_tasks = lambda: asyncio.sleep(0)
    subject._app_release_transition_locks = {}
    subject._paused_issue_ids = set()
    subject._pause_reasons = {}
    subject._lease_blocked = {}
    subject._blocked_rca_source_ids = set()
    subject._history_recovery_attempted = set()
    subject._issue_debug = {}
    subject._run_registry = None
    subject._tracker_client_lock = Lock()
    subject._tracker_client_closing = False
    subject._tracker_client = SimpleNamespace(close=lambda: calls.append("tracker"))
    subject._tracker_client_cfg = object()
    subject._tracker_client_closed = False

    await orchestrator.stop()

    assert calls == ["timer", "tracker"]
    assert subject._tracker_client is None
    assert subject._tracker_client_closed


@pytest.mark.asyncio
async def test_tick_loop_accepts_poll_timeout_as_normal_wakeup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._stopping = False
    subject._consecutive_tick_failures = 1
    subject._last_tick_completed_at = None
    subject._workflow_state = SimpleNamespace(
        current=lambda: SimpleNamespace(poll_interval_ms=1)
    )
    subject._tick_event = asyncio.Event()
    subject._refresh_pending = True

    async def one_tick() -> None:
        subject._stopping = True

    async def timeout(awaitable: Any, **_kwargs: Any) -> None:
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(orchestrator, "_on_tick", one_tick)
    monkeypatch.setattr(core_module.asyncio, "wait_for", timeout)

    await orchestrator._tick_loop()

    assert subject._consecutive_tick_failures == 0
    assert subject._last_tick_completed_at is not None
    assert not subject._refresh_pending


@pytest.mark.asyncio
async def test_tick_reports_when_no_workflow_version_is_available() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    observed: list[str] = []
    subject._schedule_snapshot = {}
    subject._workflow_state = SimpleNamespace(
        reload=lambda: (None, SymphonyError("invalid workflow")), current=lambda: None
    )
    subject._notify_observers = lambda: asyncio.sleep(
        0, result=observed.append("notified")
    )

    await orchestrator._on_tick()

    assert observed == ["notified"]


@pytest.mark.asyncio
async def test_tick_applies_reload_fallback_root_change_and_expired_lease_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg()
    cfg.workspace_root = Path("/new-root")
    cfg.workspace_reuse_policy = "reuse"
    cfg.hooks = SimpleNamespace()
    cfg.agent.feature_base_branch = "dev"
    created_roots: list[Path] = []
    monkeypatch.setattr(
        core_module,
        "WorkspaceManager",
        lambda root, *_a, **_k: (
            created_roots.append(root) or SimpleNamespace(root=root)
        ),
    )
    monkeypatch.setattr(
        core_module,
        "validate_for_dispatch",
        lambda _cfg: (_ for _ in ()).throw(SymphonyError("not dispatchable")),
    )

    fallback = core_module.Orchestrator.__new__(core_module.Orchestrator)
    fallback_observed = _configure_tick_until_validation(
        fallback, cfg, reload_error=SymphonyError("reload failed")
    )
    await fallback._on_tick()
    assert fallback_observed == ["notified"]

    changed = core_module.Orchestrator.__new__(core_module.Orchestrator)
    changed_observed = _configure_tick_until_validation(
        changed, cfg, registry=SimpleNamespace(expire_stale=lambda: 1)
    )
    cast(Any, changed)._workspace_manager = SimpleNamespace(root=Path("/old-root"))
    await changed._on_tick()
    assert changed_observed == ["notified"]
    assert created_roots == [cfg.workspace_root]


@pytest.mark.asyncio
async def test_tick_marks_candidate_fetch_failure_snapshot_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg()
    observed = _configure_tick_until_validation(orchestrator, cfg)
    subject = cast(Any, orchestrator)
    subject._schedule_snapshot = {"generated_at": "earlier", "stale": False}
    subject._consecutive_candidate_fetch_failures = 0
    subject._auto_normalize_legacy_human_review_done = lambda _cfg: asyncio.sleep(0)
    subject._auto_reopen_sources_from_resolved_rcas = lambda _cfg: asyncio.sleep(0)
    subject._fetch_candidates = lambda _cfg: asyncio.sleep(
        0, result=(_ for _ in ()).throw(OSError("tracker offline"))
    )
    monkeypatch.setattr(core_module, "validate_for_dispatch", lambda _cfg: None)

    await orchestrator._on_tick()

    assert observed == ["notified"]
    assert subject._consecutive_candidate_fetch_failures == 1
    assert subject._schedule_snapshot["reason"] == "candidate_fetch_failed"


@pytest.mark.asyncio
async def test_tick_fifo_oversized_graph_still_runs_recovery_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg()
    cfg.agent.scheduling_policy = "fifo"
    cfg.agent.max_concurrent_agents = 2
    observed = _configure_tick_until_validation(orchestrator, cfg)
    subject = cast(Any, orchestrator)
    candidates = [_issue("APP-LARGE")] * (core_module.MAX_DEPENDENCY_NODES + 1)
    subject._blocked_rca_source_ids = set()
    subject._history_recovery_attempted = set()
    subject._consecutive_candidate_fetch_failures = 0
    subject._auto_normalize_legacy_human_review_done = lambda _cfg: asyncio.sleep(0)
    subject._auto_reopen_sources_from_resolved_rcas = lambda _cfg: asyncio.sleep(0)
    subject._fetch_candidates = lambda _cfg: asyncio.sleep(0, result=candidates)
    subject._available_slots = lambda _cfg: 1
    subject._dispatch_fifo_without_schedule_projection = lambda *_a: asyncio.sleep(0)
    subject._auto_recover_blocked_sources = lambda _cfg: asyncio.sleep(
        0, result=observed.append("recovered")
    )
    subject._last_archive_sweep_monotonic = 10**20
    subject._last_artifact_sweep_monotonic = 10**20
    subject._maybe_schedule_continuous_improvement = lambda _cfg: None
    monkeypatch.setattr(core_module, "validate_for_dispatch", lambda _cfg: None)

    await orchestrator._on_tick()

    assert observed == ["recovered", "notified"]
    assert subject._schedule_snapshot["reason"] == "schedule_graph_too_large"


@pytest.mark.parametrize(
    ("exception_mode", "stopping", "expected_reason"),
    [
        ("cancelled", True, "shutdown_interrupted"),
        ("error", False, "worker_task_finished_without_cleanup"),
        ("clean", False, "worker_task_finished_without_cleanup"),
    ],
)
def test_worker_done_callback_supervises_unclaimed_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    exception_mode: str,
    stopping: bool,
    expected_reason: str,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1")

    class Task:
        def cancelled(self) -> bool:
            return False

        def exception(self) -> BaseException | None:
            if exception_mode == "cancelled":
                raise asyncio.CancelledError
            if exception_mode == "error":
                return RuntimeError("worker crashed")
            return None

        def get_name(self) -> str:
            return "worker"

        def get_coro(self) -> Any:
            return self.exception

        def done(self) -> bool:
            return True

    task = Task()
    entry = SimpleNamespace(
        issue=issue,
        exit_started_at=None,
        started_at=NOW,
        turn_count=1,
        workspace_path=Path("workspace/APP-1"),
        cancelled_at=None,
    )
    subject._stopping = stopping
    subject._dispatch_state = SimpleNamespace(
        entry_owned_by=lambda issue_id, owned_task: (
            entry if issue_id == issue.id and owned_task is task else None
        )
    )
    spawned: list[str] = []

    class Closeable:
        def close(self) -> None:
            return None

    def spawn(coro: Any, *, name: str) -> None:
        coro.close()
        spawned.append(name)

    monkeypatch.setattr(orchestrator, "_spawn_supervised", spawn)

    def worker_exit(_issue_id: str, reason: str, *_a: Any, **_kwargs: Any) -> Closeable:
        spawned.append(reason)
        return Closeable()

    monkeypatch.setattr(orchestrator, "_on_worker_exit", worker_exit)

    orchestrator._on_worker_task_done(issue.id, cast(Any, task))

    assert spawned == [expected_reason, f"symphony-worker-exit-{issue.id}"]


def test_backend_preview_extracts_supported_assistant_content_shapes() -> None:
    preview = core_module.Orchestrator._preview_from_payload

    assert preview({"message": {"role": "assistant", "content": " text "}}) == "text"
    assert (
        preview({"message": {"role": "assistant", "content": [{}, " last "]}}) == "last"
    )
    assert (
        preview({"message": {"role": "assistant", "content": {"text": " object "}}})
        == "object"
    )
    assert (
        preview(
            {"message": {"role": "assistant", "content": None, "text": " fallback "}}
        )
        == "fallback"
    )
    assert preview({"message": {"role": "assistant", "content": None}}) == ""


@pytest.mark.asyncio
async def test_worker_preflight_refusal_is_reported_through_exit_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("VERIFY-1", state="Verify")
    entry = _worker_preflight_entry(issue)
    subject._dispatch_state = SimpleNamespace(
        running={issue.id: entry}, entry_foreign_to=lambda *_a: False
    )
    subject._stopping = False
    monkeypatch.setattr(
        orchestrator,
        "_prepare_release_dispatch",
        lambda *_a: (_ for _ in ()).throw(SymphonyError("gate unavailable")),
    )
    exits: list[tuple[str, str | None]] = []

    async def exit_worker(
        _issue_id: str, outcome: str, error: str | None, **_kwargs: Any
    ) -> None:
        exits.append((outcome, error))

    monkeypatch.setattr(orchestrator, "_on_worker_exit", exit_worker)

    cfg = _cfg()
    cfg.agent.kind = "codex"
    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert exits and exits[0][0] == "error"
    assert exits[0][1] is not None and "gate unavailable" in exits[0][1]


@pytest.mark.asyncio
async def test_worker_finalizer_preflight_guards_workspace_before_hook_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-FINAL", state="Document")
    entry = _worker_preflight_entry(issue)
    subject._dispatch_state = SimpleNamespace(
        running={issue.id: entry}, entry_foreign_to=lambda *_a: False
    )
    subject._stopping = False

    class WorkspaceManager:
        async def create_or_reuse(self, _identifier: str) -> Any:
            return SimpleNamespace(path=Path("workspace/APP-FINAL"))

        async def before_run(self, _path: Path) -> None:
            raise OSError("hook failed")

    subject._workspace_manager = WorkspaceManager()
    monkeypatch.setattr(core_module, "_config_for_issue_agent", lambda cfg, _issue: cfg)
    monkeypatch.setattr(
        orchestrator,
        "_prepare_release_dispatch",
        lambda *_a: core_module._ReleaseDispatchAuthority(
            issue=issue, app_release=True, finalizer=True
        ),
    )
    monkeypatch.setattr(orchestrator, "_heartbeat_run_lease", lambda *_a: True)
    monkeypatch.setattr(
        orchestrator, "_require_running_release_authority", lambda **_k: issue
    )
    exits: list[tuple[str, str | None]] = []

    async def exit_worker(
        _issue_id: str, outcome: str, error: str | None, **_kwargs: Any
    ) -> None:
        exits.append((outcome, error))

    monkeypatch.setattr(orchestrator, "_on_worker_exit", exit_worker)

    cfg = _cfg()
    cfg.agent.kind = "codex"
    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert entry.release_finalizer_rewind_state == "Document"
    assert exits == [("before_run_error", "hook failed")]


@pytest.mark.asyncio
async def test_stage_watchdog_handoff_records_tracker_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-1", state="Verify")
    entry = SimpleNamespace(issue=issue)
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_update_state",
        lambda *_a: (_ for _ in ()).throw(OSError("tracker offline")),
    )
    monkeypatch.setattr(
        orchestrator,
        "_record_tracker_error",
        lambda issue_id, exc: recorded.append((issue_id, str(exc))),
    )

    assert not await orchestrator._persist_no_stage_change_handoff(
        cfg=_cfg(),
        entry=cast(Any, entry),
        issue_id=issue.id,
        target_state="Human Review",
        turn_count=3,
        state_name="Verify",
    )
    assert recorded == [(issue.id, "tracker offline")]


@pytest.mark.asyncio
async def test_event_ingestion_handles_missing_worker_invalid_time_and_backend_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._dispatch_state = SimpleNamespace(running={})
    await orchestrator._on_codex_event("missing", {"event": "other"})

    issue = _issue("APP-1")
    backend_payloads: list[dict[str, Any]] = []
    entry = SimpleNamespace(
        issue=issue,
        resume_session_id="private-session",
        client=SimpleNamespace(
            is_progress_event=lambda payload: backend_payloads.append(payload) or True
        ),
        last_codex_timestamp=None,
        last_codex_event="",
        last_codex_message="",
    )
    subject._dispatch_state.running[issue.id] = entry
    subject._workflow_state = SimpleNamespace(current=lambda: None)
    subject._issue_debug = {}
    subject._latest_rate_limits = {}
    heartbeats: list[str] = []
    monkeypatch.setattr(orchestrator, "_apply_token_totals", lambda *_a: (0, 0))
    monkeypatch.setattr(orchestrator, "_token_cap_for_entry", lambda *_a: 0)
    monkeypatch.setattr(
        orchestrator,
        "_heartbeat_run_lease",
        lambda issue_id, *_a, **_k: heartbeats.append(issue_id) or True,
    )

    payload = {"type": "tool_result", "text": "progress"}
    await orchestrator._on_codex_event(
        issue.id,
        {
            "event": core_module.EVENT_OTHER_MESSAGE,
            "timestamp": "not-a-time",
            "payload": payload,
            "rate_limits": {"remaining": 4},
        },
    )

    assert backend_payloads == [payload]
    assert heartbeats == [issue.id]
    assert subject._latest_rate_limits == {"remaining": 4}
    assert entry.last_codex_timestamp is not None


@pytest.mark.asyncio
async def test_approval_denial_without_command_updates_issue_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1")
    entry = SimpleNamespace(
        issue=issue,
        resume_session_id=None,
        client=None,
        last_codex_timestamp=None,
        last_codex_event="",
        last_codex_message="",
    )
    subject._dispatch_state = SimpleNamespace(running={issue.id: entry})
    subject._workflow_state = SimpleNamespace(current=lambda: None)
    subject._issue_debug = {}
    monkeypatch.setattr(orchestrator, "_append_run_event", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestrator, "_apply_token_totals", lambda *_a: (0, 0))
    monkeypatch.setattr(orchestrator, "_token_cap_for_entry", lambda *_a: 0)
    monkeypatch.setattr(orchestrator, "_heartbeat_run_lease", lambda *_a, **_k: True)

    await orchestrator._on_codex_event(
        issue.id,
        {
            "event": core_module.EVENT_APPROVAL_DENIED,
            "payload": {"reason": "policy"},
        },
    )

    assert subject._issue_debug[issue.id].last_error == "approval denied: policy"


def test_token_cap_accepts_legacy_learn_learning_aliases() -> None:
    cfg = _cfg()
    cfg.agent.max_total_tokens = 99
    entry = SimpleNamespace(issue=_issue("APP-1", state="Learn"))
    cfg.agent.max_total_tokens_by_state = {"learning": 5}
    assert core_module.Orchestrator._token_cap_for_entry(cfg, cast(Any, entry)) == 5

    entry.issue = replace(entry.issue, state="Learning")
    cfg.agent.max_total_tokens_by_state = {"learn": 6}
    assert core_module.Orchestrator._token_cap_for_entry(cfg, cast(Any, entry)) == 6


@pytest.mark.asyncio
async def test_history_recovery_returns_false_when_tracker_cannot_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cfg = _cfg()
    cfg.agent.auto_merge_push_target = False
    issue = _issue(
        "APP-1",
        state="Blocked",
        description=(
            "## History Failure\n\n"
            "failing command: `git add -f docs/APP-1/learn/details.md`\n"
            "stderr: unable to create temporary file: Operation not permitted; "
            "fatal: updating files failed\n"
        ),
    )

    async def durable_history(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            durable=True,
            status="local_only",
            branch="symphony/APP-1",
            local_sha="a" * 40,
            remote_sha="",
        )

    monkeypatch.setattr(core_module, "verify_branch_history", durable_history)
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_append_note",
        lambda *_a: (_ for _ in ()).throw(OSError("tracker offline")),
    )

    assert not await orchestrator._recover_blocked_history_gate(cfg, issue)


@pytest.mark.asyncio
async def test_invalid_done_fix_reblocks_active_source_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    fix = _issue("FIX-1", state="Done", labels=("blocked-fix",))
    source = _issue("APP-1", state="Todo")
    updated: list[str] = []

    def update(_cfg: Any, issue: Issue, _state: str) -> None:
        updated.append(issue.identifier)
        if issue is source:
            raise OSError("source write failed")

    monkeypatch.setattr(orchestrator, "_tracker_call_update_state", update)
    monkeypatch.setattr(orchestrator, "_tracker_call_append_note", lambda *_a: None)
    recorded: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_record_tracker_error",
        lambda issue_id, _exc: recorded.append(issue_id),
    )
    monkeypatch.setattr(orchestrator, "_clear_tracker_error", lambda _issue_id: None)

    assert await orchestrator._move_blocked_fix_out_of_done(
        _cfg(),
        fix,
        source,
        reason="resolution evidence is missing",
        reason_code="missing_resolution",
    )
    assert updated == [fix.identifier, source.identifier]
    assert recorded == [source.id]


@pytest.mark.asyncio
async def test_refresh_missing_issue_and_release_transition_rewind_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._app_release_transition_locks = {}
    issue = _issue("APP-1", state="Done")
    subject._dispatch_state = SimpleNamespace(running={issue.id: SimpleNamespace()})
    monkeypatch.setattr(orchestrator, "_tracker_call_states_by_ids", lambda *_a: [])

    assert await orchestrator._refresh_issue_state(_cfg(), issue.id) is None
    first_lock = orchestrator._app_release_transition_lock(issue.id)
    assert first_lock is not None
    assert orchestrator._app_release_transition_lock(issue.id) is first_lock

    async def fail_transition(**_kwargs: Any) -> Any:
        raise OSError("registry unavailable")

    rewound = replace(issue, state="Verify")
    monkeypatch.setattr(
        orchestrator, "_enforce_app_release_transition_inner", fail_transition
    )
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **_kwargs: asyncio.sleep(0, result=rewound),
    )
    result = await orchestrator._enforce_app_release_transition_guarded(
        cfg=_cfg(),
        issue=issue,
        workspace_path=Path("workspace/APP-1"),
        producing_state="Verify",
        known_app_release=True,
        running_entry=None,
    )
    assert result == (rewound, True)


@pytest.mark.asyncio
async def test_worker_exit_waits_for_scheduled_workspace_cleanup() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1")
    cleanup_finished = asyncio.Event()
    entry = SimpleNamespace(
        workspace_cleanup_started=True, workspace_cleanup_finished=cleanup_finished
    )
    running = {issue.id: entry}
    subject._dispatch_state = SimpleNamespace(running=running)
    subject._terminal_persist_pending = set()
    finished: list[str] = []

    async def exit_impl(*_args: Any, **_kwargs: Any) -> None:
        running.pop(issue.id)
        asyncio.get_running_loop().call_soon(cleanup_finished.set)

    subject._on_worker_exit_impl = exit_impl
    subject._finish_run_lease = lambda issue_id, *_a: finished.append(issue_id)

    await orchestrator._on_worker_exit(issue.id, "normal", None)

    assert cleanup_finished.is_set()
    assert finished == [issue.id]
    assert issue.id not in subject._terminal_persist_pending


@pytest.mark.asyncio
async def test_worker_exit_impl_ignores_already_absent_entry() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._dispatch_state = SimpleNamespace(running={})
    subject._app_release_transition_locks = {}
    subject._claim_released_at = {}
    subject._pause_events = {}

    await orchestrator._on_worker_exit_impl("missing", "normal", None)

    assert subject._dispatch_state.running == {}


def test_conflict_scan_ignores_retry_for_same_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1")
    subject._dispatch_state = SimpleNamespace(
        running={}, retry={issue.id: SimpleNamespace(identifier=issue.identifier)}
    )
    monkeypatch.setattr(orchestrator, "_touched_files_for", lambda _issue: {"src/a.py"})

    assert orchestrator._conflict_blocker(issue) is None


@pytest.mark.asyncio
async def test_backend_lifecycle_events_persist_diagnostics_and_empty_turn_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1", state="Verify")
    entry = _runtime_entry(
        issue,
        turn_count=3,
        thread_id="thread-1",
        current_turn_message="",
        consecutive_empty_turns=core_module.EMPTY_TURN_LOOP_THRESHOLD - 1,
        state_at_turn_start="verify",
    )
    subject._dispatch_state = SimpleNamespace(running={issue.id: entry})
    cfg = _exit_cfg()
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    subject._totals = SimpleNamespace(
        input_tokens=0, cache_input_tokens=0, output_tokens=0, total_tokens=0
    )
    debug = core_module._IssueDebug(
        recent_events=[{"event": f"prior-{index}"} for index in range(50)]
    )
    subject._issue_debug = {issue.id: debug}
    recorded: list[tuple[str, dict[str, Any] | None]] = []

    def append_event(_entry: Any, event_type: str, payload: Any = None) -> None:
        recorded.append((event_type, payload))

    monkeypatch.setattr(orchestrator, "_append_run_event", append_event)
    monkeypatch.setattr(
        orchestrator, "_record_token_attention_for_turn", lambda *_a: None
    )
    monkeypatch.setattr(orchestrator, "_record_stats_turn", lambda *_a: None)
    monkeypatch.setattr(orchestrator, "_heartbeat_run_lease", lambda *_a, **_k: True)

    await orchestrator._on_codex_event(
        issue.id,
        {
            "event": core_module.EVENT_TURN_COMPLETED,
            "payload": {"turn_id": "turn-3"},
        },
    )
    await orchestrator._on_codex_event(
        issue.id,
        {
            "event": core_module.EVENT_TURN_FAILED,
            "payload": {"reason": "timeout", "stderr_tail": ["last line"]},
        },
    )
    await orchestrator._on_codex_event(
        issue.id,
        {
            "event": core_module.EVENT_COMPACTION,
            "payload": {"phase": "before_turn", "reason": "large", "tokens_before": 9},
        },
    )
    await orchestrator._on_codex_event(
        issue.id,
        {
            "event": core_module.EVENT_AGENT_RETRY,
            "payload": {"phase": "stream", "attempt": 2, "final_error": "reset"},
        },
    )

    assert entry.hit_empty_response_loop
    assert entry.session_id == "thread-1-turn-3"
    assert [event_type for event_type, _ in recorded] == [
        "turn_completed",
        "turn_failed",
        "compaction",
        "retry",
    ]
    assert len(debug.recent_events) == 50
    assert debug.recent_events[-1]["event"] == core_module.EVENT_AGENT_RETRY


@pytest.mark.asyncio
async def test_worker_exit_release_budget_pause_is_durable_and_reported() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Verify")
    entry = _runtime_entry(issue, release_gate_exhausted=True, turn_count=2)
    cfg = _exit_cfg()
    subject, _notifications = _configure_exit_subject(orchestrator, entry, cfg)
    stats: list[tuple[str, str]] = []
    subject._stats = SimpleNamespace(
        record_run_end=lambda **fields: stats.append(
            (fields["issue"], fields["outcome"])
        )
    )
    persisted: list[tuple[str, dict[str, Any]]] = []
    subject._set_issue_flags = lambda issue_id, **fields: persisted.append(
        (issue_id, fields)
    )

    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert stats == [(issue.identifier, "normal")]
    assert issue.id in subject._paused_issue_ids
    assert "rewind budget" in subject._pause_reasons[issue.id]
    assert persisted[0][1]["paused"] is True


@pytest.mark.asyncio
async def test_worker_exit_completed_release_handoff_clears_retry_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    entry = _runtime_entry(issue, release_verifier_handoff_complete=True)
    cfg = _exit_cfg()
    cfg.agent.auto_commit_on_done = True
    subject, notifications = _configure_exit_subject(orchestrator, entry, cfg)
    removed: list[Path] = []
    subject._workspace_manager = SimpleNamespace(
        remove=lambda path: asyncio.sleep(0, result=removed.append(path))
    )
    committed: list[str] = []
    monkeypatch.setattr(
        core_module,
        "commit_workspace_on_done",
        lambda *_a, **kwargs: asyncio.sleep(
            0, result=committed.append(kwargs["identifier"])
        ),
    )
    monkeypatch.setattr(orchestrator, "_artifact_commit_excludes", lambda _cfg: ())

    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert committed == [issue.identifier]
    assert removed == [entry.workspace_path]
    assert notifications == [f"cancel:{issue.id}", "notified"]
    assert issue.id not in subject._dispatch_state.persisted_retry_attempts


@pytest.mark.asyncio
async def test_worker_exit_token_budget_without_workflow_stays_operator_blocked() -> (
    None
):
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-1", state="Verify")
    entry = _runtime_entry(issue, hit_token_budget=True)
    subject, _notifications = _configure_exit_subject(orchestrator, entry, None)

    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert issue.id in subject._dispatch_state.turn_budget_exhausted
    assert issue.id in subject._dispatch_state.claimed
    assert "workflow config unavailable" in subject._issue_debug[issue.id].last_error


@pytest.mark.asyncio
async def test_worker_exit_finalizer_refuses_cleanup_when_gate_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-FINAL", state="Done")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_app_release_finalizer=True,
        release_gate_finalizer="APP-FINAL",
        release_finalizer_rewind_state="Document",
    )
    cfg = _exit_cfg()
    _subject, _notifications = _configure_exit_subject(orchestrator, entry, cfg)
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: None)
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **_k: asyncio.sleep(
            0, result=(_ for _ in ()).throw(OSError("board write failed"))
        ),
    )

    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert "authority disappeared" in _subject._issue_debug[issue.id].last_error


@pytest.mark.asyncio
async def test_worker_exit_rechecks_finalizer_before_terminal_workspace_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-FINAL", state="Done")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_app_release_finalizer=True,
        release_gate_finalizer="APP-FINAL",
        release_finalizer_rewind_state="Document",
    )
    cfg = _exit_cfg()
    subject, _notifications = _configure_exit_subject(orchestrator, entry, cfg)
    subject._workspace_manager = SimpleNamespace(remove=lambda _path: asyncio.sleep(0))
    gate = _approved_core_gate()
    responses = iter([gate, None])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0)
    )
    monkeypatch.setattr(
        orchestrator,
        "_guard_release_finalizer_with_version",
        lambda **_k: (issue, "completion-token"),
    )
    monkeypatch.setattr(
        orchestrator, "_mark_release_finalizer_completed", lambda **_k: gate
    )
    rewinds: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **kwargs: asyncio.sleep(
            0,
            result=rewinds.append(kwargs["producing_state"])
            or replace(issue, state="Document"),
        ),
    )

    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert rewinds == ["Document"]
    assert entry.issue.state == "Document"


@pytest.mark.asyncio
async def test_worker_exit_release_evidence_commits_then_removes_without_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    entry = _runtime_entry(issue, known_app_release=True)
    cfg = _exit_cfg()
    cfg.agent.auto_commit_on_done = True
    subject, notifications = _configure_exit_subject(orchestrator, entry, cfg)
    actions: list[str] = []
    subject._workspace_manager = SimpleNamespace(
        remove=lambda _path: asyncio.sleep(0, result=actions.append("removed"))
    )
    monkeypatch.setattr(
        core_module,
        "commit_workspace_on_done",
        lambda *_a, **_k: asyncio.sleep(0, result=actions.append("committed")),
    )
    monkeypatch.setattr(orchestrator, "_artifact_commit_excludes", lambda _cfg: ())

    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert actions == ["committed", "removed"]
    assert notifications == ["notified"]


@pytest.mark.asyncio
async def test_worker_exit_unpublished_history_preserves_workspace_for_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-1", state="Done")
    entry = _runtime_entry(issue)
    cfg = _exit_cfg()
    cfg.agent.auto_commit_on_done = True
    cfg.agent.auto_merge_push_target = True
    subject, notifications = _configure_exit_subject(orchestrator, entry, cfg)
    removed: list[Path] = []
    subject._workspace_manager = SimpleNamespace(
        remove=lambda path: asyncio.sleep(0, result=removed.append(path))
    )
    history = SimpleNamespace(status=core_module.HISTORY_PUSH_FAILED)
    monkeypatch.setattr(
        core_module,
        "finalize_delivery_history",
        lambda *_a, **_k: asyncio.sleep(0, result=history),
    )
    flagged: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_flag_unpublished_history",
        lambda _cfg, flagged_issue, _history: asyncio.sleep(
            0, result=flagged.append(flagged_issue.identifier)
        ),
    )

    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert flagged == [issue.identifier]
    assert removed == []
    assert notifications == ["notified"]


def test_force_eject_releases_owned_transition_and_reparks_worker() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1")
    entry = _runtime_entry(issue, retry_attempt=1)
    transition_lock = asyncio.Lock()
    pause_event = asyncio.Event()
    scheduled: list[dict[str, Any]] = []
    subject._dispatch_state = SimpleNamespace(
        running={issue.id: entry}, claimed={issue.id}
    )
    subject._app_release_transition_locks = {issue.id: (entry, transition_lock)}
    subject._pause_events = {issue.id: pause_event}
    subject._issue_debug = {}
    subject._finish_run_lease = lambda *_a, **_k: None
    subject._schedule_retry = lambda _issue_id, **fields: scheduled.append(fields)
    cfg = _exit_cfg()
    cfg.agent.max_retry_backoff_ms = 60_000

    orchestrator._force_eject_zombie(issue.id, entry, cfg, skip_kill=True)

    assert issue.id not in subject._app_release_transition_locks
    assert issue.id not in subject._dispatch_state.running
    assert issue.id not in subject._dispatch_state.claimed
    assert pause_event.is_set()
    assert scheduled[0]["attempt"] == 2
    assert subject._issue_debug[issue.id].last_error == "force_ejected_zombie"


@pytest.mark.asyncio
async def test_retry_escalation_releases_claim_when_workflow_disappears() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue_id = "id-APP-1"
    subject._stopping = False
    subject._workflow_state = SimpleNamespace(current=lambda: None)
    subject._dispatch_state = SimpleNamespace(
        claimed={issue_id}, retry={issue_id: object()}
    )
    subject._pending_escalations = {issue_id: 1}

    await orchestrator._escalate_max_retries(
        issue_id=issue_id,
        identifier="APP-1",
        attempt=4,
        error="backend unavailable",
    )

    assert issue_id not in subject._dispatch_state.claimed
    assert issue_id not in subject._dispatch_state.retry
    assert issue_id not in subject._pending_escalations


@pytest.mark.asyncio
async def test_retry_escalation_does_nothing_after_shutdown_starts() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._stopping = True
    subject._dispatch_state = SimpleNamespace(claimed={"id-APP-1"})

    await orchestrator._escalate_max_retries(
        issue_id="id-APP-1",
        identifier="APP-1",
        attempt=4,
        error="backend unavailable",
    )

    assert subject._dispatch_state.claimed == {"id-APP-1"}


@pytest.mark.asyncio
async def test_retry_escalation_uses_blocked_fallback_without_terminal_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue_id = "id-APP-1"
    cfg = _exit_cfg()
    cfg.tracker.terminal_states = ()
    cfg.agent.max_retries = 3
    subject._stopping = False
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    subject._dispatch_state = SimpleNamespace(
        claimed={issue_id}, retry={issue_id: object()}
    )
    subject._pending_escalations = {issue_id: 1}
    updated: list[tuple[str, str]] = []
    monkeypatch.setattr(orchestrator, "_tracker_call_append_note", lambda *_a: None)
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_update_state",
        lambda _cfg, issue, state: updated.append((issue.identifier, state)),
    )
    monkeypatch.setattr(orchestrator, "_clear_tracker_error", lambda _issue_id: None)

    await orchestrator._escalate_max_retries(
        issue_id=issue_id,
        identifier="APP-1",
        attempt=4,
        error="backend unavailable",
    )

    assert updated == [("APP-1", "Blocked")]
    assert issue_id not in subject._dispatch_state.claimed
    assert issue_id not in subject._pending_escalations


@pytest.mark.asyncio
async def test_retry_timer_missing_or_unconfigured_releases_ownership() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue_id = "id-APP-1"
    subject._dispatch_state = SimpleNamespace(
        running={},
        claimed={issue_id},
        retry={},
        persisted_retry_attempts={issue_id: 2},
    )
    subject._terminal_persist_pending = set()
    subject._pending_escalations = {}
    subject._paused_issue_ids = {issue_id}
    subject._pause_reasons = {issue_id: "operator hold"}
    cleared: list[dict[str, Any]] = []
    subject._clear_issue_flags = lambda _issue_id, **fields: cleared.append(fields)

    await orchestrator._on_retry_timer(issue_id)

    retry = SimpleNamespace(
        issue_id=issue_id,
        identifier="APP-1",
        attempt=2,
        error="failure",
        kind="retry",
        holds_slot=True,
    )
    subject._dispatch_state.retry[issue_id] = retry
    subject._paused_issue_ids.clear()
    subject._workflow_state = SimpleNamespace(current=lambda: None)
    await orchestrator._on_retry_timer(issue_id)

    assert issue_id not in subject._dispatch_state.claimed
    assert issue_id not in subject._dispatch_state.persisted_retry_attempts
    assert issue_id not in subject._pause_reasons
    assert cleared == [{"retry_attempt": True, "paused": True}]


@pytest.mark.asyncio
async def test_retry_poll_releases_ticket_that_left_active_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    retry = SimpleNamespace(
        issue_id="id-APP-1",
        identifier="APP-1",
        attempt=2,
        error="failure",
        kind="retry",
        holds_slot=True,
    )
    monkeypatch.setattr(
        orchestrator, "_fetch_candidates", lambda _cfg: asyncio.sleep(0, result=[])
    )
    released: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_release_retry_ownership",
        lambda owned, **_kwargs: released.append(owned.identifier),
    )

    await orchestrator._process_retry(cast(Any, retry), _exit_cfg())

    assert released == ["APP-1"]


@pytest.mark.asyncio
async def test_reconcile_late_release_label_rewinds_when_gate_cannot_be_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done", labels=("app-release",))
    entry = _runtime_entry(replace(issue, state="Verify"))
    cfg = _exit_cfg()
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(
        orchestrator,
        "_prepare_release_dispatch",
        lambda *_a: core_module._ReleaseDispatchAuthority(issue=issue),
    )
    notes: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **kwargs: asyncio.sleep(
            0,
            result=notes.append(kwargs["note_body"]) or replace(issue, state="Verify"),
        ),
    )

    await orchestrator._reconcile_one(
        issue,
        entry,
        cfg,
        active={"verify"},
        terminal={"done"},
        now=NOW,
        recent_grace_s=60,
    )

    assert entry.issue.state == "Verify"
    assert "could not be established" in notes[0]


@pytest.mark.asyncio
async def test_reconcile_release_rewind_stops_terminal_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    entry = _runtime_entry(
        replace(issue, state="Verify"),
        known_app_release=True,
        known_release_cycle_verifier=True,
    )
    cfg = _exit_cfg()
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(
        orchestrator,
        "_enforce_app_release_transition",
        lambda **_kwargs: asyncio.sleep(
            0, result=(replace(issue, state="Verify"), True)
        ),
    )

    await orchestrator._reconcile_one(
        issue,
        entry,
        cfg,
        active={"verify"},
        terminal={"done"},
        now=NOW,
        recent_grace_s=60,
    )

    assert entry.issue.state == "Verify"
    assert entry.terminal_seen_at is None


@pytest.mark.asyncio
async def test_reconcile_exiting_finalizer_records_completion_without_cancelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-FINAL", state="Done")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_app_release_finalizer=True,
        release_finalizer_rewind_state="Document",
        exit_started_at=NOW,
    )
    cfg = _exit_cfg()
    gate = _approved_core_gate()
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        orchestrator,
        "_guard_release_finalizer_with_version",
        lambda **_k: (issue, "completion-token"),
    )
    completed: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_mark_release_finalizer_completed",
        lambda **_k: completed.append(issue.identifier) or gate,
    )

    await orchestrator._reconcile_one(
        issue,
        entry,
        cfg,
        active={"document"},
        terminal={"done"},
        now=NOW + timedelta(minutes=5),
        recent_grace_s=60,
    )

    assert completed == [issue.identifier]
    assert entry.terminal_seen_at is not None


@pytest.mark.asyncio
async def test_reconcile_finalizer_rechecks_gate_before_stale_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-FINAL", state="Done")

    class WorkerTask:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    worker_task = WorkerTask()
    entry = _runtime_entry(
        issue,
        worker_task=worker_task,
        known_app_release=True,
        known_app_release_finalizer=True,
        release_gate_finalizer="APP-FINAL",
        release_finalizer_rewind_state="Document",
        terminal_seen_at=NOW,
    )
    cfg = _exit_cfg()
    subject = cast(Any, orchestrator)
    subject._workspace_manager = SimpleNamespace(remove=lambda _path: asyncio.sleep(0))
    subject._issue_debug = {}
    gate = _approved_core_gate()
    responses = iter([gate, None])
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    monkeypatch.setattr(
        orchestrator,
        "_guard_release_finalizer_with_version",
        lambda **_k: (issue, "completion-token"),
    )
    monkeypatch.setattr(
        orchestrator, "_mark_release_finalizer_completed", lambda **_k: gate
    )
    rewinds: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **kwargs: asyncio.sleep(
            0,
            result=rewinds.append(kwargs["producing_state"])
            or replace(issue, state="Document"),
        ),
    )

    await orchestrator._reconcile_one(
        issue,
        entry,
        cfg,
        active={"document"},
        terminal={"done"},
        now=NOW + timedelta(minutes=5),
        recent_grace_s=60,
    )

    assert worker_task.cancelled
    assert rewinds == ["Document"]
    assert entry.workspace_cleanup_finished.is_set()


@pytest.mark.asyncio
async def test_reconcile_tolerates_workers_exiting_during_tracker_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    first = _issue("APP-1")
    second = _issue("APP-2")
    running = {
        first.id: _runtime_entry(first),
        second.id: _runtime_entry(second),
    }
    subject._dispatch_state = SimpleNamespace(running=running)
    monkeypatch.setattr(orchestrator, "_heartbeat_run_lease", lambda *_a: True)
    monkeypatch.setattr(orchestrator, "_stall_timeout_ms_for_entry", lambda *_a: 0)

    def refresh(_cfg: Any, _ids: list[str]) -> list[Issue]:
        running.clear()
        return [first]

    monkeypatch.setattr(orchestrator, "_tracker_call_states_by_ids", refresh)
    recorded: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_record_tracker_error",
        lambda issue_id, _error: recorded.append(issue_id),
    )
    monkeypatch.setattr(orchestrator, "_clear_tracker_error", lambda _issue_id: None)

    await orchestrator._reconcile_running(_exit_cfg())

    assert running == {}
    assert recorded == []


@pytest.mark.asyncio
async def test_reconcile_finalizer_gate_loss_rewinds_terminal_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-FINAL", state="Done")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_app_release_finalizer=True,
        release_finalizer_rewind_state="Document",
    )
    cfg = _exit_cfg()
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: None)
    rewinds: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **kwargs: asyncio.sleep(
            0,
            result=rewinds.append(kwargs["producing_state"])
            or replace(issue, state="Document"),
        ),
    )

    await orchestrator._reconcile_one(
        issue,
        entry,
        cfg,
        active={"document"},
        terminal={"done"},
        now=NOW,
        recent_grace_s=60,
    )

    assert rewinds == ["Document"]
    assert entry.issue.state == "Document"


@pytest.mark.asyncio
async def test_reconcile_finalizer_valid_gate_allows_terminal_workspace_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-FINAL", state="Done")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_app_release_finalizer=True,
        release_gate_finalizer="APP-FINAL",
        release_finalizer_rewind_state="Document",
        terminal_seen_at=NOW,
    )
    cfg = _exit_cfg()
    subject = cast(Any, orchestrator)
    removed: list[Path] = []
    subject._workspace_manager = SimpleNamespace(
        remove=lambda path: asyncio.sleep(0, result=removed.append(path))
    )
    subject._issue_debug = {}
    gate = _approved_core_gate()
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        orchestrator,
        "_guard_release_finalizer_with_version",
        lambda **_k: (issue, "completion-token"),
    )
    monkeypatch.setattr(
        orchestrator, "_mark_release_finalizer_completed", lambda **_k: gate
    )
    monkeypatch.setattr(orchestrator, "_guard_release_finalizer", lambda **_k: issue)

    await orchestrator._reconcile_one(
        issue,
        entry,
        cfg,
        active={"document"},
        terminal={"done"},
        now=NOW + timedelta(minutes=5),
        recent_grace_s=60,
    )

    assert removed == [entry.workspace_path]
    assert entry.workspace_cleanup_finished.is_set()


@pytest.mark.asyncio
async def test_release_transition_missing_gate_rewinds_when_host_identity_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_release_cycle_verifier=True,
        release_authority_resolved=True,
    )
    cfg = _exit_cfg()
    responses = iter([None, None])
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    authority_checks: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_require_release_transition_verifier_authority",
        lambda **_k: authority_checks.append("checked"),
    )
    monkeypatch.setattr(
        core_module,
        "resolve_target_release_identity",
        lambda **_k: SimpleNamespace(
            errors=("target branch is ambiguous",),
            finalizer_ticket="",
            contract_sha256="",
        ),
    )
    rewound = replace(issue, state="Verify")
    notes: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **kwargs: asyncio.sleep(
            0, result=notes.append(kwargs["note_body"]) or rewound
        ),
    )

    result = await orchestrator._enforce_app_release_transition_inner(
        cfg=cfg,
        issue=issue,
        workspace_path=entry.workspace_path,
        producing_state="Verify",
        known_app_release=True,
        running_entry=entry,
    )

    assert result == (rewound, True)
    assert authority_checks == ["checked"]
    assert "target branch is ambiguous" in notes[0]


@pytest.mark.asyncio
async def test_release_transition_mismatched_gate_rebinds_runtime_then_rewinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    entry = _runtime_entry(
        issue, known_app_release=True, release_authority_resolved=False
    )
    cfg = _exit_cfg()
    gate = replace(
        _approved_core_gate(),
        verifier_issue_id="other-id",
        verifier_identifier="VERIFY-OTHER",
        expected_contract_sha256="c" * 64,
        finalizer_identifier="APP-OTHER",
    )
    validation = _release_validation(
        passed=False,
        contract_sha256="c" * 64,
        finalizer_ticket="APP-FINAL",
    )
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )
    notes: list[str] = []
    rewound = replace(issue, state="Verify")
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **kwargs: asyncio.sleep(
            0, result=notes.append(kwargs["note_body"]) or rewound
        ),
    )

    result = await orchestrator._enforce_app_release_transition_inner(
        cfg=cfg,
        issue=issue,
        workspace_path=entry.workspace_path,
        producing_state="Verify",
        known_app_release=True,
        running_entry=entry,
    )

    assert result == (rewound, True)
    assert entry.release_gate_finalizer == "APP-OTHER"
    assert "issue id does not match" in notes[0]
    assert "identifier does not match" in notes[0]
    assert "finalizer binding" in notes[0]


@pytest.mark.asyncio
async def test_release_transition_green_without_live_run_rewinds_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cast(Any, orchestrator)._dispatch_state = SimpleNamespace(running={})
    issue = _issue("VERIFY-1", state="Done")
    cfg = _exit_cfg()
    gate = _gate()
    validation = _release_validation(passed=True)
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )
    rewound = replace(issue, state="Verify")
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **_k: asyncio.sleep(0, result=rewound),
    )

    result = await orchestrator._enforce_app_release_transition_inner(
        cfg=cfg,
        issue=issue,
        workspace_path=Path("workspace/VERIFY-1"),
        producing_state="Verify",
        known_app_release=True,
        running_entry=None,
    )

    assert result == (rewound, True)


@pytest.mark.asyncio
async def test_release_transition_accepts_existing_approval_for_exact_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    entry = _runtime_entry(
        issue,
        run_id="verifier-run",
        known_app_release=True,
        known_release_cycle_verifier=True,
        release_authority_resolved=True,
    )
    cfg = _exit_cfg()
    gate = _approved_core_gate()
    validation = _release_validation(passed=True)
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )
    checks: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_require_release_transition_verifier_authority",
        lambda **_k: checks.append("checked"),
    )

    result = await orchestrator._enforce_app_release_transition_inner(
        cfg=cfg,
        issue=issue,
        workspace_path=entry.workspace_path,
        producing_state="Verify",
        known_app_release=True,
        running_entry=entry,
    )

    assert result == (issue, False)
    assert checks == ["checked", "checked"]


@pytest.mark.parametrize("approval_persists", [False, True])
@pytest.mark.asyncio
async def test_release_transition_approval_requires_durable_exact_binding(
    monkeypatch: pytest.MonkeyPatch,
    approval_persists: bool,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    entry = _runtime_entry(
        issue,
        run_id="verifier-run",
        known_app_release=True,
        known_release_cycle_verifier=True,
        release_authority_resolved=True,
    )
    cfg = _exit_cfg()
    gate = _gate()
    validation = _release_validation(passed=True)
    approved_gate = replace(
        gate,
        status="approved",
        approved_fingerprint=validation.fingerprint,
        target_branch=validation.target_branch,
        approved_target_sha=validation.target_sha,
        verifier_run_id=entry.run_id,
    )

    def registry_call(_cfg: Any, operation: str, _callback: Any) -> Any:
        if operation == "read_verifier_gate_for_transition":
            return gate
        if operation == "approve_release_gate":
            return approval_persists
        if operation == "read_approved_release_gate":
            return approved_gate if approval_persists else None
        raise AssertionError(operation)

    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", registry_call)
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )
    monkeypatch.setattr(
        orchestrator,
        "_require_release_transition_verifier_authority",
        lambda **_k: None,
    )
    rewound = replace(issue, state="Verify")
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **_k: asyncio.sleep(0, result=rewound),
    )

    result = await orchestrator._enforce_app_release_transition_inner(
        cfg=cfg,
        issue=issue,
        workspace_path=entry.workspace_path,
        producing_state="Verify",
        known_app_release=True,
        running_entry=entry,
    )

    expected = (issue, False) if approval_persists else (rewound, True)
    assert result == expected


@pytest.mark.asyncio
async def test_release_transition_evidence_failure_rewinds_without_repair_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cast(Any, orchestrator)._dispatch_state = SimpleNamespace(running={})
    issue = _issue("VERIFY-1", state="Done")
    cfg = _exit_cfg()
    gate = _gate()
    validation = _release_validation(
        passed=False, evidence_errors=("target SHA is missing",)
    )
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )
    notes: list[str] = []
    rewound = replace(issue, state="Verify")
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **kwargs: asyncio.sleep(
            0, result=notes.append(kwargs["note_body"]) or rewound
        ),
    )

    result = await orchestrator._enforce_app_release_transition_inner(
        cfg=cfg,
        issue=issue,
        workspace_path=Path("workspace/VERIFY-1"),
        producing_state="Verify",
        known_app_release=True,
        running_entry=None,
    )

    assert result == (rewound, True)
    assert "release did not pass" in notes[0]
    assert validation.target_sha in notes[0]


@pytest.mark.asyncio
async def test_release_transition_persists_fresh_pending_gate_before_failed_relink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    cast(Any, orchestrator)._dispatch_state = SimpleNamespace(running={})
    issue = _issue("VERIFY-1", state="Done")
    cfg = _exit_cfg()
    cfg.workflow_path = tmp_path / "WORKFLOW.md"
    cfg.agent.kind = "codex"
    gate = replace(_gate(), expected_contract_sha256="c" * 64)
    validation = _release_validation(
        passed=False, with_failure=True, contract_sha256="c" * 64
    )
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )

    def reconcile_cycle(*_args: Any, **kwargs: Any) -> Any:
        kwargs["before_finalizer_relink"](issue)
        return SimpleNamespace(passed=False, error="repair relink failed")

    monkeypatch.setattr(
        orchestrator, "_tracker_call_reconcile_release_cycle", reconcile_cycle
    )
    rewound = replace(issue, state="Verify")
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **_k: asyncio.sleep(0, result=rewound),
    )

    result = await orchestrator._enforce_app_release_transition_inner(
        cfg=cfg,
        issue=issue,
        workspace_path=tmp_path,
        producing_state="Verify",
        known_app_release=True,
        running_entry=None,
    )

    registry = RunRegistry(run_registry.registry_path_for_workflow(cfg.workflow_path))
    try:
        persisted = registry.get_release_gate(validation.finalizer_ticket)
    finally:
        registry.close()
    assert result == (rewound, True)
    assert persisted is not None
    assert persisted.expected_contract_sha256 == validation.contract_sha256


@pytest.mark.parametrize("identity_present", [False, True])
@pytest.mark.asyncio
async def test_release_transition_requires_retired_identity_after_repair_handoff(
    monkeypatch: pytest.MonkeyPatch,
    identity_present: bool,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    entry = _runtime_entry(
        issue, known_app_release=True, release_authority_resolved=False
    )
    cfg = _exit_cfg()
    cfg.agent.kind = "codex"
    gate = replace(_gate(), expected_contract_sha256="c" * 64)
    validation = _release_validation(
        passed=False, with_failure=True, contract_sha256="c" * 64
    )
    identity = ReleaseEvidenceIdentity(
        issue_id=issue.id,
        identifier=issue.identifier,
        finalizer_identifier=gate.finalizer_identifier,
        role="verifier",
        cycle_generation=gate.generation,
        retired=True,
        recorded_at=NOW,
        updated_at=NOW,
    )

    def registry_call(_cfg: Any, operation: str, _callback: Any) -> Any:
        if operation == "read_verifier_gate_for_transition":
            return gate
        if operation == "read_completed_verifier_handoff":
            return identity if identity_present else None
        raise AssertionError(operation)

    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", registry_call)
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_reconcile_release_cycle",
        lambda *_a, **_k: SimpleNamespace(
            passed=True,
            error="",
            repair_identifiers=("REPAIR-1",),
            verifier_identifier="VERIFY-2",
        ),
    )

    if identity_present:
        result = await orchestrator._enforce_app_release_transition_inner(
            cfg=cfg,
            issue=issue,
            workspace_path=Path("workspace/VERIFY-1"),
            producing_state="Verify",
            known_app_release=True,
            running_entry=entry,
        )
        assert result == (issue, False)
        assert entry.release_verifier_handoff_complete
    else:
        with pytest.raises(SymphonyError, match="handoff identity could not be proven"):
            await orchestrator._enforce_app_release_transition_inner(
                cfg=cfg,
                issue=issue,
                workspace_path=Path("workspace/VERIFY-1"),
                producing_state="Verify",
                known_app_release=True,
                running_entry=entry,
            )


def test_tracker_diagnostics_bound_long_errors_and_preserve_retryable_close() -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._issue_debug = {}
    orchestrator._record_tracker_error("id-APP-1", "prefix " + "x" * 700)
    assert subject._issue_debug["id-APP-1"].tracker_error == "x" * 500

    client = object()
    subject._shared_tracker_client = lambda _cfg: client
    subject._tracker_client_close_race_logged = False

    def unavailable(_client: Any) -> None:
        raise core_module._TrackerClientUnavailable("closing")

    with pytest.raises(core_module._TrackerClientUnavailable, match="closing"):
        orchestrator._invoke_shared_tracker_client(_cfg(), unavailable)


@pytest.mark.parametrize("release_evidence", [False, True])
@pytest.mark.asyncio
async def test_startup_release_guard_registry_failure_distinguishes_ordinary_ticket(
    monkeypatch: pytest.MonkeyPatch,
    release_evidence: bool,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue(
        "VERIFY-1",
        state="Done",
        labels=("release-cycle-verifier",) if release_evidence else (),
    )
    monkeypatch.setattr(
        orchestrator,
        "_release_registry_call",
        lambda *_a: (_ for _ in ()).throw(SymphonyError("registry unavailable")),
    )
    monkeypatch.setattr(
        core_module,
        "resolve_target_release_identity",
        lambda **_k: SimpleNamespace(errors=("not a release workspace",)),
    )

    result = await orchestrator._startup_release_terminal_guard(_exit_cfg(), issue)

    assert result == (issue, release_evidence, release_evidence)


@pytest.mark.asyncio
async def test_startup_release_guard_owned_cleanup_yields_to_new_live_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("VERIFY-1", state="Done")
    gate = _approved_core_gate()

    def registry_call(_cfg: Any, operation: str, _callback: Any) -> Any:
        if operation == "startup_read_verifier_gate":
            return gate
        if operation in {
            "startup_read_finalizer_gate",
            "startup_read_release_evidence_identity",
        }:
            return None
        if operation == "startup_heartbeat_release_cleanup":
            return False
        raise AssertionError(operation)

    monkeypatch.setattr(orchestrator, "_release_registry_call", registry_call)

    result = await orchestrator._startup_release_terminal_guard(
        _exit_cfg(), issue, owned_cleanup_run_id="cleanup-run"
    )

    assert result == (issue, True, True)


@pytest.mark.parametrize(
    ("authority_kind", "expected_state", "stopped"),
    [
        ("finalizer", "Done", False),
        ("pending_verifier", "Verify", True),
        ("retired_identity", "Done", False),
        ("current_identity", "Verify", True),
        ("historical_verifier_label", "Done", False),
    ],
)
@pytest.mark.asyncio
async def test_startup_release_guard_maps_authority_to_cleanup_or_rewind(
    monkeypatch: pytest.MonkeyPatch,
    authority_kind: str,
    expected_state: str,
    stopped: bool,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    labels = (
        ("app-release-finalizer",)
        if authority_kind == "finalizer"
        else ("release-cycle-verifier",)
        if authority_kind == "historical_verifier_label"
        else ()
    )
    identifier = "APP-FINAL" if authority_kind == "finalizer" else "VERIFY-1"
    issue = _issue(identifier, state="Done", labels=labels)
    gate = _approved_core_gate() if authority_kind == "finalizer" else None
    verifier_gate = _gate() if authority_kind == "pending_verifier" else None
    identity = (
        ReleaseEvidenceIdentity(
            issue_id=issue.id,
            identifier=issue.identifier,
            finalizer_identifier="APP-FINAL",
            role="verifier",
            cycle_generation="generation",
            retired=authority_kind == "retired_identity",
            recorded_at=NOW,
            updated_at=NOW,
        )
        if authority_kind in {"retired_identity", "current_identity"}
        else None
    )
    responses = iter([verifier_gate, gate, identity, False])
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    monkeypatch.setattr(orchestrator, "_guard_release_finalizer", lambda **_k: issue)
    rewinds: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **kwargs: asyncio.sleep(
            0,
            result=rewinds.append(kwargs["producing_state"])
            or replace(issue, state="Verify"),
        ),
    )

    (
        guarded,
        evidence_only,
        was_stopped,
    ) = await orchestrator._startup_release_terminal_guard(_exit_cfg(), issue)

    assert evidence_only
    assert was_stopped is stopped
    assert guarded.state == expected_state
    assert bool(rewinds) is stopped


@pytest.mark.asyncio
async def test_startup_terminal_cleanup_handles_fetch_and_missing_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_terminal_issues",
        lambda _cfg: (_ for _ in ()).throw(OSError("tracker offline")),
    )
    subject._workspace_manager = object()
    await orchestrator._startup_terminal_cleanup(_exit_cfg())

    monkeypatch.setattr(orchestrator, "_tracker_call_terminal_issues", lambda _cfg: [])
    subject._workspace_manager = None
    await orchestrator._startup_terminal_cleanup(_exit_cfg())


@pytest.mark.parametrize("second_guard_stops", [False, True])
@pytest.mark.asyncio
async def test_startup_release_cleanup_claim_is_finished_after_remove_or_rewind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_guard_stops: bool,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("VERIFY-1", state="Done", labels=("release-cycle-verifier",))
    workspace = tmp_path / issue.identifier
    workspace.mkdir()
    cfg = _exit_cfg()
    cfg.agent.kind = "codex"
    cfg.agent.kind_for_state = lambda _state, _requested: "codex"
    cfg.agent.auto_commit_on_done = True
    subject._workspace_manager = SimpleNamespace(
        path_for=lambda _identifier: workspace,
        remove=lambda path: asyncio.sleep(0, result=path.rmdir()),
    )
    monkeypatch.setattr(
        orchestrator, "_tracker_call_terminal_issues", lambda _cfg: [issue]
    )
    guard_results = iter(
        [
            (issue, True, False),
            (replace(issue, state="Verify"), True, second_guard_stops),
        ]
    )
    monkeypatch.setattr(
        orchestrator,
        "_startup_release_terminal_guard",
        lambda *_a, **_k: asyncio.sleep(0, result=next(guard_results)),
    )
    monkeypatch.setattr(
        orchestrator,
        "_try_acquire_run_lease",
        lambda **_k: core_module._RunLeaseAcquisition("cleanup-run"),
    )
    committed: list[str] = []
    monkeypatch.setattr(
        core_module,
        "commit_workspace_on_done",
        lambda *_a, **kwargs: asyncio.sleep(
            0, result=committed.append(kwargs["identifier"])
        ),
    )
    finished: list[str] = []

    def finish(_cfg: Any, operation: str, _callback: Any) -> None:
        assert operation == "finish_startup_release_cleanup"
        finished.append(operation)
        raise SymphonyError("registry close race")

    monkeypatch.setattr(orchestrator, "_release_registry_call", finish)

    await orchestrator._startup_terminal_cleanup(cfg)

    assert finished == ["finish_startup_release_cleanup"]
    assert committed == ([] if second_guard_stops else [issue.identifier])
    assert workspace.exists() is second_guard_stops


@pytest.mark.asyncio
async def test_startup_done_workspace_is_preserved_when_auto_merge_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1", state="Done")
    workspace = tmp_path / issue.identifier
    workspace.mkdir()
    cfg = _exit_cfg()
    cfg.agent.auto_merge_on_done = False
    subject._workspace_manager = SimpleNamespace(
        path_for=lambda _identifier: workspace,
        remove=lambda _path: (_ for _ in ()).throw(AssertionError("must preserve")),
    )
    subject._issue_debug = {}
    monkeypatch.setattr(
        orchestrator, "_tracker_call_terminal_issues", lambda _cfg: [issue]
    )
    monkeypatch.setattr(
        orchestrator,
        "_startup_release_terminal_guard",
        lambda *_a, **_k: asyncio.sleep(0, result=(issue, False, False)),
    )

    await orchestrator._startup_terminal_cleanup(cfg)

    assert workspace.exists()


@pytest.mark.asyncio
async def test_worker_continuation_resume_failure_is_redacted_and_cleaned_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = _issue("APP-1")
    checkpoint = ContinuationCheckpoint(
        resume_session_id="private-session",
        state="Todo",
        turn=2,
        checkpointed_at=NOW,
    )
    entry = _runtime_entry(issue, continuation_checkpoint=checkpoint)
    backend = _WorkerLoopBackend(resume_error=OSError("resume failed"))
    orchestrator, cfg, exits, _events = _configure_worker_loop(
        monkeypatch,
        tmp_path,
        issue=issue,
        entry=entry,
        backend=backend,
    )

    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert backend.calls == ["start", "initialize", "resume_session", "stop"]
    assert exits[0][0] == "error"
    assert "exact session continuation failed" in (exits[0][1] or "")


@pytest.mark.asyncio
async def test_worker_pause_resume_refreshes_ticket_before_empty_turn_escalation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = _issue("APP-1")
    refreshed = replace(issue, title="operator-updated title")
    entry = _runtime_entry(issue, hit_empty_response_loop=True)
    backend = _WorkerLoopBackend()
    orchestrator, cfg, exits, _events = _configure_worker_loop(
        monkeypatch,
        tmp_path,
        issue=issue,
        entry=entry,
        backend=backend,
    )

    class Pause:
        def is_set(self) -> bool:
            return False

        async def wait(self) -> None:
            return None

    pause = Pause()
    cast(Any, orchestrator)._pause_events = {issue.id: pause}
    monkeypatch.setattr(
        orchestrator,
        "_refresh_issue_state",
        lambda *_a: asyncio.sleep(0, result=refreshed),
    )
    escalated: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_escalate_empty_response_loop",
        lambda **kwargs: asyncio.sleep(
            0, result=escalated.append(kwargs["entry"].issue.title)
        ),
    )

    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert escalated == ["operator-updated title"]
    assert entry.issue == refreshed
    assert exits == [("normal", None)]


@pytest.mark.parametrize("authority_mode", ["lease_lost", "gate_lost"])
@pytest.mark.asyncio
async def test_worker_release_authority_loss_stops_before_backend_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority_mode: str,
) -> None:
    issue = _issue("VERIFY-1", state="Verify")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_release_cycle_verifier=True,
    )
    backend = _WorkerLoopBackend()
    orchestrator, cfg, exits, _events = _configure_worker_loop(
        monkeypatch,
        tmp_path,
        issue=issue,
        entry=entry,
        backend=backend,
    )
    heartbeats = 0

    def heartbeat(*_args: Any) -> bool:
        nonlocal heartbeats
        heartbeats += 1
        return authority_mode != "lease_lost" or heartbeats == 1

    monkeypatch.setattr(
        orchestrator,
        "_heartbeat_run_lease",
        heartbeat,
    )
    authority_checks = 0

    def require_authority(**_kwargs: Any) -> Issue:
        nonlocal authority_checks
        authority_checks += 1
        if authority_mode == "gate_lost" and authority_checks > 1:
            raise SymphonyError("gate generation changed")
        return issue

    monkeypatch.setattr(
        orchestrator,
        "_require_running_release_authority",
        require_authority,
    )

    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert "run_turn" not in backend.calls
    assert exits[0][0] == "release_authority_error"
    expected = (
        "lease was lost" if authority_mode == "lease_lost" else "generation changed"
    )
    assert expected in (exits[0][1] or "")


@pytest.mark.parametrize("refresh_mode", ["missing", "concurrent_exit", "second_hook"])
@pytest.mark.asyncio
async def test_worker_post_turn_boundaries_preserve_commit_and_report_exact_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refresh_mode: str,
) -> None:
    issue = _issue("APP-1")
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    entry = _runtime_entry(issue, workspace_path=workspace)
    backend = _WorkerLoopBackend()
    manager = _WorkerLoopManager(
        workspace, fail_second_before=refresh_mode == "second_hook"
    )
    orchestrator, cfg, exits, events = _configure_worker_loop(
        monkeypatch,
        tmp_path,
        issue=issue,
        entry=entry,
        backend=backend,
        manager=manager,
    )
    monkeypatch.setattr(core_module.git_inspect, "resolve_commit", lambda *_a: "a" * 40)

    async def refresh(*_args: Any) -> Issue | None:
        if refresh_mode == "missing":
            return None
        if refresh_mode == "concurrent_exit":
            cast(Any, orchestrator)._dispatch_state.running.pop(issue.id, None)
        return issue

    monkeypatch.setattr(orchestrator, "_refresh_issue_state", refresh)

    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert "workspace_updated" in events
    expected = {
        "missing": "issue_state_refresh_failed",
        "concurrent_exit": "orphaned",
        "second_hook": "before_run_error",
    }[refresh_mode]
    if refresh_mode == "concurrent_exit":
        assert exits == []
        assert issue.id not in cast(Any, orchestrator)._dispatch_state.running
    else:
        assert exits[0][0] == expected


@pytest.mark.asyncio
async def test_worker_phase_handoff_tolerates_concurrent_slot_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = _issue("APP-1", state="Todo")
    transitioned_issue = replace(issue, state="Verify")
    entry = _runtime_entry(issue)
    backend = _WorkerLoopBackend()
    orchestrator, cfg, exits, _events = _configure_worker_loop(
        monkeypatch,
        tmp_path,
        issue=issue,
        entry=entry,
        backend=backend,
    )
    monkeypatch.setattr(
        orchestrator,
        "_refresh_issue_state",
        lambda *_a: asyncio.sleep(0, result=transitioned_issue),
    )

    async def transition(**kwargs: Any) -> Any:
        cast(Any, orchestrator)._dispatch_state.running.pop(issue.id, None)
        state = kwargs["phase_state"]
        return core_module._AgentPhaseTransition(
            state=replace(state, issue=transitioned_issue, current_state="verify"),
            is_rewind=False,
        )

    monkeypatch.setattr(orchestrator, "_transition_agent_phase", transition)

    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert backend.calls.count("run_turn") == 1
    assert exits == []
    assert issue.id not in cast(Any, orchestrator)._dispatch_state.running


@pytest.mark.parametrize("rewind_fails", [False, True])
@pytest.mark.asyncio
async def test_worker_finalizer_gate_loss_rewinds_before_accepting_state_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rewind_fails: bool,
) -> None:
    issue = _issue("APP-FINAL", state="Document")
    transitioned = replace(issue, state="Done")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_app_release_finalizer=True,
        release_gate_finalizer="APP-FINAL",
    )
    backend = _WorkerLoopBackend()
    orchestrator, cfg, exits, _events = _configure_worker_loop(
        monkeypatch,
        tmp_path,
        issue=issue,
        entry=entry,
        backend=backend,
    )
    monkeypatch.setattr(orchestrator, "_heartbeat_run_lease", lambda *_a: True)
    monkeypatch.setattr(
        orchestrator, "_require_running_release_authority", lambda **_k: issue
    )
    monkeypatch.setattr(
        orchestrator,
        "_refresh_issue_state",
        lambda *_a: asyncio.sleep(0, result=transitioned),
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: None)

    async def rewind(**_kwargs: Any) -> Issue:
        if rewind_fails:
            raise OSError("board rewind failed")
        return replace(transitioned, state="Document")

    monkeypatch.setattr(orchestrator, "_rewind_app_release_transition", rewind)

    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert exits[0][0] == "phase_transition_error"
    assert "authority disappeared" in (exits[0][1] or "")
    if not rewind_fails:
        assert entry.issue.state == "Document"


@pytest.mark.parametrize("has_failure_lane", [False, True])
@pytest.mark.asyncio
async def test_worker_release_rewind_budget_escalates_or_holds_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_failure_lane: bool,
) -> None:
    issue = _issue("VERIFY-1", state="Verify")
    transitioned = replace(issue, state="Done")
    rewound = replace(issue, state="Verify")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_release_cycle_verifier=True,
    )
    backend = _WorkerLoopBackend()
    orchestrator, cfg, exits, _events = _configure_worker_loop(
        monkeypatch,
        tmp_path,
        issue=issue,
        entry=entry,
        backend=backend,
    )
    cfg.tracker.terminal_states = ("Done", "Blocked") if has_failure_lane else ("Done",)
    cast(Any, orchestrator)._issue_debug[issue.id] = core_module._IssueDebug(
        rewind_count=cfg.agent.max_attempts
    )
    monkeypatch.setattr(orchestrator, "_heartbeat_run_lease", lambda *_a: True)
    monkeypatch.setattr(
        orchestrator, "_require_running_release_authority", lambda **_k: issue
    )
    monkeypatch.setattr(
        orchestrator,
        "_refresh_issue_state",
        lambda *_a: asyncio.sleep(0, result=transitioned),
    )
    monkeypatch.setattr(
        orchestrator,
        "_enforce_app_release_transition",
        lambda **_k: asyncio.sleep(0, result=(rewound, True)),
    )
    moved: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_update_state",
        lambda _cfg, _issue, state: moved.append(state),
    )

    await orchestrator._run_agent_attempt(issue, None, cfg)

    assert exits == [("normal", None)]
    if has_failure_lane:
        assert moved == ["Blocked"]
        assert entry.issue.state == "Blocked"
    else:
        assert moved == []
        assert entry.release_gate_exhausted


@pytest.mark.asyncio
async def test_continuous_improvement_cancellation_releases_lease_without_consuming_turn() -> (
    None
):
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    released: list[str] = []
    subject._improvement_lease = SimpleNamespace(
        acquire=lambda: True, release=lambda: released.append("released")
    )

    async def cancelled(*_args: Any) -> Any:
        raise asyncio.CancelledError

    subject._improvement_runner = cancelled
    subject._improvement_run_timeout_s = 5.0
    subject._improvement_status = {"in_flight": True}
    subject._improvement_turns_used = 2
    cfg = SimpleNamespace(
        workflow_path=Path("/repo/WORKFLOW.md"),
        continuous_improvement=SimpleNamespace(interval_ms=1000),
    )

    with pytest.raises(asyncio.CancelledError):
        await orchestrator._run_continuous_improvement(cfg)

    assert released == ["released"]
    assert subject._improvement_turns_used == 2
    assert not subject._improvement_status["in_flight"]


@pytest.mark.asyncio
async def test_improvement_agent_ignores_backend_events_and_returns_last_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    cfg = SimpleNamespace(
        agent=SimpleNamespace(kind="codex"),
        continuous_improvement=SimpleNamespace(agent_kind=None),
    )
    subject._workflow_state = SimpleNamespace(current=lambda: cfg)
    monkeypatch.setattr(core_module, "cfg_for_mode", lambda *_a: (cfg, "codex"))
    monkeypatch.setattr(core_module, "_worktree_status_snapshot", lambda _path: {})

    class Backend:
        def __init__(self, init: Any) -> None:
            self.init = init

        async def start(self) -> None:
            await self.init.on_event({"event": "noise"})

        async def initialize(self) -> None:
            return None

        async def start_session(self, **_kwargs: Any) -> None:
            return None

        async def run_turn(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(last_message="proposal ready")

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(
        orchestrator, "_build_agent_backend", lambda init: Backend(init)
    )
    task = core_module.AgentTask(
        mode="code-health",
        prompt="inspect",
        cwd=tmp_path,
        output_path=tmp_path / "proposal.json",
    )

    assert await orchestrator._run_improvement_agent(task) == "proposal ready"


@pytest.mark.asyncio
async def test_release_transition_recovers_pending_gate_for_pre_upgrade_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._dispatch_state = SimpleNamespace(running={})
    issue = _issue("VERIFY-1", state="Done")
    cfg = _exit_cfg()
    responses = iter([None, None])
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(
        orchestrator, "_release_registry_call", lambda *_a: next(responses)
    )
    monkeypatch.setattr(
        core_module,
        "resolve_target_release_identity",
        lambda **_k: SimpleNamespace(
            errors=(), finalizer_ticket="APP-FINAL", contract_sha256="a" * 64
        ),
    )
    pending = _gate()
    persisted: list[str] = []
    monkeypatch.setattr(orchestrator, "_pending_release_gate", lambda **_k: pending)
    monkeypatch.setattr(
        orchestrator,
        "_persist_pending_release_gate",
        lambda **kwargs: persisted.append(kwargs["operation"]) or pending,
    )
    validation = _release_validation(
        passed=False, evidence_errors=("target evidence missing",)
    )
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )
    rewound = replace(issue, state="Verify")
    monkeypatch.setattr(
        orchestrator,
        "_rewind_app_release_transition",
        lambda **_k: asyncio.sleep(0, result=rewound),
    )

    result = await orchestrator._enforce_app_release_transition_inner(
        cfg=cfg,
        issue=issue,
        workspace_path=Path("workspace/VERIFY-1"),
        producing_state="Verify",
        known_app_release=True,
        running_entry=None,
    )

    assert persisted == ["recover_inflight_pending_gate"]
    assert result == (rewound, True)


@pytest.mark.asyncio
async def test_release_cycle_refuses_relink_when_fresh_gate_cannot_be_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    subject._dispatch_state = SimpleNamespace(running={})
    issue = _issue("VERIFY-1", state="Done")
    cfg = _exit_cfg()
    cfg.agent.kind = "codex"
    gate = replace(_gate(), expected_contract_sha256="c" * 64)
    validation = _release_validation(
        passed=False, with_failure=True, contract_sha256="c" * 64
    )
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        core_module, "validate_release_contract", lambda **_k: validation
    )

    class Registry:
        def __init__(self, _path: Path) -> None:
            return None

        def replace_pending_release_gate(self, _pending: ReleaseGate) -> None:
            return None

        def get_release_gate(self, _identifier: str) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(core_module, "RunRegistry", Registry)

    def reconcile(*_args: Any, **kwargs: Any) -> Any:
        kwargs["before_finalizer_relink"](issue)
        raise AssertionError("relink must not run after unreadable gate")

    monkeypatch.setattr(
        orchestrator, "_tracker_call_reconcile_release_cycle", reconcile
    )

    with pytest.raises(SymphonyError, match="was not persisted before relink"):
        await orchestrator._enforce_app_release_transition_inner(
            cfg=cfg,
            issue=issue,
            workspace_path=Path("workspace/VERIFY-1"),
            producing_state="Verify",
            known_app_release=True,
            running_entry=None,
        )


@pytest.mark.parametrize("budget_kind", ["empty_response_loop", "no_stage_change"])
@pytest.mark.asyncio
async def test_budget_handoff_note_explains_empty_or_stage_stall(
    monkeypatch: pytest.MonkeyPatch,
    budget_kind: str,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    subject = cast(Any, orchestrator)
    issue = _issue("APP-1", state="" if budget_kind == "no_stage_change" else "Verify")
    entry = _runtime_entry(
        issue,
        consecutive_empty_turns=core_module.EMPTY_TURN_LOOP_THRESHOLD,
    )
    cfg = _exit_cfg()
    subject._issue_debug = {
        issue.id: core_module._IssueDebug(state_turn_state="Verify", state_turn_count=4)
    }
    notes: list[str] = []
    monkeypatch.setattr(orchestrator, "_tracker_call_update_state", lambda *_a: None)
    monkeypatch.setattr(
        orchestrator,
        "_tracker_call_append_note",
        lambda _cfg, _issue, _heading, body: notes.append(body),
    )
    monkeypatch.setattr(orchestrator, "_clear_tracker_error", lambda _issue_id: None)

    assert await orchestrator._persist_budget_exhausted_state(
        cfg=cfg,
        entry=entry,
        issue_id=issue.id,
        target_state="Blocked",
        budget_kind=budget_kind,
        state_turn_limit=5,
    )
    expected = (
        "consecutive_empty_turns=3"
        if budget_kind == "empty_response_loop"
        else "state_turns=4"
    )
    assert expected in notes[0]
    if budget_kind == "no_stage_change":
        assert "Verify" in notes[0]


@pytest.mark.asyncio
async def test_worker_exit_valid_finalizer_gate_allows_evidence_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = core_module.Orchestrator.__new__(core_module.Orchestrator)
    issue = _issue("APP-FINAL", state="Done")
    entry = _runtime_entry(
        issue,
        known_app_release=True,
        known_app_release_finalizer=True,
        release_gate_finalizer="APP-FINAL",
        release_finalizer_rewind_state="Document",
    )
    cfg = _exit_cfg()
    subject, notifications = _configure_exit_subject(orchestrator, entry, cfg)
    removed: list[Path] = []
    subject._workspace_manager = SimpleNamespace(
        remove=lambda path: asyncio.sleep(0, result=removed.append(path))
    )
    gate = _approved_core_gate()
    monkeypatch.setattr(
        orchestrator, "_refresh_issue_full", lambda *_a: asyncio.sleep(0, result=issue)
    )
    monkeypatch.setattr(orchestrator, "_release_registry_call", lambda *_a: gate)
    monkeypatch.setattr(
        orchestrator,
        "_guard_release_finalizer_with_version",
        lambda **_k: (issue, "completion-token"),
    )
    monkeypatch.setattr(
        orchestrator, "_mark_release_finalizer_completed", lambda **_k: gate
    )
    guarded: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "_guard_release_finalizer",
        lambda **_k: guarded.append(issue.identifier) or issue,
    )

    await orchestrator._on_worker_exit_impl(issue.id, "normal", None)

    assert guarded == [issue.identifier]
    assert removed == [entry.workspace_path]
    assert notifications == ["notified"]
