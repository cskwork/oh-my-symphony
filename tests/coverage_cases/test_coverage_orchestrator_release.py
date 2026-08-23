"""Coverage contracts for release-contract lifecycle."""
# ruff: noqa: F405

from tests.coverage_cases._orchestrator_support import *  # noqa: F403


def test_strict_release_loaders_reject_duplicate_and_unhashable_keys() -> None:
    with pytest.raises(release_contracts._DuplicateJsonKeyError, match="duplicate"):
        release_contracts._load_strict_json('{"key": 1, "key": 2}')
    with pytest.raises(yaml.constructor.ConstructorError, match="unhashable"):
        release_contracts._load_strict_yaml(b"? [a, b]\n: value\n")


def test_release_contract_loader_reports_unreadable_and_non_mapping_documents(
    tmp_path: Path,
) -> None:
    unreadable = SimpleNamespace(
        is_file=lambda: True,
        read_bytes=lambda: (_ for _ in ()).throw(OSError("denied")),
    )
    assert (
        "cannot read release contract"
        in release_contracts._load_contract(cast(Any, unreadable))[3][0]
    )
    path = tmp_path / "release-contract.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    assert release_contracts._load_contract(path)[3] == [
        "release-contract.yaml must contain a mapping"
    ]


def test_release_contract_loader_reports_all_malformed_domain_shapes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "release-contract.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "target_branch": "",
                "finalizer_ticket": "APP-FINAL",
                "implementation_tickets": ["bad/id", "APP-1", "APP-1"],
                "launch": {"command": "", "ready_url": ""},
                "runner": {
                    "sources": [
                        "not-a-mapping",
                        {"path": "-unsafe", "sha256": "bad"},
                        {"path": "tools/missing.py"},
                        {"path": "tools/runner.py", "sha256": "a" * 64},
                        {"path": "tools/runner.py", "sha256": "b" * 64},
                    ]
                },
                "viewports": {
                    "bad/id": {"width": 1, "height": 1},
                    "desktop": "not-a-mapping",
                    "mobile": {"width": True, "height": 0},
                },
                "checks": [
                    "not-a-mapping",
                    {},
                    {
                        "id": "bad/id",
                        "kind": "unknown",
                        "description": "",
                        "repair_group": "bad/id",
                        "required_viewports": [],
                    },
                    {
                        "id": "valid-check",
                        "kind": "feature",
                        "description": "valid",
                        "repair_group": "feature",
                        "required_viewports": ["mobile", "mobile", "unknown"],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    contract, _digest, _raw, errors = release_contracts._load_contract(path)
    assert contract is None
    expected_fragments = (
        "target_branch must be a non-empty string",
        "unsafe implementation ticket",
        "duplicate implementation ticket",
        "launch.command",
        "launch.ready_url",
        "runner is missing field",
        "runner.command",
        "source[0] must be a mapping",
        "source[1] path is unsafe",
        "source[1] sha256",
        "duplicate source path",
        "unsafe viewport identifier",
        "viewport 'desktop' must be a mapping",
        "positive integer",
        "check[0] must be a mapping",
        "unknown kind",
        "description is empty",
        "unsafe repair_group",
        "required_viewports must be a non-empty list",
        "unknown viewport",
        "duplicate required viewport",
    )
    assert all(
        any(fragment in error for error in errors) for fragment in expected_fragments
    )

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["target_branch"] = "-unsafe"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    assert any(
        "target_branch is unsafe" in error
        for error in release_contracts._load_contract(path)[3]
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (PurePosixPath("boards/team"), PurePosixPath("boards/team")),
        ("../outside", None),
        ("/absolute", None),
        ("flag\tvalue", None),
        ("valid path/file.json", "valid path/file.json"),
    ],
)
def test_release_paths_are_repo_relative_and_mounts_accept_posix_objects(
    value: object, expected: object
) -> None:
    if isinstance(value, PurePosixPath):
        assert release_contracts._safe_board_mount(value) == expected
    else:
        assert release_contracts._safe_repo_relative_path(value) == expected


def test_release_evidence_loader_reports_missing_non_object_and_unknown_fields(
    tmp_path: Path,
) -> None:
    assert (
        "missing verifier"
        in release_contracts._load_evidence(tmp_path / "missing.json")[1][0]
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text("[]", encoding="utf-8")
    assert release_contracts._load_evidence(evidence)[1] == [
        "release-evidence.json must contain an object"
    ]
    evidence.write_text('{"schema_version": 1, "extra": true}', encoding="utf-8")
    _data, errors = release_contracts._load_evidence(evidence)
    assert any("unknown field" in error for error in errors)
    assert any("missing field" in error for error in errors)


def test_release_artifact_validation_rejects_bad_shapes_and_unsafe_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verifier_root = tmp_path / "docs" / "VERIFY-1"
    verifier_root.mkdir(parents=True)
    errors: list[str] = []
    assert (
        release_contracts._validated_artifact(
            workspace_root=tmp_path,
            verifier_root=verifier_root,
            raw="not-an-object",
            subject="check",
            errors=errors,
        )
        is None
    )
    assert "must be an object" in errors[-1]

    for raw, expected in (
        ({"path": str(tmp_path.resolve()), "sha256": "a" * 64}, "unsafe or empty"),
        ({"path": "docs/VERIFY-1/proof", "sha256": "bad"}, "64 hexadecimal"),
        ({"path": "docs/VERIFY-1/missing", "sha256": "a" * 64}, "must exist"),
    ):
        errors = []
        assert (
            release_contracts._validated_artifact(
                workspace_root=tmp_path,
                verifier_root=verifier_root,
                raw=raw,
                subject="check",
                errors=errors,
            )
            is None
        )
        assert expected in errors[-1]

    directory = verifier_root / "directory"
    directory.mkdir()
    errors = []
    assert (
        release_contracts._validated_artifact(
            workspace_root=tmp_path,
            verifier_root=verifier_root,
            raw={"path": "docs/VERIFY-1/directory", "sha256": "a" * 64},
            subject="check",
            errors=errors,
        )
        is None
    )
    assert "not a regular file" in errors[-1]

    empty = verifier_root / "empty.log"
    empty.touch()
    errors = []
    assert (
        release_contracts._validated_artifact(
            workspace_root=tmp_path,
            verifier_root=verifier_root,
            raw={"path": "docs/VERIFY-1/empty.log", "sha256": "a" * 64},
            subject="check",
            errors=errors,
        )
        is None
    )
    assert "is empty" in errors[-1]

    monkeypatch.setattr(release_contracts, "is_git_stageable_path", lambda *_a: False)
    proof = verifier_root / "proof.log"
    proof.write_text("proof", encoding="utf-8")
    errors = []
    assert (
        release_contracts._validated_artifact(
            workspace_root=tmp_path,
            verifier_root=verifier_root,
            raw={"path": "docs/VERIFY-1/proof.log", "sha256": "a" * 64},
            subject="check",
            errors=errors,
        )
        is None
    )
    assert "must be tracked or Git-stageable" in errors[-1]

    monkeypatch.setattr(release_contracts, "is_git_stageable_path", lambda *_a: True)
    monkeypatch.setattr(
        release_contracts,
        "_hash_file",
        lambda _path: (_ for _ in ()).throw(OSError("unreadable")),
    )
    errors = []
    assert (
        release_contracts._validated_artifact(
            workspace_root=tmp_path,
            verifier_root=verifier_root,
            raw={"path": "docs/VERIFY-1/proof.log", "sha256": "a" * 64},
            subject="check",
            errors=errors,
        )
        is None
    )
    assert "cannot be read" in errors[-1]


def test_release_artifact_validation_reports_post_resolution_stat_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verifier_root = tmp_path / "docs" / "VERIFY-1"
    verifier_root.mkdir(parents=True)
    artifact = verifier_root / "proof.log"
    artifact.write_text("proof", encoding="utf-8")
    original_resolve = Path.resolve
    original_is_file = Path.is_file
    original_stat = Path.stat

    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: (
            artifact if path == artifact else original_resolve(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: True if path == artifact else original_is_file(path),
    )
    monkeypatch.setattr(
        Path,
        "stat",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("vanished"))
            if path == artifact
            else original_stat(path, *args, **kwargs)
        ),
    )
    errors: list[str] = []
    assert (
        release_contracts._validated_artifact(
            workspace_root=tmp_path,
            verifier_root=verifier_root,
            raw={
                "path": "docs/VERIFY-1/proof.log",
                "sha256": "a" * 64,
            },
            subject="check",
            errors=errors,
        )
        is None
    )
    assert "cannot be inspected" in errors[-1]


def test_git_stageable_and_entry_text_helpers_report_operator_readable_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    errors: list[str] = []
    assert not release_contracts._require_git_stageable_path(
        workspace_root=tmp_path / "repo",
        candidate=tmp_path / "outside",
        subject="artifact",
        errors=errors,
    )
    assert "not contained" in errors[-1]

    monkeypatch.setattr(release_contracts, "is_git_stageable_path", lambda *_a: None)
    errors = []
    assert not release_contracts._require_git_stageable_path(
        workspace_root=tmp_path,
        candidate=tmp_path / "artifact",
        repo_relative_path="artifact",
        subject="artifact",
        errors=errors,
    )
    assert "could not be determined" in errors[-1]
    assert (
        release_contracts._entry_text({"name": " chosen "}, "name", "fallback")
        == "chosen"
    )
    assert release_contracts._entry_text(" direct ", "name", "fallback") == "direct"
    assert release_contracts._entry_text({}, "name", "fallback") == "fallback"


def test_native_result_loader_rejects_unbound_and_malformed_checks(
    tmp_path: Path,
) -> None:
    results = tmp_path / "native.json"
    results.write_text(
        json.dumps(
            {
                "schema_version": True,
                "verifier_ticket": "wrong",
                "contract_sha256": "wrong",
                "target_branch": "wrong",
                "target_sha": "wrong",
                "checks": [
                    "not-an-object",
                    {"id": "unsafe/id", "status": "PASS"},
                    {"id": "check-1", "status": "UNKNOWN"},
                    {"id": "check-2", "status": "PASS"},
                    {"id": "check-2", "status": "FAIL"},
                ],
            }
        ),
        encoding="utf-8",
    )
    errors: list[str] = []
    loaded = release_contracts._load_native_results(
        workspace_root=tmp_path,
        path_text="native.json",
        verifier_ticket="VERIFY-1",
        contract_hash="a" * 64,
        target_branch="main",
        target_sha="b" * 40,
        errors=errors,
    )
    assert loaded == {"check-2": "PASS"}
    assert any("schema_version" in error for error in errors)
    assert any("duplicate check id" in error for error in errors)

    results.write_text('{"checks": {}}', encoding="utf-8")
    errors = []
    assert (
        release_contracts._load_native_results(
            workspace_root=tmp_path,
            path_text="native.json",
            verifier_ticket="VERIFY-1",
            contract_hash="a" * 64,
            target_branch="main",
            target_sha="b" * 40,
            errors=errors,
        )
        is None
    )
    assert any("checks must be a list" in error for error in errors)

    results.write_text("[]", encoding="utf-8")
    errors = []
    assert (
        release_contracts._load_native_results(
            workspace_root=tmp_path,
            path_text="native.json",
            verifier_ticket="VERIFY-1",
            contract_hash="a" * 64,
            target_branch="main",
            target_sha="b" * 40,
            errors=errors,
        )
        is None
    )
    assert errors == ["native runner results must be a valid JSON object"]


def test_release_notes_distinguish_green_evidence_and_repairable_failure() -> None:
    assert release_contracts._note_text(errors=(), failures=()).startswith(
        "Release contract validated"
    )
    failure = release_contracts.RepairableFailure(
        check_id="visual",
        repair_group="ui",
        description="Visual quality",
        expected="aligned",
        actual="misaligned",
        repro="open page",
    )
    note = release_contracts._note_text(errors=("bad evidence",), failures=(failure,))
    assert "Evidence errors" in note
    assert "`visual` (ui): misaligned" in note


def test_release_workspace_board_paths_fail_closed_without_escaping_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    repository.mkdir()
    workspace.mkdir()
    outside = tmp_path / "external-board"
    outside.mkdir()
    nested = repository / "boards" / "team"
    nested.mkdir(parents=True)

    assert (
        release_contracts._repository_relative_board_root(
            repository_root=repository, board_root=outside
        )
        is None
    )
    assert (
        release_contracts._repository_relative_board_root(
            repository_root=repository, board_root=repository
        )
        is None
    )
    assert release_contracts._repository_relative_board_root(
        repository_root=repository, board_root=Path("boards/team")
    ) == (PurePosixPath("boards/team"), nested.resolve())
    assert "does not exist" in release_contracts._configured_board_root_error(
        repository_root=repository, board_root=Path("missing")
    )
    board_file = repository / "board.txt"
    board_file.write_text("not a directory", encoding="utf-8")
    assert "must be a directory" in release_contracts._configured_board_root_error(
        repository_root=repository, board_root=board_file
    )
    assert "must be inside" in release_contracts._configured_board_root_error(
        repository_root=repository, board_root=outside
    )
    assert "below repository" in release_contracts._configured_board_root_error(
        repository_root=repository, board_root=repository
    )

    monkeypatch.setattr(release_contracts, "is_merged", lambda *_a: True)
    monkeypatch.setattr(release_contracts, "changed_paths_since", lambda *_a, **_k: [])
    errors = release_contracts.release_workspace_target_errors(
        workspace_root=workspace,
        repository_root=repository,
        target_sha="a" * 40,
        board_mount=PurePosixPath("../escape"),
    )
    assert any("mount is unsafe" in error for error in errors)


def test_release_workspace_mount_routing_validates_matching_and_external_boards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    board = repository / "kanban"
    external = tmp_path / "external"
    for path in (repository, workspace, board, external):
        path.mkdir(exist_ok=True)
    monkeypatch.setattr(release_contracts, "is_merged", lambda *_a: True)
    monkeypatch.setattr(release_contracts, "changed_paths_since", lambda *_a, **_k: [])

    monkeypatch.setattr(
        release_contracts,
        "_repository_relative_board_root",
        lambda **_kwargs: (PurePosixPath("kanban"), board),
    )
    monkeypatch.setattr(
        release_contracts, "_workspace_board_mount_error", lambda **_kwargs: None
    )
    errors = release_contracts.release_workspace_target_errors(
        workspace_root=workspace,
        repository_root=repository,
        target_sha="a" * 40,
        board_root=board,
        board_mount=PurePosixPath("wrong"),
    )
    assert any("must match kanban" in error for error in errors)

    monkeypatch.setattr(
        release_contracts, "_repository_relative_board_root", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        release_contracts,
        "_resolved_configured_board_root",
        lambda **_kwargs: external,
    )
    errors = release_contracts.release_workspace_target_errors(
        workspace_root=workspace,
        repository_root=repository,
        target_sha="a" * 40,
        board_root=external,
    )
    assert any("configured host board root" in error for error in errors)

    errors = release_contracts.release_workspace_target_errors(
        workspace_root=workspace,
        repository_root=repository,
        target_sha="a" * 40,
        board_root=external,
        board_mount=PurePosixPath("mounted-board"),
    )
    assert errors == ()

    monkeypatch.setattr(
        release_contracts,
        "_workspace_board_mount_error",
        lambda **_kwargs: "workspace board mount is wrong",
    )
    errors = release_contracts.release_workspace_target_errors(
        workspace_root=workspace,
        repository_root=repository,
        target_sha="a" * 40,
        board_root=external,
        board_mount=PurePosixPath("mounted-board"),
    )
    assert errors == ("release workspace board mount is wrong",)


def test_workspace_board_mount_reports_junction_probe_resolve_and_target_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    configured = tmp_path / "configured"
    workspace.mkdir()
    configured.mkdir()
    entry = workspace / "kanban"
    entry.mkdir()

    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: (
            (_ for _ in ()).throw(OSError("unsupported")) if path == entry else False
        ),
    )
    error = release_contracts._workspace_board_mount_error(
        workspace_root=workspace,
        relative_root=PurePosixPath("kanban"),
        configured_root=configured,
    )
    assert error is not None and "must be a symlink" in error

    monkeypatch.setattr(Path, "is_symlink", lambda path: path == entry)
    original_resolve = Path.resolve

    def fail_entry_resolve(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path == entry:
            raise OSError("broken")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_entry_resolve)
    error = release_contracts._workspace_board_mount_error(
        workspace_root=workspace,
        relative_root=PurePosixPath("kanban"),
        configured_root=configured,
    )
    assert error is not None and "could not be resolved" in error

    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *_a, **_k: tmp_path / "different" if path == entry else path,
    )
    error = release_contracts._workspace_board_mount_error(
        workspace_root=workspace,
        relative_root=PurePosixPath("kanban"),
        configured_root=configured,
    )
    assert error is not None and "resolved to" in error
    assert (
        release_contracts._configured_board_root_error(
            repository_root=tmp_path, board_root=configured
        )
        == "configured host board root is invalid"
    )


