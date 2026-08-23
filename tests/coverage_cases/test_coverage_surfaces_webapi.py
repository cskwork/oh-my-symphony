"""Coverage contracts for web API request and mutation surfaces."""
# ruff: noqa: F405

from tests.coverage_cases._surfaces_support import *  # noqa: F403


@pytest.mark.parametrize(
    ("checker", "value", "message"),
    [
        (webapi._check_identifier, "../escape", "identifier must match"),
        (webapi._check_branch, "", "branch is required"),
        (webapi._check_branch, "bad branch", "invalid branch name"),
        (webapi._check_title, " ", "title is required"),
        (webapi._check_title, "x" * (webapi._MAX_TITLE + 1), "title too long"),
        (webapi._check_description, 42, "description must be a string"),
        (webapi._check_priority, True, "priority must be an integer"),
        (webapi._check_priority, 5, "priority must be between"),
        (webapi._check_labels, "release", "labels must be a list"),
        (webapi._check_request, 42, "request must be a string"),
        (webapi._check_request_schedule_key, "", "schedule id is required"),
        (webapi._check_request_schedule_key, "bad\nkey", "schedule id is invalid"),
        (webapi._check_blocked_by, "ISSUE-1", "blocked_by must be a list"),
        (webapi._check_blocked_by, [42], "blocked_by must be a list"),
        (webapi._check_agent_kind, 42, "agent_kind must be a string"),
        (webapi._check_chat_session_id, "../chat", "invalid chat session id"),
        (webapi._check_project_setup_action_id, "bad", "invalid project setup"),
        (webapi._check_chat_confirmation_token, "short", "invalid chat confirmation"),
    ],
)
def test_web_mutation_boundaries_reject_unsafe_values(
    checker: Any, value: Any, message: str
) -> None:
    """Unsafe browser input fails before it can reach disk, Git, or a process."""
    with pytest.raises(WorkflowMutationError, match=message):
        checker(value)


def test_web_mutation_boundaries_normalize_safe_values() -> None:
    assert webapi._check_description(None) == ""
    assert webapi._check_priority("") is None
    assert webapi._check_labels([" Release ", 4, "release", "UI"]) == [
        "release",
        "ui",
    ]
    assert webapi._check_request(None) == ""
    assert webapi._check_blocked_by(["ISSUE-1", "ISSUE-1", "ISSUE-2"]) == [
        "ISSUE-1",
        "ISSUE-2",
    ]
    assert webapi._check_agent_kind(None) == ""
    assert webapi._check_chat_confirmation_token(None) is None
    assert webapi._check_budget("", "max_turns") is None
    assert webapi._check_budget(0, "max_turns") == 0


def test_web_boundary_limits_reject_oversized_body_and_label_sets() -> None:
    with pytest.raises(WorkflowMutationError, match="description too long"):
        webapi._check_description("x" * (webapi._MAX_BODY + 1))
    with pytest.raises(WorkflowMutationError, match="too many labels"):
        webapi._check_labels(
            [f"label-{index}" for index in range(webapi._MAX_LABELS + 1)]
        )
    assert webapi._check_ci_modes(None) == []


def test_web_context_reloads_config_and_rejects_non_file_editing(
    tmp_path: Path,
) -> None:
    workflow_state = SimpleNamespace(
        current=lambda: None,
        reload=lambda: (None, "invalid workflow"),
    )
    ctx = webapi._Ctx(SimpleNamespace(workflow_state=workflow_state))  # type: ignore[arg-type]
    with pytest.raises(WorkflowMutationError, match="workflow not loaded"):
        ctx.config()

    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        tracker=SimpleNamespace(kind="linear"),
        artifacts=SimpleNamespace(enabled=False),
    )
    orchestrator = cast(
        Any,
        SimpleNamespace(
            workflow_state=SimpleNamespace(
                current=lambda: cfg, reload=lambda: (cfg, None)
            )
        ),
    )
    ctx = webapi._Ctx(orchestrator)
    with pytest.raises(WorkflowMutationError, match="tracker.kind: file"):
        ctx.file_tracker()
    assert ctx.artifacts() is None


def test_web_loopback_and_origin_boundaries_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert webapi._request_is_loopback(
        cast(Any, SimpleNamespace(remote=None, app={webapi.BIND_HOST_KEY: "127.0.0.1"}))
    )
    assert not webapi._request_is_loopback(
        cast(Any, SimpleNamespace(remote="not-an-ip", app={}))
    )
    monkeypatch.setenv(webapi.TRUSTED_ORIGINS_ENV, "https://board.example")
    assert webapi._host_is_declared_trusted("board.example") is True
    assert webapi._host_is_declared_trusted("other.example") is False
    monkeypatch.setenv(webapi.TRUSTED_ORIGINS_ENV, "*")
    assert webapi._host_is_declared_trusted("anything.example") is True
    assert webapi._origin_is_trusted(
        cast(Any, SimpleNamespace()), "https://evil.example"
    )
    monkeypatch.setenv(webapi.TRUSTED_ORIGINS_ENV, "board.example")
    assert webapi._host_is_declared_trusted("") is False
    assert webapi._origin_is_trusted(
        cast(Any, SimpleNamespace()), "https://board.example:8443"
    )


