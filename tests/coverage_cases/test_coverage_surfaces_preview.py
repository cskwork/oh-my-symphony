"""Coverage contracts for product preview lifecycle."""
# ruff: noqa: F405

from tests.coverage_cases._surfaces_support import *  # noqa: F403


@pytest.mark.asyncio
async def test_product_preview_restart_stops_before_starting() -> None:
    manager = ProductPreviewManager()
    events: list[str] = []

    async def stop(*, remove_checkout: bool) -> None:
        assert remove_checkout is True
        events.append("stop")

    async def start(_cfg: Any) -> None:
        events.append("start")

    async def status(_cfg: Any) -> dict[str, Any]:
        events.append("status")
        return {"phase": "healthy"}

    manager._stop_unlocked = stop  # type: ignore[method-assign]
    manager._start_unlocked = start  # type: ignore[method-assign]
    manager._status_unlocked = status  # type: ignore[method-assign]

    assert await manager.restart(object()) == {"phase": "healthy"}  # type: ignore[arg-type]
    assert events == ["stop", "start", "status"]


@pytest.mark.asyncio
async def test_product_preview_start_is_idempotent_for_live_process() -> None:
    manager = ProductPreviewManager()
    manager._process = SimpleNamespace(returncode=None, pid=123)  # type: ignore[assignment]

    async def status(_cfg: Any) -> dict[str, Any]:
        return {"running": True, "pid": 123}

    manager._status_unlocked = status  # type: ignore[method-assign]
    assert await manager.start(object()) == {"running": True, "pid": 123}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_product_preview_rejects_empty_command_before_git() -> None:
    manager = ProductPreviewManager()
    cfg = SimpleNamespace(preview=SimpleNamespace(enabled=True, command="   "))
    with pytest.raises(ProductPreviewError, match="command is empty"):
        await manager.start(cfg)  # type: ignore[arg-type]
    assert (await manager.status())["phase"] == "stopped"


@pytest.mark.asyncio
async def test_product_preview_requires_a_resolvable_target_branch(
    tmp_path: Path,
) -> None:
    manager = ProductPreviewManager()
    cfg = SimpleNamespace(
        preview=SimpleNamespace(enabled=True, command="server"),
        workflow_path=tmp_path / "WORKFLOW.md",
        agent=SimpleNamespace(auto_merge_target_branch=""),
    )
    calls: list[tuple[str, ...]] = []

    async def git(_cwd: Path, *args: str, check: bool = True) -> str:
        calls.append(args)
        return str(tmp_path) if args == ("rev-parse", "--show-toplevel") else ""

    manager._git = git  # type: ignore[method-assign]
    with pytest.raises(ProductPreviewError, match="target branch is not configured"):
        await manager.start(cfg)  # type: ignore[arg-type]
    assert calls == [
        ("rev-parse", "--show-toplevel"),
        ("branch", "--show-current"),
    ]


@pytest.mark.asyncio
async def test_product_preview_reports_exited_process_and_cleanup_failure(
    tmp_path: Path,
) -> None:
    manager = ProductPreviewManager()
    manager._process = SimpleNamespace(returncode=7, pid=321)  # type: ignore[assignment]
    manager._phase = "healthy"

    status = await manager.status()
    assert status["phase"] == "failed"
    assert status["last_error"] == "preview process exited with code 7"

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    manager._repo = tmp_path
    manager._checkout = checkout

    async def refuse_cleanup(_repo: Path, _checkout: Path) -> None:
        raise ProductPreviewError("still mounted")

    manager._remove_worktree = refuse_cleanup  # type: ignore[method-assign]
    stopped = await manager.stop()
    assert stopped["phase"] == "stopped"
    assert stopped["last_error"] == "preview checkout cleanup failed: still mounted"


@pytest.mark.asyncio
async def test_product_preview_captures_streams_and_refuses_unmanaged_checkout(
    tmp_path: Path,
) -> None:
    manager = ProductPreviewManager(max_log_lines=2)
    reader = asyncio.StreamReader()
    reader.feed_data(b"first\nsecond\nthird\n")
    reader.feed_eof()

    await manager._read_stream(None, "stdout")
    await manager._read_stream(reader, "stderr")
    assert list(manager._logs) == [
        {"stream": "stderr", "line": "second"},
        {"stream": "stderr", "line": "third"},
    ]

    checkout = tmp_path / "checkout"
    checkout.mkdir()

    async def git_noop(_cwd: Path, *_args: str, check: bool = True) -> str:
        return ""

    manager._git = git_noop  # type: ignore[method-assign]
    with pytest.raises(ProductPreviewError, match="unmanaged path"):
        await manager._remove_worktree(tmp_path, checkout)


@pytest.mark.asyncio
async def test_product_preview_git_error_is_operator_safe(tmp_path: Path) -> None:
    with pytest.raises(ProductPreviewError, match="not a git repository"):
        await ProductPreviewManager._git(tmp_path, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_product_preview_cancels_log_readers_during_cleanup() -> None:
    manager = ProductPreviewManager()
    reader = asyncio.create_task(asyncio.sleep(60))
    manager._readers = [reader]
    await manager._terminate_process()
    assert reader.cancelled()


@pytest.mark.parametrize(
    ("returncode", "expected"), [(7, "exited with code 7"), (None, "timed out")]
)
@pytest.mark.asyncio
async def test_product_preview_start_reports_process_exit_and_health_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int | None,
    expected: str,
) -> None:
    manager = ProductPreviewManager()
    checkout = tmp_path / ".symphony" / "preview" / "worktree"
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        agent=SimpleNamespace(auto_merge_target_branch="dev"),
        preview=SimpleNamespace(
            enabled=True,
            command="serve",
            cwd=".",
            url_path="/",
            health_path="/health",
            startup_timeout_ms=1_000,
        ),
    )

    async def git(_cwd: Path, *args: str, check: bool = True) -> str:
        del check
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args[:2] == ("rev-parse", "--verify"):
            return "abc123"
        if args[:2] == ("worktree", "add"):
            checkout.mkdir(parents=True)
        return ""

    async def remove(_repo: Path, _checkout: Path) -> None:
        return None

    process = SimpleNamespace(returncode=returncode, pid=123, stdout=None, stderr=None)
    manager._git = git  # type: ignore[method-assign]
    manager._remove_worktree = remove  # type: ignore[method-assign]
    monkeypatch.setattr(
        product_preview.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=process),
    )
    monkeypatch.setattr(manager, "_probe", lambda _url: asyncio.sleep(0, result=False))
    monkeypatch.setattr(
        product_preview,
        "terminate_process_tree",
        lambda _proc: asyncio.sleep(0),
    )
    if returncode is None:
        cfg.preview.startup_timeout_ms = 1

    with pytest.raises(ProductPreviewError, match=expected):
        await manager.start(cfg)  # type: ignore[arg-type]
