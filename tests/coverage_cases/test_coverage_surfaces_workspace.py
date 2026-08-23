"""Coverage contracts for workspace and Git lifecycle."""
# ruff: noqa: F405

from tests.coverage_cases._surfaces_support import *  # noqa: F403


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FileNotFoundError(), (True, None, False)),
        (PermissionError("locked"), (False, "locked", os.name == "nt")),
        (OSError("io failure"), (False, "io failure", False)),
    ],
)
def test_workspace_delete_attempt_classifies_recovery_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    expected: tuple[bool, str | None, bool],
) -> None:
    monkeypatch.setattr(
        workspace.shutil, "rmtree", lambda _path: (_ for _ in ()).throw(error)
    )
    assert workspace._try_rmtree_once(tmp_path / "workspace") == expected


def test_workspace_delete_attempt_reports_success(tmp_path: Path) -> None:
    target = tmp_path / "workspace"
    target.mkdir()
    assert workspace._try_rmtree_once(target) == (True, None, False)
    assert not target.exists()


@pytest.mark.asyncio
async def test_workspace_force_delete_retries_only_retryable_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = iter(
        [
            (False, "locked once", True),
            (True, None, False),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(workspace, "_try_rmtree_once", lambda _path: next(outcomes))

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(workspace.asyncio, "sleep", sleep)
    assert await workspace._force_rmtree(tmp_path, attempts=2) == (True, None)
    assert sleeps == [0.05]

    monkeypatch.setattr(
        workspace,
        "_try_rmtree_once",
        lambda _path: (False, "permanent", False),
    )
    assert await workspace._force_rmtree(tmp_path, attempts=3) == (False, "permanent")


def test_workspace_git_queries_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(workspace.subprocess, "run", unavailable)
    assert workspace._git_repo_root(tmp_path) is None
    assert workspace._git_query(tmp_path, "status") is None
    assert workspace._local_branch_ref(tmp_path, "dev") is None
    assert workspace._git_command(tmp_path, "status") is None

    assert workspace._local_branch_ref(tmp_path, " dev") is None
    assert workspace._local_branch_ref(tmp_path, "refs/heads/dev") is None
    assert (
        workspace._resolve_git_path(tmp_path, ".git") == (tmp_path / ".git").resolve()
    )


def test_workspace_refreshes_clean_linked_ticket_to_captured_target(
    tmp_path: Path,
) -> None:
    """Preserve reuse fast-forwards only a proven clean linked worktree."""
    host = tmp_path / "host"
    ticket = tmp_path / "ticket"
    host.mkdir()
    _git_text(host, "init", "-b", "main")
    _git_text(host, "config", "user.name", "Coverage Test")
    _git_text(host, "config", "user.email", "coverage@example.com")
    (host / "product.txt").write_text("base\n", encoding="utf-8")
    _git_text(host, "add", "product.txt")
    _git_text(host, "commit", "-m", "base")
    _git_text(host, "branch", "ticket")
    _git_text(host, "worktree", "add", str(ticket), "ticket")
    (host / "product.txt").write_text("released\n", encoding="utf-8")
    _git_text(host, "commit", "-am", "release")
    target_sha = _git_text(host, "rev-parse", "main")

    plan = workspace._linked_worktree_refresh_plan(
        path=ticket,
        workflow_dir=host,
        branch="ticket",
        merge_target="main",
    )
    assert plan == workspace._LinkedWorktreeRefreshPlan(
        branch="ticket",
        head_sha=_git_text(ticket, "rev-parse", "HEAD"),
        target_sha=target_sha,
    )
    assert (
        workspace._refresh_linked_worktree_in_place(
            path=ticket,
            workflow_dir=host,
            branch="ticket",
            merge_target="main",
            base_branch="ticket",
        )
        is True
    )
    assert _git_text(ticket, "rev-parse", "HEAD") == target_sha
    assert (
        _git_text(ticket, "config", "--worktree", "--get", "symphony.basesha")
        == target_sha
    )
    assert (ticket / "product.txt").read_text(encoding="utf-8") == "released\n"


def test_workspace_refresh_refuses_ineligible_or_raced_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        workspace, "_linked_worktree_refresh_plan", lambda **_kwargs: None
    )
    assert (
        workspace._refresh_linked_worktree_in_place(
            path=tmp_path,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
            base_branch="ticket",
        )
        is False
    )

    first = workspace._LinkedWorktreeRefreshPlan("ticket", "a", "b")
    plans = iter((first, workspace._LinkedWorktreeRefreshPlan("ticket", "a", "c")))
    monkeypatch.setattr(
        workspace, "_linked_worktree_refresh_plan", lambda **_kwargs: next(plans)
    )
    assert (
        workspace._refresh_linked_worktree_in_place(
            path=tmp_path,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
            base_branch="ticket",
        )
        is False
    )


@pytest.mark.parametrize("result", [None, subprocess.CompletedProcess([], 1)])
def test_workspace_refresh_reports_guarded_merge_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[Any] | None,
) -> None:
    plan = workspace._LinkedWorktreeRefreshPlan("ticket", "a", "b")
    monkeypatch.setattr(
        workspace, "_linked_worktree_refresh_plan", lambda **_kwargs: plan
    )
    monkeypatch.setattr(workspace, "_git_command", lambda *_args: result)
    with pytest.raises(chat.SymphonyError, match="guarded fast-forward"):
        workspace._refresh_linked_worktree_in_place(
            path=tmp_path,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
            base_branch="ticket",
        )