def test_board_root_resolution_error_and_matching_mount_are_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "configured"
    configured.mkdir()
    original_resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(OSError("denied"))
            if path == configured
            else original_resolve(path, *args, **kwargs)
        ),
    )
    assert "could not be resolved" in release_contracts._configured_board_root_error(
        repository_root=tmp_path, board_root=configured
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    entry = workspace / "kanban"
    monkeypatch.setattr(Path, "is_junction", lambda path: path == entry)
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(Path, "exists", lambda path: path == entry)
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, *_a, **_k: configured if path == entry else path,
    )
    assert (
        release_contracts._workspace_board_mount_error(
            workspace_root=workspace,
            relative_root=PurePosixPath("kanban"),
            configured_root=configured,
        )
        is None
    )


def test_release_validation_covers_unsafe_identity_missing_authority_and_source_edges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = release_contracts.validate_release_contract(
        workspace_root=workspace,
        repository_root=workspace,
        verifier_ticket="../unsafe",
        configured_target_branch="-unsafe",
    )
    assert "current verifier ticket identifier is unsafe" in result.evidence_errors
    assert "configured target branch is unsafe or empty" in result.evidence_errors

    contract_path = workspace / "release-contract.yaml"
    contract_path.write_text("placeholder", encoding="utf-8")
    source_hash = release_contracts._hash_bytes(b"runner")
    contract = release_contracts.ReleaseContract(
        target_branch="main",
        finalizer_ticket="APP-FINAL",
        implementation_tickets=(),
        launch={"command": "run"},
        runner_command="python tools/runner.py",
        runner_sources=(
            release_contracts.ReleaseRunnerSource("tools/runner.py", source_hash),
        ),
        viewports=("desktop",),
        checks=(),
    )
    monkeypatch.setattr(
        release_contracts,
        "_load_contract",
        lambda _path: (contract, "c" * 64, b"contract", []),
    )
    monkeypatch.setattr(
        release_contracts, "resolve_local_branch_commit", lambda *_a: "a" * 40
    )
    monkeypatch.setattr(
        release_contracts, "release_workspace_target_errors", lambda **_kwargs: ()
    )
    monkeypatch.setattr(release_contracts, "read_commit_blob", lambda *_a: None)
    (workspace / "docs" / "VERIFY-1" / "qa").mkdir(parents=True)
    result = release_contracts.validate_release_contract(
        workspace_root=workspace,
        repository_root=workspace,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    assert any("target commit is missing" in error for error in result.evidence_errors)
    assert any("runner source is missing" in error for error in result.evidence_errors)
    assert any(
        "must be contained in the workspace" in error
        for error in result.evidence_errors
    )
    assert any(
        "release-evidence.json must exist" in error for error in result.evidence_errors
    )

    source_path = workspace / "tools" / "runner.py"
    source_path.mkdir(parents=True)
    monkeypatch.setattr(
        release_contracts,
        "read_commit_blob",
        lambda _repo, _sha, path: (
            b"contract" if path == "release-contract.yaml" else b"runner"
        ),
    )
    result = release_contracts.validate_release_contract(
        workspace_root=workspace,
        repository_root=workspace,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    assert any(
        "not a regular workspace file" in error for error in result.evidence_errors
    )

    source_path.rmdir()
    source_path.write_bytes(b"runner")
    monkeypatch.setattr(
        release_contracts,
        "_hash_file",
        lambda _path: (_ for _ in ()).throw(OSError("read denied")),
    )
    result = release_contracts.validate_release_contract(
        workspace_root=workspace,
        repository_root=workspace,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    assert any(
        "cannot be read from the workspace" in error for error in result.evidence_errors
    )


def test_release_validation_reports_nonobject_runner_checks_and_native_status_drift(
    tmp_path: Path,
) -> None:
    from tests.test_release_contracts import _git, _write_valid_release

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Coverage Test")
    _git(repo, "config", "user.email", "coverage@example.test")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "branch", "symphony/APP-1")
    _contract, evidence = _write_valid_release(repo)
    evidence_path = repo / "docs" / "VERIFY-1" / "qa" / "release-evidence.json"
    original = json.loads(json.dumps(evidence))
    evidence["runner"] = None
    evidence["checks"] = {}
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    result = release_contracts.validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    assert "release evidence runner must be an object" in result.evidence_errors
    assert "release evidence checks must be a list" in result.evidence_errors

    first_check = original["checks"][0]
    first_check["status"] = "FAIL"
    evidence_path.write_text(json.dumps(original), encoding="utf-8")
    result = release_contracts.validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )
    assert any("status for" in error for error in result.evidence_errors)


def test_release_validation_surfaces_malformed_bound_evidence(
    tmp_path: Path,
) -> None:
    from tests.test_release_contracts import _git, _write_valid_release

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Coverage Test")
    _git(repo, "config", "user.email", "coverage@example.test")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "branch", "symphony/APP-1")
    _contract, evidence = _write_valid_release(repo)
    evidence["verifier_ticket"] = "WRONG"
    evidence["contract_sha256"] = "bad"
    evidence["target_branch"] = "wrong"
    evidence["target_sha"] = "short"
    evidence["runner"] = {"exit_code": True}
    checks = evidence["checks"]
    assert isinstance(checks, list)
    first = checks[0]
    assert isinstance(first, dict)
    first.update(
        {
            "status": "",
            "expected": "",
            "actual": "",
            "repro": "",
            "viewports": "desktop",
            "artifacts": [],
        }
    )
    second = checks[1]
    assert isinstance(second, dict)
    second.update(
        {
            "status": "UNKNOWN",
            "viewports": ["desktop", "desktop", "unknown"],
        }
    )
    checks.extend(["not-an-object", {"id": "bad/id"}, {"id": "extra-check"}])
    evidence["console_errors"] = "not-a-list"
    evidence["failed_requests"] = [
        {"expected": "none", "actual": "500", "repro": "reload"}
    ]
    evidence_path = repo / "docs" / "VERIFY-1" / "qa" / "release-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    _git(repo, "branch", "-D", "symphony/APP-1")

    result = release_contracts.validate_release_contract(
        workspace_root=repo,
        repository_root=repo,
        verifier_ticket="VERIFY-1",
        configured_target_branch="main",
    )

    assert not result.passed
    expected_fragments = (
        "verifier_ticket does not match",
        "contract_sha256 does not match",
        "target_branch does not match",
        "target_sha must be a full commit SHA",
        "runner is missing field",
        "runner.name must be a non-empty string",
        "runner.exit_code must be an integer",
        "check[6] must be an object",
        "unsafe id",
        "unknown check",
        "status must be non-empty",
        "viewports must be a string list",
        "duplicate viewport",
        "unknown viewport",
        "console_errors must be a list",
    )
    assert all(
        any(fragment in error for error in result.evidence_errors)
        for fragment in expected_fragments
    )
    assert any(
        failure.check_id == "ancestry:APP-1" for failure in result.repairable_failures
    )
    assert release_contracts.inspect_release_contract(
        repo / "release-contract.yaml", configured_target_branch="dev"
    ) == ("release contract target_branch does not match the configured target branch",)


