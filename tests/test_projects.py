from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

from symphony.cli import project as project_cli
from symphony.projects import (
    Project,
    ProjectError,
    ProjectRegistry,
    create_or_adopt_project,
    project_target_expectation,
)
from symphony.service import ServiceStatus


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True
    )
    (path / "WORKFLOW.md").write_text(
        "---\ntracker: {kind: file}\n---\n", encoding="utf-8"
    )
    return path


def source_bundle(path: Path) -> Path:
    init_repo(path)
    for name in ("tui-open.sh", "tui-open.bat", "AGENTS.md", "GEMINI.md"):
        (path / name).write_text(name + "\n", encoding="utf-8")
    (path / "WORKFLOW.file.example.md").write_text("workflow\n", encoding="utf-8")
    (path / "scripts").mkdir()
    (path / "scripts/symphony-setup-worktree.sh").write_text(
        "#!/bin/sh\n", encoding="utf-8"
    )
    (path / "docs/symphony-prompts").mkdir(parents=True)
    (path / "docs/symphony-prompts/base.md").write_text("prompt\n", encoding="utf-8")
    (path / "skills/demo").mkdir(parents=True)
    (path / "skills/demo/SKILL.md").write_text("skill\n", encoding="utf-8")
    return path


def project(repo: Path, *, id: str = "one", port: int = 9999) -> Project:
    return Project(
        id, id.title(), str(repo), str(repo / "WORKFLOW.md"), "127.0.0.1", port
    )


def create_project_in_process(payload: tuple[str, str, str, str]) -> Project:
    target, source, project_id, registry_path = payload
    return create_or_adopt_project(
        target,
        source=source,
        name=project_id,
        project_id=project_id,
        registry=ProjectRegistry(Path(registry_path)),
    )


def test_registry_json_v1_uses_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state" / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(path))
    repo = init_repo(tmp_path / "repo")
    registry = ProjectRegistry()
    registry.add(project(repo))

    assert registry.load() == [project(repo)]
    assert json.loads(path.read_text()) == {
        "version": 1,
        "projects": [
            {
                "id": "one",
                "name": "One",
                "git_repo": str(repo),
                "workflow": str(repo / "WORKFLOW.md"),
                "host": "127.0.0.1",
                "port": 9999,
            }
        ],
    }


def test_registry_recognizes_exact_workflow_already_serving_registered_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony import service

    repo = init_repo(tmp_path / "repo")
    registry = ProjectRegistry(tmp_path / "projects.json")
    registry.add(project(repo))
    monkeypatch.setattr(
        service,
        "service_status",
        lambda *_args, **_kwargs: ServiceStatus(state="stopped", record=None),
    )
    probes: list[tuple[str, int, Path]] = []

    def exact_probe(host: str, port: int, workflow: str | Path) -> bool:
        probes.append((host, port, Path(workflow)))
        return True

    monkeypatch.setattr(service, "is_symphony_workflow_reachable", exact_probe)

    status = registry.status("one")

    assert status.state == "running"
    assert status.api_reachable is True
    assert status.record is None
    assert probes == [("127.0.0.1", 9999, repo / "WORKFLOW.md")]


def test_registry_stopped_status_performs_one_exact_identity_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from symphony import service

    repo = init_repo(tmp_path / "repo")
    registered = project(repo)
    registry = ProjectRegistry(tmp_path / "projects.json")
    registry.add(registered)
    service.save_record(
        service.ServiceRecord(
            workflow_path=Path(registered.workflow).resolve(),
            workflow_dir=repo.resolve(),
            host=registered.host,
            port=registered.port,
            orchestrator_pid=1234,
            log_path=repo / "log" / "symphony.log",
            started_at="2026-05-16T00:00:00Z",
            orchestrator_command=[],
        )
    )
    monkeypatch.setattr(service, "is_process_running", lambda _pid: False)
    probes: list[tuple[str, int, Path]] = []

    def exact_probe(host: str, port: int, workflow: str | Path) -> bool:
        probes.append((host, port, Path(workflow)))
        return False

    monkeypatch.setattr(service, "is_symphony_workflow_reachable", exact_probe)

    status = registry.status("one")

    assert status.state == "stopped"
    assert probes == [
        (registered.host, registered.port, Path(registered.workflow).resolve())
    ]