def test_workspace_refresh_verifies_post_merge_head_before_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = workspace._LinkedWorktreeRefreshPlan("ticket", "a", "b")
    monkeypatch.setattr(
        workspace, "_linked_worktree_refresh_plan", lambda **_kwargs: plan
    )
    monkeypatch.setattr(
        workspace,
        "_git_command",
        lambda *_args: subprocess.CompletedProcess([], 0),
    )
    monkeypatch.setattr(workspace, "_git_query", lambda *_args: "wrong")
    with pytest.raises(chat.SymphonyError, match="postcondition failed"):
        workspace._refresh_linked_worktree_in_place(
            path=tmp_path,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
            base_branch="ticket",
        )


@pytest.mark.parametrize(
    ("override", "value"),
    [
        (("rev-parse", "--show-toplevel"), None),
        (("rev-parse", "--show-toplevel"), "C:/definitely/not/the/ticket\n"),
        (("rev-parse", "--git-common-dir"), None),
        (("symbolic-ref", "--quiet", "--short", "HEAD"), "other\n"),
        (("status", "--porcelain=v1", "--untracked-files=all"), " M dirty\n"),
        (("rev-parse", "--verify", "--quiet", "HEAD^{commit}"), None),
        (
            ("rev-parse", "--verify", "--quiet", "refs/heads/ticket^{commit}"),
            "different\n",
        ),
        (
            ("rev-parse", "--verify", "--quiet", "refs/heads/main^{commit}"),
            "a\n",
        ),
    ],
)
def test_workspace_refresh_plan_rejects_unproven_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: tuple[str, ...],
    value: str | None,
) -> None:
    ticket = tmp_path / "ticket"
    ticket.mkdir()
    (ticket / ".git").write_text("gitdir", encoding="utf-8")
    common = tmp_path / "common"
    common.mkdir()

    def query(path: Path, *args: str) -> str | None:
        if args == override:
            return value
        values = {
            ("rev-parse", "--show-toplevel"): f"{ticket}\n",
            ("rev-parse", "--git-common-dir"): f"{common}\n",
            ("symbolic-ref", "--quiet", "--short", "HEAD"): "ticket\n",
            ("status", "--porcelain=v1", "--untracked-files=all"): "",
            ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"): "a\n",
            (
                "rev-parse",
                "--verify",
                "--quiet",
                "refs/heads/ticket^{commit}",
            ): "a\n",
            ("rev-parse", "--verify", "--quiet", "refs/heads/main^{commit}"): "b\n",
        }
        return values.get(args)

    monkeypatch.setattr(
        workspace, "_local_branch_ref", lambda *_args: "refs/heads/main"
    )
    monkeypatch.setattr(workspace, "_git_query", query)
    assert (
        workspace._linked_worktree_refresh_plan(
            path=ticket,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
        )
        is None
    )