@pytest.mark.asyncio
async def test_web_json_reader_and_wrapper_return_stable_error_shapes() -> None:
    app = web.Application()

    async def read(request: web.Request) -> web.Response:
        return web.json_response(await webapi._read_json(request))

    async def domain_error(_request: web.Request) -> web.Response:
        raise WorkflowMutationError("unsafe mutation")

    async def unexpected(_request: web.Request) -> web.Response:
        raise RuntimeError("private detail")

    app.router.add_get("/empty", read)
    app.router.add_post("/body", read)
    app.router.add_get("/domain", webapi._wrap(domain_error))
    app.router.add_get("/unexpected", webapi._wrap(unexpected))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        assert await (await client.get("/empty")).json() == {}
        invalid = await client.post("/body", json=[])
        assert invalid.status == 400
        assert (await invalid.json())["error"]["code"] == "invalid_body"
        domain = await client.get("/domain")
        assert domain.status == 400
        assert (await domain.json())["error"]["message"] == "unsafe mutation"
        internal = await client.get("/unexpected")
        assert internal.status == 500
        assert (await internal.json())["error"]["code"] == "internal_error"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_run_diagnostics_routes_fail_closed_and_bound_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic_calls = 0

    class Orchestrator:
        async def recent_runs(self, **kwargs: Any) -> tuple[list[Any], None]:
            assert kwargs["limit"] == 50
            return [], None

        async def run_detail(self, _run_id: str) -> tuple[None, str]:
            return None, "registry unavailable"

        async def run_diagnostic(self, _run_id: str) -> tuple[None, str | None]:
            nonlocal diagnostic_calls
            diagnostic_calls += 1
            return (
                (None, "registry unavailable")
                if diagnostic_calls == 1
                else (None, None)
            )

    orchestrator = Orchestrator()
    app = web.Application()
    webapi._register_issue_routes(
        app,
        cast(Any, SimpleNamespace()),
        cast(Any, orchestrator),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    run_id = "a" * 32
    try:
        monkeypatch.setattr(webapi, "_request_is_loopback", lambda _request: False)
        forbidden = await client.get("/api/v1/runs")
        assert forbidden.status == 403
        detail_forbidden = await client.get(f"/api/v1/runs/{run_id}")
        assert detail_forbidden.status == 403
        diagnostic_forbidden = await client.get(f"/api/v1/runs/{run_id}/diagnostic")
        assert diagnostic_forbidden.status == 403

        monkeypatch.setattr(webapi, "_request_is_loopback", lambda _request: True)
        runs = await client.get("/api/v1/runs?limit=invalid")
        assert runs.status == 200
        detail = await client.get(f"/api/v1/runs/{run_id}")
        assert detail.status == 503
        diagnostic = await client.get(f"/api/v1/runs/{run_id}/diagnostic")
        assert diagnostic.status == 503
        missing = await client.get(f"/api/v1/runs/{run_id}/diagnostic")
        assert missing.status == 404
        invalid = await client.get("/api/v1/runs/not-a-run/diagnostic")
        assert invalid.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_issue_mutation_routes_preserve_edit_and_rejection_contracts() -> (
    None
):
    issue = _issue(identifier="EDIT-1")
    updates: list[dict[str, Any]] = []

    class Tracker:
        def fetch_issue_full_by_id(self, _identifier: str) -> Issue:
            return issue

        def update_fields(self, _identifier: str, **fields: Any) -> None:
            updates.append(fields)

        def delete(self, _identifier: str) -> None:
            raise chat.SymphonyError("missing")

    class Stats:
        def record_transition(self, **_kwargs: Any) -> None:
            return None

    class Context:
        def config(self) -> Any:
            return SimpleNamespace(
                tracker=SimpleNamespace(
                    kind="file", active_states=("Todo",), terminal_states=("Done",)
                )
            )

        def file_tracker(self) -> Tracker:
            return Tracker()

        def artifacts(self) -> None:
            return None

        def stats(self) -> Stats:
            return Stats()

    class Orchestrator:
        def __init__(self) -> None:
            self.refreshes = 0

        def request_refresh(self) -> None:
            self.refreshes += 1

        def find_running_issue_id(self, _identifier: str) -> None:
            return None

        async def recover_blocked_issue(self, identifier: str, **_kwargs: Any) -> Any:
            return False, f"unknown issue {identifier}", {}

        async def skip_document(self, _identifier: str) -> tuple[bool, str]:
            return False, "not in Document"

    orchestrator = Orchestrator()
    app = web.Application()
    webapi._register_issue_routes(app, cast(Any, Context()), cast(Any, orchestrator))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        artifacts = await client.get("/api/v1/issues/EDIT-1/artifacts")
        assert await artifacts.json() == {"artifacts": [], "enabled": False}
        artifact = await client.get("/api/v1/issues/EDIT-1/artifacts/log.txt")
        assert artifact.status == 404

        patched = await client.patch(
            "/api/v1/issues/EDIT-1",
            json={
                "description": "updated",
                "priority": None,
                "skills": ["review", 3],
                "agent_kind": "codex",
            },
        )
        assert patched.status == 200
        assert updates == [
            {
                "description": "updated",
                "clear_priority": True,
                "skills": ["review"],
                "agent_kind": "codex",
            }
        ]
        priority = await client.patch("/api/v1/issues/EDIT-1", json={"priority": 3})
        assert priority.status == 200
        assert updates[-1] == {"priority": 3}
        invalid_identifier = await client.post(
            "/api/v1/issues", json={"title": "Invalid", "identifier": 3}
        )
        assert invalid_identifier.status == 400
        invalid_prefix = await client.post(
            "/api/v1/issues", json={"title": "Invalid", "prefix": "bad prefix"}
        )
        assert invalid_prefix.status == 400

        invalid_skills = await client.patch(
            "/api/v1/issues/EDIT-1", json={"skills": "review"}
        )
        assert invalid_skills.status == 400
        invalid_recovery = await client.post(
            "/api/v1/issues/EDIT-1/recover-blocked", json={"fix_state": 4}
        )
        assert invalid_recovery.status == 400
        rejected = await client.post("/api/v1/issues/EDIT-1/recover-blocked", json={})
        assert rejected.status == 404
        deleted = await client.delete("/api/v1/issues/EDIT-1")
        assert deleted.status == 404
        skipped = await client.post("/api/v1/issues/EDIT-1/skip-document", json={})
        assert skipped.status == 409
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_request_catalog_bounds_graph_and_tracker_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = _issue(identifier="REQ-1", request="GROUP-1")

    class Tracker:
        def fetch_issues_by_states(self, _states: Any) -> list[Issue]:
            return [issue]

    cfg = SimpleNamespace(
        tracker=SimpleNamespace(
            kind="linear", active_states=("Todo",), terminal_states=("Done",)
        ),
        agent=SimpleNamespace(scheduling_policy="fifo"),
    )
    ctx = cast(Any, SimpleNamespace(config=lambda: cfg))
    orchestrator = cast(
        Any,
        SimpleNamespace(
            schedule_snapshot=lambda: {"available": False, "entries": []},
            dependency_state_resolved=lambda _state: False,
        ),
    )
    monkeypatch.setattr(webapi, "FileBoardTracker", lambda _cfg: Tracker())
    app = web.Application()
    webapi._register_issue_routes(app, ctx, orchestrator)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        unsupported = await client.get("/api/v1/requests/GROUP-1/schedule")
        assert unsupported.status == 200
        cfg.tracker.kind = "file"
        catalog = await client.get("/api/v1/requests")
        assert catalog.status == 200
        assert (await catalog.json())["requests"][0]["counts"]["waiting"] == 1
        invalid_kind = await client.get(
            "/api/v1/requests/GROUP-1/schedule?kind=invalid"
        )
        assert invalid_kind.status == 400
        monkeypatch.setattr(webapi, "MAX_DEPENDENCY_NODES", 0)
        oversized = await client.get("/api/v1/requests")
        assert oversized.status == 413
        oversized_detail = await client.get("/api/v1/requests/GROUP-1/schedule")
        assert oversized_detail.status == 413
    finally:
        await client.close()


def test_web_schedule_payload_surfaces_dangling_and_terminal_dependencies() -> None:
    dangling = _issue(
        id="a",
        identifier="A",
        blocked_by=(BlockerRef(id="ghost", identifier="GHOST", state=None),),
        request="REQ-1",
    )
    successful = _issue(
        id="done",
        identifier="DONE",
        state="Done",
        request="REQ-1",
        priority=None,
        labels=(),
    )
    unresolved = _issue(
        id="cancelled",
        identifier="CANCELLED",
        state="Cancelled",
        request="REQ-1",
        priority=None,
        labels=(),
    )
    ignored_ref = _issue(
        id="empty-ref",
        identifier="EMPTY",
        blocked_by=(BlockerRef(id=None, identifier=None, state=None),),
        request="REQ-1",
    )
    orchestrator = SimpleNamespace(
        dependency_state_resolved=lambda state: state.strip().lower() == "done"
    )
    cfg = SimpleNamespace(
        tracker=SimpleNamespace(terminal_states=("Done", "Cancelled"))
    )

    payload = webapi._request_group_schedule_payload(
        key=RequestGroupKey("request", "REQ-1"),
        members=[dangling, successful, unresolved, ignored_ref],
        all_issues=[dangling, successful, unresolved, ignored_ref],
        schedule={"available": False, "entries": []},
        orchestrator=orchestrator,  # type: ignore[arg-type]
        cfg=cfg,  # type: ignore[arg-type]
    )

    decisions = {node["identifier"]: node["decision"] for node in payload["nodes"]}
    assert decisions["GHOST"]["code"] == "dangling_dependency"
    assert decisions["DONE"]["code"] == "terminal_success"
    assert decisions["CANCELLED"]["code"] == "terminal_needs_action"
    assert "dangling_dependency:GHOST" in payload["warnings"]
    assert payload["summary"]["longest_unresolved_chain_nodes"] == 2


def test_web_schedule_payload_marks_cycles_and_stale_decisions() -> None:
    first = _issue(
        id="a",
        identifier="A",
        blocked_by=(BlockerRef(id="b", identifier="B", state="Todo"),),
        request="REQ-CYCLE",
    )
    second = _issue(
        id="b",
        identifier="B",
        blocked_by=(BlockerRef(id="a", identifier="A", state="Todo"),),
        request="REQ-CYCLE",
    )
    payload = webapi._request_group_schedule_payload(
        key=RequestGroupKey("request", "REQ-CYCLE"),
        members=[first, second],
        all_issues=[first, second],
        schedule={
            "available": True,
            "entries": [
                {
                    "identifier": "A",
                    "status": "ready",
                    "code": "ready",
                    "reason": "ready",
                    "evaluated_state": "In Progress",
                }
            ],
        },
        orchestrator=SimpleNamespace(dependency_state_resolved=lambda _state: False),  # type: ignore[arg-type]
        cfg=SimpleNamespace(tracker=SimpleNamespace(terminal_states=("Done",))),  # type: ignore[arg-type]
    )
    assert payload["execution_valid"] is False
    assert payload["summary"]["longest_unresolved_chain_nodes"] is None
    assert payload["decision_drifted"] is True
    assert "dependency_cycle" in payload["warnings"]


def test_web_schedule_ignores_resolved_issue_with_empty_identifier() -> None:
    malformed = _issue(id="ghost-id", identifier="", priority=None, labels=())
    dependent = _issue(
        id="dependent",
        identifier="DEPENDENT",
        blocked_by=(BlockerRef(id="ghost-id", identifier=None, state=None),),
    )
    payload = webapi._request_group_schedule_payload(
        key=RequestGroupKey("ticket", "DEPENDENT"),
        members=[dependent],
        all_issues=[dependent, malformed],
        schedule={"available": False, "entries": []},
        orchestrator=cast(
            Any, SimpleNamespace(dependency_state_resolved=lambda _state: False)
        ),
        cfg=cast(
            Any, SimpleNamespace(tracker=SimpleNamespace(terminal_states=("Done",)))
        ),
    )
    assert payload["edges"] == []


def test_web_live_index_and_project_urls_accept_runtime_status_shapes(
    tmp_path: Path,
) -> None:
    orchestrator = SimpleNamespace(
        snapshot=lambda: {
            "running": [{"issue_identifier": "RUN-1", "turn": 2}],
            "retrying": [{"identifier": "RETRY-1", "attempt": 3}],
        }
    )
    assert webapi._live_by_identifier(orchestrator) == {  # type: ignore[arg-type]
        "RUN-1": {"status": "running", "issue_identifier": "RUN-1", "turn": 2},
        "RETRY-1": {"status": "retrying", "identifier": "RETRY-1", "attempt": 3},
    }
    project = _project(tmp_path, host="::1", port=8123)
    assert webapi._status_is_running(True) is True
    assert webapi._status_is_running({"running": 1}) is True
    assert webapi._status_is_running({"state": "RUNNING"}) is True
    assert webapi._project_url(project) == "http://[::1]:8123/"


@pytest.mark.parametrize("value", [True, "1", -1])
def test_web_chat_budget_rejects_non_integer_or_negative_values(value: Any) -> None:
    with pytest.raises(WorkflowMutationError, match="non-negative integer"):
        webapi._check_budget(value, "max_turns")


@pytest.mark.parametrize(
    "body",
    [
        {"unknown": True},
        {"enabled": "yes"},
        {"interval_ms": True},
        {"max_turns": "many"},
        {},
    ],
)
def test_continuous_improvement_settings_reject_ambiguous_edits(
    body: dict[str, Any],
) -> None:
    with pytest.raises(WorkflowMutationError):
        webapi._parse_ci_settings(body)


def test_continuous_improvement_settings_preserve_explicit_values() -> None:
    assert webapi._parse_ci_settings(
        {
            "enabled": False,
            "interval_ms": 60_000,
            "max_turns": 2,
            "agent_kind": "codex",
            "modes": [],
        }
    ) == {
        "enabled": False,
        "interval_ms": 60_000,
        "max_turns": 2,
        "agent_kind": "codex",
        "modes": [],
    }


def test_state_spec_payload_requires_a_list_of_named_states() -> None:
    with pytest.raises(WorkflowMutationError, match="states.*list"):
        webapi._parse_state_specs({})
    with pytest.raises(WorkflowMutationError, match="each state"):
        webapi._parse_state_specs({"states": ["Todo"]})
    with pytest.raises(WorkflowMutationError, match="state is required"):
        webapi._check_state(
            cast(
                Any,
                SimpleNamespace(
                    tracker=SimpleNamespace(active_states=(), terminal_states=())
                ),
            ),
            "",
        )


@pytest.mark.asyncio
async def test_web_project_routes_cover_create_current_and_open_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text("workflow", encoding="utf-8")
    record = _project(tmp_path)
    cfg = SimpleNamespace(
        workflow_path=workflow,
        tracker=SimpleNamespace(board_root=tmp_path / "kanban"),
    )

    class Registry:
        def __init__(self) -> None:
            self.running = False
            self.unexpected = False

        def list(self) -> list[Project]:
            return [record]

        def status(self, _project_id: str) -> Any:
            if self.unexpected:
                raise OSError("probe exploded")
            return SimpleNamespace(state="running" if self.running else "stopped")

        def get(self, project_id: str) -> Project:
            if project_id != record.id:
                raise ProjectError(f"unknown project {project_id!r}")
            return record

        def start(self, _project_id: str) -> int:
            return 0

    registry = Registry()
    monkeypatch.setattr(webapi, "ProjectRegistry", lambda: registry)
    ctx = SimpleNamespace(config=lambda: cfg)
    app = web.Application()
    app[webapi.BIND_HOST_KEY] = "127.0.0.1"
    webapi._register_project_routes(app, ctx)  # type: ignore[arg-type]
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        current = await client.get("/api/v1/projects/current")
        assert current.status == 200
        assert (await current.json())["current"]["registered"] is True

        invalid_path = await client.post("/api/v1/projects", json={"name": "Demo"})
        assert invalid_path.status == 400
        assert (await invalid_path.json())["error"]["code"] == "invalid_project_path"

        monkeypatch.setattr(
            webapi,
            "_create_or_adopt_registered_project",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ProjectError("unsafe setup")
            ),
        )
        setup_failed = await client.post(
            "/api/v1/projects", json={"name": "Demo", "path": str(tmp_path)}
        )
        assert setup_failed.status == 409
        assert (await setup_failed.json())["error"]["code"] == "project_setup_failed"

        monkeypatch.setattr(
            webapi,
            "_create_or_adopt_registered_project",
            lambda *_args, **_kwargs: record,
        )
        created = await client.post(
            "/api/v1/projects", json={"name": "Demo", "path": str(tmp_path)}
        )
        assert created.status == 201
        assert (await created.json())["project"]["id"] == "demo"

        did_not_start = await client.post("/api/v1/projects/demo/open", json={})
        assert did_not_start.status == 409
        assert (
            "did not report running" in (await did_not_start.json())["error"]["message"]
        )

        registry.unexpected = True
        unexpected = await client.post("/api/v1/projects/demo/open", json={})
        assert unexpected.status == 409
        assert (await unexpected.json())["error"]["message"] == "could not open project"
    finally:
        await client.close()

    denied_app = web.Application()
    denied_app[webapi.BIND_HOST_KEY] = "0.0.0.0"
    webapi._register_project_routes(denied_app, ctx)  # type: ignore[arg-type]
    denied_client = TestClient(TestServer(denied_app))
    await denied_client.start_server()
    try:
        denied = await denied_client.post("/api/v1/projects/demo/open", json={})
        assert denied.status == 403
    finally:
        await denied_client.close()


