"""Immutable Git-object trust boundary for AIDT routing."""

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from symphony.aidt_routing.contract import (
    MAX_OBSERVATION_BYTES,
    MAX_SERVICE_OBJECT_BYTES,
    AidtRoutingFailure,
    load_routing_settings,
)
from symphony.aidt_routing.git_objects import (
    GIT_BLOB_STDOUT_CAP,
    GIT_METADATA_FILE_CAP,
    GIT_OBJECT_TRUST_SCHEMA,
    GIT_PATH_STDOUT_CAP,
    GIT_STDERR_CAP,
    GIT_TIMEOUT_SECONDS,
    GIT_TOKEN_STDOUT_CAP,
    GIT_TREE_RECORD_CAP,
    GitCommandResult,
    _checked_result,
    _decode_blob,
    _decode_scalar,
    _default_git_runner,
    _parse_tree_record,
    _read_metadata_file,
    observe_catalog,
    recheck_catalog,
)
from tests.aidt_routing_support import (
    FrozenGitRepository,
    frozen_git_repository,
    git_command,
    routing_config,
    service_config,
    service_definition,
)


def _repository(tmp_path: Path) -> FrozenGitRepository:
    root = tmp_path / "aidt"
    root.mkdir()
    return frozen_git_repository(
        root,
        {
            "pom.xml": "<project />",
            "src/Route.java": "GET /v-api/learning routeSymbol",
            "src/Domain.java": "math learning center",
            "staged.txt": "base staged content",
            ".gitignore": "ignored.txt\n",
        },
    )


def _object_service() -> dict[str, Any]:
    return service_definition(
        routes=[
            {
                "id": "learning-route",
                "file": "src/Route.java",
                "method": "GET",
                "endpoint": "/v-api/learning",
                "symbols": ["routeSymbol"],
            }
        ],
        domains=[
            {
                "id": "learning-domain",
                "file": "src/Domain.java",
                "terms": ["math learning center"],
            }
        ],
    )


def _settings_for_service(repo: FrozenGitRepository, service: dict[str, Any]):
    return _settings_for_services(repo.root, [service])


def _settings_for_services(root: Path, services: list[dict[str, Any]]):
    board = root.parent / "board"
    board.mkdir(exist_ok=True)
    settings = load_routing_settings(
        service_config(board, routing_config(root, services))
    )
    assert settings is not None
    return settings


def _routing_settings(repo: FrozenGitRepository):
    return _settings_for_service(repo, _object_service())


def _file_snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def _dirty_snapshot(repo: FrozenGitRepository) -> dict[str, object]:
    git_dir = repo.checkout / ".git"
    head_name = git_command(repo.checkout, "symbolic-ref", "HEAD")
    head_ref = git_dir / head_name
    marker = repo.checkout / "pom.xml"
    return {
        "index": _file_snapshot(git_dir / "index"),
        "head_name": head_name,
        "head_ref": _file_snapshot(head_ref),
        "route": _file_snapshot(repo.checkout / "src/Route.java"),
        "staged": _file_snapshot(repo.checkout / "staged.txt"),
        "untracked": _file_snapshot(repo.checkout / "untracked.txt"),
        "ignored": _file_snapshot(repo.checkout / "ignored.txt"),
        "marker_target": os.readlink(marker),
        "marker_mtime": marker.lstat().st_mtime_ns,
    }


def _linked_worktree(tmp_path: Path) -> tuple[FrozenGitRepository, FrozenGitRepository]:
    origin_parent = tmp_path / "origin"
    origin_parent.mkdir()
    origin = _repository(origin_parent)
    root = tmp_path / "linked-aidt"
    root.mkdir()
    checkout = root / "viewer-api"
    git_command(
        origin.checkout,
        "worktree",
        "add",
        "--detach",
        "-q",
        str(checkout),
        origin.base_commit,
    )
    linked = FrozenGitRepository(
        root,
        checkout,
        origin.base_commit,
        git_command(checkout, "rev-parse", "HEAD"),
    )
    return origin, linked