@pytest.mark.parametrize(
    "merge_result", [OSError("git missing"), subprocess.CompletedProcess([], 1)]
)
def test_workspace_refresh_plan_requires_common_dir_and_ancestor_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    merge_result: OSError | subprocess.CompletedProcess[Any],
) -> None:
    ticket = tmp_path / "ticket-proof"
    ticket.mkdir()
    (ticket / ".git").write_text("gitdir", encoding="utf-8")
    common = tmp_path / "common-proof"
    common.mkdir()

    def query(path: Path, *args: str) -> str | None:
        values = {
            ("rev-parse", "--show-toplevel"): f"{ticket}\n",
            ("symbolic-ref", "--quiet", "--short", "HEAD"): "ticket\n",
            ("status", "--porcelain=v1", "--untracked-files=all"): "",
            ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"): "a\n",
            (
                "rev-parse",
                "--verify",
                "--quiet",
                "refs/heads/ticket^{commit}",
            ): "a\n",
            ("rev-parse", "--verify", "--quiet", "refs/heads/main^{commit}"): "b\n",
        }
        if args == ("rev-parse", "--git-common-dir"):
            return f"{common}\n"
        return values.get(args)

    monkeypatch.setattr(
        workspace, "_local_branch_ref", lambda *_args: "refs/heads/main"
    )
    monkeypatch.setattr(workspace, "_git_query", query)

    def run(*_args: Any, **_kwargs: Any) -> Any:
        if isinstance(merge_result, Exception):
            raise merge_result
        return merge_result

    monkeypatch.setattr(workspace.subprocess, "run", run)
    assert (
        workspace._linked_worktree_refresh_plan(
            path=ticket,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
        )
        is None
    )

    monkeypatch.setattr(workspace, "_local_branch_ref", lambda *_args: None)
    assert (
        workspace._linked_worktree_refresh_plan(
            path=ticket,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
        )
        is None
    )


@pytest.mark.parametrize("failed_call", [2, 3, 4, 5])
def test_workspace_refresh_reports_each_config_persistence_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_call: int
) -> None:
    plan = workspace._LinkedWorktreeRefreshPlan("ticket", "a", "b")
    monkeypatch.setattr(
        workspace, "_linked_worktree_refresh_plan", lambda **_kwargs: plan
    )
    calls = 0

    def command(*_args: Any) -> subprocess.CompletedProcess[Any] | None:
        nonlocal calls
        calls += 1
        return None if calls == failed_call else subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(workspace, "_git_command", command)
    monkeypatch.setattr(workspace, "_git_query", lambda *_args: "b")
    with pytest.raises(chat.SymphonyError):
        workspace._refresh_linked_worktree_in_place(
            path=tmp_path,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
            base_branch="ticket",
        )


@pytest.mark.parametrize("failure", ["basesha", "final"])
def test_workspace_refresh_verifies_persisted_base_and_final_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    plan = workspace._LinkedWorktreeRefreshPlan("ticket", "a", "b")
    monkeypatch.setattr(
        workspace, "_linked_worktree_refresh_plan", lambda **_kwargs: plan
    )
    monkeypatch.setattr(
        workspace,
        "_git_command",
        lambda *_args: subprocess.CompletedProcess([], 0),
    )
    calls = 0

    def query(*_args: Any) -> str:
        nonlocal calls
        calls += 1
        if failure == "basesha" and calls == 3:
            return "wrong"
        if failure == "final" and calls == 5:
            return "wrong"
        return "b"

    monkeypatch.setattr(workspace, "_git_query", query)
    with pytest.raises(chat.SymphonyError):
        workspace._refresh_linked_worktree_in_place(
            path=tmp_path,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
            base_branch="ticket",
        )