@pytest.mark.parametrize("collision", ["id", "repo", "port"])
def test_registry_rejects_duplicate_identity_repo_or_port(
    tmp_path: Path, collision: str
) -> None:
    first_repo = init_repo(tmp_path / "one")
    second_repo = init_repo(tmp_path / "two")
    registry = ProjectRegistry(tmp_path / "projects.json")
    registry.add(project(first_repo))
    duplicate = Project(
        "one" if collision == "id" else "two",
        "Two",
        str(first_repo if collision == "repo" else second_repo),
        str(second_repo / "WORKFLOW.md"),
        "0.0.0.0",
        9999 if collision == "port" else 10000,
    )
    with pytest.raises(ProjectError):
        registry.add(duplicate)


def test_add_resolves_git_top_level_and_rejects_source_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = init_repo(tmp_path / "repo")
    nested = repo / "nested"
    nested.mkdir()
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "projects.json"))
    source = source_bundle(tmp_path / "source")
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)

    assert project_cli.main(["add", str(nested), "--id", "app"]) == 0
    assert ProjectRegistry().get("app").git_repo == str(repo.resolve())

    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "other.json"))
    assert project_cli.main(["add", str(source), "--id", "source"]) == 1
    assert "protected Symphony source checkout" in capsys.readouterr().err


def test_create_bootstraps_sibling_repo_and_initial_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_bundle(tmp_path / "symphony-source")
    (source / "skills/.DS_Store").write_text("local metadata", encoding="utf-8")
    registry_path = tmp_path / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(registry_path))
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)

    assert (
        project_cli.main(["create", "Demo App", "--id", "demo", "--port", "10010"]) == 0
    )
    target = tmp_path / "demo"
    record = ProjectRegistry().get("demo")
    assert Path(record.git_repo) == target
    assert (target / "WORKFLOW.md").read_text() == "workflow\n"
    assert (target / "docs/symphony-prompts/base.md").is_file()
    assert (target / "skills/demo/SKILL.md").is_file()
    assert not (target / "skills/.DS_Store").exists()
    assert (target / "scripts/symphony-setup-worktree.sh").is_file()
    assert (target / "kanban/.gitkeep").is_file()
    assert git(target, "branch", "--show-current") == "main"
    assert git(target, "rev-list", "--count", "HEAD") == "1"
    assert not git(target, "status", "--porcelain")


def test_lifecycle_commands_delegate_to_service_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = init_repo(tmp_path / "repo")
    registry_path = tmp_path / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(registry_path))
    ProjectRegistry().add(project(repo, id="app", port=10001))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        project_cli.service, "main", lambda argv: calls.append(argv) or 0
    )

    assert project_cli.main(["start", "app", "--replace"]) == 0
    assert project_cli.main(["stop", "app", "--force"]) == 0
    assert project_cli.main(["status", "app"]) == 0
    assert calls == [
        [
            "start",
            str(repo / "WORKFLOW.md"),
            "--host",
            "127.0.0.1",
            "--port",
            "10001",
            "--replace",
        ],
        ["stop", str(repo / "WORKFLOW.md"), "--timeout", "10.0", "--force"],
        ["status", str(repo / "WORKFLOW.md"), "--port", "10001"],
    ]


def test_create_rejects_absolute_workflow_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_bundle(tmp_path / "symphony-source")
    sentinel = tmp_path / "outside-workflow.md"
    sentinel.write_text("keep me\n", encoding="utf-8")
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "projects.json"))
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)

    assert (
        project_cli.main(
            ["create", "Unsafe", "--id", "unsafe", "--workflow", str(sentinel)]
        )
        == 1
    )
    assert sentinel.read_text(encoding="utf-8") == "keep me\n"
    assert not (tmp_path / "unsafe").exists()


def test_add_rejects_workflow_from_another_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = init_repo(tmp_path / "first")
    init_repo(tmp_path / "second")
    source = init_repo(tmp_path / "source")
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "projects.json"))
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)

    assert (
        project_cli.main(
            ["add", str(first), "--id", "first", "--workflow", "../second/WORKFLOW.md"]
        )
        == 1
    )
    assert ProjectRegistry().load() == []