def _sized_service_repository(
    root: Path,
    service_id: str,
    sizes: list[int],
) -> dict[str, Any]:
    files = {"pom.xml": "<project />"}
    routes: list[dict[str, Any]] = []
    for index, size in enumerate(sizes):
        path = f"src/Blob{index}.txt"
        files[path] = "x" * size
        routes.append(
            {
                "id": f"blob-{index}",
                "file": path,
                "method": "GET",
                "endpoint": f"/blob/{index}",
                "symbols": ["x"],
            }
        )
    frozen_git_repository(
        root,
        files,
        service_id=service_id,
        unrelated_head=False,
    )
    return service_definition(service_id, routes=routes)


def test_scalar_and_tree_protocols_accept_only_exact_binary_records() -> None:
    oid = b"a" * 40
    record = b"100644 blob " + oid + b"\tpom.xml\0"

    assert _decode_scalar(oid + b"\n") == "a" * 40
    assert _parse_tree_record(record, "pom.xml") == "a" * 40
    malformed = [
        (oid + b"\nextra", "git_protocol_invalid"),
        (b"100644 blob " + oid + b"\tpom.xml", "git_protocol_invalid"),
        (record + record, "git_protocol_invalid"),
        (b"120000 blob " + oid + b"\tpom.xml\0", "git_object_invalid"),
        (b"100644 tree " + oid + b"\tpom.xml\0", "git_object_invalid"),
        (b"160000 commit " + oid + b"\tpom.xml\0", "git_object_invalid"),
        (b"100644 blob " + oid + b"\tother.xml\0", "git_protocol_invalid"),
    ]
    for output, category in malformed:
        with pytest.raises(AidtRoutingFailure, match=category):
            if output.startswith(oid):
                _decode_scalar(output)
            else:
                _parse_tree_record(output, "pom.xml")


