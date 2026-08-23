"""Coverage contracts for multi-project hub surfaces."""
# ruff: noqa: F405

from tests.coverage_cases._surfaces_support import *  # noqa: F403


@pytest.mark.parametrize(
    ("status", "running"),
    [
        (True, True),
        ({"running": 1}, True),
        ({"state": "RUNNING"}, True),
        (SimpleNamespace(running=False), False),
        (SimpleNamespace(state="running"), True),
    ],
)
def test_hub_recognizes_registry_status_shapes(status: Any, running: bool) -> None:
    assert hub._status_running(status) is running


def test_hub_service_url_normalizes_wildcard_and_ipv6_hosts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert hub._service_url(project, {"url": "http://preview.local/"}) == (
        "http://preview.local/"
    )
    assert (
        hub._service_url(
            project, {"running": True, "record": {"host": "::1", "port": 8123}}
        )
        == "http://[::1]:8123/"
    )
    assert (
        hub._service_url(project, SimpleNamespace(host="0.0.0.0", port=8124))
        == "http://127.0.0.1:8124/"
    )


def test_hub_project_lookup_and_default_creation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _project(tmp_path)
    with pytest.raises(KeyError):
        hub._project_by_id([record], "missing")
    monkeypatch.setattr(projects, "source_checkout", lambda: tmp_path / "source")
    monkeypatch.setattr(
        projects,
        "create_or_adopt_project",
        lambda **kwargs: _project(Path(kwargs["target"]), name=kwargs["name"]),
    )
    created = hub._default_create_project(
        cast(Any, SimpleNamespace()),
        {"name": "Created", "path": str(tmp_path / "created")},
    )
    assert created.name == "Created"


@pytest.mark.asyncio
async def test_hub_invokes_sync_async_and_sync_returning_awaitable() -> None:
    async def direct(value: int) -> int:
        return value + 1

    def deferred(value: int) -> Any:
        async def result() -> int:
            return value + 2

        return result()

    assert await hub._invoke(direct, 1) == 2
    assert await hub._invoke(deferred, 1) == 3


@pytest.mark.asyncio
async def test_hub_non_loopback_bind_forbids_mutation(tmp_path: Path) -> None:
    registry = _HubRegistry(_project(tmp_path))
    app = hub.build_hub_app(registry)
    app[hub._HUB_BIND_HOST] = "0.0.0.0"
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/v1/projects/demo/start", json={})
        assert response.status == 403
        assert (await response.json())["error"]["code"] == "forbidden_bind"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_hub_rejects_mutation_when_peer_identity_is_invalid() -> None:
    request = cast(
        Any,
        SimpleNamespace(
            app={hub._HUB_BIND_HOST: "127.0.0.1"},
            host="127.0.0.1",
            method="POST",
            transport=SimpleNamespace(
                get_extra_info=lambda _key: ("invalid-peer", 1234)
            ),
            headers={},
            scheme="http",
            content_type="application/json",
        ),
    )
    response = await hub._hub_guard(request, lambda _request: None)
    assert response.status == 403
    assert isinstance(response.text, str)
    assert json.loads(response.text)["error"]["code"] == "forbidden_peer"