def test_adopt_non_git_directory_preserves_contents_and_commits_only_created_files(
    tmp_path: Path,
) -> None:
    source = source_bundle(tmp_path / "source")
    target = tmp_path / "existing"
    target.mkdir()
    unrelated = target / "notes.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")
    registry = ProjectRegistry(tmp_path / "projects.json")

    record = create_or_adopt_project(
        target, source=source, name="Existing", project_id="existing", registry=registry
    )

    assert Path(record.git_repo) == target.resolve()
    assert unrelated.read_text(encoding="utf-8") == "keep me\n"
    assert (
        "notes.txt"
        not in git(target, "show", "--format=", "--name-only", "HEAD").splitlines()
    )
    assert git(target, "status", "--porcelain", "--", "notes.txt") == "?? notes.txt"
    assert (target / ".git").is_dir()


def test_create_or_adopt_rejects_directory_created_after_missing_target_confirmation(
    tmp_path: Path,
) -> None:
    source = source_bundle(tmp_path / "source")
    target = tmp_path / "target"
    expected = project_target_expectation(target)
    target.mkdir()
    registry = ProjectRegistry(tmp_path / "projects.json")

    with pytest.raises(ProjectError, match="project target changed"):
        create_or_adopt_project(
            target,
            source=source,
            name="Target",
            project_id="target",
            registry=registry,
            expected_target=expected,
        )

    assert registry.load() == []
    assert not (target / "kanban").exists()


def test_create_or_adopt_rejects_git_adoption_after_new_target_confirmation(
    tmp_path: Path,
) -> None:
    source = source_bundle(tmp_path / "source")
    target = tmp_path / "target"
    target.mkdir()
    expected = project_target_expectation(target)
    subprocess.run(
        ["git", "init", "-b", "main", str(target)],
        check=True,
        capture_output=True,
    )
    registry = ProjectRegistry(tmp_path / "projects.json")

    with pytest.raises(ProjectError, match="project target changed"):
        create_or_adopt_project(
            target,
            source=source,
            name="Target",
            project_id="target",
            registry=registry,
            expected_target=expected,
        )

    assert registry.load() == []
    assert not (target / "kanban").exists()


def test_create_or_adopt_rejects_changed_expected_repo_before_mutation(
    tmp_path: Path,
) -> None:
    source = source_bundle(tmp_path / "source")
    parent = init_repo(tmp_path / "parent")
    target = parent / "nested"
    target.mkdir()
    registry = ProjectRegistry(tmp_path / "projects.json")

    with pytest.raises(ProjectError, match="project target changed"):
        create_or_adopt_project(
            target,
            source=source,
            name="Nested",
            project_id="nested",
            registry=registry,
            expected_repo=target,
        )

    assert registry.load() == []
    assert not (parent / "kanban").exists()