def test_web_project_mutation_boundary_rejects_remote_and_oversized_requests() -> None:
    def request(
        *, bind: str = "127.0.0.1", remote: str = "127.0.0.1", length: int = 0
    ) -> Any:
        return cast(
            Any,
            SimpleNamespace(
                app={webapi.BIND_HOST_KEY: bind},
                remote=remote,
                headers={},
                content_length=length,
                scheme="http",
                host="127.0.0.1",
            ),
        )

    responses = [
        webapi._project_mutation_error(request(bind="0.0.0.0")),
        webapi._project_mutation_error(request(remote="203.0.113.1")),
        webapi._project_mutation_error(request(remote="invalid")),
        webapi._project_mutation_error(request(length=16_385)),
    ]
    assert all(response is not None for response in responses)
    assert [
        response.status if response is not None else None for response in responses
    ] == [
        403,
        403,
        403,
        413,
    ]


def test_web_project_creation_adapter_uses_shared_guarded_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ProjectRegistry(tmp_path / "projects-adapter.json")
    monkeypatch.setattr(projects, "source_checkout", lambda: tmp_path / "source")
    monkeypatch.setattr(
        projects,
        "create_or_adopt_project",
        lambda target, **kwargs: _project(Path(target), name=kwargs["name"]),
    )
    result = webapi._create_or_adopt_registered_project(
        registry, name="Adapter", path=tmp_path / "adapter"
    )
    assert result.name == "Adapter"