@pytest.mark.asyncio
async def test_hub_listing_degrades_invalid_service_record_url(tmp_path: Path) -> None:
    registry = _HubRegistry(_project(tmp_path))
    registry.status = lambda _project_id: {  # type: ignore[method-assign]
        "running": True,
        "record": {"host": "127.0.0.1", "port": "invalid"},
    }
    client = TestClient(TestServer(hub.build_hub_app(registry)))
    await client.start_server()
    try:
        response = await client.get("/api/v1/projects")
        project = (await response.json())["projects"][0]
        assert project["url"] is None
        assert any(
            diagnostic.startswith("Service URL unavailable:")
            for diagnostic in project["diagnostics"]
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_hub_open_rejects_service_that_never_reports_running(
    tmp_path: Path,
) -> None:
    registry = _HubRegistry(_project(tmp_path))
    registry.start = lambda _project_id: 0  # type: ignore[method-assign]
    client = TestClient(TestServer(hub.build_hub_app(registry)))
    await client.start_server()
    try:
        response = await client.post("/api/v1/projects/demo/open", json={})
        assert response.status == 409
        assert "did not report running" in (await response.json())["error"]["message"]
        missing = await client.post("/api/v1/projects/missing/open", json={})
        assert missing.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_hub_serve_cleans_up_after_signal_wait(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cleaned: list[bool] = []

    class Runner:
        async def cleanup(self) -> None:
            cleaned.append(True)

    class Event:
        def set(self) -> None:
            return None

        async def wait(self) -> None:
            return None

    class Loop:
        def add_signal_handler(self, *_args: Any) -> None:
            raise NotImplementedError

    async def run_hub(_app: Any, *, host: str, port: int) -> tuple[Any, int]:
        assert (host, port) == ("0.0.0.0", 0)
        return Runner(), 8123

    monkeypatch.setattr(hub, "run_hub", run_hub)
    monkeypatch.setattr(hub.asyncio, "Event", Event)
    monkeypatch.setattr(hub.asyncio, "get_running_loop", lambda: Loop())
    assert await hub._serve(SimpleNamespace(), "0.0.0.0", 0) == 0  # type: ignore[arg-type]
    assert cleaned == [True]
    assert "http://127.0.0.1:8123/" in capsys.readouterr().err


def test_hub_main_rejects_invalid_port_and_maps_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SystemExit):
        hub.main(["--port", "70000"])

    def interrupt(awaitable: Any) -> None:
        awaitable.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(hub.asyncio, "run", interrupt)
    assert hub.main([]) == 130


@pytest.mark.asyncio
async def test_hub_http_validates_create_and_reports_service_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker:\n  kind: linear\n  project_slug: DEMO\n---\nbody\n",
        encoding="utf-8",
    )
    registry = _HubRegistry(_project(tmp_path))
    registry.result = 7
    monkeypatch.setattr(
        hub,
        "canonical_project_repo",
        lambda _path: (_ for _ in ()).throw(ProjectError("repo unavailable")),
    )
    client = TestClient(
        TestServer(
            hub.build_hub_app(
                registry,
                create_project=lambda _registry, _values: (_ for _ in ()).throw(
                    ProjectError("unsafe target")
                ),
            )
        )
    )
    await client.start_server()
    try:
        invalid_json = await client.post(
            "/api/v1/projects",
            data="{",
            headers={"Content-Type": "application/json"},
        )
        assert invalid_json.status == 400
        assert (await invalid_json.json())["error"]["code"] == "invalid_json"

        for payload in (
            [],
            {"name": "Demo", "path": str(tmp_path), "extra": True},
            {"name": "Demo", "path": str(tmp_path), "id": 3},
            {"name": "x" * 201, "path": str(tmp_path)},
        ):
            response = await client.post("/api/v1/projects", json=payload)
            assert response.status == 400

        failed_setup = await client.post(
            "/api/v1/projects", json={"name": "Demo", "path": str(tmp_path)}
        )
        assert failed_setup.status == 409
        assert (await failed_setup.json())["error"]["code"] == "project_setup_failed"

        failed_start = await client.post("/api/v1/projects/demo/start", json={})
        assert failed_start.status == 409
        assert (await failed_start.json())["error"]["code"] == "project_start_failed"

        listing = await client.get("/api/v1/projects")
        entry = (await listing.json())["projects"][0]
        assert entry["board"] is None
        assert entry["repo"] == str(tmp_path.resolve())
        assert (
            entry["diagnostics"][0] == "Repository path unavailable: repo unavailable"
        )
        assert entry["diagnostics"][1].startswith("Board path unavailable:")
    finally:
        await client.close()