def test_adopt_git_repo_never_stages_unrelated_changes_or_overwrites_bundle(
    tmp_path: Path,
) -> None:
    source = source_bundle(tmp_path / "source")
    target = init_repo(tmp_path / "target")
    (target / "WORKFLOW.md").write_text("custom workflow\n", encoding="utf-8")
    (target / "tracked.txt").write_text("original\n", encoding="utf-8")
    git(target, "add", "WORKFLOW.md", "tracked.txt")
    git(
        target,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "initial",
    )
    (target / "tracked.txt").write_text("modified\n", encoding="utf-8")
    (target / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    create_or_adopt_project(
        target,
        source=source,
        name="Target",
        project_id="target",
        registry=ProjectRegistry(tmp_path / "projects.json"),
    )

    assert (target / "WORKFLOW.md").read_text(encoding="utf-8") == "custom workflow\n"
    committed = git(target, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert "tracked.txt" not in committed
    assert "untracked.txt" not in committed
    assert "tracked.txt" in git(target, "diff", "--name-only").splitlines()
    assert (
        "tracked.txt" not in git(target, "diff", "--cached", "--name-only").splitlines()
    )
    assert "?? untracked.txt" in git(target, "status", "--porcelain")


def test_create_or_adopt_is_idempotent_for_registered_repository(
    tmp_path: Path,
) -> None:
    source = source_bundle(tmp_path / "source")
    target = tmp_path / "target"
    registry = ProjectRegistry(tmp_path / "projects.json")
    first = create_or_adopt_project(
        target, source=source, name="First", project_id="first", registry=registry
    )
    second = create_or_adopt_project(
        target,
        source=source,
        name="Ignored",
        project_id="other",
        port=10042,
        registry=registry,
    )

    assert second == first
    assert registry.list() == [first]


def test_conflict_and_workflow_escape_leave_existing_directory_untouched(
    tmp_path: Path,
) -> None:
    source = source_bundle(tmp_path / "source")
    target = tmp_path / "existing"
    target.mkdir()
    (target / "docs").write_text("not a directory\n", encoding="utf-8")
    before = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ProjectError, match="requires a directory"):
        create_or_adopt_project(
            target,
            source=source,
            project_id="conflict",
            registry=ProjectRegistry(tmp_path / "projects.json"),
        )
    assert target.is_dir()
    assert not (target / ".git").exists()
    assert before == {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ProjectError, match="escapes"):
        create_or_adopt_project(
            target,
            source=source,
            project_id="escape",
            workflow="../WORKFLOW.md",
            registry=ProjectRegistry(tmp_path / "other-projects.json"),
        )
    with pytest.raises(ProjectError, match="must be relative"):
        create_or_adopt_project(
            target,
            source=source,
            project_id="absolute",
            workflow=str(tmp_path / "WORKFLOW.md"),
            registry=ProjectRegistry(tmp_path / "other-projects.json"),
        )


def test_missing_workflow_gets_project_scoped_workspace_and_contained_board(
    tmp_path: Path,
) -> None:
    source = source_bundle(tmp_path / "source")
    (source / "WORKFLOW.file.example.md").write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "workspace:\n  root: ~/symphony_workspaces\n---\n",
        encoding="utf-8",
    )
    target = tmp_path / "new-project"

    create_or_adopt_project(
        target,
        source=source,
        name="New",
        project_id="new",
        registry=ProjectRegistry(tmp_path / "projects.json"),
    )

    assert "root: ~/symphony_workspaces/new" in (target / "WORKFLOW.md").read_text()


def test_existing_git_without_workflow_is_bootstrapped_without_replacing_history(
    tmp_path: Path,
) -> None:
    source = scoped_source_bundle(tmp_path / "source-existing")
    target = tmp_path / "existing-git"
    target.mkdir()
    git(target, "init", "-b", "main")
    (target / "product.txt").write_text("product\n", encoding="utf-8")
    git(target, "add", "product.txt")
    git(
        target,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "product baseline",
    )
    baseline = git(target, "rev-parse", "HEAD")

    project_record = create_or_adopt_project(
        target,
        source=source,
        name="Existing Git",
        project_id="existing-git",
        registry=ProjectRegistry(tmp_path / "projects-existing.json"),
    )

    assert Path(project_record.git_repo) == target.resolve()
    assert git(target, "rev-parse", "HEAD^") == baseline
    assert (target / "product.txt").read_text(encoding="utf-8") == "product\n"
    assert "root: ~/symphony_workspaces/existing-git" in (
        target / "WORKFLOW.md"
    ).read_text(encoding="utf-8")
    assert not git(target, "status", "--porcelain")


def test_existing_git_can_adopt_symphony_files_ignored_by_repository(
    tmp_path: Path,
) -> None:
    source = scoped_source_bundle(tmp_path / "source-ignored")
    target = tmp_path / "ignored-git"
    target.mkdir()
    git(target, "init", "-b", "main")
    (target / ".gitignore").write_text("*\n.domain-agent/\n", encoding="utf-8")
    (target / "product.txt").write_text("preserve me\n", encoding="utf-8")
    git(target, "add", "-f", ".gitignore")
    git(
        target,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "ignore policy",
    )

    project_record = create_or_adopt_project(
        target,
        source=source,
        name="Ignored",
        project_id="ignored",
        registry=ProjectRegistry(tmp_path / "projects-ignored.json"),
    )

    assert Path(project_record.git_repo) == target.resolve()
    assert git(target, "ls-files", "WORKFLOW.md") == "WORKFLOW.md"
    assert git(target, "check-ignore", "product.txt") == "product.txt"
    assert "product.txt" not in git(target, "show", "--format=", "--name-only", "HEAD")
    assert (target / "product.txt").read_text(encoding="utf-8") == "preserve me\n"
    assert not git(target, "status", "--porcelain")


