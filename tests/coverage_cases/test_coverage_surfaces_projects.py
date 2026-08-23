"""Coverage contracts for project creation and registry lifecycle."""
# ruff: noqa: F405

import builtins

from tests.coverage_cases._surfaces_support import *  # noqa: F403


def test_project_json_and_registry_fail_closed_on_corrupt_data(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="JSON objects"):
        Project.from_json("demo")
    with pytest.raises(ProjectError, match="invalid project entry"):
        Project.from_json({"id": "demo"})
    with pytest.raises(ProjectError, match="invalid project id"):
        projects.validate_id("Bad ID")
    with pytest.raises(ProjectError, match="invalid port"):
        projects.validate_port(0)

    registry_path = tmp_path / "projects.json"
    registry = ProjectRegistry(registry_path)
    registry_path.write_text("{", encoding="utf-8")
    with pytest.raises(ProjectError, match="cannot read project registry"):
        registry.load()
    registry_path.write_text(
        json.dumps({"version": 99, "projects": []}), encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="unsupported project registry"):
        registry.load()
    registry_path.write_text(
        json.dumps({"version": 1, "projects": {}}), encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="projects must be a list"):
        registry.load()


def test_project_registry_remove_and_service_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ProjectRegistry(tmp_path / "projects.json")
    record = _project(tmp_path)
    registry.save([record])
    assert registry.remove("demo") == record
    assert registry.list() == []
    with pytest.raises(ProjectError, match="unknown project"):
        registry.remove("missing")

    registry.add(record)
    monkeypatch.setattr(
        "symphony.service.main", lambda argv: 9 if argv[0] == "start" else 4
    )
    assert registry.start("demo") == 9
    assert registry.stop("demo") == 4