@pytest.mark.asyncio
async def test_workspace_manager_lifecycle_failures_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_hooks = workspace.HooksConfig(None, None, None, None, 1_000)
    manager = workspace.WorkspaceManager(tmp_path / "workspaces", no_hooks)
    occupied = manager.path_for("OCCUPIED")
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_text("not a directory", encoding="utf-8")
    with pytest.raises(chat.SymphonyError, match="occupied by non-directory"):
        await manager.create_or_reuse("OCCUPIED")
    await manager.after_run_best_effort(tmp_path / "missing")
    assert (
        await manager.after_done_best_effort(
            tmp_path / "missing", identifier="ISSUE-1", title="Missing"
        )
        is True
    )
    await manager.remove(tmp_path / "outside")
    await manager.remove(manager.path_for("MISSING"))

    hooks = workspace.HooksConfig(
        None,
        None,
        None,
        "cleanup",
        1_000,
        after_done="done",
    )
    manager = workspace.WorkspaceManager(tmp_path / "managed", hooks)
    target = manager.path_for("ISSUE-2")
    target.mkdir(parents=True)

    async def fail_hook(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("hook failed")

    monkeypatch.setattr(manager, "_run_hook", fail_hook)
    assert (
        await manager.after_done_best_effort(
            target, identifier="ISSUE-2", title="Failure"
        )
        is False
    )
    monkeypatch.setattr(
        workspace,
        "_force_rmtree",
        lambda _path: asyncio.sleep(0, result=(False, "still locked")),
    )
    await manager.remove(target)
    assert target.exists()


def test_workspace_output_normalization_strips_only_shell_chrome() -> None:
    payload = b"visible output"
    assert (
        workspace._strip_shell_exit_trailer(payload + workspace._SHELL_EXIT_TRAILER)
        == payload
    )
    assert workspace._coerce_output_bytes(None) == b""
    assert workspace._coerce_output_bytes("text") == b"text"


def test_workspace_zero_attempt_delete_and_failed_git_queries_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert asyncio.run(workspace._force_rmtree(tmp_path, attempts=0)) == (False, None)
    monkeypatch.setattr(
        workspace.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout=""),
    )
    assert workspace._git_query(tmp_path, "status") is None
    assert workspace._local_branch_ref(tmp_path, "ticket") is None


def test_workspace_owner_and_hook_artifact_io_failures_are_operator_safe(
    tmp_path: Path,
) -> None:
    hooks = workspace.HooksConfig("setup", None, None, None, 1_000)
    workflow_dir = tmp_path / "workflow"
    board = workflow_dir / "kanban"
    workflow_dir.mkdir()
    board.mkdir()
    root = tmp_path / "workspaces-io"
    manager = workspace.WorkspaceManager(
        root,
        hooks,
        workflow_dir=workflow_dir,
        board_root=board,
    )
    workspace_path = manager.path_for("IO-1")
    workspace_path.mkdir()
    manager._enforce_board_reachable(workspace_path)

    marker_dir = root / workspace._OWNER_MARKER_DIR
    marker_dir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(chat.SymphonyError, match="owner marker write failed"):
        manager._write_workspace_owner_marker("IO-1")

    artifact_dir = root / workspace._HOOK_OUTPUT_DIR
    artifact_dir.write_text("not a directory", encoding="utf-8")
    assert (
        manager._write_hook_output_artifacts(
            name="after_create",
            cwd=workspace_path,
            returncode=1,
            stdout=b"stdout",
            stderr=b"stderr",
        )
        is None
    )
    assert manager._metadata_key_for_cwd(tmp_path / "outside") == "outside"


@pytest.mark.asyncio
async def test_workspace_after_done_missing_path_with_configured_hook_is_success(
    tmp_path: Path,
) -> None:
    hooks = workspace.HooksConfig(None, None, None, None, 1_000, after_done="done-hook")
    manager = workspace.WorkspaceManager(tmp_path / "workspaces-done", hooks)
    assert (
        await manager.after_done_best_effort(
            tmp_path / "missing", identifier="DONE-1", title="Done"
        )
        is True
    )