@pytest.mark.asyncio
async def test_web_project_routes_surface_registry_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Registry:
        def list(self) -> list[Project]:
            raise ProjectError("registry corrupt")

    monkeypatch.setattr(webapi, "ProjectRegistry", Registry)
    app = web.Application()
    webapi._register_project_routes(
        app, cast(Any, SimpleNamespace(config=lambda: None))
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        listing = await client.get("/api/v1/projects")
        assert listing.status == 409
        current = await client.get("/api/v1/projects/current")
        assert current.status == 409
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_git_routes_map_repository_remote_and_operation_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        agent=SimpleNamespace(
            auto_merge_target_branch="",
            auto_merge_on_done=False,
            auto_merge_push_target=False,
            auto_merge_exclude_paths=(),
            auto_merge_capture_untracked=False,
        ),
        tracker=SimpleNamespace(
            kind="file", active_states=("Todo",), terminal_states=("Done",)
        ),
    )

    class Tracker:
        return_issue = False

        def fetch_issues_by_states(self, _states: Any) -> Any:
            raise RuntimeError("ticket lookup failed")

        def fetch_issue_full_by_id(self, _identifier: str) -> Any:
            if self.return_issue:
                return _issue(identifier="TASK-1")
            raise RuntimeError("ticket lookup failed")

        def append_note(self, *_args: Any) -> None:
            raise RuntimeError("note failed")

    ctx = cast(
        Any,
        SimpleNamespace(
            config=lambda: cfg,
            workflow_dir=lambda: tmp_path,
            file_tracker=lambda: Tracker(),
        ),
    )
    orchestrator = cast(
        Any,
        SimpleNamespace(
            find_running_issue_id=lambda _identifier: None,
            request_refresh=lambda: None,
        ),
    )
    monkeypatch.setattr(webapi.git_inspect, "is_git_repo", lambda _path: False)
    monkeypatch.setattr(webapi.git_inspect, "current_branch", lambda _path: None)
    monkeypatch.setattr(webapi.git_ops, "gh_available", lambda: True)
    app = web.Application()
    webapi._register_git_routes(app, ctx, orchestrator)
    client = TestClient(TestServer(app))
    await client.start_server()
    task_branch = "symphony/TASK-1"
    try:
        assert (await client.get("/api/v1/git/remote-status")).status == 200
        assert (
            await client.get(f"/api/v1/git/compare?branch={task_branch}")
        ).status == 400
        assert (await client.get("/api/v1/git/diff?commit=" + "a" * 40)).status == 400
        assert (
            await client.get(f"/api/v1/git/diff?branch={task_branch}")
        ).status == 400
        deleted = await client.post(
            "/api/v1/git/branch/delete", json={"branch": task_branch}
        )
        assert deleted.status == 409

        monkeypatch.setattr(webapi.git_inspect, "is_git_repo", lambda _path: True)
        monkeypatch.setattr(
            webapi.git_inspect,
            "list_task_branches",
            lambda *_args: [{"identifier": "TASK-1"}],
        )
        branches = await client.get("/api/v1/git/task-branches")
        assert branches.status == 200

        no_target = await client.get(f"/api/v1/git/compare?branch={task_branch}")
        assert no_target.status == 400
        no_diff_target = await client.get(f"/api/v1/git/diff?branch={task_branch}")
        assert no_diff_target.status == 400
        merge_no_target = await client.post(
            "/api/v1/git/merge", json={"branch": task_branch}
        )
        assert merge_no_target.status == 400

        monkeypatch.setattr(webapi.git_ops, "is_valid_remote_name", lambda _name: False)
        invalid_remote = await client.post(
            "/api/v1/git/push", json={"branch": task_branch, "remote": "bad!"}
        )
        assert invalid_remote.status == 400

        monkeypatch.setattr(webapi.git_ops, "is_valid_remote_name", lambda _name: True)
        monkeypatch.setattr(webapi.git_ops, "list_remotes", lambda _path: ["origin"])
        monkeypatch.setattr(webapi.git_inspect, "ref_exists", lambda *_args: False)
        unknown = await client.post(
            "/api/v1/git/push", json={"branch": task_branch, "remote": "origin"}
        )
        assert unknown.status == 400

        monkeypatch.setattr(webapi.git_inspect, "ref_exists", lambda *_args: True)
        failure = webapi.GitOpResult(False, "push_failed", "rejected")
        monkeypatch.setattr(webapi.git_ops, "push_branch", lambda *_args: failure)
        rejected = await client.post(
            "/api/v1/git/push", json={"branch": task_branch, "remote": "origin"}
        )
        assert rejected.status == 409

        pr_no_target = await client.post("/api/v1/git/pr", json={"branch": task_branch})
        assert pr_no_target.status == 400
        cfg.agent.auto_merge_target_branch = "main"
        monkeypatch.setattr(webapi.git_ops, "list_remotes", lambda _path: [])
        pr_no_remote = await client.post(
            "/api/v1/git/pr", json={"branch": task_branch, "remote": "origin"}
        )
        assert pr_no_remote.status == 400

        monkeypatch.setattr(webapi.git_ops, "list_remotes", lambda _path: ["origin"])
        monkeypatch.setattr(webapi.git_ops, "branch_on_remote", lambda *_args: True)
        monkeypatch.setattr(
            webapi.git_ops, "create_pull_request", lambda *_args: failure
        )
        pr_failed = await client.post(
            "/api/v1/git/pr", json={"branch": task_branch, "remote": "origin"}
        )
        assert pr_failed.status == 409

        merge_result = SimpleNamespace(ok=True, status="merged", detail="done")
        monkeypatch.setattr(
            webapi,
            "auto_merge_on_done_best_effort",
            lambda **_kwargs: asyncio.sleep(0, result=merge_result),
        )
        merge_lookup_failed = await client.post(
            "/api/v1/git/merge", json={"branch": task_branch}
        )
        assert merge_lookup_failed.status == 200
        Tracker.return_issue = True
        merge_note_failed = await client.post(
            "/api/v1/git/merge", json={"branch": task_branch}
        )
        assert merge_note_failed.status == 200
        assert (await merge_note_failed.json())["ticket_note_appended"] is False

        monkeypatch.setattr(webapi.git_inspect, "current_branch", lambda _path: "main")
        monkeypatch.setattr(webapi.git_inspect, "is_merged", lambda *_args: True)
        monkeypatch.setattr(
            webapi.git_ops, "delete_branch", lambda *_args, **_kwargs: failure
        )
        delete_failed = await client.post(
            "/api/v1/git/branch/delete", json={"branch": task_branch}
        )
        assert delete_failed.status == 409
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_workflow_routes_guard_live_states_and_invalid_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        tracker=SimpleNamespace(active_states=("Todo",), terminal_states=("Done",)),
    )
    issue = _issue(identifier="MOVE-1", state="Old")

    class Tracker:
        def fetch_issues_by_states(self, _states: Any) -> list[Issue]:
            return [issue]

        def transition(self, *_args: Any) -> None:
            return None

    class WorkflowState:
        fail_reload = False

        def reload(self) -> Any:
            return (None, "invalid") if self.fail_reload else (cfg, None)

        def current(self) -> Any:
            return cfg

    workflow_state = WorkflowState()

    class Orchestrator:
        running: list[Issue] = [_issue(identifier="RUN-1", state="Todo")]
        running_identifier: str | None = None

        def iter_running_issues(self) -> list[Issue]:
            return self.running

        def find_running_issue_id(self, _identifier: str) -> str | None:
            return self.running_identifier

        def request_refresh(self) -> None:
            return None

        def reset_continuous_improvement_turns(self) -> None:
            return None

        def continuous_improvement_status(self) -> dict[str, Any]:
            return {"running": False}

    orchestrator = Orchestrator()
    orchestrator.workflow_state = workflow_state  # type: ignore[attr-defined]
    ctx = cast(
        Any,
        SimpleNamespace(config=lambda: cfg, file_tracker=lambda: Tracker()),
    )
    monkeypatch.setattr(
        webapi,
        "apply_states_update",
        lambda *_args: SimpleNamespace(
            renamed={"Old": "New"}, removed=[], added=[], fallback_state="Todo"
        ),
    )
    monkeypatch.setattr(
        webapi,
        "apply_lane_preset",
        lambda *_args: SimpleNamespace(
            renamed={}, removed=["Old"], added=[], fallback_state="Todo"
        ),
    )
    monkeypatch.setattr(
        webapi, "set_continuous_improvement_settings", lambda *_args, **_kwargs: None
    )
    app = web.Application()
    webapi._register_workflow_routes(app, ctx, cast(Any, orchestrator))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        in_use = await client.put(
            "/api/v1/workflow/states", json={"states": [{"name": "Done"}]}
        )
        assert in_use.status == 409

        orchestrator.running = []
        orchestrator.running_identifier = "running"
        migrated = await client.put(
            "/api/v1/workflow/states",
            json={"states": [{"name": "Todo"}, {"name": "Done", "terminal": True}]},
        )
        assert migrated.status == 200
        assert (await migrated.json())["skipped_running"] == ["MOVE-1"]

        bad_prompt = await client.put(
            "/api/v1/workflow/prompts/Todo", json={"content": 3}
        )
        assert bad_prompt.status == 400
        bad_branch = await client.put(
            "/api/v1/workflow/branch-policy", json={"feature_base_branch": 3}
        )
        assert bad_branch.status == 400

        preset = await client.post(
            "/api/v1/workflow/presets/apply", json={"name": "default"}
        )
        assert preset.status == 200
        assert (await preset.json())["skipped_running"] == ["MOVE-1"]

        workflow_state.fail_reload = True
        ci = await client.put(
            "/api/v1/workflow/continuous-improvement", json={"enabled": False}
        )
        assert ci.status == 400
        reset = await client.post(
            "/api/v1/workflow/continuous-improvement/reset-turns", json={}
        )
        assert reset.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_preview_routes_use_authoritative_release_gate_and_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = SimpleNamespace(
        preview=SimpleNamespace(
            release_ticket="REL-1",
            enabled=True,
            command="serve",
            acceptance=("browser",),
        ),
        tracker=SimpleNamespace(kind="linear"),
    )
    issue = _issue(identifier="REL-1", state="Done", title="Release")
    calls: list[str] = []

    class Manager:
        async def status(self, _cfg: Any) -> dict[str, Any]:
            return {"phase": "stopped", "running": False}

        async def start(self, _cfg: Any) -> None:
            calls.append("start")

        async def restart(self, _cfg: Any) -> None:
            calls.append("restart")

        async def stop(self, _cfg: Any = None) -> None:
            calls.append("stop")

        async def close(self) -> None:
            calls.append("close")

    class TrackerContext:
        def __enter__(self) -> Any:
            return SimpleNamespace(fetch_issue_full_by_id=lambda _ticket: issue)

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(webapi, "ProductPreviewManager", Manager)
    monkeypatch.setattr(
        webapi, "tracker_context_manager", lambda _cfg: TrackerContext()
    )
    ctx = SimpleNamespace(config=lambda: cfg)
    orchestrator = SimpleNamespace(
        issue_snapshot=lambda _ticket: {"state": "Todo", "title": "Release"}
    )
    app = web.Application()
    app[webapi.BIND_HOST_KEY] = "127.0.0.1"
    webapi._register_preview_routes(app, ctx, orchestrator)  # type: ignore[arg-type]
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        status = await client.get("/api/v1/preview")
        assert (await status.json())["release_gate"]["ready"] is False
        for action in ("start", "restart", "stop"):
            response = await client.post(f"/api/v1/preview/{action}", json={})
            assert response.status == 200
        assert calls[:3] == ["start", "restart", "stop"]
        invalid_body = await client.post("/api/v1/preview/stop", json={"command": "x"})
        assert invalid_body.status == 400
    finally:
        await client.close()
    assert calls[-1] == "close"

    denied_app = web.Application()
    denied_app[webapi.BIND_HOST_KEY] = "0.0.0.0"
    webapi._register_preview_routes(denied_app, ctx, orchestrator)  # type: ignore[arg-type]
    denied_client = TestClient(TestServer(denied_app))
    await denied_client.start_server()
    try:
        denied = await denied_client.post("/api/v1/preview/stop", json={})
        assert denied.status == 403
    finally:
        await denied_client.close()