def test_target_release_identity_fails_closed_for_missing_and_invalid_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        release_contracts, "resolve_local_branch_commit", lambda *_a: None
    )
    missing_branch = release_contracts.resolve_target_release_identity(
        repository_root=tmp_path, configured_target_branch="main"
    )
    assert "resolvable local branch" in missing_branch.errors[0]

    monkeypatch.setattr(
        release_contracts, "resolve_local_branch_commit", lambda *_a: "a" * 40
    )
    monkeypatch.setattr(release_contracts, "read_commit_blob", lambda *_a: None)
    missing_contract = release_contracts.resolve_target_release_identity(
        repository_root=tmp_path, configured_target_branch="main"
    )
    assert "does not contain" in missing_contract.errors[0]

    monkeypatch.setattr(
        release_contracts,
        "read_commit_blob",
        lambda *_a: b"target_branch: dev\nfinalizer_ticket: '../unsafe'\n",
    )
    invalid = release_contracts.resolve_target_release_identity(
        repository_root=tmp_path, configured_target_branch="main"
    )
    assert any("target_branch" in error for error in invalid.errors)
    assert any("finalizer_ticket" in error for error in invalid.errors)


def test_release_cycle_policy_uses_explicit_lanes_labels_and_stable_ids() -> None:
    issue = _issue(
        labels=(
            " APP-RELEASE ",
            "release-contract-sha256-B",
            "",
            "release-contract-sha256-a",
        ),
        blocked_by=(
            BlockerRef(None, "VERIFY-1", "Done"),
            BlockerRef("FIX-1", None, "Done"),
        ),
    )
    assert release_cycle.normalized_label_set(issue) == {
        "app-release",
        "release-contract-sha256-a",
        "release-contract-sha256-b",
    }
    assert release_cycle._blocker_identifiers(issue) == ["VERIFY-1", "FIX-1"]
    assert release_cycle.release_expected_hash_labels(issue) == (
        "release-contract-sha256-a",
        "release-contract-sha256-b",
    )
    identifier = release_cycle._release_cycle_item_identifier(
        prefix="QUALITY",
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="cycle",
        item_role="repair",
        item_key="ui",
    )
    assert identifier == release_cycle._release_cycle_item_identifier(
        prefix="QUALITY",
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="cycle",
        item_role="repair",
        item_key="ui",
    )
    assert identifier.startswith("QUALITY-")

    cfg = _cfg(active=("Build", "Verify"), terminal=("Rejected", "Shipped"))
    assert release_cycle.has_active_verify_lane(cfg)
    assert release_cycle.has_release_finalizer_lane(cfg)
    assert release_cycle.release_failure_target_state(cfg) == "Rejected"
    assert release_cycle.release_repair_state(cfg) == "Build"
    assert release_cycle.release_verifier_state(cfg) == "Verify"
    assert release_cycle.is_release_success_state(cfg, "Shipped")
    assert release_cycle.has_release_success_terminal(cfg)
    assert (
        release_cycle.release_failure_target_state(_cfg(terminal=("Done", "Archive")))
        == ""
    )