@pytest.mark.asyncio
async def test_workspace_malformed_owner_and_stdout_only_hook_failure_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_dir = tmp_path / "workflow-owner"
    workflow_dir.mkdir()
    hooks = workspace.HooksConfig(None, None, None, None, 1_000)
    manager = workspace.WorkspaceManager(
        tmp_path / "owner-root", hooks, workflow_dir=workflow_dir
    )
    owned = manager.path_for("OWNER-1")
    owned.mkdir()
    marker = manager._owner_marker_path("OWNER-1")
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"identity": []}), encoding="utf-8")
    manager._enforce_workspace_owner("OWNER-1", owned)

    monkeypatch.setattr(
        workspace.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, stdout=b"visible stdout", stderr=b""
        ),
    )
    with pytest.raises(chat.SymphonyError, match="stdout: visible stdout"):
        await manager._run_hook("after_create", "false", owned)


@pytest.mark.asyncio
async def test_workspace_preserve_reuse_derives_target_and_base_from_host_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_dir = tmp_path / "workflow-preserve"
    workflow_dir.mkdir()
    hooks = workspace.HooksConfig("setup", None, None, None, 1_000)
    manager = workspace.WorkspaceManager(
        tmp_path / "preserved",
        hooks,
        workflow_dir=workflow_dir,
        reuse_policy="preserve",
    )
    existing = manager.path_for("PRESERVE-1")
    existing.mkdir()
    monkeypatch.setattr(workspace, "_git_query", lambda *_args: "main\n")
    refreshed: list[dict[str, Any]] = []

    def refresh(**kwargs: Any) -> bool:
        refreshed.append(kwargs)
        return True

    monkeypatch.setattr(workspace, "_refresh_linked_worktree_in_place", refresh)
    result = await manager.create_or_reuse("PRESERVE-1")
    assert result.created_now is False
    assert refreshed[0]["merge_target"] == "main"
    assert refreshed[0]["base_branch"] == "main"