def scoped_source_bundle(path: Path) -> Path:
    source = source_bundle(path)
    (source / "WORKFLOW.file.example.md").write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "workspace:\n  root: ~/symphony_workspaces\n---\n",
        encoding="utf-8",
    )
    return source


def test_concurrent_distinct_projects_keep_all_records_and_unique_ports(
    tmp_path: Path,
) -> None:
    source = scoped_source_bundle(tmp_path / "source")
    registry_path = tmp_path / "projects.json"

    def create(index: int) -> Project:
        return create_or_adopt_project(
            tmp_path / f"repo-{index}",
            source=source,
            name=f"Repo {index}",
            project_id=f"repo-{index}",
            registry=ProjectRegistry(registry_path),
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(create, range(6)))

    stored = ProjectRegistry(registry_path).list()
    assert {item.id for item in stored} == {item.id for item in records}
    assert len({item.port for item in stored}) == 6


def test_concurrent_processes_serialize_registry_and_port_allocation(
    tmp_path: Path,
) -> None:
    source = scoped_source_bundle(tmp_path / "source")
    registry_path = tmp_path / "projects.json"
    payloads = [
        (
            str(tmp_path / f"process-{index}"),
            str(source),
            f"process-{index}",
            str(registry_path),
        )
        for index in range(2)
    ]

    process_context = get_context("spawn")
    processes = [
        process_context.Process(
            name=payload[2], target=create_project_in_process, args=(payload,)
        )
        for payload in payloads
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)

        assert {process.name: process.exitcode for process in processes} == {
            payload[2]: 0 for payload in payloads
        }
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            if process.pid is not None:
                process.join()

    stored = ProjectRegistry(registry_path).list()
    assert {item.id for item in stored} == {payload[2] for payload in payloads}
    assert len({item.port for item in stored}) == 2


def test_concurrent_same_target_returns_one_record_without_cleanup_race(
    tmp_path: Path,
) -> None:
    source = scoped_source_bundle(tmp_path / "source")
    registry_path = tmp_path / "projects.json"
    target = tmp_path / "shared"

    def adopt(index: int) -> Project:
        return create_or_adopt_project(
            target,
            source=source,
            name=f"Shared {index}",
            project_id=f"shared-{index}",
            registry=ProjectRegistry(registry_path),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(adopt, range(4)))

    assert len({item.id for item in records}) == 1
    assert ProjectRegistry(registry_path).list() == [records[0]]
    assert (target / "WORKFLOW.md").is_file()
    assert git(target, "rev-list", "--count", "HEAD") == "1"


def test_env_backed_board_escape_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_bundle(tmp_path / "source")
    target = init_repo(tmp_path / "target")
    (target / "WORKFLOW.md").write_text(
        "---\ntracker:\n  kind: file\n  board_root: $BOARD_ROOT\n"
        "workspace:\n  root: ./workspaces\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOARD_ROOT", str(tmp_path / "outside-board"))

    with pytest.raises(ProjectError, match="board root escapes"):
        create_or_adopt_project(
            target,
            source=source,
            project_id="target",
            registry=ProjectRegistry(tmp_path / "projects.json"),
        )


def test_duplicate_resolved_workspace_root_is_rejected(tmp_path: Path) -> None:
    source = source_bundle(tmp_path / "source")
    workspace = tmp_path / "shared-workspaces"
    registry = ProjectRegistry(tmp_path / "projects.json")

    def existing_repo(name: str) -> Path:
        repo = init_repo(tmp_path / name)
        (repo / "WORKFLOW.md").write_text(
            "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
            f"workspace:\n  root: {workspace}\n---\n",
            encoding="utf-8",
        )
        return repo

    first = existing_repo("first")
    second = existing_repo("second")
    create_or_adopt_project(first, source=source, project_id="first", registry=registry)
    with pytest.raises(ProjectError, match="workspace root already owned"):
        create_or_adopt_project(
            second, source=source, project_id="second", registry=registry
        )
    assert (second / "WORKFLOW.md").is_file()