def test_release_cycle_refuses_missing_verify_lane_and_non_file_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(active=("Build",), tracker_kind="linear")
    with pytest.raises(SymphonyError, match="active Verify lane"):
        release_cycle.release_verifier_state(cfg)

    client = SimpleNamespace(closed=False)

    def close() -> None:
        client.closed = True

    client.close = close
    monkeypatch.setattr(release_cycle, "build_tracker_client", lambda _cfg: client)
    with pytest.raises(SymphonyError, match="atomic local file tracker"):
        release_cycle._file_tracker(cfg)
    assert client.closed


def test_release_ticket_token_fails_closed_when_ticket_is_missing_or_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TokenClient:
        def __init__(self, path: Any) -> None:
            self.path = path
            self.closed = False

        def find_path(self, _identifier: str) -> Any:
            return self.path

        def close(self) -> None:
            self.closed = True

    missing = TokenClient(None)
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: missing)
    with pytest.raises(SymphonyError, match="cannot find board ticket"):
        release_cycle.release_ticket_version_token(_cfg(), "APP-FINAL")
    assert missing.closed

    bad_path = SimpleNamespace(
        open=lambda *_a, **_k: (_ for _ in ()).throw(OSError("denied"))
    )
    unreadable = TokenClient(bad_path)
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: unreadable)
    with pytest.raises(SymphonyError, match="cannot read board ticket"):
        release_cycle.release_ticket_version_token(_cfg(), "APP-FINAL")
    assert unreadable.closed