def test_fixed_aidt_prd_objects_ignore_unrelated_head(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    observation = observe_catalog(_routing_settings(repo))

    assert repo.head_commit != repo.base_commit
    assert observation.trust_schema == GIT_OBJECT_TRUST_SCHEMA
    assert len(observation.services) == 1
    service = observation.services[0]
    assert service.revision_ref == "refs/remotes/origin/aidt-prd"
    assert service.checkout_revision == repo.base_commit
    assert service.contents == {
        "src/Domain.java": "math learning center",
        "src/Route.java": "GET /v-api/learning routeSymbol",
    }
    assert "pom.xml" not in service.contents


def test_git_argv_and_environment_are_fixed_and_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path)
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/secret/object-override")

    def runner(
        argv: tuple[str, ...],
        environment: Any,
        timeout: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> GitCommandResult:
        calls.append((argv, dict(environment)))
        return _default_git_runner(
            argv, environment, timeout, stdout_cap, stderr_cap
        )

    observe_catalog(_routing_settings(repo), git_runner=runner)

    forbidden = {"status", "diff", "ls-files", "hash-object", "fetch"}
    fixed_prefix = (
        "git",
        "--no-optional-locks",
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "extensions.partialClone=",
        "-c",
        "remote.origin.promisor=false",
    )
    for argv, environment in calls:
        checkout_index = argv.index("-C")
        assert argv[:checkout_index] == fixed_prefix
        assert argv[checkout_index + 1] == str(repo.checkout)
        assert argv[checkout_index + 2] != "--"
        assert forbidden.isdisjoint(argv)
        assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
        assert environment["GIT_NO_LAZY_FETCH"] == "1"
        assert "GIT_OBJECT_DIRECTORY" not in environment
    assert any("refs/remotes/origin/aidt-prd^{commit}" in argv for argv, _ in calls)
    assert any("ls-tree" in argv for argv, _ in calls)
    assert any("cat-file" in argv and "blob" in argv for argv, _ in calls)


def test_production_runner_stream_caps_kill_and_reap_boundary_plus_one() -> None:
    environment = {"PATH": os.environ.get("PATH", ""), "LANG": "C", "LC_ALL": "C"}
    boundary = _default_git_runner(
        (sys.executable, "-c", "import os; os.write(1, b'x' * 16)"),
        environment,
        GIT_TIMEOUT_SECONDS,
        16,
        16,
    )
    overflow = _default_git_runner(
        (sys.executable, "-c", "import os; os.write(1, b'x' * 17)"),
        environment,
        GIT_TIMEOUT_SECONDS,
        16,
        16,
    )

    assert boundary.stdout == b"x" * 16
    assert boundary.stdout_overflow is False
    assert len(overflow.stdout) == 16
    assert overflow.stdout_overflow is True
    assert isinstance(overflow.returncode, int)


def test_production_runner_times_out_and_reaps() -> None:
    result = _default_git_runner(
        (sys.executable, "-c", "import time; time.sleep(1)"),
        {"LANG": "C", "LC_ALL": "C"},
        0.01,
        16,
        16,
    )

    assert result.timed_out is True
    assert isinstance(result.returncode, int)


def test_named_git_caps_enforce_boundary_and_boundary_plus_one() -> None:
    assert GIT_TOKEN_STDOUT_CAP == 128
    assert GIT_PATH_STDOUT_CAP == 4_096
    assert GIT_TREE_RECORD_CAP == 1_024
    assert GIT_BLOB_STDOUT_CAP == 1_048_576
    assert GIT_STDERR_CAP == 8_192
    boundary = GitCommandResult(0, b"x" * GIT_TOKEN_STDOUT_CAP, b"")
    assert _checked_result(boundary, GIT_TOKEN_STDOUT_CAP, "viewer-api") == boundary.stdout
    overflow = GitCommandResult(0, b"x" * (GIT_TOKEN_STDOUT_CAP + 1), b"")
    with pytest.raises(AidtRoutingFailure, match="git_output_limit"):
        _checked_result(overflow, GIT_TOKEN_STDOUT_CAP, "viewer-api")

    stderr_boundary = GitCommandResult(0, b"", b"x" * GIT_STDERR_CAP)
    with pytest.raises(AidtRoutingFailure, match="git_protocol_invalid"):
        _checked_result(stderr_boundary, GIT_TOKEN_STDOUT_CAP, "viewer-api")
    stderr_overflow = GitCommandResult(0, b"", b"x" * (GIT_STDERR_CAP + 1))
    with pytest.raises(AidtRoutingFailure, match="git_output_limit"):
        _checked_result(stderr_overflow, GIT_TOKEN_STDOUT_CAP, "viewer-api")


def test_dirty_index_worktree_and_symlinks_are_not_routing_input(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    settings = _routing_settings(repo)
    clean = observe_catalog(settings).services[0]
    (repo.checkout / "src/Route.java").write_text(
        "HOSTILE WORKTREE CONTENT", encoding="utf-8"
    )
    (repo.checkout / "staged.txt").write_text("staged change", encoding="utf-8")
    git_command(repo.checkout, "add", "staged.txt")
    (repo.checkout / "untracked.txt").write_text("untracked", encoding="utf-8")
    (repo.checkout / "ignored.txt").write_text("ignored", encoding="utf-8")
    outside = tmp_path / "outside-marker.xml"
    outside.write_text("HOSTILE MARKER", encoding="utf-8")
    marker = repo.checkout / "pom.xml"
    marker.unlink()
    marker.symlink_to(outside)
    before = _dirty_snapshot(repo)

    dirty = observe_catalog(settings).services[0]

    assert dirty.checkout_revision == clean.checkout_revision
    assert dirty.repository_binding_digest == clean.repository_binding_digest
    assert dirty.contents == clean.contents
    assert _dirty_snapshot(repo) == before


def test_base_ref_drift_fails_recheck_then_reobserves_new_commit(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    settings = _routing_settings(repo)
    original = observe_catalog(settings)
    git_command(
        repo.checkout,
        "update-ref",
        "refs/remotes/origin/aidt-prd",
        repo.head_commit,
    )

    with pytest.raises(AidtRoutingFailure, match="revision_changed") as raised:
        recheck_catalog(original)
    moved = observe_catalog(settings)

    assert raised.value.identifier == "service:viewer-api"
    assert original.services[0].checkout_revision == repo.base_commit
    assert moved.services[0].checkout_revision == repo.head_commit


def test_identity_drift_is_sanitized_as_repository_changed(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    settings = _routing_settings(repo)
    changed = False

    def probe(path: Path) -> str:
        value = path.lstat()
        suffix = ":changed" if changed and path == repo.checkout else ""
        return f"{value.st_dev}:{value.st_ino}:{value.st_mode}{suffix}"

    observation = observe_catalog(settings, identity_probe=probe)
    changed = True

    with pytest.raises(AidtRoutingFailure, match="repository_changed") as raised:
        recheck_catalog(observation, identity_probe=probe)
    assert raised.value.identifier == "service:viewer-api"


@pytest.mark.parametrize("target", ["root", "checkout", "git-entry", "objects"])
def test_repository_path_symlinks_are_rejected(
    tmp_path: Path, target: str
) -> None:
    repo = _repository(tmp_path)
    root = repo.root
    if target == "root":
        root = tmp_path / "linked-root"
        root.symlink_to(repo.root, target_is_directory=True)
    elif target == "checkout":
        moved = tmp_path / "moved-checkout"
        repo.checkout.rename(moved)
        repo.checkout.symlink_to(moved, target_is_directory=True)
    elif target == "git-entry":
        git_entry = repo.checkout / ".git"
        moved = tmp_path / "moved-git"
        git_entry.rename(moved)
        git_entry.symlink_to(moved, target_is_directory=True)
    else:
        objects = repo.checkout / ".git/objects"
        moved = tmp_path / "moved-objects"
        objects.rename(moved)
        objects.symlink_to(moved, target_is_directory=True)
    synthetic = FrozenGitRepository(root, root / "viewer-api", repo.base_commit, repo.head_commit)

    with pytest.raises(AidtRoutingFailure, match="repository_invalid"):
        observe_catalog(_routing_settings(synthetic))


@pytest.mark.parametrize("indirection", ["alternates", "replace", "packed-replace"])
def test_object_indirection_is_rejected(tmp_path: Path, indirection: str) -> None:
    repo = _repository(tmp_path)
    if indirection == "alternates":
        path = repo.checkout / ".git/objects/info/alternates"
        path.write_text("", encoding="ascii")
    else:
        git_command(repo.checkout, "replace", repo.base_commit, repo.head_commit)
        if indirection == "packed-replace":
            git_command(repo.checkout, "pack-refs", "--all", "--prune")

    with pytest.raises(AidtRoutingFailure, match="repository_invalid"):
        observe_catalog(_routing_settings(repo))


@pytest.mark.parametrize("entry", ["tree", "symlink"])
def test_committed_non_blob_entries_fail_closed(tmp_path: Path, entry: str) -> None:
    repo = _repository(tmp_path)
    service = service_definition()
    if entry == "tree":
        service["markers"] = ["src"]
    else:
        marker = repo.checkout / "pom.xml"
        marker.unlink()
        marker.symlink_to("head-only.txt")
        git_command(repo.checkout, "add", "pom.xml")
        git_command(repo.checkout, "commit", "-qm", "committed marker symlink")
        revision = git_command(repo.checkout, "rev-parse", "HEAD")
        git_command(
            repo.checkout,
            "update-ref",
            "refs/remotes/origin/aidt-prd",
            revision,
        )

    with pytest.raises(AidtRoutingFailure, match="git_object_invalid"):
        observe_catalog(_settings_for_service(repo, service))


def test_regular_gitfile_and_common_directory_are_identity_bound(
    tmp_path: Path,
) -> None:
    _origin, linked = _linked_worktree(tmp_path)

    observation = observe_catalog(_routing_settings(linked))

    assert (linked.checkout / ".git").is_file()
    assert observation.services[0].checkout_revision == linked.base_commit


@pytest.mark.parametrize("target", ["git-directory", "common-directory"])
def test_gitfile_and_common_directory_symlink_targets_are_rejected(
    tmp_path: Path, target: str
) -> None:
    origin, linked = _linked_worktree(tmp_path)
    gitfile = linked.checkout / ".git"
    git_dir = Path(gitfile.read_text(encoding="utf-8").removeprefix("gitdir: ").strip())
    symlink = tmp_path / "metadata-link"
    if target == "git-directory":
        symlink.symlink_to(git_dir, target_is_directory=True)
        gitfile.write_text(f"gitdir: {symlink}\n", encoding="utf-8")
    else:
        symlink.symlink_to(origin.checkout / ".git", target_is_directory=True)
        relative = os.path.relpath(symlink, git_dir)
        (git_dir / "commondir").write_text(f"{relative}\n", encoding="utf-8")

    with pytest.raises(AidtRoutingFailure, match="repository_invalid"):
        observe_catalog(_routing_settings(linked))


@pytest.mark.parametrize(
    ("case", "category"),
    [
        ("nonzero", "git_command_failed"),
        ("timeout", "git_timeout"),
        ("overflow", "git_output_limit"),
        ("stderr", "git_protocol_invalid"),
        ("tree", "git_protocol_invalid"),
        ("utf8", "git_object_invalid"),
    ],
)
def test_git_failures_are_categorized_without_output_leaks(
    tmp_path: Path, case: str, category: str
) -> None:
    repo = _repository(tmp_path)

    def runner(
        argv: tuple[str, ...],
        environment: Any,
        timeout: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> GitCommandResult:
        command = argv[argv.index("-C") + 2 :]
        scalar = command == ("rev-parse", "--show-toplevel")
        if scalar and case == "nonzero":
            return GitCommandResult(7, b"SECRET-STDOUT", b"SECRET-STDERR")
        if scalar and case == "timeout":
            return GitCommandResult(-9, b"SECRET-STDOUT", b"", timed_out=True)
        if scalar and case == "overflow":
            return GitCommandResult(-9, b"SECRET", b"", stdout_overflow=True)
        if scalar and case == "stderr":
            return GitCommandResult(0, str(repo.checkout).encode() + b"\n", b"SECRET")
        if command[:1] == ("ls-tree",) and case == "tree":
            return GitCommandResult(0, b"MALFORMED-SECRET\0", b"")
        if command[:2] == ("cat-file", "blob") and case == "utf8":
            return GitCommandResult(0, b"\xffSECRET", b"")
        return _default_git_runner(argv, environment, timeout, stdout_cap, stderr_cap)

    with pytest.raises(AidtRoutingFailure, match=category) as raised:
        observe_catalog(_routing_settings(repo), git_runner=runner)
    rendered = repr(raised.value)
    assert "SECRET" not in rendered
    assert str(repo.checkout) not in rendered


def test_blob_and_tree_caps_reject_boundary_plus_one_before_decode() -> None:
    blob = b"x" * GIT_BLOB_STDOUT_CAP
    assert len(_decode_blob(blob)) == GIT_BLOB_STDOUT_CAP
    with pytest.raises(AidtRoutingFailure, match="git_output_limit"):
        _decode_blob(blob + b"x")
    with pytest.raises(AidtRoutingFailure, match="git_object_invalid"):
        _decode_blob(b"\xff")

    with pytest.raises(AidtRoutingFailure, match="git_protocol_invalid"):
        _parse_tree_record(b"x" * (GIT_TREE_RECORD_CAP - 1) + b"\0", "pom.xml")
    with pytest.raises(AidtRoutingFailure, match="git_output_limit"):
        _parse_tree_record(b"x" * GIT_TREE_RECORD_CAP + b"\0", "pom.xml")


def test_missing_required_object_fails_recheck_without_lazy_fetch(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    observation = observe_catalog(_routing_settings(repo))
    object_id = observation.services[0]._object_ids["src/Route.java"]
    object_path = repo.checkout / ".git/objects" / object_id[:2] / object_id[2:]
    object_path.unlink()

    with pytest.raises(AidtRoutingFailure, match="revision_changed") as raised:
        recheck_catalog(observation)
    assert raised.value.identifier == "service:viewer-api"


def test_promisor_object_metadata_is_rejected_without_network(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    promisor = repo.checkout / ".git/objects/pack/synthetic.promisor"
    promisor.write_bytes(b"")

    with pytest.raises(AidtRoutingFailure, match="repository_invalid"):
        observe_catalog(_routing_settings(repo))


def test_per_service_object_cap_accepts_boundary_and_rejects_boundary_plus_one(
    tmp_path: Path,
) -> None:
    root = tmp_path / "aidt"
    root.mkdir()
    service = _sized_service_repository(
        root,
        "viewer-api",
        [GIT_BLOB_STDOUT_CAP] * 4 + [1],
    )
    boundary = dict(service)
    boundary["route_anchors"] = service["route_anchors"][:4]
    observation = observe_catalog(_settings_for_services(root, [boundary]))

    assert observation.total_object_bytes == MAX_SERVICE_OBJECT_BYTES
    with pytest.raises(AidtRoutingFailure, match="git_output_limit"):
        observe_catalog(_settings_for_services(root, [service]))


def test_whole_observation_cap_accepts_boundary_and_rejects_boundary_plus_one(
    tmp_path: Path,
) -> None:
    root = tmp_path / "aidt"
    root.mkdir()
    services = [
        _sized_service_repository(root, f"service-{index}", [GIT_BLOB_STDOUT_CAP] * 4)
        for index in range(4)
    ]
    boundary = observe_catalog(_settings_for_services(root, services))
    overflow = _sized_service_repository(root, "service-overflow", [1])

    assert boundary.total_object_bytes == MAX_OBSERVATION_BYTES
    with pytest.raises(AidtRoutingFailure, match="git_output_limit"):
        observe_catalog(_settings_for_services(root, [*services, overflow]))


def test_unsupported_sha256_object_format_is_rejected(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    def runner(
        argv: tuple[str, ...],
        environment: Any,
        timeout: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> GitCommandResult:
        command = argv[argv.index("-C") + 2 :]
        if command == ("rev-parse", "--show-object-format"):
            return GitCommandResult(0, b"sha256\n", b"")
        return _default_git_runner(argv, environment, timeout, stdout_cap, stderr_cap)

    with pytest.raises(AidtRoutingFailure, match="repository_invalid"):
        observe_catalog(_routing_settings(repo), git_runner=runner)


def test_git_toplevel_must_equal_the_identity_bound_checkout(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    def runner(
        argv: tuple[str, ...],
        environment: Any,
        timeout: float,
        stdout_cap: int,
        stderr_cap: int,
    ) -> GitCommandResult:
        command = argv[argv.index("-C") + 2 :]
        if command == ("rev-parse", "--show-toplevel"):
            return GitCommandResult(0, b"/different/repository\n", b"")
        return _default_git_runner(argv, environment, timeout, stdout_cap, stderr_cap)

    with pytest.raises(AidtRoutingFailure, match="repository_invalid"):
        observe_catalog(_routing_settings(repo), git_runner=runner)


def test_gitfile_and_metadata_caps_accept_boundary_and_reject_plus_one(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "metadata"
    metadata.write_bytes(b"x" * GIT_PATH_STDOUT_CAP)
    assert len(
        _read_metadata_file(metadata, GIT_PATH_STDOUT_CAP, "viewer-api")
    ) == GIT_PATH_STDOUT_CAP
    metadata.write_bytes(b"x" * (GIT_PATH_STDOUT_CAP + 1))
    with pytest.raises(AidtRoutingFailure, match="repository_invalid"):
        _read_metadata_file(metadata, GIT_PATH_STDOUT_CAP, "viewer-api")
    assert GIT_METADATA_FILE_CAP == 1_048_576


@pytest.mark.parametrize(
    "result",
    [
        object(),
        GitCommandResult(True, b"", b""),
        GitCommandResult(0, bytearray(), b""),  # type: ignore[arg-type]
        GitCommandResult(0, b"", b"", timed_out=1),  # type: ignore[arg-type]
    ],
)
def test_malformed_injected_runner_results_fail_as_protocol(
    result: object,
) -> None:
    with pytest.raises(AidtRoutingFailure, match="git_protocol_invalid"):
        _checked_result(result, GIT_TOKEN_STDOUT_CAP, "viewer-api")