def test_registry_start_waits_until_service_accepts_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = init_repo(tmp_path / "repo-ready")
    registry = ProjectRegistry(tmp_path / "projects-ready.json")
    registry.add(project(repo, id="ready", port=10023))
    monkeypatch.setattr("symphony.service.main", lambda _argv: 0)
    monkeypatch.setattr("symphony.projects.time.sleep", lambda _seconds: None)
    attempts = 0

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def connect(address, timeout):
        nonlocal attempts
        assert address == ("127.0.0.1", 10023)
        assert timeout == 0.2
        attempts += 1
        if attempts == 1:
            raise OSError("not ready")
        return _Connection()

    monkeypatch.setattr("symphony.projects.socket.create_connection", connect)

    assert registry.start("ready") == 0
    assert attempts == 2


def test_project_port_allocation_skips_unregistered_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = scoped_source_bundle(tmp_path / "source-port")
    probed: list[int] = []

    def available(host: str, port: int) -> bool:
        assert host == "127.0.0.1"
        probed.append(port)
        return port == 10002

    monkeypatch.setattr("symphony.projects._port_is_available", available)
    record = create_or_adopt_project(
        tmp_path / "port-project",
        source=source,
        name="Port Project",
        project_id="port-project",
        registry=ProjectRegistry(tmp_path / "projects-port.json"),
    )

    assert record.port == 10002
    assert probed == [9999, 10000, 10001, 10002]


def test_create_excludes_tracker_lock_dir_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board's runtime ``.locks/`` dir must not pollute git status.

    The file tracker creates ``<board>/.locks/`` on first mutation; without
    a local exclude rule it shows up as untracked noise (visible on any
    platform; reported on Windows boards). The rule lives in the shared
    ``info/exclude`` — never the operator's checked-in .gitignore.
    """
    source = source_bundle(tmp_path / "source-locks")
    registry_path = tmp_path / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(registry_path))
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)

    assert (
        project_cli.main(["create", "Locks App", "--id", "locks-app", "--port", "10110"])
        == 0
    )
    target = tmp_path / "locks-app"
    exclude = (target / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/kanban/.locks/" in exclude.splitlines()

    # The rule actually silences the runtime directory.
    (target / "kanban" / ".locks").mkdir()
    (target / "kanban" / ".locks" / "allocator.lock").write_text("", encoding="utf-8")
    status = git(target, "status", "--porcelain")
    assert ".locks" not in status


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the win32 junction bootstrap end to end: Git for "
    "Windows follows junctions where POSIX records a symlink entry",
)
def test_windows_skill_junction_stays_machine_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The skill junction must never publish the bundle's contents.

    Regression (verified by repro): with Git for Windows' default
    ``core.symlinks=false``, ``git add`` of the ``.claude/skills/
    symphony-skill`` junction stages every file *inside* the link target —
    ``.claude/skills/symphony-skill/SKILL.md`` etc. — where POSIX commits
    a single mode-120000 symlink entry. The junction is therefore kept out
    of the bootstrap commit and excluded locally so it relinks per machine.
    """
    source = source_bundle(tmp_path / "source-junction")
    skill = source / "skills" / "symphony-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("router skill\n", encoding="utf-8")
    registry_path = tmp_path / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(registry_path))
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)

    assert (
        project_cli.main(
            ["create", "Junction App", "--id", "junction-app", "--port", "10120"]
        )
        == 0
    )
    target = tmp_path / "junction-app"
    link = target / ".claude" / "skills" / "symphony-skill"
    assert link.is_junction()
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "router skill\n"

    tracked = git(target, "ls-files").splitlines()
    # The canonical bundle content is committed once...
    assert "skills/symphony-skill/SKILL.md" in tracked
    # ...and the junction never duplicates it under .claude/skills/.
    assert not [name for name in tracked if name.startswith(".claude/")]

    # Machine-local rule keeps the untracked junction out of git status.
    exclude = (target / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.claude/skills/symphony-skill" in exclude.splitlines()
    status = git(target, "status", "--porcelain")
    assert ".claude" not in status