def test_release_ticket_token_detects_atomic_replacement_during_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ticket = tmp_path / "APP-FINAL.md"
    ticket.write_text("ticket", encoding="utf-8")
    client = SimpleNamespace(find_path=lambda _identifier: ticket, close=lambda: None)
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: client)
    stats = iter(
        [
            SimpleNamespace(st_dev=1, st_ino=1, st_mtime_ns=1, st_size=6),
            SimpleNamespace(st_dev=1, st_ino=2, st_mtime_ns=2, st_size=6),
        ]
    )
    monkeypatch.setattr(release_cycle.os, "fstat", lambda _fd: next(stats))
    with pytest.raises(SymphonyError, match="changed while completion was observed"):
        release_cycle.release_ticket_version_token(_cfg(), "APP-FINAL")


def test_release_cycle_writes_fail_closed_on_missing_or_unpersisted_tickets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = release_cycle.ReleaseCycleService(_cfg())
    issue = _issue("VERIFY-1", state="Verify")
    gate = _gate()

    client = _TicketClient([None])
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: client)
    with pytest.raises(SymphonyError, match="cannot be read"):
        service.restore_verifier_gate_labels(issue=issue, gate=gate)
    assert client.closed

    client = _TicketClient([issue, issue])
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: client)
    with pytest.raises(SymphonyError, match="not durably reconciled"):
        service.restore_verifier_gate_labels(issue=issue, gate=gate)

    client = _TicketClient([None])
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: client)
    with pytest.raises(SymphonyError, match="could not be read before rewind"):
        service.rewind_transition(
            issue=issue, producing_state="Build", note_body="rerun checks"
        )

    client = _TicketClient([issue, issue])
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: client)
    with pytest.raises(SymphonyError, match="rewind was not durably persisted"):
        service.rewind_transition(
            issue=issue, producing_state="Build", note_body="rerun checks"
        )