@pytest.mark.asyncio
async def test_web_chat_routes_map_session_errors_without_backend_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "20260824-120000-abcdef"
    action_id = "project-" + "a" * 32

    class Manager:
        live_count = 0

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def start_session(self, *_args: Any, **_kwargs: Any) -> Any:
            raise chat.ChatSessionExistsError("limit")

        async def set_mode(self, *_args: Any, **_kwargs: Any) -> Any:
            raise chat.ChatBusyError("busy")

        async def stop_session(self, *_args: Any, **_kwargs: Any) -> None:
            raise chat.ChatNoSessionError("missing")

        async def send_message(self, *_args: Any, **_kwargs: Any) -> Any:
            if _args and _args[0] == "missing":
                raise chat.ChatNoSessionError("missing")
            raise chat.ChatBackendUnavailableError("backend unavailable")

        async def reattach(self, *_args: Any, **_kwargs: Any) -> Any:
            raise chat.ChatSessionExistsError("limit")

        async def confirm_project_setup(self, *_args: Any, **_kwargs: Any) -> Any:
            if _args and str(_args[0]).endswith("b" * 32):
                raise chat.ChatProjectActionError("unknown project setup action")
            raise chat.ChatNoSessionError("missing")

        def project_setup_for_choice(self, *_args: Any) -> None:
            return None

        def snapshot(self, *_args: Any) -> dict[str, Any]:
            return {"active": False}

        def list_sessions(self) -> dict[str, Any]:
            return {"sessions": []}

        def subscribe(self, *_args: Any) -> asyncio.Queue[Any]:
            return asyncio.Queue()

        def unsubscribe(self, *_args: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(webapi, "ChatManager", Manager)
    app = web.Application()
    app[webapi.BIND_HOST_KEY] = "127.0.0.1"
    webapi._register_chat_routes(
        app,
        cast(Any, SimpleNamespace(config=lambda: None)),
        cast(Any, SimpleNamespace(request_refresh=lambda: None)),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        invalid_start = await client.post("/api/v1/chat/sessions", json={"mode": 3})
        assert invalid_start.status == 400
        limited = await client.post("/api/v1/chat/sessions", json={"mode": "qa"})
        assert limited.status == 409
        invalid_mode = await client.patch("/api/v1/chat/session", json={})
        assert invalid_mode.status == 400
        busy = await client.patch("/api/v1/chat/session", json={"mode": "edit"})
        assert busy.status == 409
        too_long = await client.post(
            "/api/v1/chat/message", json={"text": "x" * (webapi._MAX_CHAT_MESSAGE + 1)}
        )
        assert too_long.status == 400
        backend = await client.post("/api/v1/chat/message", json={"text": "hello"})
        assert backend.status == 409
        missing_message = await client.post(
            "/api/v1/chat/message", json={"text": "missing"}
        )
        assert missing_message.status == 404
        extra = await client.post(
            f"/api/v1/chat/sessions/{session_id}/reattach", json={"extra": True}
        )
        assert extra.status == 400
        reattach = await client.post(
            f"/api/v1/chat/sessions/{session_id}/reattach", json={}
        )
        assert reattach.status == 409
        selection = await client.post(
            f"/api/v1/chat/sessions/{session_id}/project-setup/{action_id}/select",
            json={},
        )
        assert selection.status == 404
        nonempty_selection = await client.post(
            f"/api/v1/chat/sessions/{session_id}/project-setup/{action_id}/select",
            json={"unexpected": True},
        )
        assert nonempty_selection.status == 400
        unknown_action = "project-" + "b" * 32
        unknown = await client.post(
            f"/api/v1/chat/sessions/{session_id}/project-setup/{unknown_action}/select",
            json={},
        )
        assert unknown.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_chat_websocket_ignores_malformed_focus_and_honors_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        queue: asyncio.Queue[dict[str, Any] | None] | None = None

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def subscribe(self, _focus: Any = None) -> asyncio.Queue[dict[str, Any] | None]:
            self.queue = asyncio.Queue()
            return self.queue

        def unsubscribe(self, _queue: Any) -> None:
            return None

        def set_focus(self, _queue: Any, _session_id: Any) -> None:
            return None

        def snapshot(self, *_args: Any) -> dict[str, Any]:
            return {"active": False}

        def list_sessions(self) -> dict[str, Any]:
            return {"sessions": []}

        async def close(self) -> None:
            return None

    monkeypatch.setattr(webapi, "ChatManager", Manager)
    app = web.Application()
    app[webapi.BIND_HOST_KEY] = "127.0.0.1"
    webapi._register_chat_routes(
        app,
        cast(Any, SimpleNamespace(config=lambda: None)),
        cast(Any, SimpleNamespace(request_refresh=lambda: None)),
    )
    manager = cast(Any, app[webapi.CHAT_MANAGER_KEY])
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/api/v1/chat/ws")
        hello = await ws.receive_json()
        assert hello["type"] == "hello"
        await ws.send_bytes(b"binary")
        await asyncio.sleep(0.02)
        await ws.send_str("not-json")
        await asyncio.sleep(0.02)
        await ws.send_json({"type": "other"})
        await asyncio.sleep(0.02)
        await ws.send_json({"type": "focus", "session_id": "invalid"})
        await asyncio.sleep(0.02)
        assert manager.queue is not None
        await manager.queue.put(None)
        await ws.receive()
        assert ws.closed
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_chat_websocket_transport_failure_isolated_from_inbound_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, manager = _websocket_boundary_app(monkeypatch)
    original_send_json = web.WebSocketResponse.send_json
    failed_send = asyncio.Event()

    async def send_json(
        response: web.WebSocketResponse, data: Any, *args: Any, **kwargs: Any
    ) -> None:
        if isinstance(data, dict) and data.get("type") == "transport_failure":
            failed_send.set()
            raise ConnectionResetError("simulated disconnected peer")
        await original_send_json(response, data, *args, **kwargs)

    monkeypatch.setattr(web.WebSocketResponse, "send_json", send_json)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/api/v1/chat/ws")
        assert (await ws.receive_json())["type"] == "hello"

        manager.emit({"type": "transport_failure"})
        await asyncio.wait_for(failed_send.wait(), timeout=2)
        await ws.send_json({"type": "focus", "session_id": None})
        await asyncio.wait_for(manager.focused.wait(), timeout=2)

        assert manager.focus is None
        await ws.close()
        await asyncio.wait_for(manager.unsubscribed.wait(), timeout=2)
        assert ws.closed
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_web_chat_shutdown_closes_live_websocket_before_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, manager = _websocket_boundary_app(monkeypatch)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    shutdown: asyncio.Task[None] | None = None
    try:
        ws = await client.ws_connect("/api/v1/chat/ws")
        assert (await ws.receive_json())["type"] == "hello"

        shutdown = asyncio.create_task(server.close())
        message = await asyncio.wait_for(ws.receive(), timeout=2)
        assert message.type is WSMsgType.CLOSE
        assert message.data == WSCloseCode.GOING_AWAY
        await ws.close()
        await asyncio.wait_for(shutdown, timeout=2)

        assert server.closed
        assert manager.closed.is_set()
        assert manager.unsubscribed.is_set()
    finally:
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
            await asyncio.gather(shutdown, return_exceptions=True)
        await client.close()


@pytest.mark.asyncio
async def test_web_chat_shutdown_continues_after_socket_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, manager = _websocket_boundary_app(monkeypatch)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    shutdown: asyncio.Task[None] | None = None
    try:
        ws = await client.ws_connect("/api/v1/chat/ws")
        assert (await ws.receive_json())["type"] == "hello"
        original_close = web.WebSocketResponse.close
        failed_close = asyncio.Event()

        async def close(
            response: web.WebSocketResponse, *args: Any, **kwargs: Any
        ) -> bool:
            if (
                kwargs.get("code") == WSCloseCode.GOING_AWAY
                and not failed_close.is_set()
            ):
                failed_close.set()
                raise RuntimeError("simulated websocket close failure")
            return await original_close(response, *args, **kwargs)

        monkeypatch.setattr(web.WebSocketResponse, "close", close)
        shutdown = asyncio.create_task(server.close())
        await asyncio.wait_for(failed_close.wait(), timeout=2)
        message = await asyncio.wait_for(ws.receive(), timeout=2)
        assert message.type is WSMsgType.CLOSE
        assert message.data == WSCloseCode.GOING_AWAY
        await ws.close()
        await asyncio.wait_for(shutdown, timeout=2)

        assert server.closed
        assert manager.closed.is_set()
        assert manager.unsubscribed.is_set()
    finally:
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
            await asyncio.gather(shutdown, return_exceptions=True)
        await client.close()


@pytest.mark.asyncio
async def test_web_meta_route_reports_missing_static_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webapi, "STATIC_DIR", tmp_path / "missing-static")
    app = web.Application()
    webapi._register_meta_routes(
        app,
        SimpleNamespace(config=lambda: None),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/")
        assert response.status == 503
        assert "assets missing" in await response.text()
    finally:
        await client.close()