def test_project_registry_get_and_start_timeout_are_operator_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ProjectRegistry(tmp_path / "projects.json")
    record = _project(tmp_path)
    registry.add(record)
    with pytest.raises(ProjectError, match="unknown project"):
        registry.get("missing")

    monkeypatch.setattr("symphony.service.main", lambda _argv: 0)
    clock = iter((0.0, 0.0, 11.0))
    monkeypatch.setattr(projects.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(projects.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(
        projects.socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not ready")),
    )
    assert registry.start("demo") == 1


def test_project_registry_rejects_duplicate_identity_surfaces(tmp_path: Path) -> None:
    first_repo = tmp_path / "first"
    second_repo = tmp_path / "second"
    first_repo.mkdir()
    second_repo.mkdir()
    first = _project(first_repo, id="one", port=10001)

    with pytest.raises(ProjectError, match="duplicate project id"):
        projects.validate_unique([first, _project(second_repo, id="one", port=10002)])
    with pytest.raises(ProjectError, match="repository already registered"):
        projects.validate_unique([first, _project(first_repo, id="two", port=10002)])
    with pytest.raises(ProjectError, match="service port already registered"):
        projects.validate_unique([first, _project(second_repo, id="two", port=10001)])


def test_project_paths_must_stay_inside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert projects._inside(repo / "WORKFLOW.md", repo) is True
    assert projects._inside(tmp_path / "outside", repo) is False
    with pytest.raises(ProjectError, match="must be relative"):
        projects._workflow_path(repo, str(tmp_path / "WORKFLOW.md"))
    with pytest.raises(ProjectError, match="escapes"):
        projects._workflow_path(repo, "../WORKFLOW.md")


def test_project_port_probes_and_exhaustion_fail_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        occupied = int(listener.getsockname()[1])
        assert projects._port_is_available("127.0.0.1", occupied) is False

    monkeypatch.setattr(projects, "_port_is_available", lambda _host, _port: False)
    with pytest.raises(ProjectError, match="no available registry port"):
        projects._next_port([], "127.0.0.1")


def test_project_copy_preserves_tracked_deletions_and_rejects_directory_conflict(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    deleted = tmp_path / "deleted.txt"
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    projects._copy_missing_file(source, deleted, created_files, created_dirs, {deleted})
    assert not deleted.exists()
    assert created_files == []

    conflict = tmp_path / "conflict"
    conflict.mkdir()
    with pytest.raises(ProjectError, match="requires a file"):
        projects._copy_missing_file(source, conflict, [], [], set())


def test_project_git_and_source_discovery_errors_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("git is missing")

    monkeypatch.setattr(projects.subprocess, "run", unavailable)
    assert projects._repo_key(str(tmp_path)) == projects.os.path.normcase(
        str(tmp_path.resolve())
    )
    with pytest.raises(ProjectError, match="cannot run git"):
        projects._run_git(tmp_path, "status")
    with pytest.raises(ProjectError, match="source checkout"):
        projects.source_checkout()
    with pytest.raises(ProjectError, match="letter or digit"):
        projects._slug("---")


def test_project_bundle_validation_reports_wrong_file_types(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in projects._BUNDLE_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("bundle", encoding="utf-8")
    for name in projects._BUNDLE_DIRS:
        (source / name).mkdir(parents=True)

    (source / "AGENTS.md").unlink()
    (source / "AGENTS.md").mkdir()
    with pytest.raises(ProjectError, match="AGENTS.md is not a file"):
        projects._validate_source_bundle(source)

    (source / "AGENTS.md").rmdir()
    (source / "AGENTS.md").write_text("bundle", encoding="utf-8")
    prompt_dir = source / "docs" / "symphony-prompts"
    prompt_dir.rmdir()
    prompt_dir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ProjectError, match="symphony-prompts is not a directory"):
        projects._validate_source_bundle(source)


def test_project_workflow_resolution_and_cleanup_preserve_primary_error(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "WORKFLOW.md"
    malformed.write_text("---\ntracker: [\n---\n", encoding="utf-8")
    assert projects._workflow_resources(malformed, strict=False) == (None, None)
    with pytest.raises(ProjectError, match="cannot resolve workflow resources"):
        projects._workflow_resources(malformed, strict=True)

    missing = tmp_path / "already-missing"
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "child").write_text("keep", encoding="utf-8")
    projects._cleanup_created([missing], [missing, nonempty])
    assert nonempty.exists()

    copied_directory = tmp_path / "copied-directory"
    copied_directory.mkdir()
    (copied_directory / "nested").write_text("temporary", encoding="utf-8")
    projects._cleanup_created([copied_directory], [])
    assert not copied_directory.exists()


def test_project_local_excludes_append_without_rewriting_existing_rules(
    tmp_path: Path,
) -> None:
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    projects._ensure_local_git_excludes(non_repo, ("/kanban/.locks/",))

    repo = tmp_path / "repo-excludes"
    repo.mkdir()
    _git_text(repo, "init", "-b", "main")
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text("existing-rule", encoding="utf-8")
    projects._ensure_local_git_excludes(repo, ("/kanban/.locks/",))
    assert exclude.read_text(encoding="utf-8") == ("existing-rule\n/kanban/.locks/\n")
    projects._ensure_local_git_excludes(repo, ("/kanban/.locks/",))
    assert exclude.read_text(encoding="utf-8").count("/kanban/.locks/") == 1


@pytest.mark.parametrize(
    "conflict",
    [
        "bundle-file-is-directory",
        "bundle-parent-is-file",
        "bundle-directory-is-file",
        "operator-directory-is-file",
        "skill-link-is-file",
    ],
)
def test_project_adoption_validates_all_target_shapes_before_writing(
    tmp_path: Path, conflict: str
) -> None:
    repo = tmp_path / conflict
    repo.mkdir()
    workflow = repo / "WORKFLOW.md"
    if conflict == "bundle-file-is-directory":
        (repo / "AGENTS.md").mkdir()
    elif conflict == "bundle-parent-is-file":
        (repo / "scripts").write_text("not a directory", encoding="utf-8")
    elif conflict == "bundle-directory-is-file":
        (repo / "docs").mkdir()
        (repo / "docs" / "symphony-prompts").write_text(
            "not a directory", encoding="utf-8"
        )
    elif conflict == "operator-directory-is-file":
        (repo / "kanban").write_text("not a directory", encoding="utf-8")
    else:
        (repo / ".claude" / "skills").mkdir(parents=True)
        (repo / ".claude" / "skills" / "symphony-skill").write_text(
            "not a directory", encoding="utf-8"
        )
    with pytest.raises(ProjectError, match="requires a (file|directory)"):
        projects._validate_target_conflicts(repo, workflow)


def test_project_tree_merge_and_clean_preflight_validate_directory_contracts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-tree"
    source.mkdir()
    destination = tmp_path / "destination"
    destination.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ProjectError, match="requires a directory"):
        projects._merge_missing_tree(source, destination, [], [], set())

    clean_repo = tmp_path / "clean"
    clean_repo.mkdir()
    projects._validate_target_conflicts(clean_repo, clean_repo / "WORKFLOW.md")


def test_project_windows_junction_fallbacks_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        projects.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )
    assert projects._mklink_junction(tmp_path / "link", tmp_path / "target") is True

    repo = tmp_path / "repo-junction"
    target = repo / "skills" / "symphony-skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("skill", encoding="utf-8")
    link = repo / ".claude" / "skills" / "symphony-skill"
    link.parent.mkdir(parents=True)
    fake_winapi = SimpleNamespace(
        CreateJunction=lambda *_args: (_ for _ in ()).throw(OSError("blocked"))
    )
    monkeypatch.setitem(sys.modules, "_winapi", fake_winapi)
    monkeypatch.setattr(projects, "_mklink_junction", lambda *_args: False)
    assert projects._link_skill_dir(link) is True
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "skill"

    exact = repo / ".claude" / "skills" / "symphony-skill"
    original = Path.is_junction

    def broken_junction(path: Path) -> bool:
        if path == exact:
            raise OSError("unreadable")
        return original(path)

    monkeypatch.setattr(Path, "is_junction", broken_junction)
    assert projects._is_windows_skill_junction(repo, exact) is False


@pytest.mark.skipif(os.name != "nt", reason="PyPy compatibility is Windows-only")
def test_project_pypy_windows_falls_back_when_winapi_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "skills" / "symphony-skill"
    target.mkdir(parents=True)
    link = tmp_path / ".claude" / "skills" / "symphony-skill"
    link.parent.mkdir(parents=True)
    original_import = builtins.__import__

    def import_without_winapi(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "_winapi":
            raise ImportError("PyPy does not provide _winapi")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_winapi)
    monkeypatch.setattr(projects, "_mklink_junction", lambda *_args: True)

    assert projects._link_skill_dir(link) is True


def test_project_setup_rejects_invalid_source_target_and_registry_collisions(
    tmp_path: Path,
) -> None:
    source = _minimal_project_source(tmp_path / "source-no-git", git=False)
    occupied = tmp_path / "occupied"
    occupied.write_text("file", encoding="utf-8")
    with pytest.raises(ProjectError, match="not a directory"):
        projects._create_or_adopt_project_locked(occupied, source=source)
    with pytest.raises(ProjectError, match="source checkout is not a Git repository"):
        projects._create_or_adopt_project_locked(tmp_path / "new", source=source)

    git_source = _minimal_project_source(tmp_path / "source-git", git=True)
    existing_repo = tmp_path / "existing"
    existing_repo.mkdir()
    registry = ProjectRegistry(tmp_path / "projects-collisions.json")
    registry.save([_project(existing_repo, id="taken", port=12000)])
    with pytest.raises(ProjectError, match="duplicate project id"):
        projects._create_or_adopt_project_locked(
            tmp_path / "duplicate-id",
            source=git_source,
            project_id="taken",
            port=12001,
            registry=registry,
        )
    with pytest.raises(ProjectError, match="service port already registered"):
        projects._create_or_adopt_project_locked(
            tmp_path / "duplicate-port",
            source=git_source,
            project_id="available",
            port=12000,
            registry=registry,
        )


def test_project_local_exclude_write_failure_is_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    common = tmp_path / "common"
    common.mkdir()
    (common / "info").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(projects, "_git_common_dir", lambda _repo: common)
    projects._ensure_local_git_excludes(tmp_path, ("/kanban/.locks/",))


def test_project_tree_special_entry_and_existing_parent_are_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "special-source"
    source.mkdir()
    special = source / "special"
    special.write_text("special", encoding="utf-8")
    destination = tmp_path / "merged"
    original_file = Path.is_file

    def is_file(path: Path) -> bool:
        return False if path == special else original_file(path)

    monkeypatch.setattr(Path, "is_file", is_file)
    with pytest.raises(ProjectError, match="unsupported source bundle entry"):
        projects._merge_missing_tree(source, destination, [], [], set())

    repo = tmp_path / "parent-break"
    (repo / "scripts").mkdir(parents=True)
    projects._validate_target_conflicts(repo, repo / "WORKFLOW.md")


def test_project_junction_cleanup_error_and_mklink_success_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_paths: list[str] = []
    original_unlink = projects.os.unlink

    def run(args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[Any]:
        batch_paths.append(str(args[3]))
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(projects.subprocess, "run", run)
    monkeypatch.setattr(
        projects.os,
        "unlink",
        lambda _path: (_ for _ in ()).throw(OSError("cleanup blocked")),
    )
    assert projects._mklink_junction(tmp_path / "link", tmp_path / "target") is True
    for batch in batch_paths:
        original_unlink(batch)

    repo = tmp_path / "mklink-fallback"
    target = repo / "skills" / "symphony-skill"
    target.mkdir(parents=True)
    link = repo / ".claude" / "skills" / "symphony-skill"
    link.parent.mkdir(parents=True)
    monkeypatch.setitem(
        sys.modules,
        "_winapi",
        SimpleNamespace(
            CreateJunction=lambda *_args: (_ for _ in ()).throw(OSError("blocked"))
        ),
    )
    monkeypatch.setattr(projects, "_mklink_junction", lambda *_args: True)
    assert projects._link_skill_dir(link) is True


def test_project_nested_creation_and_tracked_workflow_deletion_rollback(
    tmp_path: Path,
) -> None:
    source = _minimal_project_source(tmp_path / "source-create", git=True)
    nested = tmp_path / "absent" / "parents" / "project"
    created = projects._create_or_adopt_project_locked(
        nested,
        source=source,
        project_id="nested",
        port=12100,
        registry=ProjectRegistry(tmp_path / "nested-projects.json"),
    )
    assert Path(created.git_repo) == nested.resolve()

    target = tmp_path / "tracked-deletion"
    target.mkdir()
    _git_text(target, "init", "-b", "main")
    _git_text(target, "config", "user.name", "Coverage Test")
    _git_text(target, "config", "user.email", "coverage@example.com")
    (target / "WORKFLOW.md").write_text("tracked\n", encoding="utf-8")
    _git_text(target, "add", "WORKFLOW.md")
    _git_text(target, "commit", "-m", "workflow")
    (target / "WORKFLOW.md").unlink()
    with pytest.raises(ProjectError, match="workflow file not found"):
        projects._create_or_adopt_project_locked(
            target,
            source=source,
            project_id="tracked-deletion",
            port=12101,
            registry=ProjectRegistry(tmp_path / "deleted-projects.json"),
        )
    assert not (target / "WORKFLOW.md").exists()


def test_project_resource_ownership_rejects_shared_file_board(tmp_path: Path) -> None:
    repo = tmp_path / "owner-repo"
    board = repo / "kanban"
    board.mkdir(parents=True)
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "workspace:\n  root: ./workspaces\n---\nbody\n",
        encoding="utf-8",
    )
    other = tmp_path / "other-owner"
    other.mkdir()
    other_workflow = other / "WORKFLOW.md"
    other_workflow.write_text(
        "---\ntracker:\n  kind: file\n"
        f"  board_root: '{board.as_posix()}'\n"
        "workspace:\n  root: ./other-workspaces\n---\nbody\n",
        encoding="utf-8",
    )
    registered = _project(
        other,
        id="other-owner",
        workflow=str(other_workflow),
        port=12200,
    )
    with pytest.raises(ProjectError, match="board already owned"):
        projects._validate_resource_ownership(workflow, repo, [registered])


def test_project_confirmation_rejects_repo_and_git_identity_changes(
    tmp_path: Path,
) -> None:
    source = _minimal_project_source(tmp_path / "source-identity", git=True)
    target = tmp_path / "identity-target"
    target.mkdir()
    _git_text(target, "init", "-b", "main")
    expectation = projects.project_target_expectation(target)

    wrong_repo = replace(expectation, repo=tmp_path / "other-repo")
    with pytest.raises(ProjectError, match="project target changed"):
        projects._create_or_adopt_project_locked(
            target, source=source, expected_target=wrong_repo
        )

    wrong_common = replace(
        expectation,
        git_common_dir=tmp_path / "other-common",
        git_device=0,
        git_inode=0,
    )
    with pytest.raises(ProjectError, match="project target changed"):
        projects._create_or_adopt_project_locked(
            target, source=source, expected_target=wrong_common
        )

    assert expectation.git_device is not None
    wrong_inode = replace(expectation, git_device=expectation.git_device + 1)
    with pytest.raises(ProjectError, match="project target changed"):
        projects._create_or_adopt_project_locked(
            target, source=source, expected_target=wrong_inode
        )


@pytest.mark.parametrize("existing", [False, True])
def test_project_staged_inspection_failure_rolls_back_created_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    source = _minimal_project_source(tmp_path / f"source-stage-{existing}", git=True)
    target = tmp_path / f"target-stage-{existing}"
    if existing:
        target.mkdir()
        _git_text(target, "init", "-b", "main")

    original_run = projects.subprocess.run

    def run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if "diff" in args and "--cached" in args and "--quiet" in args:
            return subprocess.CompletedProcess(args, 2)
        return original_run(args, **kwargs)

    monkeypatch.setattr(projects.subprocess, "run", run)
    if existing:
        original_git = projects._run_git

        def git(cwd: Path, *args: str) -> str:
            if args and args[0] in {"reset", "rm"}:
                raise ProjectError("cleanup failed")
            return original_git(cwd, *args)

        monkeypatch.setattr(projects, "_run_git", git)

    with pytest.raises(ProjectError, match="cannot inspect staged"):
        projects._create_or_adopt_project_locked(
            target,
            source=source,
            project_id=f"stage-{str(existing).lower()}",
            port=12210 if existing else 12211,
            registry=ProjectRegistry(tmp_path / f"stage-{existing}.json"),
        )


@pytest.mark.parametrize("replacement", ["git-file", "missing"])
def test_project_adoption_rollback_preserves_external_git_metadata_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    source = _minimal_project_source(tmp_path / f"source-{replacement}", git=True)
    target = tmp_path / f"target-{replacement}"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("operator data\n", encoding="utf-8")
    moved_git = tmp_path / f"moved-{replacement}.git"
    registry = ProjectRegistry(tmp_path / f"projects-{replacement}.json")
    original_run = projects.subprocess.run

    def run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if "diff" in args and "--cached" in args and "--quiet" in args:
            (target / ".git").rename(moved_git)
            if replacement == "git-file":
                (target / ".git").write_text(f"gitdir: {moved_git}\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                args, 2, stdout="", stderr="simulated staged inspection failure"
            )
        return original_run(args, **kwargs)

    monkeypatch.setattr(projects.subprocess, "run", run)
    with pytest.raises(ProjectError, match="cannot inspect staged Symphony files"):
        projects.create_or_adopt_project(
            target,
            source=source,
            project_id=f"rollback-{replacement}",
            port=12220 if replacement == "git-file" else 12221,
            registry=registry,
        )

    assert sentinel.read_text(encoding="utf-8") == "operator data\n"
    assert sorted(path.name for path in target.iterdir()) == ["keep.txt"]
    assert moved_git.is_dir()
    assert registry.load() == []