def test_release_cycle_reopen_and_reconcile_report_operator_actionable_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = release_cycle.ReleaseCycleService(_cfg())
    finalizer = _issue("APP-FINAL", labels=("app-release-finalizer",))
    verifier = _issue("VERIFY-1", state="Verify")
    gate = _gate()

    client = _TicketClient([None, finalizer])
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: client)
    with pytest.raises(SymphonyError, match="binding cannot be reopened"):
        service.reopen_after_target_change(
            finalizer=finalizer,
            gate=gate,
            expected_contract_sha256="b" * 64,
            reason="target moved",
        )

    client = _TicketClient([verifier, finalizer, verifier, finalizer])
    monkeypatch.setattr(release_cycle, "_file_tracker", lambda _cfg: client)
    with pytest.raises(SymphonyError, match="reopen was not durably persisted"):
        service.reopen_after_target_change(
            finalizer=finalizer,
            gate=gate,
            expected_contract_sha256="b" * 64,
            reason="target moved",
        )

    non_file = release_cycle.ReleaseCycleService(_cfg(tracker_kind="linear"))
    result = non_file.reconcile(
        verifier,
        release_contracts.ReleaseValidationResult(
            passed=False,
            evidence_errors=("bad",),
            repairable_failures=(),
            target_branch="main",
            target_sha="c" * 40,
            contract_sha256="b" * 64,
            fingerprint="fingerprint",
            finalizer_ticket="APP-FINAL",
            note_text="bad",
        ),
        "codex",
    )
    assert not result.passed and "atomic local" in result.error

    no_verify = release_cycle.ReleaseCycleService(_cfg(active=("Build",)))
    result = no_verify.reconcile(
        verifier,
        release_contracts.ReleaseValidationResult(
            passed=False,
            evidence_errors=("bad",),
            repairable_failures=(),
            target_branch="main",
            target_sha="c" * 40,
            contract_sha256="b" * 64,
            fingerprint="fingerprint",
            finalizer_ticket="APP-FINAL",
            note_text="bad",
        ),
        "codex",
    )
    assert not result.passed and "Verify lane" in result.error