@pytest.mark.asyncio
async def test_workspace_failed_create_reports_incomplete_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks = workspace.HooksConfig("setup", None, None, None, 1_000)
    manager = workspace.WorkspaceManager(tmp_path / "cleanup-failure", hooks)

    async def fail_hook(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("setup failed")

    monkeypatch.setattr(manager, "_run_hook", fail_hook)
    monkeypatch.setattr(
        workspace,
        "_force_rmtree",
        lambda _path: asyncio.sleep(0, result=(False, "locked")),
    )
    with pytest.raises(RuntimeError, match="setup failed"):
        await manager.create_or_reuse("FAIL-CLEANUP")


def test_workspace_refresh_plan_rejects_mismatched_git_common_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = tmp_path / "ticket-common"
    ticket.mkdir()
    (ticket / ".git").write_text("gitdir", encoding="utf-8")

    def query(path: Path, *args: str) -> str | None:
        if args == ("rev-parse", "--show-toplevel"):
            return f"{ticket}\n"
        if args == ("rev-parse", "--git-common-dir"):
            return str(tmp_path / ("one" if path == ticket else "two"))
        return None

    monkeypatch.setattr(
        workspace, "_local_branch_ref", lambda *_args: "refs/heads/main"
    )
    monkeypatch.setattr(workspace, "_git_query", query)
    assert (
        workspace._linked_worktree_refresh_plan(
            path=ticket,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
        )
        is None
    )


@pytest.mark.parametrize("failure_stage", ["workspace", "common"])
def test_workspace_refresh_plan_fails_closed_when_path_resolution_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    ticket = tmp_path / f"ticket-resolve-{failure_stage}"
    ticket.mkdir()
    (ticket / ".git").write_text("gitdir", encoding="utf-8")
    sentinel = tmp_path / f"broken-{failure_stage}"
    original_resolve = Path.resolve

    def resolve(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path == sentinel:
            raise OSError("resolution failed")
        return original_resolve(path, *args, **kwargs)

    def query(_path: Path, *args: str) -> str | None:
        if args == ("rev-parse", "--show-toplevel"):
            return f"{sentinel if failure_stage == 'workspace' else ticket}\n"
        if args == ("rev-parse", "--git-common-dir"):
            return f"{sentinel}\n"
        return None

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(
        workspace, "_local_branch_ref", lambda *_args: "refs/heads/main"
    )
    monkeypatch.setattr(workspace, "_git_query", query)
    assert (
        workspace._linked_worktree_refresh_plan(
            path=ticket,
            workflow_dir=tmp_path,
            branch="ticket",
            merge_target="main",
        )
        is None
    )


@pytest.mark.parametrize(
    "failure",
    [subprocess.TimeoutExpired("bash", 1), OSError("spawn failed")],
)
@pytest.mark.asyncio
async def test_workspace_auto_commit_maps_timeout_and_spawn_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    repo = tmp_path / "commit-failure"
    repo.mkdir()
    monkeypatch.setattr(
        workspace.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    outcome = await workspace.commit_workspace_on_done(
        repo,
        identifier="FAIL-1",
        title="failure",
        exit_reason="timeout",
        timeout_s=1,
    )
    assert outcome.status == workspace.COMMIT_FAILED


@pytest.mark.parametrize(
    ("commit_status", "push", "failure"),
    [
        (workspace.COMMIT_FAILED, True, None),
        (workspace.COMMIT_OK, False, None),
        (workspace.COMMIT_OK, True, subprocess.TimeoutExpired("bash", 1)),
        (workspace.COMMIT_OK, True, OSError("spawn failed")),
    ],
)
@pytest.mark.asyncio
async def test_workspace_history_gate_maps_commit_push_and_spawn_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commit_status: str,
    push: bool,
    failure: Exception | None,
) -> None:
    async def commit(*_args: Any, **_kwargs: Any) -> workspace.CommitOutcome:
        return workspace.CommitOutcome(commit_status, detail="commit detail")

    monkeypatch.setattr(workspace, "commit_workspace_on_done", commit)
    if failure is not None:
        monkeypatch.setattr(
            workspace.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )
    result = await workspace.finalize_delivery_history(
        tmp_path,
        identifier="HISTORY-1",
        title="history",
        push=push,
        timeout_s=1,
    )
    if commit_status == workspace.COMMIT_FAILED:
        assert result.status == workspace.HISTORY_COMMIT_FAILED
    elif not push:
        assert result.status == workspace.HISTORY_LOCAL_ONLY
    else:
        assert result.status == workspace.HISTORY_PUSH_FAILED


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.TimeoutExpired("bash", 1),
        OSError("spawn failed"),
        subprocess.CompletedProcess([], 0, stdout=b"LOCAL_SHA=a\nREMOTE_SHA=a\n"),
        subprocess.CompletedProcess(
            [], workspace._PUSH_NO_UPSTREAM, stdout=b"LOCAL_SHA=a\n"
        ),
        subprocess.CompletedProcess([], workspace._PUSH_REJECTED, stderr=b"rejected"),
    ],
)
@pytest.mark.asyncio
async def test_workspace_branch_history_maps_process_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: Exception | subprocess.CompletedProcess[bytes],
) -> None:
    def run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(workspace.subprocess, "run", run)
    result = await workspace.verify_branch_history(
        tmp_path, branch="symphony/TASK-1", timeout_s=1
    )
    assert result.status in {
        workspace.HISTORY_OK,
        workspace.HISTORY_LOCAL_ONLY,
        workspace.HISTORY_PUSH_FAILED,
        workspace.HISTORY_COMMIT_FAILED,
    }


def test_workspace_agent_cwd_must_exist_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(workspace.InvalidWorkspaceCwd, match="not a directory"):
        workspace.validate_agent_cwd(root / "missing", root)