def test_release_cycle_reconcile_rejects_duplicate_repair_and_verifier_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair_labels = (
        "quality-fix",
        "release-fingerprint-fingerprint",
        "release-repair-group-ui",
    )
    repairs = [
        _issue("QUALITY-1", labels=repair_labels),
        _issue("QUALITY-2", labels=repair_labels),
    ]
    client = SimpleNamespace(
        fetch_issues_by_states=lambda _states: repairs,
        fetch_issue_full_by_id=lambda _identifier: None,
        create=lambda **_kwargs: None,
        update_fields=lambda *_a, **_k: None,
        close=lambda: None,
    )
    result = _run_cycle_reconcile(
        monkeypatch,
        client=client,
        registry=_CycleRegistry(),
        source=_issue("VERIFY-OLD", state="Verify"),
        validation=_cycle_validation(with_failure=True),
    )
    assert not result.passed and "multiple repair tickets" in result.error

    verifier_labels = (
        "release-cycle-verifier",
        "release-fingerprint-fingerprint",
    )
    verifiers = [
        _issue("VERIFY-1", labels=verifier_labels),
        _issue("VERIFY-2", labels=verifier_labels),
    ]
    client.fetch_issues_by_states = lambda _states: verifiers
    result = _run_cycle_reconcile(
        monkeypatch,
        client=client,
        registry=_CycleRegistry(),
        source=_issue("VERIFY-OLD", state="Verify"),
        validation=_cycle_validation(with_failure=False),
    )
    assert not result.passed and "multiple fresh verifier" in result.error


def test_release_cycle_reconcile_retries_reserved_repair_then_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []

    def create(**kwargs: Any) -> None:
        created.append(kwargs["identifier"])
        raise SymphonyError("peer race")

    registry = _CycleRegistry(repair=_cycle_item("repair", "QUALITY-1", "ui"))
    client = SimpleNamespace(
        fetch_issues_by_states=lambda _states: [],
        fetch_issue_full_by_id=lambda _identifier: None,
        create=create,
        update_fields=lambda *_a, **_k: None,
        close=lambda: None,
    )
    result = _run_cycle_reconcile(
        monkeypatch,
        client=client,
        registry=registry,
        source=_issue("VERIFY-OLD", state="Verify"),
        validation=_cycle_validation(with_failure=True),
    )
    assert not result.passed and "reserved release repair" in result.error
    assert created == ["QUALITY-1"]


def test_release_cycle_reconcile_preserves_partial_repair_then_requires_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair = _issue(
        "QUALITY-1",
        state="Todo",
        description="operator partial note",
        labels=(
            "quality-fix",
            "release-fingerprint-fingerprint",
            "release-repair-group-ui",
        ),
    )
    updates: list[dict[str, Any]] = []
    client = SimpleNamespace(
        fetch_issues_by_states=lambda _states: [repair],
        fetch_issue_full_by_id=lambda _identifier: None,
        create=lambda **_kwargs: None,
        update_fields=lambda _identifier, **fields: updates.append(fields),
        close=lambda: None,
    )
    result = _run_cycle_reconcile(
        monkeypatch,
        client=client,
        registry=_CycleRegistry(),
        source=_issue("VERIFY-OLD", state="Verify"),
        validation=_cycle_validation(with_failure=True),
    )
    assert not result.passed and "could not be read back" in result.error
    assert "Reconciled partial content" in updates[0]["description"]


@pytest.mark.parametrize("invalid_mode", ["collision", "reserved_missing", "invalid"])
def test_release_cycle_repair_creation_and_readback_errors_are_explicit(
    monkeypatch: pytest.MonkeyPatch, invalid_mode: str
) -> None:
    validation = _cycle_validation(with_failure=True)
    deterministic = release_cycle._release_cycle_item_identifier(
        prefix="QUALITY",
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="fingerprint",
        item_role="repair",
        item_key="ui",
    )
    collision = _issue(deterministic)
    invalid = _issue("QUALITY-1", state="Todo", description="incomplete")
    fetches = 0

    def fetch(identifier: str) -> Issue | None:
        nonlocal fetches
        fetches += 1
        if invalid_mode == "collision" and identifier == deterministic:
            return collision
        if invalid_mode == "invalid" and fetches >= 2:
            return invalid
        return None

    client = SimpleNamespace(
        fetch_issues_by_states=lambda _states: [],
        fetch_issue_full_by_id=fetch,
        create=lambda **_kwargs: None,
        update_fields=lambda *_a, **_k: None,
        close=lambda: None,
    )
    result = _run_cycle_reconcile(
        monkeypatch,
        client=client,
        registry=_CycleRegistry(),
        source=_issue("VERIFY-OLD", state="Verify"),
        validation=validation,
    )
    assert not result.passed
    if invalid_mode == "collision":
        assert "already unowned" in result.error
    elif invalid_mode == "reserved_missing":
        assert "reserved release repair" in result.error
    else:
        assert "durably reconciled" in result.error


@pytest.mark.parametrize("mode", ["recorded", "collision", "reserved"])
def test_release_cycle_verifier_reservation_failures_are_explicit(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    validation = _cycle_validation(with_failure=False)
    deterministic = release_cycle._release_cycle_item_identifier(
        prefix="RELEASE-VERIFY",
        finalizer_identifier="APP-FINAL",
        cycle_fingerprint="fingerprint",
        item_role="verifier",
        item_key="fresh-verifier",
    )
    fetches = 0

    def fetch(identifier: str) -> Issue | None:
        nonlocal fetches
        fetches += 1
        if mode == "collision" and identifier == deterministic:
            return _issue(identifier)
        return None

    client = SimpleNamespace(
        fetch_issues_by_states=lambda _states: [],
        fetch_issue_full_by_id=fetch,
        create=lambda **_kwargs: (_ for _ in ()).throw(SymphonyError("race")),
        update_fields=lambda *_a, **_k: None,
        close=lambda: None,
    )
    registry = _CycleRegistry(
        verifier=_cycle_item("verifier", "VERIFY-RESERVED", "fresh-verifier")
        if mode == "recorded"
        else None
    )
    result = _run_cycle_reconcile(
        monkeypatch,
        client=client,
        registry=registry,
        source=_issue("VERIFY-OLD", state="Verify"),
        validation=validation,
    )
    assert not result.passed
    expected = (
        "already unowned" if mode == "collision" else "reserved fresh release verifier"
    )
    assert expected in result.error


@pytest.mark.parametrize(
    "mode",
    [
        "missing_readback",
        "invalid_readback",
        "missing_finalizer",
        "unlabeled_finalizer",
        "bad_relink",
    ],
)
def test_release_cycle_final_readback_and_finalizer_guards(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    source = _issue("VERIFY-OLD", state="Verify", request="REQ-1")
    verifier = _valid_cycle_verifier(source)
    finalizer = _issue(
        "APP-FINAL",
        labels=("app-release-finalizer",),
        blocked_by=(BlockerRef(None, source.identifier, "Verify"),),
    )
    calls: dict[str, int] = {}

    def fetch(identifier: str) -> Issue | None:
        calls[identifier] = calls.get(identifier, 0) + 1
        if identifier == verifier.identifier:
            if mode == "missing_readback":
                return None
            if mode == "invalid_readback":
                return replace(verifier, labels=())
            return verifier
        if identifier == finalizer.identifier:
            if mode == "missing_finalizer":
                return None
            if mode == "unlabeled_finalizer":
                return replace(finalizer, labels=())
            return finalizer
        return None

    client = SimpleNamespace(
        fetch_issues_by_states=lambda _states: [verifier],
        fetch_issue_full_by_id=fetch,
        create=lambda **_kwargs: None,
        update_fields=lambda *_a, **_k: None,
        close=lambda: None,
    )
    result = _run_cycle_reconcile(
        monkeypatch,
        client=client,
        registry=_CycleRegistry(),
        source=source,
        validation=_cycle_validation(with_failure=False),
    )
    assert not result.passed
    expected = {
        "missing_readback": "could not be read back",
        "invalid_readback": "could not be durably reconciled",
        "missing_finalizer": "does not exist",
        "unlabeled_finalizer": "lacks app-release-finalizer",
        "bad_relink": "not durably relinked",
    }[mode]
    assert expected in result.error
